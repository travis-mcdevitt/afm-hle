# Execution status — 2026-09-16

The pinned HLE dataset is available locally: 2,500 questions, including 342 image
questions. The working snapshot excludes binary preview/rationale fields. Five
WebP and three GIF images were static and converted losslessly to PNG; all eight
passed decoded-pixel equality checks. The largest payload is 3,241,554 bytes.

## Completed pilot

50 generations and 50 independent reference-judge grades are checkpointed: the
same 25 questions on each cloud route (1% coverage per model).

| Route | Judged | Correct | Pilot accuracy | Wilson 95% interval |
| --- | ---: | ---: | ---: | ---: |
| AFM Cloud | 25 | 0 | 0% | 0–13.3% |
| AFM Cloud Pro | 25 | 2 | 8% | 2.2–25.0% |

This is a small partial run, not a full HLE score or evidence of a reliable model
ranking. No model build identity is attested by Shortcuts. The grader returned
`o3-mini-2025-01-31` for all 50 completed grades.

All 50 generation records matched successful, delivered gateway ledger entries.
No Apple quota or transport failure occurred in this pilot; cloud token usage
remains unknown. Generation stopped at the chosen pilot call cap. Completed
answers will not be repeated when the same run resumes.

The judge completed 51 client attempts: 50 successful grades and one HTTP 502
overload response. An explicit retry retained the failed attempt in history.
The completed grades used 45,738 input and 18,133 output tokens. Their estimated
and proxy-reported cost both total $0.130097. The failed attempt has unknown usage
and cost, so this is not a guaranteed complete billing total. No budget cap was
requested. No automatic client retry occurred.

Twenty-three offline tests pass, including checkpoints, quota/transport stops,
judge cost history, budget guards, immutable configurations, local environment
parsing, image conversion, and exclusive locks. The earlier four synthetic checks
remain separate from HLE. `.env`, raw benchmark content, responses, SQLite state,
and the SQLite-consistent local pilot backup remain ignored and private.

See reports/progress.json for machine-readable aggregate results. Remaining work:
scale beyond the pilot in resumable batches, review grading quality, and publish
full-run and subgroup results when sufficient coverage is available. No background
runner or scheduled quota probe is active.

## Additional judge trial

`nous-glm-5.3-flash` was tested against the existing saved responses with no new
Apple calls. Five valid grades matched the reference; two attempts on the next
answer failed JSON validation, and grading paused. Reference grades remain intact.
See [judge comparison](JUDGE-COMPARISON.md) for the compatibility finding and
[aggregate comparison](../reports/judge-comparison.json) for costs and agreement.
Twenty-four offline tests now pass, including isolated snapshots and comparisons.

## Gemini trial completed

`nous-gemini-3.7-flash` graded all 50 saved answers with an explicitly recorded
API schema variant (boolean enum omitted; identical local validation). It agreed
with o3-mini on 49/50 correctness labels and all confidence values. See the
[Gemini trial](GEMINI-TRIAL.md) and [aggregate comparison](../reports/gemini-comparison.json).
No additional Apple calls were made; the reference baseline remains unchanged.
Twenty-six offline tests pass.
