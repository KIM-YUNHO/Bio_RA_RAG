"""Noise floor of fact-level retrieval recall from planner variance. No answer calls.

Retrieval is deterministic for a fixed plan, so run-to-run variance comes from the
planner (queries, roles). For a question subset, several fresh plans are frozen
(planner calls are made once; existing files are reused), and each retrieval
profile is scored on every (question, plan):

  per question   recall spread across plans
  set level      distribution of mean recall when one plan per question is drawn
                 at random (what a single-plan evaluation measures)
  paired         distribution of the profile-minus-baseline difference under the
                 same draws; a change is outside the noise only if its interval
                 excludes 0
  selection      recall@8 against recall@40 under the same plans

  python eval/plan_noise.py --plans 4 --output data/runs/retrieval_noise_2026-10-15/noise_v1.json
"""
import argparse, json, random, statistics, sys, time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GOLD = ROOT / 'data/runs/retrieval_scope_2026-10-15/nondefault_gold'
# One or two products per template; every template, every product present.
SUBSET = ['storage:SB17', 'storage:BMAB1200', 'shelf_life:ABP654', 'shelf_life:CTP43', 'mah:BAT2206', 'mah:SB17',
          'eu_first_authorisation:ABP654', 'eu_first_authorisation:SB17', 'fda_approval_date:CTP43',
          'fda_approval_date:BMAB1200', 'fda_postmarketing:SB17', 'excipients:CTP43', 'excipients:ABP654',
          'ema_submission:BMAB1200', 'hc_noc_date:CTP43', 'crohn_iv_induction:BAT2206']
PROFILES = {'baseline': {},
            'role_boost_off': {'role_boost': 0.0},
            'correspondence_default': {'extra_default_groups': ('correspondence',)}}
PROFILE_SETS = {
    'v1': PROFILES,
    'rerank': {'baseline': {}, 'rerank_request': {'rerank': 'request'}, 'rerank_queries': {'rerank': 'queries'},
               'rerank_both': {'rerank': 'both'},
               'rerank_both_corr': {'rerank': 'both', 'extra_default_groups': ('correspondence',)}},
    'rerank_cost': {'baseline': {}, 'rerank_both_corr': {'rerank': 'both', 'extra_default_groups': ('correspondence',)},
                    'rerank_request_corr': {'rerank': 'request', 'extra_default_groups': ('correspondence',)},
                    'rerank_pool20_corr': {'rerank': 'both', 'rerank_pool': 20, 'extra_default_groups': ('correspondence',)},
                    'rerank_request_pool20_corr': {'rerank': 'request', 'rerank_pool': 20,
                                                   'extra_default_groups': ('correspondence',)}},
}


def plans_for(case, n, client, pipe):
    from rag import ra_tasks
    folder = GOLD / 'plan_variants'
    folder.mkdir(exist_ok=True)
    stem = case['id'].replace(':', '_')
    result = [json.loads((GOLD / 'plans' / f'{stem}.json').read_text(encoding='utf-8'))['understanding']]
    products = sorted({d['product_id'] for d in pipe.documents if d['product_id']})
    agencies = sorted({d['jurisdiction'] for d in pipe.documents})
    for m in range(1, n):
        path = folder / f'{stem}_{m}.json'
        if not path.exists():
            client.begin_request()
            try:
                u, _ = ra_tasks.interpret(case['question'], client, products, agencies, documents=pipe.documents)
            finally:
                client.release()
            path.write_text(json.dumps({'mode': 'ra', 'question': case['question'], 'understanding': u},
                                       ensure_ascii=False, indent=1), encoding='utf-8')
        result.append(json.loads(path.read_text(encoding='utf-8'))['understanding'])
    return result


def interval(values):
    values = sorted(values)
    return [round(values[int(.025 * (len(values) - 1))], 3), round(values[int(.975 * (len(values) - 1))], 3)]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    p.add_argument('--plans', type=int, default=4)
    p.add_argument('--draws', type=int, default=5000)
    p.add_argument('--profiles', choices=sorted(PROFILE_SETS), default='v1')
    p.add_argument('--ks', default='8,40', help='comma-separated retrieval k values')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        p.error('Choose a new output file')
    cases = {c['id']: c for c in json.loads((GOLD / 'gold.json').read_text(encoding='utf-8'))['cases']}
    from rag import ra_retrieval
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    pipe = RAPipeline(a.run, None, load_config(ROOT / 'configs/preprocess.local.json'),
                      research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    scores = {}  # (profile, k) -> {case: [recall per plan]}
    roles, seconds = {}, {}
    try:
        plans = {cid: plans_for(cases[cid], a.plans, pipe.client, pipe) for cid in SUBSET}
        pipe.warmup()
        corpus = pipe.retrieval_corpus()
        docs = {(d['document_id'], d['version_id']) for d in pipe.documents}
        corpus.allowed_documents = docs
        profiles, ks = PROFILE_SETS[a.profiles], [int(x) for x in a.ks.split(',')]
        for cid in SUBSET:
            facts = [{tuple(x) for x in f['pages']} for f in cases[cid]['facts']]
            roles[cid] = [[t['source_roles'] for t in u['tasks']] for u in plans[cid]]
            for name, profile in profiles.items():
                for k in ks:
                    row = scores.setdefault(f'{name}@{k}', {}).setdefault(cid, [])
                    for u in plans[cid]:
                        started = time.perf_counter()
                        groups = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs,
                                                       include_nondefault=False, k=k, profile=profile)
                        seconds.setdefault(f'{name}@{k}', []).append(time.perf_counter() - started)
                        pages = {(h['chunk']['document_id'], h['chunk']['pdf_page']) for g in groups for h in g['hits']}
                        row.append(sum(bool(f & pages) for f in facts) / len(facts))
            print(cid, {n: v[cid] for n, v in scores.items()}, flush=True)
    finally:
        pipe.close()
    rng = random.Random(0)
    draws = [[rng.randrange(a.plans) for _ in SUBSET] for _ in range(a.draws)]
    mean = lambda key, d: statistics.mean(scores[key][cid][j] for cid, j in zip(SUBSET, d))
    summary = {}
    for key in scores:
        values = [mean(key, d) for d in draws]
        summary[key] = {'all_plans_mean': round(statistics.mean(v for r in scores[key].values() for v in r), 3),
                        'single_plan_draw_95': interval(values),
                        'questions_with_any_plan_disagreement': sum(len(set(r)) > 1 for r in scores[key].values()),
                        'retrieve_seconds_median': round(statistics.median(seconds[key]), 2),
                        'retrieve_seconds_max': round(max(seconds[key]), 2)}
    for key in scores:
        if not key.startswith('baseline'):
            base = 'baseline@' + key.split('@')[1]
            diffs = [mean(key, d) - mean(base, d) for d in draws]
            summary[key]['paired_minus_baseline_95'] = interval(diffs)
            summary[key]['paired_minus_baseline_mean'] = round(statistics.mean(diffs), 3)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'index': str(a.run), 'plans_per_question': a.plans, 'questions': SUBSET,
        'profiles': {k: {kk: list(vv) if isinstance(vv, tuple) else vv for kk, vv in v.items()} for k, v in profiles.items()},
        'summary': summary, 'scores': scores, 'planned_roles': roles}, ensure_ascii=False, indent=1), encoding='utf-8')
    for key, value in summary.items():
        print(key, json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
