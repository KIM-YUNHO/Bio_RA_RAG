"""Runs against a throwaway database on the local compose server; skipped when it is not running."""
import os
import unittest
import uuid

import psycopg

from ingestion import db, db_verify, load_catalog


def server_url(database):
    return db.url().rsplit("/", 1)[0] + "/" + database


def reachable():
    try:
        with psycopg.connect(server_url("postgres"), connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@unittest.skipUnless(reachable(), "local PostgreSQL (ingestion/db/compose.yaml) is not running")
class CatalogMirror(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = "biosim_test_" + uuid.uuid4().hex[:8]
        with psycopg.connect(server_url("postgres"), autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{cls.name}"')
        cls.previous = os.environ.get("BIOSIM_DATABASE_URL")
        os.environ["BIOSIM_DATABASE_URL"] = server_url(cls.name)
        cls.conn = db.connect()
        db.migrate(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        if cls.previous is None:
            os.environ.pop("BIOSIM_DATABASE_URL", None)
        else:
            os.environ["BIOSIM_DATABASE_URL"] = cls.previous
        with psycopg.connect(server_url("postgres"), autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{cls.name}" WITH (FORCE)')

    def test_load_twice_matches_catalog(self):
        first = load_catalog.load(self.conn)
        second = load_catalog.load(self.conn)
        self.assertEqual({k: v for k, v in first.items() if k != "runs"}, {k: v for k, v in second.items() if k != "runs"})
        problems, facts = db_verify.check(self.conn)
        self.assertEqual(problems, [])
        self.assertEqual(facts["pages"], first["pages"])

    def test_migrations_apply_once(self):
        self.assertEqual(db.migrate(self.conn), [])

    def test_approvals_are_append_only(self):
        approval = uuid.uuid4()
        with self.conn.transaction():
            self.conn.execute("INSERT INTO ingest.approvals (approval_id, product_id, approved_by, report_sha256) VALUES (%s, 'X', 'test', %s)",
                              (approval, "0" * 64))
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "append only"):
            with self.conn.transaction():
                self.conn.execute("DELETE FROM ingest.approvals WHERE approval_id = %s", (approval,))

    def test_guideline_document_has_no_product(self):
        load_catalog.load(self.conn)
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.transaction():
                self.conn.execute("UPDATE corpus.documents SET product_id = 'SB17' WHERE role = 'guideline'")


if __name__ == "__main__":
    unittest.main()
