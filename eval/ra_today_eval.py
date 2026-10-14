"""Frozen 12-case execution, at most 36 Luna calls, no automatic retries."""
from datetime import datetime, timezone
from pathlib import Path
import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from eval.ra_workflow_update_eval import write, digest, audit
from rag.openai_chat import EvaluationAPIChat
from rag.ra_pipeline import RAPipeline, VERSION
from rag import ra_retrieval

ROOT=Path(__file__).resolve().parents[1]
RELEASE=ROOT/'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2'
GUIDELINES=ROOT/'data/guidelines/ra_guidelines_native_2026-10-12_v2'
CASES=ROOT/'eval/ra_today_cases_2026-10-12.json'

def benchmark(pipeline):
    started=time.monotonic();warmup=pipeline.warmup()
    corpus=pipeline.retrieval_corpus()
    understanding=json.loads((ROOT/'data/runs/ra_workflow_update_2026-10-12_v1/final_live_report.json').read_text(encoding='utf-8'))['understanding']
    scopes=[{(c['document_id'],c['version_id']) for c in corpus.chunks if c['product_id']==p and c['jurisdiction']=='EU'} for p in ['SB17','ABP654']]
    cases=[(deepcopy(understanding),scopes[0]),(deepcopy(understanding),scopes[1]),(deepcopy(understanding),scopes[0])]
    cases[1][0]['products']=['ABP654']
    timings={'uncached_bm25':[],'reused_bm25':[]};results={}
    # The same warm encoder, queries, fixed vectors and scopes in both arms.
    # Connection construction is included in the uncached arm each time.
    def run(mode,n,item):
        u,scope=item;corpus.shared_cache=mode=='reused_bm25';corpus.allowed_documents=scope
        tick=time.monotonic();groups=ra_retrieval.retrieve(corpus,u,pipeline.guidelines,scope)
        elapsed=round(time.monotonic()-tick,3)
        hits=[(g['task_id'],g['product'],g['jurisdiction'],[(h['chunk']['chunk_id'],h['score']) for h in g['hits']]) for g in groups]
        return elapsed,hits
    with ThreadPoolExecutor(max_workers=1) as worker:
        for mode in timings:
            for n,item in enumerate(cases):
                elapsed,hits=worker.submit(run,mode,n,item).result()
                timings[mode].append(elapsed);results[(mode,n)]=hits
    identical=all(results['uncached_bm25',n]==results['reused_bm25',n] for n in range(3))
    corpus.shared_cache=True;corpus.allowed_documents=None
    assert identical and results['reused_bm25',0]==results['reused_bm25',2]
    return {'warmup':warmup,'samples':timings,'sample_count_per_arm':3,'exact_rank_and_score_equivalence':identical,
        'scope_A_B_A_restored':True,'cross_thread_sqlite_reuse':True,'new_provider_calls':0,
        'same_warm_encoder':True,'p95_claimed':False,'wall_seconds':round(time.monotonic()-started,3)}

def main(out):
    if out.exists():raise ValueError('Use a new directory; no automatic repeat')
    specs=json.loads(CASES.read_text(encoding='utf-8'))
    code_paths=[*sorted((ROOT/'rag').glob('*.py')),*sorted((ROOT/'rag_service').glob('*.py')),
        *sorted((ROOT/'rag_service/static').glob('*')),Path(__file__),CASES,
        ROOT/'docs/rag/GUIDELINE_STATUS_2026-10-12.json',ROOT/'scripts/start_ra_service.ps1']
    code={str(p.relative_to(ROOT)):digest(p) for p in code_paths if p.is_file()}
    indices={str(p.relative_to(ROOT)):digest(p) for p in [RELEASE/'release_manifest.json',RELEASE/'candidate/chunks.jsonl',GUIDELINES/'manifest.json',GUIDELINES/'chunks.jsonl']}
    write(out/'protocol.json',{'created_at':datetime.now(timezone.utc).isoformat(),'pipeline_version':VERSION,
        'model':'gpt-5.6-luna','reasoning_effort':'none','maximum_new_calls':36,'maximum_per_case':3,
        'automatic_retries':0,'gold_sent':False,'external_grader':False,**specs,
        'code_sha256':code,'index_sha256':indices,'promotion_gates':['12 requests complete without process error',
        'exact own source quotes','selected-source review boundary','E01 actual dose answered',
        'withdrawn FDA guidance absent','new EMA adopted PDF used instead of stored draft']})
    for name in code:
        target=out/'code_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
    client=EvaluationAPIChat(out/'api',max_new_calls=36,model='gpt-5.6-luna')
    pipeline=RAPipeline(RELEASE,guideline_snapshot=GUIDELINES,chat_client=client,research_rounds=1)
    results=[]
    try:
        performance=benchmark(pipeline);write(out/'retrieval_benchmark.json',performance)
        print(json.dumps(performance),flush=True)
        for case in specs['cases']:
            client.case=case['id'];print('Fresh pipeline: '+case['id'],flush=True)
            documents=None
            if case.get('document_id'):
                documents=[d['key'] for d in pipeline.documents if d['document_id']==case['document_id'] and d['version_id'].startswith(case['version_prefix'])]
                assert len(documents)==1
            report=pipeline.ask(case['question'],case['products'],case['jurisdictions'],documents)
            assert len(report['generation_telemetry'])<=3
            assert all(b['document_id']!='GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015' for b in report['results'][0]['evidence'].values())
            write(out/(case['id']+'.json'),report)
            (out/(case['id']+'.md')).write_text(report['review_memo']['markdown'],encoding='utf-8')
            row={'id':case['id'],**audit(report),'usage':report['usage'],'new_calls':len(report['generation_telemetry'])}
            results.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    except Exception as exc:
        message=str(exc).replace(client.secret,'[REDACTED]') if client.secret else str(exc)
        write(out/'failure.json',{'error':message,'diagnostics':pipeline.last_failure,'completed_cases':[r['id'] for r in results]})
        raise
    finally:
        pipeline.close();client.close()
        unchanged=all(digest(ROOT/n)==h for n,h in {**code,**indices}.items())
        write(out/'summary.json',{'rows':results,'attempts':client.attempts,'frozen_files_unchanged':unchanged,
            'input_tokens':sum(r['usage']['input_tokens'] for r in results),'output_tokens':sum(r['usage']['output_tokens'] for r in results),
            'observable_promotion_gates_passed':len(results)==12 and unchanged and results[0]['status']=='answered',
            'independent_accuracy_evaluation':False,'human_expert_certified':False})
        assert unchanged

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);args=p.parse_args();main(args.out.resolve())
