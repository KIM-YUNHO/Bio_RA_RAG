-- P4: collection as stateless tasks (plan section 9.1). Each task writes what it produced here instead of into a shared
-- Collector object; a later task reads its inputs from here. Keyed like ingest.tasks: (run_id, step, product_id, document_version).

CREATE TABLE ingest.task_outputs (
    run_id            text NOT NULL,
    step              text NOT NULL,
    product_id        text NOT NULL DEFAULT '',
    document_version  text NOT NULL DEFAULT '',
    ordinal           integer NOT NULL,
    kind              text NOT NULL CHECK (kind IN ('document', 'parsed_document', 'ema_product', 'licensure', 'study', 'study_result',
                                                    'guideline_record', 'download', 'error')),
    payload           jsonb NOT NULL,
    PRIMARY KEY (run_id, step, product_id, document_version, ordinal),
    FOREIGN KEY (run_id, step, product_id, document_version) REFERENCES ingest.tasks(run_id, step, product_id, document_version) ON DELETE CASCADE
);
CREATE INDEX ON ingest.task_outputs (run_id, kind);

-- Lists one task leaves for the next (ema -> ema_documents, fda -> fda_reviews / fda_letters), as JSONL text.
CREATE TABLE ingest.task_artifacts (
    run_id            text NOT NULL,
    step              text NOT NULL,
    product_id        text NOT NULL DEFAULT '',
    document_version  text NOT NULL DEFAULT '',
    name              text NOT NULL,
    content           text NOT NULL,
    PRIMARY KEY (run_id, step, product_id, document_version, name),
    FOREIGN KEY (run_id, step, product_id, document_version) REFERENCES ingest.tasks(run_id, step, product_id, document_version) ON DELETE CASCADE
);
