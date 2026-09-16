# Evaluation protocol v1

Use the pinned `cais/hle` test split, all modalities, no tools or retrieval, and
one delivered answer per model/question. Each item gets a new request without
conversation history. Reference answers never enter generation requests.

Questions are ordered by SHA256 of `afm-hle-v1:` followed by their ID. Both routes
see the same order; Cloud goes first on each item, then Pro. This deterministic
order reduces subject-order effects in interrupted prefixes, but partial results
remain partial. A manifest hashes the full dataset snapshot and system prompt,
records the revision and route IDs, and prevents resume with changed inputs.
The prompt follows the upstream Explanation / Answer / Confidence convention.

Images are passed unchanged as inline PNG/JPEG data URLs. Unsupported formats or
oversized inputs block execution for review instead of being silently dropped,
resized, or converted into text-only examples. Actual context acceptance depends
on Shortcuts. Requests cannot set temperature or output length; record these as
uncontrolled, and label comparisons to standard API benchmark runs accordingly.
The gateway response model identifies a route, not a verifiable Apple snapshot.

A successful response is retained even if its answer is wrong or refuses the
question. A transport failure is not an incorrect answer. HTTP failures, unknown
outcomes, skipped inputs, pending items, and delivered responses stay distinct.
No new answer may be cherry-picked over a completed response. Explicit retries of
unknown attempts remain in the audit history because Apple may have executed them.

The reference HLE judge currently defaults to `o3-mini-2025-01-31` and extracts
correctness plus confidence using structured output. This repository has no judge
configured yet. Progress reports intentionally emit null accuracy. A future judge
must use the same dataset snapshot and separate credentials, pin its route/version
and prompt/schema, and enforce the agreed budget. Publish judged coverage and
confidence intervals, with matched-question comparisons for incomplete runs.
Missing judgments must not be silently treated as wrong or dropped from a claimed
full-dataset score. Preserve raw grading privately for review.

The dataset authors request that the dataset not be publicly redistributed.
Publish only code, protocol, provenance, and aggregate results. Model responses
may reproduce questions/answers, so these are private too. Benchmark content
must never enter training corpora.
