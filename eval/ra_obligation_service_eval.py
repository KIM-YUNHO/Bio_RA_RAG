"""Three actual service controls; the framework uses mini, no external grader."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import urllib.request

from eval.ra_pipeline_update_eval import ROOT,read,write,digest
from rag.ra_pipeline import VERSION


def request(base,path,value=None):
    data=json.dumps(value,ensure_ascii=False).encode('utf-8') if value is not None else None
    req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'} if data else {},
        method='POST' if data else 'GET')
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)


def run(out,base):
    candidate=read(out/'candidate.json')
    assert all(digest(ROOT/n)==v for n,v in candidate['code_sha256'].items())
    summary=request(base,'/api/summary')
    assert summary['pipeline_version']==VERSION and summary['answer_model']=='gpt-4o-mini'
    previous=ROOT/'data/runs/ra_pipeline_completion_2026-10-12_v2'
    normal=read(previous/'service/live_report.json')
    cases={'normal':{'question':normal['question'],'products':['SB17'],'jurisdictions':['EU']},
        'RA04':{'question':read(previous/'api/RA04.json')['question'],'products':['SB17','ABP654'],'jurisdictions':['EU']},
        'RA10':{'question':read(previous/'api/RA10.json')['question'],'jurisdictions':['EU']}}
    write(out/'service/protocol.json',{'created_at':datetime.now(timezone.utc).isoformat(),'runner_sha256':digest(Path(__file__)),
        'service':base,'pipeline_version':VERSION,'cases':cases,'new_call_maximum':9,
        'independent_holdout':False,'gold_sent':False,'automatic_retries':0,'external_grader':False})
    rows=[]
    for key,payload in cases.items():
        print(key,'actual planning, retrieval, generation and internal review',flush=True)
        submitted=request(base,'/api/questions',payload);deadline=time.monotonic()+600
        while True:
            job=request(base,'/api/jobs/'+submitted['id'])
            if job['state']!='running':break
            if time.monotonic()>deadline:raise TimeoutError('Service control deadline; job left available for inspection')
            time.sleep(2)
        write(out/'service'/(key+'_job.json'),job)
        if job['state']!='completed':raise RuntimeError(job.get('error','Service failed'))
        report=job['report'];write(out/'service'/(key+'.json'),report)
        result=report['results'][0];calls=report['generation_telemetry']
        assert report['pipeline_version']==VERSION and len(calls)<=3
        assert all(c['model_requested']=='gpt-4o-mini' for c in calls)
        row={'id':key,'status':result['status'],'claims':len(result['claims']),
            'rejected':len(result['validation']['rejected_items']),'new_calls':len(calls),
            'usage':report['usage'],'context_tokens':result['context_budget']['estimated_prompt_and_schema_tokens']}
        rows.append(row);print(json.dumps(row),flush=True);print(result['answer_ko'],flush=True)
    assert rows[0]['status']=='answered'
    write(out/'service/summary.json',{'rows':rows,'source':'actual HTTP service requests','external_grader':False})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--base',default='http://127.0.0.1:8775')
    args=parser.parse_args();run(args.out.resolve(),args.base)
