"""Load RAG releases (chunks.jsonl + emb/bge-m3) into PostgreSQL + pgvector, and check the copy against the files.

A release folder is the unit: line i of chunks.jsonl = chunk_ids.json[i] = row i of doc.npy. It is written in one transaction
with its rag.releases row, so there is never a half-loaded release. A loaded release is never changed; loading the same
release again is a no-op when chunks_sha256 matches and an error otherwise (use --replace to drop and reload it).

Usage (from the project root, DB up and migrated):
  uv run python -m storage.rag_loader load                 # data/versions/versions.json: roles pilot and latest
  uv run python -m storage.rag_loader load --version 02_pipeline_h_2026-10-14
  uv run python -m storage.rag_loader verify               # files vs DB, and numpy vs pgvector exact search
  uv run python -m storage.rag_loader list
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
from psycopg.types.json import Jsonb

from storage.postgres import connect, migrate

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "data/versions/versions.json"
MODEL_KEY = "bge-m3"
TABLE = "rag.embeddings_bge_m3"
PROMOTED = ["document_id", "version_id", "product_id", "source", "role", "document_type", "doc_group", "jurisdiction",
            "locator_type", "pdf_page", "pdf_page_label", "section", "header", "text", "n_tokens", "n_tokens_with_header",
            "content_hash", "page_hash"]
COLUMNS = ["release_id", "ordinal", "chunk_id", *PROMOTED, "metadata"]


def release_id(version_id):
    """`01_pilot_baseline_2026-10-10` -> `01_pilot_baseline_2026_10_10` (a safe identifier; it names the vector partition)."""
    return re.sub(r"[^a-z0-9]+", "_", version_id.lower()).strip("_")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def versions():
    doc = json.loads(VERSIONS.read_text(encoding="utf-8"))
    return doc, {v["id"]: v for v in doc["versions"]}


def release_files(entry):
    """(release folder, candidate folder) of a versions.json entry."""
    folder = ROOT / entry["embedded_path"]
    return folder, folder / "candidate"


def read_chunks(candidate):
    text = (candidate / "chunks.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def chunk_row(rel, ordinal, c):
    return (rel, ordinal, c["chunk_id"], *[c.get(k) for k in PROMOTED], Jsonb({k: v for k, v in c.items() if k not in PROMOTED and k != "chunk_id"}))


def rebuild(row, keys=None):
    """Inverse of chunk_row: the chunks.jsonl row (as a dict) from a rag.chunks row. `keys` is rag.releases.chunk_keys:
    the key order of the file row (jsonb keeps none)."""
    d = dict(zip(COLUMNS, row))
    out = {k: d[k] for k in PROMOTED}
    out.update(d["metadata"])
    out["chunk_id"] = d["chunk_id"]
    return {k: out[k] for k in keys} if keys else out


def key_order(chunks):
    """Key order shared by every chunk row, or None when rows differ."""
    first = list(chunks[0])
    return first if all(list(c) == first for c in chunks) else None


def vector_text(v):
    return "[" + ",".join(map(repr, v.tolist())) + "]"


def load_release(conn, entry, replace=False):
    rel = release_id(entry["id"])
    folder, candidate = release_files(entry)
    emb = candidate / "emb" / MODEL_KEY
    meta = json.loads((emb / "meta.json").read_text(encoding="utf-8"))
    ids = json.loads((emb / "chunk_ids.json").read_text(encoding="utf-8"))
    vec = np.load(emb / "doc.npy")
    chunks_sha = sha256(candidate / "chunks.jsonl")
    chunks = read_chunks(candidate)

    # Contract checks before anything is written.
    if chunks_sha != meta["chunks_sha256"] or chunks_sha != entry["chunks_sha256"]:
        raise ValueError(f"{rel}: chunks.jsonl hash differs from meta.json / versions.json")
    if ids != [c["chunk_id"] for c in chunks] or len(set(ids)) != len(ids):
        raise ValueError(f"{rel}: chunk_ids.json does not match chunks.jsonl order (or has duplicates)")
    if meta["model"] != "BAAI/bge-m3" or meta["similarity"] != "cosine" or meta.get("truncated_inputs") != 0:
        raise ValueError(f"{rel}: unexpected embedding contract")
    if vec.shape != (len(chunks), meta["dim"]) or vec.dtype != np.float32 or not np.isfinite(vec).all():
        raise ValueError(f"{rel}: doc.npy shape/dtype/values invalid: {vec.shape} {vec.dtype}")
    if not np.allclose(np.linalg.norm(vec, axis=1), 1.0, atol=0.002):
        raise ValueError(f"{rel}: vectors are not L2-normalized")
    if any("\\u0000" in json.dumps(c) for c in chunks):
        raise ValueError(f"{rel}: U+0000 in a chunk (PostgreSQL text/jsonb cannot hold it)")

    exists = conn.execute("SELECT chunks_sha256, chunk_keys FROM rag.releases WHERE release_id = %s", (rel,)).fetchone()
    if exists and not replace:
        if exists[0] == chunks_sha:
            if exists[1] is None and key_order(chunks) is not None:     # loaded before 006_rag_chunk_keys.sql
                conn.execute("UPDATE rag.releases SET chunk_keys = %s WHERE release_id = %s", (Jsonb(key_order(chunks)), rel))
                return rel, "already loaded (chunk key order recorded)"
            return rel, "already loaded"
        raise RuntimeError(f"{rel} is loaded with different chunks (sha {exists[0][:12]}); use --replace")

    manifest_path = folder / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    documents = {}
    for c in chunks:
        documents[(c["document_id"], c["version_id"])] = documents.get((c["document_id"], c["version_id"]), 0) + 1

    with conn.transaction():
        if exists:
            conn.execute(f"DROP TABLE IF EXISTS rag.emb_bge_m3__{rel}")
            conn.execute("DELETE FROM rag.active_release WHERE release_id = %s", (rel,))
            conn.execute("DELETE FROM rag.releases WHERE release_id = %s", (rel,))
        conn.execute("INSERT INTO rag.releases (release_id, kind, title, source_dir, n_chunks, n_documents, chunks_sha256, embedding_model,"
                     " embedding_dim, similarity, embedding_meta, manifest, version_record, chunk_keys)"
                     " VALUES (%s,'corpus',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (rel, entry["release"], entry["embedded_path"], len(chunks), len(documents), chunks_sha, meta["model"], meta["dim"],
                      meta["similarity"], Jsonb(meta), Jsonb(manifest), Jsonb(entry), Jsonb(key_order(chunks))))
        with conn.cursor().copy(f"COPY rag.chunks ({', '.join(COLUMNS)}) FROM STDIN") as copy:
            for i, c in enumerate(chunks):
                copy.write_row(chunk_row(rel, i, c))
        for (doc, version), n in sorted(documents.items()):
            conn.execute("INSERT INTO rag.release_documents VALUES (%s, %s, %s, %s)", (rel, doc, version, n))
        conn.execute(f"CREATE TABLE rag.emb_bge_m3__{rel} PARTITION OF {TABLE} FOR VALUES IN ('{rel}')")
        with conn.cursor().copy(f"COPY {TABLE} (release_id, chunk_id, embedding) FROM STDIN") as copy:
            for chunk_id, v in zip(ids, vec):
                copy.write_row((rel, chunk_id, vector_text(v)))
    conn.execute(f"ANALYZE {TABLE}")
    conn.execute("ANALYZE rag.chunks")
    return rel, f"loaded {len(chunks)} chunks, {len(documents)} documents, {vec.shape[1]}-d vectors"


def set_roles(conn, doc, wanted):
    """rag.active_release rows from versions.json roles whose release is loaded."""
    for role, version_id in doc["roles"].items():
        rel = release_id(version_id)
        if rel in wanted:
            conn.execute("INSERT INTO rag.active_release (role, release_id, note) VALUES (%s, %s, 'from data/versions/versions.json') "
                         "ON CONFLICT (role) DO UPDATE SET release_id = EXCLUDED.release_id, set_at = now()", (role, rel))


def verify_release(conn, entry, queries=300, top=10, seed=7):
    rel = release_id(entry["id"])
    _, candidate = release_files(entry)
    emb = candidate / "emb" / MODEL_KEY
    problems = []
    chunks = read_chunks(candidate)
    vec = np.load(emb / "doc.npy")
    ids = json.loads((emb / "chunk_ids.json").read_text(encoding="utf-8"))

    head = conn.execute("SELECT n_chunks, chunks_sha256, chunk_keys FROM rag.releases WHERE release_id = %s", (rel,)).fetchone()
    if not head:
        return [f"{rel}: not loaded"], {}
    keys = head[2]
    if keys != key_order(chunks):
        problems.append(f"{rel}: stored chunk key order differs from the file")
    if head[0] != len(chunks) or head[1] != sha256(candidate / "chunks.jsonl"):
        problems.append(f"{rel}: release row (n_chunks/sha) differs from the files")

    # 0. every document version of the release is in the corpus mirror (a soft reference; see 005_rag.sql).
    missing = conn.execute("SELECT count(*) FROM rag.release_documents d WHERE d.release_id = %s AND NOT EXISTS "
                           "(SELECT 1 FROM corpus.document_versions v WHERE v.document_id = d.document_id AND v.version_id = d.version_id)",
                           (rel,)).fetchone()[0]
    if missing:
        problems.append(f"{rel}: {missing} document versions are not in corpus.document_versions (run ingestion.load_catalog)")

    # 1. every chunk row rebuilds to the file row, in file order.
    rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM rag.chunks WHERE release_id = %s ORDER BY ordinal", (rel,)).fetchall()
    if len(rows) != len(chunks):
        problems.append(f"{rel}: {len(rows)} chunk rows, {len(chunks)} in file")
    # Compared as canonical JSON, so 1 vs 1.0 and key order count as differences (dict equality would not see them).
    def canon(c):
        return json.dumps(c, ensure_ascii=False)
    bad = [i for i, (row, c) in enumerate(zip(rows, chunks)) if canon(rebuild(row, keys)) != canon(c) or row[1] != i]
    if bad:
        problems.append(f"{rel}: {len(bad)} chunk rows differ from chunks.jsonl (first ordinals {bad[:5]})")

    # 2. every vector equals doc.npy row, bit for bit.
    stored = conn.execute(f"SELECT c.ordinal, e.embedding::text FROM {TABLE} e JOIN rag.chunks c USING (release_id, chunk_id) "
                          "WHERE e.release_id = %s ORDER BY c.ordinal", (rel,)).fetchall()
    if [o for o, _ in stored] != list(range(len(ids))):
        problems.append(f"{rel}: vector rows do not cover ordinals 0..{len(ids) - 1}")
    else:
        back = np.array([json.loads(t) for _, t in stored], dtype=np.float32)
        if not np.array_equal(back, vec):
            problems.append(f"{rel}: {int((back != vec).any(axis=1).sum())} vectors differ from doc.npy")

    # 3. exact search: numpy inner product (what rag/retrieval/search.py ranks by) vs pgvector `<#>`, queries = stored vectors of random chunks.
    rng = np.random.default_rng(seed)
    picks = rng.choice(len(ids), size=min(queries, len(ids)), replace=False)
    conn.execute("SET enable_indexscan = off")
    conn.execute("SET enable_bitmapscan = off")
    same_set = same_order = 0
    for p in picks:
        want = np.argsort(-(vec @ vec[p]), kind="stable")[:top]
        got = [r[0] for r in conn.execute(
            f"SELECT c.ordinal FROM {TABLE} e JOIN rag.chunks c USING (release_id, chunk_id) WHERE e.release_id = %s "
            f"ORDER BY e.embedding <#> %s::vector, c.ordinal LIMIT %s", (rel, vector_text(vec[p]), top))]
        same_set += set(got) == set(int(i) for i in want)
        same_order += got == [int(i) for i in want]
    conn.execute("RESET enable_indexscan")
    conn.execute("RESET enable_bitmapscan")
    if same_set != len(picks):
        problems.append(f"{rel}: exact pgvector top-{top} differs from numpy for {len(picks) - same_set}/{len(picks)} queries")

    # 4. the approximate (HNSW) index: recall against the exact answer.
    hit = 0
    conn.execute("SET hnsw.ef_search = 100")
    for p in picks:
        want = set(int(i) for i in np.argsort(-(vec @ vec[p]), kind="stable")[:top])
        got = {r[0] for r in conn.execute(
            f"SELECT c.ordinal FROM {TABLE} e JOIN rag.chunks c USING (release_id, chunk_id) WHERE e.release_id = %s "
            f"ORDER BY e.embedding <#> %s::vector LIMIT %s", (rel, vector_text(vec[p]), top))}
        hit += len(got & want)
    conn.execute("RESET hnsw.ef_search")
    stats = {"chunks": len(rows), "queries": len(picks), "exact_same_set": same_set, "exact_same_order": same_order,
             f"hnsw_recall@{top}(ef=100)": round(hit / (len(picks) * top), 4)}
    return problems, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("load", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--version", action="append", help="versions.json id (repeatable). Default: roles pilot and latest")
        if name == "load":
            p.add_argument("--replace", action="store_true")
        else:
            p.add_argument("--queries", type=int, default=300)
    sub.add_parser("list")
    args = ap.parse_args(argv)

    doc, by_id = versions()
    with connect() as conn:
        if args.cmd == "list":
            for r in conn.execute("SELECT release_id, n_chunks, n_documents, embedding_model, embedding_dim, loaded_at FROM rag.releases ORDER BY release_id"):
                print(r)
            print("active:", conn.execute("SELECT role, release_id FROM rag.active_release ORDER BY role").fetchall())
            return 0
        wanted = args.version or [doc["roles"]["pilot"], doc["roles"]["latest"]]
        unknown = [v for v in wanted if v not in by_id]
        if unknown:
            sys.exit(f"unknown version(s) {unknown}; versions.json has {list(by_id)}")
        if args.cmd == "load":
            print("migrations applied:", migrate(conn) or "nothing new")
            loaded = set()
            for v in wanted:
                rel, msg = load_release(conn, by_id[v], replace=args.replace)
                loaded.add(rel)
                print(f"{rel}: {msg}")
            set_roles(conn, doc, {r[0] for r in conn.execute("SELECT release_id FROM rag.releases")})
            print("active:", conn.execute("SELECT role, release_id FROM rag.active_release ORDER BY role").fetchall())
            return 0
        failed = False
        for v in wanted:
            problems, stats = verify_release(conn, by_id[v], queries=args.queries)
            print(f"{release_id(v)}: {'OK' if not problems else 'FAILED'} {stats}")
            for p in problems:
                print("  -", p)
            failed |= bool(problems)
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
