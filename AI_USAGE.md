# AI Usage Disclosure

> DRAFT: the parts marked _TODO_ must be written by me, in my own words, before submitting.

## Tools used
Claude (Anthropic), used as a coding agent.

## What I used them for
- Claude wrote the first version of the whole solution: the SQLite schema, the ingest and
  report code, the PII approach (HMAC blind index + Fernet), the tests, and the first draft of
  `SUBMISSION.md`.
- Running the generator on seeds 42 and 7 and on the large dataset, and timing it.

## What I wrote or decided myself
- Chose Assignment A over B.
- _TODO: list what you reviewed, changed or decided yourself (e.g. decisions in the
  SUBMISSION.md table you agree or disagree with, anything you rewrote)._

## Where the AI was wrong or unhelpful
- _TODO: at least one real example from your own review._
- One from the build itself: the first PII check flagged ~369 "10-digit phone numbers" in the
  database. Looking closer, they were digit runs inside HMAC hex digests, not phones. The
  final test checks for a specific known name and phone in the raw DB bytes instead.

## How I verified AI-generated code
- 75 pytest tests (`python -m pytest solution`), including end-to-end runs of the provided
  generator.
- Checked the output against the README's example values for seed 42 (`0.5625` connectivity,
  `2026-10-14T17:55:24+05:30` last call); both match.
- Cross-checked event and lead-row counts with a separate throwaway script.
- _TODO: describe your own line-by-line read-through and any manual checks you ran._
