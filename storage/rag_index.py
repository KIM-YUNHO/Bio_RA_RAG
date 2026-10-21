"""Read a loaded RAG release (rag.chunks + rag.embeddings_bge_m3) for `rag.retrieval.search.load_index`.

BIOSIM_SEARCH_BACKEND (rag/retrieval/search.py):
  postgres  chunks and the whole vector matrix are read from the database into memory, then searched as from files.
  pgvector  chunks are read from the database; the vectors stay there and every dense search is a pgvector query
            (PgVectors), so servers share one vector store instead of each holding a matrix. Callers that score
            vectors themselves (evaluation scripts) still get the matrix.
The release is found from the candidate folder the caller passes (`<release folder>/candidate`, as recorded in
rag.releases.source_dir). A folder that is not a loaded release returns None and the caller reads files as before.

What this does and does not replace: chunks and vectors come from the database. The release bundle's pages, assets,
contexts and page images are still files (rag/index/release.py), and BM25 is still built in memory from the chunks.
"""
import re
import struct
from pathlib import Path
from threading import Lock

import numpy as np
import psycopg

from storage.postgres import connect
from storage.rag_loader import COLUMNS, ROOT, TABLE, rebuild, vector_text

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


class PgVectors:
    """The vectors of one loaded release, searched in PostgreSQL with pgvector instead of a matrix in each server.

    Row i is rag.chunks.ordinal = i, the row numbers rag.retrieval.search uses for the matrix, so filters (row lists) carry over.
    A search inside a row list is exact: every listed row is scored by inner product (`<#>`, the same measure as
    `matrix @ query`) and ties go to the lower row, so results match the file matrix up to float rounding. The release's
    HNSW index is for unfiltered nearest-neighbour search (`nearest`), which is approximate."""

    def __init__(self, release_id, count, dim, connector=connect):
        if not SAFE_ID.match(release_id):
            raise ValueError(f"unsafe release id: {release_id}")
        self.release_id, self.shape = release_id, (count, dim)
        self._connector, self._conn, self._lock = connector, None, Lock()

    def _rows(self, sql, params):
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._conn is None or self._conn.closed:
                        self._conn = self._connector()
                    return self._conn.execute(sql, params).fetchall()
                except psycopg.OperationalError:
                    self._conn = None  # a dropped connection is opened again once
                    if attempt:
                        raise

    SCORED = ("SELECT c.ordinal, e.embedding <#> %(q)s::vector AS distance FROM rag.chunks c "
              f"JOIN {TABLE} e ON e.release_id = c.release_id AND e.chunk_id = c.chunk_id "
              "WHERE c.release_id = %(r)s AND e.release_id = %(r)s AND c.ordinal = ANY(%(rows)s)")

    def top(self, query_vec, rows, top_n):
        """[(score, row)] of the best `top_n` rows among `rows`, best first (exact)."""
        rows = [int(i) for i in rows]
        if not rows or top_n <= 0:
            return []
        # MATERIALIZED keeps the planner from answering through the HNSW index, which would be approximate here.
        found = self._rows(f"WITH scored AS MATERIALIZED ({self.SCORED}) SELECT ordinal, distance FROM scored "
                           "ORDER BY distance, ordinal LIMIT %(n)s",
                           {"q": vector_text(np.asarray(query_vec, dtype=np.float32)), "r": self.release_id, "rows": rows, "n": int(top_n)})
        return [(-float(d), int(i)) for i, d in found]

    def scores(self, rows, query_vec):
        """{row: inner product with the query} for the listed rows."""
        rows = [int(i) for i in rows]
        if not rows:
            return {}
        found = self._rows(self.SCORED, {"q": vector_text(np.asarray(query_vec, dtype=np.float32)), "r": self.release_id, "rows": rows})
        return {int(i): -float(d) for i, d in found}

    def nearest(self, query_vec, top_n, ef_search=100):
        """[(score, row)] from the HNSW index over the whole release (approximate, no filter)."""
        with self._lock:
            if self._conn is None or self._conn.closed:
                self._conn = self._connector()
            with self._conn.transaction():
                self._conn.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)}")
                found = self._conn.execute(
                    f"SELECT c.ordinal, e.embedding <#> %(q)s::vector AS distance FROM {TABLE} e JOIN rag.chunks c "
                    "ON c.release_id = e.release_id AND c.chunk_id = e.chunk_id WHERE e.release_id = %(r)s "
                    "ORDER BY e.embedding <#> %(q)s::vector LIMIT %(n)s",
                    {"q": vector_text(np.asarray(query_vec, dtype=np.float32)), "r": self.release_id, "n": int(top_n)}).fetchall()
        return [(-float(d), int(i)) for i, d in found]

    @staticmethod
    def all_finite():
        return True  # the vector type refuses NaN and infinity

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def load_from_database(rag_dir, conn=None, store=False):
    """(chunks, vectors, meta) of the loaded release whose candidate folder is `rag_dir`; None when there is none.
    vectors is the float32 matrix, or with store=True a PgVectors that searches the release in the database."""
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
        if store:
            stored = conn.execute(f"SELECT count(*) FROM {TABLE} WHERE release_id = %s", (release_id,)).fetchone()[0]
            if stored != n_chunks:
                raise ValueError(f"{release_id}: {stored} vectors for {n_chunks} chunks")
            return chunks, PgVectors(release_id, n_chunks, dim), meta
        return chunks, read_vectors(conn, release_id, n_chunks, dim), meta
    finally:
        if own:
            conn.close()
