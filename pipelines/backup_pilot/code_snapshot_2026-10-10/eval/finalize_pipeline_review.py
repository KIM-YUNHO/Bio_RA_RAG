"""Seal source invariants and summarize the paired development review."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from preprocess.ingest import sha256
from preprocess.hybrid import read_jsonl
from preprocess.hybrid_validate import validate_extraction
from preprocess.visual_groups import build_visual_groups
from rag.preprocessed_chunks import furniture_units


def read(path): return json.loads(path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    folder = ROOT/'data/runs/pipeline_review_2026-10-08'
    upgrade = read(folder/'upgrade.json')
    run, candidate = Path(upgrade['run']), Path(upgrade['candidate'])
    original = ROOT/'data/preprocessed/ra_workflow_native_v1_2026-10-08/labels'
    validate_extraction(run)
    assets = {a['asset_id']: a for a in read_jsonl(run/'extracted_assets.jsonl')}
    old_assets = {a['asset_id']: a for a in read_jsonl(original/'extracted_assets.jsonl')}
    groups = read_jsonl(run/'visual_groups.jsonl')
    recomputed, _ = build_visual_groups(deepcopy(list(assets.values())))
    if recomputed != groups: raise ValueError('Final grouping code changes saved output; rebuild candidate')
    summary = read(candidate/'chunks_summary.json')
    chunks = read_jsonl(candidate/'chunks.jsonl')
    import numpy as np
    vectors = np.load(candidate/'emb/bge-m3/doc.npy')
    if vectors.shape != (len(chunks), 1024) or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=.002):
        raise ValueError('Invalid revised document vectors')
    units = read_jsonl(run/'source_units.jsonl')
    pages = {(p['document_id'], p['version_id'], p['pdf_page']): p for p in read_jsonl(run/'extraction_pages.jsonl')}
    furniture = furniture_units(units, assets, pages, summary['config'])
    represented = {s['unit_id'] for c in chunks for s in c['provenance']['source_spans']}
    excluded = {e['unit_id'] for e in read_jsonl(candidate/'chunk_exclusions.jsonl')}
    if represented & furniture: raise ValueError('Page furniture re-entered index')
    if represented | excluded != {u['unit_id'] for u in units}: raise ValueError('Unaccounted source units')
    if any(sha256(Path(p)) != digest for p, digest in upgrade['previous_artifacts'].items()):
        raise ValueError('Prior artifacts changed')
    figures = [uid for uid, a in old_assets.items() if a['asset_type'] in ('figure', 'figure_group')]
    if any(assets[uid]['selected'] != old_assets[uid]['selected'] or
           assets[uid].get('figure_labels') != old_assets[uid].get('figure_labels') for uid in figures):
        raise ValueError('Figure text/label provenance changed')
    old_captioned = {uid for g in read_jsonl(original/'visual_groups.jsonl') if g.get('caption_text') for uid in g['member_asset_ids']}
    additions = [g for g in groups if g.get('caption_text') and any(uid not in old_captioned for uid in g['member_asset_ids'])]
    result = {'verified_at': datetime.now(timezone.utc).isoformat(), 'all_523_pages_preserved': len(pages) == 523,
        'all_source_units_accounted_for': True, 'source_units': len(units), 'represented_units': len(represented),
        'excluded_units': len(excluded), 'page_furniture_excluded': len(furniture), 'figure_text_and_label_provenance_preserved': True,
        'final_grouping_code_reproduces_saved_groups': True, 'previous_artifacts_unchanged': True,
        'active_index_updated': False, 'new_ocr_calls': 0, 'new_vlm_calls': 0,
        'restored_caption_groups': [{k: g[k] for k in ('document_id', 'label', 'caption_text', 'pdf_pages', 'continuation_status')} for g in additions],
        'source_documents': [{'document_id': d['document_id'], 'hash_valid': sha256(Path(d['raw_path'])) == d['version_id'],
            'doc_date': d['metadata'].get('doc_date')} for d in read(run/'extraction_manifest.json')['documents']]}
    for phase in ('before', 'after'):
        path = folder/('answers_before' if phase == 'before' else 'answers_final')/'summary.json'
        if not path.exists(): raise ValueError(f'Missing actual answer comparison: {phase}')
        answers = read(path)
        if len(answers['rows']) != 4: raise ValueError('Incomplete answer comparison')
        result[f'answers_{phase}'] = answers
    if not result['all_523_pages_preserved'] or not all(d['hash_valid'] for d in result['source_documents']):
        raise ValueError('Source coverage or document integrity failed')
    result['embedding_shape_finite_and_norm_checks'] = 'passed'
    test_log = (folder/'tests_final.log').read_text(encoding='utf-8')
    if not test_log.rstrip().endswith('OK'): raise ValueError('Final unit suite did not pass')
    result['test_summary'] = test_log[test_log.rfind('Ran '):].strip()
    snapshots = folder/'final_code'
    files = ['preprocess/ingest.py', 'preprocess/regroup.py', 'preprocess/visual_groups.py', 'preprocess/cli.py',
        'rag/preprocessed_chunks.py', 'rag/pipeline.py', 'rag/answer.py', 'rag/preprocessed_evidence.py',
        'rag_service/__main__.py', 'rag/search.py', 'rag/study_search.py', 'tests/test_preprocessed_search.py',
        'tests/test_pipeline_review.py', 'tests/test_preprocessed_chunks.py',
        'tests/test_visual_groups.py', 'tests/test_rag_pipeline.py', 'eval/review_pipeline.py',
        'eval/review_answers.py', 'eval/upgrade_review_candidate.py', 'eval/finalize_pipeline_review.py']
    for name in files:
        dest = snapshots/name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/name, dest)
    result['code_sha256'] = {name: sha256(ROOT/name) for name in files}
    (folder/'final_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if not k.startswith('answers_') and k != 'code_sha256'}, ensure_ascii=False, indent=2))
