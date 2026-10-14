#!/usr/bin/env python3
"""Measure the question-understanding step, then retrieval with the scope it predicts.

1. Accuracy on the 54 retrieval questions (both Korean forms): product, study, jurisdiction,
   document group, topic, intent. Their expected values come from the gold-derived query set.
2. Accuracy on eval/routing_v0.1.csv: 14 hand-written questions of other kinds (comparisons,
   labels, SAP, out of scope). I wrote both the questions and the expected labels.
3. Retrieval with the predicted scope compared with the oracle scope used so far.
Results are cached, so the LLM is called once per question.
"""
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
from embed_index import Embedder  # noqa: E402
from search import Bm25, dense_hits, filter_idx, load_index, rrf, to_pages  # noqa: E402
from translate_query import load_glossary  # noqa: E402
from understand_query import PROMPT_VERSION, cache_fingerprint, cache_path as understanding_cache_path, understand  # noqa: E402
import study_registry as sr  # noqa: E402

TOPIC = {"design": "design", "population": "population", "dosing": "dosing", "endpoints": "endpoints", "margin": "equivalence_margin",
         "assay": "assay", "n_randomized": "n_subjects", "n_pk_set": "n_subjects", "ada": "immunogenicity", "nab": "immunogenicity",
         "gmr": "pk_results", "equivalence": "equivalence_conclusion"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cache_path = understanding_cache_path()
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    queries = [q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"]
    routing = list(csv.DictReader(open(ROOT / "eval/routing_v0.1.csv", encoding="utf-8-sig")))
    jobs = [(f"{q['query_id']}|{form}", q[form]) for q in queries for form in ("ko_a", "ko_b")] + [(r["id"], r["question"]) for r in routing]
    todo = [j for j in jobs if cache.get(j[0], {}).get("cache_fingerprint") != cache_fingerprint(j[1])]
    glossary = load_glossary()
    t0 = time.time()
    with ThreadPoolExecutor(4) as pool:
        for key, out in pool.map(lambda j: (j[0], {**understand(j[1], glossary), "source": j[1]}), todo):
            cache[key] = out
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    report = {"model": sorted({v["model"] for v in cache.values()}), "called_now": len(todo), "seconds": round(time.time() - t0, 1)}

    # 1. Accuracy on the gold-derived questions.
    acc = {}
    for form in ("ko_a", "ko_b"):
        c = {"n": 0, "product_exact": 0, "study_right": 0, "study_empty": 0, "study_wrong": 0, "jurisdiction_empty": 0,
             "doc_group_has_assessment": 0, "doc_group_only_assessment": 0, "topic_right": 0, "intent_fact_lookup": 0}
        for q in queries:
            u = cache[f"{q['query_id']}|{form}"]
            c["n"] += 1
            c["product_exact"] += u["products"] == [q["product_id"]]
            studies = set(u["studies"])
            if not studies:
                c["study_empty"] += 1
            elif studies <= sr.family(q["study_key"]):
                c["study_right"] += 1
            else:
                c["study_wrong"] += 1
            c["jurisdiction_empty"] += not u["jurisdictions"]
            c["doc_group_has_assessment"] += "assessment" in u["doc_groups"]
            c["doc_group_only_assessment"] += u["doc_groups"] == ["assessment"]
            c["topic_right"] += u["topic"] == TOPIC[q["topic"]]
            c["intent_fact_lookup"] += u["intent"] == "fact_lookup"
        acc[form] = c
    report["gold_questions"] = acc

    # 2. Routing questions.
    rows = []
    for r in routing:
        u = cache[r["id"]]
        want_j = [set(x.split(";")) if x else set() for x in r["jurisdictions_ok"].split("|")]
        rows.append({
            "id": r["id"], "intent": u["intent"] in r["intent_ok"].split(";"),
            "products": set(u["products"]) == (set(r["products"].split(";")) if r["products"] else set()),
            "jurisdictions": set(u["jurisdictions"]) in want_j,
            "doc_group": (not r["doc_group_any"]) or bool(set(u["doc_groups"]) & set(r["doc_group_any"].split(";"))),
            "got": {k: u[k] for k in ("intent", "products", "studies", "jurisdictions", "doc_groups", "topic")},
        })
    report["routing"] = {k: sum(x[k] for x in rows) for k in ("intent", "products", "jurisdictions", "doc_group")}
    report["routing"]["n"] = len(rows)
    report["routing_rows"] = rows

    # 3. Retrieval: oracle scope vs predicted scope, with the query text from the understanding step.
    for q in queries:
        q["acc"] = set(json.loads(q["acceptable_pages"]))

    def first_rank(pages, q):
        for rank, p in enumerate(pages, 1):
            for d, page in [(p["document_id"], p["pdf_page"]), *p["also_in"]]:
                if d == q["document_id"] and page in q["acc"]:
                    return rank

    chunks, _, _ = load_index("bge-m3")
    bm = Bm25(chunks)
    retr = {}
    for key in ("bge-m3",):
        _, vec, _ = load_index(key)
        emb = Embedder(key)
        for form in ("ko_a", "ko_b"):
            us = [cache[f"{q['query_id']}|{form}"] for q in queries]
            qv = emb.encode_queries([u["english_query"] for u in us])
            for scope in ("oracle", "predicted_eu_slice", "predicted_all"):
                b5 = h5 = n5 = 0
                for i, (q, u) in enumerate(zip(queries, us)):
                    if scope == "oracle":
                        idx = filter_idx(chunks, q["product_id"], "assessment", "EU")
                    else:
                        jur = "EU" if scope == "predicted_eu_slice" else (u["jurisdictions"] or None)
                        idx = filter_idx(chunks, u["products"] or None, u["doc_groups"] or None, jur)
                    b = bm.hits(u["search_terms"], idx)
                    d = dense_hits(qv[i], vec, idx)
                    rb, rh = first_rank(to_pages(b, chunks, 10), q), first_rank(to_pages(rrf([d, b]), chunks, 10), q)
                    rn = first_rank(to_pages(bm.hits(u["english_query"] + " " + " ".join(u["keywords"]), idx), chunks, 10), q)
                    b5 += bool(rb and rb <= 5)
                    h5 += bool(rh and rh <= 5)
                    n5 += bool(rn and rn <= 5)
                retr.setdefault(f"{form}|{scope}", {})["bm25"] = b5
                retr[f"{form}|{scope}"]["bm25_without_glossary_expansion"] = n5
                retr[f"{form}|{scope}"][f"hybrid:{key}"] = h5
        del emb
    report["retrieval_s@5"] = retr
    out = ROOT / f"data/rag/eval/understanding_{PROMPT_VERSION}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "routing_rows"}, ensure_ascii=False, indent=1))
    for x in rows:
        if not all(x[k] for k in ("intent", "products", "jurisdictions", "doc_group")):
            print("routing miss:", x["id"], {k: x[k] for k in ("intent", "products", "jurisdictions", "doc_group")}, x["got"])


if __name__ == "__main__":
    main()
