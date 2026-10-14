"""Check a completed local evaluation without calling generation or mutating its index."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.current_server_eval import digest, read, request, write


def verify(folder):
    frozen = read(folder/'frozen.json')
    live = request(frozen['base_url'], '/api/summary')
    viewer = request('http://127.0.0.1:8766', '/api/summary')
    assert live == frozen['server_summary'], 'Live server changed'
    assert viewer['chunks_sha256'] == live['chunks_sha256'], 'Viewer index differs'
    index = ROOT/'data/rag/candidates/pipeline_review_2026-10-08'
    assert digest(index/'chunks.jsonl') == live['chunks_sha256']
    assert read(index/'emb/bge-m3/meta.json')['chunks_sha256'] == live['chunks_sha256']
    for group in [frozen['code_sha256'], frozen['source_label_sha256'],
                  read(folder/'additional_freeze.json')['sha256']]:
        for name, sha in group.items():
            assert digest(ROOT/name) == sha, f'Frozen file changed: {name}'
    assert digest(folder/'cases.json') == frozen['cases_sha256']
    assert digest(folder/'rubric.json') == read(folder/'rubric_freeze.json')['sha256']
    models = request('http://127.0.0.1:11434','/api/tags')['models']
    original = read(folder/'model_inventory.json')['models'][0]
    model = next(m for m in models if m['name']==live['answer_model'])
    assert model['digest'] == original['digest'], 'Answer model changed'
    cases = read(folder/'cases.json')
    judgments = read(folder/'source_judgments.json')['rows']
    assert {c['id'] for c in cases} == {j['id'] for j in judgments}
    counts = {'requests':0,'responses':0,'source_version_checked_evidence_blocks':0}
    for case in cases:
        key = case['id']
        payload = read(folder/f'responses/{key}.request.json')
        assert payload == case['payload']
        assert set(payload) <= {'question','products','jurisdictions','documents','include_nondefault'}
        report = read(folder/f'responses/{key}.json')
        assert report['question'] == case['question']
        assert report['index']['chunks_sha256'] == live['chunks_sha256']
        assert report['retrieval_pages'] == 5
        for unit in report['results']:
            for block in unit.get('evidence',{}).values():
                assert (block['document_id'],block['version_id']) == (case['document_id'],case['version_id'])
                counts['source_version_checked_evidence_blocks'] += 1
        counts['requests'] += 1
        counts['responses'] += 1
    assert read(folder/'completed.json')['n'] == len(cases) == 22
    assert read(folder/'analysis.json')['n_responses'] == 22
    assert read(folder/'analysis.json')['reviewed_answerable'] == 20
    code_names = ['eval/current_server_eval.py','eval/live_eval_analyze.py',
        'eval/write_live_eval_report.py','eval/verify_live_eval.py',
        'eval/live_eval_rubric_2026-10-09.json','tests/test_current_server_eval.py']
    for name in code_names:
        target = folder/'final_eval_code'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
    result = {'checked_at':datetime.now(timezone.utc).isoformat(),
        'all_checks_passed':True,'live_server_unchanged':True,
        'viewer_same_index':True,'frozen_code_and_labels_unchanged':True,
        'model_digest_unchanged':True,'chunks_and_embeddings_match':True,
        'all_requests_exclude_gold':True,'all_cases_source_adjudicated':True,
        'counts':counts,'main_summary':live,'viewer_summary':viewer,
        'eval_code_sha256':{n:digest(ROOT/n) for n in code_names},
        'report_sha256':digest(ROOT/'docs/evaluation/RAG_PERFORMANCE_2026-10-09.md'),
        'analysis_sha256':digest(folder/'analysis.json'),
        'judgments_sha256':digest(folder/'source_judgments.json'),
        'limitations':'Agent source adjudication against draft gold; not independent expert-certified accuracy.'}
    write(folder/'final_checks.json',result)
    print({k:v for k,v in result.items() if k not in ['main_summary','viewer_summary','eval_code_sha256']})


if __name__ == '__main__':
    verify(ROOT/'data/runs/rag_evaluation_2026-10-09')
