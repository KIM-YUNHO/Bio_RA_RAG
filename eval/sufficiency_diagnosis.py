"""Can a reranker score tell answerable questions from questions the sources cannot answer?

No provider calls: planning is replayed from earlier runs (--replay-plans for answerable
questions, --replay-absent for not_found questions, --replay-condition for the condition set),
retrieval serves the given profile, and the cross-encoder scores the served
hits against three texts of the plan: the standalone Korean question, the planner's English
query, and each task query. Scores are sigmoid probabilities (0-1).

Groups of questions
  answerable_found     answerable and at least one gold unit is among the served hits
  answerable_missed    answerable but no gold unit is served (would need another search)
  absent               the requested data is not in the sources (queries absent rows,
                       unanswerable qa_gold, condition cases of kind 'absent')
Questions whose plan left nothing to search (scope conflict, out of scope) are listed apart.
The summary gives each group's score distribution and the ROC AUC of each score for
answerable_found vs absent, plus the best single threshold on the development questions.
"""
from copy import deepcopy
import argparse, json, math, statistics, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    parser.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    parser.add_argument('--qa-gold', type=Path, required=True)
    parser.add_argument('--replay-plans', type=Path, required=True)
    parser.add_argument('--replay-absent', type=Path, required=True)
    parser.add_argument('--replay-condition', type=Path, required=True)
    parser.add_argument('--k', type=int, default=12)
    parser.add_argument('--profile', default='{"rerank": true, "rerank_keep_first_slots": true}')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile)

    from eval.post_retrieval_diagnosis import gold17, queries, qagold, condition, chunk_pages, NoProvider
    from rag import ra_retrieval, ra_tasks
    from rag.rerank import LocalReranker
    from rag.openai_chat import EvaluationAPIChat, OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    from sklearn.metrics import roc_auc_score

    OpenAIPipelineChat.__call__ = lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError('no provider calls'))
    pipe = RAPipeline(args.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=args.guidelines, research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna'))
    pipe.warmup()
    corpus = pipe.retrieval_corpus()
    reranker = LocalReranker()
    v2d = {d['version_id']: d['document_id'] for d in pipe.documents}
    items = list(gold17()) + list(queries(True)) + list(qagold(args.qa_gold, v2d)) + list(condition())
    dev_condition = {c['id'] for c in condition() if c['split'] == 'dev'}

    def plan(item):
        folder = (args.replay_condition if item['set'] == 'condition'
                  else args.replay_absent if item['expected'] == 'not_found' else args.replay_plans)
        for folder in [folder]:
            planner = EvaluationAPIChat(folder / f'planner_{item["set"]}', max_new_calls=1, model='gpt-5.6-luna', client=NoProvider())
            planner.case = item['id']
            selected = [d for d in pipe.documents
                        if (not item['products'] or d['product_id'] in item['products'] or d.get('role') == 'guideline')
                        and (not item['jurisdictions'] or d['jurisdiction'] in item['jurisdictions'])]
            try:
                u, _ = ra_tasks.interpret(item['question'], planner,
                    sorted({d['product_id'] for d in selected if d['product_id'] is not None}),
                    sorted({d['jurisdiction'] for d in selected}),
                    selected_products=item['products'], selected_agencies=item['jurisdictions'],
                    document_scope=False, documents=pipe.documents)
                return u, selected
            except AttributeError:  # not in this folder's cache
                continue
        return None, None

    sig = lambda x: 1 / (1 + math.exp(-x))
    rows = []
    try:
        for item in items:
            u, selected = plan(item)
            row = {'set': item['set'], 'id': item['id'], 'expected': item['expected']}
            if u is None:
                row['group'] = 'no_cached_plan'; rows.append(row); continue
            if u['request_scope']['status'] in ('conflict', 'empty') or u['intent'] == 'out_of_scope':
                row['group'] = 'not_searched'; rows.append(row); continue
            docs = {(d['document_id'], d['version_id']) for d in selected}
            corpus.allowed_documents = docs
            groups = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs, k=args.k, profile=profile)
            hits = [h['chunk'] for g in groups for h in g['hits']]
            passages = [c.get('header', '') + '\n' + c['text'] for c in hits]
            if not passages:
                row['group'] = 'no_hits'; rows.append(row); continue
            texts = {'question_ko': u['source_question'], 'english_query': u.get('english_query') or u['source_question']}
            scores = {name: max(sig(s) for s in reranker.score(text, passages)) for name, text in texts.items()}
            scores['task_query'] = max(sig(s) for t in u['tasks'] for q in t['queries'] for s in reranker.score(q, passages))
            pages = set().union(*(chunk_pages(c) for c in hits))
            found = any({tuple(p) for p in unit['pages']} & pages for unit in item['units'])
            row.update(scores, hits=len(hits),
                       group='absent' if item['expected'] == 'not_found' else 'answerable_found' if found or not item['units'] else 'answerable_missed',
                       dev=item['set'] != 'condition' or item['id'] in dev_condition)
            rows.append(row)
            print(item['id'], row['group'], {k: round(v, 3) for k, v in scores.items()}, flush=True)
    finally:
        corpus.allowed_documents = None
        reranker.close()
        pipe.close()

    summary = {}
    names = ['question_ko', 'english_query', 'task_query']
    for g in ['answerable_found', 'answerable_missed', 'absent']:
        rs = [r for r in rows if r.get('group') == g]
        summary[g] = {'n': len(rs), **{n: {'min': round(min(r[n] for r in rs), 3), 'median': round(statistics.median(r[n] for r in rs), 3),
                                           'max': round(max(r[n] for r in rs), 3)} for n in names if rs}}
    pos = [r for r in rows if r.get('group') == 'answerable_found' and r['dev']]
    neg = [r for r in rows if r.get('group') == 'absent' and r['dev']]
    summary['separation_dev'] = {}
    for n in names:
        y = [1] * len(pos) + [0] * len(neg)
        s = [r[n] for r in pos] + [r[n] for r in neg]
        best = max(((sum(v >= t for v in s[:len(pos)]) + sum(v < t for v in s[len(pos):])) / len(s), t)
                   for t in sorted(set(s)))
        summary['separation_dev'][n] = {'auc': round(roc_auc_score(y, s), 3), 'best_accuracy': round(best[0], 3),
                                        'threshold': round(best[1], 3)}
    summary['other_groups'] = {g: [r['id'] for r in rows if r.get('group') == g] for g in ('not_searched', 'no_hits', 'no_cached_plan')}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'profile': profile, 'k': args.k, 'summary': summary, 'rows': rows},
                                   ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
