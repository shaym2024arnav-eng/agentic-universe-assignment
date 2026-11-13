"""Ingest one drop (lead CSVs + event JSONL) into the database.

A whole drop is applied in one transaction: a crash mid-drop leaves the DB as it was, and
re-running the same drop afterwards is a no-op because every file is recorded in
source_files with its content hash.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from .catalog import Schema
from .crypto import Vault
from .normalize import (Invalid, normalize_phone, parse_date, parse_decimal, parse_enum,
                        parse_timestamp, normalize_header)


class IngestError(RuntimeError):
    pass


@dataclass
class RowResult:
    reason: str | None          # rejection reason, None if the row is a valid lead
    values: dict                # field name -> normalised value (None = missing/invalid)
    warnings: int


# --------------------------------------------------------------------------- lead rows

def _parse_field(ftype: str, raw: str, values: tuple[str, ...]):
    if ftype == "string":
        return raw
    if ftype == "phone":
        p = normalize_phone(raw)
        if p is None:
            raise Invalid("invalid phone")
        return p
    if ftype == "date":
        return parse_date(raw).isoformat()
    if ftype == "decimal":
        return str(parse_decimal(raw))
    if ftype == "enum":
        return parse_enum(raw, list(values))
    raise IngestError(f"unknown field type {ftype}")


def validate_row(schema: Schema, raw: dict[str, str | None]) -> RowResult:
    """Apply the spec's per-row rules. `raw` maps field name -> trimmed value or None."""
    reason: str | None = None
    warnings = 0
    out: dict = {}
    for f in schema.fields:  # schema order: first failure wins
        value = raw.get(f.name)
        if value is None:
            out[f.name] = None
            if f.required and reason is None:
                reason = f"missing_required:{f.name}"
            continue
        try:
            out[f.name] = _parse_field(f.type, value, f.values)
        except Invalid:
            out[f.name] = None
            if f.type == "phone":
                # The phone is the lead's identity, so an invalid one always rejects the row.
                reason = reason or "invalid_phone"
            elif f.required:
                reason = reason or f"invalid:{f.name}"   # not reachable with current schemas
            else:
                warnings += 1
    return RowResult(reason, out, 0 if reason else warnings)


def read_lead_file(path: Path, schema: Schema):
    """Yield (row_number, {field: trimmed value or None}) for each non-blank data row."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return
        col_to_field: dict[int, str] = {}
        taken: set[str] = set()
        for i, h in enumerate(header):
            name = schema.header_map.get(normalize_header(h))
            if name and name not in taken:  # if two columns map to one field, first wins
                col_to_field[i] = name
                taken.add(name)
        row_number = 0
        for cells in reader:
            if all(not c.strip() for c in cells):
                continue  # blank rows are skipped and not counted
            row_number += 1
            raw: dict[str, str | None] = {}
            for i, name in col_to_field.items():
                v = cells[i].strip() if i < len(cells) else ""
                raw[name] = v or None
            yield row_number, raw


def _apply_lead_row(conn: sqlite3.Connection, vault: Vault, schema: Schema, batch_id: str,
                    values: dict) -> str:
    """Create or merge a lead. Returns 'created' or 'merged'."""
    phone = values[schema.phone_field]
    key = (schema.workflow_id, vault.blind_index(phone))
    pii_names = {f.name for f in schema.fields if f.pii}
    ref = schema.reference_field
    existing = conn.execute(
        "SELECT attrs_json, pii_enc FROM leads WHERE workflow_id=? AND phone_hmac=?", key
    ).fetchone()
    if existing is None:
        merged = dict(values)
        outcome = "created"
    else:
        merged = {**json.loads(existing["attrs_json"]), **vault.decrypt(existing["pii_enc"])}
        for k, v in values.items():
            if v is not None:  # last non-null write wins
                merged[k] = v
        outcome = "merged"
    attrs = {k: v for k, v in merged.items() if k not in pii_names}
    pii = {k: v for k, v in merged.items() if k in pii_names}
    if outcome == "created":
        conn.execute(
            "INSERT INTO leads (workflow_id, phone_hmac, phone_last4, batch_id, external_ref,"
            " attrs_json, pii_enc) VALUES (?,?,?,?,?,?,?)",
            (*key, phone[-4:], batch_id, merged.get(ref), json.dumps(attrs, sort_keys=True),
             vault.encrypt(pii)),
        )
    else:
        conn.execute(
            "UPDATE leads SET external_ref=?, attrs_json=?, pii_enc=? "
            "WHERE workflow_id=? AND phone_hmac=?",
            (merged.get(ref), json.dumps(attrs, sort_keys=True), vault.encrypt(pii), *key),
        )
    return outcome


# --------------------------------------------------------------------------- events

def _parse_event_line(line: str):
    """Return the event dict, or None if malformed."""
    try:
        e = json.loads(line)
    except ValueError:
        return None
    if not isinstance(e, dict) or e.get("event_id") in (None, ""):
        return None
    try:
        e["_occurred_utc"] = parse_timestamp(e["occurred_at"]).isoformat() if e.get("occurred_at") else None
    except (ValueError, TypeError):
        return None  # unparseable timestamp: treated as malformed (documented decision)
    return e


def _ingest_event_file(conn, vault: Vault, file_id: int, path: Path) -> tuple[int, int, int]:
    total = malformed = dupes = 0
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            total += 1
            e = _parse_event_line(line)
            if e is None:
                malformed += 1
                continue
            raw_phone = e.get("phone")
            canon = normalize_phone(raw_phone) if raw_phone not in (None, "") else None
            cur = conn.execute(
                "INSERT OR IGNORE INTO events (event_id, file_id, line_no, event_type, workflow_id,"
                " call_id, occurred_at, phone_hmac, phone_enc, payload_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (str(e["event_id"]), file_id, line_no, e.get("event_type"), e.get("workflow_id"),
                 e.get("call_id"), e["_occurred_utc"],
                 vault.blind_index(canon) if canon else None,
                 vault.encrypt(raw_phone) if raw_phone not in (None, "") else None,
                 json.dumps(e.get("payload") or {}, sort_keys=True)),
            )
            if cur.rowcount == 0:
                dupes += 1
    return total, malformed, dupes


# --------------------------------------------------------------------------- drop

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _register_file(conn, seq: int, kind: str, workflow_id: str, path: Path):
    """Return a new file_id, or None if this exact file was already ingested."""
    digest = _sha256(path)
    row = conn.execute(
        "SELECT sha256 FROM source_files WHERE kind=? AND workflow_id=? AND file_name=?",
        (kind, workflow_id, path.name),
    ).fetchone()
    if row is not None:
        if row["sha256"] != digest:
            raise IngestError(
                f"{kind} file {workflow_id}/{path.name} was already ingested with different "
                "content; refusing to silently re-ingest it")
        return None
    cur = conn.execute(
        "INSERT INTO source_files (ingest_seq, kind, workflow_id, file_name, sha256)"
        " VALUES (?,?,?,?,?)", (seq, kind, workflow_id, path.name, digest))
    return cur.lastrowid


def ingest_drop(conn: sqlite3.Connection, vault: Vault, schemas: dict[str, Schema],
                drop: Path) -> dict:
    if not drop.is_dir():
        raise IngestError(f"{drop} is not a directory")
    stats = {"lead_files": 0, "event_files": 0, "skipped_files": 0}
    conn.execute("BEGIN IMMEDIATE")
    try:
        seq = conn.execute("SELECT COALESCE(MAX(ingest_seq), 0) + 1 FROM source_files").fetchone()[0]

        lead_files = sorted((drop / "leads").glob("*/*.csv"), key=lambda p: (p.name, p.parent.name))
        for path in lead_files:
            wf = path.parent.name
            schema = schemas.get(wf)
            if schema is None:
                print(f"warning: no schema for workflow {wf!r}, skipping {path}", file=sys.stderr)
                continue
            file_id = _register_file(conn, seq, "leads", wf, path)
            if file_id is None:
                stats["skipped_files"] += 1
                continue
            batch_id = path.stem
            for row_number, raw in read_lead_file(path, schema):
                res = validate_row(schema, raw)
                outcome = "rejected" if res.reason else _apply_lead_row(conn, vault, schema, batch_id, res.values)
                conn.execute(
                    "INSERT INTO lead_rows (file_id, row_number, workflow_id, batch_id, outcome,"
                    " reason, warnings) VALUES (?,?,?,?,?,?,?)",
                    (file_id, row_number, wf, batch_id, outcome, res.reason, res.warnings))
            stats["lead_files"] += 1

        for path in sorted((drop / "events").glob("*.jsonl")):
            file_id = _register_file(conn, seq, "events", "", path)
            if file_id is None:
                stats["skipped_files"] += 1
                continue
            total, malformed, dupes = _ingest_event_file(conn, vault, file_id, path)
            conn.execute("UPDATE source_files SET lines_total=?, malformed=?, duplicates=? WHERE file_id=?",
                         (total, malformed, dupes, file_id))
            stats["event_files"] += 1
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return stats
