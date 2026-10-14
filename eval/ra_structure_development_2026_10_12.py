"""Fresh development verification on the repaired index; production is unchanged."""
from datetime import datetime, timezone
from copy import deepcopy
import json
from pathlib import Path
import time

from eval.ra_structure_compare_2026_10_12 import PairedClient, live_state, stable_hash
from eval.ra_workflow_update_eval import audit, digest, write
from rag import answer, ra_quantities, ra_table_citations
from rag.openai_chat import EvaluationAPIChat
from rag.ra_pipeline import RAPipeline, VERSION

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = ROOT/'data/runs/ra_structure_compare_2026-10-12_v1'
RELEASE = ROOT/'data/rag/releases/rag_source_structure_2026-10-11_v1'
GUIDELINES = ROOT/'data/guidelines/ra_guidelines_native_2026-10-12_v2'
PROBES = [
    {'id':'T03','question':'EMA SB17-1001 PK 분석표에서 SB17·EU Stelara·US Stelara의 Cmax 중앙값을 ng/mL 단위로 각각 확인해줘. 산술평균과 구분해줘.',
     'products':['SB17'],'jurisdictions':['EU']},
    {'id':'T04','question':'EMA ABP 654의 Study 20190230 PK 요약표에서 US ustekinumab군과 EU ustekinumab군의 Tmax 중앙값·범위 및 t1/2 평균·표준편차를 시간 단위로 확인해줘. 각 군과 통계량을 구분해줘.',
     'products':['ABP654'],'jurisdictions':['EU']},
    {'id':'T05','question':'Health Canada Steqeyma 제품 모노그래프의 성인 크론병 정맥 유도 용량표에서 체중 55 kg 이하, 55 kg 초과 85 kg 이하, 85 kg 초과의 용량과 바이알 수를 확인해줘. 이후 피하 유지 용량과 투여 일정도 구분해줘.',
     'products':['CTP43'],'jurisdictions':['CA']},
]


def prior_responses(folder):
    """Reuse completed responses only when the entire provider input is identical."""
    cache, plans, provenance = {}, {}, []
    if folder is None:
        return cache, plans, provenance
    for path in sorted((folder/'api/api_calls').glob('*.response.json')):
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('status')!='completed':continue
        request_path=path.with_name(path.name.replace('.response.json','.request.json'))
        request=json.loads(request_path.read_text(encoding='utf-8'))
        payload=request['payload'];case=data['case'].split('_')[0]
        assert payload['messages']==data['messages']
        assert payload['model']=='gpt-5.6-luna' and payload.get('reasoning_effort')=='none'
        if data.get('schema')=='query_understanding':plans[case]=(data,path)
        contract=payload.get('response_format',{}).get('json_schema')
        contract={k:v for k,v in contract.items() if k!='strict'} if contract else None
        key=stable_hash({'case':case,'model':payload['model'],'reasoning_effort':'none',
                         'messages':payload['messages'],'schema':contract})
        reply={'text':data['raw_text'],'model_reported':data.get('model_reported'),
               'prompt_tokens':data['prompt_tokens'],'completion_tokens':data['completion_tokens'],
               'finish_reason':data.get('finish_reason'),'telemetry':deepcopy(data)}
        source={'case':case,'schema':data['schema'],'path':str(path),'sha256':digest(path),
                'request_path':str(request_path),'request_sha256':digest(request_path),'exact_payload_key':key}
        cache[key]=(reply,source);provenance.append(source)
    return cache,plans,provenance


def main(out, selected=None, reuse_from=None):
    if out.exists():
        raise ValueError('Choose a new output directory; no automatic retry or overwrite')
    assert VERSION == 'ra-research-rag-4.8.9'
    previous = json.loads((PREVIOUS/'protocol.json').read_text(encoding='utf-8'))
    cases = previous['cases'] + PROBES
    order = ['E01','T01','T02','E03','C03','U03','E04','E02','U01','U02','U04','C01','C02','C04','T03','T04','T05']
    cases = sorted(cases, key=lambda c:order.index(c['id']))
    if selected:
        cases = [c for c in cases if c['id'] in selected]
        assert len(cases) == len(selected)
    plans = {}
    for case, info in previous['fixed_planning_responses'].items():
        path=Path(info['path'])
        assert digest(path)==info['sha256']
        plans[case]=(json.loads(path.read_text(encoding='utf-8')),path)
    for path in (PREVIOUS/'api/api_calls').glob('*.response.json'):
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema')=='query_understanding' and data.get('status')=='completed':
            plans[data['case'].split('_')[0]]=(data,path)
    cache,cache_provenance={},[]
    for folder in reuse_from or []:
        previous_cache,cached_plans,provenance=prior_responses(folder)
        cache.update(previous_cache);plans.update(cached_plans);cache_provenance.extend(provenance)
    maximum=2*len(cases)+sum(c['id'] not in plans for c in cases)
    paths=[*sorted((ROOT/'rag').glob('*.py')),*sorted((ROOT/'rag_service').glob('*.py')),
           Path(__file__),ROOT/'tests/test_ra_table_value_preservation.py']
    code={str(p.relative_to(ROOT)):digest(p) for p in paths}
    indices=previous['index_sha256']
    assert all(digest(ROOT/name)==sha for name,sha in indices.items())
    before_live=live_state()
    write(out/'protocol.json',{'created_at':datetime.now(timezone.utc).isoformat(),
        'pipeline_version':VERSION,'model':'gpt-5.6-luna','reasoning_effort':'none',
        'cases':cases,'case_count':len(cases),'maximum_new_provider_calls':maximum,
        'development_index':str(RELEASE),'guidelines':str(GUIDELINES),
        'baseline_reports':str(PREVIOUS/'structure'),'baseline_pipeline_version':'ra-research-rag-4.8.3',
        'fixed_plans':{c['id']:{'path':str(plans[c['id']][1]),'sha256':digest(plans[c['id']][1])}
                       for c in cases if c['id'] in plans},
        'response_reuse_policy':'completed exact identical messages, schema, model, reasoning and case only',
        'reused_response_candidates':cache_provenance,'gold_sent':False,'external_grader':False,
        'independent_holdout':False,'probe_note':'New question variations over known sources; not independent holdout.',
        'automatic_retries':0,'production_changes':False,'live_before':before_live,
        'code_sha256':code,'index_sha256':indices})
    for name in code:
        target=out/'code_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
    base=EvaluationAPIChat(out/'api',max_new_calls=maximum,model='gpt-5.6-luna')
    encoder=answer.Embedder('bge-m3',local_only=True)
    pipeline=RAPipeline(RELEASE,guideline_snapshot=GUIDELINES,research_rounds=1,
        chat_client=PairedClient(base,'structure',cases[0]['id'],plans,cache),encoder_factory=lambda:encoder)
    rows=[];started=time.monotonic()
    try:
        print(json.dumps({'stage':'warmup',**pipeline.warmup()}),flush=True)
        for case in cases:
            assert all(digest(ROOT/name)==sha for name,sha in code.items()),'Frozen source changed'
            pipeline.client=PairedClient(base,'structure',case['id'],plans,cache)
            before=base.attempts
            print(json.dumps({'stage':'case','case':case['id']}),flush=True)
            try:
                documents=None
                if case.get('document_id'):
                    documents=[d['key'] for d in pipeline.documents if d['document_id']==case['document_id']
                               and d['version_id'].startswith(case['version_prefix'])]
                    assert len(documents)==1
                report=pipeline.ask(case['question'],case['products'],case['jurisdictions'],documents)
                calls=pipeline.client.calls
                generation=[c for c in calls if c['schema'] in ('ra_question_obligations','ra_requested_answer','ra_requested_synthesis','ra_grounded_answer') or
                            c['schema'] not in ('query_understanding','ra_selected_claim_truth','ra_bound_context_review')]
                reviews=[c for c in calls if c['schema']=='ra_selected_claim_truth']
                report['development_verification']={'fresh_generation':bool(generation) and all(c.get('new_provider_call') for c in generation),
                    'fresh_semantic_review':bool(reviews) and all(c.get('new_provider_call') for c in reviews),
                    'fixed_planning':case['id'] in plans,'new_provider_calls':base.attempts-before,
                    'executions':[{'schema':c['schema'],'execution':c.get('comparison_execution'),
                                   'request_key':c.get('request_key')} for c in calls]}
                write(out/(case['id']+'.json'),report)
                (out/(case['id']+'.md')).write_text(report['review_memo']['markdown'],encoding='utf-8')
                r=report['results'][0];relations=ra_table_citations.bindings(r['evidence'])
                assert all(ra_quantities.check(c,r['evidence']) is None and
                    ra_table_citations.check(c,r['evidence'],relations) is None for c in r['claims'])
                assert all(e['document_id']!='GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015' for e in r['evidence'].values())
                row={'case':case['id'],**audit(report),'new_provider_calls':base.attempts-before,
                     'table_and_quantity_guards_passed':True,
                     'question_requirement_coverage':r['question_requirement_coverage'],
                     'citation_completions':r['validation']['citation_completion']}
            except Exception as exc:
                error=str(exc).replace(base.secret,'[REDACTED]') if base.secret else str(exc)
                write(out/(case['id']+'.failure.json'),{'error':error,'diagnostics':pipeline.last_failure})
                row={'case':case['id'],'error':error,'new_provider_calls':base.attempts-before}
            rows.append(row)
            print(json.dumps({k:row.get(k) for k in ('case','status','claims','rejected','new_provider_calls','error')}),flush=True)
            if base.halted:break
    finally:
        pipeline.close();base.close()
        after_live=live_state()
        ledger=json.loads(base.ledger_path.read_text(encoding='utf-8'))
        calls=[c for c in ledger['attempts'] if c.get('evaluation_out')==str(base.out)]
        unchanged=all(digest(ROOT/name)==sha for name,sha in code.items())
        indices_unchanged=all(digest(ROOT/name)==sha for name,sha in indices.items())
        write(out/'summary.json',{'rows':rows,'new_provider_calls':len(calls),
            'input_tokens':sum(c.get('prompt_tokens',0) for c in calls),
            'output_tokens':sum(c.get('completion_tokens',0) for c in calls),
            'frozen_code_unchanged':unchanged,'frozen_indices_unchanged':indices_unchanged,
            'live_configuration_unchanged':before_live==after_live,'live_after':after_live,
            'all_cases_present_without_process_error':len(rows)==len(cases) and not any('error' in r for r in rows),
            'seconds':round(time.monotonic()-started,3),'independent_accuracy_evaluation':False,
            'human_expert_certified':False,'promotion_decision':'Pending direct source review.'})
        assert unchanged and indices_unchanged and before_live==after_live


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--cases',nargs='+')
    parser.add_argument('--reuse-from',type=Path,action='append')
    args=parser.parse_args();main(args.out.resolve(),args.cases,[p.resolve() for p in args.reuse_from or []])
