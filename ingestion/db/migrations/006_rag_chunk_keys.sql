-- The order of a chunk row's keys in chunks.jsonl, so a row read back from rag.chunks is the same dict, key for key and in
-- the same order, as the file row (jsonb does not keep key order). Filled by storage/rag_loader.py.
ALTER TABLE rag.releases ADD COLUMN chunk_keys jsonb;
