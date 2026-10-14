#!/usr/bin/env python3
"""Retrieval that keeps clinical studies apart (docs/temp/rag/RAG_STUDY_CONTEXT_PLAN.md step 5, 작업 라).

Chunks carry the studies they are about (rag/study_context.py, built into chunks.jsonl). Given the
studies a question is about (rag/plan_query.py), four ways to use them:

A  header only: plain hybrid search; the study is only in the chunk headers the index was built from.
B  demote: search as A, then move chunks that are about other studies only behind the rest.
C  filter with fallback: search only chunks about the asked studies or about no study; when that gives
   fewer than k pages, the rest follow in A's order.
D  per study: for several studies, search each as in C and interleave the pages study by study.
E  bounded preference: search the whole allowed corpus; boost exact study candidates by at most
   15% and related context by 5%. Heuristic tags never exclude or demote source text.

A chunk is "about other studies only" when it names studies and none of them is an asked study, a
part or parent of one, or an analysis pooled across one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study_registry as sr  # noqa: E402
from search import dense_hits, rrf, to_pages, literal_caption_hits  # noqa: E402

METHODS = ["A", "B", "C", "D", "E"]


def related(keys):
    reg = sr.load()
    out = set()
    for k in keys:
        out |= sr.family(k)
    out |= {sr.top(k) for k in out}
    for k, s in reg.items():
        if s["kind"] == "cross_study_analysis":
            covered = {sr.top(c) for c in s["covers"]} | set(s["covers"])
            if k in out:
                out |= covered
            elif covered & out:
                out.add(k)
    return out


def other_only(chunk, wanted):
    return bool(chunk.get("studies")) and not (set(chunk["studies"]) & wanted)


def _hits(qv, bm_text, idx, chunks, vec, bm, use_dense):
    b = bm.hits(bm_text, idx)
    return rrf([dense_hits(qv, vec, idx), b]) if use_dense else b


def preference(keys, targets):
    """Exact parts/analyses are distinct targets; their relatives supply context only."""
    if set(keys) & set(targets):
        return 1.15
    return 1.05 if set(keys) & related(targets) else 1.0


def adaptive_hits(qv, bm_text, idx, chunks, vec, bm, studies, doc_groups=None, use_dense=True, topic=None):
    """Calibrated rank fusion with bounded metadata hints and an always-present broad candidate pool."""
    rankings = [bm.hits(bm_text, idx)]
    if use_dense:
        rankings.append(dense_hits(qv, vec, idx))
    captions = literal_caption_hits(bm_text, idx, chunks)
    if captions:
        rankings.append(captions)
    from rag.clinical_retrieval import rank as clinical_rank, cues
    clinical=clinical_rank(bm_text,idx,chunks,studies,topic)
    if clinical:
        rankings.append(clinical)
    hits = rrf(rankings)
    out = []
    for score, i in hits:
        c = chunks[i]
        boost = preference(c.get("studies", []), studies)
        if doc_groups and c["doc_group"] in doc_groups:
            boost *= 1.1
        # This preference requires printed clinical qualifiers, not a guessed
        # study label. The original broad rankings remain in the candidate pool.
        if cues(bm_text,c,studies,topic):boost*=1.7
        out.append((score * boost, i))
    return sorted(out, reverse=True)


def search(qv, bm_text, idx, chunks, vec, bm, studies, method, k=10, use_dense=True):
    """Pages (as rag/search.py to_pages) for one search unit."""
    if method == "E":
        return to_pages(adaptive_hits(qv, bm_text, idx, chunks, vec, bm, studies, use_dense=use_dense), chunks, k)
    if not studies or method == "A":
        return to_pages(_hits(qv, bm_text, idx, chunks, vec, bm, use_dense), chunks, k)
    if method == "D" and len(studies) > 1:
        per = [search(qv, bm_text, idx, chunks, vec, bm, [s], "C", k, use_dense) for s in studies]
        out, seen = [], set()
        for rank in range(k):
            for pages in per:
                if rank < len(pages):
                    key = (pages[rank]["document_id"], pages[rank]["pdf_page"])
                    if key not in seen:
                        seen.add(key)
                        out.append(pages[rank])
        return out[:k]
    wanted = related(studies)
    if method == "B":
        # Pages are ordered by score, so demoted chunks get a score below every kept one.
        hits = [(s - 1e6 if other_only(chunks[i], wanted) else s, i) for s, i in _hits(qv, bm_text, idx, chunks, vec, bm, use_dense)]
        return to_pages(hits, chunks, k)
    # C (and D with one study)
    keep = [i for i in idx if not other_only(chunks[i], wanted)]
    pages = to_pages(_hits(qv, bm_text, keep, chunks, vec, bm, use_dense), chunks, k)
    if len(pages) < k:
        seen = {(p["document_id"], p["pdf_page"]) for p in pages}
        rest = [p for p in to_pages(_hits(qv, bm_text, idx, chunks, vec, bm, use_dense), chunks, k * 2)
                if (p["document_id"], p["pdf_page"]) not in seen]
        pages += rest[: k - len(pages)]
    return pages
