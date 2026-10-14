#!/usr/bin/env python3
"""Load version-pinned visual spot checks with canonical study keys; expert review remains pending."""
import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))
import study_registry as sr


def validate(db, rows):
    checked, seen = [], set()
    for row in rows:
        r = dict(row)
        if r["observation_id"] in seen:
            raise ValueError(f"Duplicate observation: {r['observation_id']}")
        seen.add(r["observation_id"])
        ref = r["evidence"]
        source = db.execute("SELECT product_id FROM pages WHERE document_id=? AND version_id=? AND pdf_page=?",
                            (ref["document_id"], ref["version_id"], ref["pdf_page"])).fetchone()
        if not source or source[0] != r["product_id"]:
            raise ValueError(f"Missing or wrong-product source version: {r['observation_id']}")
        key = sr.identifier_key(r["study_id"], r["product_id"])
        if not key:
            raise ValueError(f"Unresolved or ambiguous study identifier: {r['study_id']}")
        if r["raw_scale"] not in ("percent", "ratio"):
            raise ValueError(f"Unsupported value scale: {r['raw_scale']}")
        divisor = 100 if r["raw_scale"] == "percent" else 1
        for raw, normalized in (("raw_value", "value_ratio"), ("raw_ci_lower", "ci_lower_ratio"), ("raw_ci_upper", "ci_upper_ratio")):
            value = float(r[normalized])
            if not math.isfinite(value) or not math.isclose(float(r[raw]) / divisor, value, rel_tol=0, abs_tol=1e-10):
                raise ValueError(f"Scale mismatch: {r['observation_id']} {raw}")
        if not 0 < r["ci_level"] < 1 or not r["ci_lower_ratio"] <= r["value_ratio"] <= r["ci_upper_ratio"]:
            raise ValueError(f"Invalid interval: {r['observation_id']}")
        checked.append({**r, "study_key": key})
    return checked


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check-only", action="store_true", help="Validate without changing SQLite")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    rows = [json.loads(line) for line in (ROOT / "data/reviewed/observations.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    db_path = ROOT / "data/collection.sqlite"
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
        rows = validate(db, rows)
    if not args.check_only:
        with sqlite3.connect(db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS reviewed_observations(observation_id TEXT PRIMARY KEY, product_id TEXT, study_id TEXT, endpoint TEXT, comparator TEXT, value_ratio REAL, ci_level REAL, ci_lower_ratio REAL, ci_upper_ratio REAL, document_id TEXT, version_id TEXT, pdf_page INTEGER, review_status TEXT, metadata_json TEXT)")
            for r in rows:
                ref = r["evidence"]
                db.execute("INSERT OR REPLACE INTO reviewed_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (r["observation_id"], r["product_id"], r["study_id"], r["endpoint"], r["comparator"],
                            r["value_ratio"], r["ci_level"], r["ci_lower_ratio"], r["ci_upper_ratio"],
                            ref["document_id"], ref["version_id"], ref["pdf_page"], r["review_status"], json.dumps(r, ensure_ascii=False)))
    print(json.dumps({"observations_validated": len(rows), "canonical_study_keys": len(rows),
                      "database_written": not args.check_only, "domain_expert_review_pending": True}, indent=2))


if __name__ == "__main__":
    main()
