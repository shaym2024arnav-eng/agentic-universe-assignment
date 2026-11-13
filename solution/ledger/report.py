"""Build the five report files from everything ingested so far."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .catalog import Disposition, Schema
from .derive import Call, Event, bucket, link_events
from .normalize import IST, to_ist_iso

BUCKETS = ["yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"]


def _load_events(conn: sqlite3.Connection) -> list[Event]:
    rows = conn.execute(
        "SELECT event_id, event_type, workflow_id, call_id, occurred_at, phone_hmac, payload_json "
        "FROM events ORDER BY event_id")
    return [
        Event(r["event_id"], r["event_type"], r["workflow_id"], r["call_id"],
              datetime.fromisoformat(r["occurred_at"]) if r["occurred_at"] else None,
              r["phone_hmac"], json.loads(r["payload_json"]))
        for r in rows
    ]


def _write_csv(path: Path, header: list[str], rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _b(x: bool) -> str:
    return "true" if x else "false"


def build_reports(conn: sqlite3.Connection, schemas: dict[str, Schema],
                  catalog: dict[tuple[str, str], Disposition], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    leads = conn.execute(
        "SELECT workflow_id, phone_hmac, phone_last4, batch_id, external_ref FROM leads").fetchall()
    lead_keys = {(r["workflow_id"], r["phone_hmac"]) for r in leads}
    linked = link_events(_load_events(conn), lead_keys, catalog)

    calls_by_lead: dict[tuple[str, str], list[Call]] = defaultdict(list)
    for c in linked.calls.values():
        calls_by_lead[c.lead].append(c)

    def is_success(c: Call) -> bool:
        d = catalog.get((c.lead[0], c.effective_disposition)) if c.effective_disposition else None
        return bool(d and d.is_success)

    # ---- lead_report.csv + batch_summary.csv
    batch_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    lead_rows = []
    for r in leads:
        key = (r["workflow_id"], r["phone_hmac"])
        calls = sorted(calls_by_lead.get(key, []), key=lambda c: (c.initiated_at, c.call_id))
        b = bucket(calls, catalog)
        batch_counts[(r["workflow_id"], r["batch_id"])][b] += 1
        with_disp = [c for c in calls if c.effective_disposition]
        lead_rows.append((
            r["workflow_id"], r["external_ref"] or "", r["batch_id"], "+91******" + r["phone_last4"], b,
            len(calls), sum(c.connected for c in calls),
            to_ist_iso(calls[-1].initiated_at) if calls else "",
            with_disp[-1].effective_disposition if with_disp else "",
            _b(any(is_success(c) for c in calls)),
        ))
    lead_rows.sort(key=lambda x: (x[0], x[1]))
    _write_csv(out / "lead_report.csv",
               ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
                "connected_calls", "last_call_at", "current_disposition", "ever_success"], lead_rows)

    _write_csv(out / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", *BUCKETS],
               [(wf, bid, sum(cnt.values()), *(cnt[b] for b in BUCKETS))
                for (wf, bid), cnt in sorted(batch_counts.items())])

    # ---- daily_overview.csv
    daily: dict[tuple[str, str], dict] = defaultdict(lambda: {"total": 0, "connected": 0, "success": set()})
    for c in linked.calls.values():
        d = daily[(c.lead[0], c.initiated_at.astimezone(IST).date().isoformat())]
        d["total"] += 1
        d["connected"] += c.connected
        if is_success(c):
            d["success"].add(c.lead)
    _write_csv(out / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
               [(wf, day, v["total"], v["connected"], f"{v['connected'] / v['total']:.4f}", len(v["success"]))
                for (wf, day), v in sorted(daily.items())])

    # ---- rejections.csv (no PII: only ids, row numbers and reasons)
    rej = conn.execute(
        "SELECT workflow_id, batch_id, row_number, reason FROM lead_rows WHERE outcome='rejected' "
        "ORDER BY workflow_id, batch_id, row_number").fetchall()
    _write_csv(out / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"],
               [tuple(r) for r in rej])

    # ---- data_quality.json
    lr = conn.execute(
        "SELECT COUNT(*) total, "
        "COALESCE(SUM(outcome='rejected'),0) rejected, COALESCE(SUM(outcome='merged'),0) merged, "
        "COALESCE(SUM(warnings),0) warnings FROM lead_rows").fetchone()
    ev = conn.execute(
        "SELECT COALESCE(SUM(lines_total),0) t, COALESCE(SUM(malformed),0) m, COALESCE(SUM(duplicates),0) d "
        "FROM source_files WHERE kind='events'").fetchone()
    by_reason = dict(sorted(Counter(r["reason"] for r in rej).items()))
    dq = {
        "lead_rows_total": lr["total"],
        "lead_rows_rejected": lr["rejected"],
        "rejections_by_reason": by_reason,
        "duplicate_lead_rows_merged": lr["merged"],
        "field_warnings": lr["warnings"],
        "events_total": ev["t"],
        "malformed_events": ev["m"],
        "duplicate_events_dropped": ev["d"],
        "orphan_events": linked.orphan_events,
        "unknown_disposition_events": linked.unknown_disposition_events,
    }
    (out / "data_quality.json").write_text(json.dumps(dq, indent=2) + "\n", encoding="utf-8")
    return dq
