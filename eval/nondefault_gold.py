"""Fact-level retrieval gold for facts printed in non-default documents.

Silver recall only contains pages cited by past answers, so it cannot reward
retrieving documents that were never searched (labels, approval letters). This
set defines each fact by a field pattern over source units — independent of
chunking — and records every (document, page) that prints it. A fact is found
if any of its pages is retrieved. Default-document facts are included as a
control. Plans come from one real planner call per question and are frozen,
so two releases are compared on identical plans.

  python eval/nondefault_gold.py --output data/runs/retrieval_scope_2026-10-15/nondefault_gold
"""
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REFRIGERATED = r'(?i)(?:\bstore\b|\bkeep\b)[^.\n]{0,60}refrigerat|refrigerat\w*[^.\n]{0,40}(?:2\s*°\s*C|36\s*°\s*F)'
# (id, location, question template, {jurisdiction: [(doc_group, title filter, pattern)]}); every pattern
# group of one jurisdiction defines the same fact. 'default' facts are a control set.
TEMPLATES = [
    ('storage', 'non_default', '{name}의 보관 조건(보관 온도)은 무엇인가?',
     {j: [('label', None, REFRIGERATED)] for j in ('EU', 'US', 'CA')}),
    ('shelf_life', 'non_default', '{name}의 유효기간(shelf life)은 얼마인가?',
     {'EU': [('label', 'product information', r'Shelf life\s*\n\s*\d+\s*(?:years|months)')],
      'US': [('correspondence', 'approval letter', r'(?i)dating period[^.]{0,160}shall be \d+ months')]}),
    ('mah', 'non_default', '{name}의 EU 판매허가 보유자(MAH)는 누구인가?',
     {'EU': [('label', 'product information', r'(?m)^\s*7\.\s+MARKETING AUTHORISATION HOLDER')]}),
    ('eu_first_authorisation', 'non_default', '{name}의 EU 최초 판매허가일은 언제인가?',
     {'EU': [('label', 'product information', r'Date of first authori[sz]ation:\s*\d')]}),
    # Original approval is also restated in later (default-searched) review documents.
    ('fda_approval_date', 'mixed', '{name}의 FDA 최초 승인일은 언제인가?',
     {'US': [('assessment', None, r'Original 1 applications? received an approval letter dated [A-Z][a-z]+ \d{1,2}, \d{4}'),
             ('assessment', None, r'On [A-Z][a-z]+ \d{1,2}, \d{4}, the Agency approved'),
             ('correspondence', 'approval letter', r'\d{6}Orig1\s*[–—-]\s*[A-Z][a-z]+ \d'),
             ('correspondence', 'orig-1', r'Approval Package for:[\s\S]{0,400}?Orig1s000(?![\s\S]{0,40}Orig2s000)'),
             ('correspondence', 'orig-1, 20', r'/s/[\s\S]{0,200}?\d{2}/\d{2}/\d{4}')]}),
    ('fda_postmarketing', 'non_default', 'FDA가 {name} 승인 시 요구한 시판 후 요구사항(PMR)이나 시판 후 약속(PMC)은 무엇인가?',
     {'US': [('correspondence', 'approval letter', r'POSTMARKETING (?:REQUIREMENTS|COMMITMENTS)')]}),
    ('excipients', 'non_default', '{name}의 첨가제(부형제) 구성은 무엇인가?',
     {'EU': [('label', 'product information', r'6\.1\s+List of excipients')],
      'CA': [('label', None, r'(?i)non-?medicinal ingredients')]}),
    ('ema_submission', 'default', '{name}의 EMA 판매허가 신청 제출일은 언제인가?',
     {'EU': [('assessment', 'assessment report', r'submitted on [^.]{0,30}20\d\d an application for (?:a )?marketing authori[sz]ation')]}),
    ('hc_noc_date', 'default', '{name}의 캐나다 Health Canada NOC 허가일은 언제인가?',
     {'CA': [('assessment', 'summary basis of decision', r'Authori[sz]ation Date:\s*\d{4}-\d{2}-\d{2}')]}),
    ('crohn_iv_induction', 'default', '{name}의 크론병 정맥 유도요법 체중별 용량은?',
     {j: [(None, None, r'260\s*mg[\s\S]{0,400}390\s*mg[\s\S]{0,400}520\s*mg')] for j in ('EU', 'US', 'CA')}),
]


def facts(run):
    """{(product, jurisdiction, template): sorted pages} from source units, joined per page."""
    candidate = run / 'candidate'
    docs = {}
    for line in open(candidate / 'chunks.jsonl', encoding='utf-8'):
        c = json.loads(line)
        docs.setdefault(c['document_id'], (c['product_id'], c['jurisdiction'], c['doc_group'], c['title'].lower()))
    pages = defaultdict(list)
    for line in open(candidate / 'source_units.jsonl', encoding='utf-8'):
        u = json.loads(line)
        if u['kind'] in ('text', 'table') and u['document_id'] in docs:
            pages[(u['document_id'], u['pdf_page'])].append(u['text'])
    found = defaultdict(set)
    for (document, page), texts in pages.items():
        product, jurisdiction, group, title = docs[document]
        text = '\n'.join(texts)
        for tid, _, _, by_agency in TEMPLATES:
            for doc_group, title_filter, pattern in by_agency.get(jurisdiction, []):
                if ((doc_group is None or group == doc_group) and (title_filter is None or title_filter in title)
                        and re.search(pattern, text)):
                    found[(product, jurisdiction, tid)].add((document, page))
    return {k: sorted(v) for k, v in found.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--no-plans', action='store_true', help='Write gold only; make no planner calls')
    a = p.parse_args()
    from rag import study_registry
    gold = facts(a.run)
    # First registry name that is a brand, not a code spelling of the product ID.
    names = {pid: [n for n in m.get('names', []) if re.sub(r'[\s-]', '', n).upper() != pid.upper()
                   and not re.fullmatch(r'[A-Z]+[- ]?\d+', n, re.I)][:1] for pid, m in study_registry.products().items()}
    cases = []
    for tid, location, template, by_agency in TEMPLATES:
        for product in sorted({k[0] for k in gold}):
            own = [{'key': list(k), 'pages': v} for k, v in gold.items() if k[0] == product and k[2] == tid]
            if not own:
                continue
            brand = re.sub(r'\s*\([^)]*\)', '', names[product][0]).strip() if names.get(product) else ''
            name = f'{brand}({product})' if brand else product
            cases.append({'id': f'{tid}:{product}', 'template': tid, 'location': location, 'scope': 'fixed',
                          'question': template.format(name=name), 'facts': own})
    a.output.mkdir(parents=True, exist_ok=True)
    (a.output / 'gold.json').write_text(json.dumps({'note': __doc__.strip().split('\n\n')[1], 'cases': cases},
                                                   ensure_ascii=False, indent=1), encoding='utf-8')
    print('cases', len(cases), 'facts', sum(len(c['facts']) for c in cases))
    if a.no_plans:
        return
    from rag import ra_tasks
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    pipe = RAPipeline(a.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    plans = a.output / 'plans'
    plans.mkdir(exist_ok=True)
    try:
        products = sorted({d['product_id'] for d in pipe.documents if d['product_id']})
        agencies = sorted({d['jurisdiction'] for d in pipe.documents})
        for case in cases:
            path = plans / (case['id'].replace(':', '_') + '.json')
            if path.exists():
                continue  # frozen plan: never re-planned
            pipe.client.begin_request()
            try:
                u, _ = ra_tasks.interpret(case['question'], pipe.client, products, agencies, documents=pipe.documents)
            finally:
                pipe.client.release()
            path.write_text(json.dumps({'mode': 'ra', 'question': case['question'], 'understanding': u},
                                       ensure_ascii=False, indent=1), encoding='utf-8')
            print(case['id'], u['products'], u['jurisdictions'], [t['source_roles'] for t in u['tasks']], flush=True)
    finally:
        pipe.close()


if __name__ == '__main__':
    main()
