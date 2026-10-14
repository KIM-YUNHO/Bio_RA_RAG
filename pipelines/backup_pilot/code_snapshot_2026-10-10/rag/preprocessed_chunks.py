"""Adapt eligible upload representations to the existing exact-budget chunker."""
from collections import Counter as Counts, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_chunks as bc
import document_structure as ds
from chunk_schema import profile_id, validate_dataset
from chunking import Fragment, Packed, pack_units
from preprocess.hybrid import read_jsonl, save_rows
from preprocess.hybrid_validate import validate_extraction
from preprocess.ingest import sha256, stable_hash, INTAKE_METADATA_FIELDS, document_date
from preprocess.inventory import write_json

VERSION = 'preprocessed-chunks-12'
CONFIG = {**bc.CONFIG, 'config_version': VERSION, 'structure_version': ds.VERSION, 'section_rules_version': ds.VERSION, 'schema_version': 'rag-chunk-3', 'figure_max_tokens': 2048,
          'visual_context_mode': 'linked', 'visual_context_max_tokens': 256}


def furniture_units(units, assets, pages, cfg):
    """Exclude marginal page numbers and recurrent labeled running furniture.

    Keep source units and an explicit exclusion record. Captions/table notes and
    body-position text are never removed on the strength of their wording alone.
    """
    from preprocess.visual_groups import display_box
    counts, candidates, excluded = defaultdict(set), {}, set()
    doc_pages = Counts((d, v) for d, v, _ in pages)
    for unit in units:
        a = assets[unit['unit_id']]
        if unit['kind'] != 'text' or unit.get('structure', {}).get('logical_group_id'):
            continue
        box = display_box(a)
        edge = 'top' if box[3] <= 120 else 'bottom' if box[1] >= 880 else None
        if edge is None:
            continue
        label, text = a['block_label'], unit['text'].strip()
        if label in {'number', 'page_number', 'header', 'footer'} and re.fullmatch(
                r'(?:page\s+)?\d+(?:\s*(?:of|/)\s*\d+)?', text, re.I):
            excluded.add(unit['unit_id'])
        elif label in {'header', 'footer'} and text:
            key = (unit['document_id'], unit['version_id'], edge, bc.mask_digits(bc.norm_for_hash(text)))
            counts[key].add(a['pdf_page'])
            candidates[unit['unit_id']] = key
    excluded.update(uid for uid, key in candidates.items()
        if len(counts[key]) >= cfg['boilerplate_min_pages']
        and len(counts[key])/doc_pages[key[:2]] >= cfg['boilerplate_page_share'])
    return excluded


def load_input(run):
    run = Path(run).resolve()
    manifest = json.loads((run/'extraction_manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') not in {'partial', 'extraction_complete'}:
        raise ValueError('Preprocessing is not at a completed checkpoint')
    progress = run/'processing_status.json'
    if progress.exists() and json.loads(progress.read_text(encoding='utf-8'))['lifecycle'] != 'finished':
        raise ValueError('Cannot chunk a running or interrupted preprocessing operation')
    validate_extraction(run)
    names = ['extraction_manifest.json', 'extraction_integrity.json', 'source_units.jsonl',
             'extracted_assets.jsonl', 'extraction_pages.jsonl']
    if manifest.get('grouping_version'):
        names.append('visual_groups.jsonl')
    integrity = json.loads((run/'extraction_integrity.json').read_text(encoding='utf-8'))
    names.extend((integrity.get('visual_context') or {}).get('files', {}))
    hashes = {name: sha256(run/name) for name in names}
    units = read_jsonl(run/'source_units.jsonl')
    assets = {a['asset_id']: a for a in read_jsonl(run/'extracted_assets.jsonl')}
    pages = {(p['document_id'], p['version_id'], p['pdf_page']): p for p in read_jsonl(run/'extraction_pages.jsonl')}
    groups = read_jsonl(run/'visual_groups.jsonl') if manifest.get('grouping_version') else []
    return run, manifest, hashes, units, assets, pages, groups


def reasons(unit, asset, page):
    problems = []
    if page['status'] != 'parsed':
        problems.append('parent_page_incomplete')
    if unit.get('retrieval_eligible') is not True:
        problems.append('retrieval_excluded_by_preprocessing')
    if unit.get('review_required'):
        problems.append('representation_requires_review')
    if unit.get('retrieval_role') == 'logical_figure_member':
        problems.append('represented_by_logical_figure_parent')
    if asset.get('continuation_status') == 'data_fragment_continuation_candidate':
        problems.append('continuation_semantics_require_review')
    if not unit['text'].strip():
        problems.append('empty_representation')
    return problems


class DeferredTable(ValueError):
    pass


def table_parts(text, counter, limit, header, embedding_limit):
    def fits(value):
        return counter.one(value) <= limit and counter.embedding(header, value) <= embedding_limit
    if fits(text):
        return [Packed([Fragment(text, 0)], 'table_whole')]
    # Splitting merged geometry can change cell meaning. Keep the complete original
    # if it fits; otherwise defer rather than fragment a spanning cell or data row.
    if any(int(value) > 1 for value in re.findall(r'\b(?:rowspan|colspan)\s*=\s*["\']?(\d+)', text, re.I)):
        raise DeferredTable('oversized_table_with_merged_cells')
    if len(re.findall(r'<table\b', text, re.I)) != 1 or re.search(r'<(?:thead|tbody|tfoot)\b', text, re.I):
        raise DeferredTable('unsupported_table_wrappers_for_split')
    matches = list(re.finditer(r'<tr\b[^>]*>.*?</tr\s*>', text, re.I | re.S))
    first = next((i for i, row in enumerate(matches) if re.search(r'<td\b', row.group(), re.I)), None)
    if first is None:
        raise DeferredTable('oversized_table_without_body_rows')
    prefix, suffix = text[:matches[first].start()], text[matches[-1].end():]
    pieces = [Fragment(text[m.start():matches[i+1].start() if i+1 < len(matches) else m.end()], 0, m.start())
              for i, m in enumerate(matches) if i >= first]
    parts, batch = [], []
    for piece in pieces:
        if not fits(prefix + piece.text + suffix):
            raise DeferredTable('oversized_table_row_or_header')
        if batch and not fits(prefix + ''.join(p.text for p in batch+[piece]) + suffix):
            parts.append(batch)
            batch = []
        batch.append(piece)
    if batch:
        parts.append(batch)
    return [Packed(([Fragment(prefix, 0, 0)] if prefix else []) + batch +
        ([Fragment(suffix, 0, matches[-1].end())] if suffix else []), 'table_rows') for batch in parts]


def table_bundle(first, lookup, assets, pages, counter, limit, header, embedding_limit):
    structure = first.get('structure', {})
    context = []
    for uid in structure.get('context_unit_ids', []):
        unit = lookup.get(uid)
        if (unit is None or unit['kind'] != 'text'
                or (unit['document_id'], unit['version_id']) != (first['document_id'], first['version_id'])
                or unit.get('structure', {}).get('table_id') != structure.get('table_id')
                or reasons(unit, assets[uid], pages[(unit['document_id'], unit['version_id'], assets[uid]['pdf_page'])])):
            raise DeferredTable('table_context_unavailable')
        context.append(unit)
    roles = structure.get('context_roles', {})
    before = [u for u in context if roles.get(u['unit_id'], 'caption') in {'caption', 'caption_continuation', 'unit'}]
    after = [u for u in context if u not in before]
    selected = [*before, first, *after]
    body_index = len(before)
    reserve = counter.one('\n'.join(u['text'] for u in context)) + len(context) + 8 if context else 0
    if limit-reserve < 1:
        raise DeferredTable('table_context_exceeds_token_budget')
    body_parts = table_parts(first['text'], counter, limit-reserve, header, embedding_limit)
    parts = []
    for body in body_parts:
        fragments = [Fragment(u['text'], i) for i, u in enumerate(before)]
        fragments += [Fragment(f.text, body_index, f.start, f.hard_split) for f in body.fragments]
        fragments += [Fragment(u['text'], body_index+1+i) for i, u in enumerate(after)]
        # Concatenate table row fragments directly and separate physical context units.
        value = ''.join(('\n' if i and (f.uid != body_index or fragments[i-1].uid != body_index) else '')+f.text
                        for i, f in enumerate(fragments))
        if counter.one(value) > limit or counter.embedding(header, value) > embedding_limit:
            raise DeferredTable('table_context_exceeds_token_budget')
        parts.append(Packed(fragments, body.reason))
    return selected, parts


def build(run, counter, cfg=None, *, _loaded=None):
    requested=Path(run).resolve()
    run, manifest, hashes, units, assets, pages, groups = _loaded or load_input(run)
    if run!=requested:raise ValueError('Validated input belongs to another preprocessing run')
    cfg = {**CONFIG, **(cfg or {})}
    from preprocess.visual_context import assemble, apply_to_chunks
    context_bundle = assemble(list(assets.values()), units, groups, manifest['documents'], list(pages.values()))
    furniture = furniture_units(units, assets, pages, cfg)
    if not (0 <= cfg['overlap_tokens'] < cfg['target_tokens'] <= cfg['max_tokens'] < cfg['embedding_max_tokens']
            and 0 < cfg['table_max_tokens'] < cfg['embedding_max_tokens']
            and 0 < cfg['figure_max_tokens'] < cfg['embedding_max_tokens']):
        raise ValueError('Invalid chunk token budgets')
    tok = getattr(counter, 'tok', None)
    backend = getattr(tok, 'backend_tokenizer', None)
    cfg['tokenizer_sha256'] = hashlib.sha256(backend.to_str().encode()).hexdigest() if backend else None
    cfg['preprocessing_inputs_sha256'] = stable_hash(hashes)
    pid = profile_id(cfg)
    by_unit = {u['unit_id']: u for u in units}
    doc_groups = json.loads((bc.ROOT/'rag/doc_groups.json').read_text(encoding='utf-8'))
    chunks, exclusions = [], []
    unit_index = {u['unit_id']: i for i, u in enumerate(units)}
    page_positions = {}
    for key in pages:
        physical = [u for u in units if not assets[u['unit_id']].get('is_logical_group') and
            (u['document_id'], u['version_id'], assets[u['unit_id']]['pdf_page']) == key]
        page_positions.update({u['unit_id']: i for i, u in enumerate(physical)})
    def reading_order(u):
        a = assets[u['unit_id']]
        order = a.get('block_order')
        if not isinstance(order, (int, float)):
            members = a.get('member_asset_ids') or [u['unit_id']]
            order = min(page_positions.get(uid, unit_index[uid]) for uid in members)
        return a['pdf_page'], order, u['unit_id']

    def exclude(unit, why):
        exclusions.append({'unit_id': unit['unit_id'], 'document_id': unit['document_id'],
            'version_id': unit['version_id'], 'source_refs': unit['source_refs'], 'reasons': why})

    for doc in manifest['documents']:
        meta = doc['metadata']
        source, product = meta.get('source') or 'unknown', meta.get('product_id') or 'unknown'
        base = {'document_id': doc['document_id'], 'version_id': doc['version_id'], 'product_id': product,
            'source': source, 'role': meta.get('role') or 'unknown', 'document_type': meta.get('document_type') or 'unknown',
            'doc_group': doc_groups['doc_group'].get(source, {}).get(meta.get('document_type'), 'unknown'),
            'jurisdiction': doc_groups['jurisdiction'].get(source, 'unknown'),
            'language': meta.get('language') or 'unknown', 'title': meta.get('title') or Path(doc['raw_path']).name,
            'source_url': meta.get('final_url') or meta.get('url'), 'doc_date': document_date(meta), 'schema_version': 'rag-chunk-3',
            'studies': [], 'study_label': None, 'study_basis': 'not_assigned', 'study_scope': 'unknown',
            'study_attribution_status': 'heuristic_unverified', 'source_kind': 'preprocessed', 'locator_type': 'html_section' if doc['kind']=='html' else 'pdf_page',
            'region': None, 'ocr_status': None,
            'analysis_context': {'status': 'unresolved', **{k: None for k in ('analysis_id', 'population', 'comparison', 'endpoint', 'timepoint')}}}
        intake_meta = {key: meta[key] for key in INTAKE_METADATA_FIELDS if key in meta}
        if intake_meta:
            base['document_metadata'] = intake_meta
        own = sorted([u for u in units if (u['document_id'], u['version_id']) == (doc['document_id'], doc['version_id'])],
            key=reading_order)
        tracker, last_page = ds.Tracker(doc['document_id'], doc['version_id'], meta.get('product_id'), version=ds.VERSION), None
        for number in sorted({assets[u['unit_id']]['pdf_page'] for u in own}):
            if last_page is not None and number != last_page + 1:
                tracker = ds.Tracker(doc['document_id'], doc['version_id'], meta.get('product_id'), version=ds.VERSION)
            last_page = number
            page = pages[(doc['document_id'], doc['version_id'], number)]
            eligible = []
            for u in [u for u in own if assets[u['unit_id']]['pdf_page'] == number]:
                why = reasons(u, assets[u['unit_id']], page)
                if u['unit_id'] in furniture:
                    why.append('repeated_page_furniture')
                if why:
                    exclude(u, why)
                else:
                    if u['kind'] == 'text' and not u.get('structure', {}).get('table_id'):
                        eligible.extend({**u, 'text':u['text'][start:end], 'source_offset':start}
                            for start,end in ds.source_segments(u['text'],product))
                    else:
                        eligible.append(u)
            # Only prose can create section boundaries; axis labels and table cells cannot.
            prose = [u for u in eligible if u['kind'] == 'text' and not u.get('structure', {}).get('table_id')]
            refs = [{'block_id': u['unit_id'], 'bbox_pdf': assets[u['unit_id']]['bbox_pdf'],
                'line_start': by_unit[u['unit_id']]['text'][:u.get('source_offset',0)].count('\n'),
                'line_end': by_unit[u['unit_id']]['text'][:u.get('source_offset',0)+len(u['text'])].count('\n'),
                'representation_start':u.get('source_offset',0),
                'representation_end':u.get('source_offset',0)+len(u['text']), 'joiner': '\n'} for u in prose]
            page_start = tracker.state()
            _, _, _, states = tracker.scan([u['text'] for u in prose], refs, number, 'preprocessed')
            state_by_id = dict(zip([(u['unit_id'],u.get('source_offset',0)) for u in prose], states))
            active, batches, batch = page_start, [], []
            for u in eligible:
                active = state_by_id.get((u['unit_id'],u.get('source_offset',0)), active)
                visual = u['kind'] in {'figure', 'figure_group', 'table'}
                boundary = batch and (visual or batch[-1][0]['kind'] in {'figure', 'figure_group', 'table'}
                    or batch[-1][1]['section_id'] != active['section_id']
                    or batch[-1][0].get('structure', {}).get('table_id') != u.get('structure', {}).get('table_id'))
                # Consecutive ancestor headings belong with the descendant body.
                # Sibling sections and distinct study headings still split.
                heading_prefix = (boundary and not visual
                    and batch[-1][1]['section_id'] in active['ancestor_section_ids']
                    and all(item['kind'] == 'text' and ds.identify(item['text'], product, version=ds.VERSION)
                            and not item.get('structure', {}).get('table_id') for item, _ in batch))
                if heading_prefix:
                    batch = [(item, active) for item, _ in batch]
                    boundary = False
                if boundary:
                    batches.append(batch)
                    batch = []
                batch.append((u, active))
                if visual:
                    batches.append(batch)
                    batch = []
            if batch:
                batches.append(batch)
            for batch in batches:
                selected = [u for u, _ in batch]
                state = batch[0][1]
                first = selected[0]
                structure = first.get('structure', {})
                kind = 'table_candidate' if first['kind'] == 'table' else 'figure_candidate' if first['kind'] in {'figure', 'figure_group'} else 'text'
                caption = structure.get('caption')
                # Table bundles already contain the exact caption source units.
                # Repeating it in the embedding header overweights its wording.
                header_caption = caption if kind == 'figure_candidate' or kind == 'table_candidate' and not structure.get('context_unit_ids') else None
                header = ' | '.join(x for x in (doc_groups['product_label'].get(product), source if source != 'unknown' else None,
                    base['title'], ' > '.join(state['path']), header_caption, f'p.{number}') if x)
                body_max = cfg['table_max_tokens'] if kind == 'table_candidate' else cfg['figure_max_tokens'] if kind == 'figure_candidate' else cfg['max_tokens']
                limit = min(body_max, cfg['embedding_max_tokens'] - counter.embedding(header, '') - 8)
                if limit < 1:
                    for u in selected:
                        exclude(u, ['metadata_header_exceeds_embedding_budget'])
                    continue
                try:
                    if kind == 'table_candidate':
                        selected, parts = table_bundle(first, by_unit, assets, pages, counter, limit, header, cfg['embedding_max_tokens'])
                    else:
                        parts = pack_units([u['text'] for u in selected], counter, limit,
                            limit if kind != 'text' else min(cfg['target_tokens'], limit), cfg['overlap_tokens'] if kind == 'text' else 0)
                except DeferredTable as exc:
                    exclude(first, [str(exc)])
                    continue
                parent = structure.get('figure_id') if kind == 'figure_candidate' else structure.get('table_id') if kind == 'table_candidate' else None
                parent = parent or f"{doc['document_id']}:{doc['version_id']}:p{number}:{stable_hash([(u['unit_id'],u.get('source_offset',0)) for u in selected])[:16]}"
                for i, part in enumerate(parts):
                    spans, source_refs = [], []
                    body_fragments = [f for f in part.fragments if selected[f.uid]['kind'] == 'table']
                    for j, fragment in enumerate(part.fragments):
                        u = selected[fragment.uid]
                        repeated = kind == 'table_candidate' and (u['kind'] != 'table' or part.reason == 'table_rows'
                            and (fragment is body_fragments[0] or fragment is body_fragments[-1]))
                        spans.append({'unit_index': unit_index[u['unit_id']], 'unit_id': u['unit_id'],
                            'representation_start': u.get('source_offset',0)+fragment.start,
                            'representation_end': u.get('source_offset',0)+fragment.end,
                            'separator_before': '\n' if j and (kind != 'table_candidate' or u['kind'] != 'table'
                                or selected[part.fragments[j-1].uid]['kind'] != 'table')
                                and selected[part.fragments[j-1].uid]['unit_id'] != u['unit_id'] else '',
                            'role': 'repeated_context' if repeated else 'content'})
                        for ref in u['source_refs']:
                            if ref not in source_refs:
                                source_refs.append(ref)
                    used = list(dict.fromkeys(s['unit_id'] for s in spans))
                    text = ''.join(s['separator_before'] + by_unit[s['unit_id']]['text'][s['representation_start']:s['representation_end']] for s in spans)
                    warnings = ['model_representation_unverified']
                    if any(f.hard_split for f in part.fragments):
                        warnings.append('forced_character_split')
                    description_review = any(by_unit[u].get('description_review_required') for u in used)
                    if description_review:
                        has_labels = any(by_unit[u].get('figure_labels', {}).get('accepted_fields') for u in used)
                        warnings.append('literal_figure_labels_roles_unverified' if has_labels else 'caption_only_figure_description_requires_review')
                    if kind == 'table_candidate' and structure.get('context_unit_ids'):
                        warnings.append('table_context_association_unverified')
                    # Several physical regions may share one logical table parent
                    # on a page. Their local part indices each start at zero.
                    chunks.append({**base, 'chunk_id': f"{doc['document_id']}:{doc['version_id']}:{pid}:{number:04d}:p{stable_hash([parent,first['unit_id'],i])[:18]}",
                        'pdf_page': number, 'pdf_page_label': ('HTML 렌더 p.' if doc['kind']=='html' else '')+str(number), 'section': ' > '.join(state['path']) or None,
                        'header': header, 'text': text, 'n_tokens': counter.one(text), 'n_tokens_with_header': counter.embedding(header, text),
                        'redaction_markers': len(bc.REDACTION.findall(text)), 'content_hash': bc.md5(bc.norm_for_hash(text)),
                        'page_hash': stable_hash(page),
                        'provenance': {'parsed_path': str(run/'source_units.jsonl'), 'parsed_sha256': hashes['source_units.jsonl'],
                            'parser': 'PaddleOCR-VL-1.6 + selective local Qwen', 'parser_version': manifest['pipeline_version'],
                            'offset_basis': 'preprocessing_representation_unit', 'source_spans': spans,
                            'source_refs': source_refs, 'preprocessing_run': str(run)},
                        'structure': {'kind': kind, 'section_path': state['path'], 'parent_id': parent,
                            **{k: state[k] for k in ('section_id', 'ancestor_section_ids', 'heading_studies', 'heading_anchors')},
                            'previous_chunk_id': None, 'next_chunk_id': None, 'caption': caption,
                            'table_id': structure.get('table_id') or (first['unit_id'] if kind == 'table_candidate' else None),
                            'figure_id': structure.get('figure_id') or (first['unit_id'] if kind == 'figure_candidate' else None),
                            'logical_group_id': structure.get('logical_group_id'),
                            'member_unit_ids': structure.get('member_unit_ids', []), 'context_unit_ids': structure.get('context_unit_ids', []),
                            'context_roles': structure.get('context_roles', {}),
                            **({'figure_labels': first['figure_labels']} if first.get('figure_labels') else {}),
                            'association_status': structure.get('association_status'), 'continuation_status': structure.get('continuation_status'),
                            'part_index': i, 'part_count': len(parts), 'repeated_context': bool(i and kind == 'table_candidate')},
                        'quality': {'evidence_status': 'unreviewed', 'table_status': 'model_structure_unverified' if kind == 'table_candidate' else None, 'warnings': warnings},
                        'representation': {'unit_ids': used, 'origins': {uid: by_unit[uid]['content_origin'] for uid in used},
                            'retrieval_eligible': True, 'numeric_evidence_eligible': False, 'semantic_status': 'unverified',
                            'description_review_required': description_review},
                        'chunking': {'config_version': cfg['config_version'], 'profile_id': pid, 'tokenizer': cfg['token_counter'],
                            'body_limit': limit, 'embedding_limit': cfg['embedding_max_tokens'], 'split_reason': part.reason, 'overlap_tokens': part.overlap_tokens}})
                own_parts = chunks[-len(parts):]
                for j, c in enumerate(own_parts):
                    c['structure']['previous_chunk_id'] = own_parts[j-1]['chunk_id'] if j else None
                    c['structure']['next_chunk_id'] = own_parts[j+1]['chunk_id'] if j+1 < len(own_parts) else None
    bundled_context = {uid for c in chunks if c['structure']['kind'] == 'table_candidate'
                       for uid in c['structure']['context_unit_ids']}
    before_suppression = len(chunks)
    # Context is indexed with its successful table. If that table is deferred,
    # its standalone printed text remains available; no evidence is discarded.
    chunks = [c for c in chunks if not (c['structure']['kind'] == 'text'
        and set(c['representation']['unit_ids']).issubset(bundled_context))]
    suppressed = before_suppression-len(chunks)
    context_summary = apply_to_chunks(chunks, context_bundle, counter, cfg['visual_context_mode'], cfg['visual_context_max_tokens'])
    from rag.preprocessed_studies import annotate_all
    chunks, study_summary = annotate_all(chunks, units)
    validate_dataset(chunks, counter)
    verification = verify_ranges(chunks, units, assets, pages, context_bundle=context_bundle, excluded_unit_ids=furniture)
    if any(sha256(run/name) != digest for name, digest in hashes.items()):
        raise ValueError('Preprocessing input changed during chunk build')
    summary = {'config': cfg, 'profile_id': pid, 'input_run': str(run), 'input_hashes': hashes,
        'chunks': len(chunks), 'standalone_table_context_chunks_suppressed': suppressed,
        'input_units': len(units), 'included_units': len({s['unit_id'] for c in chunks for s in c['provenance']['source_spans']}),
        'excluded_units': len(exclusions), 'by_content_kind': dict(Counts(c['structure']['kind'] for c in chunks)),
        'by_source': dict(Counts(c['source'] for c in chunks)), 'source_verification': verification,
        'tokens': bc.quantiles(c['n_tokens'] for c in chunks), 'embedding_tokens': bc.quantiles(c['n_tokens_with_header'] for c in chunks),
        'numeric_evidence_eligible': 0, 'active_index_updated': False, 'visual_context': context_summary,
        'study_candidates': study_summary,
        'schema_validation': 'passed', 'exclusion_reasons': dict(Counts(r for e in exclusions for r in e['reasons']))}
    return chunks, summary, exclusions


def verify_ranges(chunks, units, assets, pages, require_full_coverage=True, context_bundle=None, excluded_unit_ids=()):
    lookup = {u['unit_id']: u for u in units}
    indices = {u['unit_id']: i for i, u in enumerate(units)}
    coverage, spans_count = defaultdict(list), 0
    for c in chunks:
        reconstructed, expected_refs = '', []
        if c['structure']['kind'] == 'table_candidate' and c['chunking']['config_version'] in {'preprocessed-chunks-3', 'preprocessed-chunks-4', 'preprocessed-chunks-5', 'preprocessed-chunks-6', 'preprocessed-chunks-7', 'preprocessed-chunks-8', 'preprocessed-chunks-9', 'preprocessed-chunks-10', 'preprocessed-chunks-11', VERSION}:
            body_ids = {s['unit_id'] for s in c['provenance']['source_spans'] if lookup[s['unit_id']]['kind'] == 'table'}
            if len(body_ids) != 1:
                raise ValueError('Table bundle must preserve one source table body')
            body_structure = lookup[next(iter(body_ids))].get('structure', {})
            context_ids = body_structure.get('context_unit_ids', [])
            if (c['structure']['context_unit_ids'] != context_ids
                    or c['structure'].get('context_roles', {}) != body_structure.get('context_roles', {})):
                raise ValueError('Table bundle context differs from its source association')
            for uid in context_ids:
                own_spans = [s for s in c['provenance']['source_spans'] if s['unit_id'] == uid]
                if (len(own_spans) != 1 or own_spans[0]['role'] != 'repeated_context'
                        or own_spans[0]['representation_start'] != 0
                        or own_spans[0]['representation_end'] != len(lookup[uid]['text'])):
                    raise ValueError('Table bundle lost or truncated printed context')
        for span in c['provenance']['source_spans']:
            u = lookup[span['unit_id']]
            if u['unit_id'] in excluded_unit_ids:
                raise ValueError('Chunk includes excluded page furniture')
            a = assets[u['unit_id']]
            if reasons(u, a, pages[(u['document_id'], u['version_id'], a['pdf_page'])]):
                raise ValueError('Chunk includes an excluded or incomplete source unit')
            table_context = (c['structure']['kind'] == 'table_candidate'
                and u['unit_id'] in c['structure']['context_unit_ids'] and u['kind'] == 'text'
                and u.get('structure', {}).get('table_id') == c['structure']['table_id']
                and span['role'] == 'repeated_context' and span['representation_start'] == 0
                and span['representation_end'] == len(u['text']))
            if ((u['document_id'], u['version_id']) != (c['document_id'], c['version_id'])
                    or a['pdf_page'] != c['pdf_page'] and not table_context):
                raise ValueError('Chunk span crosses a source/page boundary')
            start, end = span['representation_start'], span['representation_end']
            if not 0 <= start < end <= len(u['text']):
                raise ValueError('Invalid representation character range')
            if span['unit_index'] != indices[u['unit_id']]:
                raise ValueError('Representation row index differs from its unit identity')
            for ref in u['source_refs']:
                if ref not in expected_refs:
                    expected_refs.append(ref)
            reconstructed += span['separator_before'] + u['text'][start:end]
            coverage[u['unit_id']].append((start, end))
            spans_count += 1
        if reconstructed != c['text']:
            raise ValueError('Chunk text does not reconstruct from representation spans')
        if c['structure'].get('visual_context') or c['structure'].get('related_visual_link_ids'):
            from preprocess.visual_context import assemble, verify_chunk_links
            if context_bundle is None:
                source = Path(c['provenance']['preprocessing_run'])
                manifest = json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
                context_bundle = assemble(list(assets.values()), units,
                    read_jsonl(source/'visual_groups.jsonl') if (source/'visual_groups.jsonl').exists() else [], manifest['documents'], list(pages.values()))
            verify_chunk_links(c, context_bundle)
        used = list(dict.fromkeys(s['unit_id'] for s in c['provenance']['source_spans']))
        if (c['representation']['unit_ids'] != used or c['representation']['origins'] != {uid: lookup[uid]['content_origin'] for uid in used}
                or c['provenance']['source_refs'] != expected_refs
                or c['representation']['description_review_required'] != any(bool(lookup[uid].get('description_review_required')) for uid in used)):
            raise ValueError('Chunk origin/review/source metadata differs from its input representation')
        if c['structure'].get('figure_labels'):
            labels = [lookup[uid].get('figure_labels') for uid in used if lookup[uid].get('figure_labels')]
            if len(labels) != 1 or c['structure']['figure_labels'] != labels[0]:
                raise ValueError('Chunk figure labels differ from their input representation')
    for uid, ranges in coverage.items() if require_full_coverage else []:
        cursor = 0
        for start, end in sorted(ranges):
            if lookup[uid]['text'][cursor:start].strip():
                raise ValueError('Non-whitespace representation content was lost')
            cursor = max(cursor, end)
        if lookup[uid]['text'][cursor:].strip():
            raise ValueError('Representation tail was lost')
    return {'status': 'passed', 'spans_reconstructed': spans_count, 'units_covered': len(coverage), 'offset_basis': 'preprocessing_representation_unit'}


def materialize(chunks, root=bc.ROOT, *, _loaded=None):
    """The existing context writer dispatches here for the new representation basis."""
    if not chunks:
        return {}, {}, {}
    run, manifest, hashes, input_units, assets, pages, groups = _loaded or load_input(chunks[0]['provenance']['preprocessing_run'])
    if any(c['provenance']['preprocessing_run'] != str(run) or c['provenance']['parsed_sha256'] != hashes['source_units.jsonl'] for c in chunks):
        raise ValueError('Chunks reference different or changed preprocessing sources')
    verify_ranges(chunks, input_units, assets, pages, require_full_coverage=False)
    lookup = {u['unit_id']: u for u in input_units}
    units, parents, membership = {}, {}, {}
    for c in chunks:
        s, context_ids = c['structure'], []
        for anchor in s['heading_anchors']:
            parents.setdefault(anchor['section_id'], {'context_id': anchor['section_id'], **anchor,
                'document_id': c['document_id'], 'version_id': c['version_id'], 'product_id': c['product_id'],
                'kind': 'section', 'heading_kind': anchor['kind'], 'status': 'structural_candidate', 'unit_ids': [], 'chunk_ids': []})
            context_ids.append(anchor['section_id'])
        visual_id = s['figure_id'] or s['table_id']
        parent = visual_id or s['section_id'] or f"{c['document_id']}:{c['version_id']}:p{c['pdf_page']}:unheaded"
        parents.setdefault(parent, {'context_id': parent, 'document_id': c['document_id'], 'version_id': c['version_id'],
            'product_id': c['product_id'], 'kind': 'figure' if s['figure_id'] else 'table' if s['table_id'] else 'page',
            'title': s['caption'] or '', 'parent_section_id': s['section_id'] if visual_id else None,
            'status': 'representation_unverified', 'member_unit_ids': s['member_unit_ids'],
            'association_status': s['association_status'], 'unit_ids': [], 'chunk_ids': []})
        context_ids = list(dict.fromkeys(context_ids+[parent]))
        ranges = []
        for span in c['provenance']['source_spans']:
            uid = span['unit_id']
            u = lookup[uid]
            units.setdefault(uid, {**u, 'product_id': c['product_id'], 'pdf_page': assets[uid]['pdf_page'], 'source_kind': 'preprocessed',
                'region': None, 'section_id': s['section_id'], 'heading_studies': s['heading_studies'],
                'source_span': {'unit_id': uid, 'representation_start': 0, 'representation_end': len(u['text']),
                    'offset_basis': 'preprocessing_representation_unit'}})
            ranges.append({'unit_id': uid, 'start': span['representation_start'], 'end': span['representation_end']})
            for cid in context_ids:
                parents[cid]['unit_ids'].append(uid)
        membership[c['chunk_id']] = {'chunk_id': c['chunk_id'], 'context_ids': context_ids, 'source_ranges': ranges}
        for cid in context_ids:
            parents[cid]['chunk_ids'].append(c['chunk_id'])
    for p in parents.values():
        p['unit_ids'] = list(dict.fromkeys(p['unit_ids']))
        if p['kind'] == 'table':
            group = next((g for g in groups if g['group_id'] == p['context_id']), None)
            order = [*group['caption_part_asset_ids'], *group['member_asset_ids'],
                     *[uid for uid in group.get('context_asset_ids', []) if uid not in group['caption_part_asset_ids']]] if group else p['unit_ids']
            p['unit_ids'].sort(key=lambda uid: order.index(uid) if uid in order else len(order))
        p['chunk_ids'] = list(dict.fromkeys(p['chunk_ids']))
        pnums = [units[u]['pdf_page'] for u in p['unit_ids']]
        p['page_from'], p['page_to'] = min(pnums), max(pnums)
    return units, parents, membership


def write(run, out, counter=None, cfg=None):
    out, run = Path(out).resolve(), Path(run).resolve()
    if out == bc.ROOT/'data/rag' or out == run or out.is_relative_to(run) or run.is_relative_to(out):
        raise ValueError('Choose a separate candidate output directory')
    counter = counter or bc.Counter((cfg or CONFIG)['token_counter'], local_only=True)
    loaded=load_input(run)
    chunks, summary, exclusions = build(run, counter, cfg, _loaded=loaded)
    if not chunks:
        raise ValueError('No eligible chunks; resolve preprocessing exclusions first')
    bc.write_build(chunks, summary, out, context_data=materialize(chunks,_loaded=loaded))
    save_rows(out/'chunk_exclusions.jsonl', exclusions)
    save_rows(out/'visual_groups.jsonl', read_jsonl(run/'visual_groups.jsonl') if (run/'visual_groups.jsonl').exists() else [])
    members = []
    input_units = {u['unit_id']: u for u in read_jsonl(run/'source_units.jsonl')}
    for a in read_jsonl(run/'extracted_assets.jsonl'):
        if a.get('logical_group_id') or a['asset_type'] in {'figure', 'table', 'figure_group'}:
            members.append({**{k: a[k] for k in ('asset_id', 'document_id', 'version_id', 'pdf_page', 'asset_type',
                'bbox_pdf', 'source_refs', 'page_image', 'logical_group_id', 'panel_index') if k in a},
                'retrieval_eligible': input_units[a['asset_id']]['retrieval_eligible'], 'numeric_evidence_eligible': False})
    save_rows(out/'visual_members.jsonl', members)
    from preprocess.visual_context import assemble, save_bundle
    input_manifest = json.loads((run/'extraction_manifest.json').read_text(encoding='utf-8'))
    summary['visual_context_contract'] = save_bundle(out, assemble(read_jsonl(run/'extracted_assets.jsonl'),
        list(input_units.values()), read_jsonl(run/'visual_groups.jsonl') if (run/'visual_groups.jsonl').exists() else [], input_manifest['documents'],
        read_jsonl(run/'extraction_pages.jsonl')))
    summary['output_directory'] = str(out)
    summary['artifacts'] = {name: sha256(out/name) for name in ('chunks.jsonl', 'chunk.schema.json', 'source_units.jsonl',
        'contexts.jsonl', 'chunk_contexts.jsonl', 'chunk_exclusions.jsonl', 'visual_groups.jsonl', 'visual_members.jsonl')}
    summary['artifacts'].update(summary['visual_context_contract']['files'])
    if any(sha256(run/name) != digest for name, digest in summary['input_hashes'].items()):
        raise ValueError('Preprocessing input changed while exporting chunks')
    write_json(out/'chunks_summary.json', summary)
    return summary


def visual_context(folder, chunk_id):
    """Restore source panels/regions for a retrieval hit without adding excluded text."""
    folder = Path(folder)
    summary = json.loads((folder/'chunks_summary.json').read_text(encoding='utf-8'))
    for name, digest in summary['artifacts'].items():
        if sha256(folder/name) != digest:
            raise ValueError(f'Changed candidate artifact: {name}')
    chunk = next((c for c in read_jsonl(folder/'chunks.jsonl') if c['chunk_id'] == chunk_id), None)
    if chunk is None:
        raise ValueError('Unknown chunk ID')
    s = chunk['structure']
    identity = s['figure_id'] or s['table_id']
    if not identity:
        raise ValueError('Chunk has no visual parent')
    groups = {g['group_id']: g for g in read_jsonl(folder/'visual_groups.jsonl')}
    members = {m['asset_id']: m for m in read_jsonl(folder/'visual_members.jsonl')}
    group = groups.get(identity)
    ids = group['member_asset_ids'] if group else [identity]
    context_ids = s['context_unit_ids'] if s['table_id'] else []
    context_units = {u['unit_id']: u for u in read_jsonl(folder/'source_units.jsonl')}
    printed = [{'unit_id': uid, 'role': s.get('context_roles', {}).get(uid, 'caption'),
                'text': context_units[uid]['text'], 'origin': context_units[uid]['content_origin'],
                'source_refs': context_units[uid]['source_refs'], 'numeric_evidence_eligible': False}
               for uid in context_ids if uid in context_units]
    return {'logical_group_id': identity, 'kind': 'figure' if s['figure_id'] else 'table',
        'caption': s['caption'], 'association_status': s['association_status'],
        'continuation_status': s['continuation_status'], 'regions': [members[uid] for uid in ids],
        'source_refs': group['source_refs'] if group else chunk['provenance']['source_refs'],
        'printed_context': printed,
        **({'figure_labels': s['figure_labels']} if s.get('figure_labels') else {}),
        'description_review_required': chunk['representation']['description_review_required'],
        'numeric_evidence_eligible': False, 'semantic_status': 'unverified'}


def related_visual_contexts(folder, chunk_id, include_nondefault=False, include_evaluation=False):
    """Restore visual hits and reverse prose references under the same search policy."""
    from rag.search import filter_idx
    from preprocess.visual_context import load_bundle, link_text, verify_chunk_links
    folder = Path(folder)
    summary = json.loads((folder/'chunks_summary.json').read_text(encoding='utf-8'))
    for name, digest in summary['artifacts'].items():
        if sha256(folder/name) != digest:
            raise ValueError(f'Changed candidate artifact: {name}')
    chunks = read_jsonl(folder/'chunks.jsonl')
    eligible = [chunks[int(i)] for i in filter_idx(chunks, include_nondefault=include_nondefault,
        include_evaluation=include_evaluation)]
    chunk = next((c for c in eligible if c['chunk_id'] == chunk_id), None)
    if chunk is None:
        raise ValueError('Unknown or search-excluded context hit')
    s = chunk['structure']
    vid = s.get('figure_id') or s.get('table_id')
    if not summary.get('visual_context_contract'):
        return [visual_context(folder, chunk_id)] if vid else []
    bundle = load_bundle(folder, summary['visual_context_contract'])
    verify_chunk_links(chunk, bundle)
    links = {l['link_id']: l for l in bundle['links']}
    ids = [vid] if vid else list(dict.fromkeys(links[lid]['visual_id'] for lid in s.get('related_visual_link_ids', [])))
    result = []
    for identity in ids:
        target = next((c for c in eligible if (c['structure'].get('figure_id') or c['structure'].get('table_id')) == identity
            and c['structure'].get('kind') in {'figure_candidate', 'table_candidate'}
            and (c['document_id'], c['version_id']) == (chunk['document_id'], chunk['version_id'])), None)
        if target is None:
            continue  # A deferred table cannot re-enter search through its prose.
        restored = visual_context(folder, target['chunk_id'])
        own = [l for l in links.values() if l['visual_id'] == identity and l['link_status'] == 'linked_candidate']
        restored['source_context'] = [{**l, 'text': link_text(l, bundle)} for l in own]
        restored['retrieved_via_chunk_id'] = chunk_id
        restored['visual_chunk_id'] = target['chunk_id']
        result.append(restored)
    return result
