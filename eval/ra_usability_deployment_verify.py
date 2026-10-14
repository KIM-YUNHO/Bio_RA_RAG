"""Read-only verification of the review-notes service update; no model requests."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

BASE = Path('data/runs/ra_planning_usability_2026-10-12_v1')
URL = 'http://127.0.0.1:8775'
JOB = 'e34a2991876d40edbf2b11610d1052a0'

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def get(path):
    with urlopen(URL+path, timeout=10) as response:
        return response.read(), dict(response.headers)

def main():
    summary_raw, _ = get('/api/summary')
    summary = json.loads(summary_raw)
    assert summary['pipeline_version'] == 'ra-research-rag-4.7.2'
    assert summary['answer_model'] == 'gpt-5.6-luna'
    job_raw, _ = get('/api/jobs/'+JOB)
    job = json.loads(job_raw)
    before = json.loads((BASE/'pre_restart_job.json').read_text(encoding='utf-8-sig'))
    assert job == before
    archived = 'data/runs/ra_service_reports/'+JOB+'.json'
    prior_hash = json.loads((BASE/'archive_before.json').read_text(encoding='utf-8-sig'))['Hash'].lower()
    assert digest(archived) == prior_hash
    static = []
    for url_path, file in [('/', 'index.html'), ('/app.js', 'app.js'), ('/review-notes.js', 'review-notes.js'), ('/style.css', 'style.css')]:
        body, headers = get(url_path)
        assert body == (Path('rag_service/static')/file).read_bytes()
        assert "script-src 'self'" in headers['Content-Security-Policy']
        static.append({'path':url_path, 'sha256':hashlib.sha256(body).hexdigest(), 'content_type':headers['Content-Type']})
    html = (Path('rag_service/static')/'index.html').read_text(encoding='utf-8')
    assert html.index('src="/review-notes.js"') < html.index('src="/app.js"')
    previous = json.loads(Path('data/runs/ra_workflow_update_2026-10-12_v1/final_service_protocol.json').read_text(encoding='utf-8'))
    changed = [file for file, value in previous['code_sha256'].items() if digest(file) != value]
    assert not any(file.startswith('rag/') for file in changed), changed
    assert all(digest(file) == value for file, value in previous['index_sha256'].items())
    result = {'pipeline_version':summary['pipeline_version'], 'model':summary['answer_model'], 'new_provider_calls':0,
        'restored_report_identical':True, 'archive_sha256_unchanged':True, 'retrieval_synthesis_code_unchanged':True,
        'index_sha256_unchanged':True, 'changed_prior_frozen_files':changed, 'static_resources':static,
        'notes_module_load_order_verified':True, 'browser_visual_verification':False,
        'history_storage':'browser_localStorage_per_job', 'shared_server_review_storage':False}
    (BASE/'deployment_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
