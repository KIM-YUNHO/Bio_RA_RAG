"""Frozen generation-stage experiment on saved admitted sources, mini only."""
from copy import deepcopy
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
import time

from eval.ra_pipeline_update_eval import ROOT,read,digest,write
from rag import ra_answer
from rag.openai_chat import EvaluationAPIChat
from rag.ra_pipeline import VERSION


def run(out,selected=None,rebuilt_context=False):
    frozen=read(out/'candidate.json')
    assert all(digest(ROOT/name)==value for name,value in frozen['code_sha256'].items())
    assert all(digest(ROOT/name)==value for name,value in read(out/'baseline.json')['index_sha256'].items())
    previous=ROOT/'data/runs/ra_pipeline_completion_2026-10-12_v2'
    cases={key:previous/'api'/(key+'.json') for key in ['RA04','RA10','RA14']}
    cases.update({p.stem:p for p in (previous/'package_eval').glob('EMA*.json')})
    if selected:cases={key:cases[key] for key in selected}
    write(out/'generation_protocol.json',{'created_at':datetime.now(timezone.utc).isoformat(),
        'runner_sha256':digest(Path(__file__)),'candidate_sha256':digest(out/'candidate.json'),
        'cases':list(cases),'source_report_sha256':{key:digest(path) for key,path in cases.items()},
        'source_context':('reading context rebuilt from actual saved rankings' if rebuilt_context else
            'exact previously admitted blocks; controlled generation-stage comparison'),
        'rebuilt_context_sha256':({key:digest(out/'local'/(key+'.json')) for key in cases} if rebuilt_context else {}),
        'model':'gpt-4o-mini','new_call_limit':12,'planning_reused':True,'new_search_executions':0,
        'gold_sent':False,'independent_holdout':False,'automatic_retries':0,'external_grader':False})
    client=EvaluationAPIChat(out/'api',max_new_calls=12,model='gpt-4o-mini')
    rows=[]
    try:
        for key,path in cases.items():
            report=read(path);before=deepcopy(report['results'][0]);client.case=key
            if rebuilt_context:
                context=read(out/'local'/(key+'.json'))
                assert context['source_report_sha256']==digest(path)
                before.update(evidence=context['evidence'],context_budget=context['context_budget'],
                    omitted_chunks=context['context_budget']['omitted'])
            client.calls=[];started=time.monotonic()
            print(key,'generation and internal selected-source review',flush=True)
            try:
                result=ra_answer.generate(report['question'],report['understanding'],before['evidence'],client,'spans')
            except Exception as error:
                write(out/'api'/(key+'.failure.json'),{'error':str(error),'case':key,'calls':client.calls,
                    'question':report['question'],'evidence':before['evidence']})
                raise
            result.update({k:before[k] for k in ['unit','evidence','context_budget','omitted_chunks']})
            calls=deepcopy(client.calls)
            report.update(pipeline_version=VERSION,results=[result],generation_telemetry=calls,
                usage={'input_tokens':sum(c.get('prompt_tokens',0) for c in calls),
                       'output_tokens':sum(c.get('completion_tokens',0) for c in calls)},
                context_origin='rebuilt_actual_saved_rankings' if rebuilt_context else 'saved_admitted_source_context',generation_stage_experiment={
                    'source_report':str(path),'sha256':digest(path),'previous_pipeline_version':'4.5.1',
                    'planning_and_retrieval_reused':True,'generation_seconds':round(time.monotonic()-started,3)})
            write(out/'api'/(key+'.json'),report)
            row={'id':key,'status':result['status'],'claims':len(result['claims']),
                'rejected':len(result['validation']['rejected_items']),
                'required_slots':len(result['question_requirement_coverage']['requirements']),
                'slots_with_verified_claim':sum(r['has_verified_claim'] for r in result['question_requirement_coverage']['requirements']),
                'new_calls':sum(not c.get('cache_reused',False) for c in calls)}
            rows.append(row);print(json.dumps(row),flush=True);print(result['answer_ko'],flush=True)
        assert all(digest(ROOT/name)==value for name,value in frozen['code_sha256'].items())
        write(out/'generation_summary.json',{'rows':rows,'independent_holdout':False,'gold_sent':False,
            'limited_scope':'Generation on fixed supplied sources, not full retrieval or independent accuracy evaluation.'})
    finally:client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--cases',nargs='+')
    parser.add_argument('--rebuilt-context',action='store_true')
    args=parser.parse_args()
    run(args.out.resolve(),args.cases,args.rebuilt_context)
