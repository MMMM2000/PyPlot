2026-08-10 15:15 UTC

- Preserve TMA wire-break/contact-loss metadata across the dedicated-process handoff so the visible logger reports the fault, generates run summaries, and offers recovery without an invisible child-process dialog blocking shutdown.
- Let campaign-supervised process-isolated runs transfer hardware ownership before the authoritative child opens its logging session, and accept the campaign's predeclared mounted length without an invisible modal prompt.
- Do not delay recipes that already enable first overheating on an unrelated completed-run history scan.
- Make campaign-supervised runs load their recipe before deciding whether previous-run history is required, then hand that completed decision to the isolated start path instead of repeating the gate after sample identity changes.
- Recover a shared-HMP bench lock only when its recorded controller PID is verified absent, allowing safe restart after an abnormal process exit without stealing a live controller's ownership.
- Keep the external TMA safety supervisor from mistaking the first completed run in a multi-run validation plan for completion of the whole plan.
- Restore the motor-supply channel to its pre-test output state after shared-HMP live validation.
- Suppress current-hold motor correction only for genuinely target-spanning fluctuations, preventing one-sided stationary error from deadlocking a current ramp.
- Retain finalized child-run metadata in the unattended bench summary after the isolated window clears its live session path.
