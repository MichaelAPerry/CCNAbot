-- CCNAbot local store. One of these lives on each rack PC.
--
-- `observations` is append-only and is the single source of truth. Mastery,
-- FSRS scheduling state and the signed proficiency report are all derived by
-- folding it, never stored. Two triggers enforce the invariant at the database
-- level so a future careless UPDATE cannot silently rewrite a student's record.
--
-- Rack PCs sync by exchanging rows; `event_id` is a UUID so the union is
-- idempotent and order-independent.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Roster. Mutable by design: names and cohorts get corrected.
-- Keep this minimal -- it is the only place the store holds anything
-- identifying, and it is student data.
CREATE TABLE IF NOT EXISTS students (
    student_id   TEXT PRIMARY KEY,      -- POSIX username on the rack PC
    display_name TEXT NOT NULL DEFAULT '',
    track        TEXT NOT NULL DEFAULT 'networking',
    cohort       TEXT NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS observations (
    event_id    TEXT PRIMARY KEY,       -- UUID; makes cross-rack merge idempotent
    student_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,          -- CIP 11.0901 task number
    kind        TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    occurred_at TEXT NOT NULL,          -- ISO 8601, UTC, timezone-aware
    source      TEXT NOT NULL,          -- which rack PC recorded it
    detail      TEXT NOT NULL DEFAULT '{}'   -- JSON payload, kind-specific
);

CREATE INDEX IF NOT EXISTS obs_student_task ON observations (student_id, task_id);
CREATE INDEX IF NOT EXISTS obs_occurred     ON observations (occurred_at);
CREATE INDEX IF NOT EXISTS obs_student_time ON observations (student_id, occurred_at);

-- Append-only enforcement. Evidence behind a signed state record must not be
-- editable after the fact; corrections are made by appending, not rewriting.
CREATE TRIGGER IF NOT EXISTS observations_no_update
BEFORE UPDATE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations are append-only: append a correction instead');
END;

CREATE TRIGGER IF NOT EXISTS observations_no_delete
BEFORE DELETE ON observations
BEGIN
    SELECT RAISE(ABORT, 'observations are append-only: append a revocation instead');
END;
