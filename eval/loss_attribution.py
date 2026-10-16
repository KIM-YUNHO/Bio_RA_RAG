"""Stage-wise loss attribution for answer-level gold items on saved end-to-end reports.

For every gold item of the answer evaluation (eval/answer_scoring.py, eval/prompt_variants.py)
the first stage that holds the value decides where it was lost:

  success      a kept claim states it (product/agency of its cited evidence match when known)
  validation   only a rejected claim stated it
  generation   the LLM input evidence held it (for that product/agency) but no claim stated it
  ranking      the hybrid candidate pool (40 per group) held it but the LLM input did not
  retrieval    not even the candidate pool held it
  upstream     a computed duration whose operand dates were not both answered
  unknown      the value is Korean-only wording (cannot occur in English sources)

The candidate pool is rebuilt from each report's saved plan (no model calls). Dev rubric
values are matched as plain text, so a generic value (e.g. "45 mg") found anywhere in the
input counts as present; generation losses for such items are an upper bound.

  python eval/loss_attribution.py data/runs/planner_facets_2026-10-15/e2e_atomic_fuse --output data/runs/loss_2026-10-16/attribution.json
"""
import argparse, glob, json, re, sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STAGES = ['success', 'validation', 'generation', 'ranking', 'retrieval', 'upstream', 'unknown']


def norm(text):
    return re.sub(r'\s+', ' ', (text or '').replace(',', '')).lower()


def text_matcher(options):
    opts = [norm(o) for o in options]
    return lambda t: any(o in norm(t) for o in opts)


def items_for(case, units):
    """[(label, product, agency, matcher, latin_ok, kind)] for one case ID."""
    from eval.e2e_enumeration import dates
    from eval.answer_scoring import MAH, SHELF_MONTHS, GUARD, months
    from eval.prompt_variants import EXP
    out = []
    timeline = {'Q1': ('EU', 'US', 'CA'), 'Q4': ('US',), 'Q5': ('CA',), 'Q7': ('EU',)}
    if case in timeline:
        for (p, j), u in sorted(units.items()):
            if j not in timeline[case]:
                continue
            for kind in ('submission', 'approval'):
                if u.get(kind):
                    out.append((f'{p}/{j}/{kind}', p, j, (lambda d: lambda t: d in dates(t))(u[kind]), True, 'date'))
            if case in ('Q1', 'Q5', 'Q7'):
                out.append((f'{p}/{j}/duration', p, j, None, True, 'duration'))
    elif case in ('Q2', 'Q3', 'Q6'):
        names = ('SB17', 'ABP654') if case == 'Q6' else tuple(MAH)
        for p in names:
            if case in ('Q2', 'Q6'):
                out.append((f'{p}/EU/mah', p, 'EU', text_matcher([MAH[p]]), True, 'text'))
            if case == 'Q2' and units.get((p, 'EU'), {}).get('approval'):
                d = units[(p, 'EU')]['approval']
                out.append((f'{p}/EU/first_authorisation', p, 'EU', (lambda d: lambda t: d in dates(t))(d), True, 'date'))
            if case in ('Q3', 'Q6'):
                gold = SHELF_MONTHS[p]
                out.append((f'{p}/EU/shelf_life', p, 'EU', (lambda g: lambda t: bool(months(t) & g))(gold), True, 'text'))
    else:
        for n, item in enumerate(EXP.get(case) or GUARD.get(case) or [], 1):
            options = item if isinstance(item, tuple) else (item,)
            if all(isinstance(o, date) for o in options):
                out.append((f'item{n}:{options[0]}', None, None, (lambda os: lambda t: any(o in dates(t) for o in os))(options),
                            True, 'date'))
            else:
                latin = [o for o in options if re.search(r'[A-Za-z0-9]', o) and not re.search(r'[가-힣]', o)]
                out.append((f'item{n}:{options[0]}', None, None, text_matcher(options), bool(latin), 'text'))
                if latin:
                    out[-1] = out[-1][:3] + (text_matcher(options), True, 'text')
    return out


def attribute(case, report, units, pool_chunks):
    result = report['results'][0]
    evidence = result.get('evidence', {})
    kept, rejected = result.get('claims', []), result.get('validation', {}).get('rejected_items', [])

    def claim_hits(claims, product, agency, matcher, need_sources):
        for c in claims:
            if product and product not in (c.get('products') or []):
                continue
            agencies = {evidence[s['evidence']].get('jurisdiction') for s in c.get('sources', []) if s.get('evidence') in evidence}
            if agency and (agencies or need_sources) and agency not in agencies:
                continue
            if matcher(c.get('text', '')):
                return True
        return False

    def block_hits(blocks, product, agency, matcher):
        return any((not product or b.get('product_id') == product) and (not agency or b.get('jurisdiction') == agency)
                   and matcher(b.get('text', '')) for b in blocks)

    rows, answered = [], {}
    for label, product, agency, matcher, latin_ok, kind in items_for(case, units):
        if kind == 'duration':
            ok = any((c.get('computed') or {}).get('operation') == 'date_difference' and product in c.get('products', [])
                     and tuple(c['computed'].get('operands', [])) == tuple(d.isoformat() for d in (units[(product, agency)]['submission'],
                                                                                              units[(product, agency)]['approval']))
                     for c in kept)
            both = answered.get(f'{product}/{agency}/submission') and answered.get(f'{product}/{agency}/approval')
            stage = 'success' if ok else ('generation' if both else 'upstream')
        elif claim_hits(kept, product, agency, matcher, need_sources=True):
            stage = 'success'
        elif claim_hits(rejected, product, agency, matcher, need_sources=False):
            stage = 'validation'
        elif not latin_ok:
            stage = 'unknown'
        elif block_hits(evidence.values(), product, agency, matcher):
            stage = 'generation'
        elif block_hits(pool_chunks, product, agency, matcher):
            stage = 'ranking'
        else:
            stage = 'retrieval'
        answered[label] = stage == 'success'
        rows.append({'item': label, 'stage': stage})
    return rows


def bucket(case):
    return {'C': 'detail', 'E': 'detail', 'N': 'detail', 'T': 'table', 'U': 'detail', 'G': 'guard'}.get(case[0]) or \
        ('timeline' if case in ('Q1', 'Q4', 'Q5', 'Q7') else 'enumeration')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('reports', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--pool', type=int, default=40)
    a = p.parse_args()
    from rag import ra_retrieval, answer
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    from eval.answer_scoring import timeline_gold
    run = ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release'
    units = timeline_gold(run)
    pipe = RAPipeline(run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2',
                      research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    rows = []
    try:
        corpus = pipe.retrieval_corpus()
        corpus._emb = answer.Embedder('bge-m3', local_only=True)
        corpus.allowed_documents = {(d['document_id'], d['version_id']) for d in pipe.documents}
        for path in sorted(glob.glob(str(a.reports / '*_[12].json'))):
            report = json.loads(Path(path).read_text(encoding='utf-8'))
            case = Path(path).stem.split('_')[0]
            groups = ra_retrieval.retrieve(corpus, deepcopy(report['understanding']), pipe.guidelines,
                                           documents=corpus.allowed_documents, k=a.pool,
                                           profile={'rerank': None, 'rerank_fuse': False})
            pool = [h['chunk'] for g in groups for h in g['hits']]
            for row in attribute(case, report, units, pool):
                rows.append({'report': Path(path).stem, 'case': case, 'bucket': bucket(case), **row})
            print(Path(path).stem, dict(Counter(r['stage'] for r in rows if r['report'] == Path(path).stem)), flush=True)
    finally:
        pipe.close()
    summary = {'all': dict(Counter(r['stage'] for r in rows))}
    for b in sorted({r['bucket'] for r in rows}):
        summary[b] = dict(Counter(r['stage'] for r in rows if r['bucket'] == b))
    by_case = defaultdict(Counter)
    for r in rows:
        by_case[r['case']][r['stage']] += 1
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'summary': summary, 'by_case': {k: dict(v) for k, v in by_case.items()}, 'rows': rows},
                                   ensure_ascii=False, indent=1), encoding='utf-8')
    for k, v in summary.items():
        print(k, json.dumps({s: v.get(s, 0) for s in STAGES}))


if __name__ == '__main__':
    main()
