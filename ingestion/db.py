"""Connection and schema migrations for the ingestion database (PostgreSQL + pgvector, ingestion/db/compose.yaml).

Usage:
  docker compose -f ingestion/db/compose.yaml up -d
  uv run python -m ingestion.db migrate      # apply ingestion/db/migrations/*.sql not yet applied
  uv run python -m ingestion.db status
The URL comes from BIOSIM_DATABASE_URL; the default is the local compose database.
"""
import hashlib
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent / "db" / "migrations"
DEFAULT_URL = "postgresql://biosim:biosim_local@127.0.0.1:5432/biosim"


def url():
    return os.environ.get("BIOSIM_DATABASE_URL", DEFAULT_URL)


def connect(**kwargs):
    # Autocommit, so each `with conn.transaction()` in the callers is a real transaction rather than a savepoint of an outer one.
    kwargs.setdefault("autocommit", True)
    return psycopg.connect(url(), **kwargs)


def migrate(conn):
    """Apply each migration file once, in name order, each in its own transaction. A changed applied file is an error."""
    conn.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
    conn.commit()
    applied = dict(conn.execute("SELECT name, sha256 FROM public.schema_migrations").fetchall())
    done = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if path.name in applied:
            if applied[path.name] != digest:
                raise RuntimeError(f"{path.name} changed after it was applied; add a new migration instead")
            continue
        with conn.transaction():
            conn.execute(sql)
            conn.execute("INSERT INTO public.schema_migrations (name, sha256) VALUES (%s, %s)", (path.name, digest))
        done.append(path.name)
    return done


def status(conn):
    rows = conn.execute("SELECT name, applied_at FROM public.schema_migrations ORDER BY name").fetchall()
    extensions = dict(conn.execute("SELECT extname, extversion FROM pg_extension").fetchall())
    return {"server": conn.execute("SHOW server_version").fetchone()[0], "extensions": extensions,
            "migrations": [(name, at.isoformat()) for name, at in rows]}


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    with connect() as conn:
        if command == "migrate":
            print("applied:", migrate(conn) or "nothing new")
        print(status(conn))
