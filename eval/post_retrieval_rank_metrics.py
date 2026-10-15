"""Ranking metrics of served hits: Hit@k, Recall@k, MRR, NDCG@k. Planning replayed, no provider calls.

For each profile the served hits are retrieved with k = max(--cutoff); groups (task x product x
agency, plus guideline groups) are read round-robin by rank, the order the context step reads
them, and duplicate chunks are dropped. A gold unit (a page or a set of acceptable pages) is
found at the first chunk whose pages intersect it. Cutoff k counts per-group ranks, so @8 is
the production k=8 selection and @12 the k=12 one.

  Hit@k     share of questions with at least one gold unit at group rank <= k
  Recall@k  share of gold units at group rank <= k (pooled over questions)
  MRR       mean over questions of 1 / (merged position of the first gold unit), 0 if none
  NDCG@k    graded gain (queries: manual grade 2/1 pages; other sets: 1) over the merged list
            truncated at group rank k; the ideal ranks every gold unit first, so units never
            retrieved lower the score (scikit-learn's ndcg_score only ranks retrieved items)
"""
from copy import deepcopy
import argparse, csv, json, math, statistics, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def grades():
    """Manual relevance grade per (query_id, document_id, page) from eval/labels_manual_v0.1.csv."""
    out = {}
    with (ROOT / 'eval/labels_manual_v0.1.csv').open(encoding='utf-8-sig') as stream:
        for r in csv.DictReader(stream):
            key = (r['query_id'], r['document_id'], int(r['pdf_page']))
            out[key] = max(out.get(key, 0), int(r['grade']))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    parser.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    parser.add_argument('--qa-gold', type=Path, required=True)
    parser.add_argument('--replay-plans', type=Path, required=True)
    parser.add_argument('--profile', action='append', required=True, help='name=JSON, e.g. rerank={"rerank": true}')
    parser.add_argument('--cutoff', type=int, action='append', default=None)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    cutoffs = sorted(args.cutoff or [5, 8, 12])
    profiles = {p.split('=', 1)[0]: json.loads(p.split('=', 1)[1]) for p in args.profile}

    from eval.post_retrieval_diagnosis import gold17, queries, qagold, chunk_pages, NoProvider
    from rag import ra_retrieval, ra_tasks
    from rag.openai_chat import EvaluationAPIChat, OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config

    OpenAIPipelineChat.__call__ = lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError('no provider calls'))
    pipe = RAPipeline(args.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=args.guidelines, research_rounds=0,
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna'))
    pipe.warmup()
    corpus = pipe.retrieval_corpus()
    v2d = {d['version_id']: d['document_id'] for d in pipe.documents}
    items = list(gold17()) + list(queries()) + list(qagold(args.qa_gold, v2d))
    items = [i for i in items if i['expected'] == 'answer' and i['units']]
    manual = grades()
    replay = {s: EvaluationAPIChat(args.replay_plans / f'planner_{s}', max_new_calls=1, model='gpt-5.6-luna', client=NoProvider())
              for s in ('gold17', 'queries', 'qagold')}
    rows = []
    try:
        for item in items:
            planner = replay[item['set']]
            planner.case = item['id']
            selected = [d for d in pipe.documents
                        if (not item['products'] or d['product_id'] in item['products'] or d.get('role') == 'guideline')
                        and (not item['jurisdictions'] or d['jurisdiction'] in item['jurisdictions'])]
            u, _ = ra_tasks.interpret(item['question'], planner,
                sorted({d['product_id'] for d in selected if d['product_id'] is not None}),
                sorted({d['jurisdiction'] for d in selected}),
                selected_products=item['products'], selected_agencies=item['jurisdictions'],
                document_scope=False, documents=pipe.documents)
            docs = {(d['document_id'], d['version_id']) for d in selected}
            corpus.allowed_documents = docs
            units = [{tuple(p) for p in u_['pages']} for u_ in item['units']]
            gains = [max((manual.get((item['id'], d, p), 0) for d, p in pages), default=0) or 2
                     if item['set'] == 'queries' else 1 for pages in units]
            for name, profile in profiles.items():
                groups = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs, k=max(cutoffs),
                                               profile=profile)
                merged, seen = [], set()
                for rank in range(max(cutoffs)):
                    for g in groups:
                        if rank < len(g['hits']) and g['hits'][rank]['chunk']['chunk_id'] not in seen:
                            seen.add(g['hits'][rank]['chunk']['chunk_id'])
                            merged.append((rank + 1, chunk_pages(g['hits'][rank]['chunk'])))
                found = []  # (merged position, group rank) of each unit's first match
                for pages in units:
                    found.append(next(((pos, r) for pos, (r, cp) in enumerate(merged, 1) if cp & pages), None))
                row = {'profile': name, 'set': item['set'], 'id': item['id'], 'units': len(units)}
                first = min((f[0] for f in found if f), default=None)
                row['rr'] = 1 / first if first else 0.0
                for k in cutoffs:
                    hit = [f is not None and f[1] <= k for f in found]
                    row[f'hit@{k}'] = int(any(hit))
                    row[f'found@{k}'] = sum(hit)
                    depth = sum(1 for r, _ in merged if r <= k)
                    dcg = sum(g / math.log2(f[0] + 1) for g, f, h in zip(gains, found, hit) if h and f[0] <= depth)
                    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(sorted(gains, reverse=True)))
                    row[f'ndcg@{k}'] = dcg / idcg if idcg else 0.0
                rows.append(row)
            print(item['id'], flush=True)
    finally:
        corpus.allowed_documents = None
        pipe.close()

    summary = {}
    for name in profiles:
        for scope in ['all', 'gold17', 'queries', 'qagold']:
            rs = [r for r in rows if r['profile'] == name and (scope == 'all' or r['set'] == scope)]
            s = {'questions': len(rs), 'units': sum(r['units'] for r in rs), 'MRR': round(statistics.mean(r['rr'] for r in rs), 3)}
            for k in cutoffs:
                s[f'Hit@{k}'] = round(statistics.mean(r[f'hit@{k}'] for r in rs), 3)
                s[f'Recall@{k}'] = round(sum(r[f'found@{k}'] for r in rs) / s['units'], 3)
                s[f'NDCG@{k}'] = round(statistics.mean(r[f'ndcg@{k}'] for r in rs), 3)
            summary.setdefault(name, {})[scope] = s
        print(name, json.dumps(summary[name]['all']), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'profiles': profiles, 'cutoffs': cutoffs, 'summary': summary, 'rows': rows},
                                   ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
