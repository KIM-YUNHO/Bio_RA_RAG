"""Task-level intent evaluation (planning call only).

Runs ra_tasks.interpret --repeats times per case with fresh provider calls and
scores the plan against expected tasks: in-scope decision, task count, and for
each expected request span the intent of the planner task whose verified quote
overlaps that span. Also reports run-to-run agreement and uncovered segments.
Cost: cases x repeats planning calls; --max-calls is a hard stop.
"""
import argparse, collections, json, re, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def norm(text):
    return re.sub(r'\s+', '', text or '')


def matches(quote, span):
    a, b = norm(quote), norm(span)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    need = max(6, int(len(short) * .6))
    return any(short[i:i + need] in long_ for i in range(len(short) - need + 1))


def score(case, u):
    expected = case.get('expected_tasks', [])
    in_scope = u['intent'] != 'out_of_scope'
    row = {'in_scope_ok': in_scope == case.get('expected_in_scope', True)}
    if not expected:
        return row
    from rag.ra_requirements import named_products
    planned = [*u['tasks'], *u.get('out_of_scope_tasks', [])]
    tasks = [(t['intent_check']['quote'] if t['intent_check']['quote_verified'] else t['request'], t['intent']['intent'], t['request'])
             for t in planned]
    found = []
    for e in expected:
        hit = [(intent, request) for quote, intent, request in tasks if matches(quote, e['span'])]
        intents = [i for i, _ in hit]
        ok = bool(hit) and e['intent'] in intents and len(set(intents)) == 1
        # Subject restoration: the matched task's own request names every target product.
        targets_ok = None if 'targets' not in e else bool(hit) and all(
            set(e['targets']) <= set(named_products(request)) for _, request in hit)
        found.append({'span': e['span'], 'expected': e['intent'], 'got': intents, 'ok': ok, 'targets_ok': targets_ok,
                      'requests': [r for _, r in hit]})
    row.update(task_count_ok=len(planned) == len(expected), spans=found,
               all_spans_ok=all(f['ok'] for f in found),
               all_targets_ok=all(f['targets_ok'] is not False for f in found),
               uncovered=u['intent_check']['uncovered_request_segments'])
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--cases', type=Path, default=ROOT / 'eval/intent_cases_v1.json')
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--max-calls', type=int, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    spec = json.loads(a.cases.read_text(encoding='utf-8'))
    cases = spec['cases']
    if len(cases) * a.repeats > a.max_calls:
        p.error(f'{len(cases) * a.repeats} calls exceed --max-calls {a.max_calls}')
    if a.output.exists():
        p.error('Choose a new output file')
    from rag import ra_tasks
    from rag.openai_chat import OpenAIPipelineChat
    from rag import study_registry
    products = sorted(study_registry.products())
    agencies = ['CA', 'EU', 'US']
    out_root = a.output.parent / (a.output.stem + '_requests')
    results = []
    for case in cases:
        runs = []
        for _ in range(a.repeats):
            client = OpenAIPipelineChat(out_root=out_root, model='gpt-5.6-luna', context_tokens=65536)
            client.begin_request()
            try:
                u, _ = ra_tasks.interpret(case['question'], client, products, agencies)
                runs.append({'intents': u.get('intents'), 'intent': u['intent'],
                             'tasks': [{k: t.get(k) for k in ('request', 'intent', 'intent_check')}
                                       for t in [*u['tasks'], *u.get('out_of_scope_tasks', [])]],
                             'score': score(case, u)})
            except Exception as exc:
                runs.append({'error': f'{type(exc).__name__}: {exc}'})
            finally:
                client.release()
        ok = [r for r in runs if 'error' not in r]
        summary = {'id': case['id'], 'kind': case['kind'], 'runs': len(runs), 'errors': len(runs) - len(ok),
                   'in_scope_ok': sum(r['score']['in_scope_ok'] for r in ok),
                   'task_count_ok': sum(r['score'].get('task_count_ok', True) for r in ok),
                   'all_spans_ok': sum(r['score'].get('all_spans_ok', True) for r in ok),
                   'all_targets_ok': sum(r['score'].get('all_targets_ok', True) for r in ok),
                   'distinct_intent_lists': len({json.dumps(r['intents']) for r in ok}),
                   'uncovered_runs': sum(bool(r['score'].get('uncovered')) for r in ok)}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        results.append({'case': case, 'summary': summary, 'runs': runs})
    totals = collections.Counter()
    for r in results:
        for k in ('runs', 'in_scope_ok', 'task_count_ok', 'all_spans_ok', 'all_targets_ok', 'uncovered_runs'):
            totals[k] += r['summary'][k]
        totals['stable_cases'] += r['summary']['distinct_intent_lists'] == 1
    by_kind = collections.defaultdict(collections.Counter)
    for r in results:
        by_kind[r['case']['kind']]['runs'] += r['summary']['runs']
        by_kind[r['case']['kind']]['all_spans_ok'] += r['summary']['all_spans_ok']
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x', encoding='utf-8') as stream:
        json.dump({'created_at': datetime.now().isoformat(), 'cases_status': spec.get('status'),
                   'planner_version': ra_tasks.VERSION, 'totals': dict(totals),
                   'by_kind': {k: dict(v) for k, v in by_kind.items()}, 'results': results},
                  stream, ensure_ascii=False, indent=1)
    print('TOTALS', json.dumps(dict(totals)), json.dumps({k: dict(v) for k, v in by_kind.items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
