-- rag.chunks.metadata keeps the chunk's own text: `json` instead of `jsonb`. jsonb sorts object keys and normalizes
-- numbers, so a row read back was equal to the file row but not the same dict (key order at every level). The search and
-- answer code takes chunks as files give them, so the database copy must too. Rows must be loaded again after this
-- migration (storage/rag_loader.py load --replace); `verify` compares canonical JSON, key order included.
ALTER TABLE rag.chunks ALTER COLUMN metadata TYPE json USING metadata::json;
