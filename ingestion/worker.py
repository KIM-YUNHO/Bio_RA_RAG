"""Runs ingestion requests (plan P3; later an Airflow DAG calls the same functions, section 9).

stage <request_id>               queued -> staging -> awaiting_approval: product_search.collect_products --plan, then the
                                 promote check table per product (kept with its SHA-256 in request_events).
promote <request_id> <approval>  approved -> promoting -> done: the check table is produced again and must hash to what the person
                                 approved; then product_search.promote --apply, the catalog reload into PostgreSQL, its parity
                                 check and a registry rebuild.
Logs: data/runs/ingest_requests/<request_id>/.
"""
import hashlib
import json
import subprocess
import sys

from psycopg.types.json import Jsonb

from ingestion import db, db_verify, load_catalog, registry
from ingestion.contracts import ROOT

RUNS = ROOT / "data/runs/ingest_requests"


def event(conn, request_id, name, payload):
    conn.execute("INSERT INTO ingest.request_events (request_id, event, payload) VALUES (%s, %s, %s)", (request_id, name, Jsonb(payload)))


def set_status(conn, request_id, status):
    conn.execute("UPDATE ingest.requests SET status = %s WHERE request_id = %s", (status, request_id))


def run(args, log_name, folder):
    """A project command in this interpreter; output kept in the request's folder."""
    done = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (folder / log_name).write_text(done.stdout + "\n--- stderr ---\n" + done.stderr, encoding="utf-8")
    return done


def report(product_id, folder, name):
    done = run(["product_search.promote", product_id], name, folder)
    if done.returncode != 0:
        raise RuntimeError(f"promote check failed for {product_id}: {done.stderr[-300:]}")
    return done.stdout, hashlib.sha256(done.stdout.encode("utf-8")).hexdigest()


def check_here(product_id, request_id):
    """SHA-256 of the check table rebuilt from this folder's staging, or None if it cannot be built here.
    Projects that share the database stage into their own product_search/data, so approval checks this before promoting."""
    folder = RUNS / request_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        return report(product_id, folder, f"promote_check_{product_id}_at_approval.txt")[1]
    except RuntimeError:
        return None


def stage(conn, request_id):
    folder = RUNS / request_id
    folder.mkdir(parents=True, exist_ok=True)
    payload, status = conn.execute("SELECT payload, status FROM ingest.requests WHERE request_id = %s", (request_id,)).fetchone()
    if status != "queued":
        raise RuntimeError(f"request {request_id} is {status}, not queued")
    planned = conn.execute("SELECT payload FROM ingest.request_events WHERE request_id = %s AND event = 'planned' ORDER BY event_id DESC LIMIT 1",
                           (request_id,)).fetchone()[0]
    set_status(conn, request_id, "staging")
    event(conn, request_id, "staging_started", {"products": [p["product_id"] for p in payload["products"]]})
    try:
        (folder / "plan.json").write_text(json.dumps(planned["discover_entries"], ensure_ascii=False, indent=1), encoding="utf-8")
        done = run(["product_search.collect_products", "--plan", str(folder / "plan.json")], "collect_products.log", folder)
        if done.returncode != 0:
            raise RuntimeError(f"collect_products exited {done.returncode}: {done.stderr[-300:]}")
        for p in payload["products"]:
            text, digest = report(p["product_id"], folder, f"promote_check_{p['product_id']}.txt")
            event(conn, request_id, "staged", {"product_id": p["product_id"], "report": text, "report_sha256": digest, "stage_root": str(ROOT)})
        set_status(conn, request_id, "awaiting_approval")
    except Exception as exc:
        event(conn, request_id, "failed", {"step": "staging", "error": f"{type(exc).__name__}: {exc}"})
        set_status(conn, request_id, "failed")
        raise


def promote(conn, request_id, approval_id):
    folder = RUNS / request_id
    folder.mkdir(parents=True, exist_ok=True)
    status = conn.execute("SELECT status FROM ingest.requests WHERE request_id = %s", (request_id,)).fetchone()[0]
    product_id, approved_sha = conn.execute("SELECT product_id, report_sha256 FROM ingest.approvals WHERE approval_id = %s AND request_id = %s",
                                            (approval_id, request_id)).fetchone()
    if status != "approved":
        raise RuntimeError(f"request {request_id} is {status}, not approved")
    set_status(conn, request_id, "promoting")
    try:
        _, digest = report(product_id, folder, f"promote_check_{product_id}_at_apply.txt")
        if digest != approved_sha:
            raise RuntimeError("the check table changed after it was approved; stage and approve again")
        done = run(["product_search.promote", product_id, "--apply"], f"promote_apply_{product_id}.log", folder)
        if done.returncode != 0 or f"moved {product_id}" not in done.stdout:
            raise RuntimeError(f"promote --apply did not move {product_id}: {(done.stdout + done.stderr)[-400:]}")
        event(conn, request_id, "promoted", {"product_id": product_id, "output_tail": done.stdout[-1500:]})
        counts = load_catalog.load(conn)
        problems, facts = db_verify.check(conn)
        names = registry.Builder(conn).build()
        event(conn, request_id, "database_updated", {"catalog": counts, "parity_problems": problems, "registry": names})
        set_status(conn, request_id, "done" if not problems else "failed")
    except Exception as exc:
        event(conn, request_id, "failed", {"step": "promote", "error": f"{type(exc).__name__}: {exc}"})
        set_status(conn, request_id, "failed")
        raise


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with db.connect() as conn:
        if sys.argv[1] == "stage":
            stage(conn, sys.argv[2])
        elif sys.argv[1] == "promote":
            promote(conn, sys.argv[2], sys.argv[3])
        else:
            raise SystemExit(__doc__)
    print("done", sys.argv[1:])
