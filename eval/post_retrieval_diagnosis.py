"""Where do answer pages drop after first-stage retrieval? Planning calls only.

For each question the current code plans the question (one provider call, cached
under --out), then runs retrieval and context assembly without further calls.
Every gold unit (a page, or a set of acceptable pages) gets the furthest stage
it reached:

  scope          planner scope/intent left the gold document unsearched
  not_in_pool    not among the fused top-40 candidates of its group (k=40)
  dropped_select in the pool but not among the served hits (k=8)
  dropped_context among the hits but not in the assembled evidence
  supplied       in the evidence handed to synthesis (after bounded research)

The pool is always the production fusion (k=--pool); --profile changes only how the
served k hits are chosen from it, so stages compare across profiles.

Question sets
  gold17   eval/post_retrieval_gold_17.json (+ question text from the H v2 cases)
  queries  eval/queries_v0.1.csv retrieval rows (EMA EPAR, acceptable pages)
  qagold   qa_gold.zip dev/indomain/external splits (EMA EPAR evidence pages)
The 17 cases and both gold sets were seen during development: these are
development diagnostics, not holdout results. Answer generation is not run, so
"supplied" does not mean the answer used the page.
"""
from copy import deepcopy
from collections import Counter
import argparse, csv, json, statistics, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STAGES = ['scope', 'not_in_pool', 'dropped_select', 'dropped_context', 'supplied']
QA_PRODUCTS = {'Pyzchiva': 'SB17', 'Wezenla': 'ABP654', 'Steqeyma': 'CTP43', 'Usymro': 'BAT2206', 'Yesintek': 'BMAB1200'}
QA_SPLITS = {'dev': 'qa_gold_dev.jsonl', 'indomain_test': 'qa_gold_indomain_test.jsonl',
             'external_holdout': 'qa_gold_external_holdout.jsonl'}
H2V = ROOT / 'data/runs/h_v2_rag_development_2026-10-14_luna_v1/cases.json'


def gold17():
    data = json.loads((ROOT / 'eval/post_retrieval_gold_17.json').read_text(encoding='utf-8'))
    text = {c['id']: c['question'] for c in json.loads(H2V.read_text(encoding='utf-8'))}
    for c in data['cases']:
        yield {'set': 'gold17', 'id': c['id'], 'question': c.get('question') or text[c['id']], 'expected': c['expected'],
               'products': c['products'], 'jurisdictions': c['jurisdictions'],
               'units': [{'unit': f'{d}:p{p}', 'pages': [[d, p]], 'basis': b} for d, p, b in c['pages']]}


def condition():
    """eval/condition_cases_v1.json: questions restricted to a route, indication or population."""
    data = json.loads((ROOT / 'eval/condition_cases_v1.json').read_text(encoding='utf-8'))
    for c in data['cases']:
        yield {'set': 'condition', 'split': c['split'], 'id': c['id'], 'question': c['question'],
               'expected': 'answer' if c['kind'] == 'present' else 'not_found',
               'products': c['products'], 'jurisdictions': c['jurisdictions'], 'units': []}


def queries(include_absent=False):
    with (ROOT / 'eval/queries_v0.1.csv').open(encoding='utf-8-sig') as stream:
        for r in csv.DictReader(stream):
            if r['type'] != 'retrieval' and not (include_absent and r['type'] == 'absent'):
                continue
            pages = json.loads(r['acceptable_pages'] or '[]') or json.loads(r['gold_pages'] or '[]')
            yield {'set': 'queries', 'id': r['query_id'], 'question': r['ko_a'],
                   'expected': 'answer' if r['type'] == 'retrieval' else 'not_found',
                   'products': [r['product_id']], 'jurisdictions': ['EU'],
                   'units': [{'unit': r['query_id'], 'pages': [[r['document_id'], p] for p in pages], 'basis': 'acceptable_pages'}]}


def qagold(path, version_to_document):
    z = zipfile.ZipFile(path)
    rows = lambda name: [json.loads(x) for x in z.read(name).decode('utf-8-sig').splitlines() if x.strip()]
    evidence = {e['evidence_id']: e for e in rows('evidence_records.jsonl')}
    for split, name in QA_SPLITS.items():
        for q in rows(name):
            units = {}
            for fact in q.get('required_facts') or []:
                for eid in fact.get('evidence_ids') or []:
                    e = evidence[eid]
                    units[eid] = {'unit': eid, 'pages': [[version_to_document[e['document_sha256']], e['pdf_page']]],
                                  'basis': 'qa_gold_evidence'}
            yield {'set': 'qagold', 'split': split, 'id': q['question_id'], 'question': q['question'],
                   'expected': 'answer' if q['answerable'] else 'not_found',
                   'products': sorted({QA_PRODUCTS[p] for p in q['product_scope']}), 'jurisdictions': ['EU'],
                   'units': list(units.values()) if q['answerable'] else []}


class NoProvider:
    """Replay guard: a planner call missing from the cache fails instead of reaching the provider."""
    def __getattr__(self, name):
        raise AttributeError('planner call not in the replay cache')


def chunk_pages(chunk):
    pages = {(chunk['document_id'], chunk['pdf_page'])}
    for ref in (chunk.get('provenance') or {}).get('source_refs') or chunk.get('source_refs') or []:
        if ref.get('pdf_page') is not None:
            pages.add((chunk['document_id'], ref['pdf_page']))
    return pages


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    parser.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    parser.add_argument('--qa-gold', type=Path, help='qa_gold.zip (read only, never copied)')
    parser.add_argument('--set', action='append', choices=['gold17', 'queries', 'qagold', 'condition'], required=True)
    parser.add_argument('--only', action='append', help='Question IDs to run (smoke test)')
    parser.add_argument('--include-absent', action='store_true', help='also load absent (not_found) rows of the queries set')
    parser.add_argument('--pool', type=int, default=40)
    parser.add_argument('--k', type=int, default=8)
    parser.add_argument('--context-tokens', type=int, default=65536)
    parser.add_argument('--out', type=Path, required=True, help='Run folder; planner calls are cached here')
    parser.add_argument('--replay-plans', type=Path, help='Earlier --out folder: reuse its cached planner calls, never call the provider')
    parser.add_argument('--profile', default='{}', help='JSON retrieval profile for the served hits, e.g. {"rerank": true}')
    args = parser.parse_args()
    profile = json.loads(args.profile)

    from rag import ra_retrieval, ra_tasks
    from rag.ra_research import extend
    from rag.openai_chat import EvaluationAPIChat, OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config

    def blocked(self, *a, **k):
        raise RuntimeError('only planning calls are allowed in this diagnosis')
    OpenAIPipelineChat.__call__ = blocked
    budget_client = OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=args.context_tokens)
    pipe = RAPipeline(args.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=args.guidelines, research_rounds=1, retrieval_pages=min(args.k, 10), chat_client=budget_client)
    pipe.warmup()
    corpus = pipe.retrieval_corpus()
    by_id = {c['chunk_id']: c for c in corpus.chunks}
    by_id.update({c['chunk_id']: c for c in (pipe.guidelines.chunks if pipe.guidelines else [])})
    version_to_document = {d['version_id']: d['document_id'] for d in pipe.documents}
    known = {d['document_id'] for d in pipe.documents} | {c['document_id'] for c in (pipe.guidelines.chunks if pipe.guidelines else [])}

    items = []
    for name in args.set:
        if name == 'qagold' and not args.qa_gold:
            parser.error('--qa-gold is required for the qagold set')
        items += (list(queries(args.include_absent)) if name == 'queries' else list(gold17()) if name == 'gold17'
                  else list(condition()) if name == 'condition'
                  else list(qagold(args.qa_gold, version_to_document)))
    if args.only:
        items = [i for i in items if i['id'] in args.only]
    missing_docs = sorted({p[0] for i in items for u in i['units'] for p in u['pages'] if p[0] not in known})
    if missing_docs:
        parser.error(f'gold documents not in this index: {missing_docs}')

    args.out.mkdir(parents=True, exist_ok=True)
    planners = {}
    rows = []
    try:
        for item in items:
            planner = planners.get(item['set'])
            if planner is None:
                folder = (args.replay_plans or args.out) / f'planner_{item["set"]}'
                planner = planners[item['set']] = (EvaluationAPIChat(folder, max_new_calls=1, model='gpt-5.6-luna', client=NoProvider())
                    if args.replay_plans else EvaluationAPIChat(folder, max_new_calls=60, model='gpt-5.6-luna'))
            planner.case = item['id']
            selected = [d for d in pipe.documents
                        if (not item['products'] or d['product_id'] in item['products'] or d.get('role') == 'guideline')
                        and (not item['jurisdictions'] or d['jurisdiction'] in item['jurisdictions'])]
            row = {k: item.get(k) for k in ('set', 'split', 'id', 'question', 'expected', 'products', 'jurisdictions')}
            try:
                u, _ = ra_tasks.interpret(item['question'], planner,
                    sorted({d['product_id'] for d in selected if d['product_id'] is not None}),
                    sorted({d['jurisdiction'] for d in selected}),
                    selected_products=item['products'], selected_agencies=item['jurisdictions'],
                    document_scope=False, documents=pipe.documents)
            except Exception as exc:
                row.update(error=f'planner: {type(exc).__name__}: {exc}', units=[])
                rows.append(row)
                print(item['id'], 'planner error', exc, flush=True)
                continue
            row.update(intent=u.get('intent'), scope_status=u['request_scope']['status'],
                       tasks=[{'request': t['request'], 'queries': t['queries'], 'roles': t['source_roles']} for t in u['tasks']],
                       out_of_scope_tasks=len(u.get('out_of_scope_tasks') or []))
            searched = u['request_scope']['status'] not in ('conflict', 'empty') and u['intent'] != 'out_of_scope'
            pool_pages, hit_pages, ev_pages, research_pages, ranks = set(), set(), set(), set(), {}
            if searched:
                docs = {(d['document_id'], d['version_id']) for d in selected}
                corpus.allowed_documents = docs
                pool = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs, k=args.pool, profile={'rerank': False})
                served = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs, k=args.k, profile=profile)
                question = u['source_question']
                evidence, budget = ra_retrieval.context(corpus, served, question, deepcopy(u), budget_client, 'spans',
                                                        guideline_index=pipe.guidelines)
                before = set(evidence)
                evidence, budget, research = extend(corpus, served, evidence, budget, question, deepcopy(u), budget_client,
                                                    pipe.guidelines, max_rounds=1, citation_mode='spans')
                for g in pool:
                    for rank, h in enumerate(g['hits'], 1):
                        for page in chunk_pages(h['chunk']):
                            pool_pages.add(page)
                            ranks[page] = min(ranks.get(page, 999), rank)
                hit_pages = {p for g in served for h in g['hits'] for p in chunk_pages(h['chunk'])}
                for key, block in evidence.items():
                    pages = chunk_pages(by_id[block['chunk_id']]) if block.get('chunk_id') in by_id else {(block['document_id'], block['pdf_page'])}
                    ev_pages |= pages
                    if key not in before:
                        research_pages |= pages
                row.update(groups=len(served), evidence_blocks=len(evidence), omitted=len(budget.get('omitted', [])),
                           prompt_tokens=budget.get('estimated_prompt_and_schema_tokens'),
                           research={'stop': research.get('stop_reason'), 'added': len(research.get('added_packets', []))})
            units = []
            for unit in item['units']:
                pages = {tuple(p) for p in unit['pages']}
                stage = ('supplied' if pages & ev_pages else 'dropped_context' if pages & hit_pages
                         else 'dropped_select' if pages & pool_pages else 'not_in_pool' if searched else 'scope')
                best = min((ranks[p] for p in pages if p in ranks), default=None)
                units.append({**unit, 'stage': stage, 'pool_rank': best, 'via_research': bool(pages & research_pages)})
            row['units'] = units
            rows.append(row)
            print(item['id'], Counter(x['stage'] for x in units), flush=True)
    finally:
        corpus.allowed_documents = None
        pipe.close()

    summary = {}
    for name in dict.fromkeys(r['set'] for r in rows):
        rs = [r for r in rows if r['set'] == name and r.get('expected') == 'answer']
        units = [x for r in rs for x in r['units']]
        stages = Counter(x['stage'] for x in units)
        summary[name] = {'questions': len(rs), 'units': len(units), 'stages': {s: stages.get(s, 0) for s in STAGES},
            'questions_any_supplied': sum(any(x['stage'] == 'supplied' for x in r['units']) for r in rs),
            'questions_all_supplied': sum(bool(r['units']) and all(x['stage'] == 'supplied' for x in r['units']) for r in rs),
            'pool_rank_of_dropped_select': sorted(x['pool_rank'] for x in units if x['stage'] == 'dropped_select'),
            'via_research': sum(x['via_research'] for x in units),
            'errors': sum('error' in r for r in rows if r['set'] == name),
            'mean_prompt_tokens': round(statistics.mean(r['prompt_tokens'] for r in rs if r.get('prompt_tokens'))) if any(r.get('prompt_tokens') for r in rs) else None}
        print(name, json.dumps(summary[name], ensure_ascii=False), flush=True)
    head = __import__('subprocess').run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    (args.out / 'diagnosis.json').write_text(json.dumps({'code_commit': head, 'run': str(args.run), 'pool': args.pool, 'k': args.k,
        'profile': profile, 'replay_plans': str(args.replay_plans) if args.replay_plans else None,
        'context_tokens': args.context_tokens, 'chunks_sha256': pipe.index_meta['chunks_sha256'],
        'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
