"""Literal study identities constrain context links; headings remain hints.

These checks reject explicit contradictions. They do not infer a missing study
or silently repair a source's table reference.
"""
from copy import deepcopy
from functools import lru_cache

from rag.retrieval import study_registry as sr

VERSION = 'literal-study-context-1'


@lru_cache(maxsize=4096)
def identifiers(text, product):
    return frozenset(k for _, _, k in sr.finditer(text or '', product))


def compatible(left, right):
    if left == right:
        return True
    registry = sr.load()
    return (registry.get(left, {}).get('parent') == right or
            registry.get(right, {}).get('parent') == left)


def disjoint(left, right):
    return bool(left and right) and not any(compatible(a, b) for a in left for b in right)


def printed(block):
    structure = block.get('structure', {})
    # A caption identifies the table itself; identifiers in its cells can name
    # other trials in an overview. Never infer identity from a number alone.
    if structure.get('kind') in ('table_candidate', 'figure_candidate'):
        text = structure.get('caption') or block.get('text', '').split('<table', 1)[0]
    else:
        text = block.get('text', '')
    return identifiers(text, block.get('product_id'))


def carried(block):
    anchors = [h for h in block.get('structure', {}).get('heading_anchors', []) if h.get('kind') == 'study']
    if not anchors:
        return frozenset()
    anchor = anchors[-1]
    return identifiers(anchor.get('title', ''), block.get('product_id')) or frozenset(anchor.get('study_keys', []))


def heading_conflict(block):
    return disjoint(printed(block), carried(block))


def link_conflict(source, target, reference_text=''):
    if (source.get('document_id'), source.get('version_id')) != (target.get('document_id'), target.get('version_id')):
        return True
    if source.get('product_id') and target.get('product_id') and source['product_id'] != target['product_id']:
        return True
    # The exact reference sentence is stronger than surrounding carried scope.
    source_keys = (identifiers(reference_text, source.get('product_id')) or printed(source) or carried(source))
    # An overview table may print study IDs in its cells rather than its
    # caption. A link cannot override an explicitly different set of trials.
    target_keys = (printed(target) or identifiers(target.get('text', ''), target.get('product_id')) or carried(target))
    return disjoint(source_keys, target_keys)


def without_conflicting_heading(block):
    """Create a reading view; immutable stored chunks and literal text stay intact."""
    if not heading_conflict(block):
        return block
    result = deepcopy(block)
    structure = result['structure']
    anchors = structure.get('heading_anchors', [])
    last = max(i for i, h in enumerate(anchors) if h.get('kind') == 'study')
    structure['study_context_conflict'] = {'version': VERSION, 'status': 'unresolved',
        'printed_studies': sorted(printed(block)), 'carried_studies': sorted(carried(block))}
    # Descendants inherited from the contradicted study cannot prove identity.
    kept = anchors[:last]
    structure.update(heading_anchors=kept, section_path=[h.get('title', '') for h in kept],
                     section_id=kept[-1]['section_id'] if kept else None,
                     ancestor_section_ids=[h['section_id'] for h in kept],
                     heading_studies=[])
    candidates = result.get('study_candidates')
    if candidates:
        candidates['anchors'] = [a for a in candidates.get('anchors', []) if a.get('basis') != 'section_heading']
    return result
