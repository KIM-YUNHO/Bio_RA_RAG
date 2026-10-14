"""Explicit continuation after a diagnosed local routing failure. Reuse its
completed planning response; never resend provider requests or overwrite v1."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from eval.ra_today_eval import ROOT, RELEASE, GUIDELINES, CASES
from eval.ra_workflow_update_eval import write, digest, audit
from rag import ra_answer
from rag.ra_pipeline import RAPipeline, VERSION
from rag.ra_memo import build
from rag.openai_chat import EvaluationAPIChat, ApiTokenizer

OLD=ROOT/'data/runs/ra_today_completion_2026-10-12_v1'

def main(out):
    if out.exists():raise ValueError('Do not overwrite a prior run')
    specs=json.loads(CASES.read_text(encoding='utf-8'))
    paths=[*sorted((ROOT/'rag').glob('*.py')),*sorted((ROOT/'rag_service').glob('*.py')),
        *sorted((ROOT/'rag_service/static').glob('*')),Path(__file__),CASES,
        ROOT/'docs/rag/GUIDELINE_STATUS_2026-10-12.json',ROOT/'scripts/start_ra_service.ps1']
    code={str(p.relative_to(ROOT)):digest(p) for p in paths if p.is_file()}
    indices={str(p.relative_to(ROOT)):digest(p) for p in [RELEASE/'release_manifest.json',RELEASE/'candidate/chunks.jsonl',GUIDELINES/'manifest.json',GUIDELINES/'chunks.jsonl']}
    write(out/'protocol.json',{'created_at':datetime.now(timezone.utc).isoformat(),'pipeline_version':VERSION,
        'model':'gpt-5.6-luna','reasoning_effort':'none','original_protocol':str(OLD/'protocol.json'),
        'original_protocol_sha256':digest(OLD/'protocol.json'),'fixed_cases_unchanged':True,'maximum_unique_new_calls_across_both_runs':36,
        'automatic_retries':0,'continuation_reason':'Corrected explicit guidance routing witness and false source-qualifier checks after inspecting v1 failures.',
        'completed_cases':'Replay only, no new model generation. Original claim-review inputs remain the proof boundary.',
        'U04':'Reuse the completed cached planning response, then generate/review once. Remaining CA cases run once.',
        'gold_sent':False,'independent_holdout':False,'cases':specs['cases'],'code_sha256':code,'index_sha256':indices})
    for name in code:
        target=out/'code_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
    rows=[]
    for case in specs['cases']:
        saved=OLD/(case['id']+'.json')
        if not saved.is_file():continue
        old=json.loads(saved.read_text(encoding='utf-8'));before=old['results'][0]
        class SavedClient:
            tokenizer=ApiTokenizer('gpt-5.6-luna');input_limit=13128;provider='saved_response_replay'
            def __init__(self):self.n=0
            def __call__(self,messages,json_schema=None,provider=None):
                self.n+=1;assert self.n<=2
                payload=before['request_generation'] if self.n==1 else {'claims':before['validation']['semantic_review']['claims']}
                return {'text':json.dumps(payload,ensure_ascii=False)}
        result=ra_answer.generate(old['question'],old['understanding'],before['evidence'],SavedClient(),'spans')
        result['evidence']=deepcopy(before['evidence'])
        # Candidate source selections may never borrow an unreviewed span.
        reviewed={c['id']:{s['citation_span_id'] for s in c['initial_sources']} for c in before['claims']+before['validation']['rejected_items']}
        assert all({s['citation_span_id'] for s in c['initial_sources']}<=reviewed[c['id']] for c in result['claims'])
        report={**old,'pipeline_version':VERSION,'results':[result],'seconds':None,'timings':{},
            'postprocessing_replay':{'source_report':str(saved),'source_report_sha256':digest(saved),
                'source_pipeline_version':old['pipeline_version'],'source_report_seconds':old['seconds'],
                'new_provider_calls':0,'fresh_generation':False,'fresh_semantic_review':False,
                'source_selection_stays_within_previously_reviewed_quotes':True}}
        report['review_memo']=build(report);write(out/(case['id']+'.json'),report)
        (out/(case['id']+'.md')).write_text(report['review_memo']['markdown'],encoding='utf-8')
        row={'id':case['id'],'execution':'postprocessing_replay','source_status':before['status'],**audit(report),'new_calls':0}
        rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    client=EvaluationAPIChat(OLD/'api',max_new_calls=36,model='gpt-5.6-luna')
    pipeline=RAPipeline(RELEASE,guideline_snapshot=GUIDELINES,chat_client=client,research_rounds=1)
    try:
        pipeline.warmup()
        for case in specs['cases']:
            if (OLD/(case['id']+'.json')).is_file():continue
            client.case=case['id'];print('Continue pipeline: '+case['id'],flush=True)
            report=pipeline.ask(case['question'],case['products'],case['jurisdictions'])
            telemetry=report['generation_telemetry'];assert len(telemetry)<=3
            if case['id']=='U04':assert telemetry[0].get('cache_reused') is True
            assert all(b['document_id']!='GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015' for b in report['results'][0]['evidence'].values())
            write(out/(case['id']+'.json'),report)
            (out/(case['id']+'.md')).write_text(report['review_memo']['markdown'],encoding='utf-8')
            row={'id':case['id'],'execution':'continued_pipeline',**audit(report),
                'new_calls':sum(not c.get('cache_reused',False) for c in telemetry)}
            rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    except Exception as exc:
        message=str(exc).replace(client.secret,'[REDACTED]') if client.secret else str(exc)
        write(out/'failure.json',{'error':message,'diagnostics':pipeline.last_failure,'completed_cases':[r['id'] for r in rows]})
        raise
    finally:
        pipeline.close();client.close()
        unchanged=all(digest(ROOT/n)==h for n,h in {**code,**indices}.items())
        ledger=json.loads(client.ledger_path.read_text(encoding='utf-8'))
        calls=[a for a in ledger['attempts'] if a.get('evaluation_out')==str(client.out)]
        assert len(calls)<=36
        write(out/'summary.json',{'rows':rows,'unique_provider_calls_across_v1_v2':len(calls),
            'input_tokens':sum(c.get('prompt_tokens',0) for c in calls),'output_tokens':sum(c.get('completion_tokens',0) for c in calls),
            'frozen_v2_files_unchanged':unchanged,'all_fixed_cases_present':len(rows)==12,
            'mixed_fresh_and_postprocessing_replay':True,'E01_replayed_status':next((r['status'] for r in rows if r['id']=='E01'),None),
            'independent_accuracy_evaluation':False,'human_expert_certified':False})
        assert unchanged

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);args=p.parse_args();main(args.out.resolve())
