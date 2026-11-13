"""SQLite schema. See SUBMISSION.md for the reasoning behind each table."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
PRAGMA foreign_keys = ON;

-- One row per source file ever ingested. The idempotency guard: a file whose
-- (kind, workflow_id, file_name) is already here with the same hash is skipped.
CREATE TABLE IF NOT EXISTS source_files (
    file_id       INTEGER PRIMARY KEY,
    ingest_seq    INTEGER NOT NULL,          -- order of the `ingest` run (drop) that loaded it
    kind          TEXT    NOT NULL CHECK (kind IN ('leads', 'events')),
    workflow_id   TEXT    NOT NULL DEFAULT '',  -- '' for event files
    file_name     TEXT    NOT NULL,
    sha256        TEXT    NOT NULL,
    lines_total   INTEGER NOT NULL DEFAULT 0, -- events: non-empty lines read
    malformed     INTEGER NOT NULL DEFAULT 0,
    duplicates    INTEGER NOT NULL DEFAULT 0,
    ingested_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (kind, workflow_id, file_name)
);

-- Current state of each lead. Identity = (workflow_id, phone). The phone is only present
-- as an HMAC blind index + last 4 digits; full PII is encrypted in pii_enc.
CREATE TABLE IF NOT EXISTS leads (
    workflow_id   TEXT NOT NULL,
    phone_hmac    TEXT NOT NULL,
    phone_last4   TEXT NOT NULL,
    batch_id      TEXT NOT NULL,             -- batch of the first valid row, never changes
    external_ref  TEXT,
    attrs_json    TEXT NOT NULL,             -- non-PII fields (normalised)
    pii_enc       BLOB NOT NULL,             -- Fernet(JSON of PII fields)
    PRIMARY KEY (workflow_id, phone_hmac)
);

-- Outcome of every non-blank lead row: feeds rejections.csv and data_quality.json.
CREATE TABLE IF NOT EXISTS lead_rows (
    file_id       INTEGER NOT NULL REFERENCES source_files(file_id),
    row_number    INTEGER NOT NULL,
    workflow_id   TEXT    NOT NULL,
    batch_id      TEXT    NOT NULL,
    outcome       TEXT    NOT NULL CHECK (outcome IN ('created', 'merged', 'rejected')),
    reason        TEXT,                      -- only for rejected
    warnings      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, row_number)
);

-- The interaction ledger. Append-only: enforced by triggers below.
-- Linking to leads is NOT frozen here; it is derived at report time, so an event that
-- arrived before its lead links automatically once the lead exists.
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,          -- dedupe key
    file_id       INTEGER NOT NULL REFERENCES source_files(file_id),
    line_no       INTEGER NOT NULL,
    event_type    TEXT,
    workflow_id   TEXT,
    call_id       TEXT,
    occurred_at   TEXT,                      -- UTC ISO-8601
    phone_hmac    TEXT,                      -- NULL if no phone or phone invalid
    phone_enc     BLOB,                      -- encrypted raw phone as received
    payload_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_link ON events (workflow_id, phone_hmac);
CREATE INDEX IF NOT EXISTS ix_events_call ON events (call_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'interaction ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'interaction ledger is append-only'); END;
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(DDL)
    return conn
