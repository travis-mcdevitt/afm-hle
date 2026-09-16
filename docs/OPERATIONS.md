# Running and resuming

The default gateway address is http://127.0.0.1:1979. Run on the Mac hosting the
gateway while logged into its GUI session. The token is read at request time,
never written into configuration or output. Generation must go through the
gateway, not directly to Hollis. Keep `.private/` on encrypted local storage;
back it up while the runner is stopped (or use SQLite's backup API).

`afm-hle run --max-calls 10` permits at most ten new attempts in this invocation.
Repeat with the same data and DB to continue pending work. `report` is aggregate
only. Exit code 2 means paused/blocked, 1 means a setup failure, 0 means the batch
completed or its call cap was reached (not necessarily the entire benchmark).

When paused, inspect local item IDs and statuses without publishing them:

```sh
sqlite3 .private/run.sqlite3 "SELECT qid,model,status FROM items WHERE status NOT IN ('done','pending');"
```

An explicit provider quota limit creates a persistent gateway hold. There is no
known automatic expiration. After choosing to test recovery, clear the applicable
hold with the gateway's existing control:

```sh
python3 ../afm-gateway/gateway.py resume --model afm-cloud
```

Then explicitly authorize one item to retry:

```sh
afm-hle resolve --id QUESTION_ID --model afm-cloud --action retry
afm-hle run --max-calls 1
```

This does not assert that Apple has recovered. Another 429 pauses again. Both
routes stop because their underlying quota relationship is unknown. Generic 429
busy responses also stop, but do not imply a provider quota/reset time.

A timeout, missing correlation ID, invalid response, HTTP 5xx, or interrupted
in-flight request is `unknown`. Correlate the attempt's gateway request ID with
the private gateway ledger if present. Do not replay it automatically: it may
already have consumed quota. `resolve --action retry` records an explicit retry;
`--action skip` leaves a coverage gap. Neither deletes the attempt history.
A crash between dispatch and saving the request ID can remain irreconcilable.
Exactly-once execution at Apple is not available without provider idempotency.

Only one process may own a run DB. The gateway additionally enforces one active
upstream call across clients. Never edit the dataset or protocol under an existing
run: use a new DB for any changed snapshot or prompt.

CI runs only synthetic offline tests. It never obtains the Apple token, accesses
HLE, or runs inference. Review `git diff --cached` before pushing; never force-add
`.private/`, token files, SQLite databases, or raw model output.
