# Native autonomous continuation

Run `.venv/bin/python -m afm_hle.autonomous start` on the gateway Mac. The detached
supervisor checks local checkpoints every minute without Codex turns. It runs the
existing Pro campaign first, then the existing base Cloud campaign, with only one
Apple route active. The Mac must remain powered on and logged in. Caffeinate keeps
the active supervisor from idle sleeping; it does not restart after a reboot.

The supervisor writes aggregate events to `.private/autonomous/supervisor.log` and
status to `.private/autonomous/state.json`. `autonomous status` reads that status.
The existing dashboard remains at http://127.0.0.1:1981/.

Confirmed quota denials create `quota-recovery-state.json` in the campaign. No
inference occurs until at least 24 hours after denial and a later Pacific calendar
day. Then one HLE question probes recovery. A confirmed quota denial starts a new
cooldown; success resumes the detached campaign. This conservative schedule is
not a claim about Apple's actual replenishment time.

Narrow, timestamp-correlated Apple logs distinguish device quota and recitation
rejection from generic Shortcuts failures. Confirmed recitation rejections become
explicit coverage gaps. Other recognized transient failures receive at most five
recorded retries with backoff. Permanent input failures and exhausted retries
become coverage gaps for later review; unknown error categories remain flagged.
No original attempt or grade is deleted, and gaps are never scored as incorrect
answers or replaced by new sample questions.

iPad dispatch remains disabled. Its four saved answers and one unresolved
reservation stay separate. Finishing Mac work therefore produces
`finished_with_gaps`, with device-owned coverage reported explicitly, rather than
claiming all 400 questions were answered by the Mac.

Grading uses the existing independent bounded recovery policy. All available
Mac answers are graded before each model handoff, or outstanding grading failures
are retained and reported. The supervisor exits when both Mac samples are
exhausted. To stop supervision, send SIGTERM to the PID in
`.private/autonomous/process.json`; separately stop campaign workers with
`campaign stop` if inference is currently active.
