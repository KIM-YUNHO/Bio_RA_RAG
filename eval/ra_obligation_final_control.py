"""One fresh control of the promoted primary framework; maximum three mini calls."""
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import urllib.error
from eval.ra_pipeline_update_eval import ROOT,read,write,digest
from eval.ra_obligation_service_eval import request
from rag.ra_pipeline import VERSION

out=ROOT/'data/runs/ra_pipeline_obligations_2026-10-12_v7'
base='http://127.0.0.1:8775'
question=read(ROOT/'data/runs/ra_pipeline_completion_2026-10-12_v2/service/live_report.json')['question']
deadline=time.monotonic()+120
while True:
    try:summary=request(base,'/api/summary');break
    except urllib.error.URLError:
        if time.monotonic()>deadline:raise
        time.sleep(2)
assert summary['pipeline_version']==VERSION and summary['answer_model']=='gpt-4o-mini'
payload={'question':question,'products':['SB17'],'jurisdictions':['EU']}
write(out/'service/protocol.json',{'created_at':datetime.now(timezone.utc).isoformat(),'runner_sha256':digest(Path(__file__)),
    'pipeline_version':VERSION,'service':base,'request':payload,'new_call_maximum':3,
    'external_grader':False,'automatic_provider_retry':False,'gold_sent':False})
print('primary service control submitted',flush=True)
submitted=request(base,'/api/questions',payload);deadline=time.monotonic()+600
while True:
    job=request(base,'/api/jobs/'+submitted['id'])
    if job['state']!='running':break
    if time.monotonic()>deadline:raise TimeoutError('Control deadline; inspect existing job, do not resubmit')
    time.sleep(2)
write(out/'service/live_job.json',job)
if job['state']!='completed':raise RuntimeError(job.get('error','Service failed'))
report=job['report'];write(out/'service/live_report.json',report)
result=report['results'][0];calls=report['generation_telemetry']
assert len(calls)<=3 and all(c['model_requested']=='gpt-4o-mini' for c in calls)
write(out/'service/control.json',{'status':result['status'],'claims':len(result['claims']),
    'rejected':len(result['validation']['rejected_items']),'new_calls':len(calls),'usage':report['usage'],
    'pipeline_version':report['pipeline_version'],'source':'fresh actual primary service request'})
print(result['answer_ko'],flush=True)
print(json.dumps(report['usage']),flush=True)
assert result['status']=='answered'
