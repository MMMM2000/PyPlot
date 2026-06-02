2026-05-28 10:42
- Added a bench-automation-only option to continue tensile slack take-up after current-sweep mechanical load loss, while normal operator recipes still stop on the same condition.
- Added an optional bench guardrail override for the current-sweep correction travel cap so automated slack take-up can pull far enough to re-tension the wire.
- Discard stale stopped-run resume state when the visible recipe controls have changed, preventing an older 50 MPa current sweep from resuming under a newly edited target start.
