# Mini DMA Target-Acquisition Handoff: Ni46Fe27Ga23Co2Cu2 2/8

Date: 2026-06-09
Worker branch: `codex/mini-dma-live-opt-12-2-12e8`
Pushed head at handoff: `3861932` (`Revert "Probe Mini DMA held-current recovery"`)

## Scope

This handoff captures the hardware-backed target-acquisition investigation for the
`Ni46Fe27Ga23Co2Cu2` `2/8` Mini DMA wire. The objective was robust target settling
across wires with different composition, diameter, length, and stiffness while
preserving the shared HMP channel model:

- CH1: reserved for AC susceptibility; this worker did not control CH1.
- CH3: Mini DMA motor rail, preserved on when required.
- CH4: Mini DMA current sweep, safe-off after supervised runs.

## Selected Control State

The selected code state is the target-acquisition trust-region fix:

- `9cc71af` fixed stale auto-generated Mini DMA sample headers when the Sample tab
  composition/wire fields identify a new sample.
- `d01a4c0` added target-ramp trust-region behavior. It preserves the fast
  `5 mm/s` stage speed for large monotonic target errors, but limits correction
  distance near the target using live error/probe behavior.
- `b04aff0` and `1d548cd` were hardware-rejected experiments and were reverted by
  `3861932` and `ffe06f3`.

This selected target-acquisition fix is not sample-name-specific. It uses the
existing stress/load sensitivity path, so it remains tied to geometry and live
feedback rather than hard-coded values for this Ni46 2/8 wire.

## Hardware Evidence

| Variant | Commit | Run folder | Stop/status | Result |
| --- | --- | --- | --- | --- |
| Target probe repeat 1 | `d01a4c0` | `G:\My Drive\1 Projects\Praha\mini DMA\automated_control_tests\Ni46Fe27Ga23Co2Cu2 2_8 target probe iso-stress_run02` | `recipe_completed` | Target acquisition completed; stress max about `56.9 MPa`. |
| Target probe repeat 2 | `d01a4c0` | `G:\My Drive\1 Projects\Praha\mini DMA\automated_control_tests\Ni46Fe27Ga23Co2Cu2 2_8 target probe iso-stress_run03` | `recipe_completed` | Repeat completed; stress max about `54.0 MPa`. |
| Full sweep, selected logic | `d01a4c0` | `G:\My Drive\1 Projects\Praha\mini DMA\automated_control_tests\Ni46Fe27Ga23Co2Cu2 2_8 trust sweep iso-stress` | guard recovered / supervisor closed app | Target acquisition was fixed, but current-hold instability appeared around `11.4 mA`; stress max about `280 MPa`. |
| Held-current probe experiment | `b04aff0` | `G:\My Drive\1 Projects\Praha\mini DMA\automated_control_tests\Ni46Fe27Ga23Co2Cu2 2_8 trust hold probe iso-stress` | guard recovered / supervisor closed app | Worse: failed earlier around `4.2 mA`; stress max about `303.6 MPa`. |
| Dynamic current-backoff experiment | `1d548cd` | `G:\My Drive\1 Projects\Praha\mini DMA\automated_control_tests\Ni46Fe27Ga23Co2Cu2 2_8 dynamic backoff iso-stress` | guard recovered / supervisor closed app | Not a useful controller trial; stress was already about `350 MPa` at `Recording 50 MPa` with zero useful measurement rows. |

Generated phone-friendly plots are under:

- `artifacts\mini-dma-ni46-2_8-target-probe\run02_core.png`
- `artifacts\mini-dma-ni46-2_8-target-probe\run03_core.png`
- `artifacts\mini-dma-ni46-2_8-target-probe\full_sweep_core.png`
- `artifacts\mini-dma-ni46-2_8-target-probe\full_sweep_hold_probe_core.png`
- `artifacts\mini-dma-ni46-2_8-target-probe\full_sweep_dynamic_backoff_core.png`

The raw/generated plot artifacts are intentionally not committed because `artifacts/`
is ignored, but this file records the inspection handles.

## Safety State

The last supervisor safe-off reported:

- CH4 off at `0 V / 0 mA`.
- CH3 motor rail preserved on at `12 V`, about `224 mA`.
- CH1 was not controlled by this worker; supervisor readback showed CH1 off/0.
- No bench lock remained after the supervisor exited.

## Stop Condition

Do not continue full-sweep controller trials from the final physical state without
operator inspection or measured unload. The final run saw overload-level stress
before useful measurement rows, so another immediate automation run would not be a
fair controller test and could misattribute a physical/setup state to software.

The target-acquisition part of the objective is hardware-verified and repeated.
The broader across-wire full-sweep/current-hold objective remains unproven until a
fresh, inspected physical state is available for another controlled hardware trial.
