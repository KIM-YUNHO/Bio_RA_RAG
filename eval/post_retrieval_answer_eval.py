"""Answer-level check of a retrieval profile on diagnosis question sets (default: the 17 H v2 cases +2 controls).

Runs the production RA ask() path end to end with two substitutions:
  planning   replayed from a post_retrieval_diagnosis run (no new planning call)
  retrieval  served with --k and --profile instead of the service default
Answer and review calls go to the provider and are cached under --out, so a rerun
reuses them. Reports are saved per case for manual grading; the summary adds
automatic signals only (status, cited gold pages, cited documents outside the
gold set) and the provider token cost at --price-in/--price-out USD per 1M.
"""
from collections import Counter
import argparse, functools, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    parser.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    parser.add_argument('--replay-plans', type=Path, required=True, help='post_retrieval_diagnosis --out folder')
    parser.add_argument('--k', type=int, default=8)
    parser.add_argument('--profile', default='{}')
    parser.add_argument('--context-tokens', type=int, default=65536)
    parser.add_argument('--only', action='append')
    parser.add_argument('--set', action='append', choices=['gold17', 'queries', 'qagold', 'condition', 'nondefault'])
    parser.add_argument('--qa-gold', type=Path, help='qa_gold.zip (read only, never copied)')
    parser.add_argument('--include-absent', action='store_true')
    parser.add_argument('--price-in', type=float, default=0.20)
    parser.add_argument('--price-out', type=float, default=1.20)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile)

    from eval.post_retrieval_diagnosis import gold17, queries, qagold, condition, nondefault, NoProvider
    from rag import ra_retrieval, ra_tasks
    from rag.openai_chat import EvaluationAPIChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config

    replays = {name: EvaluationAPIChat(args.replay_plans / f'planner_{name}', max_new_calls=1, model='gpt-5.6-luna',
                                       client=NoProvider()) for name in ('gold17', 'queries', 'qagold', 'condition', 'nondefault')}
    current = {}
    interpret = ra_tasks.interpret
    ra_tasks.interpret = lambda question, client, *a, **kw: interpret(question, current['replay'], *a, **kw)
    retrieve = ra_retrieval.retrieve
    ra_retrieval.retrieve = functools.wraps(retrieve)(lambda *a, **kw: retrieve(*a, **{**kw, 'k': args.k, 'profile': profile}))

    client = EvaluationAPIChat(args.out / 'api', max_new_calls=60, model='gpt-5.6-luna')
    client.config.num_ctx = args.context_tokens
    pipe = RAPipeline(args.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=args.guidelines, research_rounds=1, chat_client=client)
    pipe.warmup()
    reports = args.out / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    rows = []
    version_to_document = {d['version_id']: d['document_id'] for d in pipe.documents}
    items = []
    for name in args.set or ['gold17']:
        items += (list(qagold(args.qa_gold, version_to_document)) if name == 'qagold'
                  else list(queries(args.include_absent)) if name == 'queries'
                  else list(condition()) if name == 'condition' else list(nondefault()) if name == 'nondefault' else list(gold17()))
    try:
        for item in items:
            if args.only and item['id'] not in args.only:
                continue
            current['replay'] = replays[item['set']]
            client.case = current['replay'].case = item['id']
            start = len(client.calls)
            try:
                report = pipe.ask(item['question'], products=item['products'], jurisdictions=item['jurisdictions'])
            except Exception as exc:
                rows.append({'id': item['id'], 'error': f'{type(exc).__name__}: {exc}'})
                print(item['id'], 'error', exc, flush=True)
                continue
            (reports / f'{item["id"]}.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
            result = report['results'][0]
            evidence = result.get('evidence') or {}
            gold = {tuple(p[:2]) for u in item['units'] for p in u['pages']}
            cited = {(evidence[s['evidence']]['document_id'], evidence[s['evidence']]['pdf_page'])
                     for c in result.get('claims', []) for s in c.get('sources', []) if s.get('evidence') in evidence}
            gold_docs = {d for d, _ in gold}
            calls = report.get('generation_telemetry') or []
            tin, tout = sum(c.get('prompt_tokens', 0) for c in calls), sum(c.get('completion_tokens', 0) for c in calls)
            rows.append({'set': item['set'], 'id': item['id'], 'expected': item['expected'], 'status': result['status'],
                         'claims': len(result.get('claims', [])), 'gaps': len(result.get('gaps', [])),
                         'gold_pages_cited': len(gold & cited), 'gold_pages': len(gold),
                         'units_cited': sum(bool({tuple(p[:2]) for p in u['pages']} & cited) for u in item['units']),
                         'units': len(item['units']),
                         'cited_outside_gold_docs': sorted({d for d, _ in cited if d not in gold_docs}),
                         'evidence_blocks': len(evidence), 'calls': len(calls), 'input_tokens': tin, 'output_tokens': tout,
                         'usd': round(tin / 1e6 * args.price_in + tout / 1e6 * args.price_out, 5)})
            print(item['id'], result['status'], f"claims={rows[-1]['claims']} gold={rows[-1]['gold_pages_cited']}/{len(gold)}", flush=True)
    finally:
        pipe.close()
    ok = [r for r in rows if 'error' not in r]
    summary = {'k': args.k, 'profile': profile, 'cases': len(rows), 'errors': len(rows) - len(ok),
               'status': dict(Counter(r['status'] for r in ok)),
               'gold_pages_cited': sum(r['gold_pages_cited'] for r in ok), 'gold_pages': sum(r['gold_pages'] for r in ok),
               'units_cited': sum(r['units_cited'] for r in ok), 'units': sum(r['units'] for r in ok),
               'calls': sum(r['calls'] for r in ok), 'input_tokens': sum(r['input_tokens'] for r in ok),
               'output_tokens': sum(r['output_tokens'] for r in ok), 'usd': round(sum(r['usd'] for r in ok), 4)}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    (args.out / 'answer_eval.json').write_text(json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1),
                                              encoding='utf-8')


if __name__ == '__main__':
    main()
