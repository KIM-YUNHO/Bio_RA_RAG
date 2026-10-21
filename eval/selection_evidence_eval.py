"""Re-evaluate the selected retrieval setup with frozen current-planner inputs.

Outputs live in a new evaluation folder. Production code, releases and service
settings are not changed. A saved planner response is reused only when its full
OpenAI request hash (messages, schema, model and output settings) matches today's
planner request. The old version label alone is never treated as equivalence.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import math
import statistics
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GOLD = ROOT / "data/runs/retrieval_gold_2026-10-16/gold.json"
RELEASE = ROOT / "data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release"
GUIDELINES = ROOT / "data/guidelines/ra_guidelines_native_2026-10-12_v2"
KST = timezone(timedelta(hours=9))
EVAL_VERSION = "selected-retrieval-comparison-1"
PROFILES = {
    "dense_channel": {"channel": "dense", "strip_slot_terms": True},
    "bm25_channel": {"channel": "bm25", "strip_slot_terms": True},
    "hybrid_unstripped": {"channel": "hybrid", "strip_slot_terms": False},
    "hybrid_current": {"channel": "hybrid", "strip_slot_terms": True},
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CaptureRequest(Exception):
    pass


class RequestProbe:
    provider = "openai"
    model = "gpt-5.6-luna"

    def __call__(self, messages, json_schema=None, provider=None):
        schema = deepcopy(json_schema)
        maximum = schema.pop("max_output_tokens", 1600)
        self.payload = {"model": self.model, "messages": deepcopy(messages),
                        "max_completion_tokens": maximum, "reasoning_effort": "none",
                        "response_format": {"type": "json_schema", "json_schema": {**schema, "strict": True}}}
        self.key = digest(self.payload)
        raise CaptureRequest()


class ReplayResponse:
    provider = "openai"
    model = "gpt-5.6-luna"

    def __init__(self, payload, telemetry):
        self.payload = payload
        self.telemetry = telemetry

    def __call__(self, messages, json_schema=None, provider=None):
        probe = RequestProbe()
        try:
            probe(messages, json_schema, provider)
        except CaptureRequest:
            pass
        if probe.payload != self.payload or probe.key != self.telemetry["request_key"]:
            raise ValueError("Cached response does not match the current complete request")
        return {"text": self.telemetry["raw_text"], "telemetry": deepcopy(self.telemetry)}


def make_pipeline():
    from rag.llm import load_env
    load_env()  # Application-owned loading; never print credentials or environment values.
    os.environ["BIOSIM_SEARCH_BACKEND"] = "pgvector"
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    return RAPipeline(RELEASE, None, load_config(ROOT / "configs/preprocess.local.json"), guideline_snapshot=GUIDELINES,
                      research_rounds=0, retrieval_pages=8, chat_client=RequestProbe())


def scope_for(pipe, item, case=None):
    products = case.get("products") if case else None
    agencies = case.get("jurisdictions") if case else None
    selected = [d for d in pipe.documents
                if (not products or d["product_id"] in products or d.get("role") == "guideline")
                and (not agencies or d["jurisdiction"] in agencies)]
    return {"products": sorted({d["product_id"] for d in selected if d["product_id"] is not None}),
            "agencies": sorted({d["jurisdiction"] for d in selected}),
            "selected_products": products, "selected_agencies": agencies,
            "document_scope": False, "documents": pipe.documents}


def interpret(item, client, scope):
    from rag import ra_tasks
    return ra_tasks.interpret(item["question"], client, scope["products"], scope["agencies"],
                              selected_products=scope["selected_products"],
                              selected_agencies=scope["selected_agencies"],
                              document_scope=False, documents=scope["documents"])


def saved_responses():
    matches = {}
    folders = [ROOT / "data/runs/stable_queries_2026-10-16/e2e_qual",
               ROOT / "data/runs/ra_service_reports"]
    for folder in folders:
        for path in sorted(folder.glob("*.json")):
            if path.stat().st_size > 20_000_000:
                continue
            value = read(path)
            value = value.get("report", value)
            if not isinstance(value, dict):
                continue
            for telemetry in value.get("understanding_telemetry", []):
                if (telemetry.get("status") == "completed" and telemetry.get("request_key")
                        and telemetry.get("raw_text") and telemetry.get("model_requested") == "gpt-5.6-luna"):
                    messages = telemetry.get("messages", [])
                    question = next((m["content"] for m in messages if m["role"] == "user"), None)
                    matches.setdefault(question, []).append({"path": str(path.relative_to(ROOT)),
                        "telemetry": telemetry, "case": value.get("run", {}).get("case"),
                        "pipeline_version": value.get("pipeline_version"),
                        "planner_version": value.get("understanding", {}).get("planner_version")})
    return matches


def prepare(out, allow_new=False, only=None):
    from rag import ra_tasks
    from rag.ra_pipeline import VERSION
    pipe = make_pipeline()
    items = read(GOLD)["items"]
    if only:
        items = [i for i in items if i["id"] in only]
    responses = saved_responses()
    manifest = {"created_at_kst": datetime.now(KST).isoformat(), "pipeline_version": VERSION,
                "planner_version": ra_tasks.VERSION, "gold_sha256": file_hash(GOLD),
                "source_hashes": {p: file_hash(ROOT / p) for p in
                    ["rag/ra_tasks.py", "rag/ra_models.py", "rag/ra_retrieval.py", "rag/search.py", "rag/rerank.py"]},
                "new_planner_calls": 0, "items": []}
    for port in (8885, 8775):
        with urlopen(f"http://127.0.0.1:{port}/api/summary", timeout=15) as response:
            live = json.load(response)
        manifest.setdefault("services", []).append({"port": port, **{k: live.get(k) for k in
            ["pipeline_version", "query_strategy", "chunks_sha256", "product_index_chunks", "vector_store"]}})
    try:
        for item in items:
            stem = item["id"].replace(":", "_")
            candidates = responses.get(item["question"], [])
            # For the existing answer set preserve its originally declared UI scope.
            case = next((c["case"] for c in candidates if c.get("case") and
                         "e2e_qual" in c["path"] and c["path"].endswith("_1.json")), None)
            scope = scope_for(pipe, item, case)
            probe = RequestProbe()
            try:
                interpret(item, probe, scope)
            except CaptureRequest:
                pass
            write(out / "planner_requests" / f"{stem}.json", {"request_key": probe.key, "payload": probe.payload})
            record = {"id": item["id"], "source": item["source"], "request_key": probe.key,
                      "ui_products": scope["selected_products"], "ui_agencies": scope["selected_agencies"]}
            saved_plan = out / "plans" / f"{stem}.json"
            if saved_plan.exists():
                prior = read(saved_plan)
                if prior["request_key"] != probe.key:
                    raise ValueError(f"Frozen planner request changed: {item['id']}")
                record.update(status="ready", origin=prior["origin"])
            else:
                match = next((c for c in candidates if c["telemetry"]["request_key"] == probe.key), None)
                if match:
                    u, reply = interpret(item, ReplayResponse(probe.payload, match["telemetry"]), scope)
                    origin = {"kind": "exact_request_hash_replay", "report": match["path"],
                              "original_pipeline_label": match["pipeline_version"],
                              "original_planner_label": match["planner_version"]}
                    write(saved_plan, {"id": item["id"], "request_key": probe.key, "origin": origin, "understanding": u})
                    record.update(status="ready", origin=origin)
                elif allow_new:
                    from rag.openai_chat import EvaluationAPIChat
                    client = EvaluationAPIChat(out / "new_planner" / stem, max_new_calls=1, model="gpt-5.6-luna")
                    client.config.num_ctx = 65536
                    client.case = stem
                    manifest["new_planner_calls"] += 1
                    try:
                        u, reply = interpret(item, client, scope)
                        telemetry = reply["telemetry"]
                        if telemetry["request_key"] != probe.key:
                            raise ValueError("Actual current planner request differs from preflight")
                        origin = {"kind": "new_current_planner", "request_key": probe.key}
                        write(saved_plan, {"id": item["id"], "request_key": probe.key, "origin": origin, "understanding": u})
                        record.update(status="ready", origin=origin)
                    finally:
                        client.close()
                else:
                    record.update(status="needs_current_plan", historical_responses=len(candidates))
            manifest["items"].append(record)
            write(out / "preparation.json", manifest)
            print(item["id"], record["status"], record.get("origin", {}).get("kind", ""), flush=True)
    finally:
        pipe.close()
    return manifest


def query_list(plan):
    from rag import ra_topic_queries
    tasks = plan["tasks"]
    fixed = {t["id"]: ra_topic_queries.for_task(plan.get("source_question", ""), t, len(tasks))
             for t in tasks}
    return list(dict.fromkeys([q for t in tasks for q in t["queries"]] +
                              [q for _, queries in fixed.values() for q in queries]))


def ready_items(out, only=None):
    result = []
    for item in read(GOLD)["items"]:
        if only and item["id"] not in only:
            continue
        path = out / "plans" / f"{item['id'].replace(':', '_')}.json"
        if not path.is_file():
            raise ValueError(f"Current plan missing: {item['id']}; prepare all plans before evaluation")
        result.append((item, read(path)))
    return result


def encode(out, only=None):
    """Freeze CUDA/fp16 query vectors in the same per-item batches as retrieval.

    The encoder is then released before loading the reranker, to share the GPU
    with the two existing services without changing either service.
    """
    import numpy as np
    import torch
    from rag.answer import Embedder
    items = ready_items(out, only)
    folder = out / "query_vectors"
    folder.mkdir(exist_ok=True)
    needed = []
    for item, frozen in items:
        stem = item["id"].replace(":", "_")
        q = query_list(frozen["understanding"])
        meta = folder / f"{stem}.json"
        if meta.exists():
            prior = read(meta)
            if prior["plan_sha256"] != digest(frozen["understanding"]) or prior["queries"] != q:
                raise ValueError("Frozen query vector input changed")
            if (folder / f"{stem}.npy").exists():
                continue
        needed.append((item, frozen, q))
    if not needed:
        return
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required to match the serving query embedding precision")
    started = time.monotonic()
    model = Embedder("bge-m3", local_only=True)
    for item, frozen, queries in needed:
        stem = item["id"].replace(":", "_")
        stamp = time.monotonic()
        vectors = model.encode_queries(queries) if queries else np.empty((0, 1024), dtype=np.float32)
        if vectors.shape != (len(queries), 1024) or not np.isfinite(vectors).all():
            raise ValueError("Invalid frozen query vectors")
        np.save(folder / f"{stem}.npy", vectors)
        write(folder / f"{stem}.json", {"plan_sha256": digest(frozen["understanding"]), "queries": queries,
            "device": model.device, "half": model.half, "revision": model.revision,
            "batch_contract": "one item; same ordered unique queries as production retrieval",
            "seconds": round(time.monotonic() - stamp, 4), "vectors_sha256": file_hash(folder / f"{stem}.npy")})
        print("encoded", item["id"], len(queries), flush=True)
    del model
    import gc
    gc.collect()
    torch.cuda.empty_cache()
    write(out / "query_encoding.json", {"seconds": round(time.monotonic() - started, 3),
        "new_items": len(needed), "device": "cuda", "half": True,
        "note": "Query encoding is frozen before comparison; first-stage timings below exclude it."})


class FrozenEncoder:
    def __init__(self, folder, item_id, frozen):
        import numpy as np
        stem = item_id.replace(":", "_")
        meta = read(folder / f"{stem}.json")
        if meta["plan_sha256"] != digest(frozen["understanding"]):
            raise ValueError("Query vectors refer to another plan")
        vectors = np.load(folder / f"{stem}.npy")
        self.vectors = dict(zip(meta["queries"], vectors))

    def encode_queries(self, queries):
        import numpy as np
        if any(q not in self.vectors for q in queries):
            raise ValueError("Evaluation requested a query absent from the frozen production batch")
        return np.stack([self.vectors[q] for q in queries]) if queries else np.empty((0, 1024), dtype=np.float32)


def capture_pools(corpus, plan, guidelines, documents, spec):
    """Intercept only the production rerank boundary, retaining its exact pools.

    Calling the production retriever with k=8 preserves the statistical-table
    candidate limit. Calling k=40 with rerank off would change that limit.
    Channel ablations disable only candidate lists; common statistical-table
    prioritization, its dense tie-break, filters, roles and final budget remain.
    """
    from rag import ra_retrieval
    captured = {}

    def capture(_corpus, task, hits, k, mode, product=None, agency=None, fuse=False, question=None):
        key = (task["id"], product, agency)
        if key in captured:
            raise ValueError("Duplicate task/product/agency group")
        captured[key] = list(hits)
        return hits[:k]

    with ExitStack() as stack:
        stack.enter_context(patch.object(ra_retrieval, "rerank", capture))
        if spec["channel"] == "bm25":
            stack.enter_context(patch.object(ra_retrieval, "dense_hits", return_value=[]))
        if spec["channel"] == "dense":
            stack.enter_context(patch.object(corpus._ra_bm25, "hits", return_value=[]))
        groups = ra_retrieval.retrieve(corpus, deepcopy(plan), guidelines, documents=documents, k=8,
                                      profile={"strip_slot_terms": spec["strip_slot_terms"]})
    return groups, captured


def evaluate_order(item, groups):
    """Same page/fact-level definitions and weak split as the historical scorer."""
    facts = []
    for fact in item["groups"]:
        pages = {tuple(p) for p in fact["pages"]}
        ranks = [rank for group in groups for rank, chunk in enumerate(group, 1)
                 if (chunk["document_id"], chunk["pdf_page"]) in pages]
        rank = min(ranks) if ranks else None
        facts.append({"label": fact["label"], "weak": fact["weak"], "rank": rank})
    return facts


def metric(facts):
    n = len(facts)
    if not n:
        return {}
    return {"groups": n, **{f"recall@{k}": sum(g["rank"] is not None and g["rank"] <= k for g in facts) / n
                             for k in (1, 4, 8, 16, 40)},
            "mrr": sum(1 / g["rank"] for g in facts if g["rank"] is not None) / n,
            "ndcg@8": sum(1 / math.log2(g["rank"] + 1) for g in facts
                          if g["rank"] is not None and g["rank"] <= 8) / n,
            "pool_ceiling": sum(g["rank"] is not None for g in facts) / n}


def channel_baselines(out, only=None):
    """Pure dense/BM25/hybrid candidates with the statistical-table prior off.

    This is the standalone-channel table. The main evaluation separately keeps
    the service's common statistical prior for channel ablations, so effects of
    removing a candidate channel inside the deployed pipeline remain visible.
    No embedding, reranker or provider call is made in this phase.
    """
    from rag import ra_retrieval
    from rag import ra_statistical_tables
    pipe = make_pipeline()
    corpus = pipe.retrieval_corpus()
    corpus.allowed_documents = {(d["document_id"], d["version_id"]) for d in pipe.documents}
    corpus._ra_bm25 = ra_retrieval.Bm25([
        {**c, "header": " ".join(c.get("structure", {}).get("section_path", [])) + " " +
         (c.get("structure", {}).get("caption") or "")} for c in corpus.chunks], thread_safe=True)
    catalog = corpus.chunks + (pipe.guidelines.chunks if pipe.guidelines else [])
    index = {c["chunk_id"]: i for i, c in enumerate(catalog)}
    rows = []
    try:
        for item, frozen in ready_items(out, only):
            path = out / "channel_rows" / f"{item['id'].replace(':', '_')}.json"
            if path.exists():
                row = read(path)
                if row["plan_sha256"] != digest(frozen["understanding"]):
                    raise ValueError("Pure channel row uses a different plan")
                rows.append(row)
                continue
            corpus._emb = FrozenEncoder(out / "query_vectors", item["id"], frozen)
            row = {"id": item["id"], "source": item["source"], "plan_sha256": digest(frozen["understanding"]), "variants": {}}
            with patch.object(ra_statistical_tables, "hits", return_value=[]):
                for name, spec in PROFILES.items():
                    groups, captured = capture_pools(corpus, frozen["understanding"], pipe.guidelines,
                                                     corpus.allowed_documents, spec)
                    full = []
                    for group in groups:
                        key = (group["task_id"], group.get("product"), group.get("jurisdiction"))
                        pool = captured.get(key)
                        full.append([i for _, i in pool] if pool is not None else
                                    [index[h["chunk"]["chunk_id"]] for h in group["hits"]])
                    facts = evaluate_order(item, [[catalog[i] for i in group] for group in full])
                    row["variants"][name] = {"groups": facts, "orders": full,
                        "metrics": metric([g for g in facts if not g["weak"]])}
            write(path, row)
            rows.append(row)
            print("channels", item["id"], " ".join(f"{k}:{v['metrics'].get('recall@8', 0):.3f}"
                  for k, v in row["variants"].items()), flush=True)
    finally:
        corpus._emb = None
        if hasattr(corpus.vec, "close"):
            corpus.vec.close()
        pipe.close()
    summary = {}
    for scope in ("all", "fact_gold", "qa_chemr", "e2e29"):
        rs = [r for r in rows if scope == "all" or r["source"] == scope]
        summary[scope] = {v: metric([g for r in rs for g in r["variants"][v]["groups"] if not g["weak"]])
                          for v in PROFILES}
    write(out / "channel_baselines.json", {"eval_version": EVAL_VERSION, "items": len(rows), "summary": summary,
        "contract": "Standalone candidate channels; statistical-table prior OFF in all variants; filters, roles, queries and budgets fixed",
        "provider_calls": 0, "gold_sha256": file_hash(GOLD)})
    print(json.dumps(summary["all"], ensure_ascii=False, indent=2), flush=True)


def evaluate(out, only=None, batch_size=4):
    from rag import ra_retrieval
    from rag.rerank import LocalReranker, MODEL, REVISION
    items = ready_items(out, only)
    pipe = make_pipeline()
    corpus = pipe.retrieval_corpus()
    if not hasattr(corpus.vec, "top"):
        raise ValueError("Evaluation must use the same pgvector backend as the services")
    corpus.allowed_documents = {(d["document_id"], d["version_id"]) for d in pipe.documents}
    # Initialize the production product BM25 once, as the service does.
    corpus._ra_bm25 = ra_retrieval.Bm25([
        {**c, "header": " ".join(c.get("structure", {}).get("section_path", [])) + " " +
         (c.get("structure", {}).get("caption") or "")} for c in corpus.chunks], thread_safe=True)
    index = {c["chunk_id"]: i for i, c in enumerate(corpus.chunks)}
    all_chunks = corpus.chunks + (pipe.guidelines.chunks if pipe.guidelines else [])
    global_index = {c["chunk_id"]: i for i, c in enumerate(all_chunks)}
    if len(global_index) != len(all_chunks):
        raise ValueError("Chunk IDs must be globally unique")
    write(out / "chunk_catalog.json", [{k: c.get(k) for k in ["chunk_id", "document_id", "version_id", "pdf_page"]}
                                        for c in all_chunks])
    ranker = LocalReranker(model=MODEL, revision=REVISION, batch_size=batch_size, max_length=1024,
                          cache_path=out / "reranker_scores.jsonl")
    corpus._ra_reranker = ranker
    identity = {"eval_version": EVAL_VERSION, "gold_sha256": file_hash(GOLD),
                "chunks_sha256": corpus.index_meta["chunks_sha256"],
                "profiles": PROFILES, "retrieval_pages": 8, "pool": 40,
                "backend": "pgvector", "reranker_model": MODEL, "reranker_revision": REVISION,
                "reranker_batch": batch_size, "reranker_max_length": 1024,
                "source_hashes": {p: file_hash(ROOT / p) for p in ["rag/ra_tasks.py", "rag/ra_retrieval.py", "rag/rerank.py", "rag/search.py"]},
                "channel_ablation_contract": "Disable candidate channel only; keep statistical-table prior and dense tie-break common",
                "latency_contract": "Frozen query vectors; persistent reranker score cache. Timings are diagnostic, not model latency benchmarks."}
    prior_path = out / "evaluation_identity.json"
    if prior_path.exists() and read(prior_path) != identity:
        raise ValueError("Frozen evaluation identity changed; choose a new output folder")
    write(prior_path, identity)
    tasks_by_item = {}
    try:
        for item, frozen in items:
            path = out / "rows" / f"{item['id'].replace(':', '_')}.json"
            if path.exists():
                prior = read(path)
                if prior["plan_sha256"] != digest(frozen["understanding"]) or prior["identity_sha256"] != digest(identity):
                    raise ValueError("A completed row belongs to another frozen evaluation")
                print("reuse row", item["id"], flush=True)
                continue
            started = time.monotonic()
            plan = frozen["understanding"]
            corpus._emb = FrozenEncoder(out / "query_vectors", item["id"], frozen)
            tasks = {t["id"]: t for t in plan["tasks"]}
            row = {"id": item["id"], "source": item["source"], "question_type": item["question_type"],
                   "question": item["question"], "plan_sha256": digest(plan), "identity_sha256": digest(identity),
                   "variants": {}, "pools": {}, "timings": {}}
            expected_current = None
            for name, spec in PROFILES.items():
                stamp = time.monotonic()
                groups, captured = capture_pools(corpus, plan, pipe.guidelines, corpus.allowed_documents, spec)
                row["timings"][name + ":first_stage"] = round(time.monotonic() - stamp, 5)
                orders = {"none": [], "fquery_rrf": []}
                if name == "hybrid_current":
                    orders.update(ask_rrf=[], fquery_only=[])
                row["pools"][name] = []
                for group in groups:
                    key = (group["task_id"], group.get("product"), group.get("jurisdiction"))
                    pool = captured.get(key, [])
                    if pool:
                        original = [i for _, i in pool]
                        row["pools"][name].append({"group": list(key), "chunks": original})
                        orders["none"].append(original)
                        for suffix, mode, fuse in [("fquery_rrf", "fquery_unit", True)] + (
                                [("ask_rrf", "ask_unit", True), ("fquery_only", "fquery_unit", False)]
                                if name == "hybrid_current" else []):
                            stamp = time.monotonic()
                            before = len(ranker.cache)
                            ranked = ra_retrieval.rerank(corpus, tasks[group["task_id"]], pool, len(pool), mode,
                                product=group.get("product"), agency=group.get("jurisdiction"), fuse=fuse)
                            row["timings"].setdefault(name + ":" + suffix, {"seconds": 0.0, "new_pairs": 0})
                            row["timings"][name + ":" + suffix]["seconds"] += time.monotonic() - stamp
                            row["timings"][name + ":" + suffix]["new_pairs"] += len(ranker.cache) - before
                            orders[suffix].append([i for _, i in ranked])
                    else:
                        unchanged = [global_index[h["chunk"]["chunk_id"]] for h in group["hits"]]
                        row["pools"][name].append({"group": list(key), "chunks": unchanged})
                        for values in orders.values():
                            values.append(unchanged)
                for suffix, ranked_groups in orders.items():
                    facts = evaluate_order(item, [[all_chunks[i] for i in g] for g in ranked_groups])
                    row["variants"][name + ":" + suffix] = {"groups": facts, "orders": ranked_groups,
                        "metrics": metric([g for g in facts if not g["weak"]])}
                if name == "hybrid_current":
                    expected_current = [g[:8] for g in orders["fquery_rrf"]]
                    # The none view must reproduce the top eight of the captured full pool.
                    actual_none = [[global_index[h["chunk"]["chunk_id"]] for h in g["hits"]] for g in groups]
                    if actual_none != [g[:8] for g in orders["none"]]:
                        raise ValueError("Captured pool order differs from production interception")
            # Independently execute the unmodified production retriever, using the
            # same frozen vectors and scored pairs, and require exact top-8 parity.
            actual = ra_retrieval.retrieve(corpus, deepcopy(plan), pipe.guidelines,
                                          documents=corpus.allowed_documents, k=8)
            actual_order = [[global_index[h["chunk"]["chunk_id"]] for h in g["hits"]] for g in actual]
            if actual_order != expected_current:
                write(out / "parity_failure.json", {"id": item["id"], "expected": expected_current, "actual": actual_order})
                raise ValueError("Evaluation current variant differs from the unmodified production retriever")
            row["production_top8_parity"] = True
            row["wall_seconds"] = round(time.monotonic() - started, 3)
            write(path, row)
            print("evaluated", item["id"], " ".join(f"{v}:{r['metrics'].get('recall@8', 0):.3f}"
                  for v, r in row["variants"].items()), "seconds", row["wall_seconds"], flush=True)
    finally:
        corpus._emb = None
        ranker.close()
        if hasattr(corpus.vec, "close"):
            corpus.vec.close()
        pipe.close()


def summarize(out, only=None):
    import numpy as np
    from collections import Counter
    pairs = ready_items(out, only)
    rows = [read(out / "rows" / f"{i['id'].replace(':', '_')}.json") for i, _ in pairs]
    variants = list(rows[0]["variants"])
    summary = {}
    for scope in ["all", "fact_gold", "qa_chemr", "e2e29"]:
        scope_rows = [r for r in rows if scope == "all" or r["source"] == scope]
        summary[scope] = {}
        for variant in variants:
            facts = [g for r in scope_rows for g in r["variants"][variant]["groups"] if not g["weak"]]
            summary[scope][variant] = metric(facts)
    comparisons = [("hybrid_current:none", v) for v in ["dense_channel:none", "bm25_channel:none", "hybrid_unstripped:none"]]
    comparisons += [("hybrid_current:fquery_rrf", v) for v in ["hybrid_current:none", "hybrid_current:ask_rrf", "hybrid_current:fquery_only"]]
    differences = []
    rng = np.random.default_rng(20261019)
    scored_positions = np.array([i for i, r in enumerate(rows)
        if any(not g["weak"] for g in r["variants"][variants[0]]["groups"])])
    draws = scored_positions[rng.integers(0, len(scored_positions), size=(2000, len(scored_positions)))]
    for chosen, base in comparisons:
        n = np.array([sum(not g["weak"] for g in r["variants"][chosen]["groups"]) for r in rows])
        for measure in ["recall@8", "recall@40", "ndcg@8", "mrr"]:
            chosen_values = np.array([r["variants"][chosen]["metrics"].get(measure, 0) for r in rows])
            base_values = np.array([r["variants"][base]["metrics"].get(measure, 0) for r in rows])
            delta = chosen_values - base_values
            boot = (delta[draws] * n[draws]).sum(axis=1) / n[draws].sum(axis=1)
            differences.append({"chosen": chosen, "baseline": base, "metric": measure,
                "difference": float((delta * n).sum() / n.sum()),
                "bootstrap95": [float(x) for x in np.quantile(boot, [.025, .975])],
                "questions_improved": int(((delta > 1e-12) & (n > 0)).sum()),
                "questions_worse": int(((delta < -1e-12) & (n > 0)).sum()),
                "questions_equal": int(((abs(delta) <= 1e-12) & (n > 0)).sum()),
                "questions_without_primary_gold": int((n == 0).sum())})
    provenance = read(out / "preparation.json")
    result = {"eval_version": EVAL_VERSION, "items": len(rows), "summary": summary,
              "paired_question_bootstrap": {"seed": 20261019, "replicates": 2000,
                  "scored_questions": len(scored_positions),
                  "without_primary_gold": [r["id"] for i, r in enumerate(rows) if i not in set(scored_positions)],
                  "comparisons": differences},
              "production_top8_parity": sum(r["production_top8_parity"] for r in rows),
              "planner_origins": dict(Counter(x["origin"]["kind"] for x in provenance["items"] if x["status"] == "ready")),
              "metric_contract": "Historical page-level per-fact metrics; weak groups excluded; not standard query-level nDCG/MRR",
              "evaluation_identity": read(out / "evaluation_identity.json")}
    write(out / "results.json", result)
    # Recompute the standalone-channel intervals from the same frozen question
    # order, so all report tables can be reproduced with this one command.
    pure_rows = [read(out / "channel_rows" / f"{i['id'].replace(':', '_')}.json") for i, _ in pairs]
    pure_differences = []
    for chosen, base in [("hybrid_current", v) for v in ["dense_channel", "bm25_channel", "hybrid_unstripped"]]:
        n = np.array([sum(not g["weak"] for g in r["variants"][chosen]["groups"]) for r in pure_rows])
        for measure in ["recall@40", "recall@8", "ndcg@8", "mrr"]:
            delta = np.array([r["variants"][chosen]["metrics"].get(measure, 0) -
                              r["variants"][base]["metrics"].get(measure, 0) for r in pure_rows])
            boot = (delta[draws] * n[draws]).sum(axis=1) / n[draws].sum(axis=1)
            pure_differences.append({"chosen": chosen, "baseline": base, "metric": measure,
                "difference": float((delta * n).sum() / n.sum()),
                "bootstrap95": [float(v) for v in np.quantile(boot, [.025, .975])],
                "improved": int(((delta > 1e-12) & (n > 0)).sum()),
                "worse": int(((delta < -1e-12) & (n > 0)).sum()),
                "equal": int(((abs(delta) <= 1e-12) & (n > 0)).sum()),
                "without_primary_gold": int((n == 0).sum())})
    write(out / "channel_baselines_ci.json", {"seed": 20261019, "replicates": 2000,
        "unit": "question; micro fact metric recomputed in each sample", "items": len(pure_rows),
        "scored_questions": len(scored_positions),
        "question_order": [r["id"] for r in pure_rows], "comparisons": pure_differences})
    print(json.dumps({"items": len(rows), "parity": result["production_top8_parity"],
                      "summary": summary["all"]}, ensure_ascii=False, indent=2), flush=True)


def verify(out):
    """Audit labels/ranks against frozen gold and check real channel isolation."""
    from rag import ra_retrieval, ra_statistical_tables
    from eval.retrieval_gold import ndcg
    items = read(GOLD)["items"]
    evidence = read(out / "evaluation_identity.json")
    prep = read(out / "preparation.json")
    checks = {}
    for path, expected in evidence["source_hashes"].items():
        if file_hash(ROOT / path) != expected:
            raise ValueError(f"Production source changed during evaluation: {path}")
    checks["production_sources_unchanged"] = True
    if file_hash(GOLD) != evidence["gold_sha256"]:
        raise ValueError("Gold changed during evaluation")
    checks["gold_unchanged"] = True
    count = 0
    for item in items:
        row = read(out / "rows" / f"{item['id'].replace(':', '_')}.json")
        expected = [(g["label"], g["weak"]) for g in item["groups"]]
        for name, value in row["variants"].items():
            if [(g["label"], g["weak"]) for g in value["groups"]] != expected:
                raise ValueError("Evaluation changed gold labels or exclusions")
            gs = [g for g in value["groups"] if not g["weak"]]
            if gs and abs(ndcg(gs, 8) - value["metrics"]["ndcg@8"]) > 1e-12:
                raise ValueError("nDCG differs from the original historical scoring function")
            if any(g["rank"] is not None and not 1 <= g["rank"] <= 40 for g in value["groups"]):
                raise ValueError("Invalid candidate rank")
        if not row["production_top8_parity"]:
            raise ValueError("Production parity was not checked")
        count += 1
    checks["rows_with_identical_gold_and_production_top8"] = count
    checks["historical_ndcg_scorer_agreement"] = True
    checks["primary_gold_groups"] = sum(not g["weak"] for i in items for g in i["groups"])
    pipe = make_pipeline()
    ui_scope = {i["id"]: i for i in prep["items"]}
    for item in items:
        scope = ui_scope[item["id"]]
        pipe.validate_request(item["question"], scope["ui_products"], scope["ui_agencies"])
    checks["service_request_validation_passed"] = len(items)
    corpus = pipe.retrieval_corpus()
    corpus.allowed_documents = {(d["document_id"], d["version_id"]) for d in pipe.documents}
    corpus._ra_bm25 = ra_retrieval.Bm25([
        {**c, "header": " ".join(c.get("structure", {}).get("section_path", [])) + " " +
         (c.get("structure", {}).get("caption") or "")} for c in corpus.chunks], thread_safe=True)
    try:
        item = next(i for i in items if i["id"] == "storage:SB17")
        frozen = read(out / "plans/storage_SB17.json")
        corpus._emb = FrozenEncoder(out / "query_vectors", item["id"], frozen)
        with patch.object(ra_statistical_tables, "hits", return_value=[]):
            with patch.object(corpus.vec, "top", wraps=corpus.vec.top) as dense_top, \
                 patch.object(corpus.vec, "scores", wraps=corpus.vec.scores) as dense_scores:
                _, pools = capture_pools(corpus, frozen["understanding"], pipe.guidelines,
                                         corpus.allowed_documents, PROFILES["bm25_channel"])
                if dense_top.call_count or dense_scores.call_count or not any(pools.values()):
                    raise ValueError("Pure BM25 channel unexpectedly used dense scores or returned no candidates")
                checks["pure_bm25_dense_database_queries"] = dense_top.call_count + dense_scores.call_count
            # Isolate one label document. Every admitted row has the same role,
            # so role boosting cannot change a single-query native dense/BM25 order.
            doc = max((d for d in pipe.documents if d["product_id"] == "SB17" and
                       d["jurisdiction"] == "EU" and d.get("doc_group") == "label"),
                      key=lambda d: d["chunks"])
            documents = {(doc["document_id"], doc["version_id"])}
            idx = [i for i in ra_retrieval.filter_idx(corpus.chunks, include_nondefault=True)
                   if (corpus.chunks[int(i)]["document_id"], corpus.chunks[int(i)]["version_id"]) in documents
                   and ra_retrieval.allowed(corpus.chunks[int(i)])
                   and ra_retrieval.permitted(corpus.chunks[int(i)].get("document_metadata") or {})]
            task = frozen["understanding"]["tasks"][0]
            query = task["queries"][0]
            if len(task["queries"]) != 1 or len(frozen["understanding"]["tasks"]) != 1:
                raise ValueError("Native channel oracle requires one query")
            vector = corpus.encode([query])[0]
            native = {
                "dense_channel": [i for _, i in ra_retrieval.dense_hits(vector, corpus.vec, idx, top_n=40)],
                "bm25_channel": [i for _, i in corpus._ra_bm25.hits(ra_retrieval.strip_slot_terms(query), idx, top_n=40)],
            }
            for channel, expected_order in native.items():
                _, pools = capture_pools(corpus, frozen["understanding"], pipe.guidelines, documents, PROFILES[channel])
                nonempty = [v for v in pools.values() if v]
                if len(nonempty) != 1 or [i for _, i in nonempty[0]] != expected_order:
                    from collections import Counter
                    write(out / "verification_channel_debug.json", {"channel": channel, "query": query,
                        "document": list(documents), "expected": expected_order,
                        "actual": [[i for _, i in p] for p in nonempty],
                        "chunk_roles": dict(Counter(corpus.chunks[int(i)]["doc_group"] for i in idx)),
                        "native_bm25": corpus._ra_bm25.hits(ra_retrieval.strip_slot_terms(query), idx, top_n=40)})
                    raise ValueError(f"{channel} does not reproduce the independent native channel order")
                checks[channel + "_native_top40_match"] = True
                checks[channel + "_native_candidates"] = len(expected_order)
            checks["native_check_document"] = [doc["document_id"], doc["version_id"]]
    finally:
        corpus._emb = None
        if hasattr(corpus.vec, "close"):
            corpus.vec.close()
        pipe.close()
    checks["planner_response_replays"] = sum(i["origin"]["kind"] == "exact_request_hash_replay" for i in prep["items"])
    checks["new_current_planner_calls"] = read(out / "planner_usage.json")["requests"]
    services = []
    for before in prep["services"]:
        with urlopen(f"http://127.0.0.1:{before['port']}/api/summary", timeout=15) as response:
            live = json.load(response)
        now = {"port": before["port"], **{k: live.get(k) for k in before if k != "port"}}
        if now != before:
            raise ValueError("Service version, release hash or backend changed during evaluation")
        services.append(now)
    checks["services_unchanged"] = services
    checks["verified_at_kst"] = datetime.now(KST).isoformat()
    checks["passed"] = True
    write(out / "verification.json", checks)
    print(json.dumps(checks, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-new-plans", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--phase", choices=["prepare", "encode", "evaluate", "channels", "summarize", "verify", "run"], default="prepare")
    parser.add_argument("--reranker-batch", type=int, default=4)
    args = parser.parse_args()
    only = args.only.split(",") if args.only else None
    if args.phase == "prepare":
        manifest = prepare(args.out, args.allow_new_plans, only)
        from collections import Counter
        print(json.dumps({"status": dict(Counter(x["status"] for x in manifest["items"])),
                          "new_calls": manifest["new_planner_calls"]}), flush=True)
    if args.phase in ("encode", "run"):
        encode(args.out, only)
    if args.phase in ("evaluate", "run"):
        evaluate(args.out, only, args.reranker_batch)
    if args.phase == "channels":
        channel_baselines(args.out, only)
    if args.phase in ("summarize", "run"):
        summarize(args.out, only)
    if args.phase == "verify":
        verify(args.out)


if __name__ == "__main__":
    main()
