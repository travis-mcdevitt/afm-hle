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

## Judge execution

Store `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY` in `.env` at the
project root. It is ignored by Git. Do not pass the key on the command line.
The base URL should include the API prefix (typically `/v1`).

```sh
.venv/bin/python -m afm_hle.judge --no-budget-cap --input-rate 1.1 --output-rate 4.4 --max-calls 10
```

Repeat to grade only newly completed, ungraded generations. Generation and judging
share the database lock and run sequentially. Judge errors pause grading without
replaying the request. Inspect failed/unknown grades privately. After review, add `--retry-attempt ID`
to explicitly authorize a retry of that generation-attempt ID; the original
grade attempt is archived, and its known costs remain in accounting. A timeout
may already have incurred cost. Completed grades cannot be retried. Generation
can continue independently of failed grading.

For a future budgeted evaluation, use `--budget-usd AMOUNT` instead of
`--no-budget-cap`. This reserves a conservative input-byte/output-token estimate
before each call and halts if any prior cost is unknown. It is a local guard at
the supplied prices, not a billing limit enforced by the provider. For a contractual
spend ceiling, also configure a hard limit on the proxy key. Pricing and budget
are immutable within a grading run.

The `migrate-images` command upgrades early v1 runs only if the dataset is unchanged,
no judge configuration exists, and every already dispatched image payload remains
identical. It records the original manifest in the private event log.

## Comparing an additional judge

Keep `.env` unchanged and specify the new route with `--model`. Create an isolated
judge snapshot once; this copies generation records but not reference grades and
blocks new Apple generations in the copy. It refuses to overwrite an existing DB.

```sh
.venv/bin/python -m afm_hle.judge --model nous-glm-5.3-flash \
  --snapshot-from .private/run.sqlite3 --db .private/glm-flash.sqlite3 \
  --no-budget-cap --input-rate 0.06 --output-rate 0.20 --max-calls 50
```

To resume, omit `--snapshot-from`; keep all other judge settings unchanged.
To compare the saved grades:

```sh
.venv/bin/python -m afm_hle.compare .private/run.sqlite3 .private/glm-flash.sqlite3 \
  > reports/judge-comparison.json
```

Comparison requires identical generation manifests and saved responses, plus the
same grading prompt, schema, and output ceiling. It reports each judge separately,
correctness disagreements, positive agreement, and confidence differences. High
agreement on a mostly incorrect sample does not establish judge quality. If the
source generation run grows, preserve a reference snapshot of the original cohort
before comparing; the comparator intentionally rejects different response sets.

## Gemini schema compatibility

The Nous Gemini route accepted a minimal JSON schema but returned empty objects
for the reference schema's boolean `enum: [true]`. A synthetic check succeeded
when that API-only enum was removed. The `portable-boolean` profile makes exactly
that change; local validation still requires `strict is True` and all other
reference fields. It does not change the judge prompt or correctness rubric.
The profile and schema hash are frozen in the new judge database.

```sh
.venv/bin/python -m afm_hle.judge --model nous-gemini-3.7-flash \
  --schema-profile portable-boolean \
  --snapshot-from .private/run.sqlite3 --db .private/gemini-flash.sqlite3 \
  --no-budget-cap --input-rate 0.75 --output-rate 3.75 --max-calls 50
.venv/bin/python -m afm_hle.compare .private/run.sqlite3 .private/gemini-flash.sqlite3 \
  --allow-portable-boolean > reports/gemini-comparison.json
```

Omit `--snapshot-from` when resuming. Comparison rejects different schema hashes
by default. The opt-in above permits only the two known schema variants and
explicitly labels the difference in the report; other schema differences still
fail. No existing reference or GLM grades are modified.

## Detached Cloud Pro campaign

The existing campaign is already prepared under `.private/campaign/` with a
frozen 400-question Pro-only plan. Run commands from the repository root:

```sh
.venv/bin/python -m afm_hle.campaign start
.venv/bin/python -m afm_hle.campaign status
tail -f .private/campaign/worker.log
```

Open [the live dashboard](http://127.0.0.1:1981) on the gateway Mac. Its
`/api/status` endpoint exposes aggregate JSON. It never exposes credentials,
questions, reference answers, or generated answer text. Logs/checkpoints remain
private. No Codex session is required after launch.

```sh
.venv/bin/python -m afm_hle.campaign stop
```

Stop waits for the current batch to checkpoint (up to ten requests); it avoids
cancelling an in-flight provider call. Wait for the process to exit before
restarting. On a clean stop, `start` resumes pending work. Do not rerun `prepare`
or change the frozen plan to resume. A duplicate worker is rejected by a lock.

On a quota or ambiguous failure, the dashboard remains up in `paused` state.
Stop the worker, inspect `.private/campaign/generation.sqlite3` and the gateway
ledger, and follow the explicit resolution procedure above using this campaign
DB and `--model afm-cloud-pro`. Only clear a gateway hold when ready to test
recovery; reset times are unknown. Then `start` resumes. A crash can leave an
`inflight` record: reconcile it before retrying because it may have consumed
quota. Judge failures similarly need explicit review/retry in
`.private/campaign/judging.sqlite3` with the frozen Gemini settings. No automatic
cooldown polling or unbounded retries are performed.

### Independent grading failures

Judge timeouts, invalid grades, and other grading failures now defer further
judging without pausing Apple generation. The dashboard shows failures flagged
for retry and the total backlog. Failed grades remain unchanged in the private
judging database, including raw responses and cost; they are not scored as wrong
or silently retried. New answers continue to be copied into the judging snapshot.
The final phase is `generation_complete_grading_pending` until grading finishes.
Apple generation errors and quota holds still pause generation.

For an explicit future grading retry, stop the worker and use the judge command
with the campaign judging database, frozen Gemini settings, and
`--retry-attempt ID` as described above. After resolving all failed grades, remove
`.private/campaign/grading-deferred.json` if present and restart the worker to
finish the backlog. Do not change the sample plan or delete grade records.

### Detached backlog grading and Cloud-only continuation

The Cloud-only continuation uses `.private/campaign-cloud/`, the same frozen
400 question IDs, and port 1982. It reuses 25 Cloud pilot answers. Pro generation
remains paused; its saved-answer grading runs in a separate detached process.
The Pro dashboard phase describes generation, so it can say paused while the
graded count rises. Logs are `.private/campaign/grading.log`.

`afm_hle.judge --continue-on-error` skips existing failed grades and attempts each
previously ungraded answer once. New timeouts and invalid grades remain recorded
for explicit future retry, while the worker advances to other answers. A judge
HTTP 429 stops judging. The call limit still bounds execution, and budget guards
still apply. The normal judge CLI retains its stop-on-error default.

```sh
.venv/bin/python -m afm_hle.campaign status --directory .private/campaign-cloud
# Cloud dashboard: http://127.0.0.1:1982
# Pro generation / live grading counts: http://127.0.0.1:1981
```

To prepare a separate campaign, specify its directory, model, and unused port:
`campaign prepare --directory .private/NEW --models afm-cloud --port 1982`.
Never prepare over an existing campaign. `campaign start --directory ...` detaches
its worker. Concurrent workers must use distinct database directories and ports.

The dashboard at port 1981 now shows both Pro and standard Cloud campaigns,
with separate generation phase, grading status, counts, failures, and costs.
`/api/campaigns` returns both aggregates; `/api/status` retains its original
single-campaign response. Refresh an already-open page to load the new layout.

### Explicit coverage gaps

A user-authorized `resolve --action skip` preserves every attempt and records an
explicit coverage gap. The campaign proceeds to the next pending question in the
frozen sample without replacement. The dashboard displays gap counts. Exhausting
the sample with gaps produces `finished_with_gaps` when all available answers are
graded, rather than claiming a complete evaluation. Accuracy on graded answers
must be reported alongside coverage. On September 16, one Cloud item was deferred
after three 120-second Shortcuts timeouts, and Shortcuts was restarted before
continuing. All three attempts remain in the private ledger.
