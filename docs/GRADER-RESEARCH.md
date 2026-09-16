# Modern reference-grader recommendation — 2026-09-16

Recommendation: **Gemini 3.7 Flash**, model ID `gemini-3.7-flash` on Google's API
or `google/gemini-3.7-flash` on OpenRouter, as the next candidate for this project's
reference grader. This is a project recommendation, not a claim that the HLE
authors have replaced their historical o3-mini default. Keep the existing baseline.

## Evidence

[Generality Labs' HLE judge comparison](https://generality.org/blog/posts/hle-judge-comparison/)
evaluated candidates against a three-family frontier-model panel across 4,943
judgments. Disputed panel labels were deliberated and then majority-resolved.
Gemini 3.7 Flash was selected as an independent backup judge; GLM 5.3 Flash was
the lower-cost selection. These are model-panel labels, not human-certified truth,
and the evaluated answers came from other model families rather than AFM.

The current [Inspect HLE configuration](https://github.com/UKGovernmentBEIS/inspect_evals/blob/2999eaadad35edebada59bac4a687793c11d1c3c/src/inspect_evals/hle/run_configs/default.yaml)
was fetched directly at commit `2999eaadad35edebada59bac4a687793c11d1c3c`.
It configures GLM 5.3 Flash with fp8 routing as primary and Gemini 3.7 Flash as a
separate backup. Both use temperature 0 and a 32,768-token ceiling. Its scorer
uses a GRADE: C/I text protocol, not our original JSON-schema protocol. A cached
raw-file web result showed older defaults; the pinned live source resolves that
discrepancy. Do not claim our current runner reproduces Inspect's configuration.

[Google documents Gemini 3.7 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash)
as a stable August 2026 model with structured-output support. [OpenRouter](https://openrouter.ai/google/gemini-3.7-flash)
also advertises JSON-schema responses; its current standard listing is $0.75/M
input and $3.75/M output, with different service-tier pricing. Verify the actual
local route's prices and parameter translation before running. Documentation
support is not proof that our exact schema will work through every proxy.

## What the GLM trial does and does not establish

Our Nous route returned five valid grades and two invalid JSON responses on the
next saved answer. This identifies a compatibility failure in that tested route
and protocol, not poor HLE grading accuracy. [Z.AI's structured-output guide](https://docs.z.ai/guides/capabilities/struct-output)
documents JSON-object mode plus explicit format instructions; that is distinct
from assuming strict JSON-schema enforcement on a third-party route. We have not
verified that the Nous route matches Inspect's fp8 deployment.

## Selection and validation

Prefer Gemini 3.7 Flash for the next independent reference trial. Retain GLM as a
low-cost candidate if its protocol/routing compatibility is resolved. Google's
[newer-model catalog](https://ai.google.dev/gemini-api/docs/models) also lists
Gemini 3.8 Flash, but the HLE judge evidence located here specifically validates
3.7; newer model capability alone is insufficient evidence for a grader switch.

Once a Gemini route is available, validate schema handling on synthetic input,
then grade the existing 50-response snapshot without new Apple calls. Preserve
judge identity, response hashes, reasoning/output settings, failures, and costs.
Review correctness disagreements and positive-label agreement before promoting
it. Publish separate judge scores; neither average them nor overwrite o3-mini.
Changing prompts, parsing, or completion ceilings requires a new named protocol.
No new model calls or grader switch were made during this research.
