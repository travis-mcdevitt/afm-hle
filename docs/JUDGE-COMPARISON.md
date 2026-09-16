# Additional judge trial — 2026-09-16

The `nous-glm-5.3-flash` route was verified using the existing private LiteLLM
credential. `.env` was not changed. No additional Apple generation requests were
made, and the original 50 reference grades remain intact.

The trial uses an isolated, generation-disabled snapshot of the same 50 saved
answers. Both judges receive the same reference prompt, answer keys, structured
schema, and 4,096-token completion ceiling. The comparator verifies generation
manifests, response hashes, and grading protocol before producing aggregates.

## Result: paused on structured-output incompatibility

Five grades completed and agree with the reference on correctness and confidence:
three Cloud responses (all incorrect) and two Cloud Pro responses (one correct).
This is far too small to establish judge equivalence or compare model accuracy.
The other 45 answers are not scored by Flash; one is unresolved and 44 are pending.

The sixth answer received HTTP 200 with `finish_reason=stop`, but the content was
plain labeled text rather than valid JSON. One explicitly recorded retry also
returned invalid JSON. The outputs and usage are retained privately; neither was
converted into an incorrect answer or accepted through a relaxed parser. The
route advertises `num_retries: 0`; structured-schema support was not advertised
in the inspected metadata. This observation does not establish whether the
failure originates in the model, provider, or proxy parameter handling.

Seven judge calls were made: five valid grades and two invalid-format responses.
Their total usage-based cost estimate is $0.00146072 using the proxy's advertised
$0.06/M input and $0.20/M output token rates. This is proxy metadata, not an
independent verification of upstream billing. The generated aggregate report
also records any proxy-reported cost separately.

See [machine-readable comparison](../reports/judge-comparison.json). The reference
accounting covers all 50 reference grades; alternative accounting covers only the
Flash trial. Agreement metrics use only the five matched valid grades, not these
different denominators.

Next step: verify this route forwards and supports `response_format=json_schema`,
or explicitly test a different output-format protocol with separate provenance.
Do not change the frozen reference grades or quietly relax schema validation.
The Flash trial is paused; there is no background inference process.
