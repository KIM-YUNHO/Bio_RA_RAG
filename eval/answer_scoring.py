"""Answer-level scoring of the enumeration questions in data/runs/answer_eval_2026-10-15.

Gold values come from labelled regulator fields and were hand-checked on
2026-10-15: Q1 submission/approval dates per product x agency (+ code-computed
durations), Q2 EU first authorisation date (SmPC section 9) and marketing
authorisation holder (SmPC section 7), Q3 EU shelf life (SmPC 6.3; any printed
presentation value). A value counts when a kept claim about that product, citing
that agency's evidence, states it. Values attributed to a unit that are not gold
are listed for manual review, not auto-judged.

  python eval/answer_scoring.py data/runs/answer_eval_2026-10-15/baseline data/runs/answer_eval_2026-10-15/branch
"""
import glob, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.e2e_enumeration import gold as timeline_gold, dates, score as score_q1  # noqa: E402

MAH = {'ABP654': 'Amgen Technology (Ireland)', 'CTP43': 'Celltrion Healthcare Hungary', 'BAT2206': 'Gedeon Richter',
       'SB17': 'Samsung Bioepis NL', 'BMAB1200': 'Biosimilar Collaborations Ireland'}
SHELF_MONTHS = {'ABP654': {36}, 'CTP43': {48}, 'BAT2206': {24}, 'SB17': {42}, 'BMAB1200': {36, 24}}


def months(text):
    found = {int(n) * 12 for n in re.findall(r'(\d+)\s*(?:years?|년)', text, re.I)}
    return found | {int(n) for n in re.findall(r'(\d+)\s*(?:months?|개월)', text, re.I)}


def eu_claims(report, product):
    result = report['results'][0]
    evidence = result.get('evidence', {})
    for claim in result.get('claims', []):
        agencies = {evidence[s['evidence']].get('jurisdiction') for s in claim['sources'] if s['evidence'] in evidence}
        if product in claim['products'] and 'EU' in agencies:
            yield claim


def score_q2(report, units):
    rows, other = {}, []
    for product, mah in MAH.items():
        authorised = units.get((product, 'EU'), {}).get('approval')
        texts = [c['text'] for c in eu_claims(report, product)]
        found = set().union(*(dates(t) for t in texts)) if texts else set()
        rows[product] = {'first_authorisation': authorised in found,
                         'mah': any(mah.lower() in re.sub(r'\s+', ' ', t).lower() for t in texts)}
        other += [(product, d.isoformat()) for d in found - {authorised}]
    return {'items': 2 * len(rows), 'correct': sum(v for r in rows.values() for v in r.values()), 'by_product': rows,
            'non_gold_dates_for_review': other}


def score_q3(report, units):
    rows, other = {}, []
    for product, gold in SHELF_MONTHS.items():
        found = set().union(*(months(c['text']) for c in eu_claims(report, product))) if True else set()
        rows[product] = bool(found & gold)
        other += [(product, m) for m in found - gold]
    return {'items': len(rows), 'correct': sum(rows.values()), 'by_product': rows, 'non_gold_months_for_review': other}


def main():
    units = timeline_gold(ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    for folder in sys.argv[1:]:
        print('==', folder)
        for path in sorted(glob.glob(str(Path(folder) / 'Q?_?.json'))):
            report = json.loads(Path(path).read_text(encoding='utf-8'))
            name = Path(path).stem
            if name.startswith('Q1'):
                s = score_q1(report, units)
                summary = {'items': 2 * s['units'] + s['units'], 'correct': s['submission_dates'] + s['approval_dates'] +
                           s['durations_computed_correctly'], 'dates': s['submission_dates'] + s['approval_dates'],
                           'durations': s['durations_computed_correctly'], 'claims_kept': s['claims_kept']}
            elif name.startswith('Q2'):
                s = score_q2(report, units); summary = {k: s[k] for k in ('items', 'correct', 'non_gold_dates_for_review')}
            else:
                s = score_q3(report, units); summary = {k: s[k] for k in ('items', 'correct', 'non_gold_months_for_review')}
            summary['status'] = report['results'][0]['status']
            print(' ', name, json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
