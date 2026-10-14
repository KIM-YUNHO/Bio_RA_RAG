"""One UTF-8 HTTP service probe, capped at the service's three model calls.

Existing protocol file prevents resubmission. Original question comes from the
saved UTF-8 report, avoiding PowerShell pipe encoding changes.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.request

from eval.ra_workflow_update_eval import ROOT, read, write, audit
from rag.ra_pipeline import VERSION
from rag.ra_source_constraints import check


def run(out):
    protocol = out/'final_service_protocol.json'
    if protocol.exists():
        raise ValueError('Probe already submitted or reserved; never automatically resubmit')
    url = 'http://127.0.0.1:8775'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path):
        with opener.open(url+path, timeout=20) as response:
            return json.load(response)
    summary = get('/api/summary')
    assert summary['pipeline_version'] == VERSION == 'ra-research-rag-4.7.2'
    assert summary['answer_model'] == 'gpt-5.6-luna'
    question = read(ROOT/'data/runs/ra_luna_deployment_2026-10-12_v1/live_report.json')['question']
    assert '?' not in question and any(ord(c)>127 for c in question)
    payload = {'question':question, 'products':['SB17'], 'jurisdictions':['EU']}
    code = {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [*sorted((ROOT/'rag').glob('*.py')),ROOT/'rag_service/__main__.py',
                      ROOT/'rag_service/static/app.js',ROOT/'rag_service/static/style.css',Path(__file__)]}
    frozen = read(out/'baseline.json')['index_sha256']
    assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in frozen.items())
    write(protocol, {'created_at':datetime.now(timezone.utc).isoformat(), 'payload':payload,
        'pipeline_version':VERSION, 'max_provider_calls':3, 'automatic_resubmissions':0,
        'gold_sent':False, 'expert_reviewed':False, 'independent_accuracy_evaluation':False,
        'original_question_from_utf8_json':True, 'code_sha256':code,'index_sha256':frozen})
    with opener.open(urllib.request.Request(url+'/api/questions',
            data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type':'application/json'}),timeout=20) as response:
        submitted=json.load(response)
    write(out/'final_service_submitted.json',submitted)
    print('Submitted: '+submitted['id'],flush=True)
    for _ in range(180):
        job=get('/api/jobs/'+submitted['id'])
        if job['state']!='running':break
        time.sleep(1)
    else:raise RuntimeError('Polling deadline reached; do not resubmit')
    write(out/'final_live_job.json',job)
    assert job['state']=='completed',job.get('error')
    report=job['report'];assert report['question']==question and job['question']==question
    assert report['pipeline_version']==VERSION
    write(out/'final_live_report.json',report)
    (out/'final_live_memo.md').write_text(report['review_memo']['markdown'],encoding='utf-8')
    result=report['results'][0]
    assert all(check(c,result['evidence']) is None for c in result['claims'])
    row=audit(report)
    calls=report['generation_telemetry'];assert len(calls)<=3
    assert all(c['model_requested']=='gpt-5.6-luna' and c['model_reported']=='gpt-5.6-luna' for c in calls)
    archive=ROOT/'data/runs/ra_service_reports'/(job['id']+'.json')
    assert read(archive)==job
    assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in code.items())
    assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in frozen.items())
    write(out/'final_service_verification.json',{'id':job['id'],'url':url+'/#job='+job['id'],
        'fresh_actual_service':True,'question_preserved_utf8':True,'pipeline_version':VERSION,'audit':row,
        'usage':report['usage'],'calls':len(calls),'all_models_luna':True,'runtime_code_and_index_unchanged':True,
        'persisted_job_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'memo_saved':True,'browser_visual_verification':False})
    print(json.dumps({'audit':row,'usage':report['usage'],'url':url+'/#job='+job['id']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    run(parser.parse_args().out.resolve())
