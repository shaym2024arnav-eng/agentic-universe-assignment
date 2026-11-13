"""End-to-end tests: generate data with the provided generator and run the real CLI."""
import csv
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ledger.cli import REPO_ROOT, main

GENERATOR = REPO_ROOT / "assignment-a-data" / "tools" / "generate_data.py"


def generate(out: Path, seed: int) -> Path:
    subprocess.run([sys.executable, str(GENERATOR), "--seed", str(seed), "--scale", "small",
                    "--out", str(out)], check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def data42(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("d42"), 42)


@pytest.fixture(scope="module")
def data7(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("d7"), 7)


def run(db: Path, *args) -> None:
    assert main(["--db", str(db), *args]) == 0


def read_all(d: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(d.iterdir())}


def test_incremental_equals_all_at_once_and_reingest_is_noop(data7, tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    run(a, "ingest", str(data7 / "drop_1"))
    run(a, "ingest", str(data7 / "drop_2"))
    run(a, "report", "--out", str(tmp_path / "ra1"))
    run(a, "ingest", str(data7 / "drop_1"))          # re-runs must change nothing
    run(a, "ingest", str(data7 / "drop_2"))
    run(a, "report", "--out", str(tmp_path / "ra2"))
    run(b, "ingest", str(data7 / "drop_1"), str(data7 / "drop_2"))
    run(b, "report", "--out", str(tmp_path / "rb"))
    r1 = read_all(tmp_path / "ra1")
    assert r1 == read_all(tmp_path / "ra2") == read_all(tmp_path / "rb")
    assert set(r1) == {"batch_summary.csv", "daily_overview.csv", "lead_report.csv",
                       "rejections.csv", "data_quality.json"}


def test_orphan_after_drop1_counts_after_drop2(data42, tmp_path):
    db = tmp_path / "x.db"
    run(db, "ingest", str(data42 / "drop_1"))
    run(db, "report", "--out", str(tmp_path / "r1"))
    run(db, "ingest", str(data42 / "drop_2"))
    run(db, "report", "--out", str(tmp_path / "r2"))
    leads1 = {r["external_ref"]: r for r in csv.DictReader(open(tmp_path / "r1" / "lead_report.csv"))}
    leads2 = {r["external_ref"]: r for r in csv.DictReader(open(tmp_path / "r2" / "lead_report.csv"))}
    new_with_calls = [r for k, r in leads2.items() if k not in leads1 and int(r["total_calls"]) > 0]
    assert new_with_calls, "leads first seen in drop_2 should pick up calls from drop_1 events"


def test_matches_readme_examples_for_seed_42(data42, tmp_path):
    db = tmp_path / "x.db"
    run(db, "ingest", str(data42 / "drop_1"), str(data42 / "drop_2"))
    run(db, "report", "--out", str(tmp_path / "r"))
    daily = list(csv.DictReader(open(tmp_path / "r" / "daily_overview.csv")))
    assert daily[0]["connectivity_rate"] == "0.5625"
    leads = list(csv.DictReader(open(tmp_path / "r" / "lead_report.csv")))
    assert leads[0]["last_call_at"] == "2026-10-14T17:55:24+05:30"
    dq = json.loads((tmp_path / "r" / "data_quality.json").read_text())
    assert dq["events_total"] == 8745 and dq["malformed_events"] == 8


def test_changed_file_is_refused(data7, tmp_path):
    drop = tmp_path / "drop"
    shutil.copytree(data7 / "drop_1", drop)
    db = tmp_path / "x.db"
    run(db, "ingest", str(drop))
    with open(next((drop / "events").glob("*.jsonl")), "a") as fh:
        fh.write('{"event_id": "evt_new"}\n')
    assert main(["--db", str(db), "ingest", str(drop)]) == 2


def test_no_pii_in_plaintext(data42, tmp_path):
    db = tmp_path / "x.db"
    run(db, "ingest", str(data42 / "drop_1"))
    first = next(csv.DictReader(open(data42 / "drop_1/leads/motor_renewal/batch_001.csv")))
    name = first["customer_name"]
    digits = "".join(c for c in first["phone"] if c.isdigit())[-10:]
    blob = db.read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("x.db*") if p.suffix != ".key")
    assert name.encode() not in blob
    assert digits.encode() not in blob


def test_ledger_is_append_only(data7, tmp_path):
    db = tmp_path / "x.db"
    run(db, "ingest", str(data7 / "drop_1"))
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE events SET event_type='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM events")
