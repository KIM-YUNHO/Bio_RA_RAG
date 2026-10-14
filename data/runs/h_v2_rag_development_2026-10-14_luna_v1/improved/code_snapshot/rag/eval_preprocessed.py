"""Offline source-based pilot retrieval review; does not generate answers."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from embed_index import Embedder
from search import Bm25, dense_hits, filter_idx, load_index, rrf, to_pages


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target(chunk, case):
    return (chunk['document_id'].startswith(case['document_prefix'])
            and chunk['pdf_page'] in case['pages']
            and chunk['structure']['kind'] == case['kind']
            and case.get('caption_contains', '') in (chunk['structure']['caption'] or ''))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--rag-dir', type=Path, required=True)
    parser.add_argument('--queries', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    sys.stdout.reconfigure(encoding='utf-8')
    root = Path(__file__).resolve().parent.parent
    before = digest(root/'data/rag/chunks.jsonl')
    chunks, vectors, meta = load_index('bge-m3', args.rag_dir)
    assert vectors.shape == (len(chunks), 1024) and np.isfinite(vectors).all()
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1., atol=0.002)
    suite = json.loads(args.queries.read_text(encoding='utf-8'))
    queries = [(case, lang, case[lang]) for case in suite['cases'] for lang in ('en', 'ko', 'ja') if lang in case]
    assert all(any(target(c, case) for c in chunks) for case in suite['cases'])
    embedder = Embedder('bge-m3', local_only=True)
    start = time.monotonic()
    query_vectors = embedder.encode_queries([text for _, _, text in queries])
    query_seconds = time.monotonic() - start
    bm25 = Bm25(chunks)
    rows = []
    for (case, lang, text), qv in zip(queries, query_vectors):
        for scope in ('all_candidate_pages', 'provided_agency_filter'):
            indices = filter_idx(chunks, jurisdiction=case['jurisdiction'] if scope == 'provided_agency_filter' else None)
            dense = dense_hits(qv, vectors, indices, len(chunks))
            keyword = bm25.hits(text, indices, len(chunks))
            for mode, hits in [('dense', dense), ('bm25', keyword), ('hybrid', rrf([dense, keyword], top_n=len(chunks)))]:
                pages = to_pages(hits, chunks, 10, collapse=False)
                page_rank = next((n for n, p in enumerate(pages, 1) if p['document_id'].startswith(case['document_prefix']) and p['pdf_page'] in case['pages']), None)
                chunk_rank = next((n for n, (_, index) in enumerate(hits, 1) if target(chunks[index], case)), None)
                rows.append({'case_id': case['id'], 'language': lang, 'scope': scope, 'mode': mode,
                    'query': text, 'page_rank': page_rank, 'target_chunk_rank': chunk_rank,
                    'bm25_match': bm25.to_match(text),
                    'top_chunks': [{'rank': n, 'score': round(score, 6), 'chunk_id': chunks[i]['chunk_id'],
                        'document_id': chunks[i]['document_id'], 'pdf_page': chunks[i]['pdf_page'],
                        'kind': chunks[i]['structure']['kind'], 'is_target': target(chunks[i], case),
                        'representation': chunks[i]['representation']} for n, (score, i) in enumerate(hits[:5], 1)],
                    'top_pages': [{'document_id': p['document_id'], 'pdf_page': p['pdf_page'], 'best_chunk': p['best_chunk']} for p in pages[:5]]})
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row['scope'], row['language'], row['mode'])].append(row)
    metrics = []
    for (scope, lang, mode), group in buckets.items():
        metrics.append({'scope': scope, 'language': lang, 'mode': mode, 'queries': len(group),
            **{f'{kind}_hit_at_{k}': sum(r[field] is not None and r[field] <= k for r in group)
               for kind, field in [('page', 'page_rank'), ('target_chunk', 'target_chunk_rank')] for k in (1, 5)}})
    summary = json.loads((args.rag_dir/'chunks_summary.json').read_text(encoding='utf-8'))
    assert all(digest(args.rag_dir/name) == sha for name, sha in summary['artifacts'].items())
    assert before == digest(root/'data/rag/chunks.jsonl')
    result = {'evaluated_at_kst': datetime.now(timezone(timedelta(hours=9))).isoformat(),
        'scope': suite['scope'], 'model': meta, 'queries_sha256': digest(args.queries),
        'query_forms': len(queries), 'metrics': metrics, 'results': rows,
        'query_encoding_seconds': round(query_seconds, 3), 'local_only': True,
        'new_ocr_vlm_calls': 0, 'generated_answers': 0, 'active_chunks_unchanged': True,
        'artifact_hashes': {name: digest(args.rag_dir/name) for name in ('chunks.jsonl', 'emb/bge-m3/doc.npy', 'emb/bge-m3/meta.json', 'emb/bge-m3/chunk_ids.json')}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'query_forms': len(queries), 'metrics': metrics, 'out': str(args.out)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
