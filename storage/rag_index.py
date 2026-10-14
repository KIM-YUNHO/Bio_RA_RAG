"""Read a loaded RAG release (rag.chunks + rag.embeddings_bge_m3) as the (chunks, vectors, meta) that `rag.search.load_index`
returns from files, so the search and answer code does not change.

Switched on with BIOSIM_SEARCH_BACKEND=postgres (rag/search.py). The release is found from the candidate folder the caller
passes (`<release folder>/candidate`, as recorded in rag.releases.source_dir). A folder that is not a loaded release returns
None and the caller reads files as before.

What this does and does not replace: chunks and vectors come from the database. The release bundle's pages, assets,
contexts and page images are still files (rag/release.py), and BM25 is still built in memory from the chunks.
"""
import re
import struct
from pathlib import Path

import numpy as np

from storage.postgres import connect
from storage.rag_loader import COLUMNS, ROOT, TABLE, rebuild

SAFE_ID = re.compile(r"^[a-z0-9_]+$")
SIGNATURE = b"PGCOPY\n\xff\r\n\x00"


def release_for(conn, rag_dir):
    """release_id of the loaded release whose candidate folder is `rag_dir`, else None."""
    target = Path(rag_dir).resolve()
    for release_id, source_dir in conn.execute("SELECT release_id, source_dir FROM rag.releases ORDER BY release_id"):
        if (ROOT / source_dir / "candidate").resolve() == target:
            return release_id
    return None


def read_vectors(conn, release_id, count, dim):
    """The release's vectors as float32 [count, dim] in chunk order, exactly as stored (binary COPY of the vector type)."""
    if not SAFE_ID.match(release_id):
        raise ValueError(f"unsafe release id: {release_id}")
    sql = (f"COPY (SELECT e.embedding FROM {TABLE} e JOIN rag.chunks c USING (release_id, chunk_id) "
           f"WHERE e.release_id = '{release_id}' ORDER BY c.ordinal) TO STDOUT (FORMAT BINARY)")
    parts = []
    with conn.cursor().copy(sql) as copy:
        for block in copy:
            parts.append(bytes(block))
    data = b"".join(parts)
    if not data.startswith(SIGNATURE):
        raise ValueError("unexpected COPY header")
    flags, extension = struct.unpack_from(">ii", data, len(SIGNATURE))
    start = len(SIGNATURE) + 8 + extension
    record = np.dtype([("fields", ">i2"), ("size", ">i4"), ("dim", ">i2"), ("unused", ">i2"), ("v", ">f4", (dim,))])
    body = data[start:-2]
    if data[-2:] != b"\xff\xff" or len(body) != count * record.itemsize:
        raise ValueError(f"unexpected vector payload: {len(body)} bytes for {count} x {dim}")
    rows = np.frombuffer(body, dtype=record)
    if not ((rows["fields"] == 1).all() and (rows["size"] == 4 + 4 * dim).all() and (rows["dim"] == dim).all()):
        raise ValueError("unexpected vector record layout")
    return rows["v"].astype("<f4")


def load_from_database(rag_dir, conn=None):
    """(chunks, vectors, meta) of the loaded release whose candidate folder is `rag_dir`; None when there is none."""
    own = conn is None
    conn = conn or connect()
    try:
        release_id = release_for(conn, rag_dir)
        if release_id is None:
            return None
        n_chunks, dim, meta, keys = conn.execute(
            "SELECT n_chunks, embedding_dim, embedding_meta, chunk_keys FROM rag.releases WHERE release_id = %s", (release_id,)).fetchone()
        rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM rag.chunks WHERE release_id = %s ORDER BY ordinal", (release_id,)).fetchall()
        if len(rows) != n_chunks or [r[1] for r in rows] != list(range(n_chunks)):
            raise ValueError(f"{release_id}: chunk rows are incomplete")
        chunks = [rebuild(r, keys) for r in rows]
        return chunks, read_vectors(conn, release_id, n_chunks, dim), meta
    finally:
        if own:
            conn.close()
