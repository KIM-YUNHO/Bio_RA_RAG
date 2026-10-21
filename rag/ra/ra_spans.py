"""Request-time citation IDs over existing source units; no model or index build."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import re

from rag.evidence.table_evidence import cells

VERSION = 'ra-source-span-1'
GAP = '[UNSUPPLIED SOURCE GAP]'


@lru_cache(maxsize=256)
def ranges(text):
    """Keep existing paragraphs whole; reuse the HTML cell parser for tables."""
    parsed = cells(text) if re.search(r'<table\b', text, re.I) else []
    if parsed:
        cuts = sorted({0, len(text), *(c['start'] for c in parsed), *(c['end'] for c in parsed)})
        cell_ranges = {(c['start'], c['end']) for c in parsed}
        return tuple((a, b, 'table_cell' if (a, b) in cell_ranges else 'table_context')
                     for a, b in zip(cuts, cuts[1:]) if b > a)
    # Native guidelines have page-sized units; blank lines retain paragraph
    # boundaries without splitting PDF wrapped lines or compound quantities.
    cuts = [0] + [m.end() for m in re.finditer(r'\n\s*\n', text)] + [len(text)]
    return tuple((a, b, 'source_paragraph') for a, b in zip(cuts, cuts[1:]) if b > a)


def catalog(evidence):
    result, reading = {}, {}
    for key, block in evidence.items():
        text = block['text']
        spans = block.get('source_spans') or block.get('provenance', {}).get('source_spans') or []
        if block.get('role') == 'guideline' and spans and 'representation_start' not in spans[0]:
            # The existing native guideline snapshot locates a parsed page,
            # rather than a product source_unit. Adapt that verified locator;
            # never rewrite or rebuild the guideline snapshot.
            if len(spans) != 1 or spans[0].get('start') != 0 or spans[0].get('end') != len(text):
                raise ValueError('Invalid native guideline page locator')
            spans = [{'unit_id': block['chunk_id'], 'representation_start': spans[0]['start'],
                      'representation_end': spans[0]['end'], 'separator_before': ''}]
        if not spans:
            spans = [{'unit_id': block.get('chunk_id', key), 'representation_start': 0,
                      'representation_end': len(text), 'separator_before': ''}]
        cursor, segments = 0, []
        for span in spans:
            separator = span.get('separator_before', '')
            if text[cursor:cursor + len(separator)] != separator:
                raise ValueError('Citation separator differs from supplied source')
            if separator:
                segments.append({'text': separator})
            cursor += len(separator)
            start, end = span['representation_start'], span['representation_end']
            if type(start) is not int or type(end) is not int or not 0 <= start < end:
                raise ValueError('Invalid source-unit representation range')
            unit_text = text[cursor:cursor + end - start]
            if len(unit_text) != end - start:
                raise ValueError('Citation source-unit range exceeds supplied text')
            for a, b, kind in ranges(unit_text):
                quote = unit_text[a:b]
                # Wrappers/whitespace/gaps remain readable, never a fact citation.
                selectable = bool(quote.strip()) and GAP not in quote and (
                    kind != 'table_context' or bool(re.sub(r'<[^>]*>', '', quote).strip()))
                entry = {'text': quote}
                if selectable:
                    alias = f'S{len(result) + 1}'
                    identity = [VERSION, block.get('document_id'), block['version_id'], span['unit_id'],
                                start + a, start + b, hashlib.sha256(quote.encode()).hexdigest()]
                    stable = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
                    result[alias] = {'evidence': key, 'quote': quote, 'span_id': VERSION + ':' + stable,
                        'start': cursor + a, 'end': cursor + b, 'offset_basis': 'evidence_text',
                        'unit_id': span['unit_id'], 'representation_start': start + a,
                        'representation_end': start + b, 'document_id': block.get('document_id'),
                        'version_id': block['version_id'], 'pdf_page': block.get('pdf_page'), 'kind': kind}
                    entry['id'] = alias
                segments.append(entry)
            cursor += end - start
        if cursor != len(text) or ''.join(s['text'] for s in segments) != text:
            raise ValueError('Citation segments must preserve the entire supplied source')
        reading[key] = segments
    return result, reading


def resolve(raw, evidence, spans):
    """Materialize individual original quotes; never concatenate separate spans."""
    result = deepcopy(raw)
    if not isinstance(result, dict) or not isinstance(result.get('claims'), list):
        return result
    for claim in result['claims']:
        if not isinstance(claim, dict):
            continue
        selected = claim.pop('citation_span_ids', None)
        claim['sources'] = []
        if (not isinstance(selected, list) or not selected or len(selected) > 40
                or any(not isinstance(s, str) or s not in spans for s in selected)):
            claim['citation_error'] = 'unknown_or_invalid_citation_span'
            continue
        for alias in dict.fromkeys(selected):
            span = spans[alias]
            block = evidence.get(span['evidence'])
            if (block is None or block['version_id'] != span['version_id']
                    or block.get('document_id') != span['document_id']
                    or block['text'][span['start']:span['end']] != span['quote']):
                raise ValueError('Selected citation no longer matches its source version')
            claim['sources'].append({'evidence': span['evidence'], 'quote': span['quote'],
                'citation_span_id': span['span_id'], 'citation_alias': alias,
                'source_range': [span['start'], span['end']], 'offset_basis': span['offset_basis'],
                'unit_id': span['unit_id'], 'representation_range': [
                    span['representation_start'], span['representation_end']]})
    return result
