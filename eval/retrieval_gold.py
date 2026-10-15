"""Retrieval / reranker evaluation on the page-level gold set (eval/build_retrieval_gold.py).

Plans are frozen per item (e2e29: the e2e_final plan; fact_gold: its frozen plan; qa_chemr:
one planner call, saved). For every item the hybrid retriever builds a candidate pool of
--pool hits per task x product x agency group (current default profile, no reranker).
Each variant then orders every group's pool:

  none                    the hybrid order (production)
  <model>:<mode>          a local cross-encoder; mode = request | queries | both | ask, or
                          request_unit | ask_unit (prefixed with the group's product names and agency)

and the top k of every group are the selected pages. Metrics per gold group (a group is
satisfied by ANY of its pages): recall@k for k in --ks, MRR (best rank of a gold page over
the item's groups; 0 if absent from every pool) and the pool ceiling. Weak groups (more
than 20 gold pages) are reported separately. Re-running with another reranker reuses the
saved plans and pools: no model API calls after the first run.

  python eval/retrieval_gold.py --variants none,bge-reranker-v2-m3:ask --output data/runs/retrieval_gold_2026-10-16/results.json
"""
import argparse, json, os, re, statistics, sys, time
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GOLD = ROOT / 'data/runs/retrieval_gold_2026-10-16/gold.json'
FACT_PLANS = ROOT / 'data/runs/retrieval_scope_2026-10-15/nondefault_gold/plans'
MODELS = {'bge-reranker-v2-m3': ('BAAI/bge-reranker-v2-m3', '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e')}


def plan_for(item, pipe, folder):
    if item['source'] == 'e2e29':
        return json.loads(Path(item['plan_report']).read_text(encoding='utf-8'))['understanding']
    if item['source'] == 'fact_gold':
        return json.loads((FACT_PLANS / f"{item['id'].replace(':', '_')}.json").read_text(encoding='utf-8'))['understanding']
    path = folder / f"{item['id']}.json"
    if not path.exists():
        from rag import ra_tasks
        products = sorted({d['product_id'] for d in pipe.documents if d['product_id']})
        agencies = sorted({d['jurisdiction'] for d in pipe.documents})
        pipe.client.begin_request()
        try:
            u, _ = ra_tasks.interpret(item['question'], pipe.client, products, agencies, documents=pipe.documents)
        finally:
            pipe.client.release()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(u, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    return json.loads(path.read_text(encoding='utf-8'))


AGENCY_LABEL = {'EU': 'EMA', 'US': 'FDA', 'CA': 'Health Canada'}


def unit_prefix(product, agency):
    """The group's own product names and agency, so the reranker scores that unit's passages."""
    from rag import study_registry
    meta = study_registry.products().get(product, {}) if product else {}
    key = lambda n: re.sub(r'[^a-z0-9]', '', n.lower())
    brands = [n.split('(')[0].strip() for n in meta.get('names', []) if key(n.split('(')[0]) != key(product or '')]
    names = [product, *list(dict.fromkeys(brands))[:2]] if product else []
    return ' '.join(dict.fromkeys(n for n in [*names, AGENCY_LABEL.get(agency, '')] if n)) + ': '


def queries_for(task, mode, product=None, agency=None):
    if mode.endswith('_unit'):
        return [unit_prefix(product, agency) + q for q in queries_for(task, mode[:-5])]
    asks = list(dict.fromkeys(f['ask'] + (' ' + item if item else '')
                              for f in task.get('answer_facets', []) if f.get('ask')
                              for item in (f.get('items') or [None])))
    return {'request': [task['request']], 'queries': list(task['queries']),
            'both': [task['request'], *task['queries']], 'ask': asks or [task['request']]}[mode]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--variants', default='none,bge-reranker-v2-m3:request,bge-reranker-v2-m3:ask')
    p.add_argument('--pool', type=int, default=40)
    p.add_argument('--ks', default='4,8,16')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    ks = [int(x) for x in a.ks.split(',')]
    variants = a.variants.split(',')
    items = json.loads(GOLD.read_text(encoding='utf-8'))['items']
    from rag import ra_retrieval, answer
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    pipe = RAPipeline(ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release', None,
                      load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2',
                      research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    rerankers, rows = {}, []
    try:
        plans = {it['id']: plan_for(it, pipe, a.output.parent / 'plans') for it in items}
        pipe.warmup()
        corpus = pipe.retrieval_corpus()
        corpus._emb = pipe._shared_encoder or answer.Embedder('bge-m3', local_only=True)
        corpus.allowed_documents = {(d['document_id'], d['version_id']) for d in pipe.documents}
        index = {c['chunk_id']: i for i, c in enumerate(corpus.chunks)}
        page = lambda i: (corpus.chunks[i]['document_id'], corpus.chunks[i]['pdf_page'])
        for it in items:
            u = plans[it['id']]
            tasks = {t['id']: t for t in u['tasks']}
            groups = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=corpus.allowed_documents, k=a.pool)
            pools = [(g['task_id'], g.get('product'), g.get('jurisdiction'),
                      [index[h['chunk']['chunk_id']] for h in g['hits'] if h['chunk']['chunk_id'] in index])
                     for g in groups if g.get('product') is not None or g['hits']]
            gold = [({tuple(x) for x in g['pages']}, g['weak'], g['label']) for g in it['groups']]
            row = {'id': it['id'], 'source': it['source'], 'type': it['question_type'], 'variants': {}}
            for variant in variants:
                started = time.perf_counter()
                ordered = []
                for task_id, product, agency, pool in pools:
                    if variant == 'none' or not pool or task_id not in tasks:
                        ordered.append(pool); continue
                    name, mode = variant.split(':')
                    if name not in rerankers:
                        from rag.rerank import LocalReranker
                        model, revision = MODELS.get(name, (name, None))
                        rerankers[name] = LocalReranker(model=model, revision=revision, batch_size=8, max_length=1024)
                    best = {}
                    for q in queries_for(tasks[task_id], mode, product, agency):
                        for score, i in rerankers[name].rank(q, [(0, i) for i in pool], corpus.chunks, limit=len(pool)):
                            best[i] = max(best.get(i, score), score)
                    ordered.append([i for i, _ in sorted(best.items(), key=lambda x: -x[1])])
                seconds = time.perf_counter() - started
                result = {'seconds': round(seconds, 3), 'groups': []}
                for pages, weak, label in gold:
                    ranks = [n for order in ordered for n, i in enumerate(order, 1) if page(i) in pages]
                    best_rank = min(ranks) if ranks else None
                    result['groups'].append({'label': label, 'weak': weak, 'rank': best_rank,
                                             **{f'r@{k}': bool(best_rank and best_rank <= k) for k in ks}})
                row['variants'][variant] = result
            rows.append(row)
            print(it['id'], ' '.join(f"{v}:{sum(g['r@8'] for g in r['groups'] if not g['weak'])}/"
                                     f"{sum(1 for g in r['groups'] if not g['weak'])}({r['seconds']:.1f}s)"
                                     for v, r in row['variants'].items()), flush=True)
    finally:
        pipe.close()
    summary = defaultdict(dict)
    for scope in ['all', 'qa_chemr', 'fact_gold', 'e2e29', 'e2e29_weak']:
        for variant in variants:
            gs = [g for r in rows for g in r['variants'][variant]['groups']
                  if (scope.startswith(r['source']) or scope == 'all') and (g['weak'] == scope.endswith('_weak'))]
            if not gs:
                continue
            secs = [r['variants'][variant]['seconds'] for r in rows if scope == 'all' or scope.startswith(r['source'])]
            summary[scope][variant] = {'groups': len(gs), **{f'recall@{k}': round(sum(g[f'r@{k}'] for g in gs) / len(gs), 3) for k in ks},
                                       'mrr': round(sum(1 / g['rank'] for g in gs if g['rank']) / len(gs), 3),
                                       'pool_ceiling': round(sum(g['rank'] is not None for g in gs) / len(gs), 3),
                                       'seconds_median': round(statistics.median(secs), 2), 'seconds_max': round(max(secs), 2)}
    by_type = defaultdict(dict)
    for variant in variants:
        for t in sorted({r['type'] for r in rows if r['source'] == 'qa_chemr'}):
            gs = [g for r in rows if r['source'] == 'qa_chemr' and r['type'] == t for g in r['variants'][variant]['groups']]
            by_type[t][variant] = round(sum(g['r@8'] for g in gs) / len(gs), 3)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'pool': a.pool, 'summary': summary, 'qa_chemr_recall@8_by_type': by_type, 'rows': rows},
                                   ensure_ascii=False, indent=1), encoding='utf-8')
    for scope, value in summary.items():
        for variant, m in value.items():
            print(scope, variant, json.dumps(m))
    print('qa_chemr recall@8 by type', json.dumps(by_type, ensure_ascii=False))


if __name__ == '__main__':
    main()
