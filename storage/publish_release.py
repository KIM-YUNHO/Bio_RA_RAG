"""Publish an embedded release: register it in data/versions/versions.json, load it into PostgreSQL + pgvector, check the copy.

The service searches releases in the database (BIOSIM_SEARCH_BACKEND=pgvector), so a release built by the preprocessing
tools (pipelines/pipeline_h/merge_release.py, rechunk_release.py) is published here right after its embeddings are written;
those tools call this unless given --no-publish. Nothing is served until --serve is given.

Usage (from the project root, DB up):
  uv run python -m storage.publish_release data/versions/05_example/embedded/release
  uv run python -m storage.publish_release data/versions/05_example/embedded/release --serve   # roles latest + served_by_ra_service

Steps, each refusing before it writes:
  1. the folder is a complete release (chunks.jsonl, emb/bge-m3/doc.npy, chunk_ids.json, meta.json agreeing with each other);
  2. versions.json gets an entry for it (or already has one with the same path and chunks hash);
  3. storage.rag_loader loads it in one transaction (a release already loaded with the same hash is left as it is);
  4. storage.rag_loader.verify_release compares the database copy with the files and numpy search with pgvector search;
  5. with --serve, the roles move to it in versions.json and rag.active_release. Running services keep their release
     until they are restarted (scripts/start_ra_service.ps1 reads served_by_ra_service).
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

from storage import rag_loader
from storage.postgres import connect, migrate

SERVE_ROLES = ("latest", "served_by_ra_service")


def version_id(folder):
    """data/versions/<id>/embedded/release -> <id>."""
    folder = Path(folder).resolve()
    if folder.parent.name != "embedded" or folder.parent.parent.parent != rag_loader.VERSIONS.parent.resolve():
        raise ValueError(f"{folder} is not data/versions/<id>/embedded/<release>; pass --id")
    return folder.parent.parent.name


def check_files(folder):
    """(chunks_sha256, n_chunks, n_documents, dim) of a complete release folder."""
    candidate = Path(folder) / "candidate"
    emb = candidate / "emb" / rag_loader.MODEL_KEY
    missing = [str(p) for p in (candidate / "chunks.jsonl", emb / "doc.npy", emb / "chunk_ids.json", emb / "meta.json") if not p.exists()]
    if missing:
        raise ValueError(f"not a complete release, missing: {missing}")
    sha = rag_loader.sha256(candidate / "chunks.jsonl")
    meta = json.loads((emb / "meta.json").read_text(encoding="utf-8"))
    chunks = rag_loader.read_chunks(candidate)
    ids = json.loads((emb / "chunk_ids.json").read_text(encoding="utf-8"))
    shape = np.load(emb / "doc.npy", mmap_mode="r").shape
    if meta.get("chunks_sha256") != sha or ids != [c["chunk_id"] for c in chunks] or shape != (len(chunks), meta.get("dim")):
        raise ValueError("chunks.jsonl, chunk_ids.json, doc.npy and meta.json do not describe the same release; embed it again")
    return sha, len(chunks), len({(c["document_id"], c["version_id"]) for c in chunks}), shape[1]


def register(doc, vid, folder, sha, n_chunks, n_documents, dim, note):
    """The versions.json entry for the release, added when missing; an existing entry must name the same files."""
    path = Path(folder).resolve().relative_to(rag_loader.ROOT).as_posix()
    entry = next((v for v in doc["versions"] if v["id"] == vid), None)
    if entry is not None:
        if entry.get("embedded_path") != path or entry.get("chunks_sha256") != sha:
            raise ValueError(f"versions.json already has {vid} for other files ({entry.get('embedded_path')}, {entry.get('chunks_sha256')})")
        return entry, False
    entry = {"id": vid, "release": note or Path(folder).name, "documents": n_documents, "chunks": n_chunks, "vectors": [n_chunks, dim],
             "chunks_sha256": sha, "embedded_path": path,
             "status": f"published to PostgreSQL + pgvector on {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} KST (storage.publish_release)"}
    doc["versions"].append(entry)
    return entry, True


def write_versions(doc):
    doc["updated_kst"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    rag_loader.VERSIONS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publish(folder, vid=None, note=None, serve=False, queries=100, conn=None):
    """Steps 1-5 of the module docstring; returns a summary dict. Raises when a check fails."""
    vid = vid or version_id(folder)
    sha, n_chunks, n_documents, dim = check_files(folder)
    doc, _ = rag_loader.versions()
    entry, added = register(doc, vid, folder, sha, n_chunks, n_documents, dim, note)
    own = conn is None
    conn = conn or connect()
    try:
        migrate(conn)
        rel, loaded = rag_loader.load_release(conn, entry)
        problems, stats = rag_loader.verify_release(conn, entry, queries=min(queries, n_chunks))
        if problems:
            raise RuntimeError(f"{rel}: database copy failed its check: {problems}")
        if added:
            write_versions(doc)  # only once the release is loaded and checked
        if serve:
            doc, _ = rag_loader.versions()
            doc["roles"].update({role: vid for role in SERVE_ROLES})
            write_versions(doc)
            rag_loader.set_roles(conn, doc, {rel})
        roles = conn.execute("SELECT role FROM rag.active_release WHERE release_id = %s ORDER BY role", (rel,)).fetchall()
        return {"version": vid, "release_id": rel, "registered": "added" if added else "already listed", "load": loaded,
                "chunks": n_chunks, "documents": n_documents, "check": stats, "roles": [r[0] for r in roles]}
    finally:
        if own:
            conn.close()


def after_build(folder, skip=False):
    """For the release builders, once a release folder is written: publish it, or say why not. Returns an exit status."""
    if skip:
        print(f"not published (--no-publish); later: python -m storage.publish_release {folder}")
        return 0
    try:
        summary = publish(folder)
    except Exception as exc:  # the release folder stays; only the database step is repeated
        print(f"release written to {folder} but not published: {type(exc).__name__}: {exc}\n"
              f"fix the cause, then run: python -m storage.publish_release {folder}", file=sys.stderr)
        return 1
    print("published:", json.dumps(summary, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="release folder: data/versions/<id>/embedded/<release>")
    ap.add_argument("--id", help="versions.json id (default: <id> of the folder path)")
    ap.add_argument("--note", help="short description for the versions.json entry")
    ap.add_argument("--serve", action="store_true", help="point the roles latest and served_by_ra_service at it")
    ap.add_argument("--queries", type=int, default=100, help="search queries in the numpy vs pgvector check")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(publish(a.folder, a.id, a.note, a.serve, a.queries), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
