#!/usr/bin/env python3
"""Compare BGE-M3 chunk sizes against the active corpus without replacing it.

Uses existing cached English queries, relevance labels and study interpretation.
No LLM calls or model downloads. Relevance labels describe the existing PoC only.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))
sys.path.insert(0, str(ROOT / "eval"))
import numpy as np
import torch
import build_chunks as bc
from embed_index import DEFAULT_MODEL, MODELS, RAG, Embedder
from search import Bm25, dense_hits, filter_idx, load_index, rrf, to_pages
from eval_study_context import load_page_studies, page_class, study_family
from understand_query import resolve_cached
from plan_query import plan
import study_search

AGENCIES = ("EU", "US", "CA")


def jobs_and_labels():
    with open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig") as f:
        base = {q["query_id"]: q for q in csv.DictReader(f) if q["type"] == "retrieval"}
    tr = json.loads((RAG / "cache/translations_v0.1_openai-gpt-4o-mini.json").read_text(encoding="utf-8"))["items"]
    cache = json.loads((RAG / "cache/understanding_v0.1_openai-gpt-4o-mini.json").read_text(encoding="utf-8"))
    jobs = []

    def add(qid, kind, prod, components, form, dense_text, bm_text):
        cached = cache[f"{qid}|ko_a" if kind == "base" else f"{qid}|ko"]
        u = resolve_cached(cached["source"], cached)
        wanted = sorted({k for unit in plan(u) for k in unit["studies"]})
        jobs.append({"query_id": qid, "kind": kind, "product": prod, "components": components,
                     "form": form, "dense_text": dense_text, "bm_text": bm_text, "studies": wanted})

    for qid, q in base.items():
        add(qid, "base", q["product_id"], [qid], "en", q["en_ref"], q["en_ref"])
        for src, form in (("ko_a", "ko_a_translated"), ("ko_b", "ko_b_translated")):
            t = tr[f"{qid}|{src}|glossary"]
            add(qid, "base", q["product_id"], [qid], form, t["english_query"], t["english_query"] + " " + " ".join(t["keywords"]))
    with open(ROOT / "eval/queries_study_v0.1.csv", encoding="utf-8-sig") as f:
        for q in csv.DictReader(f):
            if q["kind"] != "indirect":
                continue
            t = tr[f"{q['query_id']}|ko|glossary"]
            for form, dense, bm in (("en", q["en"], q["en"]),
                                   ("ko_a_translated", t["english_query"], t["english_query"] + " " + " ".join(t["keywords"]))):
                add(q["query_id"], "indirect", q["product_id"], q["components"].split(";"), form, dense, bm)
    return jobs, json.loads((RAG / "eval/qrels_v0.1.json").read_text(encoding="utf-8")), load_page_studies()


def evaluate(chunks, vec, jobs, qv, labels, page_labels):
    bm = Bm25(chunks)
    indices = {(prod, ag): filter_idx(chunks, prod, "assessment", ag) for prod in {j["product"] for j in jobs} for ag in AGENCIES}
    values, per_query = collections.defaultdict(list), []
    for qi, job in enumerate(jobs):
        wanted_family = {k for s in job["studies"] for k in study_family(s)}
        for agency in AGENCIES:
            components = [c for c in job["components"] if labels["status"][f"{c}|{agency}"]["status"] in ("reported", "partial")]
            if not components:
                continue
            rel = {}
            for component in components:
                for key, val in labels["qrels"][f"{component}|{agency}"].items():
                    rel[key] = max(rel.get(key, 0), val["grade"])
            idx = indices[(job["product"], agency)]
            dense = dense_hits(qv[qi], vec, idx)
            keywords = bm.hits(job["bm_text"], idx)
            rankings = {"bm25": keywords, "dense": dense, "hybrid/A": rrf([dense, keywords])}
            pages_by_method = {name: to_pages(hits, chunks, 10) for name, hits in rankings.items()}
            pages_by_method["hybrid/B"] = study_search.search(qv[qi], job["bm_text"], idx, chunks, vec, bm, job["studies"], method="B", k=10)
            for method, pages in pages_by_method.items():
                grades = [max(rel.get(f"{d}|{p}", 0) for d, p in [(pg["document_id"], pg["pdf_page"]), *pg["also_in"]]) for pg in pages]
                rank = next((i + 1 for i, grade in enumerate(grades) if grade == 2), None)
                dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades[:10]))
                ideal = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(sorted(rel.values(), reverse=True)[:10])) or 1
                classes = collections.Counter(page_class(pg, wanted_family, page_labels) for pg in pages[:5]) if wanted_family else collections.Counter()
                key = (method, job["kind"], job["form"], agency)
                values[key].append((rank, dcg / ideal, classes, len(pages[:5])))
                per_query.append({"query_id": job["query_id"], "kind": job["kind"], "form": job["form"], "agency": agency,
                                  "method": method, "first_rank": rank, "ndcg@10": dcg / ideal,
                                  "top5_classes": dict(classes), "top5_pages": [(p["document_id"], p["pdf_page"]) for p in pages[:5]]})
    rows = []
    for (method, kind, form, agency), vals in values.items():
        counts = sum((v[2] for v in vals), collections.Counter())
        page_count = sum(v[3] for v in vals)
        rows.append({"method": method, "kind": kind, "form": form, "agency": agency, "n": len(vals),
                     **{f"s@{k}": sum(v[0] is not None and v[0] <= k for v in vals) for k in (1, 5, 10)},
                     "ndcg@10": round(sum(v[1] for v in vals) / len(vals), 4),
                     "other_study_share@5": round(counts["other"] / page_count, 4) if page_count else 0,
                     "labelled_page_counts@5": dict(counts)})
    bm.db.close()
    return {"summary": rows, "per_query": per_query}


def verify_sources(chunks):
    """Resolve every source span and reassemble the emitted text exactly."""
    pages_by_path, checked = {}, 0
    with sqlite3.connect(f"file:{(ROOT / 'data/collection.sqlite').as_posix()}?mode=ro", uri=True) as db:
        ocr = {(d, v, p, r): text or "" for d, v, p, r, text in db.execute("select document_id, version_id, pdf_page, region, text from ocr_regions")}
    for c in chunks:
        prov = c["provenance"]
        path = prov["parsed_path"]
        if path not in pages_by_path:
            raw = (ROOT / path).read_bytes()
            pages_by_path[path] = (hashlib.sha256(raw).hexdigest(), {p["pdf_page"]: p for p in map(json.loads, raw.decode("utf-8").splitlines()) if p})
        digest, pages = pages_by_path[path]
        if digest != prov["parsed_sha256"]:
            raise ValueError(f"Parsed artifact changed: {path}")
        page = pages[c["pdf_page"]]
        blocks = {b["block_id"]: b for b in page["blocks"]}
        fragments = []
        ocr_text = ocr.get((c["document_id"], c["version_id"], c["pdf_page"], c["region"]))
        if ocr_text is not None and hashlib.sha256(ocr_text.encode("utf-8")).hexdigest() != prov["ocr_text_sha256"]:
            raise ValueError(f"OCR source changed: {c['chunk_id']}")
        for span in prov["source_spans"]:
            raw = blocks[span["block_id"]]["text"] if span["block_id"] else page["text"] if c["source_kind"] == "native" else ocr_text
            if raw is None:
                raise ValueError(f"Missing source unit: {c['chunk_id']}")
            lines = [bc.clean_line(t) for t in raw.split("\n")[span["line_start"]:span["line_end"] + 1]]
            cleaned = span["joiner"].join(t for t in lines if t)
            if span["normalized_end"] > len(cleaned):
                raise ValueError(f"Source span out of bounds: {c['chunk_id']}")
            fragments.append(cleaned[span["normalized_start"]:span["normalized_end"]])
            checked += 1
        if "\n".join(fragments) != c["text"]:
            raise ValueError(f"Emitted text differs from source spans: {c['chunk_id']}")
    return {"chunks_checked": len(chunks), "spans_checked": checked, "status": "passed"}


def save_vectors(folder, chunks, summary, vec, emb, elapsed):
    out = folder / "emb" / DEFAULT_MODEL
    out.mkdir(parents=True, exist_ok=True)
    norm_error = float(np.abs(np.linalg.norm(vec, axis=1) - 1).max())
    meta = {"model_key": DEFAULT_MODEL, **{k: v for k, v in MODELS[DEFAULT_MODEL].items() if k != "processor_kwargs"},
            "revision": emb.revision, "dim": vec.shape[1], "n_chunks": len(chunks), "similarity": "cosine",
            "chunks_sha256": summary["chunks_sha256"], "chunk_schema_version": bc.CONFIG["schema_version"],
            "chunking_profile_id": summary["profile_id"], "truncated_inputs": 0,
            "non_finite_values": int((~np.isfinite(vec)).sum()), "device": emb.device, "embed_seconds": round(elapsed, 2),
            "dtype_model": "float16" if emb.half else "float32", "max_norm_error": norm_error,
            "normalization_atol": 0.001,
            "gpu_peak_mib": int(torch.cuda.max_memory_allocated() // 2**20) if emb.device == "cuda" else None,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z")}
    if meta["non_finite_values"] or norm_error > meta["normalization_atol"]:
        raise ValueError("Non-finite or unnormalized document vectors")
    np.save(out / "doc.npy", vec)
    (out / "chunk_ids.json").write_text(json.dumps([c["chunk_id"] for c in chunks]), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def finalize_comparison(result):
    """Record source/index checks and cell-level regressions; never promote automatically."""
    key = lambda row: (row["kind"], row["form"], row["agency"])
    baseline = {key(row): row for row in result["baseline"]["summary"] if row["method"] == "hybrid/B"}
    reviews = []
    for candidate in result["candidates"]:
        rows = [row for row in candidate["summary"] if row["method"] == "hybrid/B"]
        failures = []
        for row in rows:
            previous = baseline[key(row)]
            for field, tolerance in (("s@5", 0), ("other_study_share@5", 0.02)):
                failed = row[field] < previous[field] if field == "s@5" else row[field] > previous[field] + tolerance
                if failed:
                    failures.append({"kind": row["kind"], "form": row["form"], "agency": row["agency"], "check": field,
                                     "baseline": previous[field], "candidate": row[field]})
        chunks, vec, _ = load_index(rag_dir=candidate["rag_dir"])
        norm_error = float(np.abs(np.linalg.norm(vec, axis=1) - 1).max())
        checks = {"sha_and_ids": "passed", "shape": list(vec.shape), "finite": bool(np.isfinite(vec).all()),
                  "normalization_atol": 0.001, "max_norm_error": norm_error, "normalized": norm_error <= 0.001}
        if vec.shape != (len(chunks), 1024) or not checks["finite"] or not checks["normalized"]:
            raise ValueError(checks)
        candidate["vector_integrity"] = checks
        reviews.append({"body_size": candidate["body_size"], "nonregression_passed": not failures, "failures": failures,
                        "total_s@5": sum(row["s@5"] for row in rows), "total_scored": sum(row["n"] for row in rows)})
    active_sha = hashlib.sha256((RAG / "chunks.jsonl").read_bytes()).hexdigest()
    if active_sha != result["baseline"]["index_meta"]["chunks_sha256"]:
        raise ValueError("Active chunks changed during evaluation")
    result["promotion_review"] = {
        "criteria": "Every hybrid/B kind/form/agency cell retains baseline S@5; other-study share increases by at most 2 percentage points. Engineering guard, not a statistical significance test.",
        "candidates": reviews, "active_index_changed": False, "active_chunks_sha256": active_sha,
    }
    result["evaluation_inputs_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        "eval/queries_v0.1.csv", "eval/queries_study_v0.1.csv", "eval/study_pages_v0.1.csv", "data/rag/eval/qrels_v0.1.json",
        "data/rag/cache/translations_v0.1_openai-gpt-4o-mini.json", "data/rag/cache/understanding_v0.1_openai-gpt-4o-mini.json")}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sizes", type=int, nargs="+", default=[512, 768, 1024])
    p.add_argument("--output-dir", type=Path, default=RAG / "candidates/chunking_2026-10-06")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if any(n < 256 or n >= bc.CONFIG["embedding_max_tokens"] for n in a.sizes):
        p.error("Each body size must be between 256 and model capacity minus one")
    jobs, labels, page_labels = jobs_and_labels()
    emb = Embedder(local_only=True)
    # Reuse the same encoded query vectors across the baseline and every candidate.
    qv = emb.encode_queries([j["dense_text"] for j in jobs])
    a.output_dir.mkdir(parents=True, exist_ok=True)
    chunks, vec, meta = load_index()
    result = {"created_at": "2026-10-06 (Asia/Seoul)", "model": DEFAULT_MODEL, "query_jobs": len(jobs),
              "evaluation_scope": "Cached PoC questions, assessment documents and existing relevance labels; no answer accuracy claims",
              "baseline": {"rag_dir": str(RAG), "index_meta": meta, **evaluate(chunks, vec, jobs, qv, labels, page_labels)}, "candidates": []}
    counter = bc.Counter(bc.CONFIG["token_counter"], local_only=True)
    result["baseline"]["actual_bge_tokens"] = bc.quantiles(counter.embedding(c["header"], c["text"]) for c in chunks)
    (a.output_dir / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Baseline evaluated", flush=True)
    for size in a.sizes:
        cfg = {**bc.CONFIG, "max_tokens": size, "target_tokens": int(size * 0.75)}
        folder = a.output_dir / f"body_{size}"
        print(f"Building body={size}", flush=True)
        chunks, summary = bc.build(cfg, local_only=True)
        source_check = verify_sources(chunks)
        bc.write_build(chunks, summary, folder)
        print(f"Embedding {len(chunks)} chunks; source spans passed", flush=True)
        emb.truncated = 0
        if emb.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        started = time.monotonic()
        vec = emb.encode_docs(chunks)
        metadata = save_vectors(folder, chunks, summary, vec, emb, time.monotonic() - started)
        metadata["gpu_peak_mib"] = int(torch.cuda.max_memory_allocated() // 2**20) if emb.device == "cuda" else None
        evaluated = evaluate(chunks, vec, jobs, qv, labels, page_labels)
        candidate = {"body_size": size, "rag_dir": str(folder), "build_summary": summary,
                     "source_check": source_check, "index_meta": metadata, **evaluated}
        result["candidates"].append(candidate)
        (folder / "retrieval_comparison.json").write_text(json.dumps(evaluated, ensure_ascii=False, indent=1), encoding="utf-8")
        (a.output_dir / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Evaluated body={size}", flush=True)
    finalize_comparison(result)
    (a.output_dir / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(str((a.output_dir / "comparison.json").resolve()), flush=True)


if __name__ == "__main__":
    main()
