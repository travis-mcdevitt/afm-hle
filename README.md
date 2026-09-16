# AFM × Humanity’s Last Exam

A public, resumable evaluation of Apple's **AFM 3 Cloud** and **AFM 3 Cloud Pro**
through the `afm-cloud` and `afm-cloud-pro` Shortcuts routes in `afm-gateway`.
Model family names describe the intended targets; Shortcuts does not attest the
underlying model build. HLE generation and independent judging are now running; pilot results are partial.

The runner operates on the Mac hosting the gateway, using its authenticated
loopback endpoint directly. Every generation passes through the gateway ledger.
It sends one request at a time, alternates the two routes, and saves progress to
SQLite after every answer. Completed items are not submitted again. A quota or
uncertain outcome stops the whole run; recovery is explicit, with attempt history
retained. No fallback, hidden retries, tools, or streaming.

## Status

Four live synthetic checks and 26 offline tests passed. Dataset authentication and
the independent judge route are configured; HLE pilot execution has begun. See [execution status](docs/STATUS.md).

See [plan and feasibility](docs/PLAN.md), [methodology](docs/METHODOLOGY.md), and
[operations](docs/OPERATIONS.md). Initial prerequisites: authenticated access to
`cais/hle`, an active Mac GUI session with the gateway/Shortcuts installed, and a
separately configured judge to produce accuracy metrics.

Generation uses only Python's standard library (Python 3.9+). Downloading the
pinned dataset needs `huggingface-hub`, `pyarrow`, and `pillow`; use Python 3.11+ for a fresh
environment and current dependency wheels. This is a macOS/Linux CLI (`fcntl`).

```sh
uv venv --python 3.11
uv pip install -e '.[data]'
# Authenticate with Hugging Face and accept cais/hle access first.
.venv/bin/afm-hle prepare --revision 5a81a4c7271a2a2a312b9a690f0c2fde837e4c29
.venv/bin/afm-hle run --max-calls 10
.venv/bin/python -m afm_hle.judge --no-budget-cap --input-rate 1.1 --output-rate 4.4 --max-calls 10
.venv/bin/afm-hle report > reports/progress.json
python3 -m unittest discover -s tests -v
```

Data, images, reference answers, generated answers, and checkpoints stay under
ignored `.private/`. Public reports contain aggregate progress, not question or
answer text. Credentials are loaded from protected files at runtime. The default
is `~/Library/Application Support/afm-gateway/gateway.token`.

Cloud token consumption, account-wide remaining quota, and reset times are
**unknown**, never zero. Request counts measure this runner's gateway requests,
not confirmed Apple inference counts. Other uses of the Apple account can share
its limits. No completion-date estimate is justified until quota behavior and
representative latency have been observed.

Judge connection settings are loaded from ignored `.env`: `OPENAI_BASE_URL`,
`OPENAI_MODEL`, and `OPENAI_API_KEY`. Values are never sourced as shell commands.
The configured reference judge is `o3-mini-2025-01-31`; its current local proxy
metadata lists $1.10/M input tokens and $4.40/M output tokens. Cost estimates use
returned usage; proxy-reported cost is recorded separately. This run has no spend
cap at the owner's request. Future runs may use `--budget-usd` instead.

An additional `nous-glm-5.3-flash` judge can grade an isolated snapshot of the same
responses with the same rubric. See [judge comparison operations](docs/OPERATIONS.md#comparing-an-additional-judge).
Reference grades remain intact, and no extra Apple requests are needed.

The initial Flash trial is paused on JSON-format failures after five valid grades.
See the [comparison findings](docs/JUDGE-COMPARISON.md); the reference baseline is unchanged.

The [Gemini trial](docs/GEMINI-TRIAL.md) completed all 50 saved answers with an
explicit schema compatibility profile: 49/50 correctness agreement with the
reference, and about $0.073 total known cost including synthetic checks.
The reference grader remains unchanged.
