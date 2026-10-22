"""Source-bound visual context links. No OCR, VLM, cell repair or clinical inference."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import unicodedata

from .ingest import sha256, stable_hash

VERSION = 'visual-context-3'
SUPPORTED_VERSIONS = {'visual-context-1', 'visual-context-2', VERSION}
FILES = ('visual_context_units.jsonl', 'visual_context_links.jsonl', 'visual_context_targets.jsonl')
IDENTIFIER = r'(?:S\d+(?:[.-]\d+)*|\d+(?:[.-]\d+)*(?:[A-Z])?|[IVXLCDM]+|[A-Z])'
REFERENCE = re.compile(r'(?<![A-Za-z0-9_])(Figures?|Figs?\.?|Tables?|図|表)\s*(' + IDENTIFIER + r')(?![A-Za-z0-9_])', re.I)
TAIL = re.compile(r'\s*(,\s*(?:and\s+)?|and\s+|&\s*|、|及び|[–—]\s*)(' + IDENTIFIER + r')(?![A-Za-z0-9_])', re.I)
EXTERNAL = re.compile(r'^(?:source(?:\s+data)?|data\s+source|references?|出典|資料出所|来源)\s*[:：]', re.I)
IGNORE_LABELS = {'footer', 'page_number', 'header', 'header_image', 'reference', 'references'}


def normalized_map(text):
    result, positions = [], []
    for i, char in enumerate(text):
        part = unicodedata.normalize('NFKC', char)
        result.append(part)
        positions.extend([i] * len(part))
    return ''.join(result), positions


def references(text):
    """Recognize literal references and preserve offsets in the original string."""
    value, positions = normalized_map(text)
    result = []
    for match in REFERENCE.finditer(value):
        name, number = match[1], match[2]
        if number.isalpha() and not number.isupper():
            continue
        kind = 'figure' if name.casefold().startswith(('fig', '図')) else 'table'
        ids, end = [number.upper()], match.end()
        plural = name.casefold().rstrip('.').endswith('s')
        # Hyphenated numbers are identifiers unless the noun explicitly signals a range.
        if plural and re.fullmatch(r'\d+-\d+', number):
            lo, hi = map(int, number.split('-'))
            if not 0 < hi-lo <= 20:
                continue
            ids = [str(i) for i in range(lo, hi+1)]
        while tail := TAIL.match(value, end):
            following = tail[2]
            if following.isalpha() and not following.isupper():
                break
            if tail[1].strip() in {'–', '—'}:
                if not ids[-1].isdigit() or not following.isdigit() or not 0 < int(following)-int(ids[-1]) <= 20:
                    break
                ids.extend(str(i) for i in range(int(ids[-1])+1, int(following)+1))
            else:
                ids.append(following.upper())
            end = tail.end()
        span = [positions[match.start()], positions[end-1]+1]
        for identifier in dict.fromkeys(ids):
            result.append({'kind': kind, 'identifier': identifier, 'range': span,
                'literal': text[span[0]:span[1]]})
    return result


def sentences(text):
    """Conservative sentence windows; do not split decimals or Fig. abbreviations."""
    start = 0
    for match in re.finditer(r'[.!?。！？](?:\s+|$)', text):
        punctuation = text[match.start()]
        if punctuation == '.' and re.search(r'\b(?:fig|figs|vs|e\.g|i\.e)\.$', text[start:match.start()+1], re.I):
            continue
        end = match.start()+1
        while start < end and text[start].isspace():
            start += 1
        if start < end:
            yield start, end
        start = match.end()
    while start < len(text) and text[start].isspace():
        start += 1
    end = len(text.rstrip())
    if start < end:
        yield start, end


def text_for(asset, unit):
    native = asset.get('native_reference') or {}
    return (native['text'], 'native_source_unit', 'native') if native.get('reliable') else (
        unit['text'], 'preprocessing_representation_unit', unit['content_origin'])


def reference_sentences(text):
    """Keep an immediately trailing standalone (See Figure X) with its sentence."""
    windows = []
    for start, end in sentences(text):
        value = text[start:end]
        trailing = re.match(r'\(\s*(?:see\s+)?(?:figures?|figs?\.?|tables?|図|表)\s*[^()]+\)\s*[.!?]?', value, re.I)
        if trailing and references(trailing[0]):
            stop = start+len(trailing[0].rstrip())
            if windows:
                windows[-1][1] = stop
            else:
                windows.append([start,stop])
            tail = start+trailing.end()
            while tail < end and text[tail].isspace():
                tail += 1
            if tail < end:
                windows.append([tail,end])
        else:
            windows.append([start,end])
    for window in windows:
        prefix = re.match(r'(?:[•●▪]\s*|[-*]\s+)', text[window[0]:window[1]])
        if prefix:
            window[0] += prefix.end()
    return windows


def position(asset):
    return asset['pdf_page'], asset['bbox_pdf'][1], asset['bbox_pdf'][0], asset['asset_id']


def instruction_start(asset, text):
    value = ' '.join(text.split())
    return bool(re.match(r'^(?:instructions\s+for\s+use\b|使用説明書|使用方法説明書)', value, re.I)
        and len(value) <= 400 and (asset['block_label'] != 'header' or len(text.splitlines()) >= 3))


def toc_entry(text):
    """A literal reference plus printed dot leaders/page number, not a caption."""
    return bool(references(text) and re.search(r'(?:\.{6,}|…{3,})\s*\d+\s*(?:\n|$)', text))


def instruction_role_conflict(asset, text):
    """Recover a native bullet instruction; unmarked/real footnotes stay excluded."""
    if (asset['block_label'] != 'vision_footnote' or asset.get('logical_group_id')
            or not asset.get('native_reference', {}).get('reliable')):
        return False
    bullet = re.match(r'^\s*[•●▪◦]\s*', text)
    if not bullet:
        return False
    value = text[bullet.end():]
    return bool(len(value.split()) >= 6 and re.search(
        r'\(\s*see\s+(?:figures?|figs?\.?)\s+[^()]+\)\s*[.!]?\s*$', value, re.I)
        and references(value))


def embedded_table_notes(asset):
    """Locate marked trailing spanning note rows in reliable native text.

    Keep the HTML/grid intact. Return only exact source spans, never invented cells.
    """
    from .table_quality import TableHTML
    native = asset.get('native_reference') or {}
    if asset['asset_type'] != 'table' or not native.get('reliable'):
        return []
    parser = TableHTML()
    parser.feed(asset['selected']['content'])
    parser.close()
    if parser.errors or parser.tables != 1 or parser.table_depth or parser.cell or parser.row:
        return []
    width = max((sum(c['col_span'] for c in row) for row in parser.rows), default=0)
    if width < 2:
        return []
    result = []
    for index in range(len(parser.rows)-1, 0, -1):
        row = parser.rows[index]
        if len(row) != 1 or row[0]['is_header'] or row[0]['row_span'] != 1 or row[0]['col_span'] != width:
            break
        value = row[0]['text']
        # Paddle may render a standalone footnote symbol as LaTeX superscript.
        value = re.sub(r'^\s*\$\s*\^\{+([*†‡])\}+\s*\$\s*', r'\1 ', value)
        marked = re.match(r'^\s*([*†‡])\s*(.+)', value, re.S)
        labelled = re.match(r'^\s*(notes?|abbreviations?|source(?:\s+data)?|units?|注|出典|単位)\s*[:：]', value, re.I)
        if not marked and not labelled:
            break
        body = marked[2] if marked else value.strip()
        window = literal_window(native['text'], body)
        if window is None or len(body.split()) < 4:
            break
        start,end = window
        if marked:
            prefix = native['text'][:start]
            symbol = re.search(r'(?:^|\n)[ \t]*'+re.escape(marked[1])+r'\s*$', prefix)
            if not symbol:
                break
            start = symbol.start()+len(symbol[0])-len(symbol[0].lstrip())
            role = 'footnote'
        else:
            role = 'source' if labelled[1].casefold().startswith(('source','出典')) else (
                'unit' if labelled[1].casefold().startswith(('unit','単位')) else 'footnote')
        result.append({'range':[start,end], 'role':role, 'html_row':index})
    return list(reversed(result))


def assemble(assets, units, groups, documents=(), pages=(), version=VERSION):
    from rag.index import document_structure as ds
    from .visual_groups import caption_identity
    if version not in SUPPORTED_VERSIONS:
        raise ValueError('Unsupported visual context rules')
    current_rules = version != 'visual-context-1'
    revised_rules = version == VERSION
    assets = {a['asset_id']: a for a in assets}
    units = {u['unit_id']: u for u in units}
    meta = {(d['document_id'], d['version_id']): d.get('metadata', {}) for d in documents}
    by_doc = defaultdict(list)
    parsed = {(p['document_id'], p['version_id'], p['pdf_page']) for p in pages if p['status'] == 'parsed'} if pages else None
    for a in assets.values():
        if not a.get('is_logical_group') and (parsed is None or (a['document_id'], a['version_id'], a['pdf_page']) in parsed):
            by_doc[(a['document_id'], a['version_id'])].append(a)
    contexts, states = [], {}
    for identity, rows in sorted(by_doc.items()):
        tracker = ds.Tracker(*identity, meta.get(identity, {}).get('product_id'))
        scope, scope_basis, previous_page = None, 'document', None
        heading_ids, context_ids = [], {}
        for a in sorted(rows, key=position):
            uid, page = a['asset_id'], a['pdf_page']
            if previous_page is not None and page > previous_page+1:
                tracker = ds.Tracker(*identity, meta.get(identity, {}).get('product_id'))
                scope, scope_basis, heading_ids = 'gap:'+uid, 'partial_run_segment', []
            previous_page = page
            unit = units[uid]
            if a['asset_type'] == 'text' and not unit.get('review_required'):
                text, basis, origin = text_for(a, unit)
                if text.strip():
                    cid = 'vc'+stable_hash([uid, basis, text])[:26]
                    context_ids[uid] = cid
                    context = {'context_unit_id': cid, 'source_unit_id': uid,
                        'document_id': identity[0], 'version_id': identity[1], 'pdf_page': page,
                        'text': text, 'offset_basis': basis, 'source_origin': origin,
                        'source_refs': a['source_refs'], 'block_label': a['block_label'],
                        'numeric_evidence_eligible': False}
                    start = instruction_start(a, text)
                    is_toc = revised_rules and toc_entry(text)
                    ordinary = a['block_label'] not in IGNORE_LABELS and not is_toc
                    found = ds.identify(' '.join(text.split()), meta.get(identity, {}).get('product_id')) if ordinary else None
                    caption = references(text)
                    is_caption = bool(caption and caption[0]['range'][0] == len(text)-len(text.lstrip())
                        and (a.get('logical_group_id') or a['block_label'] in {'figure_title', 'table_title', 'caption'}))
                    if current_rules:
                        is_caption = bool(caption_identity(a))
                    if is_toc:
                        is_caption = False
                        context['context_role_hint'] = 'table_of_contents'
                    if revised_rules and instruction_role_conflict(a, text):
                        context['layout_role_conflict'] = 'native_bullet_explicit_figure_reference'
                    if start:
                        scope, scope_basis = 'ifu:'+cid, 'instruction_title'
                        heading_ids = [cid]
                        tracker = ds.Tracker(*identity, meta.get(identity, {}).get('product_id'))
                    elif ordinary and not is_caption and not (current_rules and caption
                            and caption[0]['range'][0] == len(text)-len(text.lstrip())) and (found or a['block_label'] == 'paragraph_title'
                            and len(text.strip()) <= 150 and not text.strip().endswith(('.', '?', '!'))):
                        if found:
                            tracker.push(found, page, {'block_id': uid, 'bbox_pdf': a['bbox_pdf'],
                                'line_start': 0, 'line_end': 0}, 'preprocessed', None)
                        structural = [context_ids[n['heading_location']['block_id']]
                            for n in tracker.state()['heading_anchors']]
                        instruction = [scope.removeprefix('ifu:')] if scope_basis == 'instruction_title' else []
                        heading_ids = list(dict.fromkeys([*instruction, *structural, cid]))
                    context['scope_id'] = scope
                    context['scope_basis'] = scope_basis
                    context['heading_context_ids'] = list(heading_ids)
                    context['is_heading'] = cid in heading_ids
                    if current_rules:
                        context['is_caption'] = is_caption
                    context['section_state'] = tracker.state()
                    context['usable'] = (ordinary or start) and a['block_label'] != 'native_figure_context'
                    contexts.append(context)
            states[uid] = {'scope_id': scope, 'scope_basis': scope_basis,
                'heading_context_ids': list(heading_ids), 'section_state': tracker.state()}
    context_by_source = {c['source_unit_id']: c for c in contexts}
    by_context = {c['context_unit_id']: c for c in contexts}
    targets = []
    owned = set()
    for g in groups:
        owned.update(g['member_asset_ids'])
        anchor_id = g.get('caption_asset_id') or g['member_asset_ids'][0]
        if anchor_id not in states or any(uid not in states for uid in g['member_asset_ids']):
            continue
        anchor = assets[anchor_id]
        mentions = references(g.get('label') or g.get('caption_text', ''))
        targets.append({'visual_id': g['group_id'], 'document_id': g['document_id'], 'version_id': g['version_id'],
            'kind': g['kind'], 'label': g.get('label'), 'identifier': mentions[0]['identifier'] if mentions else None,
            'caption': g.get('caption_text', ''), 'member_unit_ids': g['member_asset_ids'],
            'source_refs': g.get('source_refs') or anchor['source_refs'],
            'pdf_page': min(assets[uid]['pdf_page'] for uid in g['member_asset_ids']),
            'caption_page': anchor['pdf_page'] if g.get('caption_asset_id') else None,
            'pdf_pages': sorted({assets[uid]['pdf_page'] for uid in g['member_asset_ids']}),
            **states[anchor_id], 'numeric_evidence_eligible': False})
    for a in assets.values():
        if a['asset_type'] in {'figure', 'table'} and a['asset_id'] not in owned and a['asset_id'] in states:
            targets.append({'visual_id': a['asset_id'], 'document_id': a['document_id'], 'version_id': a['version_id'],
                'kind': a['asset_type'], 'label': None, 'identifier': None, 'caption': '',
                'member_unit_ids': [a['asset_id']], 'source_refs': a['source_refs'], 'pdf_page': a['pdf_page'],
                'caption_page': None, 'pdf_pages': [a['pdf_page']],
                **states[a['asset_id']], 'numeric_evidence_eligible': False})
    links = []
    def add(target, context, role, basis, span=None, refs=(), status='linked_candidate', candidates=(), issue=None):
        start, end = span or [0, len(context['text'])]
        row = {'schema_version': version, 'visual_id': target['visual_id'] if target else None,
            'document_id': context['document_id'], 'version_id': context['version_id'],
            'context_unit_id': context['context_unit_id'], 'source_unit_id': context['source_unit_id'],
            'context_range': [start, end], 'offset_basis': context['offset_basis'],
            'context_role': role, 'link_basis': basis, 'scope_id': context['scope_id'],
            'source_origin': context['source_origin'], 'source_refs': context['source_refs'],
            'reference_ranges': list(refs), 'panel_scope': 'whole_visual', 'link_status': status,
            'candidate_visual_ids': list(candidates), 'issue': issue,
            'semantic_status': 'unverified', 'numeric_evidence_eligible': False}
        row['link_id'] = 'vl'+stable_hash(row)[:26]
        links.append(row)
    target_by_id = {t['visual_id']: t for t in targets}
    if revised_rules:
        for target in targets:
            if target['kind'] != 'table':
                continue
            for uid in target['member_unit_ids']:
                asset = assets[uid]
                notes = embedded_table_notes(asset)
                if not notes:
                    continue
                text = asset['native_reference']['text']
                context = {'context_unit_id':'vc'+stable_hash([uid,'embedded_table_native',text])[:26],
                    'source_unit_id':uid,'document_id':asset['document_id'],'version_id':asset['version_id'],
                    'pdf_page':asset['pdf_page'],'text':text,'offset_basis':'native_source_unit','source_origin':'native',
                    'source_refs':asset['source_refs'],'block_label':asset['block_label'],
                    'numeric_evidence_eligible':False,**states[uid], 'is_heading':False,'is_caption':False,
                    'usable':False,'context_role_hint':'embedded_table_notes'}
                contexts.append(context)
                for note in notes:
                    add(target,context,note['role'],'embedded_table_row_native_span',note['range'])
                    links[-1]['embedded_html_row'] = note['html_row']
                    # Additional evidence is part of the immutable link identity.
                    links[-1]['link_id'] = 'vl'+stable_hash({k:v for k,v in links[-1].items() if k!='link_id'})[:26]
    for g in groups:
        if g['group_id'] not in target_by_id:
            continue
        target = target_by_id[g['group_id']]
        roles = {uid: 'caption' for uid in g.get('caption_part_asset_ids', [])}
        roles.update(g.get('context_roles', {}))
        for uid, role in roles.items():
            if uid in context_by_source:
                add(target, context_by_source[uid], role, 'caption_anchor' if role.startswith('caption') else 'printed_context')
    for target in targets:
        for cid in target['heading_context_ids']:
            context = by_context[cid]
            # Never carry headings across an explicit instruction reset or a missing-page gap.
            if context['scope_id'] == target['scope_id']:
                add(target, context, 'section_heading', 'section_scope')
    label_index = defaultdict(list)
    for target in targets:
        if target['identifier']:
            label_index[(target['document_id'], target['version_id'], target['kind'], target['identifier'])].append(target)
    for context in contexts:
        conflict = revised_rules and context.get('layout_role_conflict')
        if not context['usable'] or (not conflict and context['block_label'] in {'caption', 'figure_title', 'table_title', 'vision_footnote', 'footnote', 'table_footnote'}):
            continue
        asset = assets[context['source_unit_id']]
        if asset.get('logical_group_id') or context.get('is_caption') or context['is_heading'] and not references(context['text']):
            continue
        external = bool(EXTERNAL.match(context['text'].lstrip()))
        for start, end in (reference_sentences(context['text']) if current_rules else sentences(context['text'])):
            refs = [r for r in references(context['text']) if start <= r['range'][0] < end]
            by_target = defaultdict(list)
            for ref in refs:
                candidates = label_index.get((context['document_id'], context['version_id'], ref['kind'], ref['identifier']), [])
                all_candidates = candidates
                if external:
                    add(None, context, 'source', 'cited_external_visual', [start, end], [ref], 'external_reference')
                    continue
                # Repeated numbers require a known identical instruction scope; no nearest-page guessing.
                if len(candidates) > 1:
                    candidates = [t for t in candidates if context['scope_id'] is not None and t['scope_id'] == context['scope_id']]
                elif candidates and (context['scope_basis'] in {'instruction_title', 'partial_run_segment'}
                        or candidates[0]['scope_basis'] in {'instruction_title', 'partial_run_segment'}):
                    candidates = [t for t in candidates if t['scope_id'] == context['scope_id']]
                if len(candidates) == 1:
                    by_target[candidates[0]['visual_id']].append(ref)
                else:
                    add(None, context, 'referring_sentence', 'explicit_reference', [start, end], [ref],
                        'ambiguous' if len(all_candidates) > 1 else 'unresolved',
                        [t['visual_id'] for t in candidates or all_candidates],
                        'ambiguous_or_uncertain_scope_target' if all_candidates else 'missing_target')
            for tid, refs in by_target.items():
                add(target_by_id[tid], context, 'referring_sentence',
                    'explicit_reference_layout_role_conflict' if conflict else 'explicit_reference', [start, end], refs)
    links = list({row['link_id']: row for row in links}.values())
    summary = {'version': version, 'model_calls': 0, 'visual_targets': len(targets),
            'context_units': len(contexts), 'links': len(links),
            'by_role': dict(Counter(row['context_role'] for row in links)),
            'by_status': dict(Counter(row['link_status'] for row in links)),
            'numeric_evidence_eligible': 0, 'association_accuracy_measured': False}
    if current_rules:
        summary['orphan_caption_source_unit_ids'] = [c['source_unit_id'] for c in contexts
            if c.get('is_caption') and not assets[c['source_unit_id']].get('logical_group_id')]
    return {'version': version, 'units': contexts, 'links': links, 'targets': targets, 'summary': summary}


def save_bundle(out, bundle):
    from .files import save_rows, write_json
    out = Path(out)
    for name, key in zip(FILES, ('units', 'links', 'targets')):
        save_rows(out/name, bundle[key])
    write_json(out/'visual_context_summary.json', bundle['summary'])
    return {'version': bundle['version'], 'files': {name: sha256(out/name) for name in (*FILES, 'visual_context_summary.json')}}


def load_bundle(out, contract):
    from .files import read_jsonl
    out = Path(out)
    if contract.get('version') not in SUPPORTED_VERSIONS or set(contract['files']) != set((*FILES, 'visual_context_summary.json')):
        raise ValueError('Unsupported or incomplete visual context contract')
    for name, expected in contract['files'].items():
        if sha256(out/name) != expected:
            raise ValueError(f'Changed visual context artifact: {name}')
    return {'version': contract['version'], **{key: read_jsonl(out/name) for name, key in zip(FILES, ('units', 'links', 'targets'))},
        'summary': json.loads((out/'visual_context_summary.json').read_text(encoding='utf-8'))}


def validate_bundle(bundle, assets, units, groups, documents=(), pages=()):
    if bundle != assemble(assets, units, groups, documents, pages, version=bundle['version']):
        raise ValueError('Visual context differs from its source text, scope or association rules')
    return {'status': 'passed', 'links': len(bundle['links']), 'numeric_evidence_eligible': 0}


def link_text(link, bundle):
    context = next(u for u in bundle['units'] if u['context_unit_id'] == link['context_unit_id'])
    start, end = link['context_range']
    return context['text'][start:end]


def header_line(link, bundle):
    return f"[{link['context_role']}] {link_text(link, bundle)}"


def literal_window(text, needle):
    """Map a whitespace/NFKC-equivalent quote to this representation, not native offsets."""
    def indexed(value):
        chars, positions = [], []
        for i, original in enumerate(value):
            for char in unicodedata.normalize('NFKC', original):
                if char.isspace():
                    if chars and chars[-1] != ' ':
                        chars.append(' ')
                        positions.append(i)
                else:
                    chars.append(char)
                    positions.append(i)
        if chars and chars[-1] == ' ':
            chars.pop()
            positions.pop()
        return ''.join(chars), positions
    haystack, positions = indexed(text)
    value, _ = indexed(needle)
    start = haystack.find(value) if value else -1
    if start < 0:
        return None
    return [positions[start], positions[start+len(value)-1]+1]


def reverse_link_present(chunk, link, bundle):
    cursor = 0
    for span in chunk['provenance']['source_spans']:
        cursor += len(span['separator_before'])
        length = span['representation_end']-span['representation_start']
        fragment = chunk['text'][cursor:cursor+length]
        cursor += length
        if span['unit_id'] == link['source_unit_id'] and literal_window(fragment, link_text(link,bundle)) is not None:
            return True
    return False


def apply_to_chunks(chunks, bundle, counter, mode='linked', max_tokens=256):
    """Add bounded, separately sourced retrieval context; never alter chunk bodies."""
    if mode not in {'none', 'headings', 'linked'} or type(max_tokens) is not int or max_tokens < 0:
        raise ValueError('Invalid visual context indexing configuration')
    if mode == 'none':
        return {'indexed_links': 0, 'visual_chunks_enriched': 0}
    links = {l['link_id']: l for l in bundle['links'] if l['link_status'] == 'linked_candidate'}
    context_by_id = {u['context_unit_id']: u for u in bundle['units']}
    total, enriched = 0, 0
    for chunk in chunks:
        structure = chunk['structure']
        visual_id = structure.get('figure_id') or structure.get('table_id')
        if visual_id:
            own = [l for l in links.values() if l['visual_id'] == visual_id]
            allowed = {'section_heading', 'referring_sentence'} if mode == 'linked' else {'section_heading'}
            candidates = sorted((l for l in own if l['context_role'] in allowed),
                key=lambda l: (l['context_role'] != 'section_heading', l['source_refs'][0]['pdf_page'], l['link_id']))
            base, added, accepted = chunk['header'], [], []
            for link in candidates:
                text = link_text(link, bundle)
                if text.strip() in base or text.strip() in chunk['text'] or header_line(link, bundle) in added:
                    continue
                proposed = [*added, header_line(link, bundle)]
                header = base+'\n'+'\n'.join(proposed)
                if counter.one('\n'.join(proposed)) > max_tokens or counter.embedding(header, chunk['text']) > chunk['chunking']['embedding_limit']:
                    continue  # Preserve the full sentence in the sidecar; no truncation.
                added, accepted = proposed, [*accepted, link['link_id']]
            chunk['header'] = base+('\n'+'\n'.join(added) if added else '')
            chunk['n_tokens_with_header'] = counter.embedding(chunk['header'], chunk['text'])
            structure['visual_context'] = {'version': bundle['version'], 'base_header': base,
                'link_ids': [l['link_id'] for l in own], 'indexed_link_ids': accepted}
            total += len(accepted)
            enriched += bool(accepted)
        else:
            related = []
            for link in links.values():
                if link['context_role'] != 'referring_sentence':
                    continue
                if reverse_link_present(chunk, link, bundle) and (link['document_id'], link['version_id']) == (chunk['document_id'], chunk['version_id']):
                    related.append(link['link_id'])
            if related:
                structure['related_visual_link_ids'] = list(dict.fromkeys(related))
    return {'indexed_links': total, 'visual_chunks_enriched': enriched}


def verify_chunk_links(chunk, bundle):
    links = {l['link_id']: l for l in bundle['links']}
    structure = chunk['structure']
    metadata = structure.get('visual_context')
    if metadata:
        if metadata['version'] != bundle['version'] or not set(metadata['indexed_link_ids']).issubset(metadata['link_ids']):
            raise ValueError('Invalid visual context indexing metadata')
        vid = structure.get('figure_id') or structure.get('table_id')
        for lid in metadata['link_ids']:
            link = links[lid]
            if link['visual_id'] != vid or link['link_status'] != 'linked_candidate' or (
                    link['document_id'], link['version_id']) != (chunk['document_id'], chunk['version_id']):
                raise ValueError('Chunk visual context crosses identity or unresolved association')
        expected = metadata['base_header']+('' if not metadata['indexed_link_ids'] else '\n'+'\n'.join(
            header_line(links[lid], bundle) for lid in metadata['indexed_link_ids']))
        if expected != chunk['header']:
            raise ValueError('Visual search header does not reconstruct from source links')
    for lid in structure.get('related_visual_link_ids', []):
        link = links[lid]
        if link['link_status'] != 'linked_candidate' or link['context_role'] != 'referring_sentence' or (
                link['document_id'], link['version_id']) != (chunk['document_id'], chunk['version_id']) or not reverse_link_present(chunk, link, bundle):
            raise ValueError('Reverse visual link differs from its referring sentence')


def write(run, out):
    from .files import read_jsonl
    from .hybrid_validate import validate_extraction
    from .files import write_json
    run, out = Path(run).resolve(), Path(out).resolve()
    if out.exists() or out.is_relative_to(run) or run.is_relative_to(out):
        raise ValueError('Choose a new separate visual context output directory')
    state = run/'processing_status.json'
    if state.exists() and json.loads(state.read_text(encoding='utf-8'))['lifecycle'] != 'finished':
        raise ValueError('Cannot link context from a running preprocessing operation')
    validate_extraction(run)
    names = ['extraction_manifest.json', 'extraction_integrity.json', 'extracted_assets.jsonl', 'source_units.jsonl', 'extraction_pages.jsonl']
    groups = read_jsonl(run/'visual_groups.jsonl') if (run/'visual_groups.jsonl').exists() else []
    if groups:
        names.append('visual_groups.jsonl')
    hashes = {name: sha256(run/name) for name in names}
    manifest = json.loads((run/'extraction_manifest.json').read_text(encoding='utf-8'))
    bundle = assemble(read_jsonl(run/'extracted_assets.jsonl'), read_jsonl(run/'source_units.jsonl'), groups, manifest['documents'],
        read_jsonl(run/'extraction_pages.jsonl'))
    if any(sha256(run/name) != digest for name, digest in hashes.items()):
        raise ValueError('Visual context input changed during build')
    out.mkdir(parents=True)
    contract = save_bundle(out, bundle)
    write_json(out/'visual_context_manifest.json', {'version': VERSION, 'source_run': str(run),
        'input_hashes': hashes, **contract})
    return {**bundle['summary'], 'output': str(out), 'source_run_unchanged': True}
