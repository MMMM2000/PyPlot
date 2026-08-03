### Changed

- Show completed and active TMA fatigue-cycle counts beneath the fatigue recipe's cycle input and in the recipe progress display, and preserve the counters in run metadata, UI telemetry, logs, and stopped-run resume state for finite and Forever recipes.
- Persist the initiating reason, code location, automation context, and each teardown stage synchronously before changing a running recipe to Manual mode, so unattended interruptions retain a durable failure boundary instead of losing their stop cause.
- Preserve the local metadata checkpoint and emit an emergency recovery bundle when an established run directory disappears instead of silently recreating the destination during stop finalization.
