"""Hypothesis check: does showing the planner the document inventory raise retrieval recall?

Same 16 fact-gold questions and scoring as eval/plan_noise.py (a fact counts when a
retrieved page holds it). For each question, N fresh plans without and N with the
inventory (RA_PLANNER_INVENTORY=1); retrieval is the default profile. Planner calls
only; plans are saved and reused.

  python eval/inventory_recall.py --plans 3 --output data/runs/inventory_2026-10-15/recall.json
"""
import argparse, json, os, statistics, sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.plan_noise import GOLD, SUBSET  # noqa: E402

VARIANTS = {'plain': None, 'inventory': '1'}


def plan(case, variant, n, pipe, folder):
    from rag import ra_tasks
    path = folder / variant / f"{case['id'].replace(':', '_')}_{n}.json"
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.pop('RA_PLANNER_INVENTORY', None)
    if VARIANTS[variant]:
        os.environ['RA_PLANNER_INVENTORY'] = VARIANTS[variant]
    products = sorted({d['product_id'] for d in pipe.documents if d['product_id']})
    agencies = sorted({d['jurisdiction'] for d in pipe.documents})
    pipe.client.begin_request()
    try:
        u, _ = ra_tasks.interpret(case['question'], pipe.client, products, agencies, documents=pipe.documents)
    finally:
        pipe.client.release()
        os.environ.pop('RA_PLANNER_INVENTORY', None)
    path.write_text(json.dumps(u, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    return u


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    p.add_argument('--plans', type=int, default=3)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--plans-dir', type=Path, help='reuse plans from this folder (default: next to --output)')
    p.add_argument('--extra-default', default='', help='comma-separated doc groups searched by default (profile)')
    a = p.parse_args()
    profile = {'extra_default_groups': tuple(g for g in a.extra_default.split(',') if g)} if a.extra_default else None
    cases = {c['id']: c for c in json.loads((GOLD / 'gold.json').read_text(encoding='utf-8'))['cases']}
    from rag import ra_retrieval
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    pipe = RAPipeline(a.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    folder = a.plans_dir or a.output.parent / 'plans'
    scores, roles = {}, {}
    try:
        plans = {(cid, v): [plan(cases[cid], v, n, pipe, folder) for n in range(1, a.plans + 1)]
                 for cid in SUBSET for v in VARIANTS}
        pipe.warmup()
        corpus = pipe.retrieval_corpus()
        docs = {(d['document_id'], d['version_id']) for d in pipe.documents}
        corpus.allowed_documents = docs
        for cid in SUBSET:
            facts = [{tuple(x) for x in f['pages']} for f in cases[cid]['facts']]
            for v in VARIANTS:
                roles[f'{cid}|{v}'] = [[t['source_roles'] for t in u['tasks']] for u in plans[(cid, v)]]
                for k in (8, 40):
                    row = scores.setdefault(f'{v}@{k}', {}).setdefault(cid, [])
                    for u in plans[(cid, v)]:
                        groups = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs,
                                                       include_nondefault=False, k=k, profile=profile)
                        pages = {(h['chunk']['document_id'], h['chunk']['pdf_page']) for g in groups for h in g['hits']}
                        row.append(sum(bool(f & pages) for f in facts) / len(facts))
            print(cid, {key: val[cid] for key, val in scores.items()}, roles[f'{cid}|plain'], roles[f'{cid}|inventory'], flush=True)
    finally:
        pipe.close()
    summary = {key: round(statistics.mean(x for r in val.values() for x in r), 3) for key, val in scores.items()}
    per_question = {k: {cid: round(statistics.mean(scores[f'inventory@{k}'][cid]) - statistics.mean(scores[f'plain@{k}'][cid]), 3)
                        for cid in SUBSET} for k in (8, 40)}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'summary': summary, 'inventory_minus_plain': per_question, 'scores': scores,
                                    'roles': roles}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary), json.dumps(per_question, ensure_ascii=False))


if __name__ == '__main__':
    main()
