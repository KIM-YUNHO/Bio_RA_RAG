"""Ingestion backend: contracts shared by the request agent, the task runner and the database (docs/collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md)."""
# The project has one .env (root). ingestion/db.py reads BIOSIM_DATABASE_URL from the environment only, so apply .env here
# with the same rule as the RAG code: values already set in the environment win and empty values are skipped.
from rag.llm.llm import load_env as _load_env

_load_env()
