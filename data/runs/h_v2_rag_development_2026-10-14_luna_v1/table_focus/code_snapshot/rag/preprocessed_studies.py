"""Source-backed study candidates; these hints never certify a study assignment."""
from collections import Counter
from copy import deepcopy

from rag import study_registry as sr

VERSION = 'source-study-candidates-2'


def annotate(chunk, units):
    out = deepcopy(chunk)
    product = chunk['product_id']
    identity = (chunk['document_id'], chunk['version_id'])
    anchors = []

    def record(uid, start, end, basis, expected=None):
        unit = units.get(uid)
        if not unit or (unit['document_id'], unit['version_id']) != identity:
            raise ValueError('Study candidate source identity mismatch')
        if not 0 <= start < end <= len(unit['text']):
            raise ValueError('Study candidate source range mismatch')
        value = unit['text'][start:end]
        found = list(sr.finditer(value, product))
        for a, b, key in found:
            if expected is not None and key not in expected:
                continue
            entry = {'study_key': key, 'basis': basis, 'unit_id': uid,
                     'document_id': identity[0], 'version_id': identity[1],
                     'representation_start': start+a, 'representation_end': start+b,
                     'quote': value[a:b], 'source_refs': deepcopy(unit['source_refs'])}
            if entry not in anchors:
                anchors.append(entry)

    for span in chunk['provenance']['source_spans']:
        record(span['unit_id'], span['representation_start'], span['representation_end'], 'literal_identifier')
    for heading in chunk['structure'].get('heading_anchors', []):
        keys = heading.get('study_keys', [])
        if not keys:
            continue
        uid = heading['heading_location']['block_id']
        unit = units.get(uid)
        if unit is None:
            raise ValueError('Study heading source is missing')
        location=heading['heading_location']
        record(uid,location.get('representation_start',0),
            location.get('representation_end',len(unit['text'])), 'section_heading', keys)
    keys = sorted({a['study_key'] for a in anchors})
    out.update(studies=keys, study_label=', '.join(sr.load()[k].get('internal_id') or k for k in keys) or None,
               study_scope='single' if len(keys) == 1 else 'multi' if keys else 'unknown',
               study_basis='+'.join(sorted({a['basis'] for a in anchors})) or 'not_assigned',
               study_attribution_status='heuristic_unverified',
               study_candidates={'version': VERSION, 'status': 'unverified', 'anchors': anchors})
    return out


def annotate_all(chunks, units):
    lookup = {u['unit_id']: u for u in units}
    result = [annotate(c, lookup) for c in chunks]
    return result, {'version': VERSION, 'chunks': len(result),
                    'with_candidates': sum(bool(c['studies']) for c in result),
                    'by_scope': dict(Counter(c['study_scope'] for c in result)),
                    'semantic_approval': False, 'hard_filter_allowed': False}
