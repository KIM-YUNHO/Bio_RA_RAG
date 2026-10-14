#!/usr/bin/env python3
"""Retrieval evaluation per agency with the manual relevance labels (eval/build_qrels.py).

For each agency, search within that product's assessment-level documents of that agency and count a
hit when a page labelled as answering (grade 2) is in the top k. Only questions that the agency's
document answers in full or in part are scored (--statuses), so n differs per agency.
nDCG@10 uses grades 2 and 1.
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from embed_index import DEFAULT_MODEL, MODELS, RAG, Embedder  # noqa: E402
from search import Bm25, dense_hits, filter_idx, load_index, rrf, to_pages  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AGENCIES = ["EU", "US", "CA"]


def grades_of(page, rel):
    best = 0
    for d, pg in [(page["document_id"], page["pdf_page"]), *page["also_in"]]:
        best = max(best, rel.get(f"{d}|{pg}", {}).get("grade", 0))
    return best


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", nargs="+", default=[DEFAULT_MODEL])
    p.add_argument("--agencies", nargs="*", default=AGENCIES, help="Score only these agencies (e.g. while judging is incomplete)")
    p.add_argument("--statuses", nargs="*", default=["reported", "partial"], help="Answer statuses of the questions to score")
    p.add_argument("--tag", default="v0.1")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    queries = [q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"]
    Q = json.loads((ROOT / "data/rag/eval/qrels_v0.1.json").read_text(encoding="utf-8"))
    tr = json.loads((ROOT / "data/rag/cache/translations_v0.1_openai-gpt-4o-mini.json").read_text(encoding="utf-8"))["items"]
    forms = {"en_ref": [(q["en_ref"],) * 2 for q in queries]}
    for src, short in (("ko_a", "a"), ("ko_b", "b")):
        items = [tr[f"{q['query_id']}|{src}|glossary"] for q in queries]
        forms[f"tr_{short}_gloss"] = [(t["english_query"], t["english_query"] + " " + " ".join(t["keywords"])) for t in items]

    chunks, _, _ = load_index("bge-m3")
    bm = Bm25(chunks)
    results = {}  # (retriever, form, agency) -> list of (s@1, s@5, s@10, ndcg10) per scored question

    def score(pages, rel):
        g = [grades_of(pg, rel) for pg in pages[:10]]
        first = next((i + 1 for i, x in enumerate(g) if x == 2), None)
        dcg = sum((2 ** x - 1) / math.log2(i + 2) for i, x in enumerate(g))
        ideal = sorted((v["grade"] for v in rel.values()), reverse=True)[:10]
        idcg = sum((2 ** x - 1) / math.log2(i + 2) for i, x in enumerate(ideal)) or 1
        return (first is not None and first <= 1, first is not None and first <= 5, first is not None, dcg / idcg)

    idx = {(q["product_id"], ag): filter_idx(chunks, q["product_id"], "assessment", ag) for q in queries for ag in AGENCIES}
    bm_hits = {}
    for form, texts in forms.items():
        for i, q in enumerate(queries):
            for ag in AGENCIES:
                bm_hits[(form, i, ag)] = bm.hits(texts[i][1], idx[(q["product_id"], ag)])
    for key in a.models:
        _, vec, _ = load_index(key)
        emb = Embedder(key)
        for form, texts in forms.items():
            qv = emb.encode_queries([t[0] for t in texts])
            for i, q in enumerate(queries):
                for ag in a.agencies:
                    if Q["status"][f"{q['query_id']}|{ag}"]["status"] not in a.statuses:
                        continue
                    rel = Q["qrels"][f"{q['query_id']}|{ag}"]
                    d = dense_hits(qv[i], vec, idx[(q["product_id"], ag)])
                    b = bm_hits[(form, i, ag)]
                    if key == a.models[0]:
                        results.setdefault(("bm25", form, ag), []).append(score(to_pages(b, chunks, 10), rel))
                    results.setdefault((f"dense:{key}", form, ag), []).append(score(to_pages(d, chunks, 10), rel))
                    results.setdefault((f"hybrid:{key}", form, ag), []).append(score(to_pages(rrf([d, b]), chunks, 10), rel))
        del emb
    rows = []
    for (r, form, ag), v in results.items():
        n = len(v)
        rows.append({"retriever": r, "form": form, "agency": ag, "n": n, "s@1": sum(x[0] for x in v), "s@5": sum(x[1] for x in v),
                     "s@10": sum(x[2] for x in v), "ndcg@10": round(sum(x[3] for x in v) / n, 3)})
    (RAG / f"eval/retrieval_by_agency_{a.tag}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    for ag in AGENCIES:
        for form in forms:
            sub = sorted((x for x in rows if x["agency"] == ag and x["form"] == form), key=lambda x: (-x["s@5"], -x["ndcg@10"]))
            if not sub:
                continue
            print(f"\n== {ag} | {form} | scored questions n={sub[0]['n']} ==")
            print(f"{'retriever':22s} {'S@1':>5s} {'S@5':>5s} {'S@10':>5s} {'nDCG@10':>8s}")
            for x in sub:
                print(f"{x['retriever']:22s} {x['s@1']:5d} {x['s@5']:5d} {x['s@10']:5d} {x['ndcg@10']:8.3f}")


if __name__ == "__main__":
    main()
