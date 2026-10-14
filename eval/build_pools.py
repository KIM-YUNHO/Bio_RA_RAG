#!/usr/bin/env python3
"""Collect candidate pages per question and agency for relevance judging (pooling).

For each retrieval question and each agency (EU, US, CA), the top 10 pages of several retrievers and
query forms are pooled within that product's assessment-level documents of that agency. For EU the
pages already in the gold set are added. Judging only the pool means a page no retriever found is
never judged: "not found" in the results is "not found in the pool", not proof of absence.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
from embed_index import Embedder  # noqa: E402
from search import Bm25, dense_hits, filter_idx, load_index, rrf, to_pages  # noqa: E402

AGENCIES = ["EU", "US", "CA"]
TOP = 10


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    queries = [q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"]
    tr = json.loads((ROOT / "data/rag/cache/translations_v0.1_openai-gpt-4o-mini.json").read_text(encoding="utf-8"))["items"]
    forms = {
        "en_ref": [q["en_ref"] for q in queries],
        "tr_a_gloss": [tr[f"{q['query_id']}|ko_a|glossary"]["english_query"] for q in queries],
        "tr_b_gloss": [tr[f"{q['query_id']}|ko_b|glossary"]["english_query"] for q in queries],
    }
    chunks, _, _ = load_index("bge-m3")
    bm = Bm25(chunks)
    pools = {(q["query_id"], a): {} for q in queries for a in AGENCIES}

    def add(qid, agency, pages, source):
        for p in pages:
            for d, pg in [(p["document_id"], p["pdf_page"]), *p["also_in"]]:
                pools[(qid, agency)].setdefault(f"{d}|{pg}", set()).add(source)

    for key in ("bge-m3",):
        _, vec, _ = load_index(key)
        emb = Embedder(key)
        for form, texts in forms.items():
            qv = emb.encode_queries(texts)
            for i, q in enumerate(queries):
                for agency in AGENCIES:
                    idx = filter_idx(chunks, q["product_id"], "assessment", agency)
                    b = bm.hits(texts[i], idx)
                    d = dense_hits(qv[i], vec, idx)
                    add(q["query_id"], agency, to_pages(rrf([d, b]), chunks, TOP), f"hybrid:{key}|{form}")
                    add(q["query_id"], agency, to_pages(d, chunks, TOP), f"dense:{key}|{form}")
                    if key == "bge-m3":
                        add(q["query_id"], agency, to_pages(b, chunks, TOP), f"bm25|{form}")
        del emb
    for q in queries:
        for pg in json.loads(q["acceptable_pages"]):
            pools[(q["query_id"], "EU")].setdefault(f"{q['document_id']}|{pg}", set()).add("gold")

    out = {f"{qid}|{agency}": {k: sorted(v) for k, v in pages.items()} for (qid, agency), pages in pools.items()}
    path = ROOT / "data/rag/eval/pools_v0.1.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    sizes = {a: [len(v) for k, v in out.items() if k.endswith("|" + a)] for a in AGENCIES}
    print(json.dumps({a: {"questions": len(s), "pages_total": sum(s), "mean": round(sum(s) / len(s), 1), "max": max(s)} for a, s in sizes.items()}, indent=1))


if __name__ == "__main__":
    main()
