"""Apply final local checks to saved generations/reviews, without provider calls."""
from copy import deepcopy
import json
from pathlib import Path
from datetime import datetime, timezone
from eval.ra_today_eval import ROOT, RELEASE, GUIDELINES, CASES
from eval.ra_workflow_update_eval import write,digest,audit
from rag import ra_answer
from rag.ra_pipeline import VERSION
from rag.ra_memo import build
from rag.openai_chat import ApiTokenizer

SOURCE=ROOT/'data/runs/ra_today_completion_2026-10-12_v2'

def main(out):
    if out.exists():raise ValueError('Do not overwrite saved reports')
    specs=json.loads(CASES.read_text(encoding='utf-8'))
    paths=[*sorted((ROOT/'rag').glob('*.py')),*sorted((ROOT/'rag_service').glob('*.py')),
        *sorted((ROOT/'rag_service/static').glob('*')),Path(__file__),CASES,
        ROOT/'docs/rag/GUIDELINE_STATUS_2026-10-12.json',ROOT/'scripts/start_ra_service.ps1']
    code={str(p.relative_to(ROOT)):digest(p) for p in paths if p.is_file()}
    write(out/'protocol.json',{'pipeline_version':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),
        'source_protocol':str(SOURCE/'protocol.json'),'source_protocol_sha256':digest(SOURCE/'protocol.json'),
        'new_provider_calls':0,'fresh_generation':False,'fresh_semantic_review':False,
        'purpose':'Final source-qualifier, month/year and route spelling corrections only.',
        'code_sha256':code,'cases_unchanged':True,'independent_holdout':False})
    for name in code:
        target=out/'code_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
    rows=[]
    for case in specs['cases']:
        saved=SOURCE/(case['id']+'.json');old=json.loads(saved.read_text(encoding='utf-8'));before=old['results'][0]
        class SavedClient:
            tokenizer=ApiTokenizer('gpt-5.6-luna');input_limit=13128;provider='saved_response_replay'
            def __init__(self):self.n=0
            def __call__(self,messages,json_schema=None,provider=None):
                self.n+=1;assert self.n<=2
                payload=before['request_generation'] if self.n==1 else {'claims':before['validation']['semantic_review']['claims']}
                return {'text':json.dumps(payload,ensure_ascii=False)}
        result=ra_answer.generate(old['question'],old['understanding'],before['evidence'],SavedClient(),'spans')
        result['evidence']=deepcopy(before['evidence'])
        reviewed={c['id']:{s['citation_span_id'] for s in c['initial_sources']} for c in before['claims']+before['validation']['rejected_items']}
        assert all({s['citation_span_id'] for s in c['initial_sources']}<=reviewed[c['id']] for c in result['claims'])
        report={**old,'pipeline_version':VERSION,'results':[result],'seconds':None,'timings':{},
            'postprocessing_replay':{'source_report':str(saved),'source_report_sha256':digest(saved),
                'source_pipeline_version':old['pipeline_version'],'new_provider_calls':0,
                'fresh_generation':False,'fresh_semantic_review':False,
                'source_selection_stays_within_previously_reviewed_quotes':True}}
        report['review_memo']=build(report);write(out/(case['id']+'.json'),report)
        (out/(case['id']+'.md')).write_text(report['review_memo']['markdown'],encoding='utf-8')
        row={'id':case['id'],**audit(report),'new_provider_calls':0};rows.append(row)
        print(json.dumps({'id':case['id'],'claims':row['claims'],'rejected':row['rejected'],'status':row['status']}),flush=True)
    assert rows[0]['status']=='answered' and len(rows)==12
    assert all(digest(ROOT/n)==h for n,h in code.items())
    write(out/'summary.json',{'pipeline_version':VERSION,'rows':rows,'new_provider_calls':0,
        'original_unique_provider_calls':36,'all_exact_own_quote_ranges':True,
        'all_source_selection_stays_within_previously_reviewed_quotes':True,'independent_accuracy_evaluation':False})

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);args=p.parse_args();main(args.out.resolve())
