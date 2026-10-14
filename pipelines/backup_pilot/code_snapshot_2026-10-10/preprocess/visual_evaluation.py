"""Offline exposure audit and frozen visual-evaluation inputs. Never runs models.

This module prepares metadata and empty annotation jobs, not extraction or Gold.
Historical automatic processing and confirmed development review are separate.
"""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from functools import lru_cache
import json
from pathlib import Path
import random
import re
import unicodedata
import zipfile

import fitz

from .ingest import sha256, stable_hash

VERSION = 'visual-evaluation-1'
SEED = 20261008
AUTOMATIC_ONLY = {'native_parse', 'native_table_candidates', 'retrieval_index'}
DEVELOPMENT = {'confirmed_development', 'reviewed_evidence'}
MODEL_HISTORY = {'legacy_ocr', 'text_layer_ocr_audit', 'paddle_page', 'legacy_vlm', 'evidence_derivation'}
SCAN_SPECS = [('data/preprocessed', '**/extraction_pages.jsonl'),
              ('data/preprocessed', '**/sample_manifest.json'), ('data/ocr', '**/p*_r*.json'),
              ('data/evidence', '**/*.json*'), ('data/rag/eval', '*.json*'),
              ('data/reviewed', '*.jsonl'), ('gold', '**/*.json*'), ('gold', '**/*.csv'),
              ('eval', '**/*.json*'), ('eval', '**/*.csv')]


def scanned_artifacts(root):
    return sorted({p.relative_to(root).as_posix() for directory, pattern in SCAN_SPECS
                   for p in (Path(root)/directory).glob(pattern) if p.is_file()})


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def rows(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def write_rows(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')


def family_key(doc):
    """Conservative metadata families; product similarity alone is not a family."""
    meta = doc['metadata']
    source, product = meta.get('source'), meta.get('product_id')
    kind = meta.get('document_type') or meta.get('role') or doc['document_id']
    if source == 'ClinicalTrials.gov':
        study = re.search(r'NCT\d+', doc['document_id'])
        kind = study.group() if study else kind
    elif source in {'FDA', 'PMDA'}:
        # Related original/supplement/review/label files stay in one dossier.
        kind = 'regulatory_dossier'
    elif source == 'EMA':
        if kind in {'rmp', 'rmp-summary'}:
            kind = 'risk_management_versions'
        elif kind == 'procedural-steps-after':
            kind = 'procedural_versions'
    return [source, product, kind]


class Families:
    def __init__(self, ids):
        self.parent = {key: key for key in ids}

    def find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def normalized(text):
    # Preserve digits/units; remove only normalization and whitespace differences.
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).casefold())


def text_sketch(text):
    if len(text) < 500:
        return []
    hashes = {hashlib.blake2b(text[i:i+12].encode(), digest_size=8).hexdigest()
              for i in range(0, len(text)-11, 4)}
    return sorted(hashes)[:64]


def fingerprint_pdf(doc, root):
    """Hash source text/raster placements without displaying text or running OCR."""
    path = root / doc['raw_path']
    if sha256(path) != doc['version_id']:
        raise ValueError(f"Source SHA differs: {doc['document_id']}")
    result = []
    with fitz.open(path) as pdf:
        for index, page in enumerate(pdf):
            text = normalized(page.get_text('text'))
            images = []
            for image in page.get_image_info(hashes=True):
                box = fitz.Rect(image['bbox'])
                area = box.get_area() / max(page.rect.get_area(), 1)
                if image['width'] >= 150 and image['height'] >= 100 and area >= .04:
                    images.append({'digest': image['digest'].hex(), 'bbox_pdf': list(box),
                                   'page_area_fraction': round(area, 4)})
            result.append({'document_id': doc['document_id'], 'version_id': doc['version_id'],
                           'pdf_page': index+1, 'bounds': [0, 0, page.cropbox.width, page.cropbox.height],
                           'rotation': page.rotation, 'native_chars': len(text),
                           'native_sha256': hashlib.sha256(text.encode()).hexdigest() if len(text) >= 500 else None,
                           'text_sketch': text_sketch(text), 'raster': images})
    return result


def duplicate_pairs(fingerprints):
    """Candidates are conservative exclusions, not confirmed same clinical figures."""
    pairs = {}
    exact, image_index, image_pages, sketches = defaultdict(list), defaultdict(list), defaultdict(set), defaultdict(list)
    for page in fingerprints:
        key = (page['document_id'], page['pdf_page'])
        if page['native_sha256']:
            exact[page['native_sha256']].append(page)
        for image in page['raster']:
            digest = image['digest']
            image_index[digest].append(page)
            image_pages[(digest, page['document_id'])].add(page['pdf_page'])
        for value in page['text_sketch']:
            sketches[value].append(key)
    lookup = {(p['document_id'], p['pdf_page']): p for p in fingerprints}

    def add(a, b, method, score):
        if a['document_id'] == b['document_id'] or a['version_id'] == b['version_id']:
            return
        ka, kb = (a['document_id'], a['pdf_page']), (b['document_id'], b['pdf_page'])
        ka, kb = sorted((ka, kb))
        record = pairs.setdefault((ka, kb), {'a': list(ka), 'b': list(kb), 'signals': []})
        signal = {'method': method, 'score': score}
        if signal not in record['signals']:
            record['signals'].append(signal)

    for group in exact.values():
        for i, a in enumerate(group):
            for b in group[i+1:]:
                add(a, b, 'exact_native_page_text_500chars', 1.0)
    for digest, group in image_index.items():
        # Common logos/headers and repeated pictograms do not connect dossiers.
        if len({p['document_id'] for p in group}) > 8:
            continue
        useful = [p for p in group if len(image_pages[(digest, p['document_id'])]) <= 2]
        unique = {(p['document_id'], p['pdf_page']): p for p in useful}
        group = list(unique.values())
        for i, a in enumerate(group):
            for b in group[i+1:]:
                add(a, b, 'identical_large_raster_pixels', 1.0)
    hits = Counter()
    for group in sketches.values():
        if len(group) > 80:  # Boilerplate shingles are not useful pair proposals.
            continue
        for i, a in enumerate(group):
            for b in group[i+1:]:
                if a[0] != b[0]:
                    hits[tuple(sorted((a, b)))] += 1
    for (ka, kb), count in hits.items():
        if count < 48:
            continue
        a, b = lookup[ka], lookup[kb]
        sa, sb = set(a['text_sketch']), set(b['text_sketch'])
        score = len(sa & sb) / max(len(sa | sb), 1)
        if score >= .85 and min(a['native_chars'], b['native_chars']) / max(a['native_chars'], b['native_chars']) >= .8:
            add(a, b, 'near_native_page_bottom64_jaccard', round(score, 4))
    return sorted(pairs.values(), key=lambda p: (p['a'], p['b']))


def audit(root):
    root = Path(root).resolve()
    catalog_path = root/'data/catalog/documents.jsonl'
    docs = [row for row in rows(catalog_path) if row.get('kind') == 'pdf']
    if len({d['document_id'] for d in docs}) != len(docs):
        raise ValueError('Duplicate catalog document IDs')
    by_id = {d['document_id']: d for d in docs}
    slugs = {re.sub(r'[^a-zA-Z0-9_-]', '_', d['document_id']): d['document_id'] for d in docs}
    by_sha = defaultdict(list)
    for doc in docs:
        by_sha[doc['version_id']].append(doc['document_id'])
        doc['metadata'] = {k: doc.get(k) for k in ('source', 'product_id', 'role', 'document_type', 'language', 'title')}
    inputs, events, unresolved = {}, {}, []

    def register(path):
        rel = path.relative_to(root).as_posix()
        inputs[rel] = sha256(path)
        return rel

    def event(doc_id, kind, path, pages=(), certainty='artifact_recorded'):
        if doc_id not in by_id:
            return
        rel = register(path) if path.relative_to(root).as_posix() not in inputs else path.relative_to(root).as_posix()
        key = (doc_id, kind, rel)
        if key not in events:
            events[key] = {'document_id': doc_id, 'kind': kind, 'artifact': rel,
                           'artifact_sha256': inputs[rel], 'pages': set(), 'certainty': certainty,
                           'artifact_mtime_utc': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                           'time_notice': 'File mtime is not inference time or proof of human inspection.'}
        events[key]['pages'].update(p for p in pages if type(p) is int and p > 0)

    metadata_keys = {'document_id', 'target_document_id', 'target_doc', 'doc_id', 'document', 'unit_id',
                     'source_unit_id', 'target_unit_id', 'version_id', 'document_sha256', 'raw_path',
                     'text_path', 'pdf', 'file', 'doc', 'pdf_key', 'pdf_file', 'source_id'}

    @lru_cache(maxsize=20000)
    def string_owners(value):
        found = set()
        if value in by_id:
            found.add(value)
        if value in slugs:
            found.add(slugs[value])
        if len(value) >= 64:
            for digest in re.findall(r'[0-9a-f]{64}', value):
                found.update(by_sha.get(digest, []))
        if value.startswith(('EMA:', 'FDA:', 'PMDA:', 'HC:', 'CTG:')):
            for doc_id in by_id:
                if value.startswith(doc_id + ':'):
                    found.add(doc_id)
        if value.startswith(('EMA_', 'FDA_', 'PMDA_', 'HC_', 'CTG_')):
            for slug, doc_id in slugs.items():
                if value.startswith(slug + '.'):
                    found.add(doc_id)
        return frozenset(found)

    def owners(value):
        return string_owners(value) if isinstance(value, str) else frozenset()

    def visit(obj, path, kind, inherited=()):
        if isinstance(obj, dict):
            own = set()
            for key in metadata_keys:
                own.update(owners(obj.get(key)))
            for key in ('document_ids', 'target_document_ids', 'unit_ids'):
                if isinstance(obj.get(key), list):
                    for value in obj[key]:
                        own.update(owners(value))
            local = own or set(inherited)
            pages = [obj.get('pdf_page'), obj.get('source_pdf_page')]
            if isinstance(obj.get('pages'), list):
                pages += [p for p in obj['pages'] if type(p) is int]
            if own or any(type(p) is int for p in pages):
                for doc_id in local:
                    event(doc_id, kind, path, pages)
            for key, value in obj.items():
                keyed = owners(key)
                for doc_id in keyed:
                    event(doc_id, kind, path)
                if isinstance(value, (dict, list)):
                    visit(value, path, kind, keyed or local)
        elif isinstance(obj, list):
            for value in obj:
                visit(value, path, kind, inherited)

    def scan(path, kind):
        register(path)
        try:
            if path.suffix == '.csv':
                with path.open(encoding='utf-8-sig', newline='') as stream:
                    for row in csv.DictReader(stream):
                        visit(row, path, kind)
            elif path.suffix == '.jsonl':
                for row in rows(path):
                    visit(row, path, kind)
            else:
                visit(load(path), path, kind)
        except (ValueError, UnicodeError) as exc:
            unresolved.append({'artifact': path.relative_to(root).as_posix(), 'reason': type(exc).__name__})

    register(catalog_path)
    for name in ('documents.jsonl', 'pages.jsonl', 'inventory_summary.json', 'manifest.json'):
        register(root/'data/preprocessed/inventory_2026-10-07'/name)
    register(root/'preprocess/visual_evaluation.py')
    register(root/'configs/preprocess.visual.annotation.schema.json')
    register(root/'configs/preprocess.visual.evaluation.protocol.json')
    for path in (root/'data/parsed').glob('*.pages.jsonl'):
        scan(path, 'native_parse')
    for path in (root/'data/ocr').rglob('*.json'):
        if path.name == 'text_audit.json':
            scan(path, 'text_layer_ocr_audit')
        elif path.name == 'native_tables.json':
            scan(path, 'native_table_candidates')
        elif re.fullmatch(r'p\d+_r\d+\.json', path.name):
            obj = load(path)
            kind = 'native_table_candidates' if obj.get('mode') == 'native_table' else 'legacy_ocr'
            register(path)
            visit(obj, path, kind)
    for path in (root/'data/preprocessed').rglob('extraction_pages.jsonl'):
        scan(path, 'paddle_page')
    for path in (root/'data/preprocessed').rglob('sample_manifest.json'):
        scan(path, 'reference_rendered')
    for name in ('preprocess.quality.sample.json', 'preprocess.quality.gold.json',
                 'preprocess.validation.sample_2026-10-08.json', 'preprocess.transfer.literal_spots_2026-10-08.json'):
        path = root/'configs'/name
        if path.exists():
            scan(path, 'confirmed_development')
    for path in (root/'data/evidence').rglob('*.json*'):
        if 'model' in path.parent.name:
            register(path)
            for doc_id in owners(path.name):
                event(doc_id, 'legacy_vlm', path)
            scan(path, 'legacy_vlm')
        else:
            scan(path, 'prior_benchmark' if 'benchmark' in path.name else 'evidence_derivation')
    for path in (root/'data/reviewed').glob('*.jsonl'):
        scan(path, 'reviewed_evidence')
    for path in (root/'data/rag/eval').glob('*.json*'):
        scan(path, 'prior_benchmark')
    for directory in ('gold', 'eval'):
        for path in (root/directory).rglob('*.json*'):
            scan(path, 'prior_benchmark')
        for path in (root/directory).rglob('*.csv'):
            scan(path, 'prior_benchmark')
    path = root/'data/rag/chunks.jsonl'
    if path.exists():
        scan(path, 'retrieval_index')
    # Do not read questions/answers in workbooks into the model context.
    for directory in ('gold', 'eval', 'Claude outputs'):
        for path in (root/directory).rglob('*.xlsx'):
            register(path)
            found = set()
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.startswith('xl/') and name.endswith('.xml'):
                        text = archive.read(name).decode('utf-8')
                        for digest in re.findall(r'[0-9a-f]{64}', text):
                            found.update(by_sha.get(digest, []))
                        found.update(doc_id for doc_id in by_id if doc_id in text)
            for doc_id in found:
                event(doc_id, 'external_annotation_mentions', path, certainty='document_mention_not_proof_of_review')
            unresolved.append({'artifact': path.relative_to(root).as_posix(),
                               'reason': 'Workbook provenance/reviewer history not certified; content not used as Gold.',
                               'matched_document_ids': sorted(found)})
    # Generated images imply potential inspection, not confirmed human viewing.
    for directory in ('tmp', 'data/reviewed'):
        for path in (root/directory).rglob('*.png'):
            name = path.as_posix()
            for doc in docs:
                slug = re.sub(r'[^a-zA-Z0-9_-]', '_', doc['document_id'])
                if slug in name or doc['version_id'] in name:
                    page = re.search(r'(?:_p|/p)(\d+)', name)
                    event(doc['document_id'], 'possible_visual_inspection', path,
                          [int(page.group(1))] if page else [], certainty='image_exists_viewing_unknown')
    print('Exposure metadata collected; hashing PDF pages without model inference.', flush=True)
    fingerprints = []
    for i, doc in enumerate(docs):
        relative = register(root/doc['raw_path'])
        if inputs[relative] != doc['version_id']:
            raise ValueError(f"Source SHA differs: {doc['document_id']}")
        cached = next((old for old in docs[:i] if old['version_id'] == doc['version_id']), None)
        if cached:
            fingerprints += [{**p, 'document_id': doc['document_id']} for p in fingerprints
                             if p['document_id'] == cached['document_id']]
        else:
            fingerprints.extend(fingerprint_pdf(doc, root))
        if (i+1) % 10 == 0:
            print(f'Fingerprint metadata: {i+1}/{len(docs)} PDF records', flush=True)
    pair_list = duplicate_pairs(fingerprints)
    families = Families(by_id)
    family_reasons = []
    groups = defaultdict(list)
    for doc in docs:
        groups[stable_hash(family_key(doc))].append(doc['document_id'])
    for group in list(groups.values()) + list(by_sha.values()):
        for doc_id in group[1:]:
            families.join(group[0], doc_id)
    for pair in pair_list:
        a, b = pair['a'][0], pair['b'][0]
        if by_id[a]['product_id'] == by_id[b]['product_id'] and any(
                s['method'] == 'identical_large_raster_pixels' for s in pair['signals']):
            families.join(a, b)
            family_reasons.append(pair)
    members = defaultdict(list)
    for doc_id in by_id:
        members[families.find(doc_id)].append(doc_id)
    family_ids = {member: 'vf'+stable_hash(sorted(group))[:20]
                  for group in members.values() for member in group}
    doc_events = defaultdict(list)
    event_list = []
    for value in events.values():
        value['pages'] = sorted(value['pages'])
        value['page_scope'] = 'listed_pages_only' if value['pages'] else 'document_known_pages_unspecified'
        event_list.append(value)
        doc_events[value['document_id']].append(value)
    records = []
    for doc in docs:
        ev = doc_events[doc['document_id']]
        kinds = {e['kind'] for e in ev}
        records.append({'document_id': doc['document_id'], 'version_id': doc['version_id'],
                        'raw_path': doc['raw_path'], 'metadata': doc['metadata'],
                        'family_id': family_ids[doc['document_id']], 'family_key': family_key(doc),
                        'pages': sum(p['document_id'] == doc['document_id'] for p in fingerprints),
                        'exposure_kinds': sorted(kinds),
                        'confirmed_development': bool(kinds & DEVELOPMENT),
                        'prior_model_or_evidence': bool(kinds & MODEL_HISTORY),
                        'record_clean': not bool(kinds - AUTOMATIC_ONLY),
                        'human_inspection_history': 'incomplete_not_proven_unseen',
                        'exposed_pages': sorted({p for e in ev if e['kind'] not in AUTOMATIC_ONLY for p in e['pages']})})
    return {'version': VERSION, 'root': str(root), 'inputs': inputs, 'documents': records,
            'artifact_scan_specs': SCAN_SPECS, 'artifact_scan_paths': scanned_artifacts(root),
            'events': sorted(event_list, key=lambda e: (e['document_id'], e['kind'], e['artifact'])),
            'families': [{'family_id': family_ids[group[0]], 'document_ids': sorted(group)} for group in members.values()],
            'fingerprints': fingerprints, 'duplicate_page_pairs': pair_list,
            'raster_family_links': family_reasons, 'unresolved_history': unresolved,
            'scope_notice': 'Record-clean does not prove never viewed. Prior model processing is not proof of human review.'}


def select_sample(documents, pages, pairs, *, seed=SEED, max_families=10):
    by_id = {d['document_id']: d for d in documents}
    groups = defaultdict(list)
    for doc in documents:
        groups[doc['family_id']].append(doc)
    dev = {family for family, group in groups.items() if any(d['confirmed_development'] for d in group)}
    development_ids = {d['document_id'] for d in documents if d['family_id'] in dev}
    # Propagate source-page links: copied pages can travel through a third file.
    adjacency = defaultdict(set)
    for pair in pairs:
        a, b = tuple(pair['a']), tuple(pair['b'])
        adjacency[a].add(b)
        adjacency[b].add(a)
    visited = {key for key in adjacency if key[0] in development_ids}
    pending = list(visited)
    while pending:
        for neighbor in adjacency[pending.pop()]:
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    forbidden = {key for key in visited if key[0] not in development_ids}
    history_visited = {key for key in adjacency if not by_id[key[0]]['record_clean']}
    pending = list(history_visited)
    while pending:
        for neighbor in adjacency[pending.pop()]:
            if neighbor not in history_visited:
                history_visited.add(neighbor)
                pending.append(neighbor)
    reserve_forbidden = {key for key in history_visited if by_id[key[0]]['record_clean']}
    page_lookup = {(p['document_id'], p['pdf_page']): p for p in pages}
    cohorts = {}
    for cohort, clean in [('record_clean_reserve', True), ('retrospective_locked', False)]:
        blocked = forbidden | reserve_forbidden if clean else forbidden
        eligible = {family: group for family, group in groups.items() if family not in dev
                    and all(d['record_clean'] for d in group) == clean}
        # At least one safe page is required; aliases are represented by one SHA.
        eligible = {f: [d for d in group if any((d['document_id'], p) in page_lookup and
                    (d['document_id'], p) not in blocked for p in range(1, d['pages']+1))]
                    for f, group in eligible.items()}
        eligible = {f: group for f, group in eligible.items() if group}
        rng = random.Random(f'{seed}:{cohort}:families')
        families = sorted(eligible)
        rng.shuffle(families)
        # Round-robin sources before filling; product is not held out.
        ordered, rest, used_sources = [], [], set()
        for family in families:
            sources = {d['metadata']['source'] for d in eligible[family]}
            if sources - used_sources:
                ordered.append(family)
                used_sources.update(sources)
            else:
                rest.append(family)
        chosen = (ordered + rest)[:max_families]
        selected_docs, whole_docs = [], []
        for family in chosen:
            group = sorted(eligible[family], key=lambda d: (-d.get('candidate_count', 0), -d['pages'], d['document_id']))
            selected_docs.append(group[0])
        # Two whole small documents, if present in chosen families and no blocked page.
        small = sorted([d for f in chosen for d in eligible[f] if d['pages'] <= 4 and
                       all((d['document_id'], p) not in blocked for p in range(1, d['pages']+1))],
                       key=lambda d: (d['pages'], stable_hash([seed, d['document_id']])))
        used_sha = set()
        for doc in small:
            if doc['version_id'] not in used_sha:
                doc = next((d for d in selected_docs if d['version_id'] == doc['version_id']), doc)
                whole_docs.append(doc['document_id'])
                used_sha.add(doc['version_id'])
                if doc not in selected_docs:
                    selected_docs.append(doc)
            if len(whole_docs) == 2:
                break
        # Prevent distinct catalog aliases from double-counting a PDF.
        unique = {}
        for doc in selected_docs:
            unique.setdefault(doc['version_id'], doc)
        selected_docs = sorted(unique.values(), key=lambda d: d['document_id'])
        whole_docs = [uid for uid in whole_docs if uid in {d['document_id'] for d in selected_docs}]
        frame = sorted((d['document_id'], p) for d in selected_docs for p in range(1, d['pages']+1)
                       if (d['document_id'], p) in page_lookup and (d['document_id'], p) not in blocked)
        rng = random.Random(f'{seed}:{cohort}:pages')
        random_keys = rng.sample(frame, min(20, len(frame)))
        jobs = {}

        def include(key, membership, **details):
            page = page_lookup[key]
            doc = by_id[key[0]]
            item = jobs.setdefault(key, {'document_id': key[0], 'version_id': doc['version_id'],
                'family_id': doc['family_id'], 'pdf_page': key[1], 'bounds': page['bounds'],
                'rotation': page['rotation'], 'memberships': [], 'selection_details': [],
                'schema_version': VERSION, 'annotation_status': 'not_annotated',
                'reviewer': None, 'reviewer_type': None, 'reviewed_at': None,
                'clinical_semantics_reviewed': False, 'objects': None, 'negative_page': None})
            if membership not in item['memberships']:
                item['memberships'].append(membership)
            item['selection_details'].append({'membership': membership, **details})

        for key in random_keys:
            include(key, 'random_anchor', frame_size=len(frame), sample_size=len(random_keys),
                    inclusion_probability=len(random_keys)/len(frame),
                    probability_scope='Conditional on purposively selected documents and duplicate-page exclusions; not corpus-wide.')
        for family in chosen:
            keys = [key for key in frame if by_id[key[0]]['family_id'] == family]
            if not keys:
                continue
            # Heuristic difficulty proxy only; no actual visual label is asserted.
            key = max(keys, key=lambda k: (page_lookup[k].get('stress_score', 0), stable_hash([seed, list(k)])))
            include(key, 'stress_anchor', heuristic_proxy=page_lookup[key].get('stress_signals', {}),
                    actual_visual_type='unknown_until_annotated')
            for number in (key[1]-1, key[1]+1):
                adjacent = (key[0], number)
                if adjacent in frame:
                    include(adjacent, 'context_page', anchor=list(key))
        for uid in whole_docs:
            for key in frame:
                if key[0] == uid:
                    include(key, 'whole_document')
        cohorts[cohort] = {'family_ids': chosen, 'document_ids': [d['document_id'] for d in selected_docs],
                           'pages': [jobs[key] for key in sorted(jobs)], 'random_frame_size': len(frame),
                           'random_anchor_count': len(random_keys), 'whole_document_ids': whole_docs,
                           'eligible_family_count': len(eligible),
                           'evaluation_claim': 'Recorded-history reserve only; human history incomplete.' if clean else
                               'Retrospective diagnostic transfer; prior processing/exposure exists, not independent holdout.'}
    return {'seed': seed, 'development_family_ids': sorted(dev),
            'development_document_ids': sorted(development_ids), 'cohorts': cohorts,
            'excluded_duplicate_page_keys': [list(key) for key in sorted(forbidden)],
            'excluded_reserve_duplicate_page_keys': [list(key) for key in sorted(reserve_forbidden)]}


def verify_inputs(root, inputs):
    root = Path(root).resolve()
    for relative, expected in inputs.items():
        path = (root/relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Frozen input path outside expected root')
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f'Frozen input changed or missing: {relative}')


def freeze(audit_path, root, out, seed=SEED):
    root, out = Path(root).resolve(), Path(out).resolve()
    if not out.is_relative_to(root):
        raise ValueError('Evaluation output must stay inside project root')
    exposure_path, split_path = root/'configs/preprocess.visual.exposure.json', root/'configs/preprocess.visual.split.json'
    if out.exists() or exposure_path.exists() or split_path.exists():
        raise ValueError('Freeze requires a new output and absent visual configs; do not overwrite a sealed version')
    data = load(audit_path)
    if Path(data['root']).resolve() != root:
        raise ValueError('Audit belongs to a different root; create a fresh audit')
    verify_inputs(root, data['inputs'])
    inventory_dir = root/'data/preprocessed/inventory_2026-10-07'
    inventory_docs = {d['document_id']: d for d in rows(inventory_dir/'documents.jsonl')}
    inventory_pages = {(p['document_id'], p['pdf_page']): p for p in rows(inventory_dir/'pages.jsonl') if 'pdf_page' in p}
    pages = []
    for doc in data['documents']:
        doc['candidate_count'] = inventory_docs[doc['document_id']]['candidate_count']
    for page in data['fingerprints']:
        prior = inventory_pages[(page['document_id'], page['pdf_page'])]
        if prior['version_id'] != page['version_id']:
            raise ValueError('Inventory/source mismatch')
        signals = prior.get('signals', {})
        pages.append({**page, 'stress_score': min(prior.get('candidate_count', 0), 20)
                      + (8 if prior.get('needs_ocr') else 0)
                      + (6 if signals.get('raster_placements') else 0)
                      + (4 if signals.get('vector_paths', 0) > 20 else 0),
                      'stress_signals': {'modality': prior.get('modality'), 'needs_ocr': prior.get('needs_ocr'),
                                         'candidate_count': prior.get('candidate_count'), **signals}})
    selected = select_sample(data['documents'], pages, data['duplicate_page_pairs'], seed=seed)
    exposure = {k: v for k, v in data.items() if k not in {'fingerprints', 'root'}}
    exposure['audit_source_sha256'] = sha256(audit_path)
    split = {'version': VERSION, 'created_at_utc': datetime.now(timezone.utc).isoformat(), **selected,
             'stage_1_status': 'selection_and_annotation_contract_frozen_labels_pending',
             'strict_holdout_ready': False, 'gold_release_ready': False,
             'strict_holdout_reason': 'Prior model history, incomplete inspection history, and unverified visual annotation. Do not claim independent generalization.',
             'target': {'families_min': 8, 'families_max': 12, 'pages_min': 40, 'pages_max': 60, 'random_pages_min': 20},
             'known_product_scope': sorted({d['metadata']['product_id'] for d in data['documents']}),
             'unseen_product_or_active_substance_test': False,
             'annotation_schema': 'annotation_schema.json',
             'model_calls': {'ocr': 0, 'vlm': 0, 'layout': 0},
             'no_test_content_in_development': True}
    write(exposure_path, exposure)
    write(split_path, split)
    write(out/'exposure.json', exposure)
    write(out/'split.json', split)
    write_rows(out/'page_fingerprints.jsonl', data['fingerprints'])
    write(out/'duplicate_candidates.json', {'pairs': data['duplicate_page_pairs'], 'scope': 'Conservative candidates; text/vector/scanned-region similarity is not exhaustive.'})
    write(out/'source_hashes.json', {d['document_id']: d['version_id'] for d in data['documents']})
    write(out/'annotation_schema.json', load(root/'configs/preprocess.visual.annotation.schema.json'))
    write(out/'evaluation_protocol.json', load(root/'configs/preprocess.visual.evaluation.protocol.json'))
    baseline_files = ('preprocess/figure_labels.py', 'preprocess/figure_ocr.py',
                      'preprocess/hybrid_validate.py', 'preprocess/visual_groups.py',
                      'rag/preprocessed_chunks.py', 'configs/preprocess.local.json',
                      'models/paddle/model_manifest.json')
    baseline = {}
    for relative in baseline_files:
        path = root/relative
        if path.exists():
            destination = out/'baseline_snapshot'/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
            baseline[relative] = sha256(destination)
    write(out/'baseline_snapshot/manifest.json', {'files': baseline, 'inference_run': False})
    tests_log = root/'tmp/visual_evaluation/unit_tests.log'
    if tests_log.is_file():
        destination = out/'validation/unit_tests.log'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(tests_log.read_bytes())
    for cohort, spec in selected['cohorts'].items():
        write_rows(out/f'{cohort}/annotation_jobs.jsonl', spec['pages'])
        write_rows(out/f'{cohort}/annotations.jsonl', spec['pages'])
        ids = spec['document_ids']
        by_id = {d['document_id']: d for d in data['documents']}
        sample = {'sample_version': VERSION, 'cohort': cohort, 'gold_release_ready': False,
                  'selection_method': spec['evaluation_claim'],
                  'documents': [{'document_id': uid, 'version_id': by_id[uid]['version_id'],
                       'label': uid, 'pages': [p['pdf_page'] for p in spec['pages'] if p['document_id'] == uid],
                       'roles': ['unannotated'] * sum(p['document_id'] == uid for p in spec['pages'])} for uid in ids]}
        write(out/f'{cohort}/sample.json', sample)
    write(out/'annotation_inventory.json', {'gold_release_ready': False,
          'cohorts': {cohort: {'selected_pages': len(spec['pages']), 'annotated_pages': 0,
                     'human_reviewed_pages': 0, 'logical_figures': None, 'logical_tables': None,
                     'negative_pages': None, 'actual_structure_coverage': 'unknown_until_annotated'}
                     for cohort, spec in selected['cohorts'].items()}})
    seal = {'version': VERSION, 'artifacts': {path.relative_to(out).as_posix(): sha256(path)
            for path in sorted(out.rglob('*')) if path.is_file() and path.name != 'annotations.jsonl'},
            'configs': {path.relative_to(root).as_posix(): sha256(path) for path in (exposure_path, split_path)},
            'mutable_annotations': [f'{cohort}/annotations.jsonl' for cohort in selected['cohorts']]}
    write(out/'seal.json', seal)
    return split


def validate_annotation(job, external_objects=None):
    """Contract checks independent of model transcriptions; pending != negative."""
    if job.get('schema_version') != VERSION or not job.get('family_id'):
        raise ValueError('Annotation version and family identity required')
    if not isinstance(job.get('document_id'), str) or not re.fullmatch(r'[0-9a-f]{64}', job.get('version_id', '')):
        raise ValueError('Source document identity required')
    if type(job.get('pdf_page')) is not int or job['pdf_page'] <= 0 or job.get('rotation') not in (0, 90, 180, 270):
        raise ValueError('PDF page and rotation invalid')
    bounds = job.get('bounds')
    if not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) not in {int, float} for v in bounds) or not (
            bounds[0] < bounds[2] and bounds[1] < bounds[3]):
        raise ValueError('Invalid page bounds')
    if job.get('clinical_semantics_reviewed') is not False:
        raise ValueError('This visual annotation version does not certify clinical/numeric evidence')
    if job['annotation_status'] == 'not_annotated':
        if job['objects'] is not None or job['negative_page'] is not None:
            raise ValueError('Pending annotation must not assert absence or objects')
        if any(job.get(key) is not None for key in ('reviewer', 'reviewer_type', 'reviewed_at')):
            raise ValueError('Pending annotation cannot assert a completed review')
        return
    if job['annotation_status'] not in {'agent_draft', 'human_reviewed', 'adjudicated'}:
        raise ValueError('Unknown annotation status')
    objects = job['objects']
    if type(objects) is not list or type(job['negative_page']) is not bool:
        raise ValueError('Completed annotation needs objects and explicit negative status')
    if job['negative_page'] != (not objects):
        raise ValueError('Negative page/object mismatch')
    if not job.get('reviewer') or not job.get('reviewed_at'):
        raise ValueError('Completed annotation needs reviewer and time')
    try:
        reviewed = datetime.fromisoformat(job['reviewed_at'])
        if reviewed.tzinfo is None:
            raise ValueError('Timezone required')
    except (TypeError, ValueError) as exc:
        raise ValueError('Review time needs ISO date-time and timezone') from exc
    if job.get('reviewer_type') not in {'agent', 'human'}:
        raise ValueError('Explicit reviewer type required')
    if job['annotation_status'] in {'human_reviewed', 'adjudicated'} and job['reviewer_type'] != 'human':
        raise ValueError('Agent annotations cannot be certified as human reviewed')
    local_lookup = {o['object_id']: o for o in objects}
    if len(local_lookup) != len(objects):
        raise ValueError('Duplicate annotation object IDs')
    lookup = dict(external_objects or {})
    lookup.update({uid: {**obj, '_document_id': job['document_id'], '_version_id': job['version_id']}
                   for uid, obj in local_lookup.items()})
    for obj in objects:
        if obj['kind'] not in {'figure', 'panel', 'inset', 'table', 'caption', 'legend_block',
                               'legend_title', 'legend_item', 'axis_label', 'printed_label', 'table_cell', 'footnote'}:
            raise ValueError('Unknown object role')
        box = obj['bbox_pdf']
        if len(box) != 4 or any(type(v) not in {int, float} for v in box) or not (
                bounds[0] <= box[0] < box[2] <= bounds[2] and bounds[1] <= box[1] < box[3] <= bounds[3]):
            raise ValueError('Annotation bbox outside page or empty')
        if obj.get('legibility') not in {'readable', 'partially_readable', 'unreadable', 'redacted', 'not_applicable'}:
            raise ValueError('Explicit legibility required')
        if obj.get('certainty') not in {'certain', 'ambiguous', 'unresolved'}:
            raise ValueError('Explicit certainty required')
        if obj.get('legibility') == 'redacted' and obj.get('literal_text'):
            raise ValueError('Do not transcribe hidden redacted values')
        if obj['kind'] in {'legend_title', 'legend_item', 'axis_label', 'printed_label', 'table_cell', 'caption', 'footnote'} and 'literal_text' not in obj:
            raise ValueError('Printed fields need literal_text, nullable when unreadable')
        if 'literal_text' in obj and obj['literal_text'] is not None and not isinstance(obj['literal_text'], str):
            raise ValueError('Literal transcription must be text, not inferred numeric value')
        if obj.get('legibility') == 'readable' and obj['kind'] in {'legend_title', 'legend_item', 'axis_label', 'printed_label', 'table_cell', 'caption', 'footnote'} and not obj.get('literal_text'):
            raise ValueError('Readable printed field needs a transcription')
        parents = obj.get('parent_ids', [])
        for key in ('parent_ids', 'associated_panel_ids', 'header_ids', 'footnote_ids'):
            ids = obj.get(key, [])
            if not isinstance(ids, list) or any(not isinstance(uid, str) or not uid for uid in ids) or len(ids) != len(set(ids)):
                raise ValueError('Object references must be unique string IDs')
        if obj['kind'] in {'panel', 'inset', 'legend_title', 'legend_item', 'axis_label', 'table_cell'} and not parents:
            raise ValueError('Child object requires explicit ownership')
        for target in parents + obj.get('associated_panel_ids', []) + obj.get('header_ids', []) + obj.get('footnote_ids', []):
            if target not in lookup or target == obj['object_id']:
                raise ValueError('Dangling or self annotation reference')
            if (lookup[target]['_document_id'], lookup[target]['_version_id']) != (job['document_id'], job['version_id']):
                raise ValueError('Annotation ownership cannot cross source documents')
        if obj['kind'] == 'inset' and not any(lookup[p]['kind'] == 'panel' for p in parents):
            raise ValueError('Inset must attach to a main panel')
        if obj['kind'] == 'panel' and not any(lookup[p]['kind'] == 'figure' for p in parents):
            raise ValueError('Main panel must attach to Figure')
        if obj['kind'] in {'legend_title', 'legend_item'} and not any(lookup[p]['kind'] == 'legend_block' for p in parents):
            raise ValueError('Legend heading/entry must attach to legend block')
        for uid in obj.get('associated_panel_ids', []):
            if lookup[uid]['kind'] not in {'panel', 'inset'}:
                raise ValueError('Shared legend associations must identify panels')
        if obj['kind'] == 'table_cell' and not any(lookup[p]['kind'] == 'table' for p in parents):
            raise ValueError('Table cell must attach to table')
        if obj['kind'] == 'table_cell':
            for key, minimum in [('row_index', 0), ('column_index', 0), ('row_span', 1), ('column_span', 1)]:
                if type(obj.get(key)) is not int or obj[key] < minimum:
                    raise ValueError('Table row/column indices and spans required')
            if obj.get('cell_role') not in {'column_header', 'row_header', 'data', 'unit', 'stub', 'unknown'}:
                raise ValueError('Table cell role required')
        if obj['kind'] == 'axis_label' and obj.get('axis_kind') not in {'x', 'y', 'secondary_x', 'secondary_y', 'unknown'}:
            raise ValueError('Axis orientation required')
        for uid in obj.get('header_ids', []):
            if lookup[uid]['kind'] != 'table_cell' or lookup[uid].get('cell_role') not in {'column_header', 'row_header', 'unit', 'stub'}:
                raise ValueError('Header references must identify table header/unit cells')
        for uid in obj.get('footnote_ids', []):
            if lookup[uid]['kind'] != 'footnote':
                raise ValueError('Footnote references must identify footnotes')
    def walk(uid, ancestors):
        if uid in ancestors:
            raise ValueError('Cyclic parent relation')
        for parent in lookup[uid].get('parent_ids', []):
            walk(parent, ancestors | {uid})
    for uid in lookup:
        walk(uid, set())


def validate_annotations(jobs):
    lookup = {}
    for job in jobs:
        for obj in job['objects'] or []:
            uid = obj['object_id']
            if uid in lookup:
                raise ValueError('Duplicate object ID across pages')
            lookup[uid] = {**obj, '_document_id': job['document_id'], '_version_id': job['version_id']}
    for job in jobs:
        validate_annotation(job, lookup)


def verify_pack(root, out):
    root, out = Path(root), Path(out)
    seal = load(out/'seal.json')
    verify_inputs(out, seal['artifacts'])
    verify_inputs(root, seal['configs'])
    exposure = load(out/'exposure.json')
    verify_inputs(root, exposure['inputs'])
    split = load(out/'split.json')
    protected_families = {family for cohort in split['cohorts'].values() for family in cohort['family_ids']}
    protected = [d for d in exposure['documents'] if d['family_id'] in protected_families]
    new_paths = sorted(set(scanned_artifacts(root)) - set(exposure.get('artifact_scan_paths', [])))
    for relative in new_paths:
        content = (root/relative).read_text(encoding='utf-8-sig')
        for doc in protected:
            if doc['version_id'] in content or json.dumps(doc['document_id']) in content:
                raise ValueError(f'New artifact mentions protected evaluation family; record exposure before claiming a locked test: {relative}')
    annotated = 0
    total = 0
    immutable = ('schema_version', 'document_id', 'version_id', 'family_id', 'pdf_page', 'bounds',
                 'rotation', 'memberships', 'selection_details', 'clinical_semantics_reviewed')
    for relative in seal['mutable_annotations']:
        jobs_path = out/relative.replace('annotations.jsonl', 'annotation_jobs.jsonl')
        jobs, annotations = list(rows(jobs_path)), list(rows(out/relative))
        if len(jobs) != len(annotations):
            raise ValueError('Annotation page count differs from sealed jobs')
        by_key = {(j['document_id'], j['pdf_page']): j for j in jobs}
        seen = set()
        for annotation in annotations:
            key = (annotation['document_id'], annotation['pdf_page'])
            if key in seen or key not in by_key:
                raise ValueError('Duplicate or unselected annotation page')
            seen.add(key)
            if any(annotation.get(k) != by_key[key].get(k) for k in immutable):
                raise ValueError('Annotation changed frozen source/selection metadata')
            annotated += annotation['annotation_status'] != 'not_annotated'
        validate_annotations(annotations)
        total += len(jobs)
    return {'frozen_inputs_verified': True, 'selection_verified': True, 'pages': total,
            'annotated_pages': annotated, 'gold_release_ready': False,
            'new_artifacts_checked': len(new_paths),
            'model_calls': {'ocr': 0, 'vlm': 0, 'layout': 0}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit', 'freeze', 'verify'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--audit', type=Path)
    parser.add_argument('--seed', type=int, default=SEED)
    args = parser.parse_args(argv)
    if args.command == 'audit':
        if args.out.exists():
            raise ValueError('Audit output already exists; use a new version')
        data = audit(args.root)
        write(args.out, data)
        print(json.dumps({'documents': len(data['documents']), 'families': len(data['families']),
                          'record_clean_documents': sum(d['record_clean'] for d in data['documents']),
                          'confirmed_development_documents': sum(d['confirmed_development'] for d in data['documents']),
                          'duplicate_page_pairs': len(data['duplicate_page_pairs']),
                          'unresolved_sources': len(data['unresolved_history'])}, indent=2))
    elif args.command == 'freeze':
        if not args.audit:
            parser.error('--audit required')
        split = freeze(args.audit, args.root, args.out, args.seed)
        print(json.dumps({k: {'families': len(v['family_ids']), 'documents': len(v['document_ids']),
                                  'pages': len(v['pages']), 'random_anchors': v['random_anchor_count'],
                                  'whole_documents': len(v['whole_document_ids'])} for k, v in split['cohorts'].items()}, indent=2))
    else:
        print(json.dumps(verify_pack(args.root, args.out), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
