"""Command line: `python solution/ledger_cli.py ingest <drop>... | report --out <dir>`."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .catalog import load_dispositions, load_schemas
from .crypto import Vault
from .db import connect
from .ingest import IngestError, ingest_drop
from .report import build_reports

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMAS = REPO_ROOT / "assignment-a-data" / "schemas"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ledger", description="Interaction ledger & reporting pipeline")
    ap.add_argument("--db", default="ledger.db", help="SQLite database path (default: ledger.db)")
    ap.add_argument("--schemas", default=str(DEFAULT_SCHEMAS),
                    help="directory with <workflow>.json schemas and dispositions.csv")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ing = sub.add_parser("ingest", help="ingest one or more drops, in the order given")
    p_ing.add_argument("drops", nargs="+")
    p_rep = sub.add_parser("report", help="write the five report files")
    p_rep.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    schema_dir = Path(args.schemas)
    schemas = load_schemas(schema_dir)
    catalog = load_dispositions(schema_dir / "dispositions.csv")
    db_path = Path(args.db)
    conn = connect(db_path)
    vault = Vault.for_db(db_path)

    t0 = time.perf_counter()
    try:
        if args.cmd == "ingest":
            for d in args.drops:
                stats = ingest_drop(conn, vault, schemas, Path(d))
                print(f"ingested {d}: {stats}")
        else:
            dq = build_reports(conn, schemas, catalog, Path(args.out))
            print(json.dumps(dq))
    except IngestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(f"done in {time.perf_counter() - t0:.2f}s", file=sys.stderr)
    return 0
