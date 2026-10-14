-- P1 base schema (docs/collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md section 4).
-- registry: who the products are. corpus: what was collected (mirrors data/catalog while JSONL is the source of truth).
-- ingest: requests, runs and tasks. rag / app / preprocess / staging come in later migrations.
-- Key chain: (document_id, version_id, pdf_page); version_id is the SHA-256 of the raw file and never changes.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA registry;
CREATE SCHEMA corpus;
CREATE SCHEMA ingest;

-- ---- registry -------------------------------------------------------------------------------------

CREATE TABLE registry.ingredients (
    inn         text PRIMARY KEY,
    name_ja     text
);

CREATE TABLE registry.products (
    product_id            text PRIMARY KEY CHECK (product_id ~ '^[A-Z0-9]+$'),
    ingredient            text NOT NULL REFERENCES registry.ingredients(inn),
    kind                  text NOT NULL CHECK (kind IN ('reference', 'biosimilar')),
    reference_product_id  text REFERENCES registry.products(product_id) DEFERRABLE INITIALLY DEFERRED,
    development_code      text,
    ema_name              text,
    us_brand              text,
    ca_brand              text,
    company               text,
    added                 date NOT NULL,
    target                jsonb NOT NULL,   -- the targets.json entry as validated (ingestion.contracts.ProductTarget)
    record                jsonb,            -- data/catalog/products.jsonl row (EMA metadata, Purple Book licensure ...)
    CHECK ((kind = 'reference') = (reference_product_id IS NULL))
);

CREATE TABLE registry.licences (
    product_id    text NOT NULL REFERENCES registry.products(product_id),
    jurisdiction  text NOT NULL CHECK (jurisdiction IN ('EU', 'US', 'CA', 'JP', 'KR')),
    name          text NOT NULL,
    licence_id    text,
    holder        text,
    authorised    date,
    is_primary    boolean NOT NULL,
    collected     boolean NOT NULL,
    PRIMARY KEY (jurisdiction, name)          -- one licence name belongs to one product
);
CREATE INDEX ON registry.licences (product_id);

-- ---- corpus ---------------------------------------------------------------------------------------

CREATE TABLE corpus.raw_objects (
    sha256        text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    uri           text NOT NULL,              -- project-relative path today (data/raw/<sha256>.<ext>); object storage later
    bytes         bigint NOT NULL,
    kind          text NOT NULL,              -- pdf, html, json, csv, zip
    content_type  text
);

CREATE TABLE corpus.downloads (              -- download_cache.json: URL -> latest observed version
    url           text PRIMARY KEY,
    sha256        text NOT NULL REFERENCES corpus.raw_objects(sha256),
    source        text NOT NULL,
    retrieved_at  timestamptz NOT NULL,
    run_id        text,
    metadata      jsonb NOT NULL
);

CREATE TABLE corpus.documents (
    document_id         text PRIMARY KEY,
    current_version_id  text NOT NULL,
    product_id          text REFERENCES registry.products(product_id),   -- null for guidelines
    ingredient          text REFERENCES registry.ingredients(inn),
    product_kind        text CHECK (product_kind IN ('reference', 'biosimilar')),
    source              text NOT NULL,
    role                text NOT NULL,
    document_type       text NOT NULL,
    family              text NOT NULL CHECK (family <> 'unclassified'),
    author              text NOT NULL,
    tier                text NOT NULL,
    default_search      boolean NOT NULL,
    title               text NOT NULL,
    url                 text NOT NULL,
    nct_id              text,
    last_seen_run       text,
    CHECK ((role = 'guideline') = (product_id IS NULL))
);
CREATE INDEX ON corpus.documents (product_id);
CREATE INDEX ON corpus.documents (source, tier);

CREATE TABLE corpus.document_versions (
    document_id          text NOT NULL REFERENCES corpus.documents(document_id),
    version_id           text NOT NULL REFERENCES corpus.raw_objects(sha256),
    raw_path             text NOT NULL,
    text_path            text,
    parsed_path          text NOT NULL,
    page_count           integer NOT NULL CHECK (page_count >= 0),
    nonempty_text_pages  integer NOT NULL,
    parser               text NOT NULL,
    parser_version       text NOT NULL,
    retrieved_at         timestamptz NOT NULL,
    metadata             jsonb NOT NULL,      -- the full catalog row
    PRIMARY KEY (document_id, version_id)
);

ALTER TABLE corpus.documents ADD FOREIGN KEY (document_id, current_version_id)
    REFERENCES corpus.document_versions(document_id, version_id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE corpus.pages (
    document_id     text NOT NULL,
    version_id      text NOT NULL,
    pdf_page        integer NOT NULL CHECK (pdf_page >= 1),   -- PDF page from 1, or section number for web pages
    pdf_page_label  text,
    locator_type    text NOT NULL DEFAULT 'pdf_page',
    text            text NOT NULL,
    text_sha256     text NOT NULL,
    metadata        jsonb NOT NULL,           -- blocks, image regions, text-layer check
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    PRIMARY KEY (document_id, version_id, pdf_page),
    FOREIGN KEY (document_id, version_id) REFERENCES corpus.document_versions(document_id, version_id)
);
CREATE INDEX ON corpus.pages USING gin (tsv);

CREATE TABLE corpus.studies (
    nct_id       text PRIMARY KEY CHECK (nct_id ~ '^NCT\d{8}$'),
    product_id   text NOT NULL REFERENCES registry.products(product_id),
    phases       text[] NOT NULL,
    has_results  boolean NOT NULL,
    metadata     jsonb NOT NULL
);

CREATE TABLE corpus.study_results (
    nct_id         text NOT NULL REFERENCES corpus.studies(nct_id),
    outcome_index  integer NOT NULL,
    product_id     text NOT NULL REFERENCES registry.products(product_id),
    title          text,
    review_status  text NOT NULL,             -- posted_results_not_regulator_reviewed
    metadata       jsonb NOT NULL,
    PRIMARY KEY (nct_id, outcome_index)
);

CREATE TABLE corpus.guidelines (
    guideline_id  text PRIMARY KEY,
    jurisdiction  text NOT NULL,
    status        text,
    checked       date,
    seed          jsonb NOT NULL,             -- guidelines.json entry
    record        jsonb NOT NULL              -- data/catalog/guidelines.jsonl row (status read from the landing page)
);

-- ---- ingest ---------------------------------------------------------------------------------------

CREATE TABLE ingest.runs (
    run_id       text PRIMARY KEY,
    kind         text NOT NULL,              -- collect, partial, reparse, promote, staging, catalog_load
    started_at   timestamptz,
    finished_at  timestamptz,
    metrics      jsonb NOT NULL
);

CREATE TABLE ingest.run_downloads (
    run_id    text NOT NULL REFERENCES ingest.runs(run_id),
    ordinal   integer NOT NULL,
    url       text NOT NULL,
    sha256    text NOT NULL,
    reused    boolean NOT NULL,
    metadata  jsonb NOT NULL,
    PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE ingest.run_errors (
    run_id      text NOT NULL REFERENCES ingest.runs(run_id),
    ordinal     integer NOT NULL,
    label       text NOT NULL,
    error_type  text NOT NULL,
    message     text NOT NULL,
    at          timestamptz,
    PRIMARY KEY (run_id, ordinal)
);

CREATE TABLE ingest.requests (
    request_id        uuid PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    kind              text NOT NULL CHECK (kind IN ('new_product', 'refresh', 'new_guideline')),
    contract_version  text NOT NULL,
    payload           jsonb NOT NULL,         -- ingestion.contracts.IngestionRequest, validated before insert
    status            text NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'staging', 'awaiting_approval', 'approved', 'promoting', 'done', 'failed', 'cancelled'))
);

CREATE TABLE ingest.request_events (        -- append only
    event_id    bigserial PRIMARY KEY,
    request_id  uuid NOT NULL REFERENCES ingest.requests(request_id),
    at          timestamptz NOT NULL DEFAULT now(),
    event       text NOT NULL,
    payload     jsonb NOT NULL
);

CREATE TABLE ingest.tasks (
    run_id            text NOT NULL REFERENCES ingest.runs(run_id),
    step              text NOT NULL,
    product_id        text NOT NULL DEFAULT '',   -- '' when the task is not per product (lists, guidelines)
    document_version  text NOT NULL DEFAULT '',   -- '<document_id>@<version_id>' for per-document tasks
    request_id        uuid REFERENCES ingest.requests(request_id),
    status            text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
    attempts          integer NOT NULL DEFAULT 0,
    result            jsonb,                  -- ingestion.contracts.TaskResult
    updated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, step, product_id, document_version)   -- the idempotency key (TaskKey)
);

CREATE TABLE ingest.approvals (             -- append only; promote runs only against one of these
    approval_id     uuid PRIMARY KEY,
    request_id      uuid REFERENCES ingest.requests(request_id),
    product_id      text NOT NULL,
    approved_by     text NOT NULL,
    approved_at     timestamptz NOT NULL DEFAULT now(),
    report_sha256   text NOT NULL             -- the check table the person saw
);

CREATE FUNCTION ingest.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append only', TG_TABLE_NAME;
END $$;
CREATE TRIGGER request_events_append_only BEFORE UPDATE OR DELETE ON ingest.request_events
    FOR EACH ROW EXECUTE FUNCTION ingest.refuse_change();
CREATE TRIGGER approvals_append_only BEFORE UPDATE OR DELETE ON ingest.approvals
    FOR EACH ROW EXECUTE FUNCTION ingest.refuse_change();
