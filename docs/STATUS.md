# Initial execution — 2026-09-16

Implemented dataset preparation, pinned manifests, serial paired generation,
SQLite checkpointing, quota/unknown-outcome pauses, explicit audited retry/skip,
and sanitized aggregate reporting. Eleven offline tests passed on both Python
3.9.6 and 3.14.4. Tests cover restart safety, no duplicate completed requests,
quota stops, timeouts, changed datasets, answer isolation, image payloads, and
exclusive run locks.

A separate synthetic run made four successful live gateway requests: arithmetic
and PNG image identification on each route. Both routes answered correctly.
Latency was 1.19–1.24 seconds per request for these tiny examples; this is not an
HLE runtime estimate. Cloud omitted the requested Explanation/Answer/Confidence
format; Cloud Pro followed it. The reference judge's missing-confidence default
must be disclosed when reporting calibration. Synthetic accuracy is not HLE
accuracy. See reports/synthetic-smoke.json for aggregate transport evidence.

The project virtual environment uses Homebrew Python 3.14.4 and includes HF CLI
1.31.0 and PyArrow 23.0.1. requirements-lock.txt records the installed dependency
versions; install with `uv pip install -r requirements-lock.txt` to reproduce.
The standard library runner also works without these optional data dependencies.

Blocked dependencies:

1. Refresh Hugging Face authentication and accept cais/hle access. Existing cached
   credential returned 401. No HLE dataset content has been downloaded.
2. Supply the judge LiteLLM endpoint, alias, protected credential-file path, and
   spending cap. Recommended baseline is the reference `o3-mini-2025-01-31` judge.
   No grading requests have been sent; the judge adapter remains a planned phase.

No automatic quota reset or scheduled resume has been configured. Generation can
begin as soon as dataset access is restored, independently of judge setup.
