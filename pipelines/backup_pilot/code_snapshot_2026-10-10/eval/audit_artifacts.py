"""Read-only corpus integrity audit. Writes only the explicitly named audit JSON."""
import argparse
import collections
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))
import study_registry as sr


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/project/PROJECT_DIRECTION_AUDIT_CURRENT_2026-10-06.json")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    docs = [json.loads(x) for x in (ROOT / "data/catalog/documents.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    downloads = json.loads((ROOT / "data/catalog/download_cache.json").read_text(encoding="utf-8"))
    errors, raw = [], {}
    for record in downloads.values():
        if not record.get("raw_path"):
            continue
        path = ROOT / record["raw_path"]
        if path not in raw:
            raw[path] = (path.stat().st_size, sha(path)) if path.exists() else (None, None)
        if raw[path] != (record["bytes"], record["sha256"]):
            errors.append({"check": "raw_size_or_hash", "path": record["raw_path"]})
    active = {(d["document_id"], d["version_id"]) for d in docs}
    with sqlite3.connect((ROOT / "data/collection.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        integrity = db.execute("pragma integrity_check").fetchone()[0]
        counts = {t: db.execute(f"select count(*) from {t}").fetchone()[0] for t in
                  ("products", "studies", "document_versions", "pages", "page_search", "reviewed_observations", "ocr_regions", "ocr_search")}
        db_versions = set(db.execute("select document_id,version_id from document_versions"))
        if db_versions != active:
            errors.append({"check": "catalog_database_versions", "missing": sorted(active - db_versions), "extra": sorted(db_versions - active)})
        if integrity != "ok":
            errors.append({"check": "sqlite_integrity", "value": integrity})
        orphans = db.execute("select count(*) from pages p left join document_versions d on p.document_id=d.document_id and p.version_id=d.version_id where d.document_id is null").fetchone()[0]
        if orphans:
            errors.append({"check": "orphan_pages", "count": orphans})
        parsed_pages = 0
        for d in docs:
            rows = [json.loads(x) for x in (ROOT / d["parsed_path"]).read_text(encoding="utf-8").splitlines() if x.strip()]
            parsed_pages += len(rows)
            n = db.execute("select count(*) from pages where document_id=? and version_id=?", (d["document_id"], d["version_id"])).fetchone()[0]
            if len(rows) != n:
                errors.append({"check": "parsed_database_page_count", "document_id": d["document_id"], "parsed": len(rows), "database": n})
        observations = [json.loads(r[0]) for r in db.execute("select metadata_json from reviewed_observations")]
    chunks_path = ROOT / "data/rag/chunks.jsonl"
    chunks = [json.loads(x) for x in chunks_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = [c["chunk_id"] for c in chunks]
    chunks_sha = sha(chunks_path)
    if len(ids) != len(set(ids)):
        errors.append({"check": "duplicate_chunk_ids"})
    noncurrent = sum((c["document_id"], c["version_id"]) not in active for c in chunks)
    if noncurrent:
        errors.append({"check": "noncurrent_chunk_versions", "count": noncurrent})
    embeddings = []
    for folder in sorted((ROOT / "data/rag/emb").iterdir()):
        if not (folder / "meta.json").exists():
            continue
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        vec = np.load(folder / "doc.npy", mmap_mode="r")
        checks = {"sha_matches": meta["chunks_sha256"] == chunks_sha,
                  "ids_match": json.loads((folder / "chunk_ids.json").read_text(encoding="utf-8")) == ids,
                  "shape_matches": vec.shape == (len(chunks), meta["dim"]), "finite": bool(np.isfinite(vec).all())}
        embeddings.append({"model": folder.name, "shape": list(vec.shape), **checks, "truncated_inputs": meta.get("truncated_inputs")})
        if not all(checks.values()):
            errors.append({"check": "embedding_alignment", "model": folder.name, **checks})
    canonical = sum(r.get("study_key") == sr.identifier_key(r["study_id"], r["product_id"]) and bool(r.get("study_key")) for r in observations)
    report = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "documents": len(docs), "raw_paths_checked": len(raw),
              "parsed_pages": parsed_pages, "sqlite_integrity": integrity, "db_counts": counts, "orphan_pages": orphans,
              "chunks": len(chunks), "chunks_sha256": chunks_sha, "chunks_with_studies": sum(bool(c.get("studies")) for c in chunks),
              "study_scopes_in_active_artifacts": dict(collections.Counter(c.get("study_scope") for c in chunks)),
              "observations_with_canonical_key": canonical, "embedding_checks": embeddings, "errors": errors,
              "input_hashes": {p: sha(ROOT / p) for p in ("data/catalog/documents.jsonl", "data/catalog/download_cache.json",
                              "data/reviewed/studies.json", "data/reviewed/observations.jsonl", "data/rag/chunks.jsonl")}}
    out = (ROOT / args.out).resolve()
    if not out.is_relative_to(ROOT):
        raise ValueError("Audit output must stay within the workspace")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("input_hashes", "study_scopes_in_active_artifacts")}, ensure_ascii=False, indent=1))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
