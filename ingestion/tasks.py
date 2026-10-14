"""Collection as stateless tasks (plan P4, section 9.1). An Airflow DAG (P6) calls these same functions; until then run_all is the
local runner.

collect.py's steps share one Collector object: documents, licensure and studies pile up in memory and a few steps read lists the
previous step wrote into data/catalog. Here every (step, product) is its own task with a fresh Collector and its own work folder:
  inputs   targets.json entry, the lists earlier tasks produced (ingest.task_artifacts), the raw snapshot cache
  outputs  rows in ingest.task_outputs (documents, EMA product identity, Purple Book licensure, studies, posted results,
           guideline records, downloads, errors) and lists for later tasks (ingest.task_artifacts)
  key      ingest.tasks (run_id, step, product_id, document_version); running a key again replaces its outputs
Parsing is one task per document version. merge() builds the catalog rows exactly as Collector.finish_catalog does, from the
task outputs, and writes files only when asked (never into data/catalog by default).

Usage: uv run python -m ingestion.tasks run [--online] [--refresh] [--products SB17,ABP654] [--steps ema,fda] [--write <dir>]
       uv run python -m ingestion.tasks parity <run_id>      # merged rows vs the current catalog
Default is offline: every response comes from the stored snapshots, so nothing is downloaded and data/ is not changed.
"""
import argparse
import datetime as dt
import json
import sys
import tempfile
import traceback
from pathlib import Path

from psycopg.types.json import Jsonb

from collection import collect
from ingestion import db
from ingestion.contracts import ROOT, TaskKey, TaskResult, load_targets

# Step -> product field it needs (a product without it skips the step); same rule as product_search.collect_products.
NEEDS = {"ema": "ema_name", "ema_documents": "ema_name", "purple": "us_brand", "fda": "us_brand", "clinical_trials": "study_ids",
         "fda_reviews": "us_brand", "fda_letters": "us_brand", "pmda": "pmda_general_list_ids", "health_canada": "ca_brand"}
# Lists a step leaves for later steps, and which step's lists a step reads.
PRODUCES = {"ema": lambda pid: [f"ema_documents_{pid}.jsonl"], "fda": lambda pid: ["fda_products_selected.jsonl", "fda_application_docs_selected.jsonl"]}
READS = {"ema_documents": "ema", "fda_reviews": "fda", "fda_letters": "fda"}
ORDER = ["ema", "purple", "fda", "clinical_trials", "pmda", "health_canada", "ema_documents", "fda_reviews", "fda_letters", "guidelines"]
COMPARED = ["product_id", "source", "role", "document_type", "family", "author", "tier", "default_search", "title", "url", "sha256",
            "raw_path", "parsed_path", "page_count", "nonempty_text_pages", "ingredient", "product_kind"]


def new_run_id():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def targets_raw():
    load_targets()  # validated first
    return json.loads((ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))


def plan(cfg, steps=None, products=None):
    """The (step, product) task keys of a run, in an order that satisfies READS."""
    keys = []
    for step in ORDER:
        if steps and step not in steps:
            continue
        if step == "guidelines":
            if not products:
                keys.append((step, ""))
            continue
        for t in cfg["products"]:
            if (not products or t["product_id"] in products) and t.get(NEEDS[step]):
                keys.append((step, t["product_id"]))
    return keys


def record(conn, key, status, outputs, artifacts, result):
    """Replace the task's row and outputs in one transaction (a key run twice leaves only the second run)."""
    with conn.transaction():
        conn.execute("DELETE FROM ingest.tasks WHERE run_id = %s AND step = %s AND product_id = %s AND document_version = %s",
                     (key.run_id, key.step, key.product_id or "", key.document_version or ""))
        conn.execute("INSERT INTO ingest.tasks (run_id, step, product_id, document_version, status, attempts, result, updated_at) "
                     "VALUES (%s, %s, %s, %s, %s, 1, %s, now())",
                     (key.run_id, key.step, key.product_id or "", key.document_version or "", status, Jsonb(result.model_dump())))
        with conn.cursor().copy("COPY ingest.task_outputs (run_id, step, product_id, document_version, ordinal, kind, payload) FROM STDIN") as copy:
            for i, (kind, payload) in enumerate(outputs):
                copy.write_row((key.run_id, key.step, key.product_id or "", key.document_version or "", i, kind, json.dumps(payload, ensure_ascii=False)))
        for name, content in artifacts.items():
            conn.execute("INSERT INTO ingest.task_artifacts VALUES (%s, %s, %s, %s, %s, %s)",
                         (key.run_id, key.step, key.product_id or "", key.document_version or "", name, content))


def collector(run_id, work, offline, refresh):
    c = collect.Collector(offline=offline, refresh=refresh, work=work)
    c.run_id = run_id  # downloads and documents carry the run's id, not one per task
    return c


def step_task(conn, run_id, step, product_id, cfg, offline=True, refresh=False):
    key = TaskKey(run_id=run_id, step=step, product_id=product_id or None)
    with tempfile.TemporaryDirectory(prefix=f"task_{step}_") as tmp:
        work = Path(tmp)
        if step in READS:  # the lists the earlier task left, written back as files for the step to read
            for name, content in conn.execute("SELECT name, content FROM ingest.task_artifacts WHERE run_id = %s AND step = %s AND product_id = %s",
                                              (run_id, READS[step], product_id)):
                (work / name).write_text(content, encoding="utf-8")
        one = dict(cfg, products=[t for t in cfg["products"] if t["product_id"] == product_id]) if product_id else cfg
        c = collector(run_id, work, offline, refresh)
        c.review_sample_pages = cfg.get("review_sample_pages", {})
        failed = None
        try:
            getattr(c, step)(one)
        except Exception as exc:  # a step-level failure (e.g. no snapshot offline); per-item failures are already in c.errors
            failed = f"{type(exc).__name__}: {exc}"
            c.errors.append({"label": f"{step} {product_id}", "error_type": type(exc).__name__, "message": str(exc), "at": collect.now(),
                             "trace": traceback.format_exc()[-800:]})
        outputs = ([("document", d) for d in c.documents] + [("ema_product", p) for p in c.products]
                   + [("licensure", {"product_id": k, **v}) for k, v in c.licensure.items()] + [("study", s) for s in c.studies]
                   + [("study_result", r) for r in c.study_results] + [("download", d) for d in c.downloads] + [("error", e) for e in c.errors])
        if step == "guidelines" and (work / "guidelines.jsonl").exists():
            outputs += [("guideline_record", r) for r in collect.read_jsonl(work / "guidelines.jsonl")]
        names = PRODUCES[step](product_id) if step in PRODUCES else []
        artifacts = {n: (work / n).read_text(encoding="utf-8") for n in names if (work / n).exists()}
    status = "failed" if failed or (names and len(artifacts) != len(names)) else "succeeded"
    result = TaskResult(key=key, status=status, documents=[d["document_id"] for d in c.documents],
                        errors=[{k: str(v) for k, v in e.items() if k != "trace"} for e in c.errors],
                        metrics={"documents": len(c.documents), "downloads": len(c.downloads), "fetched": sum(not d.get("cache_reused") for d in c.downloads)})
    record(conn, key, status, outputs, artifacts, result)
    return result


def parse_task(conn, run_id, doc, cfg, offline=True, parse_new=False):
    """Parse one document version (reused when the catalog already has it parsed, as collect.py does). A version the catalog has
    not parsed is parsed only with parse_new (online runs); otherwise the task is 'skipped', so a comparison run writes nothing
    into data/parsed."""
    key = TaskKey(run_id=run_id, step="parse", product_id=doc.get("product_id"), document_version=f"{doc['document_id']}@{doc['version_id']}")
    with tempfile.TemporaryDirectory(prefix="task_parse_") as tmp:
        c = collector(run_id, tmp, offline, False)
        c.review_sample_pages = cfg.get("review_sample_pages", {})
        c.documents = [dict(doc)]
        failed = None
        known = any(d["document_id"] == doc["document_id"] and d["version_id"] == doc["version_id"] and d.get("parsed_path")
                    for d in collect.read_jsonl(ROOT / "data/catalog/documents.jsonl"))
        if not known and not parse_new:
            result = TaskResult(key=key, status="skipped", documents=[doc["document_id"]], errors=[], metrics={"needs_parse": 1})
            record(conn, key, "skipped", [], {}, result)
            return result
        try:
            c.parse()
        except Exception as exc:
            failed = f"{type(exc).__name__}: {exc}"
    status = "failed" if failed else "succeeded"
    result = TaskResult(key=key, status=status, documents=[doc["document_id"]], errors=[{"error": failed}] if failed else [],
                        metrics={"pages": c.documents[0].get("page_count") or 0, "reused": int(bool(c.documents[0].get("parsed_path")) and not c.pages)})
    record(conn, key, status, [("parsed_document", c.documents[0])] if not failed else [], {}, result)
    return result


def outputs(conn, run_id, kind):
    return [r[0] for r in conn.execute("SELECT payload FROM ingest.task_outputs WHERE run_id = %s AND kind = %s ORDER BY step, product_id, document_version, ordinal",
                                       (run_id, kind))]


def merge(conn, run_id, cfg):
    """Catalog rows from the run's outputs, as Collector.finish_catalog builds them (documents not produced again are carried)."""
    with tempfile.TemporaryDirectory(prefix="task_merge_") as tmp:
        c = collector(run_id, tmp, True, False)
        c.products = outputs(conn, run_id, "ema_product")
        c.licensure = {r["product_id"]: {k: v for k, v in r.items() if k != "product_id"} for r in outputs(conn, run_id, "licensure")}
        new = outputs(conn, run_id, "parsed_document")
        for d in new:
            d["last_seen_run"] = run_id
        existing = collect.read_jsonl(ROOT / "data/catalog/documents.jsonl")
        produced = {d["document_id"] for d in new}
        order = {d["document_id"]: i for i, d in enumerate(existing)}
        docs = sorted([d for d in existing if d["document_id"] not in produced] + new, key=lambda d: order.get(d["document_id"], len(order)))
        products = c.product_records(cfg)
    ingredient = {p["product_id"]: p["ingredient"] for p in products}
    kind = {p["product_id"]: p.get("kind") or "biosimilar" for p in products}
    for d in docs:
        collect.classify(d)
        d["ingredient"] = ingredient.get(d.get("product_id"))
        d["product_kind"] = kind.get(d.get("product_id"))
    studies_new = outputs(conn, run_id, "study")
    refreshed = {s["nct_id"] for s in studies_new}
    studies = [s for s in collect.read_jsonl(ROOT / "data/catalog/studies.jsonl") if s["nct_id"] not in refreshed] + studies_new
    results = [r for r in collect.read_jsonl(ROOT / "data/catalog/study_results.jsonl") if r["nct_id"] not in refreshed] + outputs(conn, run_id, "study_result")
    records = outputs(conn, run_id, "guideline_record")
    done = {r["guideline_id"] for r in records}
    guidelines = [r for r in collect.read_jsonl(ROOT / "data/catalog/guidelines.jsonl") if r["guideline_id"] not in done] + records
    return {"documents": docs, "products": products, "studies": studies, "study_results": results, "guidelines": guidelines,
            "carried": sorted(d["document_id"] for d in docs if d["document_id"] not in produced)}


def write(merged, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if folder.resolve() == (ROOT / "data/catalog").resolve():
        raise SystemExit("merge does not write into data/catalog; the JSONL catalog stays collect.py's (and promote's) until the DB is the source")
    for name in ("documents", "products", "studies", "study_results", "guidelines"):
        collect.write_jsonl(folder / f"{name}.jsonl", merged[name])


def run_all(conn, offline=True, refresh=False, steps=None, products=None, log=print):
    cfg = targets_raw()
    run_id = new_run_id()
    conn.execute("INSERT INTO ingest.runs VALUES (%s, 'tasks', now(), NULL, %s)",
                 (run_id, Jsonb({"offline": offline, "refresh": refresh, "steps": steps, "products": products})))
    summary = {"run_id": run_id, "tasks": 0, "failed": [], "documents": 0, "parsed": 0, "fetched": 0}
    for step, product_id in plan(cfg, steps, products):
        r = step_task(conn, run_id, step, product_id, cfg, offline, refresh)
        summary["tasks"] += 1
        summary["documents"] += r.metrics["documents"]
        summary["fetched"] += r.metrics["fetched"]
        if r.status != "succeeded":
            summary["failed"].append(f"{step}:{product_id}")
        log(f"{step:16} {product_id or '-':10} {r.status:9} documents={r.metrics['documents']} errors={len(r.errors)}")
    docs = outputs(conn, run_id, "document")
    for d in docs:
        r = parse_task(conn, run_id, d, cfg, offline, parse_new=not offline)
        summary["parsed"] += r.status == "succeeded"
        if r.status == "skipped":
            summary.setdefault("not_parsed_offline", []).append(d["document_id"])
        elif r.status != "succeeded":
            summary["failed"].append(f"parse:{d['document_id']}")
    conn.execute("UPDATE ingest.runs SET finished_at = now(), metrics = %s WHERE run_id = %s", (Jsonb(summary), run_id))
    return run_id, summary


def parity(conn, run_id):
    """Merged rows of a run against the current catalog: document ids and versions, the compared fields, products, studies, results."""
    merged = merge(conn, run_id, targets_raw())
    catalog = {d["document_id"]: d for d in collect.read_jsonl(ROOT / "data/catalog/documents.jsonl")}
    ours = {d["document_id"]: d for d in merged["documents"]}
    field_diffs = [(i, f, catalog[i].get(f), ours[i].get(f)) for i in sorted(catalog.keys() & ours.keys()) for f in COMPARED if catalog[i].get(f) != ours[i].get(f)]
    products_then = {p["product_id"]: p for p in collect.read_jsonl(ROOT / "data/catalog/products.jsonl")}
    product_diffs = [(pid, k) for pid, p in ((p["product_id"], p) for p in merged["products"])
                     for k in ("ema_product_number", "us_licensure", "us_proper_name", "reference_product", "kind", "ingredient")
                     if json.dumps(p.get(k), sort_keys=True) != json.dumps(products_then.get(pid, {}).get(k), sort_keys=True)]
    out = {"run_id": run_id,
           "documents": {"catalog": len(catalog), "merged": len(ours), "produced_by_run": len(ours) - len(merged["carried"]), "carried_not_produced": merged["carried"]},
           "versions_equal": {(i, d["version_id"]) for i, d in catalog.items()} == {(i, d["version_id"]) for i, d in ours.items()},
           "field_differences": field_diffs[:20], "field_difference_count": len(field_diffs), "product_differences": product_diffs,
           "studies_equal": sorted(s["nct_id"] for s in merged["studies"]) == sorted(s["nct_id"] for s in collect.read_jsonl(ROOT / "data/catalog/studies.jsonl")),
           "study_results_equal": sorted((r["nct_id"], r["outcome_index"]) for r in merged["study_results"])
                                  == sorted((r["nct_id"], r["outcome_index"]) for r in collect.read_jsonl(ROOT / "data/catalog/study_results.jsonl"))}
    out["passed"] = (out["versions_equal"] and not field_diffs and not product_diffs and out["studies_equal"] and out["study_results_equal"])
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "parity"])
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--online", action="store_true", help="fetch lists and new documents (default: stored snapshots only)")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--products")
    ap.add_argument("--steps")
    ap.add_argument("--write", help="folder for the merged catalog files (never data/catalog)")
    a = ap.parse_args()
    with db.connect() as conn:
        if a.command == "run":
            run_id, summary = run_all(conn, not a.online, a.refresh, a.steps.split(",") if a.steps else None, a.products.split(",") if a.products else None)
            print(json.dumps(summary, ensure_ascii=False))
            if a.write:
                write(merge(conn, run_id, targets_raw()), a.write)
        else:
            print(json.dumps(parity(conn, a.run_id), ensure_ascii=False, indent=2, default=list))
