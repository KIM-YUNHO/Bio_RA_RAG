#!/usr/bin/env python3
"""Search over the chunks: dense vectors, BM25 keywords, or both fused; then merge identical content and aggregate to pages.

Duplicates are handled here, not at indexing: hits whose text is identical (same content_hash) are
merged into one result, the copy from the later-dated document represents the group, and the other
locations are listed in `also_in`. Results are unreviewed page evidence, not answers.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rag.index.embed_index import DEFAULT_MODEL, MODELS, RAG, Embedder, load_chunks  # noqa: E402

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?")
# Words that carry no retrieval signal in these documents; kept short on purpose.
STOPWORDS = {"the", "of", "and", "in", "to", "for", "by", "was", "is", "are", "with", "per", "at", "on", "or", "be", "an", "as", "a"}
VISUAL_REFERENCE = re.compile(r'\b(Table|Figure|Fig\.?)\s*(\d+(?:[.-]\d+)*(?:[A-Z])?)(?!\w|\.\d|-\d)', re.I)


def visual_references(text):
    return {(('table' if m[1].casefold() == 'table' else 'figure'), m[2].upper())
            for m in VISUAL_REFERENCE.finditer(unicodedata.normalize('NFKC', text))}


def literal_caption_hits(query, idx, chunks):
    """An explicit printed identifier adds candidates; it never removes broad hits."""
    wanted = visual_references(query)
    if not wanted:
        return []
    hits = []
    for i in idx:
        caption = (chunks[i].get('structure') or {}).get('caption') or ''
        match = VISUAL_REFERENCE.match(unicodedata.normalize('NFKC', caption).lstrip())
        if match and visual_references(match[0]) & wanted:
            hits.append((1.0, int(i)))
    return hits


def load_index(model_key=DEFAULT_MODEL, rag_dir=None, vector_store=False):
    # BIOSIM_SEARCH_BACKEND=postgres reads the chunks and vectors of a release loaded by storage/rag_loader.py (same values,
    # same order as the files). =pgvector does the same, except that a caller passing vector_store=True gets the vectors as
    # a pgvector store (storage.rag_index.PgVectors) searched in the database. A folder that is not a loaded release is
    # read from files as before.
    backend = os.environ.get("BIOSIM_SEARCH_BACKEND")
    if backend in ("postgres", "pgvector") and rag_dir is not None and model_key == "bge-m3":
        from storage.rag_index import load_from_database
        loaded = load_from_database(rag_dir, store=vector_store and backend == "pgvector")
        if loaded is not None:
            return loaded
        print(f"BIOSIM_SEARCH_BACKEND={backend}: {rag_dir} is not a loaded release; reading files", file=sys.stderr)
    chunks, sha = load_chunks(rag_dir)
    out = Path(rag_dir or RAG) / "emb" / model_key
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    ids = json.loads((out / "chunk_ids.json").read_text(encoding="utf-8"))
    if meta["chunks_sha256"] != sha or ids != [c["chunk_id"] for c in chunks]:
        raise SystemExit(f"Vectors for {model_key} were built from a different chunks.jsonl; run embed_index.py again.")
    return chunks, np.load(out / "doc.npy"), meta


def filter_idx(chunks, product=None, doc_group=None, jurisdiction=None,
               include_nondefault=False, include_evaluation=False):
    """Indices of chunks that pass the filters. Each filter takes a value or a list of values."""
    def ok(c, field, wanted):
        return wanted is None or c[field] in ([wanted] if isinstance(wanted, str) else wanted)

    def eligible(c):
        meta = c.get('document_metadata') or {}
        if 'default_search' in meta and type(meta['default_search']) is not bool:
            return False
        from rag.retrieval.service_scope import default_search
        if not default_search(c) and not include_nondefault:
            return False
        if meta.get('evaluation_cohort') not in (None, 'development') and not include_evaluation:
            return False
        if (c.get('representation') or {}).get('retrieval_eligible') is False:
            return False
        return True
    return np.array([i for i, c in enumerate(chunks) if eligible(c) and ok(c, "product_id", product) and ok(c, "doc_group", doc_group) and ok(c, "jurisdiction", jurisdiction)], dtype=int)


def dense_hits(query_vec, vec, idx, top_n=50):
    """[(score, chunk index)] of the best rows in idx by inner product; vec is the matrix or a pgvector store."""
    if not len(idx):
        return []
    if hasattr(vec, "top"):
        return vec.top(query_vec, idx, top_n)
    scores = vec[idx] @ query_vec
    return [(float(scores[j]), int(idx[j])) for j in np.argsort(-scores)[:top_n]]


def similarity(vec, rows, query_vec):
    """{chunk index: inner product with the query} for a few rows; one database query when vec is a pgvector store."""
    if hasattr(vec, "scores"):
        return vec.scores(rows, query_vec)
    return {int(i): float(vec[i] @ query_vec) for i in rows}


def max_similarity(vec, rows, query_vecs):
    """Row -> best inner product over several queries. A store scores all `rows` once, on the first call."""
    if not hasattr(vec, "scores"):
        return lambda i: max(float(vec[i] @ q) for q in query_vecs)
    best = {}
    def score(i):
        if not best:
            for q in query_vecs:
                for row, s in vec.scores(rows, q).items():
                    best[row] = max(s, best.get(row, s))
        return best[i] if i in best else max(similarity(vec, [i], q)[int(i)] for q in query_vecs)
    return score


class Bm25:
    """Keyword search over header + text with SQLite FTS5, held in memory."""

    def __init__(self, chunks, *, thread_safe=False):
        # Opt-in callers must serialize access. Default ownership remains one
        # thread; RA service caches are guarded by their request/search locks.
        self.db = sqlite3.connect(":memory:", check_same_thread=not thread_safe)
        self.db.execute("create virtual table t using fts5(body, tokenize='unicode61')")
        self.db.executemany("insert into t(rowid, body) values (?, ?)", ((i, c["header"] + "\n" + c["text"]) for i, c in enumerate(chunks)))

    @staticmethod
    def to_match(query):
        terms = [t for t in TOKEN.findall(query) if len(t) > 1 and t.lower() not in STOPWORDS]
        # One-digit identifiers are useful only with their explicit visual label.
        # FTS phrases distinguish Table 7 from incidental numeric cells.
        phrases = [f'{"Table" if kind == "table" else "Figure"} {identifier}'
                   for kind, identifier in sorted(visual_references(query))]
        return " OR ".join('"' + t + '"' for t in dict.fromkeys([*terms, *phrases]))

    def hits(self, query, idx, top_n=50):
        match = self.to_match(query)
        if not match or not len(idx):
            return []
        allowed = set(int(i) for i in idx)
        out = []
        for rowid, score in self.db.execute("select rowid, bm25(t) from t where t match ? order by bm25(t)", (match,)):
            if rowid in allowed:
                out.append((-float(score), rowid))
                if len(out) >= top_n:
                    break
        return out


def rrf(rankings, k=60, top_n=50, weights=None):
    """Reciprocal rank fusion of several [(score, chunk index)] lists, optionally weighted per list."""
    fused = {}
    for n, ranking in enumerate(rankings):
        w = 1.0 if weights is None else weights[n]
        for rank, (_, i) in enumerate(ranking, 1):
            fused[i] = fused.get(i, 0.0) + w / (k + rank)
    return sorted(((s, i) for i, s in fused.items()), reverse=True)[:top_n]


def to_pages(hits, chunks, top_k=5, collapse=True):
    """Merge identical text, then aggregate the remaining chunk hits to pages (best chunk score per page)."""
    groups = {}
    for score, i in hits:
        c = chunks[i]
        # Identical wording in different products/agencies does not make interchangeable evidence.
        identity = (c["product_id"], c["jurisdiction"], c["content_hash"]) if collapse else c["chunk_id"]
        groups.setdefault(identity, []).append((score, c))
    merged = []
    for members in groups.values():
        rep = max(members, key=lambda m: (m[1]["doc_date"] or "", m[1]["document_id"], m[0]))
        others = sorted({(m[1]["document_id"], m[1]["pdf_page"]) for m in members} - {(rep[1]["document_id"], rep[1]["pdf_page"])})
        locations = sorted({(m[1]["document_id"], m[1].get("version_id"), m[1]["pdf_page"]) for m in members}, key=str)
        merged.append((max(m[0] for m in members), rep[1], others, locations, [m[1] for m in members]))
    pages = {}
    for score, c, others, locations, represented in sorted(merged, key=lambda m: -m[0]):
        p = pages.setdefault((c["document_id"], c.get("version_id"), c["pdf_page"]), {
            "score": round(score, 4), "document_id": c["document_id"], "version_id": c.get("version_id"), "pdf_page": c["pdf_page"],
            "source_url": c.get("source_url"), "evidence_status": "unreviewed_retrieval",
            "locator_type": c["locator_type"], "product_id": c["product_id"], "jurisdiction": c["jurisdiction"],
            "doc_group": c["doc_group"], "document_type": c["document_type"], "doc_date": c["doc_date"],
            "title": c["title"], "section": c["section"], "best_chunk": c["chunk_id"], "source_kinds": [],
            "redaction_markers": 0, "also_in": [], "locations": [], "excerpt": c["text"][:200],
            "study_candidates": [], "study_attribution_status": "heuristic_unverified",
        })
        if c["source_kind"] not in p["source_kinds"]:
            p["source_kinds"].append(c["source_kind"])
        p["redaction_markers"] += c["redaction_markers"]
        p["also_in"] = sorted(set(map(tuple, p["also_in"])) | set(others))
        p["locations"] = sorted(set(map(tuple, p["locations"])) | set(locations), key=str)
        p["study_candidates"] = sorted(set(p["study_candidates"]) | set(c.get("studies", [])))
        for evidence_chunk in represented:
            if evidence_chunk.get("schema_version") != "rag-chunk-3":
                continue
            # Preserve per-chunk restrictions: page aggregation must not turn
            # a caption fallback into an approved visual description.
            p.setdefault("representation_evidence", []).append({
                "chunk_id": evidence_chunk["chunk_id"],
                "document_id": evidence_chunk["document_id"], "version_id": evidence_chunk.get("version_id"),
                "pdf_page": evidence_chunk["pdf_page"], "representation": evidence_chunk["representation"],
                "document_metadata": evidence_chunk.get('document_metadata', {}),
                "source_refs": evidence_chunk["provenance"]["source_refs"],
                "visual_parent": {key: evidence_chunk["structure"].get(key) for key in (
                    "figure_id", "table_id", "caption", "member_unit_ids",
                    "context_unit_ids", "context_roles", "figure_labels", "association_status", "continuation_status")},
            })
    return sorted(pages.values(), key=lambda p: -p["score"])[:top_k]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True, help="English query (query translation is a separate step)")
    p.add_argument("--mode", choices=["dense", "bm25", "hybrid"], default="hybrid")
    p.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODELS), help="Embedding model; defaults to BGE-M3")
    p.add_argument("--rag-dir", type=Path, default=RAG, help="Active or candidate chunk/index directory")
    p.add_argument("--product", help="Catalog product ID, including newly imported products")
    p.add_argument('--include-nondefault', action='store_true', help='Explicit research search of labels/correspondence excluded from default search')
    p.add_argument('--include-evaluation', action='store_true', help='Explicit offline evaluation search; protected chunks stay excluded by default')
    p.add_argument("--group", help="doc_group, e.g. assessment, label, protocol")
    p.add_argument("--jurisdiction", choices=["EU", "US", "JP", "CA", "sponsor"])
    p.add_argument("--local-only", action="store_true", help="Use cached embedding model files only")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--no-collapse", action="store_true", help="Show identical copies as separate results")
    p.add_argument('--visual-context', action='store_true', help='Restore linked visual parents and source sentences under the current filters')
    p.add_argument('--rewrite-korean', action='store_true', help='Translate with local Qwen; use original query if literal checks fail')
    p.add_argument('--preprocess-config', type=Path, default=Path('configs/preprocess.local.json'))
    a = p.parse_args()
    if a.local_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.stdout.reconfigure(encoding="utf-8")
    if a.mode != "bm25" and not a.model:
        p.error("--model is required for dense and hybrid")
    rewrite_result = None
    search_query = a.query
    if a.rewrite_korean:
        from rag.retrieval.local_query import rewrite
        from preprocess.hybrid_config import load_config
        rewrite_result = rewrite(a.query, load_config(a.preprocess_config).qwen)
        search_query = rewrite_result['query']
    rankings = []
    if a.mode != "bm25":
        chunks, vec, meta = load_index(a.model, a.rag_dir)
        idx = filter_idx(chunks, a.product, a.group, a.jurisdiction, a.include_nondefault, a.include_evaluation)
        rankings.append(dense_hits(Embedder(a.model, local_only=a.local_only).encode_queries([search_query])[0], vec, idx))
    else:
        chunks, _ = load_chunks(a.rag_dir)
        idx = filter_idx(chunks, a.product, a.group, a.jurisdiction, a.include_nondefault, a.include_evaluation)
    if a.mode != "dense":
        rankings.append(Bm25(chunks).hits(search_query, idx))
    hits = rankings[0] if len(rankings) == 1 else rrf(rankings)
    results = to_pages(hits, chunks, a.top_k, collapse=not a.no_collapse)
    if a.visual_context:
        from rag.index.preprocessed_chunks import related_visual_contexts
        by_id = {c['chunk_id']: c for c in chunks}
        for result in results:
            result['visual_contexts'] = related_visual_contexts(a.rag_dir, result['best_chunk'],
                a.include_nondefault, a.include_evaluation) if by_id[result['best_chunk']].get('source_kind') == 'preprocessed' else []
    print(json.dumps({"mode": a.mode, "model": a.model, "query": a.query, "result_status": "unreviewed_page_search",
                      **({'query_rewrite': rewrite_result} if rewrite_result else {}),
                      "results": results}, ensure_ascii=False, indent=2))
