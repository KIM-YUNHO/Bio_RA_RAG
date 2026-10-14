#!/usr/bin/env python3
"""Measure how well retrieval keeps clinical studies apart (docs/temp/rag/RAG_STUDY_CONTEXT_PLAN.md, step 0).

Questions
- base: the 54 questions of eval/queries_v0.1.csv; each names its study.
- indirect: eval/queries_study_v0.1.csv kind=indirect; the same questions with the study described
  instead of numbered ("the second PK study", "the Japanese study"). Relevance labels are the base
  question's.
- product: kind=product; the study is not given. Each one stands for several base questions, one per
  study, and is right when every study's answer page is retrieved.

Measures, per agency (searching that agency's assessment documents of the product)
- S@k: a page labelled as answering (grade 2) is in the top k.
- other-study share @5: share of the top 5 pages that are about other studies only, by the manual
  page labels in eval/study_pages_v0.1.csv. Pages about no study (quality, admin) are counted apart.
- product questions: per-study recall @5/@10 and the share of questions with every study covered @10.
Only questions the agency's document answers (status reported or partial) are scored.

    uv run python eval/eval_study_context.py
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
import study_registry as sr  # noqa: E402
import study_search  # noqa: E402
from embed_index import RAG, Embedder  # noqa: E402
from plan_query import plan  # noqa: E402
from search import Bm25, filter_idx, load_index  # noqa: E402
from understand_query import resolve_cached  # noqa: E402

AGENCIES = ["EU", "US", "CA"]
ANSWERED = ("reported", "partial")


def study_family(key):
    s = sr.load()[key]
    fam = sr.family(key)
    if s["kind"] == "cross_study_analysis":
        fam |= set(s["covers"]) | {sr.load()[c].get("parent") for c in s["covers"]} - {None}
    return fam


def load_page_studies():
    lab = {}
    for r in csv.DictReader(open(ROOT / "eval/study_pages_v0.1.csv", encoding="utf-8")):
        st = set(filter(None, r["studies"].split(";")))
        for n in range(int(r["page_from"]), int(r["page_to"]) + 1):
            lab[f"{r['document_id']}|{n}"] = (r["scope"], st)
    return lab


def page_class(page, wanted, lab):
    """match / other / none / unlabelled for a retrieved page against the wanted study keys."""
    keys = [f"{page['document_id']}|{page['pdf_page']}"] + [f"{d}|{p}" for d, p in page["also_in"]]
    got = [lab[k] for k in keys if k in lab]
    if not got:
        return "unlabelled"
    if any(st & wanted for _, st in got):
        return "match"
    if any(st for _, st in got):
        return "other"
    return "none"


def grade(page, rel):
    keys = [(page["document_id"], page["pdf_page"]), *page["also_in"]]
    return max(rel.get(f"{d}|{p}", {}).get("grade", 0) for d, p in keys)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", nargs="+", default=["bge-m3"])
    p.add_argument("--methods", nargs="*", default=["A"], choices=study_search.METHODS,
                   help="How retrieval uses the studies (rag/study_search.py); A is plain search")
    p.add_argument("--tag", default="v0.1")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    base = {q["query_id"]: q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"}
    extra = list(csv.DictReader(open(ROOT / "eval/queries_study_v0.1.csv", encoding="utf-8-sig")))
    Q = json.loads((ROOT / "data/rag/eval/qrels_v0.1.json").read_text(encoding="utf-8"))
    tr = json.loads((ROOT / "data/rag/cache/translations_v0.1_openai-gpt-4o-mini.json").read_text(encoding="utf-8"))["items"]
    lab = load_page_studies()

    # (question id, kind, product, components, form, dense text, bm25 text)
    jobs = []
    for qid, q in base.items():
        t = tr[f"{qid}|ko_a|glossary"]
        jobs.append((qid, "base", q["product_id"], [qid], "en", q["en_ref"], q["en_ref"]))
        jobs.append((qid, "base", q["product_id"], [qid], "ko→tr", t["english_query"], t["english_query"] + " " + " ".join(t["keywords"])))
    for q in extra:
        t = tr[f"{q['query_id']}|ko|glossary"]
        comps = q["components"].split(";")
        jobs.append((q["query_id"], q["kind"], q["product_id"], comps, "en", q["en"], q["en"]))
        jobs.append((q["query_id"], q["kind"], q["product_id"], comps, "ko→tr", t["english_query"], t["english_query"] + " " + " ".join(t["keywords"])))

    # The studies each question is about, from the understanding step and the plan (eval/eval_pipeline.py):
    # the same for the English and translated forms of a question.
    cache = json.loads((ROOT / "data/rag/cache/understanding_v0.1_openai-gpt-4o-mini.json").read_text(encoding="utf-8"))

    def studies_of(qid, kind):
        u = cache[f"{qid}|ko_a" if kind == "base" else f"{qid}|ko"]
        u = resolve_cached(u["source"], u)
        return sorted({k for x in plan(u) for k in x["studies"]})

    studies = {(j[0], j[1]): studies_of(j[0], j[1]) for j in jobs}

    chunks, _, _ = load_index("bge-m3")
    bm = Bm25(chunks)
    idx = {(prod, ag): filter_idx(chunks, prod, "assessment", ag) for prod in {j[2] for j in jobs} for ag in AGENCIES}
    rows = []
    for model in [None] + a.models:
        vec = qv = None
        if model:
            _, vec, _ = load_index(model)
            emb = Embedder(model)
            qv = emb.encode_queries([j[5] for j in jobs])
        for method in a.methods:
          name = (f"hybrid:{model}" if model else "bm25") + ("" if a.methods == ["A"] else f"/{method}")
          for i, (qid, kind, prod, comps, form, _, bm_text) in enumerate(jobs):
            for ag in AGENCIES:
                comps_ok = [c for c in comps if Q["status"][f"{c}|{ag}"]["status"] in ANSWERED]
                if not comps_ok:
                    continue
                pages = study_search.search(qv[i] if model else None, bm_text, idx[(prod, ag)], chunks, vec, bm,
                                            studies[(qid, kind)], method, k=10, use_dense=bool(model))
                row = {"retriever": name, "question": qid, "kind": kind, "product": prod, "form": form, "agency": ag,
                       "studies": studies[(qid, kind)], "top10": [f"{pg['document_id']}|{pg['pdf_page']}" for pg in pages]}
                if kind in ("base", "indirect"):
                    c = comps_ok[0]
                    rel = Q["qrels"][f"{c}|{ag}"]
                    g = [grade(pg, rel) for pg in pages]
                    first = next((k + 1 for k, x in enumerate(g) if x == 2), None)
                    wanted = study_family(base[c]["study_key"])
                    cls = [page_class(pg, wanted, lab) for pg in pages[:5]]
                    row.update({"study": base[c]["study_key"], "first_hit": first, "classes5": cls})
                else:
                    per = {}
                    wanted_all = set()
                    for c in comps_ok:
                        rel = Q["qrels"][f"{c}|{ag}"]
                        g = [grade(pg, rel) for pg in pages]
                        per[c] = next((k + 1 for k, x in enumerate(g) if x == 2), None)
                        wanted_all |= study_family(base[c]["study_key"])
                    row.update({"components": per, "classes10": [page_class(pg, wanted_all, lab) for pg in pages]})
                rows.append(row)
        if model:
            del emb

    out = RAG / f"eval/study_context_{a.tag}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    def summarise(sub):
        n = len(sub)
        s1 = sum(r["first_hit"] == 1 for r in sub)
        s5 = sum(r["first_hit"] is not None and r["first_hit"] <= 5 for r in sub)
        other = sum(r["classes5"].count("other") for r in sub) / (5 * n)
        none = sum(r["classes5"].count("none") for r in sub) / (5 * n)
        return n, s1, s5, other, none

    print("\n== Single-study questions: S@1, S@5, share of top-5 pages about other studies only, about no study ==")
    print(f"{'retriever':15s} {'questions':9s} {'form':6s} {'ag':3s} {'scope':7s} {'n':>3s} {'S@1':>4s} {'S@5':>4s} {'other':>6s} {'none':>6s}")
    groups = defaultdict(list)
    for r in rows:
        if r["kind"] in ("base", "indirect"):
            groups[(r["retriever"], r["kind"], r["form"], r["agency"], "all")].append(r)
            if r["product"] == "CTP43":
                groups[(r["retriever"], r["kind"], r["form"], r["agency"], "CTP43")].append(r)
    summary = []
    for key in sorted(groups):
        n, s1, s5, other, none = summarise(groups[key])
        summary.append({"retriever": key[0], "questions": key[1], "form": key[2], "agency": key[3], "scope": key[4],
                        "n": n, "s@1": s1, "s@5": s5, "other_study_share@5": round(other, 3), "no_study_share@5": round(none, 3)})
        print(f"{key[0]:15s} {key[1]:9s} {key[2]:6s} {key[3]:3s} {key[4]:7s} {n:3d} {s1:4d} {s5:4d} {other:6.1%} {none:6.1%}")

    print("\n== Product questions (study not given): per-study recall @5/@10, all studies covered @10, top-10 pages about other studies only ==")
    print(f"{'retriever':15s} {'form':6s} {'ag':3s} {'q':>2s} {'parts':>5s} {'R@5':>5s} {'R@10':>5s} {'all@10':>6s} {'other@10':>8s}")
    pg = defaultdict(list)
    for r in rows:
        if r["kind"] == "product":
            pg[(r["retriever"], r["form"], r["agency"])].append(r)
    for key in sorted(pg):
        sub = pg[key]
        parts = [v for r in sub for v in r["components"].values()]
        r5 = sum(v is not None and v <= 5 for v in parts)
        r10 = sum(v is not None for v in parts)
        allc = sum(all(v is not None for v in r["components"].values()) for r in sub)
        other = sum(r["classes10"].count("other") for r in sub) / (10 * len(sub))
        summary.append({"retriever": key[0], "questions": "product", "form": key[1], "agency": key[2], "n": len(sub),
                        "parts": len(parts), "recall@5": r5, "recall@10": r10, "all_covered@10": allc, "other_study_share@10": round(other, 3)})
        print(f"{key[0]:15s} {key[1]:6s} {key[2]:3s} {len(sub):2d} {len(parts):5d} {r5:5d} {r10:5d} {allc:6d} {other:8.1%}")
    (RAG / f"eval/study_context_summary_{a.tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
