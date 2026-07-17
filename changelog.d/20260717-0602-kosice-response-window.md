### Fixed

Kosice force control now derives its achievable deadband and minimum useful
motor correction from scale resolution, motor step size, and learned wire gain.
Correlated KERN fluctuations use their trend-removed robust amplitude when
deciding whether to pause a current ramp, preventing sub-resolution motor
chatter and false `response_unobservable` stops near the target.

- Prevented the Košice KERN force controller from escalating motor corrections before the processed scale signal contains the preceding move response. Košice control now uses a short trend-aware estimator, waits through its post-move observation window, caps corrections relative to the active load target, and retries unobservable responses without geometric growth. The Prague scale retains its legacy control path.
- Restored Košice motor-response gain learning while the current setpoint is held, and made automatic tolerances respect the connected scale's readability. This prevents a stale low stiffness estimate from causing alternating corrections and avoids requesting sub-quantization precision from the KERN balance.
