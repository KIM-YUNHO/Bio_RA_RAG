"""Derived values computed by code from verified, cited operands.

The model never writes a calculated number. It declares an operation and the two
span IDs whose printed values are the operands; this module computes the result
only when both operands are cited by claims that passed review and each operand
span prints exactly one value of the needed type.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import re

VERSION = 'ra-derived-values-2-printed-operands'
OPERATIONS = ['date_difference', 'difference', 'ratio', 'percent_change']

MONTHS = {m: n for n, names in enumerate((
    ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'), ('may',),
    ('jun', 'june'), ('jul', 'july'), ('aug', 'august'), ('sep', 'sept', 'september'),
    ('oct', 'october'), ('nov', 'november'), ('dec', 'december')), 1) for m in names}
DATE = (r'(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}'
        r'|[A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4}|\d{2}/\d{2}/\d{4})')
NUMBER = re.compile(r'(?<![\w.])[-+−]?\d+(?:\.\d+)?(?![\w.])')


def parse(text):
    try:
        return _parse(text)
    except ValueError:  # e.g. 13/45/2024 is not a date
        return None


def _parse(text):
    text = text.strip()
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', text)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r'(\d{2})/(\d{2})/(\d{4})', text)
    if m:
        return date(int(m[3]), int(m[1]), int(m[2]))
    m = re.fullmatch(r'(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})', text)
    if m and m[2].lower() in MONTHS:
        return date(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    m = re.fullmatch(r'([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})', text)
    if m and m[1].lower() in MONTHS:
        return date(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
    return None


def elapsed(start, end):
    """Calendar difference as (days, years, months, days)."""
    years, months, day = end.year - start.year, end.month - start.month, end.day - start.day
    if day < 0:
        months -= 1
        previous = date(end.year - (end.month == 1), 12 if end.month == 1 else end.month - 1, 1)
        following = date(previous.year + (previous.month == 12), 1 if previous.month == 12 else previous.month + 1, 1)
        day += (following - previous).days
    if months < 0:
        years -= 1
        months += 12
    return (end - start).days, years, months, day


def period_ko(start, end):
    days, years, months, rest = elapsed(start, end)
    parts = [f'{years}년' if years else '', f'{months}개월' if months else '', f'{rest}일' if rest else '']
    return f'{days}일 (' + (' '.join(p for p in parts if p) or '0일') + ')'


def plain(text):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text))


def one_date(text):
    found = {parse(re.sub(r'\s+', ' ', m)) for m in re.findall(DATE, plain(text))} - {None}
    return found.pop() if len(found) == 1 else None


def one_number(text):
    found = {m.replace('−', '-') for m in NUMBER.findall(plain(text))}
    if len(found) != 1:
        return None
    try:
        return Decimal(found.pop())
    except InvalidOperation:
        return None


def fmt(value):
    value = value.quantize(Decimal('0.0001')).normalize()
    return format(value, 'f')


def without_product_names(text):
    """Registered product codes (SB17, ABP 654) are identity, not asserted numbers."""
    from rag import study_registry
    names = sorted({n for p, m in study_registry.products().items() for n in [p, *m.get('names', [])]}, key=len, reverse=True)
    for name in names:
        text = re.sub(r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])', ' ', text, flags=re.I)
    return text


def operand(printed, source, claim, kind):
    """Read one operand: the printed value must appear in the operand span or in another
    source of the same verified claim (a table header cited with its value cell)."""
    reader = one_date if kind == 'date' else one_number
    if not printed:
        return reader(source['quote'])
    wanted = re.sub(r'\s+', ' ', plain(printed)).strip().lower()
    for quote in [source['quote'], *(s['quote'] for s in claim['sources'] if s is not source)]:
        if wanted and wanted in re.sub(r'\s+', ' ', plain(quote)).lower():
            return reader(printed)
    return None


def compute(declared, claims, start=1):
    """Return (derived claims, withheld declarations) for verified claims."""
    cited = {}
    for claim in claims:
        for source in claim['sources']:
            cited.setdefault(source.get('citation_alias'), (claim, source))
    made, withheld = [], []
    for n, item in enumerate(declared or [], start):
        ids, label, op = list(item.get('operand_span_ids') or []), str(item.get('label', '')).strip(), item.get('operation')
        record = {'id': f'D{n}', 'declaration': item, 'version': VERSION}
        if op not in OPERATIONS or len(ids) != 2 or not label:
            withheld.append({**record, 'reason': 'derived_declaration_invalid'}); continue
        if any(i not in cited for i in ids):
            withheld.append({**record, 'reason': 'derived_operand_not_in_verified_claims'}); continue
        (ca, sa), (cb, sb) = cited[ids[0]], cited[ids[1]]
        # A label may repeat an identifier printed in the operand claims' sources
        # (e.g. "NDS Control #277532"), never a new number such as a result.
        printed_numbers = set(re.findall(r'\d+', ' '.join(s['quote'] for c in (ca, cb) for s in c['sources'])))
        if not set(re.findall(r'\d+', without_product_names(label))) <= printed_numbers:
            withheld.append({**record, 'reason': 'derived_label_contains_number'}); continue
        printed = list(item.get('operand_values') or [None, None]) + [None, None]
        if op == 'date_difference':
            a, b = operand(printed[0], sa, ca, 'date'), operand(printed[1], sb, cb, 'date')
            if a is None or b is None:
                withheld.append({**record, 'reason': 'derived_operand_value_ambiguous'}); continue
            if b < a:
                withheld.append({**record, 'reason': 'derived_negative_interval'}); continue
            result, shown = (b - a).days, f'{a.isoformat()} → {b.isoformat()}: {period_ko(a, b)}'
        else:
            a, b = operand(printed[0], sa, ca, 'number'), operand(printed[1], sb, cb, 'number')
            if a is None or b is None or (op in ('ratio', 'percent_change') and (b if op == 'ratio' else a) == 0):
                withheld.append({**record, 'reason': 'derived_operand_value_ambiguous'}); continue
            value = b - a if op == 'difference' else a / b if op == 'ratio' else (b - a) / a * 100
            result = fmt(value)
            shown = {'difference': f'{fmt(b)} − {fmt(a)} = {result}', 'ratio': f'{fmt(a)} / {fmt(b)} = {result}',
                     'percent_change': f'{fmt(a)} → {fmt(b)}: {result}%'}[op]
            a, b = fmt(a), fmt(b)
        made.append({'id': record['id'], 'kind': 'interpretation', 'claim_basis': 'comparison_of_sources',
            'text': f'{label}: {shown} (코드 계산)',
            'products': list(dict.fromkeys([*ca['products'], *cb['products']])),
            'task_ids': list(dict.fromkeys([*ca['task_ids'], *cb['task_ids']])),
            'sources': [sa, sb] if sa is not sb else [sa],
            'computed': {'version': VERSION, 'operation': op, 'operands': [str(a), str(b)], 'result': str(result),
                         'operand_claim_ids': [ca['id'], cb['id']], 'generated_by_model': False}})
    return made, withheld
