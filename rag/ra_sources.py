"""Frozen, product-independent guideline retrieval from existing native parses.

No download, OCR, embedding, or active-release mutation. Source and parser hashes
are checked at build and load; collection dates are not claims of current validity.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from threading import RLock
from pathlib import Path

from preprocess.ingest import sha256
from rag.portable_paths import resolve
from rag.search import Bm25
from rag.service_scope import SOURCES, JURISDICTIONS

VERSION = 'ra-guideline-native-1'
ROOT = Path(__file__).resolve().parents[1]
AGENCIES = {'EMA': 'EU', 'FDA': 'US', 'Health Canada': 'CA'}


def rows(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def permitted(meta):
    return (meta.get('evaluation_cohort') in (None, 'development')
            and meta.get('model_execution_allowed', True) is True)


def build(catalog, out, root=ROOT):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Choose a new guideline snapshot directory')
    chunks, sources, skipped = [], [], []
    for doc in rows(catalog):
        if doc.get('role') != 'guideline' or doc.get('source') not in SOURCES:
            continue
        if not permitted(doc) or not permitted(doc.get('document_metadata') or {}):
            skipped.append({'document_id': doc['document_id'], 'reason': 'protected'})
            continue
        raw = (root / doc['raw_path']).resolve()
        parsed = (root / doc['parsed_path']).resolve()
        if not raw.is_relative_to(root) or not parsed.is_relative_to(root):
            raise ValueError('Guideline sources must be inside the source workspace')
        if sha256(raw) != doc['sha256'] or doc['version_id'] != doc['sha256']:
            raise ValueError('Guideline source hash differs: ' + doc['document_id'])
        parser_rows = rows(parsed)
        pages = [p.get('pdf_page') for p in parser_rows]
        if pages != list(range(1, doc['page_count'] + 1)):
            raise ValueError('Guideline parse page coverage differs')
        sources.append({'document_id': doc['document_id'], 'raw_path': str(raw),
                        'raw_sha256': doc['sha256'], 'parsed_path': str(parsed),
                        'parsed_sha256': sha256(parsed)})
        for page in parser_rows:
            if (page.get('document_id'), page.get('version_id')) != (doc['document_id'], doc['version_id']):
                raise ValueError('Guideline parse identity differs')
            if not page.get('text', '').strip() or page.get('text_layer', {}).get('needs_ocr'):
                skipped.append({'document_id': doc['document_id'], 'pdf_page': page['pdf_page'],
                                'reason': 'native_text_unavailable'})
                continue
            meta = doc.get('source_metadata') or {}
            chunks.append({'chunk_id': f"{doc['document_id']}:{doc['version_id']}:native:{page['pdf_page']}",
                'document_id': doc['document_id'], 'version_id': doc['version_id'],
                'product_id': None, 'source': doc['source'], 'jurisdiction': AGENCIES[doc['source']],
                'role': 'guideline', 'doc_group': 'guideline', 'document_type': doc['document_type'],
                'title': doc['title'], 'header': doc['title'], 'text': page['text'],
                'pdf_page': page['pdf_page'], 'source_url': doc.get('final_url') or doc['url'],
                'document_metadata': {'family': 'guideline', 'author': 'regulator',
                    'status': meta.get('status'), 'reference_number': meta.get('reference_number'),
                    'first_published': meta.get('first_published'),
                    'legal_effective_date': meta.get('legal_effective_date'),
                    'checked': meta.get('checked'), 'retrieved_at': doc.get('retrieved_at'),
                    'current_validity_verified': False},
                'representation': {'origins': {'page': 'native'}, 'retrieval_eligible': True,
                                   'description_review_required': False, 'numeric_evidence_eligible': False},
                'structure': {'kind': 'text', 'section_path': [], 'caption': None},
                'source_refs': [{'path': str(raw), 'sha256': doc['sha256'], 'pdf_page': page['pdf_page']}],
                'source_spans': [{'parsed_path': str(parsed), 'start': 0, 'end': len(page['text'])}],
                'review_links': []})
    if not chunks:
        raise ValueError('No eligible native guideline pages')
    out.mkdir(parents=True)
    chunk_path = out / 'chunks.jsonl'
    chunk_path.write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in chunks), encoding='utf-8')
    manifest = {'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
                'catalog_sha256': sha256(catalog), 'chunks_sha256': sha256(chunk_path),
                'documents': len(sources), 'chunks': len(chunks), 'sources': sources, 'skipped': skipped,
                'jurisdictions': dict(Counter(c['jurisdiction'] for c in chunks)),
                'current_validity_verified': False, 'new_model_calls': 0, 'active_index_updated': False}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


class GuidelineIndex:
    def __init__(self, path, status_registry=None):
        self._bm = None
        self._lock = RLock()
        self.path = Path(path).resolve()
        self.manifest = json.loads((self.path / 'manifest.json').read_text(encoding='utf-8'))
        if self.manifest['version'] != VERSION or sha256(self.path / 'chunks.jsonl') != self.manifest['chunks_sha256']:
            raise ValueError('Guideline snapshot changed')
        for source in self.manifest['sources']:
            if sha256(resolve(source['raw_path'])) != source['raw_sha256'] or sha256(resolve(source['parsed_path'])) != source['parsed_sha256']:
                raise ValueError('Guideline source/parse changed')
        self.chunks = rows(self.path / 'chunks.jsonl')
        if len(self.chunks) != self.manifest['chunks'] or any(
                c['product_id'] is not None or c['jurisdiction'] not in JURISDICTIONS
                or c['role'] != 'guideline' or not permitted(c.get('document_metadata') or {}) for c in self.chunks):
            raise ValueError('Invalid guideline snapshot scope')
        registry_path = Path(status_registry) if status_registry else ROOT/'docs/rag/GUIDELINE_STATUS_2026-10-12.json'
        self.status_registry = None
        self.excluded_documents = []
        if registry_path.is_file():
            registry = json.loads(registry_path.read_text(encoding='utf-8'))
            if registry.get('schema_version') != 1 or not isinstance(registry.get('rows'),list):
                raise ValueError('Invalid guideline status registry')
            self.status_registry = {'path':str(registry_path),'sha256':sha256(registry_path),
                'checked_on':registry['checked_on'],'current_validity_certified':False}
            states = {r['document_id']:r for r in registry['rows']}
            for c in self.chunks:
                state = states.get(c['document_id'])
                if not state: continue
                meta = c['document_metadata']
                meta.update(registered_status=state['registered_status'],
                    status_checked_on=state['checked_on'],status_official_url=state['official_url'],
                    applicability_note_ko=state.get('note_ko',''))
                # A withdrawal applies to this document identity. A replacement
                # version must not relabel an older stored draft as adopted.
                denied = state.get('model_execution_allowed') is False or (
                    c['document_id']=='GL:EMA-TAILORED-CLINICAL-RP' and c['version_id']!=state['version_id'])
                if denied:
                    meta['model_execution_allowed'] = False
                    if c['document_id'] not in self.excluded_documents: self.excluded_documents.append(c['document_id'])

    def search(self, queries, agencies, documents=None, k=5):
        from rag.search import rrf
        idx = [i for i, c in enumerate(self.chunks) if c['jurisdiction'] in agencies
               and permitted(c.get('document_metadata') or {})
               and (documents is None or (c['document_id'], c['version_id']) in documents)]
        # Reuse the connection across launch/worker threads under this lock.
        with self._lock:
            if self._bm is None: self._bm = Bm25(self.chunks, thread_safe=True)
            bm = self._bm
            ranks = [bm.hits(q, idx, top_n=30) for q in queries]
            from rag.ra_retrieval import diverse_hits
            return [(score, self.chunks[i]) for score, i in diverse_hits(ranks, k)]

    def close(self):
        with self._lock:
            if self._bm is not None: self._bm.db.close(); self._bm = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=ROOT / 'data/catalog/documents.jsonl')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.catalog, args.out)
    print(json.dumps({k: v for k, v in result.items() if k not in ('sources', 'skipped')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
