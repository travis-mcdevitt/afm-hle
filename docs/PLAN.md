# Feasibility and implementation plan

Assessment: 2026-09-16. Feasible as an interrupted, no-tools evaluation of the
accessible Apple Shortcuts routes. Not equivalent to a vendor-attested evaluation
of immutable model snapshots. A single account can run it serially over multiple
sessions without discarding completed work.

## Verified prerequisites and constraints

- macOS 27.0 (26A428); Hollis source commit
  `769dde534884724a67493bd5b9dc0df0763714e9`.
- Gateway discovery advertises both cloud routes; no holds at initial inspection.
- Both cloud routes previously passed actual PNG image checks through the gateway.
- Local gateway supports authenticated Chat Completions and request correlation.
  Direct access avoids an extra proxy hop and preserves X-AFM-Request-ID.
- Hollis serializes chat roles into a text transcript for Shortcuts. Temperature,
  seed, reasoning effort, and output token ceilings cannot be specified. Total
  JSON limit is 8 MiB, and Hollis also limits prompt text to 128 KiB.
- Cloud usage is unavailable. Quota scope/reset are unknown; no inferred cooldown.
- GitHub authentication works with network access; destination afm-hle did not
  exist at initial inspection.
- HLE metadata is public, revision
  `5a81a4c7271a2a2a312b9a690f0c2fde837e4c29`; data are gated. The cached local HF
  credential returned HTTP 401. Download is blocked pending refreshed access.
- Reference judge: `o3-mini-2025-01-31`. User will supply a LiteLLM route and budget.
  No paid judge requests are authorized or implemented yet.

## Phases

1. Implement private dataset preparation, immutable run manifests, a serial
   multimodal gateway client, durable checkpoints, explicit recovery, public
   aggregate reporting, and offline failure-path tests.
2. Publish code and documentation to travis-mcdevitt/afm-hle. Keep all benchmark
   text/images/answers and credentials out of Git history and CI artifacts.
3. Run a synthetic text/image smoke test of each cloud route in a separate DB.
   Synthetic checks are not HLE results and are never included in its denominator.
4. After HF access is restored, download the pinned revision; validate actual
   counts, image forms, sizes, and schema. Run a 10-call paired HLE pilot. Assess
   refusal, output truncation, latency, and unsupported examples before scaling.
5. Continue bounded batches, checkpointing every result; pause both routes on
   quota or uncertain execution. User chooses when to retry after cooling down.
   Keep the host awake and GUI session active during a batch. No scheduler or
   provider polling has been installed.
6. Once judge route and budget are supplied, add separately checkpointed judging
   with the upstream prompt/schema, fixed judge identity, no retries by default,
   maximum per-call token budget, usage/cost accounting, and a hard spend ceiling.
   Calibrate a pilot against manually reviewed examples before bulk judging.
7. Publish coverage, accuracy with uncertainty, refusal/truncation/error counts,
   matched-pair comparisons, and per-subject/image results. Mark partial runs
   clearly; incomplete generation or judging must not become a full HLE score.

## Scale and dependencies

Nominally 2,500 public questions × 2 models = 5,000 successful generations and
up to 5,000 judge calls, plus explicitly approved retries. Actual dataset count
comes from the pinned download. Data card reports approximately 274 MB of data;
allow additional room for cache, JSON with images, SQLite responses, and backups.
Runtime equals measured serial latency plus unknown cooldown delays. No local GPU
is required. Python, optional data libraries, HF access, gateway/Shortcuts, and
judge availability are the dependencies. Judge costs require current route pricing
and observed response sizes before a meaningful budget estimate can be made.

## Sources

- [Apple AFM 3 announcement](https://machinelearning.apple.com/research/introducing-third-generation-of-apple-foundation-models)
- [HLE dataset and access conditions](https://huggingface.co/datasets/cais/hle)
- [HLE reference evaluation](https://github.com/centerforaisafety/hle/tree/main/hle_eval)
- Local afm-gateway README/INTEGRATION and Hollis source inspection.
