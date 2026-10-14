-- P3: ingestion request agent sessions and model calls (plan section 7). The first session data kept in PostgreSQL;
-- the RA service's conversations move to the app schema in P5.

CREATE TABLE ingest.agent_sessions (
    session_id   uuid PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    state        jsonb NOT NULL              -- the state machine's slots: stage, candidates shown, target, plan, request_id
);

CREATE TABLE ingest.agent_turns (            -- append only
    session_id   uuid NOT NULL REFERENCES ingest.agent_sessions(session_id),
    turn         integer NOT NULL,
    at           timestamptz NOT NULL DEFAULT now(),
    user_text    text NOT NULL,
    parse        jsonb,                      -- ingestion.contracts.TurnParse, null for slash commands
    actions      jsonb NOT NULL,             -- tools the code ran this turn
    reply        text NOT NULL,
    stage_after  text NOT NULL,
    PRIMARY KEY (session_id, turn)
);

CREATE TABLE ingest.model_calls (            -- append only; every language-model call made by ingestion code
    call_id            bigserial PRIMARY KEY,
    at                 timestamptz NOT NULL DEFAULT now(),
    purpose            text NOT NULL,        -- agent_turn, agent_benchmark
    session_id         uuid,
    model_requested    text NOT NULL,
    model_reported     text,
    prompt_tokens      integer,
    completion_tokens  integer,
    request_sha256     text NOT NULL,
    ok                 boolean NOT NULL,
    error              text
);

CREATE TRIGGER agent_turns_append_only BEFORE UPDATE OR DELETE ON ingest.agent_turns
    FOR EACH ROW EXECUTE FUNCTION ingest.refuse_change();
CREATE TRIGGER model_calls_append_only BEFORE UPDATE OR DELETE ON ingest.model_calls
    FOR EACH ROW EXECUTE FUNCTION ingest.refuse_change();
