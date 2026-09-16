# Evaluation protocol v2

Use the pinned `cais/hle` test split, all modalities, no tools or retrieval, and
one delivered answer per model/question. Each item gets a new request without
conversation history. Reference answers never enter generation requests.

Questions are ordered by SHA256 of `afm-hle-v1:` followed by their ID. Both routes
see the same order; Cloud goes first on each item, then Pro. This deterministic
order reduces subject-order effects in interrupted prefixes, but partial results
remain partial. A manifest hashes the full dataset snapshot and system prompt,
records the revision and route IDs, and prevents resume with changed inputs.
The prompt follows the upstream Explanation / Answer / Confidence convention.

PNG/JPEG images are passed unchanged. Five static WebP and three static GIF images
are decoded and losslessly re-encoded as RGBA PNG, with no resizing; pixel equality
was verified for all eight conversions. Animated images and oversized requests
block for review. The image policy is included in the run manifest. The first
eight pilot answers used unchanged payloads; an audited v1-to-v2 migration retained
those responses and enabled conversion before the first unsupported image call.
Actual context acceptance depends on Shortcuts. Requests cannot set temperature or output length; record these as
uncontrolled, and label comparisons to standard API benchmark runs accordingly.
The gateway response model identifies a route, not a verifiable Apple snapshot.

A successful response is retained even if its answer is wrong or refuses the
question. A transport failure is not an incorrect answer. HTTP failures, unknown
outcomes, skipped inputs, pending items, and delivered responses stay distinct.
No new answer may be cherry-picked over a completed response. Explicit retries of
unknown attempts remain in the audit history because Apple may have executed them.

The reference HLE judge currently defaults to `o3-mini-2025-01-31` and extracts
correctness plus confidence using structured output. The judge is configured
through a local LiteLLM route. Its prompt and schema match upstream revision `73ae974b1844c3ffa64c3f4343d9f1f259575700`; the schema additionally
rejects parsed confidence outside 0–100. The reference prompt defaults missing
confidence to 100, which can distort calibration if a generation omitted it.
Judge configuration, dataset hash, pricing, and output limit are pinned separately.
Grading is checkpointed without client retries; proxy-side behavior depends on
the local LiteLLM configuration. Private records retain the returned model name,
raw grading response, usage, estimated cost, and proxy-reported cost where available.
The owner requested no spending cap. Progress reports show accuracy only over
judged responses, with Wilson intervals; top-level full-run accuracy remains null
until every question has been graded. Reports include matched-question
comparisons for incomplete runs.
Missing judgments must not be silently treated as wrong or dropped from a claimed
full-dataset score. Preserve raw grading privately for review.

The dataset authors request that the dataset not be publicly redistributed.
Publish only code, protocol, provenance, and aggregate results. Model responses
may reproduce questions/answers, so these are private too. Benchmark content
must never enter training corpora.
