# Submission

## Candidate
- **Name:** Arnav Shyam
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** ~1.5h, built with heavy AI assistance (see `AI_USAGE.md`)

## How to run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python solution/ledger_cli.py ingest data/drop_1
python solution/ledger_cli.py ingest data/drop_2
python solution/ledger_cli.py report --out reports/

python -m pytest solution
```
`--db PATH` picks the SQLite file (default `./ledger.db`). Set `LEDGER_SECRET` to supply the PII
key; otherwise one is generated into `ledger.db.key` (git-ignored) on first run.

## Design

**Shape:** `ingest` validates and stores raw facts; `report` derives everything else from
them. Linking events to leads, orphan status, effective dispositions and buckets are **not
stored**: they are recomputed from the ledger every time a report runs. This is what makes
"an orphan after drop_1 counts once its lead arrives in drop_2" automatic, and it is why
drop-by-drop and all-at-once ingestion give byte-identical reports (tested).

The business rules sit in two pure modules (`normalize.py`, `derive.py`) with no SQL or I/O,
so the parts most likely to break are unit-tested directly.

**Database (SQLite):**

| Table | Purpose | Key / constraints |
|---|---|---|
| `source_files` | Every file ever ingested, with its SHA-256 and the event line counters (total / malformed / duplicates) | `UNIQUE(kind, workflow_id, file_name)`: the idempotency guard |
| `leads` | Current merged state of each lead | `PK(workflow_id, phone_hmac)` = the spec's lead identity |
| `lead_rows` | Outcome of every non-blank lead row: `created` / `merged` / `rejected` + reason + warning count | `PK(file_id, row_number)`; feeds `rejections.csv` and the lead counters |
| `events` | **The interaction ledger**, one row per distinct `event_id` | `PK(event_id)` dedupes; `BEFORE UPDATE/DELETE` triggers abort, so the ledger is append-only at the DB level |

Overrides are just more rows in `events`. The original `disposition.set` is never touched;
"effective disposition" is computed when reading.

**Idempotency / atomicity:** each drop is ingested inside one `BEGIN IMMEDIATE … COMMIT`. A
file already in `source_files` with the same hash is skipped, so re-running a drop changes
nothing. A crash mid-drop rolls back the whole drop, so a retry starts clean. If a file with
the same name arrives with **different** content, ingest refuses with an error rather than
silently double-counting or ignoring it.

**PII at rest:**
- Phones are stored as **HMAC-SHA256(secret, canonical phone)**, a keyed blind index. Events
  and leads are joined by equality on this value, so matching works without plaintext. A plain
  SHA-256 would not be enough: the space of Indian mobiles (~4×10⁹) can be brute-forced in
  minutes, but the keyed HMAC cannot be reversed without the secret.
- Full PII fields (`"pii": true`: name, phone) are **Fernet-encrypted** (AES + HMAC) in
  `leads.pii_enc`, so they can still be recovered by a service holding the key (e.g. the
  dialler). The raw phone on each event is encrypted too.
- Only the last 4 phone digits are kept in clear, which `phone_masked` needs.
- The test suite greps the database bytes for a known name and phone to prove neither is in
  plaintext.

**Incremental processing (stretch):** ingestion is truly incremental: drop 2 never re-reads
or re-validates drop 1's files; lead merges are applied in place. Reporting is a full
recompute over the DB. That is the trade-off: it is simple and always correct for late leads
and late overrides, and at 600k events it takes ~10 s. At larger scale I would keep a
materialised `call` table and recompute only calls whose events or leads changed in the
latest drop.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Phone that is present but invalid always rejects with `invalid_phone`, even if a schema someday marks phone optional | Phone is the lead identity; a lead without one can't be keyed or called | Treat like other optional fields (null + warning). Not reachable with the current schemas |
| A required non-phone field that is present but invalid → `invalid:<field>` | Spec lists no reason for it; can't happen with current schemas (required fields are string/phone) | Treat as missing |
| Two columns mapping to the same field: the first column wins | Deterministic; spec is silent | Last column wins |
| Missing/empty optional value is **not** a warning; only present-but-invalid is | Matches the "optional field present but invalid" wording | — |
| Defaults (`preferred_language → en`) are not baked into storage; the merged value stays null and the default applies when reading | A later duplicate row with a real value must still be able to win; storing the default would look like a real value | Store the default at merge time (equivalent here, but less honest) |
| A merged duplicate can change `external_ref` (the reference field follows last-non-null-write-wins like every other field); `batch_id` never changes | Spec says later rows overwrite non-null fields and only pins `batch_id` | Freeze the reference at first sight |
| Decimal rounding is half-up | Usual for money | Python's default banker's rounding |
| `D/M/YYYY` without zero padding is accepted | Same format, just unpadded | Strict two digits |
| An event whose `occurred_at` can't be parsed counts as **malformed** | It can't be placed on a date or ordered; never happens in the generator | Keep it and treat it as orphan |
| Same `event_id` seen again with different content: the first copy wins and the later one counts as a duplicate | Spec defines duplicates by `event_id` only | Flag it as a conflict |
| Overrides link via `call_id` to the lead of the call's phone-bearing events (`call.initiated` preferred), and must be in the same workflow | Spec: "the lead of that call's other events" | — |
| Override whose call has no linked events → orphan | Can't be attributed | — |
| Unknown-disposition events are counted only if they are linked (not orphans) | Spec processing order: orphan check comes before the unknown-disposition check | — |
| Ties in "latest" override/set (same `occurred_at`) break on `event_id` | Deterministic output | Arrival order: not stable across drops |
| A `call.connected`/`telephony_failed` event whose `call.initiated` is missing: the call is not counted at all | Spec: a call counts only if its `call.initiated` is present | — |
| `rejections_by_reason` lists only reasons that occurred, sorted by key | Avoids inventing zero keys | Every possible key with 0 |
| Batches with zero valid leads don't appear in `batch_summary.csv` | The batch only exists through its leads | Emit a row of zeros |
| Re-ingesting a changed file with the same name is an error | Silent skip or silent double-count would both corrupt reports | Treat it as a new version and re-derive |

## Failure modes considered
- **Re-running a drop:** skipped by file hash; reports unchanged (tested).
- **Crash mid-ingest:** one transaction per drop, so nothing is partially applied.
- **Late leads / late overrides:** linking is derived at report time (tested with seed 42:
  leads first seen in drop 2 pick up calls from drop 1).
- **Duplicate events across drops:** `event_id` primary key; counted once per extra copy.
- **Truncated / non-JSON lines, JSON that isn't an object:** malformed.
- **BOM in CSV, short rows, extra columns, renamed/reordered headers, blank rows:** handled.
- **Someone tries to edit the ledger:** DB triggers abort `UPDATE`/`DELETE` on `events` (tested).
- **Not handled:** concurrent `ingest` processes beyond SQLite's write lock (the second one
  waits/fails cleanly, nothing interleaves); key rotation for the PII secret; losing the key
  file means encrypted PII can't be recovered and new HMACs won't match old ones.

## Testing
`python -m pytest solution`: 75 tests, ~5 s.
- **Unit:** phone (all 6 generator formats + invalid shapes), dates (4 formats, case-
  insensitive months, 31/02, leap years), decimals (₹ / Rs / Rs. / INR, commas, negatives),
  enum, header normalisation, timestamps (Z / offset / naive=IST, IST date crossing midnight),
  row rejection order and warning rules, effective disposition (override beats a later set,
  latest by `occurred_at` not by arrival, unknown codes ignored), bucket precedence, linking
  (override via `call_id`, orphans, workflow is part of identity).
- **End-to-end** (runs the provided generator): incremental = all-at-once = re-ingested
  output; orphan-then-linked; the README's example values for seed 42 (`0.5625`,
  `2026-10-14T17:55:24+05:30`); changed file refused; no plaintext PII; append-only triggers.
- **Independent reference check (manual, not in the repo):** a separate throwaway implementation of the spec
  (in-memory, no DB, sharing no code) produced byte-identical output for all 5 files on seeds
  42, 1, 7, 123, 2026 and 99999 (small) and seed 5 (large, ~133k leads).
- **Real crash (manual):** `kill -9` during the large drop_2 ingest left drop_1 intact and nothing from
  drop_2; re-running drop_2 produced reports identical to the reference.
- Also checked by hand: a dirty CSV (BOM, messy headers, CRLF, short/blank rows, merge +
  warnings) and events arriving before their lead; all behaved as specified.
- Ran the commands and test suite on Windows (Python 3.14) as well as Linux.
- **Not tested:** that the reports match your expected files exactly (I don't have them),
  beyond the README examples, which match.

## Results
Seed 42, small: 2,057 lead rows (108 rejected, 57 merged, 122 warnings); 8,745 event lines
(8 malformed, 237 duplicates, 462 orphans, 18 unknown dispositions). Ingest + report < 1 s.

Seed 42, **large** (~144k lead rows, ~633k event lines), on a 2-vCPU cloud VM:
- `ingest drop_1 drop_2`: **48 s**
- `report`: **10 s**

I haven't profiled it. The likely biggest costs are Fernet-encrypting every event's raw phone
and the per-row lead upsert; profiling those is the first step if it ever needs to be faster.

Surprising in the data: generated duplicate lead rows sometimes change the premium or the
language, so "last non-null write wins" really does change stored values; some unknown
disposition codes (`RENEWD`, `call_back`) are near-misses of real codes but are still
treated as unknown, per the spec.

## If I had another day
1. Move report aggregation into SQL (a `calls` view + window functions) and materialise
   per-call state so reports are incremental too.
2. Key management: key id stored with each ciphertext, rotation command, KMS instead of a
   key file.
3. A `quarantine` view/report listing orphan event ids by reason, for ops to chase.
4. Property-based tests (Hypothesis) for phone/date/decimal parsing.
5. Production at 100k leads/day: run `ingest` per drop from an object-store trigger or
   scheduler (Airflow/cron) with the per-drop transaction as the retry unit; Postgres instead
   of SQLite; backfills = re-ingest into a fresh DB, since reports are a pure function of the
   ledger; alerting on spikes in orphan, malformed or rejection rates.