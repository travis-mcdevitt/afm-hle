# iPad HLE queue and Mac controls

The Pro worker now owns a bounded batch from the existing frozen HLE sample.
The original campaign SQLite database records reservations atomically, under
its existing generation lock. Mac answers and interrupted attempts are not
replaced. iPad responses and grades remain a separate device stratum.

## Operate

Open **http://127.0.0.1:1983/** on the Mac. The detached service provides:

- **Resume queue / Pause queue**: enable or disable the next iPad claim. An
  already-running Apple call cannot be cancelled from this page; its result can
  still be uploaded while paused.
- **Queue next 7**: reserve untouched questions in frozen order. Existing Mac
  attempts, including uncertain calls, are not transferred. Allocation stops
  at the first untouched image question, without skipping it or redefining the
  sample. The initial allocation is sample positions 194–200; position 193 is
  the preserved uncertain Mac attempt and position 201 requires an image.
- **unknown / rate_limited**: record a manually observed failure and pause.
  Shortcuts action errors cannot always report themselves, so an aborted call
  can remain `inflight` until the operator resolves it.
- **retry**: after stopping the iPad shortcut and acknowledging possible quota
  consumption, preserve the previous attempt as `superseded` and create a new
  ticket for the same question. Resume explicitly afterward. Never retry
  generation merely because an answer upload failed.
- **skip**: explicitly retain a coverage gap, preserving its attempt history.
- **cancel**: release only an unclaimed reservation back to pending.
- **Upload receipt**: choose a saved `afm-ipad-<ticket>.txt` receipt on the Mac
  to retry its upload. This makes no model call. Identical uploads are idempotent;
  conflicting or superseded tickets are rejected instead of misattributed.
- **Grade saved answers / Retry grade**: grading runs separately and failures
  remain recorded until explicitly retried. Generation does not wait for grades.

Run **AFM iPad HLE Pro** on the iPad. It repeats at most seven times, pulling
one ticket, extracting the prompt, invoking **AFM Bridge - Cloud Pro**, encoding
its answer, saving a uniquely named receipt in the Shortcuts folder, and then
uploading that receipt. The save action precedes the upload. Keep Shortcuts in
the foreground initially; unattended background execution is not verified.
First-run iPad permission prompts may require local interaction.

**AFM iPad Retry Upload** selects a saved receipt and uploads it without invoking
Apple. Receipts may be available on the Mac through the Shortcuts iCloud folder.
Keep the file when an upload fails. Do not create a replacement generation until
the old worker has stopped and its receipt has been checked.

The Mac cannot remotely launch iPad Shortcuts. Resume arms the server-side queue;
if the iPad shortcut has already stopped, start it again on the iPad. A paused,
empty, or unresolved queue makes the claim SSH action exit with an explanatory
error before any model action runs. This is a safe stop, not a new model failure.

## Data, provenance, and accounting

`afm_hle.ipad_campaign` maintains `ipad_jobs`, `ipad_controls`, and `ipad_events`
in the original generation DB. Original `attempts` rows stay immutable. Reserved
items have `device_reserved`, accepted answers `device_done`, and explicit gaps
`device_skipped`; the existing Mac runner does not dispatch these statuses.

Each generation ticket records sample ordinal, attempt number, input-only chat
payload and hash, exact rendered prompt and hash, timestamps, original base64
receipt bytes, decoded answer and conversion label. Text rendering matches the
installed Hollis `chat.RenderTranscript` system/user framing. The original RTF
upload is retained when `textutil` extracts text. No reference answers are sent
to the iPad. Image transport is still unqualified and cannot silently fall back
to text-only prompting.

The private `ipad-protocol.json` records device/OS and installed action hashes.
`ipad-judging.sqlite3` stores a generation-disabled snapshot and separate grader
history. The detached service grades new saved answers through the existing
configured Gemini route. No automatic generation retry or quota probing occurs.
The webpage refresh and local checkpoint scans consume no AI observation tokens.

Metadata is mirrored to the existing gateway ledger's **`worker_calls`** side
table with stable device ID and ticket. No question/answer text is mirrored.
The existing `/monitor` endpoint remains explicitly Mac-only; it does not
include this new table, and Mac `calls` and `holds` are unchanged. The iPad page
reports gateway-mirror status. Mirroring is retried independently of inference.
Tokens, reasoning tokens, and TTFT remain null. Round-trip duration includes SSH,
Shortcuts, model execution, local saving, and upload; it is not pure inference
or reasoning time. A claimed or superseded job is not proof Apple ran it.

The control service binds only `127.0.0.1:1983`, checks Host and Origin, and
requires a per-process control token. The enrolled SSH key's forced command
recognizes only the fixed claim/submit commands; it never evaluates question,
answer, filename, or caller-provided shell code. Raw prompts, answers, receipts,
keys, generated shortcuts with connection details, and databases stay ignored.

## CLI and process recovery

From the repository root:

```sh
.venv/bin/python -m afm_hle.ipad_campaign status
.venv/bin/python -m afm_hle.ipad_campaign enqueue --count 7
.venv/bin/python -m afm_hle.ipad_campaign resume
.venv/bin/python -m afm_hle.ipad_campaign pause
.venv/bin/python -m afm_hle.ipad_campaign retry --ticket TICKET --acknowledge-uncertain
.venv/bin/python -m afm_hle.ipad_campaign submit < RECEIPT.txt
.venv/bin/python -m afm_hle.ipad_campaign start
```

`start` launches a native detached service and grader, with process metadata and
logs in the private campaign directory. Restarting it preserves all queue state
and never converts in-flight work back to pending. The generation retry command
is explicit authorization for a new attempt; the old ticket is never reused.

The public generator `scripts/build-ipad-shortcuts.py` accepts local `--host`,
`--user`, and `--outdir` arguments. Generate into `.private/`, sign with Apple's
`shortcuts sign --mode people-who-know-me`, and import in Shortcuts. No SSH private
key is embedded. Review the native editor after import. The deployed shortcuts
were inspected on the Mac; the first real iPad HLE round trip remains the live
transport validation.
