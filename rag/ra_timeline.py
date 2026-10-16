"""Regulatory approval timelines read by code from fixed source fields.

Submission and approval dates are taken from labelled regulator fields (EPAR
procedural text, SmPC section 9, FDA review cover tables and approval package
covers, Health Canada SBD headers). Every date is an exact quote of the cited
evidence text. Durations are calendar differences computed here, never written
by a model, so they cannot be rejected as unprinted numbers or hallucinated.
"""
from copy import deepcopy
import re

VERSION = 'ra-approval-timeline-1'

from rag.ra_derived import DATE, parse, elapsed, period_ko  # shared date utilities
CELL = r'(?:\s|</?t[dh][^>]*>|</?b>)*'
AGENCIES = {'EU': 'EU(EMA)', 'US': 'US(FDA)', 'CA': 'CA(Health Canada)'}

# The question must ask for a date or an elapsed period of an approval, not
# merely mention approval (e.g. approval conditions or post-approval studies).
REQUEST = re.compile(
    r'(?:허가|승인)\s*(?:날짜|일자|일정|시점|연월일)|(?:허가|승인)일|(?:허가|승인)\s*까지|'
    r'(?:제출|신청|접수)[^?\n]{0,30}(?:허가|승인)[^?\n]{0,20}(?:기간|걸린|소요)|'
    r'approval\s+(?:date|timeline)|date\s+of\s+(?:first\s+)?(?:approval|authori[sz]ation)|'
    r'time\s+to\s+approval|(?:when|how\s+long)[^?\n]{0,40}(?:approv|authori[sz])', re.I)


def requested(question):
    return bool(REQUEST.search(question or ''))


# (jurisdiction, kind, priority, label, title predicate, pattern, context check)
# Lower priority wins. A pattern's first group is the date.
RULES = [
    ('EU', 'submission', 1, 'EMA 판매허가 신청 제출일 (EPAR 절차 기록)',
     lambda t: 'assessment report' in t,
     r'submitted on (' + DATE + r') an application for (?:a )?marketing authori[sz]ation', None),
    ('EU', 'approval', 1, 'EU 최초 판매허가일 (SmPC 9절)',
     lambda t: 'product information' in t,
     r'Date of first authori[sz]ation:\s*(' + DATE + r')', None),
    # Original BsUFA review cover table. An interchangeability (Original 2)
    # cover uses "Target Action Date"; a resubmission row is reported apart.
    ('US', 'submission', 1, 'FDA BLA 접수일 (심사 문서 표지 Received Date)',
     lambda t: 'review' in t and 'other review' not in t,
     r'Received Date' + CELL + r'(' + DATE + r')',
     lambda text, end: re.search(r'BsUFA Goal Date', text[end:end + 300]) is not None),
    ('US', 'submission', 2, 'FDA BLA 접수일 (최초 승인 레터)',
     lambda t: 'approval letter' in t and 'orig-1' in t and 'suppl' not in t,
     r'biologics license application \(BLA\)[^.]{0,40}?received (' + DATE + r')', None),
    ('US', 'resubmission', 1, 'FDA BLA 재제출 접수일 (심사 문서 표지)',
     lambda t: 'review' in t and 'other review' not in t,
     r'Received Date' + CELL + r'Resubmission received (' + DATE + r')', None),
    ('US', 'approval', 1, 'FDA 최초 승인일 (승인 패키지 표지, Orig1)',
     lambda t: 'approval letter' in t,
     r'\d{6}Orig1\s*[–—-]\s*(' + DATE + r')', None),
    ('US', 'approval', 2, 'FDA 최초 승인일 (승인 패키지 표지)',
     lambda t: 'approval letter' in t and 'orig-1' in t and 'suppl' not in t,
     r'Approval Package for:[\s\S]{0,400}?Orig1s000(?![\s\S]{0,40}Orig2s000)[\s\S]{0,400}?(' + DATE + r')', None),
    ('US', 'approval', 3, 'FDA 승인 레터 서명일',
     lambda t: 'approval letter' in t and 'orig-1' in t and 'suppl' not in t,
     r'/s/[\s\S]{0,200}?(\d{2}/\d{2}/\d{4})', None),
    ('CA', 'submission', 1, 'Health Canada NDS 접수일 (SBD Date Filed)',
     lambda t: 'summary basis of decision' in t,
     r'Date Filed:\s*(' + DATE + r')', None),
    ('CA', 'approval', 1, 'Health Canada NOC 허가일 (SBD Authorization Date)',
     lambda t: 'summary basis of decision' in t,
     r'Authori[sz]ation Date:\s*(' + DATE + r')', None),
]


def facts(chunks):
    """Every labelled date field in the supplied chunks, with exact offsets."""
    found = []
    for chunk in chunks:
        title, text = (chunk.get('title') or '').lower(), chunk['text']
        for jurisdiction, kind, priority, label, applies, pattern, context in RULES:
            if chunk.get('jurisdiction') != jurisdiction or not applies(title):
                continue
            for m in re.finditer(pattern, text, re.I):
                value = parse(re.sub(r'\s+', ' ', m.group(1)))
                if value is None or (context and not context(text, m.end())):
                    continue
                found.append({'product_id': chunk.get('product_id'), 'jurisdiction': jurisdiction, 'kind': kind,
                    'priority': priority, 'label': label, 'date': value, 'quote': m.group(0),
                    'start': m.start(), 'end': m.end(), 'chunk': chunk})
    return found


def choose(found):
    """Highest-priority rule, then earliest date; other values are kept as conflicts."""
    best = {}
    for fact in sorted(found, key=lambda f: (f['priority'], f['date'], f['chunk'].get('pdf_page') or 0)):
        key = (fact['product_id'], fact['jurisdiction'], fact['kind'])
        if key not in best:
            best[key] = {**fact, 'other_values': []}
        elif fact['priority'] == best[key]['priority'] and fact['date'] != best[key]['date']:
            if fact['date'] not in best[key]['other_values']:
                best[key]['other_values'].append(fact['date'])
    return best


def product_label(chunks, product):
    for chunk in chunks:
        if chunk.get('product_id') == product and chunk.get('header'):
            return chunk['header'].split(' | ')[0]
    return product


def answer(corpus, understanding, question, scope_chunks):
    """Deterministic timeline result in the RA result shape, or None if no field matched."""
    from rag.ra_retrieval import blocks
    products = [p for p in understanding.get('products', [])]
    jurisdictions = [j for j in understanding.get('jurisdictions', []) if j in AGENCIES] or list(AGENCIES)
    chunks = [c for c in scope_chunks if c.get('product_id') in products and c.get('jurisdiction') in jurisdictions]
    best = choose(facts(chunks))
    if not any(kind in ('submission', 'approval') for _, _, kind in best):
        return None
    evidence, keys = {}, {}

    def cite(fact):
        chunk = fact['chunk']
        if chunk['chunk_id'] not in keys:
            block = blocks(corpus, [(chunk, 'approval_timeline_field')])['E1']
            # The evidence text must still contain the quote at the same place.
            if block['text'][fact['start']:fact['end']] != fact['quote']:
                position = block['text'].find(fact['quote'])
                if position < 0 or block['text'].find(fact['quote'], position + 1) >= 0:
                    return None
                fact = {**fact, 'start': position, 'end': position + len(fact['quote'])}
            keys[chunk['chunk_id']] = f'E{len(evidence) + 1}'
            evidence[keys[chunk['chunk_id']]] = block
        eid = keys[chunk['chunk_id']]
        if evidence[eid]['text'][fact['start']:fact['end']] != fact['quote']:
            position = evidence[eid]['text'].find(fact['quote'])
            if position < 0:
                return None
            fact = {**fact, 'start': position, 'end': position + len(fact['quote'])}
        return {'evidence': eid, 'quote': fact['quote'], 'source_range': [fact['start'], fact['end']],
                'offset_basis': 'evidence_text', 'field': fact['label']}

    task_ids = [understanding['tasks'][0]['id']] if understanding.get('tasks') else []
    available = {(c.get('product_id'), c.get('jurisdiction')) for c in chunks}
    claims, rows, gaps, lines = [], [], [], []
    for product in products:
        label = product_label(chunks, product)
        product_lines = []
        for jurisdiction in jurisdictions:
            agency = AGENCIES[jurisdiction]
            if (product, jurisdiction) not in available:
                product_lines.append(f'- {agency}: 검색 범위에 해당 기관 문서가 없음')
                gaps.append(f'{product} · {agency}: 검색 범위에 해당 기관 문서가 없습니다.')
                continue
            submission = best.get((product, jurisdiction, 'submission'))
            approval = best.get((product, jurisdiction, 'approval'))
            resubmission = best.get((product, jurisdiction, 'resubmission'))
            sources, row = [], {'product_id': product, 'jurisdiction': jurisdiction}
            for kind, fact in (('submission', submission), ('approval', approval), ('resubmission', resubmission)):
                if fact is None:
                    continue
                source = cite(fact)
                if source is None:
                    if kind == 'submission': submission = None
                    elif kind == 'approval': approval = None
                    else: resubmission = None
                    continue
                sources.append(source)
                row[kind] = {'date': fact['date'].isoformat(), 'field': fact['label'], 'evidence': source['evidence'],
                             'other_values_same_field': [d.isoformat() for d in fact['other_values']]}
            refs = ', '.join(dict.fromkeys(s['evidence'] for s in sources))
            if not submission and not approval:
                product_lines.append(f'- {agency}: 문서는 있으나 제출일·허가일 필드를 찾지 못함')
                gaps.append(f'{product} · {agency}: 제출일·허가일이 표시된 필드를 찾지 못했습니다.')
                continue
            parts = [f'제출 {submission["date"].isoformat()}' if submission else '제출일 미확인',
                     f'허가 {approval["date"].isoformat()}' if approval else '허가일 미확인']
            text = f'{product}({label}) · {agency}: ' + ', '.join(parts)
            claims.append({'id': f'C{len(claims) + 1}', 'text': text, 'kind': 'source_fact', 'products': [product],
                'claim_basis': 'source_statement', 'task_ids': task_ids, 'sources': sources,
                'timeline_fields': {k: row[k]['field'] for k in ('submission', 'approval', 'resubmission') if k in row}})
            line = f'- {agency}: ' + ' → '.join(parts)
            if submission and approval:
                if approval['date'] >= submission['date']:
                    period = period_ko(submission['date'], approval['date'])
                    row['elapsed_days'] = (approval['date'] - submission['date']).days
                    line += f' · 소요 {period}'
                    claims.append({'id': f'C{len(claims) + 1}', 'kind': 'interpretation', 'products': [product],
                        'text': f'{product} · {agency}: 제출부터 허가까지 {period} (코드 계산)',
                        'claim_basis': 'comparison_of_sources', 'task_ids': task_ids, 'sources': sources,
                        'computed': {'method': 'calendar_difference', 'from': submission['date'].isoformat(),
                                     'to': approval['date'].isoformat(), 'days': row['elapsed_days'],
                                     'generated_by_model': False}})
                else:
                    gaps.append(f'{product} · {agency}: 추출한 허가일이 제출일보다 앞서 기간을 계산하지 않았습니다.')
            else:
                gaps.append(f'{product} · {agency}: ' + ('허가일' if submission else '제출일') + ' 필드를 찾지 못해 기간을 계산하지 못했습니다.')
            if resubmission and 'resubmission' in row:
                line += f'\n    (재제출 접수 {resubmission["date"].isoformat()} — 최초 접수 후 재제출 이력이 있어 소요 기간에 포함됨)'
            conflicts = [d for k in ('submission', 'approval') if k in row for d in row[k]['other_values_same_field']]
            if conflicts:
                line += f'\n    (같은 필드에 다른 날짜도 기재됨: {", ".join(conflicts)} — 가장 이른 값을 사용)'
            product_lines.append(line + f' [{refs}]')
            rows.append(row)
        lines.append(f'■ {label}\n' + '\n'.join(product_lines))
    complete = bool(rows) and not gaps
    definitions = ('기준: EU = EMA 판매허가 신청 제출일 → 최초 판매허가일(SmPC 9절), '
                   'US = BLA 접수일 → 최초(Orig1) 승인일(교체가능성 Orig2 승인은 제외), '
                   'CA = NDS 접수일(Date Filed) → NOC 허가일. '
                   '기간은 인용된 두 날짜의 달력 차이를 코드로 계산했으며, 심사 중단(clock-stop) 기간은 구분하지 않았습니다.')
    answer_ko = ('허가 타임라인 (원문 날짜 필드를 코드로 추출, 기간은 코드 계산)\n\n' + '\n\n'.join(lines)
                 + '\n\n' + definitions)
    if gaps:
        answer_ko += '\n\n확인하지 못한 항목:\n' + '\n'.join('- ' + g for g in gaps)
    items = [{'study': 'none', 'item': c['id'], 'source_label': c['text'], 'value': s['quote'], 'quote': s['quote'],
              'evidence': [s['evidence']], 'exact': True, 'conditions': [], 'claim_kind': c['kind']}
             for c in claims if c['kind'] == 'source_fact' for s in c['sources']]
    return {'status': 'answered' if complete else 'partial', 'answer_ko': answer_ko, 'claims': claims,
        'items': items, 'gaps': gaps, 'evidence': evidence,
        'validation': {'rejected_items': [], 'semantic_review': 'not_run_deterministic_field_extraction',
            'literal_check': 'exact_quote_offsets_in_evidence_text', 'expert_certified': False,
            'timeline': {'version': VERSION, 'rows': rows, 'rules': [r[3] for r in RULES],
                         'durations_generated_by_model': False}},
        'request_coverage': {'version': VERSION, 'tasks': [{'task_id': t['id'], 'request': t['request'],
            'checks': [], 'completion_checkable': True, 'minimum_requirements_met': complete}
            for t in understanding.get('tasks', [])]},
        'original_request_coverage': None, 'citation_mode': 'timeline_fields', 'draft': None,
        'request_generation': None}
