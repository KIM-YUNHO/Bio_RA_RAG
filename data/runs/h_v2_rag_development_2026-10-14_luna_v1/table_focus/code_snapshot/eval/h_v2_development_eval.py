"""Bounded paired development evaluation; source releases stay read-only.

Known development cases, never an independent holdout. Only question and scope
enter RAPipeline. Reference facets and prior source reviews are grading data.
"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
RELEASES = {
    'baseline': ROOT/'data/rag/releases/rag_finalization_2026-10-10_v2',
    'v2': ROOT/'pipeline_h/runs/ustekinumab_20261014_v2/release',
}
GUIDELINES = ROOT/'data/rag/supplemental/ra_guidelines_native_2026-10-12_v2'
MODEL = 'gpt-5.6-luna'
CONTROLS = [
    {'id':'N01', 'question':'EMA SB17-1001 건강인 PK 시험에서 135 mg을 정맥 투여했다는 전제가 맞는지 확인해줘. 실제 투여 용량과 경로를 구분해줘.',
     'products':['SB17'], 'jurisdictions':['EU'], 'control':'false_dose_and_route_premise'},
    {'id':'N02', 'question':'Health Canada BAT2206 제품 모노그래프에서 건강인 PK 시험의 투여 용량을 알려줘.',
     'products':['BAT2206'], 'jurisdictions':['CA'], 'control':'unavailable_product_agency_scope'},
]


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.writing')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def request_key(base, messages, schema):
    value = [base.model, base.config.num_ctx, base.config.num_predict,
             getattr(base,'reasoning_effort',None), messages, schema]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',',':')).encode()).hexdigest()


class ExactClient:
    """Reuse only a complete identical provider input; keep new usage separate."""
    def __init__(self, base, cache, all_bases, cap, case, arm):
        self.base, self.cache, self.all_bases = base, cache, all_bases
        self.cap, self.case, self.arm, self.calls = cap, case, arm, []

    def __getattr__(self, name): return getattr(self.base, name)

    def __call__(self, messages, json_schema=None, provider=None):
        key = request_key(self.base, messages, json_schema)
        if key in self.cache:
            result = deepcopy(self.cache[key]['reply'])
            telemetry = result['telemetry']
            telemetry.update(cache_reused=True, new_provider_call=False,
                cache_source=self.cache[key]['source'],
                original_prompt_tokens=telemetry.get('prompt_tokens',0),
                original_completion_tokens=telemetry.get('completion_tokens',0),
                prompt_tokens=0, completion_tokens=0)
            result.update(prompt_tokens=0, completion_tokens=0)
        else:
            if sum(b.attempts for b in self.all_bases) >= self.cap:
                raise RuntimeError('Frozen phase provider-call cap reached')
            self.base.case = self.case+'_'+self.arm
            result = self.base(messages, json_schema=json_schema, provider=provider)
            result['telemetry'].update(new_provider_call=True, cache_reused=False)
            self.cache[key] = {'reply':deepcopy(result),
                'source':{'case':self.case,'arm':self.arm,'schema':(json_schema or {}).get('name'),
                          'request_key':result['telemetry'].get('request_key')}}
        self.calls.append(deepcopy(result['telemetry']))
        return result


def code_files():
    return [*sorted((ROOT/'rag').glob('*.py')), ROOT/'review_viewer/data.py', Path(__file__)]


def index_files(releases=None):
    result = [GUIDELINES/'manifest.json', GUIDELINES/'chunks.jsonl',
              ROOT/'docs/rag/GUIDELINE_STATUS_2026-10-12.json']
    for folder in (releases or RELEASES).values():
        result += [folder/'release_manifest.json']
        result += [folder/'candidate'/n for n in ('chunks.jsonl','source_units.jsonl',
            'emb/bge-m3/doc.npy','emb/bge-m3/meta.json','emb/bge-m3/chunk_ids.json')]
    return result


def prepare(out):
    if out.exists(): raise ValueError('Use a fresh evaluation directory')
    prior = ROOT/'data/runs/ra_structure_development_2026-10-12_v1/eval_verified/protocol.json'
    cases = read(prior)['cases'] + CONTROLS
    assert len(cases)==19 and len({c['id'] for c in cases})==19
    prior_path = ROOT/'docs/rag/RA_STRUCTURE_DEVELOPMENT_2026-10-12.annotations.json'
    save(out/'cases.json', cases)
    save(out/'prior_review_for_grading_only.json', read(prior_path))
    save(out/'experiment.json', {
        'created_utc':datetime.now(timezone.utc).isoformat(), 'client_date_kst':'2026-10-14',
        'model':MODEL, 'reasoning_effort':'none', 'model_selection':'explicit user instruction',
        'cases':len(cases), 'development_positive_cases':17, 'controls':2,
        'paired_provider_call_cap':95, 'focused_improvement_call_cap':16,
        'question_transfer_call_cap':12, 'total_experiment_call_cap':123,
        'automatic_retries':0, 'availability_probe_calls':0, 'independent_holdout':False,
        'gold_in_provider_input':False, 'grading':'source-based implementation-agent review; not expert certification',
        'same_pipeline_model_guidelines_scope_across_arms':True,
        'comparison_changes':'product release only; matching complete inputs may share exact responses',
        'production_pointer_changes':False, 'source_case_protocol':str(prior),
        'source_case_protocol_sha256':digest(prior),
        'grading_reference_sha256':digest(prior_path)})
    print(json.dumps({'stage':'prepared','cases':len(cases),'paired_cap':95,'model':MODEL}),flush=True)


def execute(out, phase, selected=None, reuse=None, candidate_release=None):
    from rag import answer
    from rag.ra_pipeline import RAPipeline, VERSION
    from rag.openai_chat import EvaluationAPIChat
    from eval.ra_workflow_update_eval import audit
    from rag import ra_quantities, ra_table_citations

    experiment = read(out/'experiment.json')
    case_path = (ROOT/'eval/h_v2_transfer_cases_2026-10-14.json' if phase=='transfer_utf8' else
                 out/'transfer_cases.json' if phase=='transfer' else out/'cases.json')
    case_data = read(case_path)
    cases = case_data['cases'] if isinstance(case_data,dict) else case_data
    if any(c['question'].count('?')>5 or '\ufffd' in c['question'] for c in cases):
        raise ValueError('Question encoding is damaged; no provider calls are permitted')
    if selected:
        cases = [c for c in cases if c['id'] in selected]
        if len(cases)!=len(selected): raise ValueError('Unknown or duplicate case ID')
    arms = ['v2','baseline'] if phase=='paired' else ['v2']
    cap = (experiment['paired_provider_call_cap'] if phase=='paired' else
           experiment['question_transfer_call_cap'] if phase in ('transfer','transfer_utf8') else experiment['focused_improvement_call_cap'])
    if phase=='table_focus':
        cap-=read(out/'improved/summary.json')['new_provider_calls']
    if phase=='improved' and len(cases)>8: raise ValueError('Focused phase supports at most eight cases')
    if phase in ('transfer','transfer_utf8') and len(cases)>4: raise ValueError('Transfer phase supports at most four cases')
    releases = dict(RELEASES)
    if candidate_release:
        if phase=='paired': raise ValueError('Paired baseline releases are frozen')
        candidate_release=Path(candidate_release).resolve()
        if not candidate_release.is_relative_to(ROOT): raise ValueError('Candidate must stay inside workspace')
        releases['v2']=candidate_release
    folder = out/phase
    if folder.exists(): raise ValueError('Do not overwrite or automatically retry a frozen phase')
    code = {str(p.relative_to(ROOT)):digest(p) for p in code_files()}
    indices = {str(p.relative_to(ROOT)):digest(p) for p in index_files(releases)}
    save(folder/'protocol.json', {'created_utc':datetime.now(timezone.utc).isoformat(),
        'pipeline_version':VERSION,'model':MODEL,'reasoning_effort':'none',
        'arms':{a:str(releases[a]) for a in arms},
        'guidelines':str(GUIDELINES),'cases':[c['id'] for c in cases],
        'cases_sha256':digest(case_path),'maximum_new_provider_calls':cap,
        'code_sha256':code,'index_sha256':indices,'reuse':str(reuse) if reuse else None,
        'gold_sent':False,'automatic_retries':0,'independent_holdout':False})
    for name in code:
        target = folder/'code_snapshot'/name
        target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
    cache = read(reuse/'provider_cache.json') if reuse else {}
    bases = {a:EvaluationAPIChat(folder/'api'/a, max_new_calls=min(60,3*len(cases)), model=MODEL) for a in arms}
    pipes, rows = {}, []; started = time.monotonic(); halted = False

    def checkpoint(final=False):
        calls = [c for base in bases.values() for c in base.calls if c.get('new_provider_call') and not c.get('cache_reused')]
        save(folder/'summary.json', {'phase':phase,'rows':rows,'planned_reports':len(cases)*len(arms),
            'finished_reports':len(rows),'new_provider_calls':sum(b.attempts for b in bases.values()),
            'maximum_new_provider_calls':cap,'new_input_tokens':sum(c.get('prompt_tokens',0) for c in calls),
            'new_output_tokens':sum(c.get('completion_tokens',0) for c in calls),
            'seconds':round(time.monotonic()-started,3),'provider_halted':halted,'final':final,
            'source_hashes_unchanged':all(digest(ROOT/n)==h for n,h in indices.items()) if final else None,
            'code_unchanged':all(digest(ROOT/n)==h for n,h in code.items()),
            'semantic_accuracy_certified':False})
        save(folder/'provider_cache.json',cache)

    try:
        encoder = answer.Embedder('bge-m3',local_only=True)
        for arm in arms:
            pipe = RAPipeline(releases[arm], guideline_snapshot=GUIDELINES, research_rounds=1,
                chat_client=ExactClient(bases[arm],cache,list(bases.values()),cap,'warmup',arm),
                encoder_factory=lambda:encoder)
            pipes[arm] = pipe
            print(json.dumps({'stage':'warmup','arm':arm,**pipe.warmup()}),flush=True)
        identities = [{(d['document_id'],d['version_id']) for d in p.documents} for p in pipes.values()]
        assert all(s==identities[0] for s in identities), 'Paired document/version scopes differ'
        for n,case in enumerate(cases):
            order = arms if n%2==0 else list(reversed(arms))
            for arm in order:
                assert all(digest(ROOT/name)==h for name,h in code.items()), 'Frozen code changed'
                pipe = pipes[arm]; base = bases[arm]
                client = ExactClient(base,cache,list(bases.values()),cap,case['id'],arm)
                pipe.client = client; before = sum(b.attempts for b in bases.values())
                print(json.dumps({'stage':'case','arm':arm,'case':case['id']},ensure_ascii=False),flush=True)
                try:
                    docs = None
                    if case.get('document_id'):
                        docs = [d['key'] for d in pipe.documents if d['document_id']==case['document_id']
                                and d['version_id'].startswith(case['version_prefix'])]
                        assert len(docs)==1, 'Explicit source document absent'
                    report = pipe.ask(case['question'],case.get('products'),case.get('jurisdictions'),docs)
                    report['development_execution'] = {'arm':arm,'model':MODEL,
                        'new_provider_calls':sum(b.attempts for b in bases.values())-before,
                        'cache_calls':sum(bool(c.get('cache_reused')) for c in report['generation_telemetry']),
                        'phase':phase,'independent_holdout':False}
                    save(folder/arm/(case['id']+'.json'),report)
                    (folder/arm/(case['id']+'.md')).write_text(report['review_memo']['markdown'],encoding='utf-8')
                    row = {'case':case['id'],'arm':arm,**audit(report),**report['development_execution'],
                        'seconds':report['seconds'],'timings':report['timings'],
                        'context_packets':len(report['results'][0]['evidence']),
                        'answer_ko':report['results'][0]['answer_ko']}
                    result = report['results'][0]; relations = ra_table_citations.bindings(result['evidence'])
                    row['quantity_guard_errors'] = [ra_quantities.check(c,result['evidence']) for c in result['claims']
                                                    if ra_quantities.check(c,result['evidence']) is not None]
                    row['table_guard_errors'] = [ra_table_citations.check(c,result['evidence'],relations) for c in result['claims']
                                                if ra_table_citations.check(c,result['evidence'],relations) is not None]
                except Exception as exc:
                    error = str(exc).replace(base.secret,'[REDACTED]') if base.secret else str(exc)
                    save(folder/arm/(case['id']+'.failure.json'),{'error_type':type(exc).__name__,
                         'error':error,'diagnostics':pipe.last_failure})
                    row = {'case':case['id'],'arm':arm,'error':error,
                           'new_provider_calls':sum(b.attempts for b in bases.values())-before}
                rows.append(row); checkpoint()
                print(json.dumps({k:row.get(k) for k in ('case','arm','status','claims','rejected','new_provider_calls','cache_calls','error')},ensure_ascii=False),flush=True)
                if base.halted:
                    halted=True; raise RuntimeError('Provider halted; stopping without retry')
    finally:
        for pipe in pipes.values(): pipe.close()
        for base in bases.values(): base.close()
        checkpoint(final=True)
        print(json.dumps({'stage':'finished','reports':len(rows),'new_calls':sum(b.attempts for b in bases.values()),'halted':halted}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--phase',choices=['prepare','paired','improved','transfer','table_focus','transfer_utf8'],required=True)
    p.add_argument('--cases',nargs='+');p.add_argument('--reuse',type=Path);p.add_argument('--candidate-release',type=Path)
    a=p.parse_args();out=a.out.resolve()
    if not out.is_relative_to(ROOT): raise ValueError('Evaluation output must stay inside workspace')
    if a.phase=='prepare': prepare(out)
    else: execute(out,a.phase,a.cases,a.reuse,a.candidate_release)
