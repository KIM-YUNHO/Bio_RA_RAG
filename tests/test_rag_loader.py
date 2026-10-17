"""storage.rag_loader against a throwaway database (skipped when the local compose server is not running).

The release is a small synthetic one that points at a real catalog document, so the foreign keys are exercised.
"""
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

import numpy as np
import psycopg

from ingestion import db, load_catalog
from storage import rag_loader


def server_url(database):
    return db.url().rsplit("/", 1)[0] + "/" + database


def reachable():
    try:
        with psycopg.connect(server_url("postgres"), connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


def make_release(folder, document, n=6, seed=1):
    """chunks.jsonl + emb/bge-m3 for n chunks of one catalog document; vectors are slightly off unit norm, like fp16 ones."""
    candidate = Path(folder) / "candidate"
    emb = candidate / "emb" / "bge-m3"
    emb.mkdir(parents=True)
    chunks = [{"document_id": document[0], "version_id": document[1], "product_id": "SB17", "source": "EMA", "role": "x",
               "document_type": "t", "doc_group": "g", "jurisdiction": "EU", "locator_type": "pdf_page", "pdf_page": 1,
               "pdf_page_label": "1", "section": None, "chunk_id": f"{document[0]}:{i:04d}", "header": f"h{i}", "text": f"text {i} \u00e9\u4e2d",
               "n_tokens": 3, "n_tokens_with_header": 5, "content_hash": f"c{i}", "page_hash": "p",
               "document_metadata": {"default_search": True}, "provenance": {"parsed_path": "C:\\old\\x.jsonl"}, "quality": {"warnings": []}}
              for i in range(n)]
    body = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks)
    (candidate / "chunks.jsonl").write_bytes(body.encode("utf-8"))
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    v = np.random.default_rng(seed).normal(size=(n, 1024)).astype(np.float32)
    v = (v / np.linalg.norm(v, axis=1, keepdims=True) * np.float32(1.0003)).astype(np.float32)
    np.save(emb / "doc.npy", v)
    (emb / "chunk_ids.json").write_text(json.dumps([c["chunk_id"] for c in chunks]), encoding="utf-8")
    (emb / "meta.json").write_text(json.dumps({"model": "BAAI/bge-m3", "dim": 1024, "similarity": "cosine", "truncated_inputs": 0,
                                                "n_chunks": n, "chunks_sha256": sha}), encoding="utf-8")
    return {"id": "99_test_release", "release": "test", "embedded_path": str(Path(folder)), "chunks_sha256": sha}


@unittest.skipUnless(reachable(), "local PostgreSQL (ingestion/db/compose.yaml) is not running")
class RagLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = "biosim_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(server_url("postgres"), autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{cls.name}"')
        cls.previous = os.environ.get("BIOSIM_DATABASE_URL")
        os.environ["BIOSIM_DATABASE_URL"] = server_url(cls.name)
        cls.conn = db.connect()
        db.migrate(cls.conn)
        load_catalog.load(cls.conn)
        cls.document = cls.conn.execute("SELECT document_id, version_id FROM corpus.document_versions ORDER BY 1, 2 LIMIT 1").fetchone()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()
        if cls.previous is None:
            os.environ.pop("BIOSIM_DATABASE_URL", None)
        else:
            os.environ["BIOSIM_DATABASE_URL"] = cls.previous
        with psycopg.connect(server_url("postgres"), autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{cls.name}" WITH (FORCE)')

    def setUp(self):
        self.folder = Path(self.tmp.name) / uuid.uuid4().hex
        self.entry = make_release(self.folder, self.document)
        self.rel = rag_loader.release_id(self.entry["id"])
        self.conn.execute("DROP TABLE IF EXISTS rag.emb_bge_m3__" + self.rel)
        self.conn.execute("DELETE FROM rag.active_release WHERE release_id = %s", (self.rel,))
        self.conn.execute("DELETE FROM rag.releases WHERE release_id = %s", (self.rel,))

    def test_release_id_is_a_safe_identifier(self):
        self.assertEqual(rag_loader.release_id("03_pipeline_h_v2_table_recovery_2026-10-14"), "03_pipeline_h_v2_table_recovery_2026_10_14")

    def test_load_verifies_and_is_idempotent(self):
        rel, message = rag_loader.load_release(self.conn, self.entry)
        self.assertIn("loaded 6 chunks", message)
        problems, stats = rag_loader.verify_release(self.conn, self.entry, queries=6)
        self.assertEqual(problems, [])
        self.assertEqual(stats["exact_same_order"], 6)
        self.assertEqual(rag_loader.load_release(self.conn, self.entry)[1], "already loaded")

    def test_changed_chunks_need_replace(self):
        rag_loader.load_release(self.conn, self.entry)
        other = make_release(Path(self.tmp.name) / uuid.uuid4().hex, self.document, n=5, seed=2)
        with self.assertRaises(RuntimeError):
            rag_loader.load_release(self.conn, other)
        self.assertIn("loaded 5 chunks", rag_loader.load_release(self.conn, other, replace=True)[1])

    def test_bad_chunk_order_loads_nothing(self):
        ids_path = self.folder / "candidate/emb/bge-m3/chunk_ids.json"
        ids = json.loads(ids_path.read_text(encoding="utf-8"))
        ids_path.write_text(json.dumps(ids[::-1]), encoding="utf-8")
        with self.assertRaises(ValueError):
            rag_loader.load_release(self.conn, self.entry)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM rag.releases WHERE release_id = %s", (self.rel,)).fetchone())

    def test_database_index_equals_file_index(self):
        """rag.search.load_index with BIOSIM_SEARCH_BACKEND=postgres returns what the files give: rows, key order, vectors."""
        from unittest import mock
        from rag import search
        rag_loader.load_release(self.conn, self.entry)
        candidate = self.folder / "candidate"
        from_files = search.load_index("bge-m3", candidate)
        with mock.patch.dict(os.environ, {"BIOSIM_SEARCH_BACKEND": "postgres"}):
            from_database = search.load_index("bge-m3", candidate)
        chunks_f, vectors_f, meta_f = from_files
        chunks_d, vectors_d, meta_d = from_database
        self.assertEqual([json.dumps(c, ensure_ascii=False) for c in chunks_d], [json.dumps(c, ensure_ascii=False) for c in chunks_f])
        self.assertEqual(vectors_d.dtype, vectors_f.dtype)
        self.assertTrue(np.array_equal(vectors_d, vectors_f))
        self.assertEqual(meta_d["chunks_sha256"], meta_f["chunks_sha256"])
        self.assertEqual(search.dense_hits(vectors_f[2], vectors_d, np.arange(len(chunks_d)), 4),
                         search.dense_hits(vectors_f[2], vectors_f, np.arange(len(chunks_f)), 4))

    def test_unregistered_folder_reads_files(self):
        from unittest import mock
        from rag import search
        other = make_release(Path(self.tmp.name) / uuid.uuid4().hex, self.document, n=4, seed=3)
        with mock.patch.dict(os.environ, {"BIOSIM_SEARCH_BACKEND": "postgres"}):
            chunks, vectors, _ = search.load_index("bge-m3", Path(other["embedded_path"]) / "candidate")
        self.assertEqual((len(chunks), vectors.shape), (4, (4, 1024)))

    def test_pgvector_store_ranks_as_the_matrix(self):
        """BIOSIM_SEARCH_BACKEND=pgvector: a caller asking for the store searches in the database and gets the matrix's ranking;
        callers that score vectors themselves still get the matrix."""
        from unittest import mock
        from rag import search
        from storage.rag_index import PgVectors
        rag_loader.load_release(self.conn, self.entry)
        candidate = self.folder / "candidate"
        _, matrix, _ = search.load_index("bge-m3", candidate)
        with mock.patch.dict(os.environ, {"BIOSIM_SEARCH_BACKEND": "pgvector"}):
            chunks, store, _ = search.load_index("bge-m3", candidate, vector_store=True)
            _, plain, _ = search.load_index("bge-m3", candidate)
        self.assertIsInstance(store, PgVectors)
        self.assertTrue(np.array_equal(plain, matrix))
        self.assertEqual(store.shape, matrix.shape)
        query = (matrix[2] + np.float32(0.1) * matrix[4]).astype(np.float32)
        for rows in (np.arange(len(chunks)), np.array([5, 1, 3])):
            want, got = search.dense_hits(query, matrix, rows, 3), search.dense_hits(query, store, rows, 3)
            self.assertEqual([i for _, i in got], [i for _, i in want])
            self.assertTrue(np.allclose([s for s, _ in got], [s for s, _ in want], atol=1e-5))
        self.assertEqual(search.dense_hits(query, store, np.array([], dtype=int), 3), [])
        scores = search.similarity(store, [0, 3], query)
        self.assertEqual(set(scores), {0, 3})
        self.assertAlmostEqual(scores[3], float(matrix[3] @ query), places=5)
        self.assertAlmostEqual(search.max_similarity(store, [0, 1, 2], [matrix[0], matrix[1]])(1), float(matrix[1] @ matrix[1]), places=5)
        self.assertEqual(store.nearest(matrix[2], 1)[0][1], 2)
        store.close()
        self.assertEqual(store.top(query, [2, 4], 1)[0][1], 2)  # a closed connection is opened again
        store.close()

    def publish_project(self):
        """A throwaway project root with versions.json and the test release at data/versions/<id>/embedded/release."""
        from unittest import mock
        from storage import publish_release
        root = Path(self.tmp.name) / uuid.uuid4().hex
        versions = root / "data/versions/versions.json"
        folder = versions.parent / "99_test_release" / "embedded" / "release"
        make_release(folder, self.document)
        versions.write_text(json.dumps({"updated_kst": "", "roles": {"latest": "01_x"}, "versions": []}, indent=2) + "\n", encoding="utf-8")
        for target, value in ((rag_loader, "ROOT"), (rag_loader, "VERSIONS")):
            patcher = mock.patch.object(target, value, root if value == "ROOT" else versions)
            patcher.start()
            self.addCleanup(patcher.stop)
        return publish_release, folder, versions

    def test_publish_registers_loads_checks_and_serves(self):
        publish_release, folder, versions = self.publish_project()
        first = publish_release.publish(folder, conn=self.conn, queries=6)
        self.assertEqual((first["version"], first["registered"], first["roles"]), ("99_test_release", "added", []))
        self.assertIn("loaded 6 chunks", first["load"])
        entry = json.loads(versions.read_text(encoding="utf-8"))["versions"][0]
        self.assertEqual((entry["embedded_path"], entry["chunks"], entry["vectors"]), ("data/versions/99_test_release/embedded/release", 6, [6, 1024]))
        again = publish_release.publish(folder, conn=self.conn, serve=True, queries=6)
        self.assertEqual((again["registered"], again["load"]), ("already listed", "already loaded"))
        self.assertEqual(again["roles"], ["latest", "served_by_ra_service"])
        roles = json.loads(versions.read_text(encoding="utf-8"))["roles"]
        self.assertEqual((roles["latest"], roles["served_by_ra_service"]), ("99_test_release", "99_test_release"))

    def test_publish_refuses_before_writing(self):
        publish_release, folder, versions = self.publish_project()
        before = versions.read_text(encoding="utf-8")
        ids = folder / "candidate/emb/bge-m3/chunk_ids.json"
        ids.write_text(json.dumps(json.loads(ids.read_text(encoding="utf-8"))[::-1]), encoding="utf-8")
        with self.assertRaises(ValueError):
            publish_release.publish(folder, conn=self.conn, queries=6)
        self.assertEqual(versions.read_text(encoding="utf-8"), before)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM rag.releases WHERE release_id = %s", (self.rel,)).fetchone())
        with self.assertRaises(ValueError):  # not under data/versions/<id>/embedded/
            publish_release.version_id(Path(self.tmp.name) / "elsewhere")

    def test_catalog_reload_keeps_releases(self):
        """ingestion.load_catalog truncates and refills corpus.*; a loaded release must neither block nor lose it."""
        rag_loader.load_release(self.conn, self.entry)
        load_catalog.load(self.conn)
        problems, _ = rag_loader.verify_release(self.conn, self.entry, queries=3)
        self.assertEqual(problems, [])

    def test_loaded_rows_are_immutable(self):
        rag_loader.load_release(self.conn, self.entry)
        with self.assertRaises(psycopg.errors.RaiseException):
            self.conn.execute("UPDATE rag.chunks SET text = 'x' WHERE release_id = %s", (self.rel,))


if __name__ == "__main__":
    unittest.main()
