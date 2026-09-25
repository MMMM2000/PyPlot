# Siglent settling qualification

The fixed-time recipe is unchanged. `Resistance settling` is a per-supply setting,
default 1.0 s (provisional), configurable 0-10 s. Zero disables the interval.
Only a changed integer-mA hardware setpoint resets the interval. Output enabling
also starts an interval. Samples are classified before the I/V queries, so a
query pair crossing the interval boundary is conservatively still settling.

CSV retains original `resistance_ohm`, current, voltage and power. It adds
`qualified_resistance_ohm` (blank during settling) and `resistance_quality`
(`settling`, `settled_interval`, or `unfiltered` for other supplies). Metadata
records the interval. New live/history traces use the qualified column; legacy
CSVs without quality flags retain the original behavior. Raw power is not a
synchronized/qualified measurement. Protection uses raw readings without delay.

This is elapsed-time qualification, not a stability detector or synchronous ADC
measurement. It cannot guarantee accuracy or eliminate all transients. If steps
occur more frequently than the interval, the qualified plot may have no points.

## Authorized hardware check, 2026-09-14 UTC

Exclusive VISA ownership; no Python controllers found at preflight. SPD1305X
refused takeover if output/timer already active. Programmed current restricted to
10-20 mA, voltage ceiling 6 V, bounded 48 s hold/up/down test. Actual issued
setpoints were 10-19 mA: the fixed-time turnaround fell between control updates,
so 20 mA was not issued. Every changed setpoint was read back and verified.
849 samples: 339 settling and 510 interval-qualified. Error queue: +0, No error.
Cleanup verified output/timer off and current/voltage setpoints zero.

Example descending step: at 36.467 s readback was 13 mA / 2.298 V (176.77 ohm);
at 36.632 s current became 12 mA with voltage still 2.298 V (191.5 ohm).
Both were flagged settling. At 37.073 s, the interval-qualified value was
12 mA / 2.224 V (185.33 ohm). Further voltage settling remained visible afterward,
so 1 s is a useful provisional suppression window, not a proven universal delay.

Ignored diagnostic artifacts: `artifacts/siglent_settling_test.py` and
`artifacts/siglent-settling-result.json`. No user measurement files were modified.
