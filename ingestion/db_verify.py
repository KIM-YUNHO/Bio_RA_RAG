"""Parity check between the JSONL catalog (source of truth during P1) and its PostgreSQL mirror. Exits non-zero on any difference.

Compared: products and licences (targets.json), document versions and their catalog rows, every page's text hash, studies,
posted results, guidelines, raw objects and their files, and the SQLite copy's counts. Usage: uv run python -m ingestion.db_verify
"""
import hashlib
import json
import sqlite3
import sys

from ingestion import db
from ingestion.contracts import ROOT, load_targets
from ingestion.load_catalog import CATALOG, read_jsonl


def check(conn):
    problems, facts = [], {}

    def same(name, expected, actual):
        expected, actual = set(expected), set(actual)
        facts[name] = len(actual)
        if expected != actual:
            problems.append({"check": name, "missing_in_db": sorted(map(str, expected - actual))[:5], "extra_in_db": sorted(map(str, actual - expected))[:5]})

    targets = load_targets()
    same("products", [p.product_id for p in targets.products], [r[0] for r in conn.execute("SELECT product_id FROM registry.products")])
    same("licences", [(p.product_id, l.jurisdiction, l.name) for p in targets.products for l in p.licences],
         conn.execute("SELECT product_id, jurisdiction, name FROM registry.licences").fetchall())

    documents = read_jsonl(CATALOG / "documents.jsonl")
    same("document_versions", [(d["document_id"], d["version_id"]) for d in documents],
         conn.execute("SELECT document_id, version_id FROM corpus.document_versions").fetchall())
    same("current_versions", [(d["document_id"], d["version_id"]) for d in documents],
         conn.execute("SELECT document_id, current_version_id FROM corpus.documents").fetchall())
    stored = dict(((r[0], r[1]), r[2]) for r in conn.execute("SELECT document_id, version_id, metadata FROM corpus.document_versions"))
    changed = [d["document_id"] for d in documents if stored.get((d["document_id"], d["version_id"])) != d]
    if changed:
        problems.append({"check": "catalog_rows", "differ": changed[:5]})

    expected_pages = []
    for d in documents:
        for r in read_jsonl(ROOT / d["parsed_path"]):
            expected_pages.append((r["document_id"], r["version_id"], r["pdf_page"], hashlib.sha256(r["text"].encode("utf-8")).hexdigest()))
    actual_pages = conn.execute("SELECT document_id, version_id, pdf_page, text_sha256 FROM corpus.pages").fetchall()
    same("pages", expected_pages, actual_pages)
    rehashed = conn.execute("SELECT count(*) FROM corpus.pages WHERE encode(sha256(convert_to(text, 'UTF8')), 'hex') <> text_sha256").fetchone()[0]
    if rehashed:
        problems.append({"check": "page_text_hash", "pages_whose_text_does_not_match_its_hash": rehashed})

    same("studies", [s["nct_id"] for s in read_jsonl(CATALOG / "studies.jsonl")], [r[0] for r in conn.execute("SELECT nct_id FROM corpus.studies")])
    same("study_results", [(r["nct_id"], r["outcome_index"]) for r in read_jsonl(CATALOG / "study_results.jsonl")],
         conn.execute("SELECT nct_id, outcome_index FROM corpus.study_results").fetchall())
    same("guidelines", [g["guideline_id"] for g in read_jsonl(CATALOG / "guidelines.jsonl")], [r[0] for r in conn.execute("SELECT guideline_id FROM corpus.guidelines")])

    cache = json.loads((CATALOG / "download_cache.json").read_text(encoding="utf-8"))
    same("downloads", cache, [r[0] for r in conn.execute("SELECT url FROM corpus.downloads")])
    missing_files = [sha for sha, uri, size in conn.execute("SELECT sha256, uri, bytes FROM corpus.raw_objects")
                     if not (ROOT / uri).exists() or (ROOT / uri).stat().st_size != size]
    if missing_files:
        problems.append({"check": "raw_object_files", "missing_or_wrong_size": missing_files[:5]})

    # The SQLite copy is built from the same catalog by collect.py; its counts must agree too.
    lite = sqlite3.connect(ROOT / "data/collection.sqlite")
    for table, pg in (("document_versions", "corpus.document_versions"), ("pages", "corpus.pages"), ("studies", "corpus.studies"),
                      ("study_results", "corpus.study_results"), ("products", "registry.products")):
        a, b = lite.execute(f"SELECT count(*) FROM {table}").fetchone()[0], conn.execute(f"SELECT count(*) FROM {pg}").fetchone()[0]
        if a != b:
            problems.append({"check": f"sqlite_{table}", "sqlite": a, "postgres": b})
    lite.close()

    facts["fts_auc_cmax_pages"] = conn.execute("SELECT count(*) FROM corpus.pages WHERE tsv @@ to_tsquery('simple', 'auc & cmax')").fetchone()[0]
    if not facts["fts_auc_cmax_pages"]:
        problems.append({"check": "full_text_search", "message": "no page matches auc & cmax"})
    return problems, facts


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with db.connect() as conn:
        problems, facts = check(conn)
    print(json.dumps({"status": "failed" if problems else "passed", "counts": facts, "problems": problems}, ensure_ascii=False, indent=2))
    sys.exit(1 if problems else 0)
