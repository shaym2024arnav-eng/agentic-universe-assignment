# AI Usage Disclosure

## Tools used
Claude (Anthropic), used as a coding agent.

## What I used them for
- Claude wrote the first version of the whole solution: the SQLite schema, the ingest and
  report code, the PII approach (HMAC blind index + Fernet), the tests, and the first draft of
  `SUBMISSION.md`.
- Running the generator on several seeds and on the large dataset, and timing it.
- Step-by-step help setting up the GitHub repository and pushing.

## What I wrote or decided myself
- Chose Assignment A (data) over B (workflow): it is closer to my QA/test-automation
  background, the rules are deterministic, and I can verify the output exactly.
- Asked for a separate testing pass before pushing (an independent reference check, dirty
  input, crash recovery) instead of submitting the first working version.
- Ran the pipeline and the full test suite on my own Windows machine (Python 3.14).
- Reviewed and filled in `SUBMISSION.md` and this file.

## Where the AI was wrong or unhelpful
- The first PII check flagged ~369 "10-digit phone numbers" in the database. They turned out
  to be digit runs inside HMAC hex digests, not phones. The final test checks for a specific
  known name and phone in the raw DB bytes instead.
- Its first run instructions gave several commands in one block. Pasted into PowerShell, they
  were treated as one unfinished multi-line command (`>>` prompts) and the ingest/report steps
  printed nothing. I noticed the missing output and ran the commands one at a time instead.

## How I verified AI-generated code
- Ran all the README commands and `python -m pytest solution` locally on Windows; all tests
  passed.
- Checked the output against the README's example values for seed 42 (`0.5625` connectivity,
  `2026-10-14T17:55:24+05:30` last call); both match.
- Had all 5 output files diffed against a separate, independently written reference
  implementation on 7 seeds (one at large scale): identical.
- Had the process killed with `kill -9` mid-ingest and confirmed a re-run recovers to
  identical output.
- Cloned the pushed GitHub repo fresh and re-ran it to confirm it works from a clean clone.