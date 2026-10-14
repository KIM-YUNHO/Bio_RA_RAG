"""Move a product checked in product_search/data (staging) into the project's data/.

Usage:
  uv run python -m product_search.promote                     # products waiting in staging
  uv run python -m product_search.promote DMB3115             # the check: licences, documents and studies per agency, gaps
  uv run python -m product_search.promote DMB3115 --apply     # move it, then collection.build_indexes and collection.verify

Nothing is downloaded. Raw files, parsed pages, cache entries and catalog rows are copied as they are, so what was checked is
what lands, with the same document_id and version_id (the keys parsing, embedding and the database will hang on). The product
is added to targets.json, so later project runs of collect.py keep it up to date with the same rules.
Rules: docs/collection/INGESTION_PIPELINE.md (sections 3.1, 4.2 and 4.3).
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys

from product_search import discover  # sets collect.ROOT to product_search/
from collection import collect
from collection.collect import read_jsonl, write_jsonl

STAGE = discover.HERE
PROJECT = collect.Path(os.environ.get("PROMOTE_PROJECT") or collect.PROJECT)  # the env variable is for trying a move on a copy of the project
TARGET_KEYS = ["product_id", "ingredient", "development_code", "ema_name", "us_brand", "aliases", "study_ids", "ca_brand",
               "pmda_general_list_ids", "company", "licences", "kind", "reference_product_id"]


def staged(product_id):
    cat = STAGE / "data/catalog"
    product = next((p for p in read_jsonl(cat / "products.jsonl") if p["product_id"] == product_id), None)
    docs = [d for d in read_jsonl(cat / "documents.jsonl") if d.get("product_id") == product_id]
    studies = [s for s in read_jsonl(cat / "studies.jsonl") if s["product_id"] == product_id]
    results = [r for r in read_jsonl(cat / "study_results.jsonl") if r["product_id"] == product_id]
    return product, docs, studies, results


def report(product_id):
    """The check before moving: who the product is in each jurisdiction, what was collected there, and what is missing."""
    product, docs, studies, results = staged(product_id)
    if not product:
        sys.exit(f"{product_id} is not in product_search/data; collect it first (python -m product_search.collect_products <name>)")
    print(f"== {product_id}  ingredient={product.get('ingredient')}  development code={product.get('development_code')}  company={product.get('company')}")
    for l in product.get("licences") or []:
        flag = "primary, documents collected" if l.get("collected") else "same product, documents not collected"
        print(f"   licence {l['jurisdiction']}: {l['name']} {l.get('licence_id') or ''} ({flag})")
    lic = product.get("us_licensure") or {}
    if lic:
        types = sorted({e["License Type"] for e in lic.get("events", [])})
        print(f"   kind {product.get('kind')}; US licence type (Purple Book {lic.get('source_month')}): {', '.join(types)}; reference product {(product.get('reference_product') or {}).get('name') or '-'}")
    by_source = {}
    for d in docs:
        by_source.setdefault(d["source"], []).append(d)
    for source in ["EMA", "FDA", "Health Canada", "PMDA", "ClinicalTrials.gov"]:
        rows = by_source.get(source, [])
        print(f"-- {source}: {len(rows)} documents, {sum(d['page_count'] for d in rows)} pages/sections")
        for d in rows:
            print(f"     [{d['tier']:<10}] {d['title'][:90]}  ({d['page_count']}, {d['url']})")
    for s in studies:
        print(f"-- trial {s['nct_id']} {'/'.join(s['phases'])}: {s['brief_title'][:70]}  attachments {len([d for d in docs if d.get('nct_id') == s['nct_id']])}, "
              f"posted result rows {sum(r['nct_id'] == s['nct_id'] for r in results)}")
    gaps = [f"{j}: no {name}" for j, field, source in (("EU", "ema_name", "EMA"), ("US", "us_brand", "FDA"), ("CA", "ca_brand", "Health Canada"))
            for name in ["documents"] if product.get(field) and not by_source.get(source)]
    gaps += [f"{j}: not authorised or not found" for j, field in (("EU", "ema_name"), ("US", "us_brand"), ("CA", "ca_brand")) if not product.get(field)]
    gaps += ["JP: PMDA is not searched; add pmda_general_list_ids by hand after checking the review report names the product"]
    print("-- check:", "; ".join(gaps))
    targets = json.loads((PROJECT / "configs/collection/targets.json").read_text(encoding="utf-8"))
    if any(t["product_id"] == product_id for t in targets["products"]):
        print(f"!! {product_id} is already in the project targets.json; it is updated by the project's collect.py, not moved again")
    return product, docs, studies, results


def copy(rel):
    src, dst = STAGE / rel, PROJECT / rel
    if src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def apply(product_id):
    product, docs, studies, results = report(product_id)
    targets_path = PROJECT / "configs/collection/targets.json"
    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    if any(t["product_id"] == product_id for t in targets["products"]):
        sys.exit("not moved")
    pcat = PROJECT / "data/catalog"
    project_docs = read_jsonl(pcat / "documents.jsonl")
    clash = {d["document_id"] for d in project_docs} & {d["document_id"] for d in docs}
    if clash:
        sys.exit(f"not moved: document ids already in the project: {sorted(clash)[:5]}")
    # Files: raw (checked against its hash), the text copy parsing read, parsed pages and their layout text, sample tables.
    for d in docs:
        copy(d["raw_path"])
        if hashlib.sha256((PROJECT / d["raw_path"]).read_bytes()).hexdigest() != d["sha256"]:
            sys.exit(f"hash mismatch after copy: {d['raw_path']}")
        for rel in (d.get("text_path"), d["parsed_path"], d["parsed_path"].replace(".pages.jsonl", ".layout.txt")):
            if rel:
                copy(rel)
    for t in (STAGE / "data/tables").glob(f"{product_id}_p*.json"):
        copy(t.relative_to(STAGE).as_posix())
    for s in studies:
        copy(s["api_source"]["raw_path"])
    # Cache: the project's later runs reuse these files instead of downloading them again.
    stage_cache = json.loads((STAGE / "data/catalog/download_cache.json").read_text(encoding="utf-8"))
    cache_path = pcat / "download_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    wanted = {d["sha256"] for d in docs} | {s["api_source"]["sha256"] for s in studies}
    for url, rec in stage_cache.items():
        if rec["sha256"] in wanted and url not in cache:
            cache[url] = rec
    collect.write_json(cache_path, cache)
    # Catalog rows as checked; the target entry so the project keeps collecting the product.
    if product.get("kind", "biosimilar") == "biosimilar" and not product.get("reference_product_id"):
        # Point the biosimilar at the project's reference product of the same ingredient, if there is exactly one.
        refs = [t["product_id"] for t in targets["products"] if t.get("kind") == "reference" and (t.get("ingredient") or targets["ingredient"]) == product.get("ingredient")]
        product["reference_product_id"] = refs[0] if len(refs) == 1 else None
    write_jsonl(pcat / "documents.jsonl", project_docs + docs)
    write_jsonl(pcat / "products.jsonl", read_jsonl(pcat / "products.jsonl") + [product])
    write_jsonl(pcat / "studies.jsonl", read_jsonl(pcat / "studies.jsonl") + studies)
    write_jsonl(pcat / "study_results.jsonl", read_jsonl(pcat / "study_results.jsonl") + results)
    entry = {k: product.get(k) for k in TARGET_KEYS if product.get(k) not in (None, [])}
    entry.setdefault("pmda_general_list_ids", [])
    entry.setdefault("study_ids", [])
    entry["added"] = dt.date.today().isoformat()
    entry["notes"] = f"Moved from product_search/data (checked staging copy, {len(docs)} documents)."
    text = targets_path.read_text(encoding="utf-8")
    end = text.index("\n  ],\n", text.index('"products": ['))
    targets_path.write_text(text[:end] + ",\n    " + json.dumps(entry, ensure_ascii=False) + text[end:], encoding="utf-8", newline="\n")
    json.loads(targets_path.read_text(encoding="utf-8"))  # still valid JSON
    # Database: the project's loader with the moved pages (nothing is parsed again).
    collect.ROOT = PROJECT
    c = collect.Collector()
    c.pages = [row for d in docs for row in read_jsonl(PROJECT / d["parsed_path"])]
    c.metrics.update({"mode": "promote", "product": product_id, "documents": len(docs)})
    c.database()
    print("moved", product_id, {"documents": len(docs), "pages": len(c.pages), "studies": len(studies), "result_rows": len(results)})
    for script in ("collection.build_indexes", "collection.verify"):
        done = subprocess.run([sys.executable, "-m", script], cwd=PROJECT, capture_output=True, text=True, encoding="utf-8")
        print(script, "ok" if done.returncode == 0 else "FAILED", (done.stdout if script == "collection.build_indexes" else done.stderr)[-600:])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("product_id", nargs="?")
    parser.add_argument("--apply", action="store_true", help="move the product into the project after the check is printed")
    args = parser.parse_args()
    if not args.product_id:
        project = {t["product_id"] for t in json.loads((PROJECT / "configs/collection/targets.json").read_text(encoding="utf-8"))["products"]}
        for p in read_jsonl(STAGE / "data/catalog/products.jsonl"):
            print(p["product_id"], p.get("ema_name") or p.get("us_brand"), "(in project)" if p["product_id"] in project else "(staging only)")
    elif args.apply:
        apply(args.product_id)
    else:
        report(args.product_id)
