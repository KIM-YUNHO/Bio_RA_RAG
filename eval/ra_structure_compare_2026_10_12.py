"""Paired RA comparison with fixed plans, code, model and guideline snapshot.

Only the product index changes. Identical provider payloads within a case share
the exact response, making them equivalence controls rather than fresh samples.
The 12 exposed development cases plus two source-table probes are not a holdout.
No production server, release pointer, archived answer or review record changes.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.request import urlopen

from eval.ra_workflow_update_eval import audit, digest, write
from preprocess.ingest import stable_hash
from rag import answer, ra_tasks
from rag.openai_chat import ApiTokenizer, EvaluationAPIChat
from rag.ra_pipeline import RAPipeline, VERSION

ROOT = Path(__file__).resolve().parents[1]
RELEASES = {
    'baseline': ROOT/'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2',
    'structure': ROOT/'data/rag/releases/rag_source_structure_2026-10-11_v1',
}
GUIDELINES = ROOT/'data/guidelines/ra_guidelines_native_2026-10-12_v2'
CASES = ROOT/'eval/ra_today_cases_2026-10-12.json'
PLAN_FOLDER = ROOT/'data/runs/ra_today_completion_2026-10-12_v1/api/api_calls'
MODEL = 'gpt-5.6-luna'
MAX_CALLS = 58  # 14 pairs * two synthesis/review calls + two new shared plans.
PROBES = [
    {'id': 'T01', 'question': 'EMA SB17-1001 PK 분석표에서 SB17·EU Stelara·US Stelara의 Cmax 산술평균과 표준편차를 각각 원문 단위와 함께 정리해줘. 중앙값이나 기하평균과 구분해줘.',
     'products': ['SB17'], 'jurisdictions': ['EU'],
     'required_facets': ['three group arithmetic Cmax means', 'three SDs', 'ng/mL units', 'mean versus median or geometric mean'],
     'prior_exposure': 'New wording deliberately targets the previously inspected repaired table; not independent.',
     'source_probe': {'document_id': 'EMA:63563', 'pdf_page': 47, 'table': 'Table 8'}},
    {'id': 'T02', 'question': 'EMA ABP 654의 Study 20190230 PK 요약표에서 ABP 654군의 Tmax 중앙값·범위와 t1/2 평균·표준편차를 단위와 함께 확인해줘. 두 통계량을 구분해줘.',
     'products': ['ABP654'], 'jurisdictions': ['EU'],
     'required_facets': ['ABP654 Tmax median and range', 'ABP654 half-life mean and SD', 'hours', 'correct column and statistic identities'],
     'prior_exposure': 'New wording deliberately targets the previously inspected repaired table; not independent.',
     'source_probe': {'document_id': 'EMA:64196', 'pdf_page': 44, 'table': 'Table 4'}},
]


def live_state():
    with urlopen('http://127.0.0.1:8775/api/summary', timeout=10) as response:
        summary = json.load(response)
    return {k: summary[k] for k in ('pipeline_version', 'run_name', 'chunks_sha256', 'answer_model', 'guideline_snapshot')}


def chunks(path):
    with (path/'candidate/chunks.jsonl').open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def inventory():
    arms = {arm: chunks(path) for arm, path in RELEASES.items()}
    identity = lambda c: (c['document_id'], c['version_id'], c['product_id'], c['jurisdiction'])
    assert {identity(c) for c in arms['baseline']} == {identity(c) for c in arms['structure']}
    signature = lambda c: (c['document_id'], c['version_id'], c['pdf_page'], c['header'], c['text'])
    signatures = {arm: {signature(c) for c in rows} for arm, rows in arms.items()}
    changed = {arm: [c for c in rows if signature(c) not in signatures['structure' if arm == 'baseline' else 'baseline']]
               for arm, rows in arms.items()}
    return {'document_version_product_jurisdiction_sets_identical': True,
            'documents': len({(c['document_id'], c['version_id']) for c in arms['baseline']}),
            'counts': {arm: len(rows) for arm, rows in arms.items()},
            'text_changed_chunks': {arm: len(rows) for arm, rows in changed.items()},
            'changed_locations': {arm: [{'document_id': doc, 'pdf_page': page, 'count': n}
                for (doc, page), n in sorted(Counter((c['document_id'], c['pdf_page']) for c in rows).items())]
                for arm, rows in changed.items()}}


class PairedClient:
    provider = 'openai'
    local_only = False
    source_selection = True
    model = MODEL
    reasoning_effort = 'none'

    def __init__(self, base, arm, case, saved_plans, cache):
        self.base, self.arm, self.case = base, arm, case
        self.saved_plans, self.cache = saved_plans, cache
        self.calls = []

    def __getattr__(self, name):
        return getattr(self.base, name)

    def __call__(self, messages, json_schema=None, provider=None):
        name = (json_schema or {}).get('name', 'text')
        if name == 'query_understanding' and self.case in self.saved_plans:
            saved, path = self.saved_plans[self.case]
            assert messages == saved['messages'], 'Saved planning input changed: '+self.case
            assert json_schema == ra_tasks.SCHEMA
            telemetry = {**deepcopy(saved), 'cache_reused': True, 'new_provider_call': False,
                         'comparison_execution': 'fixed_saved_plan', 'source_response': str(path),
                         'original_prompt_tokens': saved['prompt_tokens'],
                         'original_completion_tokens': saved['completion_tokens'],
                         'prompt_tokens': 0, 'completion_tokens': 0}
            self.calls.append(telemetry)
            return {'text': saved['raw_text'], 'telemetry': telemetry}
        key = stable_hash({'case': self.case, 'model': MODEL, 'reasoning_effort': 'none',
                           'messages': messages, 'schema': json_schema})
        if key in self.cache:
            reply, source = self.cache[key]
            reply = deepcopy(reply)
            telemetry = reply['telemetry']
            telemetry.update(cache_reused=True, new_provider_call=False,
                comparison_execution='exact_identical_payload_reuse', comparison_source=source,
                original_prompt_tokens=telemetry['prompt_tokens'],
                original_completion_tokens=telemetry['completion_tokens'], prompt_tokens=0, completion_tokens=0)
            reply.update(prompt_tokens=0, completion_tokens=0)
            self.calls.append(telemetry)
            return reply
        self.base.case = self.case+'_'+self.arm
        reply = self.base(messages, json_schema=json_schema, provider=provider)
        reply['telemetry'].update(new_provider_call=True, comparison_execution='fresh_provider_response')
        self.cache[key] = (deepcopy(reply), {'arm': self.arm, 'case': self.case, 'schema': name,
                                          'request_key': reply['telemetry']['request_key']})
        self.calls.append(deepcopy(reply['telemetry']))
        return reply


def main(out):
    if out.exists():
        raise ValueError('Choose a new directory; no automatic repeat or overwrite')
    assert VERSION == 'ra-research-rag-4.8.3'
    specs = json.loads(CASES.read_text(encoding='utf-8'))['cases']+PROBES
    plans = {}
    for path in PLAN_FOLDER.glob('*.response.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema') == 'query_understanding' and data.get('status') == 'completed':
            plans[data['case']] = (data, path)
    assert all(c['id'] in plans for c in specs if c['id'] not in ('T01', 'T02'))
    paths = [*sorted((ROOT/'rag').glob('*.py')), *sorted((ROOT/'rag_service').glob('*.py')),
             *sorted((ROOT/'rag_service/static').glob('*')), Path(__file__), CASES,
             ROOT/'docs/rag/GUIDELINE_STATUS_2026-10-12.json', ROOT/'scripts/start_ra_service.ps1']
    code = {str(p.relative_to(ROOT)): digest(p) for p in paths if p.is_file()}
    index_paths = [GUIDELINES/'manifest.json', GUIDELINES/'chunks.jsonl']
    for path in RELEASES.values():
        index_paths += [path/'release_manifest.json']
        index_paths += [path/'candidate'/name for name in ('chunks.jsonl', 'source_units.jsonl',
            'contexts.jsonl', 'chunk_contexts.jsonl', 'visual_context_units.jsonl', 'visual_context_links.jsonl',
            'emb/bge-m3/doc.npy', 'emb/bge-m3/meta.json', 'emb/bge-m3/chunk_ids.json')]
    indices = {str(p.relative_to(ROOT)): digest(p) for p in index_paths if p.exists()}
    before_live = live_state()
    protocol = {'created_at': datetime.now(timezone.utc).isoformat(), 'pipeline_version': VERSION,
        'model': MODEL, 'reasoning_effort': 'none', 'maximum_new_provider_calls': MAX_CALLS,
        'automatic_retries': 0, 'cases': specs, 'case_count': len(specs),
        'arms': {arm: str(path) for arm, path in RELEASES.items()}, 'guidelines': str(GUIDELINES),
        'fixed_planning_responses': {case: {'path': str(path), 'sha256': digest(path)} for case, (_, path) in plans.items()},
        'reused_responses_require_exact_messages_and_schema_identity': True,
        'only_product_index_changes': True, 'gold_sent': False, 'independent_holdout': False,
        'external_grader': False, 'no_production_changes': True, 'code_sha256': code,
        'index_sha256': indices, 'live_before': before_live, 'source_inventory': inventory(),
        'interpretation': 'Known development questions and targeted table probes. Identical-input reuse is an equivalence check, not an independent new answer. Changed-input pairs are one sample per arm.',
        'decision_criteria': ['exact own quote and previously reviewed source boundaries',
            'repaired table values retain correct groups, statistic roles, units and header identities',
            'manual required-facet/source audit distinguishes missing facts from unsupported completion checking',
            'no corruption or product/document/jurisdiction leakage', 'existing supported answers do not regress'],
        'deployment': 'Evaluation only; deployment is not part of this run.'}
    write(out/'protocol.json', protocol)
    for name in code:
        target = out/'code_snapshot'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
    print(json.dumps({'stage': 'frozen', 'cases': len(specs), 'maximum_new_calls': MAX_CALLS,
                      'inventory': protocol['source_inventory']}, ensure_ascii=False), flush=True)
    base = EvaluationAPIChat(out/'api', max_new_calls=MAX_CALLS, model=MODEL)
    cache, rows, pipelines = {}, [], {}
    started = time.monotonic()
    try:
        # One identical local encoder for both arms; separate corpus/search caches.
        encoder = answer.Embedder('bge-m3', local_only=True)
        for arm, path in RELEASES.items():
            placeholder = PairedClient(base, arm, specs[0]['id'], plans, cache)
            pipelines[arm] = RAPipeline(path, guideline_snapshot=GUIDELINES, chat_client=placeholder,
                                       research_rounds=1, encoder_factory=lambda: encoder)
            write(out/arm/'summary_before.json', pipelines[arm].summary())
            print(json.dumps({'stage': 'warmup', 'arm': arm, **pipelines[arm].warmup()}), flush=True)
        for n, case in enumerate(specs):
            # Alternate arm order for changed-input pairs; repeated-input cache
            # has no model variance, and fresh-stage timing is stored separately.
            order = ['baseline', 'structure'] if n % 2 == 0 else ['structure', 'baseline']
            for arm in order:
                assert all(digest(ROOT/name) == value for name, value in code.items()), 'Frozen source changed'
                pipe = pipelines[arm]
                pipe.client = PairedClient(base, arm, case['id'], plans, cache)
                documents = None
                if case.get('document_id'):
                    documents = [d['key'] for d in pipe.documents if d['document_id'] == case['document_id']
                                 and d['version_id'].startswith(case['version_prefix'])]
                    assert len(documents) == 1
                before = base.attempts
                print(json.dumps({'stage': 'case', 'arm': arm, 'case': case['id']}), flush=True)
                try:
                    report = pipe.ask(case['question'], case['products'], case['jurisdictions'], documents)
                    assert len(report['generation_telemetry']) <= 3
                    assert all(e['document_id'] != 'GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015'
                               for e in report['results'][0]['evidence'].values())
                    execution = {'arm': arm, 'new_provider_calls': base.attempts-before,
                        'stages': [{'schema': t['schema'], 'execution': t['comparison_execution'],
                                    'request_key': t.get('request_key'),
                                    'payload_sha256': stable_hash(t['messages'])}
                                   for t in report['generation_telemetry']]}
                    report['paired_comparison'] = execution
                    write(out/arm/(case['id']+'.json'), report)
                    (out/arm/(case['id']+'.md')).write_text(report['review_memo']['markdown'], encoding='utf-8')
                    row = {'case': case['id'], 'arm': arm, **audit(report), **execution}
                except Exception as exc:
                    message = str(exc).replace(base.secret, '[REDACTED]') if base.secret else str(exc)
                    write(out/arm/(case['id']+'.failure.json'), {'error': message, 'diagnostics': pipe.last_failure})
                    row = {'case': case['id'], 'arm': arm, 'error': message, 'new_provider_calls': base.attempts-before}
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                if base.halted:
                    raise RuntimeError('Provider halted; no further requests')
        print(json.dumps({'stage': 'finished', 'pairs': len(specs), 'new_calls': base.attempts}), flush=True)
    finally:
        for pipe in pipelines.values():
            pipe.close()
        base.close()
        code_unchanged = all(digest(ROOT/name) == value for name, value in code.items())
        indices_unchanged = all(digest(ROOT/name) == value for name, value in indices.items())
        after_live = live_state()
        ledger = json.loads(base.ledger_path.read_text(encoding='utf-8'))
        calls = [c for c in ledger['attempts'] if c.get('evaluation_out') == str(base.out)]
        write(out/'summary.json', {'rows': rows, 'new_provider_calls': len(calls),
            'maximum_new_provider_calls': MAX_CALLS, 'input_tokens': sum(c.get('prompt_tokens', 0) for c in calls),
            'output_tokens': sum(c.get('completion_tokens', 0) for c in calls),
            'frozen_code_unchanged': code_unchanged, 'frozen_indices_unchanged': indices_unchanged,
            'live_after': after_live, 'live_service_unchanged': before_live == after_live,
            'all_pairs_present_without_process_error': len(rows) == 2*len(specs) and not any('error' in r for r in rows),
            'seconds': round(time.monotonic()-started, 3), 'independent_accuracy_evaluation': False,
            'human_expert_certified': False, 'promotion_decision': 'Pending direct source/required-facet review; not automatic.'})
        assert len(calls) <= MAX_CALLS and code_unchanged and indices_unchanged and before_live == after_live


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    main(args.out.resolve())
