"""Load the collection catalog (data/catalog/*.jsonl, data/parsed/*.pages.jsonl) into PostgreSQL (plan P1).

While the JSONL catalog is the source of truth, the registry and corpus schemas are its mirror: each load replaces them in one
transaction, so a failed load leaves the previous copy. ingest.requests, request_events, tasks and approvals are never touched.
targets.json and guidelines.json are validated with ingestion.contracts before anything is written.

Usage: uv run python -m ingestion.load_catalog          then  uv run python -m ingestion.db_verify
"""
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

from ingestion import db
from ingestion.contracts import ROOT, load_guidelines, load_targets

CATALOG = ROOT / "data/catalog"
RUN_ID = re.compile(r"^\d{8}T\d{6}Z$")
MIRRORED = ["corpus.pages", "corpus.document_versions", "corpus.documents", "corpus.study_results", "corpus.studies",
            "corpus.guidelines", "corpus.downloads", "corpus.raw_objects", "registry.licences", "registry.products", "registry.ingredients"]


def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()] if path.exists() else []


def when(value):
    return dt.datetime.fromisoformat(value) if value else None


def day(value):
    return dt.date.fromisoformat(value) if value else None


def without_nul(value):
    """PostgreSQL text and jsonb cannot hold U+0000. It occurs only in block text of pages whose fonts have no Unicode map
    (raw glyph codes; 329 characters on 20 pages of 3 documents on 2026-10-14), never in page text. It becomes U+FFFD."""
    if isinstance(value, str):
        return value.replace("\x00", "�"), value.count("\x00")
    if isinstance(value, dict):
        pairs = {k: without_nul(v) for k, v in value.items()}
        return {k: v for k, (v, _) in pairs.items()}, sum(n for _, n in pairs.values())
    if isinstance(value, list):
        pairs = [without_nul(v) for v in value]
        return [v for v, _ in pairs], sum(n for _, n in pairs)
    return value, 0


def page_rows(doc):
    """Parsed pages of one catalog document, checked against the catalog row before they are loaded."""
    rows = read_jsonl(ROOT / doc["parsed_path"])
    if [r["pdf_page"] for r in rows] != list(range(1, doc["page_count"] + 1)):
        raise ValueError(f"{doc['document_id']}: parsed pages do not match page_count {doc['page_count']}")
    if any(r["version_id"] != doc["version_id"] for r in rows):
        raise ValueError(f"{doc['document_id']}: parsed pages belong to another version")
    return rows


def load_runs(conn):
    """Collection runs from data/runs/<run_id>.* and data/summary.json; existing rows are replaced (kind 'catalog_load' rows stay)."""
    runs = {}
    for path in (ROOT / "data/runs").glob("*.summary.json"):
        if RUN_ID.match(path.name.split(".")[0]):
            runs[path.name.split(".")[0]] = json.loads(path.read_text(encoding="utf-8"))
    full = json.loads((ROOT / "data/summary.json").read_text(encoding="utf-8"))
    runs.setdefault(full["run_id"], full)
    for path in (ROOT / "data/runs").glob("*.downloads.jsonl"):
        runs.setdefault(path.name.split(".")[0], {})
    runs = {k: v for k, v in runs.items() if RUN_ID.match(k)}
    conn.execute("DELETE FROM ingest.run_downloads WHERE run_id = ANY(%s)", (list(runs),))
    conn.execute("DELETE FROM ingest.run_errors WHERE run_id = ANY(%s)", (list(runs),))
    conn.execute("DELETE FROM ingest.runs WHERE run_id = ANY(%s)", (list(runs),))
    for run_id, m in sorted(runs.items()):
        kind = m.get("mode") or ("collect" if run_id == full["run_id"] else "partial" if m else "unknown")
        conn.execute("INSERT INTO ingest.runs VALUES (%s, %s, %s, %s, %s)",
                     (run_id, kind, when(m.get("started_at")), when(m.get("finished_at")), Jsonb(m)))
        with conn.cursor().copy("COPY ingest.run_downloads FROM STDIN") as copy:
            for i, d in enumerate(read_jsonl(ROOT / f"data/runs/{run_id}.downloads.jsonl")):
                copy.write_row((run_id, i, d["url"], d["sha256"], bool(d.get("cache_reused")), json.dumps(d, ensure_ascii=False)))
        for i, e in enumerate(read_jsonl(ROOT / f"data/runs/{run_id}.errors.jsonl")):
            conn.execute("INSERT INTO ingest.run_errors VALUES (%s, %s, %s, %s, %s, %s)",
                         (run_id, i, e["label"], e["error_type"], e["message"], when(e.get("at"))))
    return len(runs)


def load(conn):
    targets, guidelines = load_targets(), load_guidelines()
    documents = read_jsonl(CATALOG / "documents.jsonl")
    products = {p["product_id"]: p for p in read_jsonl(CATALOG / "products.jsonl")}
    cache = json.loads((CATALOG / "download_cache.json").read_text(encoding="utf-8"))
    counts = {}
    with conn.transaction():
        conn.execute("SET CONSTRAINTS ALL DEFERRED")
        conn.execute("TRUNCATE " + ", ".join(MIRRORED))

        ingredients = {targets.ingredient_of(p) for p in targets.products}
        for inn in sorted(ingredients):
            conn.execute("INSERT INTO registry.ingredients VALUES (%s, %s)", (inn, targets.ingredient_ja if inn == targets.ingredient else None))
        for p in targets.products:
            conn.execute("INSERT INTO registry.products VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         (p.product_id, targets.ingredient_of(p), p.kind, p.reference_product_id, p.development_code, p.ema_name,
                          p.us_brand, p.ca_brand, p.company, day(p.added), Jsonb(p.model_dump()), Jsonb(products.get(p.product_id))))
            for l in p.licences:
                conn.execute("INSERT INTO registry.licences VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                             (p.product_id, l.jurisdiction, l.name, l.licence_id, l.holder, day(l.authorised), l.primary, l.collected))

        # Raw objects: every file the cache points at, plus every document version (a version is always a cached download).
        objects = {}
        for rec in cache.values():
            objects[rec["sha256"]] = (rec["sha256"], rec["raw_path"], rec["bytes"], rec["kind"], rec.get("content_type"))
        for d in documents:
            objects.setdefault(d["sha256"], (d["sha256"], d["raw_path"], d["bytes"], d["kind"], d.get("content_type")))
        with conn.cursor().copy("COPY corpus.raw_objects FROM STDIN") as copy:
            for row in objects.values():
                copy.write_row(row)
        with conn.cursor().copy("COPY corpus.downloads FROM STDIN") as copy:
            for u, rec in cache.items():
                copy.write_row((u, rec["sha256"], rec["source"], rec["retrieved_at"], rec.get("run_id"), json.dumps(rec, ensure_ascii=False)))

        pages = 0
        for d in documents:
            conn.execute("INSERT INTO corpus.documents VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         (d["document_id"], d["version_id"], d["product_id"], d["ingredient"], d["product_kind"], d["source"], d["role"],
                          d["document_type"], d["family"], d["author"], d["tier"], d["default_search"], d["title"], d["url"],
                          d.get("nct_id"), d.get("last_seen_run")))
            conn.execute("INSERT INTO corpus.document_versions VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         (d["document_id"], d["version_id"], d["raw_path"], d.get("text_path"), d["parsed_path"], d["page_count"],
                          d["nonempty_text_pages"], d["parser"], d["parser_version"], when(d["retrieved_at"]), Jsonb(d)))
            with conn.cursor().copy("COPY corpus.pages (document_id, version_id, pdf_page, pdf_page_label, locator_type, text, text_sha256, metadata) FROM STDIN") as copy:
                for r in page_rows(d):
                    meta, replaced = without_nul({k: v for k, v in r.items() if k != "text"})
                    if replaced:
                        meta["db_nul_replaced"] = replaced
                    if "\x00" in r["text"]:
                        raise ValueError(f"{doc['document_id']} p{r['pdf_page']}: U+0000 in page text")
                    copy.write_row((r["document_id"], r["version_id"], r["pdf_page"], r.get("pdf_page_label"), r.get("locator_type", "pdf_page"),
                                    r["text"], hashlib.sha256(r["text"].encode("utf-8")).hexdigest(), json.dumps(meta, ensure_ascii=False)))
                    pages += 1

        studies = read_jsonl(CATALOG / "studies.jsonl")
        for s in studies:
            conn.execute("INSERT INTO corpus.studies VALUES (%s, %s, %s, %s, %s)", (s["nct_id"], s["product_id"], s["phases"], s["has_results"], Jsonb(s)))
        results = read_jsonl(CATALOG / "study_results.jsonl")
        for r in results:
            conn.execute("INSERT INTO corpus.study_results VALUES (%s, %s, %s, %s, %s, %s)",
                         (r["nct_id"], r["outcome_index"], r["product_id"], r.get("title"), r["review_status"], Jsonb(r)))
        seeds = {g.id: g for g in guidelines.guidelines}
        records = read_jsonl(CATALOG / "guidelines.jsonl")
        for g in records:
            conn.execute("INSERT INTO corpus.guidelines VALUES (%s, %s, %s, %s, %s, %s)",
                         (g["guideline_id"], g["jurisdiction"], g.get("status"), day(g.get("checked")), Jsonb(seeds[g["guideline_id"]].model_dump()), Jsonb(g)))

        counts = {"products": len(targets.products), "raw_objects": len(objects), "downloads": len(cache), "documents": len(documents),
                  "pages": pages, "studies": len(studies), "study_results": len(results), "guidelines": len(records), "runs": load_runs(conn)}
        load_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        conn.execute("INSERT INTO ingest.runs VALUES (%s, 'catalog_load', now(), now(), %s) ON CONFLICT (run_id) DO NOTHING",
                     ("load-" + load_id, Jsonb(counts)))
    return counts


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with db.connect() as conn:
        print(json.dumps(load(conn), ensure_ascii=False))
