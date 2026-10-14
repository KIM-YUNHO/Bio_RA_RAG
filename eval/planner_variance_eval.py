"""Planner run-to-run variance with fresh provider calls (planning call only).

For each question the planner runs --repeats times with an identical input under
one sampling setting. Each run gets its own request folder, so no response is
replayed from cache. The script records planner output spread (queries, roles,
intent) and, without further provider calls, the retrieval hit overlap of those
plans with fixed topic queries off and on.

Cost: questions x repeats planning calls (about 1.3k input + 0.3k output tokens
each). --max-calls is a hard stop.
"""
from copy import deepcopy
import argparse, hashlib, itertools, json, statistics, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SAMPLING = {'default': None, 'zero': {'temperature': 0, 'seed': 0}}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, default=ROOT / 'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2')
    parser.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    parser.add_argument('--question', action='append', required=True)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--sampling', choices=sorted(SAMPLING), action='append', required=True)
    parser.add_argument('--max-calls', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    planned = len(args.question) * args.repeats * len(args.sampling)
    if planned > args.max_calls:
        parser.error(f'{planned} planning calls exceed --max-calls {args.max_calls}')
    if args.output.exists():
        parser.error('Choose a new output file')

    from rag import ra_retrieval, ra_tasks
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    pipe = RAPipeline(args.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=args.guidelines, research_rounds=0,
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna'))
    pipe.warmup()
    corpus = pipe.retrieval_corpus()
    docs = {(d['document_id'], d['version_id']) for d in pipe.documents}
    products = sorted({d['product_id'] for d in pipe.documents if d['product_id'] is not None})
    agencies = sorted({d['jurisdiction'] for d in pipe.documents})
    out_root = args.output.parent / (args.output.stem + '_requests')
    results, calls = [], 0
    try:
        for setting in args.sampling:
            for question in args.question:
                runs = []
                for n in range(args.repeats):
                    client = OpenAIPipelineChat(out_root=out_root, model='gpt-5.6-luna', context_tokens=32768,
                                                sampling=SAMPLING[setting])
                    client.begin_request()
                    try:
                        calls += 1
                        understanding, reply = ra_tasks.interpret(question, client, products, agencies,
                                                                  documents=pipe.documents)
                    except Exception as exc:
                        runs.append({'error': f'{type(exc).__name__}: {exc}'})
                        continue
                    finally:
                        client.release()
                    corpus.allowed_documents = docs
                    hits = {}
                    for topic in (False, True):
                        groups = ra_retrieval.retrieve(corpus, deepcopy(understanding), pipe.guidelines, documents=docs,
                                                       include_nondefault=False, k=5, topic_queries=topic)
                        hits[topic] = sorted(h['chunk']['chunk_id'] for g in groups for h in g['hits'])
                    runs.append({'intent': understanding['intent'], 'intent_check': understanding.get('intent_check'),
                                 'tasks': [{k: t.get(k) for k in ('request', 'queries', 'source_roles')} for t in understanding['tasks']],
                                 'hits_planner_only': hits[False], 'hits_with_topics': hits[True]})
                ok = [r for r in runs if 'error' not in r]

                def overlap(key):
                    pairs = [len(set(a[key]) & set(b[key])) / len(set(a[key]) | set(b[key]))
                             for a, b in itertools.combinations(ok, 2) if set(a[key]) | set(b[key])]
                    return round(statistics.mean(pairs), 3) if pairs else None
                summary = {'sampling': setting, 'question': question, 'runs': len(runs), 'errors': len(runs) - len(ok),
                           'distinct_queries': len({digest([t['queries'] for t in r['tasks']]) for r in ok}),
                           'distinct_roles': len({digest([t['source_roles'] for t in r['tasks']]) for r in ok}),
                           'distinct_task_counts': len({len(r['tasks']) for r in ok}),
                           'intents': sorted({r['intent'] for r in ok}),
                           'hit_jaccard_planner_only': overlap('hits_planner_only'),
                           'hit_jaccard_with_topics': overlap('hits_with_topics')}
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                results.append({'summary': summary, 'runs': runs})
    finally:
        pipe.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump({'created_at': datetime.now().isoformat(), 'planning_calls': calls,
                   'planner_version': ra_tasks.VERSION, 'results': results}, stream, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
