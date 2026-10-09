## Resistance-based current-program step completion

- Step editor supports time only, resistance at/above target, or resistance at/below target. Resistance steps require a finite maximum duration; reaching it without confirmation cancels the sequence into readout, not the next heating step.
- Confirmation requires three consecutive finite positive resistance readings meeting the condition. A nonqualifying/invalid reading or inter-reading gap over 5 ms resets confirmation. No extra dwell is added. A target already satisfied at entry can complete after three readings; a crossing edge is not required.
- Global resistance protection remains first-reading and takes priority over normal step completion. Resistance steps use the protection acquisition rate even when the global cap is disabled.
- Early-ended ramps preserve their attained current at final completion. Success and timeout events are saved in metadata. New fields persist through settings, JSON import/export, duplication, and reordering; existing time-only recipes remain compatible.
- Verified using synthetic engine/worker tests and isolated settings. No hardware operated; requested acquisition rate is not a guaranteed response deadline.
