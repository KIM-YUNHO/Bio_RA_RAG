"""Targeted correction after direct source review; two new provider calls.
The already completed C04 planning response is reused, not requested again."""
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
from eval.ra_today_eval import ROOT,RELEASE,GUIDELINES,CASES
from eval.ra_workflow_update_eval import write,digest,audit
from rag.ra_pipeline import RAPipeline,VERSION
from rag.openai_chat import EvaluationAPIChat

def main(out):
    if out.exists():raise ValueError('Do not repeat automatically')
    case=next(c for c in json.loads(CASES.read_text(encoding='utf-8'))['cases'] if c['id']=='C04')
    old=json.loads((ROOT/'data/runs/ra_today_completion_2026-10-12_v3/C04.json').read_text(encoding='utf-8'))
    saved_path=next(p for p in (ROOT/'data/runs/ra_today_completion_2026-10-12_v1/api/api_calls').glob('*.response.json')
        if (lambda r:r.get('case')=='C04' and r.get('schema')=='query_understanding')(json.loads(p.read_text(encoding='utf-8'))))
    saved=json.loads(saved_path.read_text(encoding='utf-8'))
    paths=[*sorted((ROOT/'rag').glob('*.py')),*sorted((ROOT/'rag_service').glob('*.py')),
        *sorted((ROOT/'rag_service/static').glob('*')),Path(__file__),CASES,
        ROOT/'docs/rag/GUIDELINE_STATUS_2026-10-12.json',ROOT/'scripts/start_ra_service.ps1']
    code={str(p.relative_to(ROOT)):digest(p) for p in paths if p.is_file()}
    write(out/'protocol.json',{'created_at':datetime.now(timezone.utc).isoformat(),'pipeline_version':VERSION,
        'reason':'Direct source review identified available CES criteria missing from retrieved context. Add a literal topic translation, with no expected result in retrieval queries.',
        'case':case,'maximum_new_provider_calls':2,'saved_planning_response':str(saved_path),'saved_planning_response_sha256':digest(saved_path),
        'model':'gpt-5.6-luna','reasoning_effort':'none','automatic_retries':0,'gold_sent':False,
        'fresh_generation_and_review':True,'fresh_planning':False,'independent_holdout':False,'code_sha256':code})
    for name in code:
        target=out/'code_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
    base=EvaluationAPIChat(out/'api',max_new_calls=2,model='gpt-5.6-luna');base.case='C04_targeted_ces_recheck'
    class Client:
        provider='openai'
        def __init__(self):self.calls=base.calls;self.first=True
        def __getattr__(self,name):return getattr(base,name)
        def __call__(self,messages,json_schema=None,provider=None):
            if self.first:
                self.first=False;assert json_schema['name']=='query_understanding'
                assert messages==saved['messages']
                telemetry={**deepcopy(saved),'cache_reused':True,'new_provider_call':False,'source_response':str(saved_path)}
                self.calls.append(telemetry)
                return {'text':saved['raw_text'],'telemetry':telemetry}
            return base(messages,json_schema=json_schema,provider=provider)
    client=Client();pipeline=RAPipeline(RELEASE,guideline_snapshot=GUIDELINES,chat_client=client,research_rounds=1)
    try:
        pipeline.warmup();report=pipeline.ask(case['question'],None,['CA'])
        report['evaluation_execution']={'fresh_generation':True,'fresh_semantic_review':True,'planning_reused':True,'new_provider_calls':base.attempts}
        assert base.attempts==2
        own='\n'.join(s['quote'] for c in report['results'][0]['claims'] for s in c['sources'])
        assert 'not typically required' in own and 'the role of the studies and the value' in own
        write(out/'C04.json',report);(out/'C04.md').write_text(report['review_memo']['markdown'],encoding='utf-8')
        unchanged=all(digest(ROOT/n)==h for n,h in code.items());assert unchanged
        write(out/'summary.json',{'case':'C04','before_claims':len(old['results'][0]['claims']),**audit(report),
            'new_provider_calls':base.attempts,'frozen_code_unchanged':unchanged,'required_source_conditions_in_final_quotes':True,
            'total_today_fixed_evaluation_calls_plus_targeted_recheck':38,'independent_accuracy_evaluation':False})
        print(json.dumps({'case':'C04','claims':len(report['results'][0]['claims']),'status':report['results'][0]['status'],'new_provider_calls':base.attempts},ensure_ascii=False))
    finally:pipeline.close();base.close()

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);args=p.parse_args();main(args.out.resolve())
