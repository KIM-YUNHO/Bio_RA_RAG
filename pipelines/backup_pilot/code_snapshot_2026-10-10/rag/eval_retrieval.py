#!/usr/bin/env python3
"""Compare retrievers on eval/queries_v0.1.csv at page level.

A query is a hit at k when any acceptable page of its source report is among the top k pages.
The query set is generated from an AI-draft gold set (see eval/build_queries.py), so the numbers
describe this corpus and these generated questions, not real user traffic.

With --translations, the cached LLM translations of the Korean forms are added as query forms:
tr_a_plain / tr_b_plain (no glossary) and tr_a_gloss / tr_b_gloss (glossary and term protection).
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from embed_index import DEFAULT_MODEL, MODELS, RAG, Embedder  # noqa: E402
from search import Bm25, dense_hits, filter_idx, load_index, rrf, to_pages  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FILTERS = {
    # The question names the source: the product's EMA assessment report.
    "scoped": {"doc_group": "assessment", "jurisdiction": "EU"},
    # Only the product is known: every document of that product competes. Pages outside the EMA report
    # count as misses even when they state the same result, so this is a pessimistic number.
    "product_only": {},
}
NUMERIC = ("gmr", "ada", "nab", "n_randomized", "n_pk_set")


def first_rank(pages, doc, acceptable):
    for rank, p in enumerate(pages, 1):
        # A merged result also stands for the identical copies listed in also_in.
        for d, page in [(p["document_id"], p["pdf_page"]), *p["also_in"]]:
            if d == doc and page in acceptable:
                return rank
    return None


def metrics(ranks):
    n = len(ranks)
    hit = lambda k: sum(r is not None and r <= k for r in ranks) / n
    return {"n": n, "s@1": round(hit(1), 3), "s@5": round(hit(5), 3), "s@10": round(hit(10), 3),
            "mrr@10": round(sum(1 / r for r in ranks if r) / n, 3)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", nargs="+", default=[DEFAULT_MODEL])
    p.add_argument("--top-n", type=int, default=50, help="Chunks taken from each retriever before merging to pages")
    p.add_argument("--translations", help="Cache file written by eval/translate_queries.py")
    p.add_argument("--tag", default="v0.1", help="Suffix of the output file data/rag/eval/retrieval_<tag>.json")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    queries = [q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"]
    for q in queries:
        q["acceptable"] = set(json.loads(q["acceptable_pages"]))

    # form -> per query (text for the embedding model, text for BM25)
    forms = {f: [(q[f], q[f]) for q in queries] for f in ("en_ref", "ko_a", "ko_b")}
    translation_info = None
    if a.translations:
        cache = json.loads((ROOT / a.translations).read_text(encoding="utf-8"))
        translation_info = {k: cache.get(k) for k in ("provider", "model_requested", "prompt_version", "updated_at")}
        for src, short in (("ko_a", "a"), ("ko_b", "b")):
            for mode, label in (("plain", "plain"), ("glossary", "gloss")):
                items = [cache["items"][f"{q['query_id']}|{src}|{mode}"] for q in queries]
                # BM25 also gets the keyword list, which carries the spelling variants documents may use.
                forms[f"tr_{short}_{label}"] = [(t["english_query"], t["english_query"] + " " + " ".join(t["keywords"])) for t in items]

    ranks = {}  # (retriever, form, filter) -> [rank of the first acceptable page per query]
    bm25, bm25_hits, idx_cache, chunks = None, {}, {}, None
    for key in a.models:
        t0 = time.time()
        chunks, vec, _ = load_index(key)
        if bm25 is None:
            bm25 = Bm25(chunks)
            for fname, f in FILTERS.items():
                for q in queries:
                    idx_cache[(fname, q["product_id"])] = filter_idx(chunks, q["product_id"], f.get("doc_group"), f.get("jurisdiction"))
            for form, texts in forms.items():
                for fname in FILTERS:
                    for qi, q in enumerate(queries):
                        h = bm25.hits(texts[qi][1], idx_cache[(fname, q["product_id"])], a.top_n)
                        bm25_hits[(form, fname, qi)] = h
                        ranks.setdefault(("bm25", form, fname), []).append(first_rank(to_pages(h, chunks, 10), q["document_id"], q["acceptable"]))
        emb = Embedder(key)
        for form, texts in forms.items():
            qv = emb.encode_queries([t[0] for t in texts])
            for fname in FILTERS:
                for qi, q in enumerate(queries):
                    d = dense_hits(qv[qi], vec, idx_cache[(fname, q["product_id"])], a.top_n)
                    ranks.setdefault((f"dense:{key}", form, fname), []).append(first_rank(to_pages(d, chunks, 10), q["document_id"], q["acceptable"]))
                    fused = rrf([d, bm25_hits[(form, fname, qi)]], top_n=a.top_n)
                    ranks.setdefault((f"hybrid:{key}", form, fname), []).append(first_rank(to_pages(fused, chunks, 10), q["document_id"], q["acceptable"]))
        del emb
        print(f"{key}: {time.time() - t0:.0f}s", file=sys.stderr, flush=True)

    rows = [{"retriever": r, "form": form, "filter": fname, **metrics(v)} for (r, form, fname), v in ranks.items()]
    breakdown = []
    for (r, form, fname), v in ranks.items():
        if fname != "scoped":
            continue
        for label, pick in (("TEXT", lambda q: q["modality"] == "TEXT"), ("IMAGE", lambda q: q["modality"] != "TEXT"),
                            ("numeric", lambda q: q["topic"] in NUMERIC), ("design", lambda q: q["topic"] not in NUMERIC)):
            breakdown.append({"retriever": r, "form": form, "subset": label, **metrics([x for x, q in zip(v, queries) if pick(q)])})
    out = RAG / "eval"
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "queries": len(queries), "top_n_chunks": a.top_n,
        "chunks_sha256": json.loads((RAG / "chunks_summary.json").read_text(encoding="utf-8"))["chunks_sha256"],
        "translations": translation_info, "summary": rows, "breakdown_scoped": breakdown,
        "per_query": {f"{r}|{form}|{fname}": v for (r, form, fname), v in ranks.items()},
        "query_ids": [q["query_id"] for q in queries],
    }
    (out / f"retrieval_{a.tag}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    for fname in FILTERS:
        for form in forms:
            print(f"\n== filter={fname} | query form={form} | n={len(queries)} ==")
            print(f"{'retriever':22s} {'S@1':>6s} {'S@5':>6s} {'S@10':>6s} {'MRR@10':>7s}")
            for x in sorted((x for x in rows if x["filter"] == fname and x["form"] == form), key=lambda x: (-x["s@5"], -x["mrr@10"])):
                print(f"{x['retriever']:22s} {x['s@1']:6.3f} {x['s@5']:6.3f} {x['s@10']:6.3f} {x['mrr@10']:7.3f}")


if __name__ == "__main__":
    main()
