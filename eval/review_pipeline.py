"""Reproducible development retrieval benchmark for a preprocessed candidate.

Uses existing source-page labels, never drops unavailable targets from metrics.
Korean raw retrieval and English reference retrieval are separate query forms;
this does not score question understanding or generated answer correctness.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')

from preprocess.ingest import sha256
from rag.search import Bm25, dense_hits, filter_idx, load_index, rrf, to_pages, literal_caption_hits
from rag.embed_index import Embedder


def cases_for(chunks):
    documents = sorted({c['document_id'] for c in chunks})
    suite = json.loads((ROOT/'eval/preprocessed_pilot_queries_2026-10-07.json').read_text(encoding='utf-8'))
    cases = []
    for case in suite['cases']:
        matches = [d for d in documents if d.startswith(case['document_prefix'])]
        if len(matches) != 1:
            raise ValueError(f'Pilot requires one exact document per prefix: {case["id"]}')
        cases.append({**case, 'document_id': matches[0], 'suite': 'visual_pilot'})
    with (ROOT/'eval/queries_v0.1.csv').open(encoding='utf-8-sig', newline='') as stream:
        for q in csv.DictReader(stream):
            if q['type'] != 'retrieval' or q['document_id'] not in documents:
                continue
            cases.append({'id': q['query_id'], 'suite': 'historical_qa', 'document_id': q['document_id'],
                'pages': json.loads(q['acceptable_pages']), 'kind': None, 'jurisdiction': 'EU',
                'en': q['en_ref'], 'ko': q['ko_a'], 'ko_b': q['ko_b']})
    return cases


def is_target(chunk, case, typed=False):
    return (chunk['document_id'] == case['document_id'] and chunk['pdf_page'] in case['pages']
        and (not typed or not case.get('kind') or chunk['structure']['kind'] == case['kind'])
        and (not typed or case.get('caption_contains', '') in (chunk['structure'].get('caption') or '')))


def metrics(rows, field):
    n = len(rows)
    return {'n': n, **{f'hit@{k}': round(sum(r[field] is not None and r[field] <= k for r in rows)/n, 4)
        for k in (1, 3, 5, 10)}, 'mrr@10': round(sum(1/r[field] for r in rows if r[field] is not None and r[field] <= 10)/n, 4)}


def audit(chunks):
    groups = defaultdict(list)
    for c in chunks:
        groups[c['structure']['kind']].append(c)
    return {'chunks': len(chunks), 'missing_date': sum(not c.get('doc_date') for c in chunks),
        'missing_source_url': sum(not c.get('source_url') for c in chunks),
        'missing_language': sum(c.get('language') in (None, 'unknown') for c in chunks),
        'default_eligible': len(filter_idx(chunks)), 'explicit_eligible': len(filter_idx(chunks, include_nondefault=True)),
        'by_kind': {kind: {'count': len(rows), 'under_32_tokens': sum(c['n_tokens'] < 32 for c in rows),
            'missing_caption': sum(not c['structure'].get('caption') for c in rows),
            'missing_section': sum(not c['structure'].get('section_path') for c in rows)} for kind, rows in groups.items()},
        'by_source': dict(Counter(c['source'] for c in chunks)),
        'numeric_evidence_eligible': sum(c['representation']['numeric_evidence_eligible'] for c in chunks)}


def evaluate(candidate, out, query_cache=None):
    chunks, vectors, meta = load_index('bge-m3', candidate)
    cases = cases_for(chunks)
    forms = [(case, lang, case[lang]) for case in cases for lang in ('en', 'ko', 'ko_b', 'ja') if lang in case]
    questions = [q for _, _, q in forms]
    import numpy as np
    fingerprint = __import__('hashlib').sha256(json.dumps(questions, ensure_ascii=False).encode()).hexdigest()
    cache = Path(query_cache) if query_cache else out.parent/'query_vectors.npz'
    if cache.exists():
        with np.load(cache, allow_pickle=False) as saved:
            if str(saved['fingerprint']) != fingerprint or str(saved['model']) != meta['model']:
                raise ValueError('Query cache belongs to a different benchmark/model')
            qvec = saved['vectors']
    else:
        embedder = Embedder('bge-m3', local_only=True)
        qvec = embedder.encode_queries(questions)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, fingerprint=fingerprint, model=meta['model'], vectors=qvec)
        del embedder
    bm = Bm25(chunks)
    rows = []
    for (case, lang, question), vector in zip(forms, qvec):
        # Policy coverage is reported explicitly, without silently dropping protocols/labels.
        for scope in ('default', 'explicit_all', 'explicit_agency'):
            indices = filter_idx(chunks, include_nondefault=scope != 'default',
                jurisdiction=case['jurisdiction'] if scope == 'explicit_agency' else None)
            ranks = {'bm25': bm.hits(question, indices, 50), 'dense': dense_hits(vector, vectors, indices, 50)}
            ranks['hybrid'] = rrf([ranks['bm25'], ranks['dense']], top_n=50)
            captions = literal_caption_hits(question, indices, chunks)
            ranks['hybrid_caption'] = rrf([ranks['bm25'], ranks['dense'], *([captions] if captions else [])], top_n=50)
            for mode, hits in ranks.items():
                pages = to_pages(hits, chunks, 10, collapse=False)
                page_rank = next((i for i, p in enumerate(pages, 1) if p['document_id'] == case['document_id'] and p['pdf_page'] in case['pages']), None)
                target_rank = next((i for i, (_, idx) in enumerate(hits, 1) if is_target(chunks[idx], case, True)), None)
                rows.append({'id': case['id'], 'suite': case['suite'], 'language': lang, 'scope': scope, 'mode': mode,
                    'query': question, 'kind': case.get('kind'), 'target_document': case['document_id'], 'target_pages': case['pages'],
                    'target_available': any(is_target(chunks[i], case, True) for i in indices),
                    'page_rank': page_rank, 'target_chunk_rank': target_rank,
                    'top_pages': [{'document_id': p['document_id'], 'pdf_page': p['pdf_page'], 'best_chunk': p['best_chunk']} for p in pages[:5]]})
    bm.db.close()
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row['suite'], row['language'], row['scope'], row['mode'])].append(row)
    result = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'Existing development/AI-draft source-page queries, not independent generalization or answer correctness.',
        'candidate': str(candidate.resolve()), 'chunks_sha256': meta['chunks_sha256'], 'query_fingerprint': fingerprint,
        'embedding_revision': meta.get('revision'), 'cases': len(cases), 'query_forms': len(forms), 'audit': audit(chunks),
        'metrics': [{'suite': s, 'language': l, 'scope': scope, 'mode': mode,
            'available': sum(r['target_available'] for r in group), 'page': metrics(group, 'page_rank'),
            'target_chunk': metrics(group, 'target_chunk_rank')} for (s, l, scope, mode), group in buckets.items()],
        'results': rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'cases': len(cases), 'query_forms': len(forms), 'audit': result['audit'],
        'hybrid': [r for r in result['metrics'] if r['mode'] == 'hybrid' and r['scope'] == 'explicit_agency'], 'out': str(out)}, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--query-cache', type=Path)
    args = parser.parse_args()
    if args.out.exists(): parser.error('Choose a new output file')
    evaluate(args.candidate, args.out, args.query_cache)
