"""Planner-only check of the planner's answer facets against the legacy keyword decomposition.

For each case, fresh plans are made (planner call only; existing plan files are reused)
and compared with what the legacy literal patterns derive from the same question:
requirement rows (facet x product), whether the requirement path is taken, prompt
modules and fixed topics. Disagreements are listed for manual reading; neither side
is gold.

  python eval/planner_facets_check.py --cases a.json b.json --plans 2 --out data/runs/planner_facets_2026-10-15
"""
import argparse, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def strip(tasks, keys):
    return [{k: v for k, v in t.items() if k not in keys} for t in tasks]


def compare(question, u):
    from rag.ra import ra_obligations, ra_prompt, ra_topic_queries
    tasks = u['tasks']
    new_rows = ra_obligations.build(question, tasks)
    old_rows = ra_obligations.build(question)
    names = {id(v): k for k, v in vars(ra_prompt).items() if isinstance(v, str) and k.isupper()}
    def mods(t):
        plan = {**u, 'tasks': t}
        return sorted(names.get(id(p), 'intent') for p in ra_prompt.modules(question, plan, {}, 8)) + \
            (['DERIVED'] if ra_prompt.DERIVED in ra_prompt.system(question, plan, {}, 8) else [])
    legacy_tasks = strip(tasks, ('answer_facets', 'topics'))
    return {
        'facets': [[(f['facet'], f['detail']) for f in t['answer_facets']] for t in tasks],
        'topics': [t['topics'] for t in tasks],
        'rows_new': sorted({(r['facet'], r['product'] or '-') for r in new_rows}),
        'rows_old': sorted({(r['facet'], r['product'] or '-') for r in old_rows}),
        'path_new': 'requirement' if new_rows else 'requests',
        'path_old': 'requirement' if old_rows else 'requests',
        'modules_new': mods(tasks), 'modules_old': mods(legacy_tasks),
        'topics_old': [ra_topic_queries.for_task(question, t, len(tasks))[0] for t in legacy_tasks],
        'roles': [t['source_roles'] for t in tasks], 'intents': [t['intent']['intent'] for t in tasks],
        'jurisdictions': u['jurisdictions'],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--cases', type=Path, nargs='+', required=True)
    p.add_argument('--data-root', type=Path, default=ROOT)
    p.add_argument('--plans', type=int, default=2)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    from rag.llm.openai_chat import OpenAIPipelineChat
    from rag.ra.ra_pipeline import RAPipeline
    from rag.ra import ra_tasks
    from preprocess.hybrid_config import load_config
    data = a.data_root.resolve()
    cases = [c for f in a.cases for c in json.loads(f.read_text(encoding='utf-8'))]
    pipe = RAPipeline(data / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release', None,
                      load_config(ROOT / 'configs/preprocess.local.json'),
                      guideline_snapshot=data / 'data/guidelines/ra_guidelines_native_2026-10-12_v2',
                      research_rounds=1, retrieval_pages=8,
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    plans = a.out / 'plans'; plans.mkdir(parents=True, exist_ok=True)
    summary = Counter(); rows = []
    for case in cases:
        products, jurisdictions = case.get('products'), case.get('jurisdictions')
        docs = [d for d in pipe.documents if (not products or d['product_id'] in products or d.get('role') == 'guideline')
                and (not jurisdictions or d['jurisdiction'] in jurisdictions)]
        domain = sorted({d['product_id'] for d in docs if d['product_id'] is not None})
        agencies = sorted({d['jurisdiction'] for d in docs})
        for n in range(1, a.plans + 1):
            path = plans / f"{case['id']}_{n}.json"
            if path.exists():
                u = json.loads(path.read_text(encoding='utf-8'))
            else:
                if hasattr(pipe.client, 'begin_request'):
                    pipe.client.begin_request()
                try:
                    u, _ = ra_tasks.interpret(case['question'], pipe.client, domain, agencies, selected_products=products,
                                              selected_agencies=jurisdictions, documents=pipe.documents)
                finally:
                    pipe.client.release()
                path.write_text(json.dumps(u, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
            c = compare(u['source_question'], u)
            c.update(case=case['id'], plan=n)
            rows.append(c)
            summary['plans'] += 1
            summary['same_path'] += c['path_new'] == c['path_old']
            summary['same_rows'] += c['rows_new'] == c['rows_old']
            summary['same_modules'] += c['modules_new'] == c['modules_old']
            print(case['id'], n, c['path_old'], '->', c['path_new'], 'rows', 'same' if c['rows_new'] == c['rows_old'] else 'DIFF',
                  'modules', 'same' if c['modules_new'] == c['modules_old'] else f"{c['modules_old']} -> {c['modules_new']}", flush=True)
    (a.out / 'compare.json').write_text(json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1),
                                        encoding='utf-8')
    print(dict(summary))


if __name__ == '__main__':
    main()
