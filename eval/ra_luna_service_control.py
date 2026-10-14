"""One actual primary-service Luna request, no resubmission or provider retries."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from rag import ra_spans, ra_quantities, ra_source_constraints

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/runs/ra_luna_deployment_2026-10-12_v1'
BASE = 'http://127.0.0.1:8775'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def request(path, payload=None):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None,
        headers={'Content-Type': 'application/json'} if payload is not None else {})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def run():
    if OUT.exists():
        raise ValueError('Existing control output; inspect it instead of submitting again')
    service = request('/api/summary')
    assert service['answer_model'] == 'gpt-5.6-luna'
    assert service['reasoning_effort'] == 'none'
    assert service['pipeline_version'] == 'ra-research-rag-4.6.7'
    baseline = ROOT / 'data/runs/ra_pipeline_obligations_2026-10-12_v7'
    question = read(baseline / 'service/live_report.json')['question']
    payload = {'question': question, 'products': ['SB17'], 'jurisdictions': ['EU']}
    index = read(baseline / 'baseline.json')['index_sha256']
    assert all(digest(ROOT / name) == sha for name, sha in index.items())
    code = {str(p.relative_to(ROOT)): digest(p) for p in (ROOT / 'rag').glob('*.py')}
    write(OUT / 'protocol.json', {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'service': BASE, 'summary': service, 'request': payload,
        'new_framework_call_maximum': 3, 'external_grader': False,
        'automatic_retry': False, 'runner_sha256': digest(Path(__file__)),
        'index_sha256': index, 'code_sha256': code,
        'local_tests': {'ra': 211, 'service': 3, 'total': 214},
    })
    print('Submitting one primary Luna request', flush=True)
    submitted = request('/api/questions', payload)
    write(OUT / 'submitted.json', submitted)
    deadline = time.monotonic() + 600
    previous_stage = None
    while True:
        job = request('/api/jobs/' + submitted['id'])
        if job.get('stage') != previous_stage:
            print(job.get('stage'), flush=True)
            previous_stage = job.get('stage')
        if job['state'] != 'running':
            break
        if time.monotonic() > deadline:
            raise TimeoutError('Inspect submitted job; do not resubmit')
        time.sleep(2)
    write(OUT / 'live_job.json', job)
    if job['state'] != 'completed':
        raise RuntimeError(job.get('error', 'Service request failed'))
    report = job['report']
    write(OUT / 'live_report.json', report)
    verify(report, index, code)


def verify(report, index, code):
    result = report['results'][0]
    calls = report['generation_telemetry']
    assert 1 <= len(calls) <= 3
    assert all(c['model_requested'] == 'gpt-5.6-luna' and c.get('model_reported') == 'gpt-5.6-luna'
               and c['status'] == 'completed' and c.get('reasoning_effort') == 'none' for c in calls)
    spans, _ = ra_spans.catalog(result['evidence'])
    citations = 0
    for claim in result['claims']:
        assert ra_quantities.check(claim, result['evidence']) is None
        assert ra_source_constraints.check(claim, result['evidence']) is None
        for source in claim['sources']:
            span = spans[source['citation_alias']]
            assert span['quote'] == source['quote']
            assert span['span_id'] == source['citation_span_id']
            assert [span['start'], span['end']] == source['source_range']
            # The same physical span can have two aliases in repeated packets.
            # Bind the stable source identity, not a packet-local alias label.
            assert source['citation_span_id'] in {spans[alias]['span_id'] for alias in claim['initial_span_ids']}
            citations += 1
    assert all(digest(ROOT / name) == sha for name, sha in index.items())
    assert all(digest(ROOT / name) == sha for name, sha in code.items())
    receipt = {'verified_at_kst': datetime.now(timezone(timedelta(hours=9))).isoformat(),
        'service_version': report['pipeline_version'], 'model': 'gpt-5.6-luna',
        'reasoning_effort': 'none', 'fresh_actual_service': True,
        'answer_status': result['status'], 'claims': len(result['claims']),
        'rejected': len(result['validation']['rejected_items']),
        'own_exact_citations_verified': citations, 'new_framework_calls': len(calls),
        'usage': report['usage'], 'index_unchanged': True,
        'verification_runner_sha256': digest(Path(__file__)),
        'verification_provider_calls': 0, 'citation_boundary_uses_stable_span_identity': True,
        'independent_accuracy_evaluation': False}
    write(OUT / 'verification.json', receipt)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    print(result['answer_ko'], flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-saved', action='store_true', help='Verify saved control without another request')
    args = parser.parse_args()
    if args.verify_saved:
        protocol = read(OUT / 'protocol.json')
        verify(read(OUT / 'live_report.json'), protocol['index_sha256'], protocol['code_sha256'])
    else:
        run()
