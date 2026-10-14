"""Collect the biosimilar guidelines in the project's guidelines.json into product_search/data, beside the product documents.

The collection rule (status read from each landing page, no product_id, role "guideline") is collect.Collector.guidelines,
the same step the project runs. Collecting again replaces the guideline rows and keeps every product row.
Usage: uv run python -m product_search.guidelines            # all
       uv run python -m product_search.guidelines FDA-QA-REV4 # some
"""
import json
import sys

from product_search import collect_products, discover
from collection import collect
from collection.collect import write_jsonl

HERE = discover.HERE


def run(ids=None):
    c = collect_products.Collector()
    c.guidelines(collect_products.BASE, ids)
    products = [p for p in discover.read_jsonl(HERE / "data/catalog/products.jsonl")]
    # finish_catalog needs the products to keep their records; no product step ran, so their values are carried over.
    docs = c.finish_catalog(dict(collect_products.BASE, ingredient=None, products=[collect_products.target_of(p) for p in products]))
    c.metrics.update({"finished_at": collect.now(), "documents": len(c.documents), "catalog_documents": len(docs),
                      "pages": sum(d["page_count"] for d in c.documents), "error_count": len(c.errors)})
    c.database()
    collect.write_json(HERE / f"data/runs/{c.run_id}.summary.json", c.metrics)
    write_jsonl(HERE / f"data/runs/{c.run_id}.errors.jsonl", c.errors)
    print(json.dumps({k: c.metrics[k] for k in ("documents", "catalog_documents", "pages", "error_count")}, ensure_ascii=False), flush=True)
    return c


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run(sys.argv[1:] or None)
