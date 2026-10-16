"""Build a page-level retrieval gold set from three sources (common schema).

  qa_chemr   QA_candidates v0.2 (Wezlana FDA Product Quality Review): one group per required
             fact = the PDF pages of its evidence IDs. Author image-checked, not human reviewed.
             Questions citing evidence IDs without a page record are skipped (Q031-Q037).
  fact_gold  data/runs/retrieval_scope_2026-10-15/nondefault_gold: one group per fact = its
             hand-checked pages.
  e2e29      the 29 answer-evaluation questions. Approval-timeline dates use the labelled
             date fields (ra_timeline facts: exact pages). Other expected values (dev rubric,
             MAH, shelf life, guard dates/IDs) become "silver" groups: pages, inside the
             plan's product x agency scope, whose text prints the value. Korean-only guard
             facets cannot occur in English sources and are skipped.

A group is satisfied when ANY of its (document_id, pdf_page) pages is retrieved.

  python eval/build_retrieval_gold.py --output data/runs/retrieval_gold_2026-10-16/gold.json
"""
import argparse, glob, json, re, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QA_DIR = Path(r'C:\Users\Admin\Documents\QA_candidates')
QA_DOCS = {'WEZENLA_FDA_CHEMR': 'FDA:761285Orig1s000,761331Orig1s000:ChemR'}
CHUNKS = ['data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/embedded/candidate_final/chunks.jsonl',
          'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/chunks.jsonl']
PLANS = ROOT / 'data/runs/planner_facets_2026-10-15/e2e_final'
WEAK_PAGES = 20


def norm(text):
    return re.sub(r'\s+', ' ', (text or '').replace(',', '')).lower()


def qa_chemr():
    evidence = {e['evidence_id']: e for e in map(json.loads, open(QA_DIR / 'evidence_candidates.jsonl', encoding='utf-8'))}
    items = []
    for q in map(json.loads, open(QA_DIR / 'qa_candidates_1.jsonl', encoding='utf-8')):
        if any(e not in evidence for f in q['required_facts'] for e in f['evidence_ids']):
            continue  # v0.2 additions (Q031-Q037) cite evidence IDs absent from evidence_candidates.jsonl
        groups = [{'label': f['fact_id'], 'pages': sorted({(QA_DOCS[evidence[e]['source_document_id']], evidence[e]['pdf_page'])
                                                         for e in f['evidence_ids']})} for f in q['required_facts']]
        items.append({'id': q['qa_id'], 'source': 'qa_chemr', 'gold_kind': 'author_checked', 'split': q['split'],
                      'question_type': q['question_type'], 'question': q['question'], 'groups': groups})
    return items


def fact_gold():
    gold = json.loads((ROOT / 'data/runs/retrieval_scope_2026-10-15/nondefault_gold/gold.json').read_text(encoding='utf-8'))
    return [{'id': c['id'], 'source': 'fact_gold', 'gold_kind': 'hand_checked', 'split': 'test',
             'question_type': c['template'], 'question': c['question'],
             'groups': [{'label': '/'.join(f['key']), 'pages': sorted({(p[0], p[1]) for p in f['pages']})} for f in c['facts']]}
            for c in gold['cases']]


def e2e29(chunks):
    from rag.ra_timeline import facts, choose
    from eval.prompt_variants import EXP
    from eval.answer_scoring import MAH, SHELF_MONTHS, GUARD
    from eval.e2e_enumeration import dates
    timeline = choose(facts(chunks))
    # Every page printing the chosen labelled date for that product x agency x kind.
    labelled = {}
    for f in facts(chunks):
        best = timeline.get((f['product_id'], f['jurisdiction'], f['kind']))
        if best and f['date'] == best['date']:
            labelled.setdefault((f['product_id'], f['jurisdiction'], f['kind']), set()).add(
                (f['chunk']['document_id'], f['chunk']['pdf_page']))
    cases = {c['id']: c for c in json.loads((ROOT / 'data/runs/guard_2026-10-15/cases_e2e29.json').read_text(encoding='utf-8'))}

    def scope_pages(products, agencies, test):
        return sorted({(c['document_id'], c['pdf_page']) for c in chunks
                       if (not products or c['product_id'] in products) and (not agencies or c['jurisdiction'] in agencies)
                       and test(c)})

    def text_has(options):
        # Whole-token match: 'SC' must not match inside 'description'.
        patterns = [re.compile(r'(?<![a-z0-9])' + re.escape(norm(o)) + r'(?![a-z0-9])') for o in options]
        return lambda c: any(pt.search(norm(c['text'])) for pt in patterns)

    def date_has(d):
        return lambda c: d in dates(c['text'])

    items = []
    for cid, case in cases.items():
        plan = json.loads(Path(sorted(glob.glob(str(PLANS / f'{cid}_1.json')))[0]).read_text(encoding='utf-8'))['understanding']
        products = [p for p in plan.get('products') or []]
        agencies = [j for j in plan.get('jurisdictions') or [] if j in ('EU', 'US', 'CA')]
        groups, kind = [], 'silver'
        timeline_scope = {'Q1': ('EU', 'US', 'CA'), 'Q4': ('US',), 'Q5': ('CA',), 'Q7': ('EU',)}
        if cid in timeline_scope:
            kind = 'labelled_fields'
            for (product, agency, k), pages in sorted(labelled.items()):
                if agency in timeline_scope[cid] and k in ('submission', 'approval'):
                    groups.append({'label': f'{product}/{agency}/{k}', 'pages': sorted(pages)})
        elif cid in ('Q2', 'Q3', 'Q6'):
            names = ('SB17', 'ABP654') if cid == 'Q6' else tuple(MAH)
            for product in names:
                if cid in ('Q2', 'Q6'):
                    groups.append({'label': f'{product}/EU/mah', 'pages': scope_pages([product], ['EU'], text_has([MAH[product]]))})
                if cid == 'Q2' and (product, 'EU', 'approval') in labelled:
                    groups.append({'label': f'{product}/EU/first_authorisation', 'pages': sorted(labelled[(product, 'EU', 'approval')])})
                if cid in ('Q3', 'Q6'):
                    months = SHELF_MONTHS[product]
                    phrases = [f'{m} months' for m in months] + [f'{m // 12} years' for m in months if m % 12 == 0]
                    shelf = lambda c, ph=phrases: 'shelf life' in norm(c['text']) and any(p in norm(c['text']) for p in ph)
                    groups.append({'label': f'{product}/EU/shelf_life', 'pages': scope_pages([product], ['EU'], shelf)})
        else:
            expected = EXP.get(cid) or GUARD.get(cid) or []
            for n, item in enumerate(expected, 1):
                options = item if isinstance(item, tuple) else (item,)
                if all(isinstance(o, date) for o in options):
                    test = lambda c, os=options: any(o in dates(c['text']) for o in os)
                else:
                    latin = [o for o in options if isinstance(o, str) and re.search(r'[A-Za-z0-9]', o)
                             and not re.search(r'[가-힣]', o)]
                    if not latin:
                        continue  # Korean-only wording cannot occur in English sources
                    test = text_has(latin)
                groups.append({'label': f'{cid}/item{n}:{options[0]}', 'pages': scope_pages(products, agencies, test)})
        groups = [g for g in groups if g['pages']]
        if groups:
            items.append({'id': cid, 'source': 'e2e29', 'gold_kind': kind, 'split': 'test',
                          'question_type': 'timeline' if kind == 'labelled_fields' else cid[0],
                          'question': case['question'], 'plan_report': str(PLANS / f'{cid}_1.json'), 'groups': groups})
    return items


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    chunks = [json.loads(l) for path in CHUNKS for l in open(ROOT / path, encoding='utf-8')]
    items = qa_chemr() + fact_gold() + e2e29(chunks)
    for it in items:
        for g in it['groups']:
            g['pages'] = [list(x) for x in g['pages']]
            # A value printed on many pages cannot separate rankings; reported apart from main metrics.
            g['weak'] = len(g['pages']) > WEAK_PAGES
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'note': __doc__, 'items': items}, ensure_ascii=False, indent=1), encoding='utf-8')
    from collections import Counter
    print(len(items), Counter(i['source'] for i in items), Counter(i['gold_kind'] for i in items),
          'groups', sum(len(i['groups']) for i in items))
    weak = [(i['id'], g['label'], len(g['pages'])) for i in items for g in i['groups'] if g['weak']]
    print('weak groups (>%d gold pages):' % WEAK_PAGES, len(weak), Counter(i[0][:1] for i in weak))


if __name__ == '__main__':
    main()
