"""Optional, selective high-resolution label OCR for a completed PDF upload."""
import json
import io
from pathlib import Path

import fitz
from PIL import Image

from .figure_labels import apply, scopes
from .hybrid import read_jsonl, save_outputs
from .hybrid_validate import validate_extraction
from .ingest import Document, sha256, stable_hash
from .inventory import write_json
from .paddle_adapter import PaddleAdapter
from .qwen_adapter import QwenAdapter
from .regroup import regroup
from .reporting import build_report, now, save_report
from .text_checks import figure_label_reference

OCR_VERSION = 'figure-label-ocr-2'


def complete(run, out, config, max_calls=None, paddle=None, targeted=False, tight=False):
    if max_calls is not None and (type(max_calls) is not int or max_calls < 0):
        raise ValueError('OCR call budget must be a nonnegative integer')
    config.validate()
    from .ingest import assert_model_execution_allowed
    source_manifest = json.loads((Path(run)/'extraction_manifest.json').read_text(encoding='utf-8'))
    assert_model_execution_allowed([Document(**d) for d in source_manifest['documents']])
    regroup(run, out)
    out = Path(out).resolve()
    manifest = json.loads((out/'extraction_manifest.json').read_text(encoding='utf-8'))
    assets = read_jsonl(out/'extracted_assets.jsonl')
    groups = read_jsonl(out/'visual_groups.jsonl')
    docs = [Document(**d) for d in manifest['documents']]
    lookup = {a['asset_id']: a for a in assets}
    apply(assets, groups)
    requested = []
    for g in scopes(assets, groups):
        rejected = [f for f in lookup[g['group_id']]['figure_labels']['fields'] if f['status'] == 'needs_review']
        indices = {f['panel_index'] for f in rejected}
        if targeted:
            for i, uid in enumerate(g['member_asset_ids']):
                roles = {f['role'] for f in rejected if f['panel_index'] == i}
                if 'x_axis' in roles:
                    requested.append((lookup[uid], 'x_axis_tight' if tight else 'x_axis'))
                if 'y_axis' in roles:
                    requested.append((lookup[uid], 'y_axis_tight' if tight else 'y_axis'))
                if None in indices and lookup[g['group_id']]['enrichment']['validation']['answer']['figure_type'] == 'chart':
                    if any(f['role'] == 'legend' for f in rejected):
                        requested.append((lookup[uid], 'legend_tight' if tight else 'legend'))
        else:
            requested += [(lookup[uid], 'full') for i, uid in enumerate(g['member_asset_ids']) if None in indices or i in indices]
    summary = json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
    stats = {'version': OCR_VERSION, 'targeted': targeted, 'requested_regions': len(requested), 'attempted_calls': 0,
             'cache_reused': 0, 'failed': 0, 'pending_budget': 0, 'dpi': 300, 'new_qwen_calls': 0}
    backend = paddle or PaddleAdapter(config.paddle, out)
    fingerprint = None
    try:
        for member, mode in requested:
            old = (member.get('label_ocr') or {}) if mode == 'full' else next((r for r in member.get('label_ocr_regions', []) if r['mode'] == mode), {})
            if old.get('status') == 'complete':
                stats['cache_reused'] += 1
                continue
            if max_calls is not None and stats['attempted_calls'] >= max_calls:
                stats['pending_budget'] += 1
                continue
            if fingerprint is None:
                if config.paddle.release_qwen_before_parser and paddle is None:
                    QwenAdapter(config.qwen).unload()
                fingerprint = backend.fingerprint()
            crop = out/'label_crops'/f"{member['asset_id']}_{mode}.png"
            crop.parent.mkdir(parents=True, exist_ok=True)
            ref = member['source_refs'][0]
            x0, y0, x1, y1 = member['bbox_pdf']
            w, h = x1-x0, y1-y0
            box = {'full': [x0,y0,x1,y1], 'x_axis': [x0,y0+.72*h,x1,y1],
                   'y_axis': [x0,y0,x0+.22*w,y1], 'legend': [x0+.4*w,y0,x1,y0+.48*h],
                   'x_axis_tight': [x0,y0+.92*h,x1,y1+4],
                   'y_axis_tight': [x0-4,y0,x0+.12*w,y0+.9*h],
                   'legend_tight': [x0+.7*w,y0,x1,y0+.3*h]}[mode]
            rotation = -90 if mode.startswith('y_axis') else 0
            with fitz.open(ref.get('rendered_path') or ref['path']) as pdf:
                page = pdf[member['pdf_page']-1]
                box = list(fitz.Rect(box) & fitz.Rect(0,0,page.cropbox.width,page.cropbox.height))
                pix = page.get_pixmap(dpi=300, clip=fitz.Rect(box), alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes('png')))
                if rotation:
                    image = image.rotate(rotation, expand=True)
                image.save(crop)
            source_refs = [{**ref, 'bbox_pdf': box}]
            metadata = {'version': OCR_VERSION, 'model': fingerprint, 'prompt': 'ocr', 'chart_recognition': False,
                'crop_path': str(crop), 'crop_sha256': sha256(crop), 'dpi': 300,
                'source_refs': source_refs, 'image_size': list(image.size), 'mode': mode,
                'rotation_degrees': rotation, 'allowed_roles': [mode.removesuffix('_tight'), 'printed_label'] if mode != 'full' else []}
            key = stable_hash(metadata)
            raw_path = out/'label_ocr_raw'/f'{key}.json'
            stats['attempted_calls'] += 1
            print(f"Paddle label OCR: {member['document_id']} p{member['pdf_page']} region {member['asset_id']}", flush=True)
            try:
                raw = backend.ocr(crop)
                write_json(raw_path, {'metadata': metadata, 'raw': raw})
                rows = raw.get('res', raw)['parsing_res_list']
                literal = figure_label_reference('\n'.join(r['block_content'] for r in rows))
                result = {**metadata, 'raw_response_path': str(raw_path),
                    'raw_response_sha256': sha256(raw_path), 'status': 'complete', 'text': literal,
                    'runtime': raw.get('ra_runtime'), 'numeric_evidence_eligible': False}
            except Exception as exc:
                stats['failed'] += 1
                result = {**metadata, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}
            if mode == 'full':
                member['label_ocr'] = result
            else:
                member.setdefault('label_ocr_regions', []).append(result)
            summary['figure_label_ocr'] = stats
            save_outputs(out, assets, read_jsonl(out/'extraction_pages.jsonl'), docs, summary, groups)
    finally:
        backend.close()
    apply(assets, groups)
    figures = [a['figure_labels'] for a in assets if a.get('figure_labels')]
    stats.update(completed_at_kst=now(), accepted_fields=sum(f['accepted_fields'] for f in figures),
                 rejected_fields=sum(f['rejected_fields'] for f in figures), figures_with_labels=sum(bool(f['accepted_fields']) for f in figures))
    summary['figure_label_ocr'] = stats
    manifest['figure_labels_version'] = OCR_VERSION
    write_json(out/'extraction_manifest.json', manifest)
    save_outputs(out, assets, read_jsonl(out/'extraction_pages.jsonl'), docs, summary, groups)
    validate_extraction(out)
    save_report(build_report(out), out)
    return stats
