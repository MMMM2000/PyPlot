### Fixed

- Keep iso-stress and fatigue current-ramp hold detection armed after a successful mid-ramp stress recovery. The endpoint-acceptance latch added for zero-span iso-current sweeps now activates only at the actual current endpoint, preventing a low-current recovery from suppressing later holds during first overheating.
