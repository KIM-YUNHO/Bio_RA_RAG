"""How stable is retrieval when the planner words the same question differently?

For every item of the page-level retrieval gold set (eval/build_retrieval_gold.py), several
plans are made with the current planner (planner calls only; saved and reused). Each plan
builds the same kind of candidate pool (hybrid, --pool per task x product x agency group);
the local reranker scores it once with the slot-aware English per-fact queries
(fquery_unit), and three orders are derived from the same scores:

  hybrid        the hybrid order (no reranker)
  rrf           hybrid and reranker ranks fused (current service)
  rerank_only   the reranker order alone

For the top --k of every group the script reports, per item and order: recall per plan
(mean and minimum over plans), the share of gold groups that flip (found with some plans,
missed with others) and the mean pairwise Jaccard overlap of the selected chunk sets
(how much the LLM input agrees across plans). Weak gold groups are excluded.

  python eval/plan_stability.py --plans 4 --output data/runs/stability_2026-10-16/stability.json
"""
import argparse, itertools, json, statistics, sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GOLD = ROOT / 'data/runs/retrieval_gold_2026-10-16/gold.json'
FIRST_PLANS = ROOT / 'data/runs/retrieval_gold_2026-10-16/plans_atomic'
ORDERS = ('hybrid', 'rrf', 'rerank_only')


def bucket(item):
    if item['source'] != 'e2e29':
        return item['source']
    if item['question_type'] == 'timeline':
        return 'timeline'
    return {'Q': 'enumeration', 'G': 'guard'}.get(item['id'][0], 'detail')


def plan(item, n, pipe, folder, saved_only=False, fresh_first=False):
    """Plan n of an item: n=1 is the saved first plan (unless fresh_first), later ones are fresh planner calls."""
    stem = item['id'].replace(':', '_')
    if n == 1 and not fresh_first:
        return json.loads((FIRST_PLANS / f'{stem}.json').read_text(encoding='utf-8'))
    path = folder / f'{stem}_{n}.json'
    if not path.exists() and saved_only:
        return None
    if not path.exists():
        from rag import ra_tasks
        products = sorted({d['product_id'] for d in pipe.documents if d['product_id']})
        agencies = sorted({d['jurisdiction'] for d in pipe.documents})
        u = None
        for attempt in range(2):  # a degenerate planner reply (finish_reason=length) is retried once
            pipe.client.begin_request()
            try:
                u, _ = ra_tasks.interpret(item['question'], pipe.client, products, agencies, documents=pipe.documents)
                break
            except Exception as exc:
                print('planner failed', item['id'], n, attempt, type(exc).__name__, str(exc)[:120], flush=True)
            finally:
                pipe.client.release()
        if u is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(u, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    return json.loads(path.read_text(encoding='utf-8'))


def orders_for(corpus, u, guidelines, reranker, index, pool_size, k, anchor='both', question_bm25=False, query_unit=True):
    """{order name: set of selected chunk indices} for one plan.

    anchor: where the original question joins - 'dense' (a first-stage ranking per group),
    'rerank' (one of the reranker's queries), 'both' or 'none'."""
    from rag import ra_retrieval
    from eval.retrieval_gold import queries_for
    tasks = {t['id']: t for t in u['tasks']}
    question = u.get('source_question') or ''
    groups = ra_retrieval.retrieve(corpus, deepcopy(u), guidelines, documents=corpus.allowed_documents, k=pool_size,
                                   profile={'rerank': None, 'rerank_fuse': False,
                                            'question_query': True if anchor in ('dense', 'both') else None,
                                            'question_bm25': question_bm25, 'query_unit': query_unit})
    selected = {name: set() for name in ORDERS}
    for g in groups:
        pool = [index[h['chunk']['chunk_id']] for h in g['hits'] if h['chunk']['chunk_id'] in index]
        if not pool:
            continue
        selected['hybrid'].update(pool[:k])
        task = tasks.get(g['task_id'])
        if task is None or g.get('product') is None:  # guidance groups are not reranked
            selected['rrf'].update(pool[:k]); selected['rerank_only'].update(pool[:k])
            continue
        best = {}
        from rag.ra_retrieval import unit_prefix
        extra = [unit_prefix(g.get('product'), g.get('jurisdiction')) + question] if anchor in ('rerank', 'both') and question else []
        for q in queries_for(task, 'fquery_unit', g.get('product'), g.get('jurisdiction')) + extra:
            for score, i in reranker.rank(q, [(0, i) for i in pool], corpus.chunks, limit=len(pool)):
                best[i] = max(best.get(i, score), score)
        model_order = [i for i, _ in sorted(best.items(), key=lambda x: -x[1])]
        model_rank = {i: n for n, i in enumerate(model_order, 1)}
        hybrid_rank = {i: n for n, i in enumerate(pool, 1)}
        fused = sorted(pool, key=lambda i: -(1 / (60 + hybrid_rank[i]) + 1 / (60 + model_rank.get(i, len(pool) + 1))))
        selected['rrf'].update(fused[:k])
        selected['rerank_only'].update(model_order[:k])
    return selected


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--plans', type=int, default=4)
    p.add_argument('--pool', type=int, default=40)
    p.add_argument('--k', type=int, default=8)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--saved-only', action='store_true', help='no planner calls; items lacking a saved plan are skipped')
    p.add_argument('--plans-dir', type=Path, help='where plans are saved (default: next to --output)')
    p.add_argument('--fresh-first', action='store_true', help='plan 1 is also a fresh planner call')
    p.add_argument('--items-from', type=Path, help='only the items of an earlier stability result')
    p.add_argument('--no-anchor', action='store_true', help='same as --anchor none')
    p.add_argument('--anchor', choices=['both', 'dense', 'rerank', 'none'], default='both')
    p.add_argument('--question-bm25', action='store_true', help='the question ranking also uses BM25')
    p.add_argument('--no-query-unit', action='store_true', help='first-stage queries without product names')
    a = p.parse_args()
    items = json.loads(GOLD.read_text(encoding='utf-8'))['items']
    from rag import answer
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from rag.rerank import LocalReranker, MODEL, REVISION
    from preprocess.hybrid_config import load_config
    pipe = RAPipeline(ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release', None,
                      load_config(ROOT / 'configs/preprocess.local.json'),
                      guideline_snapshot=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2',
                      research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    folder = a.plans_dir or a.output.parent / 'plans'
    if a.items_from:
        keep = {r['id'] for r in json.loads(a.items_from.read_text(encoding='utf-8'))['rows']}
        items = [it for it in items if it['id'] in keep]
    rows = []
    try:
        plans = {it['id']: [u for u in (plan(it, n, pipe, folder, a.saved_only, a.fresh_first)
                                        for n in range(1, a.plans + 1)) if u]
                 for it in items}
        if a.saved_only:
            items = [it for it in items if len(plans[it['id']]) == a.plans]
            print('items with all saved plans:', len(items), flush=True)
        corpus = pipe.retrieval_corpus()
        corpus._emb = answer.Embedder('bge-m3', local_only=True)
        corpus.allowed_documents = {(d['document_id'], d['version_id']) for d in pipe.documents}
        reranker = LocalReranker(model=MODEL, revision=REVISION, batch_size=8, max_length=1024)
        index = {c['chunk_id']: i for i, c in enumerate(corpus.chunks)}
        page = lambda i: (corpus.chunks[i]['document_id'], corpus.chunks[i]['pdf_page'])
        for it in items:
            gold = [{tuple(x) for x in g['pages']} for g in it['groups'] if not g['weak']]
            if not gold:
                continue
            per_plan = [orders_for(corpus, u, pipe.guidelines, reranker, index, a.pool, a.k,
                                   anchor='none' if a.no_anchor else a.anchor, question_bm25=a.question_bm25,
                                   query_unit=not a.no_query_unit)
                        for u in plans[it['id']]]
            row = {'id': it['id'], 'bucket': bucket(it), 'groups': len(gold)}
            for name in ORDERS:
                hits = [[bool({page(i) for i in sel[name]} & g) for g in gold] for sel in per_plan]
                recalls = [sum(h) / len(gold) for h in hits]
                flips = sum(len({h[j] for h in hits}) > 1 for j in range(len(gold)))
                pairs = list(itertools.combinations([sel[name] for sel in per_plan], 2))
                jaccard = statistics.mean(len(x & y) / len(x | y) if x | y else 1.0 for x, y in pairs) if pairs else 1.0
                row[name] = {'recalls': [round(r, 3) for r in recalls], 'flips': flips, 'jaccard': round(jaccard, 3)}
            rows.append(row)
            print(it['id'], ' '.join(f"{n}:{row[n]['recalls']} flip{row[n]['flips']} J{row[n]['jaccard']}" for n in ORDERS), flush=True)
    finally:
        pipe.close()
    summary = defaultdict(dict)
    for scope in ['all', 'fact_gold', 'qa_chemr', 'detail', 'guard', 'enumeration', 'timeline']:
        rs = [r for r in rows if scope == 'all' or r['bucket'] == scope]
        if not rs:
            continue
        for name in ORDERS:
            groups = sum(r['groups'] for r in rs)
            summary[scope][name] = {
                'items': len(rs), 'groups': groups,
                'recall_mean': round(sum(statistics.mean(r[name]['recalls']) * r['groups'] for r in rs) / groups, 3),
                'recall_min': round(sum(min(r[name]['recalls']) * r['groups'] for r in rs) / groups, 3),
                'flip_share': round(sum(r[name]['flips'] for r in rs) / groups, 3),
                'jaccard_mean': round(statistics.mean(r[name]['jaccard'] for r in rs), 3)}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'plans': a.plans, 'pool': a.pool, 'k': a.k, 'summary': summary, 'rows': rows},
                                   ensure_ascii=False, indent=1), encoding='utf-8')
    for scope, value in summary.items():
        for name, m in value.items():
            print(scope, name, json.dumps(m))


if __name__ == '__main__':
    main()
