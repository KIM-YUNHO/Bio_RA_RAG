-- RAG releases: chunks and bge-m3 embeddings (docs/collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md 4.1, P5;
-- docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md). Loaded by storage/rag_loader.py.
--
-- A release is immutable: its chunks and vectors are written once, in one transaction, together with the releases row.
-- Chunk rows keep the promoted columns the search path filters on; every other field of the chunks.jsonl row is in
-- `metadata` (jsonb) so the row can be rebuilt exactly (storage/rag_loader.py verify). `provenance` inside metadata still
-- holds the original project's absolute paths (see the integration map, section 3).
--
-- (document_id, version_id) points at corpus.document_versions but is not a foreign key: the corpus schema is a mirror that
-- ingestion/load_catalog.py truncates and refills, and a release is immutable. `storage/rag_loader.py verify` checks the
-- reference instead.

CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE rag.releases (
    release_id      text PRIMARY KEY CHECK (release_id ~ '^[a-z0-9_]+$'),
    kind            text NOT NULL DEFAULT 'corpus' CHECK (kind IN ('corpus', 'guideline')),
    title           text NOT NULL,
    source_dir      text NOT NULL,                -- release folder, relative to the project root
    n_chunks        integer NOT NULL,
    n_documents     integer NOT NULL,
    chunks_sha256   text NOT NULL,                -- sha256 of chunks.jsonl as loaded
    embedding_model text NOT NULL,
    embedding_dim   integer NOT NULL,
    similarity      text NOT NULL,
    embedding_meta  jsonb NOT NULL,               -- emb/bge-m3/meta.json, verbatim
    manifest        jsonb,                        -- release_manifest.json, verbatim (absolute paths of the original project)
    version_record  jsonb,                        -- the data/versions/versions.json entry
    loaded_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE rag.chunks (
    release_id           text NOT NULL REFERENCES rag.releases ON DELETE CASCADE,
    ordinal              integer NOT NULL,        -- line number in chunks.jsonl = row in doc.npy
    chunk_id             text NOT NULL,
    document_id          text NOT NULL,
    version_id           text NOT NULL,
    product_id           text,
    source               text,
    role                 text,
    document_type        text,
    doc_group            text,
    jurisdiction         text,
    locator_type         text,
    pdf_page             integer,
    pdf_page_label       text,
    section              text,
    header               text NOT NULL,
    text                 text NOT NULL,
    n_tokens             integer,
    n_tokens_with_header integer,
    content_hash         text,
    page_hash            text,
    metadata             jsonb NOT NULL,          -- all remaining chunk fields (document_metadata, structure, quality, provenance, ...)
    PRIMARY KEY (release_id, chunk_id),
    UNIQUE (release_id, ordinal)
);
CREATE INDEX chunks_location ON rag.chunks (release_id, document_id, version_id, pdf_page);
CREATE INDEX chunks_product ON rag.chunks (release_id, product_id);
CREATE INDEX chunks_content_hash ON rag.chunks (release_id, content_hash);

CREATE TABLE rag.release_documents (
    release_id  text NOT NULL REFERENCES rag.releases ON DELETE CASCADE,
    document_id text NOT NULL,
    version_id  text NOT NULL,
    n_chunks    integer NOT NULL,
    PRIMARY KEY (release_id, document_id, version_id)
);

-- One vector per chunk, BAAI/bge-m3 (1024, L2-normalized in fp16: norms are 0.9998..1.0005, not exactly 1).
-- rag/search.py ranks by inner product (`vec @ query`), so the index and queries use inner product (`<#>`, vector_ip_ops).
-- Cosine (`<=>`) divides by the norms and reorders near ties: 6-8% of top-10 sets differed from the application's on 2026-10-14.
-- One partition per release, so an approximate (HNSW) search of one release is not diluted by the others.
-- The loader creates the partition for each release.
CREATE TABLE rag.embeddings_bge_m3 (
    release_id text NOT NULL,
    chunk_id   text NOT NULL,
    embedding  vector(1024) NOT NULL,
    PRIMARY KEY (release_id, chunk_id),
    FOREIGN KEY (release_id, chunk_id) REFERENCES rag.chunks (release_id, chunk_id) ON DELETE CASCADE
) PARTITION BY LIST (release_id);
CREATE INDEX embeddings_bge_m3_hnsw ON rag.embeddings_bge_m3 USING hnsw (embedding vector_ip_ops);

-- Which release a role reads (pilot, latest, served_by_ra_service ...). Today's roles come from data/versions/versions.json.
CREATE TABLE rag.active_release (
    role       text PRIMARY KEY,
    release_id text NOT NULL REFERENCES rag.releases,
    note       text,
    set_at     timestamptz NOT NULL DEFAULT now()
);

-- Releases are immutable: rows are inserted or deleted (a whole release), never updated.
CREATE FUNCTION rag.reject_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is immutable; load a new release instead', TG_TABLE_NAME;
END $$;
CREATE TRIGGER chunks_immutable BEFORE UPDATE ON rag.chunks FOR EACH ROW EXECUTE FUNCTION rag.reject_update();
CREATE TRIGGER embeddings_immutable BEFORE UPDATE ON rag.embeddings_bge_m3 FOR EACH ROW EXECUTE FUNCTION rag.reject_update();
