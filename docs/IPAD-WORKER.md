# iPad worker extension

Status: design and offline durable-queue scaffold, not connected to live HLE
campaigns. No iPad server is exposed and no iPad model requests have been sent.
The running Mac benchmarks retain their existing configuration.

## Device and capacity checks

User device: iPad Air 11-inch (M3), iPadOS 27.0. This meets Apple's published
Apple Intelligence hardware/OS requirements. Enable Apple Intelligence and use
matching supported device/Siri languages; verify the actual Shortcuts model
picker offers AFM Cloud (and separately Pro if desired). Hardware eligibility
does not establish a specific model route's availability.

The user confirmed the iPad uses the same Apple Account and the same LAN as
the Mac. Private authenticated transport still needs provisioning. The logged error
`deniedDueToUserDeviceRateLimit` suggests device-related enforcement, but does not
prove separate per-device allowances. Observed Mac limits (Cloud 140 and Pro 100
in an 86,400-second rule) must not be assumed for this iPad or added together as
promised capacity. A successful iPad request while Mac is quota-blocked would
support separate effective capacity at that time, not establish a permanent
quota contract. Honor any iPad limit; no identity changes or automatic quota
probing. Start with one synthetic request on each route being enrolled, outside
the HLE denominator, and record the result and exact model selection.

Sources checked September 17, 2026:
- [Apple Intelligence requirements](https://support.apple.com/en-us/121115)
- [Apple Intelligence usage limits](https://support.apple.com/en-us/127901)
- [Use Apple Intelligence in Shortcuts](https://support.apple.com/guide/shortcuts/use-apple-intelligence-in-shortcuts-tpg3vrvwmclv/ios)
- [Request an API from Shortcuts](https://support.apple.com/guide/shortcuts/request-your-first-api-apd58d46713f/ios)

## Architecture

The iPad runs a bounded Shortcut that pulls a job from an authenticated gateway
endpoint on the Mac, invokes Use Model locally on the iPad, and posts the result
back. Calling the existing Mac `/v1/chat/completions` from iPad would still execute
on the Mac and would not add an iPad execution path.

The Mac stays the coordinator, judge host, and authoritative ledger. Extend
`afm-gateway` with a worker adapter; retain its current loopback listeners and
credentials. Supply private HTTPS access or an authenticated encrypted tunnel
for the iPad, selected after confirming LAN/VPN availability. Do not expose the
current dashboard, administrative token, or judge key as the worker API. Issue
one device-scoped credential for only fetching assigned work and returning its
results. The future gateway must derive device identity from that credential,
not trust a caller-provided device ID.

Suggested API (not implemented):
- `POST /workers/next`: reserve one assigned job, durably marking it in flight
  before delivery. Return an opaque job ID, payload digest, and generation input.
- `POST /workers/result`: submit the ID, digest, route, status, and output.
  An identical repeated submission is acknowledged without another inference;
  conflicting results are rejected.
- Local-only operator controls reconcile interrupted jobs, hold a device/route,
  and authorize a retry as a new attempt. There is no lease-expiry redispatch.

A dropped claim response or Shortcut abort leaves an uncertain attempt. It may
have run at Apple, so it cannot silently return to pending. If the model succeeds
but uploading fails, retain the answer privately on iPad and retry the upload,
not the model call. Shortcuts action errors may abort execution before reporting;
the coordinator must represent this explicitly instead of relying on error
callbacks always running. Foreground, powered operation is the initial target;
continuous background execution has not been validated.

## Minimal Shortcut recipe to validate

1. Get Contents of URL: authenticated POST to the future worker `next` endpoint.
2. Stop if no work, a device hold, or an unsupported route is returned.
3. Decode the request, retaining job ID and payload digest as variables.
4. For the Cloud worker, invoke Use Model with **AFM Cloud** explicitly selected,
   follow-up/chat behavior disabled, and no fallback to another model or ChatGPT.
5. Persist the result locally before uploading it; POST the result to the gateway.
6. Only continue a small fixed batch after an acknowledged result. Stop on errors.

This recipe is not an installable or tested Shortcut yet. Before HLE use, port
and verify Hollis's exact system/user prompt rendering, image input order, and
lossless image handling. Test text and image payload parity using synthetic
fixtures. Do not switch to a text-only subset just because image transport is
unfinished: that would change the sampled population.

## Benchmark and accounting requirements

Record stable device ID (not an Apple Account address), device model, OS build,
Shortcut version/hash, selected route, generation protocol hash, gateway request
ID, payload hash, and all attempt transitions. Report device-specific coverage,
latency, failures, and quota events. Model aliases do not attest identical model
builds across platforms. Do not mix iPad outputs into Mac scores until transport
parity and provenance are reviewed; publish device-stratified aggregates even if
later pooling is justified. Cloud tokens and reasoning remain null unless Apple
provides measurements. Label iPad-reported timing separately from Mac end-to-end
latency.

Partition the existing fixed sample by model/question before dispatch, retaining
one central ownership record and one accepted answer. Do not run a second
campaign from question 1, replace saved answers, or move uncertain Mac attempts
to iPad without explicit reconciliation. Start on undispatched questions only.
The current Mac runner assumes a single worker and has no cross-device ownership
fields: a coordinator integration/migration is required before concurrent HLE
assignment, rather than having both workers write its SQLite tables directly.

## Implemented scaffold and remaining dependencies

`afm_hle/device_queue.py` provides private SQLite persistence, atomic claims,
stable job IDs and payload hashes, device/result identity checks, idempotent
result uploads, retained failures, and no automatic replay after interruption.
It blocks a device with any unresolved job. Synthetic tests exercise these
properties. It is intentionally offline: authentication, transport size limits,
TLS, gateway ledger integration, operator reconciliation, device provisioning,
Shortcut export/signing, and cross-worker HLE allocation are still required.

Next deployment steps: confirm the actual iPad model picker; provision private
LAN transport; configure the gateway worker adapter and scoped credential; install/test the
bounded iPad Shortcut; record one synthetic availability check; then assign a
small set of untouched HLE questions through the central coordinator. The iPad
will require user-side setup because this session cannot operate its screen.
