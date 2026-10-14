"""Build a review candidate from saved extraction, preserving every prior artifact."""
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
from preprocess.ingest import sha256
from preprocess.regroup import regroup
from rag.preprocessed_chunks import write
from preprocess.workflow import build_index


if __name__ == '__main__':
    report_dir = ROOT/'data/runs/pipeline_review_2026-10-08'
    run = ROOT/'data/preprocessed/ra_workflow_native_v1_2026-10-08/labels'
    derived = ROOT/'data/preprocessed/pipeline_review_2026-10-08'
    candidate = ROOT/'data/rag/candidates/pipeline_review_2026-10-08'
    paths = [ROOT/'data/rag/chunks.jsonl', ROOT/'data/catalog/documents.jsonl',
        *[p for p in run.iterdir() if p.is_file()],
        *[p for p in (run.parent/'candidate').rglob('*') if p.is_file()]]
    before = {str(p): sha256(p) for p in paths}
    report_dir.mkdir(parents=True, exist_ok=True)
    if (report_dir/'upgrade.json').exists(): raise ValueError('Choose a new upgrade run')
    print('Regrouping source-verified extraction; no OCR/VLM calls', flush=True)
    grouped = regroup(run, derived, ROOT/'data/catalog/documents.jsonl')
    print('Building revised source-preserving chunks', flush=True)
    summary = write(derived, candidate)
    print('Building local BGE-M3 index', flush=True)
    index = build_index(candidate, [], 'cuda', False)
    after = {str(p): sha256(p) for p in paths}
    if before != after: raise ValueError('Prior artifacts changed during upgrade')
    snapshot = report_dir/'code'
    for filename in ('preprocess/ingest.py', 'preprocess/regroup.py', 'preprocess/visual_groups.py',
                     'preprocess/cli.py', 'rag/preprocessed_chunks.py', 'eval/review_pipeline.py',
                     'eval/upgrade_review_candidate.py'):
        dest = snapshot/filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/filename, dest)
    result = {'run': str(derived), 'candidate': str(candidate), 'grouping': grouped, 'chunks': summary,
        'index': index, 'previous_artifacts': before, 'previous_artifacts_unchanged': True,
        'active_index_updated': False, 'new_ocr_calls': 0, 'new_vlm_calls': 0,
        'code_sha256': {str(p.relative_to(snapshot)): sha256(p) for p in snapshot.rglob('*.py')}}
    (report_dir/'upgrade.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'candidate': str(candidate), 'chunks': summary['chunks'],
        'by_kind': summary['by_content_kind'], 'exclusions': summary['exclusion_reasons'],
        'previous_artifacts_unchanged': True}, ensure_ascii=False, indent=2))
