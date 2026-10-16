"""Narrow numeric invariants; semantic/study attribution is reviewed separately."""
from decimal import Decimal
from html import unescape
import re
import unicodedata

NUMBER = re.compile(r'(?<![A-Za-z\d])[-+−]?\d+(?:[.,]\d+)*(?!\d)')
UNIT_PATTERN = r'ng/mL|ng·h/mL|h\*ng/mL|kg/m2|mg|mcg|μg|ug|g|mL|ml|kg|%|weeks?|days?|hours?|h|주|일|시간'
QUANTITY = re.compile(r'(?<![\w.])([+−-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*[-‐‑]?\s*('
    + UNIT_PATTERN + r')(?![A-Za-z])')
WORDS = dict(zip('one two three four five six seven eight nine ten'.split(), range(1, 11)))
MONTHS = dict(zip('jan feb mar apr may jun jul aug sep oct nov dec'.split(), range(1, 13)))


def normalized(text):
    # HTML attributes (rowspan, colspan) are layout, never printed numbers.
    text = unescape(re.sub(r'<[^>]*>', ' ', text))
    text = unicodedata.normalize('NFKC', text).replace(r'\n', '\n').replace(r'\r', '\r')
    text = re.sub(r'kg/m\s*\$\s*\^\{?2\}?\s*\$', 'kg/m2', text)
    return re.sub(r'\b(' + '|'.join(WORDS) + r')\b', lambda m: str(WORDS[m[0].lower()]), text, flags=re.I)


def numeric_token(value):
    """Canonicalize exact values, preserving malformed/decimal-comma tokens."""
    value = value.replace('−', '-')
    if ',' in value:
        if not re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', value):
            return value
        value = value.replace(',', '')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', value):
        return value
    number = Decimal(value)
    if number.is_zero():
        return '0'
    return format(number, 'f').rstrip('0').rstrip('.') if '.' in str(number) else str(number)


def numbers(text):
    text = normalized(text)
    result = {numeric_token(n) for n in NUMBER.findall(text)}
    # Roman phase labels have a numeric meaning only in an explicit phase
    # phrase. Never treat an isolated I, II or IV as a number or a dose.
    for phase in re.findall(r'\bPhase\s+(IV|III|II|I)(?![A-Za-z])', text, re.I):
        result.add(str({'I':1,'II':2,'III':3,'IV':4}[phase.upper()]))
    # Add a month number only to an explicit day-month-year date, never to an
    # arbitrary month mention. Keep the printed source unchanged.
    for match in re.finditer(r'\b\d{1,2}\s+([A-Za-z]+)\s+\d{4}\b', text):
        month = MONTHS.get(match[1][:3].lower())
        if month:
            result.add(str(month))
    month_names='January February March April May June July August September October November December'.split()
    for match in re.finditer(r'\b('+'|'.join(month_names+list(MONTHS))+r')\s+\d{4}\b',text,re.I):
        result.add(str(MONTHS[match[1][:3].lower()]))
    return result


def unit_alias(unit):
    aliases = {'ml': 'mL', 'mcg': 'ug', 'μg': 'ug', 'week': 'week', 'weeks': 'week', '주': 'week',
               'days': 'day', '일': 'day', 'hours': 'h', 'hour': 'h', '시간': 'h'}
    return aliases.get(unit, unit)


def units(text):
    return list(dict.fromkeys(unit_alias(m[0]) for m in re.finditer(
        r'(?<![A-Za-z])(?:' + UNIT_PATTERN + r')(?![A-Za-z0-9])', normalized(text))))


def quantities(text):
    text = normalized(text)
    # Calendar dates have numeric support, but their day is not a duration.
    text = re.sub(r'\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일', ' ', text)
    result = {(Decimal(numeric_token(value)), unit_alias(unit)) for value, unit in QUANTITY.findall(text)
              if re.fullmatch(r'[+-]?\d+(?:\.\d+)?',numeric_token(value))}
    result.update((Decimal(value), 'day') for value in re.findall(r'\bDay\s*(\d+)\b', text, re.I))
    return result


LISTED = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)+\s*,?\s*(?:and|or)\s+\d+(?:\.\d+)?)\s*('
    + UNIT_PATTERN + r')(?![A-Za-z])')


def listed_quantities(text):
    """A printed source list shares its one trailing unit: "at 10, 15, and 113 days".

    Used only on cited source text, so a claim may write "10일, 15일, 113일".
    """
    result = set()
    for values, unit in LISTED.findall(normalized(text)):
        result.update((Decimal(numeric_token(v)), unit_alias(unit)) for v in re.findall(r'\d+(?:\.\d+)?', values))
    return result


def proposed_quantities(text):
    """Recognize an explicit pair sharing a trailing unit in a proposal.

    Used only to prevent a review rewrite from adding quantities. This does
    not supply source evidence or approve group/statistic attribution.
    """
    result = quantities(text)
    number = r'([+−-]?\d+(?:,\d{3})*(?:\.\d+)?)'
    pair = (r'(?<![\w.])' + number + r'\s*(?:±|\s및\s|\sand\s)\s*' + number
            + r'\s*(' + UNIT_PATTERN + r')(?![A-Za-z])')
    for left, right, unit in re.findall(pair, normalized(text)):
        for value in (left, right):
            token = numeric_token(value)
            if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', token):
                result.add((Decimal(token), unit_alias(unit)))
    return result


def substantive_text(claim, evidence):
    """Ignore a study identifier only if its own quoted/source heading binds it."""
    from rag import study_registry
    text = claim['text']
    removals = set()
    for product in claim['products']:
        if any(evidence[c['evidence']].get('product_id') == product for c in claim['sources']):
            # A registered product name is identity, not an administered amount.
            # Source product metadata already constrains this proposal's scope.
            names=[product,*study_registry.products().get(product,{}).get('names',[])]
            for name in names:
                # Not inside a longer hyphenated identifier: removing BAT-2206 from
                # BAT-2206-001-CR would leave "-001", read as the number -1.
                pattern=r'(?<![A-Za-z0-9])(?<![A-Za-z0-9]-)'+re.escape(name)+r'(?![A-Za-z0-9]|-[A-Za-z0-9])'
                removals.update((m.start(),m.end()) for m in re.finditer(pattern,text,re.I))
        identified = set()
        for cite in claim['sources']:
            block = evidence[cite['evidence']]
            if block.get('product_id') != product:
                continue
            identified.update(k for _, _, k in study_registry.finditer(cite['quote'], product))
            for anchor in block.get('structure', {}).get('heading_anchors', []):
                if anchor.get('kind') == 'study' and anchor.get('heading_location', {}).get('block_id'):
                    identified.update(k for _, _, k in study_registry.finditer(anchor.get('title', ''), product))
        for key in identified:
            study = study_registry.load()[key]
            identifiers = [key, study.get('internal_id', ''), *study.get('registry_ids', {}).values()]
            for identifier in filter(None, identifiers):
                pattern = r'(?<![A-Za-z0-9])(' + re.escape(identifier) + r')(?![A-Za-z0-9])'
                if identifier.isdigit():
                    pattern = r'(?:study|trial|시험|연구)\s*\(?(' + re.escape(identifier) + r')(?![A-Za-z0-9])'
                removals.update((m.start(1), m.end(1)) for m in re.finditer(pattern, text, re.I))
    for start, end in sorted(removals, reverse=True):
        text = text[:start] + ' ' + text[end:]
    return text


def check(claim, evidence):
    from rag.ra_table_citations import own_quantities as table_quantities
    quoted = '\n'.join(s['quote'] for s in claim['sources'])
    substantive = substantive_text(claim, evidence)
    # Exact, quoted source titles identify the cited document. Its revision
    # number is not a clinical number or a date asserted by the answer.
    for cite in claim['sources']:
        title = evidence[cite['evidence']].get('title')
        if title:
            for left, right in [("'", "'"), ('"', '"'), ('‘', '’'), ('“', '”')]:
                substantive = substantive.replace(left + title + right, '')
    if not numbers(substantive) <= numbers(quoted):
        return 'claim_number_not_in_final_citations'
    if not quantities(substantive) <= quantities(quoted) | listed_quantities(quoted) | table_quantities(claim, evidence):
        return 'claim_quantity_value_or_unit_not_in_final_citations'
    # Explicit product clauses must get their quantities from their own product.
    # This is a lexical invariant, not a parser for all cross-sentence meaning.
    if len(claim['products']) > 1:
        from rag import study_registry
        names = {p: [p, *study_registry.products().get(p, {}).get('names', [])] for p in claim['products']}
        mentions = []
        for product, aliases in names.items():
            for name in aliases:
                for m in re.finditer(r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])', claim['text'], re.I):
                    mentions.append((m.start(), m.end(), product))
        ordered = sorted(set(mentions))
        for n, (start, end, product) in enumerate(ordered):
            stop = next((a for a, _, p in ordered[n + 1:] if p != product), len(claim['text']))
            wanted = quantities(claim['text'][end:stop])
            own = '\n'.join(s['quote'] for s in claim['sources'] if evidence[s['evidence']].get('product_id') == product)
            if not wanted <= quantities(own) | table_quantities(claim, evidence, product):
                return 'claim_quantity_not_in_own_product_citations'
    return None
