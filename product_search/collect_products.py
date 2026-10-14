"""Collect the selected products into product_search/data with collect.py's own steps, parser and SQLite loader.

Usage:
  uv run python -m product_search.collect_products yesintek usymro      # search each name, collect what it finds
  uv run python -m product_search.collect_products --plan plan.json     # a list of product entries from discover.search
Products already in product_search/data are collected again: documents already there keep their stored version (collect.py's
reuse rule), new ones are added, and other products' rows are kept. Every rule (FDA letters and latest label, Purple Book
licensure, posted trial results, document tiers, ingredient and reference product) lives in collect.py; see
docs/collection/INGESTION_PIPELINE.md. Guidelines are collected with product_search.guidelines.
"""
import argparse
import json
import os
import shutil
import sys

from product_search import discover  # sets collect.ROOT to product_search/
from collection import collect
from collection.collect import write_json, write_jsonl

HERE = discover.HERE
CATALOG = HERE / "data/catalog"
# Step -> product field it needs; a product without that field (EU-only, US-only, no PMDA page) skips the step.
NEEDS = {"ema": "ema_name", "ema_documents": "ema_name", "purple": "us_brand", "fda": "us_brand", "clinical_trials": "study_ids",
         "fda_reviews": "us_brand", "fda_letters": "us_brand", "pmda": "pmda_general_list_ids", "health_canada": "ca_brand"}
STEPS = [s for s in collect.STEPS if s != "guidelines"]  # guidelines are not per product
TARGET_FIELDS = ["product_id", "ingredient", "development_code", "ema_name", "us_brand", "aliases", "study_ids", "ca_brand", "pmda_general_list_ids",
                 "company", "licences", "kind", "reference_product_id"]
# Project-only keys: the product list, the project's sample pages, and the reference product declared for ustekinumab.
BASE = {k: v for k, v in discover.PROJECT_CFG.items() if k not in ("products", "review_sample_pages", "reference_product") and not k.startswith("_")}


class Collector(collect.Collector):
    """collect.Collector that downloads each URL once per run: the EMA lists, Purple Book CSV and Drugs@FDA ZIP serve every product."""
    def __init__(self):
        super().__init__()
        self.memo = {}

    def fetch(self, url, kind, source, parent_url=None):
        if url in self.memo:
            self.downloads.append(self.memo[url][1])
        else:
            self.memo[url] = super().fetch(url, kind, source, parent_url)
        return self.memo[url]


def target_of(p):
    return {k: p.get(k) or ([] if k in ("aliases", "study_ids", "pmda_general_list_ids", "licences") else None) for k in TARGET_FIELDS}


def run(products):
    if not shutil.which("pdftotext"):  # collect.parse writes a pdftotext -layout copy; Git for Windows ships one
        os.environ["PATH"] += os.pathsep + r"C:\Program Files\Git\mingw64\bin"
    c = Collector()
    targets = [target_of(p) for p in products]
    for target in targets:
        # One product per pass: the per-product catalog files (Drugs@FDA rows, Purple Book rows) hold the product being collected.
        cfg = dict(BASE, ingredient=target["ingredient"], ingredient_ja="", products=[target])
        print("==", target["product_id"], {k: target[k] for k in ("ema_name", "us_brand", "ca_brand", "study_ids")}, flush=True)
        for step in STEPS:
            if target[NEEDS[step]]:
                c.attempt(f"{target['product_id']} {step}", lambda s=step, conf=cfg: getattr(c, s)(conf))
    docs = c.finish_catalog(dict(BASE, ingredient=targets[0]["ingredient"], products=targets))
    c.metrics.update({"finished_at": collect.now(), "products": [t["product_id"] for t in targets], "download_count": len(c.downloads),
                      "fetched_documents": sum(not d.get("cache_reused") for d in c.documents), "catalog_documents": len(docs),
                      "pages": sum(d["page_count"] for d in c.documents), "study_result_rows": len(c.study_results), "error_count": len(c.errors)})
    c.database()  # products, studies, posted results, document versions; pages of this run; stale versions removed; page_search rebuilt
    write_json(HERE / f"data/runs/{c.run_id}.summary.json", c.metrics)
    write_jsonl(HERE / f"data/runs/{c.run_id}.downloads.jsonl", c.downloads)
    write_jsonl(HERE / f"data/runs/{c.run_id}.errors.jsonl", c.errors)
    print(json.dumps(c.metrics, ensure_ascii=False, indent=2), flush=True)
    return c


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("queries", nargs="*", help="Ingredient or product names")
    parser.add_argument("--plan", help="JSON list of product entries (discover.search()['products'])")
    args = parser.parse_args()
    if bool(args.plan) == bool(args.queries):
        parser.error("give names or --plan")
    chosen = json.loads(open(args.plan, encoding="utf-8").read()) if args.plan else [p for q in args.queries for p in discover.search(q)["products"]]
    if not chosen:
        parser.error("no products found")
    run(chosen)
