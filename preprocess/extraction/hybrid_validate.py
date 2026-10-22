"""Validate stored extraction provenance and integrity, without model calls."""
import json
from collections import OrderedDict
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path

import fitz

from preprocess.ingest import assert_sources
from preprocess.files import read_jsonl
from preprocess.ingest import Document, sha256

# Native packets are deterministic for an exact source version. A transaction
# validates the snapshot, refined bundle and chunks in turn; avoid repeating
# font inspection/table detection for identical pages. Source hashes are still
# checked on every validation before this cache is consulted.
_NATIVE_PACKETS=OrderedDict()
_NATIVE_PACKET_LIMIT=256


def _source_packet(key,create):
    if key in _NATIVE_PACKETS:
        _NATIVE_PACKETS.move_to_end(key);return _NATIVE_PACKETS[key]
    value=create();_NATIVE_PACKETS[key]=value
    if len(_NATIVE_PACKETS)>_NATIVE_PACKET_LIMIT:_NATIVE_PACKETS.popitem(last=False)
    return value


def contains_crop(region, crop, padding=0):
    # PDF clip intersections are serialized through float32 by MuPDF. The
    # tolerance is 1e-4 PDF pt, not an extra visual association margin.
    tolerance = 0.0001
    outer = fitz.Rect(region) + (-padding-tolerance, -padding-tolerance, padding+tolerance, padding+tolerance)
    return not fitz.Rect(crop).is_empty and outer.contains(fitz.Rect(crop))


def validate_extraction(out):
    with ExitStack() as stack:
        pdfs={}
        def pdf_for(path):
            if path not in pdfs:pdfs[path]=stack.enter_context(fitz.open(path))
            return pdfs[path]
        return _validate_extraction(out,pdf_for)


def _validate_extraction(out,pdf_for):
    out = Path(out).resolve()
    manifest = json.loads((out / "extraction_manifest.json").read_text(encoding="utf-8"))
    docs = [Document(**d) for d in manifest["documents"]]
    assert_sources(docs)
    known = {(d.document_id, d.version_id): d for d in docs}
    integrity = json.loads((out / "extraction_integrity.json").read_text(encoding="utf-8"))
    expected = {"extracted_assets.jsonl", "extraction_pages.jsonl", "source_units.jsonl"}
    if manifest.get('grouping_version'):
        expected.add('visual_groups.jsonl')
    if set(integrity["files"]) != expected:
        raise ValueError("Extraction integrity manifest has incomplete fields")
    for name, digest in integrity["files"].items():
        if sha256(out / name) != digest:
            raise ValueError(f"Stored extraction checksum mismatch: {name}")
    pages = read_jsonl(out / "extraction_pages.jsonl")
    assets = read_jsonl(out / "extracted_assets.jsonl")
    pdf_digests={d.pdf_path:d.rendered_sha256 or d.version_id for d in docs}
    @lru_cache(maxsize=8)
    def native_packet(path, number, width, height):
        from preprocess.source.native_text import packet
        return _source_packet((path,pdf_digests[path],number,width,height),
            lambda:packet(pdf_for(path)[number-1],(width,height)))
    locations = {(p["document_id"], p["version_id"], p["pdf_page"]) for p in pages}
    page_lookup={(p['document_id'],p['version_id'],p['pdf_page']):p for p in pages}
    if len(locations) != len(pages) or len({a["asset_id"] for a in assets}) != len(assets):
        raise ValueError("Duplicate page or asset identities")
    for asset in assets:
        doc = known[(asset["document_id"], asset["version_id"])]
        if asset["schema_version"] != "ra-extraction-1" or asset["numeric_evidence_eligible"] is not False:
            raise ValueError("Inference cannot certify numeric evidence")
        if (asset["document_id"], asset["version_id"], asset["pdf_page"]) not in locations:
            raise ValueError("Asset has no parent page")
        page = pdf_for(doc.pdf_path)[asset["pdf_page"] - 1]
        rect = fitz.Rect(asset["bbox_pdf"])
        bounds = fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        if rect.is_empty or not bounds.contains(rect):
            raise ValueError("Asset coordinates are outside the PDF page")
        for ref in asset["source_refs"]:
            if doc.kind=='pdf' and doc.rendered_path and (ref.get('rendered_path')!=doc.rendered_path or ref.get('rendered_sha256')!=doc.rendered_sha256 or ref.get('locator_type')!='verified_cmap_pdf_page'):
                raise ValueError('PDF native reference lacks the exact verified font derivative')
            if doc.kind=='html' and (ref.get('rendered_path')!=doc.rendered_path or ref.get('rendered_sha256')!=doc.rendered_sha256 or ref.get('locator_type')!='html_render_page'):
                raise ValueError('HTML reference lacks the exact registered rendering')
            if (ref["path"] != doc.raw_path or ref["sha256"] != doc.version_id
                or ref["pdf_page"] != asset["pdf_page"]
                or (not asset.get('is_logical_group') and ref["bbox_pdf"] != asset["bbox_pdf"])
                or (asset.get('is_logical_group') and not fitz.Rect(asset['bbox_pdf']).contains(fitz.Rect(ref['bbox_pdf'])))):
                raise ValueError("Asset source reference crosses a document/page boundary")
        if asset['base']['origin']=='native_text_layer':
            from preprocess.source.native_text import extract
            data=native_packet(doc.pdf_path,asset['pdf_page'],*asset['input_image_size'])
            expected=extract(data,asset['block_label'],asset['bbox_input_px'])
            proof=asset['base'].get('native_text_provenance',{})
            if (expected is None or expected!=asset['base']['content']
                    or proof.get('origin')!='native_text_layer'
                    or proof.get('bbox_input_px')!=asset['bbox_input_px']):
                raise ValueError('Native block text differs from its source PDF region')
        if asset['base']['origin']=='native_table_grid':
            from preprocess.source.native_tables import select
            data=native_packet(doc.pdf_path,asset['pdf_page'],*asset['input_image_size'])
            expected=select(data,asset['block_label'],asset['bbox_input_px'])
            proof=asset['base'].get('native_text_provenance',{})
            if (expected is None or expected['content']!=asset['base']['content']
                    or proof!={**expected,'layout_bbox_input_px':asset['bbox_input_px']}):
                raise ValueError('Native grid differs from its physical PDF cells')
        if asset["base"]["quality"]:
            if any(c["bbox_pdf"] is not None for c in asset["base"]["quality"]["cells"]):
                raise ValueError("Table structure must not fabricate physical cell coordinates")
        if asset['base'].get('light_ocr_provenance') is not None and not asset.get('is_logical_group'):
            from preprocess.files import cache_read
            row=page_lookup[(asset['document_id'],asset['version_id'],asset['pdf_page'])]
            path=Path(row['raw_result_path']);raw=cache_read(path,path.stem)
            if raw is None:raise ValueError('Light OCR cache failed integrity verification')
            rows=raw.get('res',raw)['parsing_res_list']
            matches=[i for i,b in enumerate(rows) if b['block_bbox']==asset['bbox_input_px']
                and b['block_label']==asset['block_label']]
            if len(matches)!=1 or raw.get('ra_light_ocr',{}).get(str(matches[0]))!=asset['base']['light_ocr_provenance']:
                raise ValueError('Light OCR provenance differs from its exact raw region')
        extra=asset.get('light_region_ocr')
        if extra:
            from preprocess.files import cache_read
            from preprocess.ingest import stable_hash
            meta=extra['metadata'];path=Path(extra['raw_path'])
            if (meta['source_refs']!=asset['source_refs'] or meta['asset_id']!=asset['asset_id']
                or meta['numeric_evidence_eligible'] is not False or sha256(meta['crop_path'])!=meta['crop_sha256']
                or sha256(path)!=extra['raw_sha256'] or path.stem!=stable_hash(meta)):
                raise ValueError('Region light OCR source/crop binding changed')
            payload=cache_read(path,path.stem)
            if payload is None or payload['metadata']!=meta or payload['raw']['ra_light_ocr']['0']['ocr']!=extra['provenance']['ocr']:
                raise ValueError('Region light OCR differs from its stored raw result')
            if asset['selected']['origin']=='pp_ocr_table_candidate':
                from PIL import Image
                from preprocess.tables.simple_grid import reconstruct
                with Image.open(meta['crop_path']) as image:grid=reconstruct(image,extra['provenance']['ocr'])
                if grid!=extra['provenance']['grid'] or grid['html']!=asset['selected']['content']:
                    raise ValueError('Selected light table differs from physical grid/tokens')
        if asset.get('region_structure'):
            from preprocess.tables.region_structure import analyze
            expected_structure=analyze(asset,page,assets,read_jsonl(out/'visual_groups.jsonl'))
            if expected_structure!=asset['region_structure']:
                raise ValueError('Region structure differs from source geometry/cached OCR')
            if asset['selected']['origin'] in ('source_geometry_table_candidate','cached_ocr_table_candidate'):
                if (expected_structure['selected_origin']!=asset['selected']['origin']
                        or expected_structure['grid']['html']!=asset['selected']['content']
                        or expected_structure['quality']['issues']):
                    raise ValueError('Selected region table has unverified structure')
        if asset.get('native_table_fragment'):
            from preprocess.source.native_table_fragments import prepare as native_fragment_prepare, eligible as native_fragment_eligible
            expected_fragment=native_fragment_prepare(asset,page,native_packet(doc.pdf_path,asset['pdf_page'],*asset['input_image_size']))
            if expected_fragment!=asset['native_table_fragment'] or not native_fragment_eligible(asset):
                raise ValueError('Native closed rows differ from original borders/glyphs/header')
        elif asset['selected']['origin']=='native_closed_table_fragment':
            raise ValueError('Native fragment lacks original physical source proof')
        if asset.get('projection_grid'):
            from preprocess.tables.projection_grid import eligible as projection_eligible
            if not projection_eligible(asset):raise ValueError('Projection grid differs from bound source OCR/geometry')
        elif asset['selected']['origin']=='source_ocr_projection_grid':
            raise ValueError('Projection grid lacks source OCR/geometry proof')
        if asset.get('source_grid_refinement'):
            from preprocess.tables.complex_geometry import table_gate
            from preprocess.tables.source_grid import prepare
            stored=asset['source_grid_refinement'];gate=table_gate(asset,page)
            if gate!=stored['gate'] or not gate['geometry']:
                raise ValueError('Source-fixed grid differs from original physical borders')
            expected_plan=prepare(asset,page,gate['geometry'])
            if stored.get('transcriptions'):
                raise ValueError('Source-fixed transcription replay is not yet supported')
            if stored['plan']!=expected_plan:
                raise ValueError('Source-fixed cells differ from original native/OCR text')
            if asset['selected']['origin']=='source_fixed_grid_table_candidate' and (expected_plan['issues']
                    or asset['selected']['content']!=expected_plan['representation']):
                raise ValueError('Selected source-fixed table lost physical source content')
        elif asset['selected']['origin']=='source_fixed_grid_table_candidate':
            raise ValueError('Selected source-fixed table lacks physical source proof')
        if asset.get('source_grid_gate_review'):
            from preprocess.extraction.queue_finalize import validate_review
            validate_review(asset,page)
        if asset.get('visual_structure_review'):
            from preprocess.extraction.region_review import validate
            validate(asset['visual_structure_review'])
            proof=asset['visual_structure_review']
            from preprocess.ingest import stable_hash
            if (proof['asset_id']!=asset['asset_id'] or proof['source_input_sha256']!=stable_hash(
                    [asset['base'],asset['native_reference'],asset.get('light_region_ocr')])):
                raise ValueError('Visual review crosses target source state')
        for extra in [asset.get('label_ocr') or {}, *asset.get('label_ocr_regions', [])]:
            if extra.get('status') != 'complete':
                continue
            if (extra['numeric_evidence_eligible'] is not False
                    or sha256(extra['crop_path']) != extra['crop_sha256']
                    or sha256(extra['raw_response_path']) != extra['raw_response_sha256']):
                raise ValueError('Figure OCR support provenance changed')
            for ref in extra['source_refs']:
                if (ref['path'] != doc.raw_path or ref['sha256'] != doc.version_id or ref['pdf_page'] != asset['pdf_page']
                        or not contains_crop(bounds, ref['bbox_pdf'])
                        or not contains_crop(asset['bbox_pdf'], ref['bbox_pdf'])
                        and not (extra.get('mode', '').endswith('_tight')
                            and contains_crop(asset['bbox_pdf'], ref['bbox_pdf'], 4))):
                    raise ValueError('Figure OCR crop crosses a physical source boundary')
            stored = json.loads(Path(extra['raw_response_path']).read_text(encoding='utf-8'))
            metadata = {key: value for key, value in extra.items() if key not in
                        {'raw_response_path', 'raw_response_sha256', 'status', 'text', 'runtime', 'numeric_evidence_eligible'}}
            if stored['metadata'] != metadata:
                raise ValueError('Figure OCR metadata differs from its raw response')
            from preprocess.visual.text_checks import figure_label_reference
            raw = stored['raw']
            text = figure_label_reference('\n'.join(r['block_content'] for r in raw.get('res', raw)['parsing_res_list']))
            if text != extra['text']:
                raise ValueError('Figure OCR text differs from its raw response')
    units = read_jsonl(out / "source_units.jsonl")
    if len(units) != len(assets) or {u["unit_id"] for u in units} != {a["asset_id"] for a in assets}:
        raise ValueError("Representation units do not match extracted assets")
    by_id = {a["asset_id"]: a for a in assets}
    group_by_id = {}
    if manifest.get('grouping_version'):
        groups = read_jsonl(out/'visual_groups.jsonl')
        group_by_id = {g['group_id']: g for g in groups}
        if len({g['group_id'] for g in groups}) != len(groups):
            raise ValueError('Duplicate logical groups')
        for group in groups:
            identifiers = list(dict.fromkeys([*group.get('caption_part_asset_ids', []),
                *([group['caption_asset_id']] if group.get('caption_asset_id') else []),
                *group['member_asset_ids'], *group.get('context_asset_ids', [])]))
            for identifier in identifiers:
                member = by_id[identifier]
                if (member['document_id'],member['version_id'],member.get('logical_group_id')) != (group['document_id'],group['version_id'],group['group_id']):
                    raise ValueError('Logical group crosses source identity or membership')
            if group.get('context_version'):
                if set(group['context_roles']) != set(group['context_asset_ids']):
                    raise ValueError('Table context roles do not match their source units')
                expected_refs = [ref for identifier in list(dict.fromkeys([
                    *group.get('caption_part_asset_ids', []), *group['member_asset_ids'],
                    *group.get('context_asset_ids', [])])) for ref in by_id[identifier]['source_refs']]
                if group['source_refs'] != expected_refs:
                    raise ValueError('Table context source references differ from their physical assets')
            if group['kind']=='figure' and by_id[group['group_id']]['member_asset_ids'] != group['member_asset_ids']:
                raise ValueError('Figure parent does not preserve its panels')
    for unit in units:
        if unit["representation_range"] != [0, len(unit["text"])] or unit["numeric_evidence_eligible"] is not False:
            raise ValueError("Invalid representation range or numeric eligibility")
        asset = by_id[unit["unit_id"]]
        if asset.get('figure_labels'):
            from preprocess.visual.figure_labels import project, standalone_scope
            scope = group_by_id.get(asset.get('logical_group_id')) or standalone_scope(asset)
            expected_labels = project(asset, scope, by_id, asset['figure_labels']['version'])
            if (asset['figure_labels'] != expected_labels or unit.get('figure_labels') != expected_labels
                    or asset['selected']['content'] != expected_labels['text']):
                raise ValueError('Figure label projection differs from its physical text support')
        if (unit["document_id"] != asset["document_id"] or unit["version_id"] != asset["version_id"]
            or unit["text"] != asset["selected"]["content"] or unit["content_origin"] != asset["selected"]["origin"]
            or unit["source_refs"] != asset["source_refs"]):
            raise ValueError("Representation unit is inconsistent with its source asset")
        if unit.get("retrieval_eligible") and (unit.get("review_required") or not unit["text"].strip()):
            raise ValueError("Pending review/empty content cannot be eligible for retrieval")
        group = group_by_id.get(asset.get('logical_group_id'))
        if group and group.get('context_version'):
            structure = unit.get('structure', {})
            if (structure.get('table_id') != group['group_id']
                    or structure.get('context_unit_ids') != group['context_asset_ids']
                    or structure.get('context_roles') != group['context_roles']):
                raise ValueError('Representation table context differs from its physical group')
    if integrity.get('visual_context'):
        from preprocess.visual.visual_context import load_bundle, validate_bundle
        validate_bundle(load_bundle(out, integrity['visual_context']), assets, units,
            list(group_by_id.values()), manifest['documents'], pages)
    result = {"status": "passed", "documents": len(docs), "pages": len(pages), "assets": len(assets),
              "checks": "source hashes, artifact hashes, page/region relationships and representation ranges",
              "extraction_accuracy_measured": False}
    return result
