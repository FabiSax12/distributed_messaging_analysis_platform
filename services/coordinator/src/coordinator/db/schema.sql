-- Coordinator state. Applied at every coordinator startup (db.apply_schema),
-- so every statement must be idempotent. Column names match the pydantic
-- models in shared/shared/models.py.

CREATE TABLE IF NOT EXISTS cases (
    case_id          text PRIMARY KEY,           -- ULID, sortable by creation
    label            text,
    source           text        NOT NULL,
    priority         text        NOT NULL,       -- shared.analysis.CasePriority
    status           text        NOT NULL,       -- shared.states.CaseStatus
    file_count       integer     NOT NULL CHECK (file_count >= 1),
    total            integer     NOT NULL CHECK (total >= 1),     -- sub-tasks
    pending_count    integer     NOT NULL CHECK (pending_count >= 0),  -- barrier
    failed_count     integer     NOT NULL DEFAULT 0,
    cancelled_count  integer     NOT NULL DEFAULT 0,
    cancel_requested boolean     NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL,
    started_at       timestamptz,
    finished_at      timestamptz,
    report_key       text
);

-- Open cases (sweeper, snapshot) and recent cases per status (API, snapshot).
CREATE INDEX IF NOT EXISTS cases_open_idx ON cases (case_id)
    WHERE status NOT IN ('completed', 'partially_completed', 'failed', 'cancelled');
CREATE INDEX IF NOT EXISTS cases_status_idx ON cases (status, case_id DESC);

CREATE TABLE IF NOT EXISTS subtasks (
    subtask_id  text PRIMARY KEY,                -- {case_id}-0001
    case_id     text        NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    input_key   text        NOT NULL,
    media_type  text,                            -- shared.analysis.ContentType
    operation   text,                            -- shared.analysis.AnalysisOperation
    pool        text,
    queue       text,                            -- NULL = prefailed, never published
    priority    text        NOT NULL,
    status      text        NOT NULL,            -- shared.states.SubTaskStatus
    state_order integer     NOT NULL DEFAULT 0,  -- only ever grows
    attempt     integer     NOT NULL DEFAULT 1,  -- attempt of the latest dispatch
    enqueued    boolean     NOT NULL DEFAULT false,  -- outbox: broker confirmed
    closed      boolean     NOT NULL DEFAULT false,  -- counted by the barrier
    retry_at    timestamptz,                     -- republish time when retrying
    worker_id   text,
    progress    integer,
    created_at  timestamptz NOT NULL,
    started_at  timestamptz,
    finished_at timestamptz,
    output_keys jsonb       NOT NULL DEFAULT '[]',
    output_meta jsonb       NOT NULL DEFAULT '{}',
    error       jsonb,
    params      jsonb       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS subtasks_case_idx ON subtasks (case_id, subtask_id);
-- Outbox relay: what still has to be published (new or due for retry).
CREATE INDEX IF NOT EXISTS subtasks_outbox_idx ON subtasks (retry_at, subtask_id)
    WHERE NOT enqueued AND NOT closed AND queue IS NOT NULL;
-- Crash detection: in-flight sub-tasks per worker.
CREATE INDEX IF NOT EXISTS subtasks_in_flight_idx ON subtasks (worker_id)
    WHERE status IN ('assigned', 'running') AND NOT closed;

CREATE TABLE IF NOT EXISTS workers (
    worker_id       text PRIMARY KEY,
    pool            text             NOT NULL,
    instance_type   text             NOT NULL,
    hostname        text             NOT NULL,
    concurrency     integer          NOT NULL,
    cpu_percent     double precision NOT NULL DEFAULT 0,
    mem_percent     double precision NOT NULL DEFAULT 0,
    active_subtasks jsonb            NOT NULL DEFAULT '[]',
    status          text             NOT NULL DEFAULT 'alive',  -- alive | dead
    started_at      timestamptz      NOT NULL,
    last_seen       timestamptz      NOT NULL
);

-- Append-only custody chain; transaction-level lock precedes row mutations.
CREATE TABLE IF NOT EXISTS custody (
    sequence bigserial PRIMARY KEY,
    payload text NOT NULL,
    previous_hash text NOT NULL,
    hash text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION custody_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(66000003);
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION custody_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    body text;
    previous text;
BEGIN
    body := jsonb_build_object('table', TG_TABLE_NAME, 'operation', TG_OP,
      'actor', COALESCE(NULLIF(current_setting('app.actor', true), ''), 'coordinator'),
      'row', CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END)::text;
    SELECT hash INTO previous FROM custody ORDER BY sequence DESC LIMIT 1;
    previous := COALESCE(previous, repeat('0', 64));
    INSERT INTO custody(payload, previous_hash, hash)
      VALUES (body, previous, encode(sha256(convert_to(previous || body, 'UTF8')), 'hex'));
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION custody_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'custody records are append-only';
END $$;
DROP TRIGGER IF EXISTS custody_immutable ON custody;
CREATE TRIGGER custody_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON custody
    FOR EACH STATEMENT EXECUTE FUNCTION custody_immutable();
DROP TRIGGER IF EXISTS cases_custody_lock ON cases;
CREATE TRIGGER cases_custody_lock BEFORE INSERT OR UPDATE OR DELETE ON cases
    FOR EACH STATEMENT EXECUTE FUNCTION custody_lock();
DROP TRIGGER IF EXISTS cases_custody_record ON cases;
CREATE TRIGGER cases_custody_record AFTER INSERT OR UPDATE OR DELETE ON cases
    FOR EACH ROW EXECUTE FUNCTION custody_record();
DROP TRIGGER IF EXISTS subtasks_custody_lock ON subtasks;
CREATE TRIGGER subtasks_custody_lock BEFORE INSERT OR UPDATE OR DELETE ON subtasks
    FOR EACH STATEMENT EXECUTE FUNCTION custody_lock();
DROP TRIGGER IF EXISTS subtasks_custody_record ON subtasks;
CREATE TRIGGER subtasks_custody_record AFTER INSERT OR UPDATE OR DELETE ON subtasks
    FOR EACH ROW EXECUTE FUNCTION custody_record();
