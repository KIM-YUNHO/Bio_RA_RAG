"""Single import point for the PostgreSQL + pgvector connection.

New database code (RAG chunk/vector loading, conversation sessions) imports from here instead of ingestion.db, so the
connector can later move out of ingestion/ without touching its callers. Today this only re-exports ingestion/db.py,
which is unchanged from the original project. Settings come from the root .env (BIOSIM_DATABASE_URL).
"""
from ingestion.db import MIGRATIONS, connect, migrate, url

__all__ = ["MIGRATIONS", "connect", "migrate", "url"]
