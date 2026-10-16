-- Local RA notebooks. Independent of replaceable registry/corpus catalogs.
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE app.workspace_projects (
    project_id text PRIMARY KEY CHECK (project_id ~ '^[0-9a-f]{32}$'),
    name text NOT NULL,
    record jsonb NOT NULL CHECK (record->>'id' = project_id AND record->>'schema_version' = '1'),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX workspace_project_name ON app.workspace_projects (lower(name));

-- Server-registered locations only. Never cascade these when a catalog reloads.
CREATE TABLE app.memo_documents (
    document_id text NOT NULL,
    version_id text NOT NULL,
    path text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (document_id, version_id)
);
