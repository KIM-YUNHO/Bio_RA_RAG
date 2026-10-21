"""Answer-level check of the full RA pipeline on an enumeration question.

Gold: submission and approval dates per product x agency read from labelled
regulator fields (rag.ra_timeline extractor, hand-checked 2026-10-15). A unit's
date counts as answered when a kept claim about that product, citing that
agency's evidence, prints it; a duration counts when a code-computed derived
value uses exactly that unit's (submission, approval) dates. Dates in claims that
are not the unit's gold dates are listed for manual review, not auto-judged.

  python eval/e2e_enumeration.py --config rerank_corr --repeat 2 --output-dir data/runs/e2e_enumeration_2026-10-15
  python eval/e2e_enumeration.py --score <report.json> ...   (score saved reports only)
"""
import argparse, json, re, sys, time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QUESTION = '우스테키누맙의 모든 바이오시밀러들의 ra 허가 날짜를 정리해줄래?\n제출 날짜 부터 최종 허가까지 걸린 기간이 궁금하다.'
CONFIGS = {'default_retrieval': None,
           'rerank_corr': {'rerank': 'both', 'extra_default_groups': ('correspondence',)},
           'rerank_request_corr': {'rerank': 'request', 'extra_default_groups': ('correspondence',)},
           'rerank_pool20_corr': {'rerank': 'both', 'rerank_pool': 20, 'extra_default_groups': ('correspondence',)},
           'rerank_request_pool20_corr': {'rerank': 'request', 'rerank_pool': 20, 'extra_default_groups': ('correspondence',)}}
KOREAN = re.compile(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일')


def gold(run):
    from rag.ra_timeline import facts, choose
    chunks = [json.loads(line) for line in open(run / 'candidate/chunks.jsonl', encoding='utf-8')]
    best = choose(facts(chunks))
    units = {}
    for (product, jurisdiction, kind), fact in best.items():
        if kind in ('submission', 'approval'):
            units.setdefault((product, jurisdiction), {})[kind] = fact['date']
    return units


def dates(text):
    from rag.ra_derived import DATE, parse
    found = {parse(re.sub(r'\s+', ' ', m)) for m in re.findall(DATE, text)} - {None}
    found |= {date(int(y), int(m), int(d)) for y, m, d in KOREAN.findall(text)}
    return found


def score(report, units):
    result = report['results'][0]
    evidence = result.get('evidence', {})
    seen = {u: set() for u in units}
    durations, other = set(), []
    for claim in result.get('claims', []):
        agencies = {evidence[s['evidence']].get('jurisdiction') for s in claim['sources'] if s['evidence'] in evidence}
        keys = [(p, j) for p in claim['products'] for j in agencies if (p, j) in units]
        if claim.get('computed', {}).get('operation') == 'date_difference':
            a, b = (date.fromisoformat(x) for x in claim['computed']['operands'])
            durations |= {k for k in keys if (units[k].get('submission'), units[k].get('approval')) == (a, b)}
            continue
        for key in keys:
            found = dates(claim['text'])
            seen[key] |= found
            other += [(key, d.isoformat(), claim['text'][:120]) for d in found - set(units[key].values())]
    rows = {f'{p}/{j}': {'submission': units[(p, j)].get('submission') in seen[(p, j)],
                         'approval': units[(p, j)].get('approval') in seen[(p, j)],
                         'duration': (p, j) in durations} for p, j in sorted(units)}
    rejected = {}
    for item in result.get('validation', {}).get('rejected_items', []):
        rejected[item.get('reason')] = rejected.get(item.get('reason'), 0) + 1
    return {'units': len(rows), 'submission_dates': sum(r['submission'] for r in rows.values()),
            'approval_dates': sum(r['approval'] for r in rows.values()),
            'durations_computed_correctly': sum(r['duration'] for r in rows.values()),
            'claims_kept': len(result.get('claims', [])), 'rejected_reasons': rejected,
            'derived_withheld': [d['reason'] for d in result.get('validation', {}).get('derived_withheld', [])],
            'status': result.get('status'), 'llm_calls': len(report.get('generation_telemetry', [])),
            'input_tokens': report.get('usage', {}).get('input_tokens'), 'seconds': report.get('seconds'),
            'non_gold_dates_for_review': other, 'by_unit': rows}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    p.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    p.add_argument('--config', choices=sorted(CONFIGS))
    p.add_argument('--repeat', type=int, default=1)
    p.add_argument('--output-dir', type=Path)
    p.add_argument('--score', type=Path, nargs='*', default=[])
    a = p.parse_args()
    units = gold(a.run)
    for path in a.score:
        data = json.loads(path.read_text(encoding='utf-8'))
        print(path.name, json.dumps({k: v for k, v in score(data.get('report', data), units).items() if k != 'by_unit'},
                                    ensure_ascii=False))
    if not a.config:
        return
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    pipe = RAPipeline(a.run, None, load_config(ROOT / 'configs/preprocess.local.json'),
                      guideline_snapshot=a.guidelines, research_rounds=1, retrieval_pages=8,
                      retrieval_profile=CONFIGS[a.config],
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    a.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        pipe.warmup()
        for n in range(1, a.repeat + 1):
            out = a.output_dir / f'{a.config}_{n}.json'
            if out.exists():
                print(out.name, 'exists; scoring only')
                report = json.loads(out.read_text(encoding='utf-8'))
            else:
                started = time.monotonic()
                try:
                    report = pipe.ask(QUESTION)
                except Exception as exc:
                    out.with_suffix('.failure.json').write_text(json.dumps(
                        {'error': f'{type(exc).__name__}: {exc}', 'failure': pipe.last_failure}, ensure_ascii=False,
                        indent=1, default=str), encoding='utf-8')
                    print(out.name, 'FAILED', type(exc).__name__, exc, flush=True)
                    continue
                report['e2e'] = {'config': a.config, 'retrieval_profile': CONFIGS[a.config],
                                 'wall_seconds': round(time.monotonic() - started, 1)}
                out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
            s = score(report, units)
            (a.output_dir / f'{a.config}_{n}.score.json').write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str),
                                                                     encoding='utf-8')
            print(out.name, json.dumps({k: v for k, v in s.items() if k not in ('by_unit', 'non_gold_dates_for_review')},
                                       ensure_ascii=False), flush=True)
            print(report['results'][0]['answer_ko'][:3000], flush=True)
    finally:
        pipe.close()


if __name__ == '__main__':
    main()
