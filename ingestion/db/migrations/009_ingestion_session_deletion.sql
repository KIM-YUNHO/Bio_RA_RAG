-- Hide deleted conversations without altering append-only turns or request records.
ALTER TABLE ingest.agent_sessions ADD COLUMN deleted_at timestamptz;
CREATE INDEX agent_sessions_visible_updated_idx ON ingest.agent_sessions (updated_at DESC)
    WHERE deleted_at IS NULL;
