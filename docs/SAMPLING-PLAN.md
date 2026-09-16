# Fixed 400-question Cloud Pro campaign

This continuation runs **afm-cloud-pro only** against the first 400 questions
in the existing `sha256(afm-hle-v1:<question-id>)` permutation. That order was
fixed before pilot outcomes. It includes the 25 completed Pro answers and their
Gemini grades; 375 new answers remain at launch. No new Cloud calls are included.
The sample contains 60 image questions. The private plan freezes the dataset,
selected IDs, model scope, and judge configuration before dispatch.

This is a reproducible pseudorandom subset, not an official HLE leaderboard run
or a new benchmark version. Randomized order plus a fixed sample limit is an
[Inspect-supported evaluation pattern](https://inspect.aisi.org.uk/options.html).
We implement the equivalent frozen permutation and cap in the gateway runner.
At 400 questions, the usual worst-case 95% normal-approximation margin for a
proportion is about 4.9 percentage points, ignoring the finite-population
correction ([NIST sample-size formula](https://www.itl.nist.gov/div898/handbook/ppc/section3/ppc333.htm)).
Final reporting uses Wilson intervals and identifies the sampled population.
This precision calculation assumes the hash permutation behaves like random
sampling; the sample is not stratified by subject or modality.

The stopping rule is 400 completed and graded answers, independent of accuracy.
Live scores and pointwise intervals are provisional, not sequential guarantees.
Quota interruptions or systematic failures can make the completed subset
unrepresentative; report coverage and do not call incomplete coverage a full HLE
score. Route names do not attest an immutable Apple model build.

Grading uses `nous-gemini-3.7-flash`, the existing HLE rubric and portable-boolean
schema profile. It is a substitute judge, not the reference grader. The pilot
comparison and its limitations remain in [Gemini trial](GEMINI-TRIAL.md).
No judging budget cap applies. Cost estimates retain the configured $0.75/M input
and $3.75/M completion rates; actual proxy billing may differ.

The detached native process alternates batches of at most ten generations and
ten grades, checkpointing every result. It preserves the pilot databases and
extends independent campaign databases. A local HTTP dashboard reads aggregate
SQLite state every three seconds without making model requests. There is no AI
observer, recurring Codex task, or automatic quota probing. Apple generation failures pause generation for explicit review. Judge failures
or timeouts defer further grading while Apple generation continues. Failed grades
retain their raw response, usage, and cost for explicit future retry; completed
items are never replayed. After all answers are saved, an incomplete grading run
is labeled `generation_complete_grading_pending`, never complete.

This operational change was authorized after 52 Pro answers and 43 valid grades.
It changes failure handling only; the frozen sample, prompts, judge configuration,
and original plan metadata remain unchanged.

The Mac must remain running in its logged-in GUI session. `caffeinate -i` prevents
idle system sleep while work is active; closing the lid, logout, reboot, or
network loss can still interrupt it. The dashboard stays available after a
pause or completion, without keeping the machine awake for inference.

See [operations](OPERATIONS.md#detached-cloud-pro-campaign) for live monitoring,
stopping, and resumption. Public sample metadata is in
[reports/sample-plan.json](../reports/sample-plan.json); selected IDs and answers
remain private.
