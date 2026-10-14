-- P2: name registry and licence status (docs/collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md sections 5 and 6).

-- Every list or page fetched for the registry; the bytes live in data/registry/raw/<sha256>.<ext>.
CREATE TABLE registry.source_snapshots (
    snapshot_id  bigserial PRIMARY KEY,
    source       text NOT NULL,              -- EMA medicines JSON, Purple Book, HC DPD, GSRS, ClinicalTrials.gov
    url          text NOT NULL,
    sha256       text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    uri          text NOT NULL,
    bytes        bigint NOT NULL,
    as_of        text,                       -- the source's own date when it has one (Purple Book month)
    fetched_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON registry.source_snapshots (url, fetched_at DESC);

-- One row per (name, where it was seen). Rebuilt from the latest snapshots; on-demand lookups (codes, Canadian brands) add rows.
CREATE TABLE registry.entity_names (
    name_id       bigserial PRIMARY KEY,
    name          text NOT NULL,
    name_norm     text NOT NULL,             -- lower case, letters and digits only (any script)
    name_type     text NOT NULL CHECK (name_type IN ('inn', 'proper_name', 'brand', 'dev_code', 'company', 'korean')),
    jurisdiction  text CHECK (jurisdiction IN ('EU', 'US', 'CA', 'JP', 'KR')),
    ingredient    text,                      -- INN, lower case
    product_key   text,                      -- registered product_id, or EU:<number> / US:BLA<number> / CA:<brand> / CODE:<code>
    product_kind  text CHECK (product_kind IN ('reference', 'biosimilar', 'other')),
    licence_id    text,
    status        text,                      -- the source's own wording
    source        text NOT NULL,
    source_url    text,
    snapshot_id   bigint REFERENCES registry.source_snapshots(snapshot_id),
    origin        text NOT NULL CHECK (origin IN ('build', 'lookup', 'catalog'))
);
CREATE INDEX ON registry.entity_names (name_norm);
CREATE INDEX ON registry.entity_names USING gin (name_norm gin_trgm_ops);
CREATE INDEX ON registry.entity_names (ingredient);
CREATE INDEX ON registry.entity_names (product_key);

-- Each licence status lookup, kept (append only) so an answer can be traced and compared with the next one.
CREATE TABLE registry.licence_status_snapshots (
    status_id     bigserial PRIMARY KEY,
    checked_at    timestamptz NOT NULL DEFAULT now(),
    product_key   text NOT NULL,
    ingredient    text NOT NULL,
    jurisdiction  text NOT NULL,
    name          text NOT NULL,
    status        text NOT NULL,
    authorised    date,
    details       jsonb NOT NULL,
    evidence      jsonb NOT NULL,
    snapshot_id   bigint REFERENCES registry.source_snapshots(snapshot_id)
);
CREATE INDEX ON registry.licence_status_snapshots (product_key, checked_at DESC);
CREATE TRIGGER licence_status_append_only BEFORE UPDATE OR DELETE ON registry.licence_status_snapshots
    FOR EACH ROW EXECUTE FUNCTION ingest.refuse_change();
