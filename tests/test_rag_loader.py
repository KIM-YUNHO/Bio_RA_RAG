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
