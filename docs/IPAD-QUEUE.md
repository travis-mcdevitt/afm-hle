# iPad HLE queue and Mac controls

The Pro worker now owns a bounded batch from the existing frozen HLE sample.
The original campaign SQLite database records reservations atomically, under
its existing generation lock. Mac answers and interrupted attempts are not
replaced. iPad responses and grades remain a separate device stratum.

## Operate

The batch shortcut repeatedly stopped after its first successful upload. Use the
**detached remote controller** instead: it remotely opens a one-question shortcut,
waits for the matching saved receipt, then launches the next question. It reserves
untouched questions in frozen order as needed and does not wait for grading.

With the paired iPad connected, unlocked, powered and Developer Mode enabled:

```sh
.venv/bin/python -m afm_hle.ipad_remote start --device DEVICE_IDENTIFIER
.venv/bin/python -m afm_hle.ipad_remote status
```

The device identifier and launch history are kept in `.private/ipad-remote/`.
The controller runs detached and checks local checkpoints every 15 seconds; it
requires no continuous AI observation. Stop the Mac generation supervisor before
starting it, so only one Apple route collects at a time. A launch intent is saved
before invoking CoreDevice. An unresolved launch or upload is never automatically
replayed: after ten minutes it stops with `requires_review`. Check the iPad screen
and saved receipts before authorizing a retry. Quota errors may require local
interaction; this worker does not infer a reset or retry a quota-denied question.

**AFM iPad HLE Pro One** handles text questions. **AFM iPad HLE Pro Image One**
handles image questions with a native Cloud Pro image attachment. On October 2,
**AFM iPad Image Check** correctly read a randomly generated code present only in
image pixels, completing the claim, inference and upload round trip. This private
qualification gates image allocation. Images use protocol
`ipad-ssh-ticketed-native-image-v2`; text remains `ipad-ssh-ticketed-text-v1`.
The original transcript is preserved with an explicit newline separator before
the native attachment. Existing normalized PNG/JPEG bytes are transmitted by
base64 and decoded on the iPad, without resizing, OCR or base64 text in the model
prompt. Payload hashes and each dispatched attempt remain recorded.

Image grades use `ipad-image-judging.sqlite3`, separate from the existing text
`ipad-judging.sqlite3`, so their frozen manifests are not rewritten. The same
frozen judge configuration grades both. Mac results remain a separate stratum.
Completing untouched iPad work is distinct from resolving preserved Mac unknown
attempts and coverage gaps; no existing uncertain Mac question is silently moved
to the iPad.

Open **http://127.0.0.1:1983/** on the Mac. The detached service provides:

- **Resume queue / Pause queue**: enable or disable the next iPad claim. An
  already-running Apple call cannot be cancelled from this page; its result can
  still be uploaded while paused.
- **Batch size / Queue batch**: choose 1–100 and reserve untouched questions in frozen order. Existing Mac
  attempts, including uncertain calls, are not transferred. Before image qualification, allocation stops
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

**AFM iPad Retry Upload** selects a saved receipt and uploads it without invoking
Apple. Receipts may be available on the Mac through the Shortcuts iCloud folder.
Keep the file when an upload fails. Do not create a replacement generation until
the old worker has stopped and its receipt has been checked.

The SSH setup alone cannot remotely launch iPad Shortcuts. Resume arms the server-side queue;
the paired CoreDevice controller supplies remote launches when configured. A paused,
empty, or unresolved queue makes the claim SSH action exit with an explanatory
error before any model action runs. This is a safe stop, not a new model failure.

Apple's `shortcuts://run-shortcut?name=...` links launch on the device that opens
the URL. On October 2, cable trust paired the iPad with CoreDevice; after the
operator enabled Developer Mode and restarted, `devicectl` remotely opened
Shortcuts and delivered the batch URL. A new queue claim at the matching launch
time verified actual shortcut execution. This establishes remote starting, not
unattended multi-question completion or operation while locked.

With the iPad paired, connected, unlocked and Developer Mode enabled:

```sh
xcrun devicectl list devices
xcrun devicectl device process launch --device DEVICE_IDENTIFIER \
  --payload-url 'shortcuts://run-shortcut?name=AFM%20iPad%20HLE%20Pro%20Batch' \
  --timeout 20 com.apple.shortcuts
```

Replace `DEVICE_IDENTIFIER` with the paired iPad identifier from the inventory.
Check the queue first: do not launch a second worker while an attempt is in flight
or unresolved. A successful launch command is not proof of a saved answer;
verify the claim and upload events on the dashboard. Keep personal identifiers
in private local state, not this public document.

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
to the iPad. Image questions require the qualified native image worker and cannot
silently fall back to text-only prompting.

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
were inspected on the Mac and live text round trips were verified on the iPad.
The separate image qualification is recorded privately.

### SSH handoff timing

The claim command allows up to 15 seconds for an existing `inflight` upload to
commit before claiming the next question. It releases the database lock between
checks, records `next_claim_waiting_for_upload`, and does not replay inference.
Unknown, failed, quota-limited, or paused states still stop immediately. If no
upload arrives within the bound, the original attempt remains unresolved.
This addresses observed next-claim errors followed shortly by successful uploads;
the exact Shortcuts scheduling behavior remains unverified.

### Batch ceiling and loop acknowledgement

Seven was an initial qualification batch, not an Apple limit. The deployed
shortcut now has a 100-iteration ceiling; the Mac allocator accepts 1–100 per
batch and still stops before unqualified image input. Each iteration consumes
the upload's `status` in an If action before End Repeat, and stops if the
acknowledgement is absent. SSH errors also abort the loop. The conditional's
native macOS 27 variable wrapper and condition were verified in the editor.
Live batch trials stopped after one upload; the detached one-question controller
is now the collection path.

The observed Mac Pro rule was 100 requests in 86,400 seconds. This does not prove
an iPad daily allowance or a midnight reset. Qualification calls and failed or
uncertain model attempts may consume capacity; a 100-iteration ceiling does not
promise 100 completed answers. A quota interruption retains the in-flight ticket
for explicit resolution. Do not automatically retry it.
