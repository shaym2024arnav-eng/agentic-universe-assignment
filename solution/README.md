# Solution: Assignment A, Interaction Ledger & Reporting Pipeline

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python solution/ledger_cli.py ingest data/drop_1
python solution/ledger_cli.py ingest data/drop_2
python solution/ledger_cli.py report --out reports/

python -m pytest solution          # 75 tests, ~5s
```

Options: `--db PATH` (default `ledger.db` in the current directory), `--schemas DIR`
(default `assignment-a-data/schemas`). `ingest` accepts several drops and applies them in the
order given. The PII key is read from `LEDGER_SECRET`, or generated into `<db>.key` on first run.

Layout:

| Module | Responsibility |
|---|---|
| `ledger/normalize.py` | Pure value rules: phone, date, decimal, enum, header, timestamp |
| `ledger/catalog.py` | Loads workflow schemas and the disposition catalogue |
| `ledger/crypto.py` | HMAC blind index + Fernet encryption for PII |
| `ledger/db.py` | SQLite DDL, including append-only triggers on `events` |
| `ledger/ingest.py` | Lead validation/merge and event dedupe, one transaction per drop |
| `ledger/derive.py` | Pure derivation: event linking, effective disposition, buckets |
| `ledger/report.py` | Builds the five output files |
| `ledger/cli.py` | Command line |
