#!/usr/bin/env python3
"""Measure the question path end to end: Korean question -> understanding -> search plan -> retrieval.

docs/temp/rag/RAG_STUDY_CONTEXT_PLAN.md 8.2 다. Questions: the 54 retrieval questions (ko_a), the 30 questions
that describe their study, the 10 product questions (eval/queries_study_v0.1.csv) and the 14 routing
questions (eval/routing_v0.1.csv).

1. Study resolution: semantically selected studies against the question's study; no topic/phase gates.
2. Product questions: does the plan cover every study the question needs?
3. Routing questions: intent, products, jurisdictions.
4. Retrieval (hybrid bge-m3 or BM25): S@5 per agency with the predicted scope and query against the oracle
   scope (product, assessment documents, the agency) with the same query. Predicted scope executes
   the actual products and the experimental general retrieval policy, without correcting them from gold.

    uv run python eval/eval_pipeline.py
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
import study_registry as sr  # noqa: E402
from embed_index import RAG, Embedder  # noqa: E402
from plan_query import plan  # noqa: E402
from search import Bm25, dense_hits, filter_idx, load_index, rrf, to_pages  # noqa: E402
from translate_query import load_glossary  # noqa: E402
from understand_query import PROMPT_VERSION, cache_fingerprint, cache_path as understanding_cache_path, resolve_cached, understand  # noqa: E402
from answer import retrieve  # noqa: E402
from types import SimpleNamespace

ANSWERED = ("reported", "partial")


def family(key):
    s = sr.load()[key]
    f = sr.family(key)
    if s["kind"] == "cross_study_analysis":
        f |= set(s["covers"]) | {sr.load()[c].get("parent") for c in s["covers"]} - {None}
    return f


def grade(page, rel):
    return max(rel.get(f"{d}|{p}", {}).get("grade", 0) for d, p in [(page["document_id"], page["pdf_page"]), *page["also_in"]])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="bge-m3")
    p.add_argument("--tag", default=PROMPT_VERSION)
    p.add_argument("--cached-understanding", type=Path,
                   help="Offline scope/retrieval audit using the specified cache without new LLM calls or cache writes")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cache_path = a.cached_understanding or understanding_cache_path()
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    base = {q["query_id"]: q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"}
    extra = list(csv.DictReader(open(ROOT / "eval/queries_study_v0.1.csv", encoding="utf-8-sig")))
    routing = list(csv.DictReader(open(ROOT / "eval/routing_v0.1.csv", encoding="utf-8-sig")))
    # (cache key, kind, Korean question, components = base question ids)
    items = [(f"{qid}|ko_a", "base", q["ko_a"], [qid]) for qid, q in base.items()]
    items += [(f"{q['query_id']}|ko", q["kind"], q["ko"], q["components"].split(";")) for q in extra]
    items += [(r["id"], "routing", r["question"], []) for r in routing]
    if a.cached_understanding:
        missing = [it[0] for it in items if it[0] not in cache or cache[it[0]].get("source") != it[2]]
        if missing:
            raise ValueError(f"Specified cache has missing or changed source questions: {missing}")
        todo = []
    else:
        todo = [it for it in items if cache.get(it[0], {}).get("cache_fingerprint") != cache_fingerprint(it[2])]
    glossary = load_glossary()
    t0 = time.time()
    with ThreadPoolExecutor(4) as pool:
        for key, out in pool.map(lambda it: (it[0], {**understand(it[2], glossary), "source": it[2]}), todo):
            cache[key] = out
    if not a.cached_understanding:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    report = {"current_prompt_version": PROMPT_VERSION, "understanding_prompt_versions": sorted({cache[it[0]].get("prompt_version", "unknown") for it in items}),
              "understanding_source": str(cache_path), "offline_cache_audit": bool(a.cached_understanding),
              "understood_now": len(todo), "seconds": round(time.time() - t0, 1), "retrieval_policy": "general"}
    # Validate cached selections. Historical caches are explicitly marked legacy_unverified by resolve().
    for key, _, ko, _ in items:
        u = cache[key]
        cache[key] = resolve_cached(ko, u)

    # 1-2. Studies.
    res = defaultdict(Counter)
    misses = []
    for key, kind, ko, comps in items:
        if kind == "routing":
            continue
        u = cache[key]
        units = plan(u)
        planned = {k for x in units for k in (x["studies"] or x["study_candidates"])}
        if kind in ("base", "indirect"):
            want = family(base[comps[0]]["study_key"])
            got = set(u["studies"])
            r = res[kind]
            r["n"] += 1
            r["products_right"] += u["products"] == [base[comps[0]]["product_id"]]
            if not got:
                r["understood_none"] += 1
            elif got <= want:
                r["understood_right"] += 1
            else:
                r["understood_wrong"] += 1
            r["plan_has_study"] += bool(planned & want)
            r["plan_only_study"] += bool(planned) and planned <= want
            if not (got and got <= want):
                misses.append((key, ko, sorted(got), base[comps[0]]["study_key"]))
        else:
            needed = [base[c]["study_key"] for c in comps]
            r = res["product"]
            r["n"] += 1
            r["parts"] += len(needed)
            r["parts_planned"] += sum(bool(planned & family(k)) for k in needed)
            r["all_planned"] += all(planned & family(k) for k in needed)
            r["decomposed"] += any(x["decomposed"] for x in units)
            if not all(planned & family(k) for k in needed):
                misses.append((key, ko, sorted(planned), needed))
    report["studies"] = {k: dict(v) for k, v in res.items()}

    # 3. Routing questions.
    rt = Counter()
    for r in routing:
        u = cache[r["id"]]
        want_j = [set(x.split(";")) if x else set() for x in r["jurisdictions_ok"].split("|")]
        rt["n"] += 1
        rt["intent"] += u["intent"] in r["intent_ok"].split(";")
        rt["products"] += set(u["products"]) == (set(r["products"].split(";")) if r["products"] else set())
        rt["jurisdictions"] += set(u["jurisdictions"]) in want_j
        rt["doc_group"] += (not r["doc_group_any"]) or bool(set(u["doc_groups"]) & set(r["doc_group_any"].split(";")))
    report["routing"] = dict(rt)

    # 4. Retrieval, per agency, predicted vs oracle scope.
    Q = json.loads((ROOT / "data/rag/eval/qrels_v0.1.json").read_text(encoding="utf-8"))
    chunks, vec, _ = load_index(a.model)
    bm = Bm25(chunks)
    emb = Embedder(a.model)
    corpus = SimpleNamespace(chunks=chunks, vec=vec, bm=bm)
    ret_items = [it for it in items if it[1] in ("base", "indirect")]
    qv = emb.encode_queries([cache[it[0]]["english_query"] for it in ret_items])
    rows = []
    for i, (key, kind, ko, comps) in enumerate(ret_items):
        u = cache[key]
        q = base[comps[0]]
        # Execute the actual predicted products; a wrong product is never silently corrected by gold.
        predicted = {}
        for ret in ("bm25", f"hybrid:{a.model}"):
            grouped = defaultdict(list)
            for unit, pages in retrieve(corpus, u, qv[i], use_dense=ret != "bm25", k=10, policy="general"):
                grouped[unit["jurisdiction"]].extend(pages)
            predicted[ret] = {ag: sorted(pages, key=lambda pg: -pg["score"])[:10] for ag, pages in grouped.items()}
        for ag in ("EU", "US", "CA"):
            if Q["status"][f"{comps[0]}|{ag}"]["status"] not in ANSWERED:
                continue
            rel = Q["qrels"][f"{comps[0]}|{ag}"]
            for scope in ("oracle", "predicted"):
                if scope == "oracle":
                    idx = filter_idx(chunks, q["product_id"], "assessment", ag)
                else:
                    idx = []
                for ret in ("bm25", f"hybrid:{a.model}"):
                    if scope == "predicted":
                        pages = predicted[ret].get(ag, [])
                    else:
                        b = bm.hits(u["search_terms"], idx) if len(idx) else []
                        hits = rrf([dense_hits(qv[i], vec, idx), b]) if (ret != "bm25" and len(idx)) else b
                        pages = to_pages(hits, chunks, 10)
                    g = [grade(pg, rel) for pg in pages]
                    first = next((k + 1 for k, x in enumerate(g) if x == 2), None)
                    rows.append({"question": key, "kind": kind, "agency": ag, "scope": scope, "retriever": ret, "first_hit": first})
    summ = defaultdict(Counter)
    for r in rows:
        k = (r["kind"], r["agency"], r["retriever"], r["scope"])
        summ[k]["n"] += 1
        summ[k]["s@5"] += r["first_hit"] is not None and r["first_hit"] <= 5
        summ[k]["s@1"] += r["first_hit"] == 1
    report["retrieval"] = [{"questions": k[0], "agency": k[1], "retriever": k[2], "scope": k[3], **v} for k, v in sorted(summ.items())]
    (RAG / f"eval/pipeline_{a.tag}.json").write_text(json.dumps({"report": report, "rows": rows, "misses": misses}, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({k: v for k, v in report.items() if k != "retrieval"}, ensure_ascii=False, indent=1))
    print(f"\n{'questions':9s} {'ag':3s} {'retriever':15s} {'n':>3s} {'oracle S@5':>10s} {'predicted S@5':>13s}")
    for k in sorted({(r["questions"], r["agency"], r["retriever"]) for r in report["retrieval"]}):
        o = next(x for x in report["retrieval"] if (x["questions"], x["agency"], x["retriever"], x["scope"]) == (*k, "oracle"))
        pr = next(x for x in report["retrieval"] if (x["questions"], x["agency"], x["retriever"], x["scope"]) == (*k, "predicted"))
        print(f"{k[0]:9s} {k[1]:3s} {k[2]:15s} {o['n']:3d} {o['s@5']:10d} {pr['s@5']:13d}")
    print("\nstudy misses:")
    for m in misses:
        print(" ", m)


if __name__ == "__main__":
    main()
