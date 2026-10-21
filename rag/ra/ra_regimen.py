"""Source-backed historical regimen exclusions for completion, not claim truth.

Keep the displayed claim and all citations unchanged. Only an explicit former
proposal with matching proposal words and dose in that claim's own source can
be omitted from the observed-regimen completion view. Unknown roles remain.
"""
import re

from rag.ra.ra_quantities import QUANTITY, normalized, quantities

VERSION = 'ra-regimen-temporal-role-1'
PROPOSAL = re.compile(r'원래\s*(?:제안|계획)|당초\s*(?:제안|계획)|과거\s*(?:제안|계획)|'
                      r'original(?:ly)?\s+(?:propos\w*|plan\w*)|formerly\s+(?:propos\w*|plan\w*)|'
                      r'\b(?:proposed|planned)\b', re.I)
TRANSITION = re.compile(r'(?<=[.;!?])\s+|\n+|(?<=였고)[,\s]*|(?<=였으며)[,\s]*|'
                        r'(?<=하였으나)[,\s]*|(?<=했으나)[,\s]*|\bbut\b|\bhowever\b|'
                        r'\binstead\s+of\b', re.I)
CURRENT = re.compile(r'실제|투여되|투여하|받았|후속|변경|\b(?:received|administered|used|amended)\b', re.I)


def _historical_parts(text):
    """Recognize explicit proposal clauses; leave mixed/unclear clauses intact."""
    parts = []
    for part in TRANSITION.split(normalized(text)):
        doses = {q for q in quantities(part) if q[1] in ('mg', 'ug', 'g')}
        marker = PROPOSAL.search(part)
        if marker and doses:
            first_dose = next((m for m in QUANTITY.finditer(part) if m[2] in ('mg', 'mcg', 'μg', 'ug', 'g')), None)
            # A current-regimen predicate before the proposal cue makes the
            # whole fragment mixed; never hide it by guessing an attachment.
            current = CURRENT.search(part)
            if first_dose and not current:
                parts.append((part, doses))
    return parts


def completion_view(claim, evidence, product=None):
    sources = [s for s in claim['sources'] if product is None or
               evidence[s['evidence']].get('product_id') in (product, None)]
    quoted = '\n'.join(s['quote'] for s in sources)
    source_parts = _historical_parts(quoted)
    backed = set().union(*(q for _, q in source_parts)) if source_parts else set()
    text = claim['text']
    exclusions = []
    for part, doses in _historical_parts(text):
        if doses <= backed:
            text = text.replace(part, ' ', 1)
            exclusions.append({'text': part, 'role': 'former_proposal',
                'quantities': [{'value': str(v), 'unit': u} for v, u in sorted(doses)]})
    if exclusions:
        for part, doses in source_parts:
            if doses <= backed:
                quoted = normalized(quoted).replace(part, ' ', 1)
    return {'text': text, 'quoted': quoted, 'excluded_historical_clauses': exclusions,
            'version': VERSION, 'semantic_accuracy_certified': False}
