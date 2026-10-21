"""Bounded post-retrieval context from existing, literal study headings."""
from collections import defaultdict
import re

from rag.evidence.budgeted_evidence import relevance
from rag.ra.ra_requirements import conditions, study_identity
from rag.retrieval import study_registry

VERSION = 'ra-bounded-study-context-1'


def guideline_candidates(index, chunk, request):
    """Stored status/scope pages of the SAME document; skip dotted-leader TOCs."""
    if index is None:
        return []
    wanted = conditions(request)['facets']
    selected = []
    for candidate in index.chunks:
        if ((candidate['document_id'], candidate['version_id'], candidate['jurisdiction']) !=
                (chunk['document_id'], chunk['version_id'], chunk['jurisdiction'])
                or candidate['chunk_id'] == chunk['chunk_id']):
            continue
        status = 'document_status' in wanted and candidate['pdf_page'] == 1
        scope = ('applicability' in wanted and not re.search(r'\.{3,}', candidate['text']) and
                 re.search(r'^\s*(?:\d+(?:\.\d+)*\.?\s*)?(?:Scope|Introduction[^\n]*scope)\s*$', candidate['text'], re.I | re.M))
        if status or scope:
            selected.append(candidate)
    return sorted(selected, key=lambda c: c['pdf_page'])[:2]


def candidates(corpus, chunk, allowed_ids, request, queries, limit=2):
    identity = study_identity(chunk)
    if identity is None or chunk.get('role') == 'guideline':
        return []
    adapter = corpus.evidence_adapter
    if not hasattr(adapter, 'ra_study_chunks'):
        adapter.ra_study_chunks = defaultdict(list)
        for candidate in corpus.chunks:
            key = study_identity(candidate)
            if key is not None:
                adapter.ra_study_chunks[key].append(candidate)
    wanted = conditions(request)
    needs_dose = 'dose' in wanted['facets'] or any(q['unit'] in ('mg', 'ug', 'g') for q in wanted['quantities'])
    needs_route = 'route' in wanted['facets'] or bool(wanted['routes'])
    anchor = [a for a in chunk.get('structure', {}).get('heading_anchors', []) if a.get('kind') == 'study'][-1]
    anchor_studies = {study_registry.top(k) for k in anchor.get('study_keys', []) if k in study_registry.load()}
    query = ' '.join(queries)
    ranked = []
    for candidate in adapter.ra_study_chunks[identity]:
        rep = candidate.get('representation', {})
        if (candidate['chunk_id'] == chunk['chunk_id'] or candidate['chunk_id'] not in allowed_ids
                or candidate.get('product_id') != chunk.get('product_id')
                or candidate.get('jurisdiction') != chunk.get('jurisdiction')
                or candidate.get('doc_group') != chunk.get('doc_group')
                or candidate.get('structure', {}).get('kind') != 'text'
                or not rep.get('retrieval_eligible', True) or rep.get('description_review_required')):
            continue
        # A carried heading cannot override another literal study identifier.
        printed = {study_registry.top(k) for _, _, k in study_registry.finditer(candidate['text'], candidate['product_id'])}
        if anchor_studies and printed - anchor_studies:
            continue
        text = candidate['text']
        value = relevance(candidate, query)
        if needs_dose and re.search(r'\d\s*mg\b|\bdos(?:e|ing)\b', text, re.I): value += 8
        if needs_route and re.search(r'subcutaneous|intravenous|\bSC\b|\bIV\b', text, re.I): value += 8
        if (needs_dose or needs_route) and re.search(r'study design|single.dose|administered', text, re.I): value += 4
        if value > 0:
            ranked.append((value, candidate))
    ranked.sort(key=lambda row: (-row[0], row[1]['pdf_page'], row[1]['chunk_id']))
    return [c for _, c in ranked[:limit]]
