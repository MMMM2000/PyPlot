# Shared HMP Bench Validation Runbook

Use this runbook when validating that Current Annealing Logger and Mini DMA Logger can safely share the same HMP4040. It is written for the current bench wiring and is meant to be executed only when no real Mini DMA measurement is running.

## Current Bench Wiring

| HMP channel | Role | Expected connection |
| --- | --- | --- |
| CH1 | Current Annealing Logger | Current annealing wire |
| CH3 | Mini DMA motor supply | Tic VIN rail, normally `12 V` with `0.4 A` rail-current limit on this bench |
| CH4 | Mini DMA current sweep | Mini DMA sample/current path, or open circuit for no-wire checks |

The shared broker profile must confirm CH1 as `current_annealing`, CH3 as `mini_dma_motor_supply`, and CH4 as `mini_dma_current_sweep` before any output is enabled.

Mini DMA intentionally has no profile-default output channel. Before using supply controls, set **Current-sweep channel** to the wired Mini DMA current channel, normally CH4 on this bench. If motor supply is enabled, set **Motor supply** to the wired motor rail channel, normally CH3. Auto-detecting or changing the supply profile leaves both selectors unselected until the operator chooses them.

## Preconditions

- Confirm no Mini DMA recipe or Current Annealing process is already running.
- Coordinate Codex/hardware automation threads with the shared bench guard before touching the HMP. Use `python scripts/hmp_bench_guard.py status --probe` to inspect the default shared lock at `C:\tmp\pyplot_hmp_bench.lock` and the HMP readback state. Use `python scripts/hmp_bench_guard.py acquire --owner <thread-or-user> --purpose "<test>" --timeout <seconds>` when a script needs a short exclusive hardware window. Mini DMA bench automation takes this lock automatically for execute-mode plans unless the plan explicitly sets `"bench_lock": {"enabled": false}`.
- Confirm HMP4040 is on `COM3` at `115200` baud.
- Confirm the Current Annealing wire is connected to CH1 before any CH1 current test.
- Confirm whether the Mini DMA current path is connected to CH4. If no wire/sample is connected, keep CH4 at a low voltage limit and treat the expected result as open-circuit behavior.
- Confirm whether the Mini DMA motor can safely be powered. CH3 motor-supply tests should energize the Tic rail but should not move the stage unless the operator explicitly wants the small motion smoke.
- Keep a direct safety-off check ready:

```powershell
@'
from data_logging.shared_power_supply.driver import HmpSerialDriver

driver = HmpSerialDriver(port_name="COM3", baudrate=115200, timeout_s=0.7)
driver.connect()
try:
    print(driver.identify())
    for ch in (1, 3, 4):
        driver.configure_channel(channel=ch, voltage_v=1.0, current_a=0.001, output_on=False)
    for ch in (1, 3, 4):
        readback = driver.measure(channel=ch)
        out = driver.query("OUTP?")
        print(f"CH{ch} OUTP={out} {readback}")
finally:
    driver.close()
'@ | .\.venv\Scripts\python.exe -
```

## Validation Sequence

### 1. Broker And Channel Isolation

Goal: prove one broker can lease each channel role and reject cross-role control.

Steps:

- Start a broker on `COM3`.
- Assign and confirm CH1, CH3, and CH4 roles.
- In Mini DMA, explicitly select CH4 for the current-sweep channel and CH3 for motor supply before preparing outputs.
- Lease CH1 as Current Annealing, CH3 as Mini DMA motor supply, and CH4 as Mini DMA current sweep.
- Attempt one intentional wrong-role lease, such as CH1 as Mini DMA current sweep, and expect rejection.
- Configure CH3 and CH4 output off.

Pass criteria:

- All correct leases succeed.
- Wrong-role lease is rejected.
- No channel other than the requested channel changes output state.

### 2. Current Annealing CH1 Logging

Goal: prove shared-broker Current Annealing still writes real rows while another client owns other channels.

Recommended low-current settings:

- CH1 voltage limit: `1 V`
- CH1 current range: `2-4 mA`
- Step: `1 mA`
- Short run only

Pass criteria:

- Current Annealing output file contains data rows, not just a header.
- CH1 readback shows non-zero current through the connected wire.
- Resistance is finite and plausible for the wire/contact.
- CH1 is off and lease-free after stop.

### 3. Mini DMA CH4 No-Wire Logging

Goal: prove Mini DMA shared-broker logging works even with CH4 open circuit.

Recommended open-circuit settings:

- CH4 voltage limit: `1 V`
- CH4 setpoints: `1-3 mA`
- Motor supply disabled for this step

Expected no-wire behavior:

- Measured voltage reaches approximately the low voltage limit.
- Measured current stays near zero or the HMP readback floor.
- `measurement.csv` still records current, voltage, resistance, and power columns.

Pass criteria:

- `metadata.json` says `supply.profile_id == "shared_hmp_broker"`.
- `metadata.json` says `supply.channel == 4`.
- `measurement.csv` contains rows with voltage/resistance/power fields present.
- CH4 is off and lease-free after stop.

### 4. Mini DMA CH3 Motor-Supply Path

Goal: prove Mini DMA can control the motor supply rail through the broker.

Recommended settings:

- CH3 voltage: `12 V`
- CH3 rail-current limit: `0.4 A` on the current bench, unless intentionally testing the newer `0.5 A` default.
- CH4 output off unless the current path is intentionally being tested.

Pass criteria:

- Mini DMA can enable CH3 without taking over CH1 or CH4.
- Manual hardware auto-connect reports completion only after CH3 is enabled and Tic VIN is rechecked.
- HMP CH3 readback shows approximately `12 V` and a plausible Tic rail current.
- Mini DMA metadata records motor supply enabled, channel `3`, and shared broker profile.
- Mini DMA can disable CH3 cleanly, and direct readback confirms `OUTP=0`.

### 5. Small Tic Motion Smoke

Goal: prove CH3 motor power, Tic communication, and shared HMP ownership work together.

Only run this with explicit operator permission.

Recommended motion:

- Move `0.01-0.05 mm`.
- Return to the original position.
- Keep CH4 current output off unless a sample is intentionally connected.

Pass criteria:

- Tic position changes and returns as expected.
- HMP CH3 remains stable during the small move.
- CH1 Current Annealing logging is unaffected if CH1 is active.
- No unexpected current appears on CH4.

### 6. Connected CH4 Current Smoke

Goal: prove Mini DMA can deliver current through a connected CH4 sample in shared mode.

Only run this when the Mini DMA current path is connected.

Recommended settings:

- CH4 voltage limit: start at `1 V`.
- CH4 current setpoints: `1 mA`, `2 mA`, `3 mA`.
- Do not run a full recipe yet.

Pass criteria:

- CH4 measured current follows the setpoint within expected HMP/sample tolerance.
- `measurement.csv` records finite resistance.
- CH1 Current Annealing still logs independently if active.
- CH4 is off and lease-free after stop.

### 7. Short Full Shared Run

Goal: prove both applications can do a realistic concurrent run.

Only run this after steps 1-6 pass.

Recommended recipe:

- Current Annealing CH1: low-current short run.
- Mini DMA: one target only, small current range, conservative voltage limit.
- CH3 motor supply enabled.
- CH4 connected to the Mini DMA current path.

Pass criteria:

- Current Annealing log contains CH1 rows.
- Mini DMA `measurement.csv`, `metadata.json`, and `control_trace.csv` are written.
- Mini DMA metadata records shared broker, CH3 motor supply, and CH4 current sweep.
- Final direct HMP readback confirms expected output state, normally all relevant outputs off after cleanup.

## Failure Cleanup

If any step fails:

- Stop the active app process through its normal UI path if possible.
- Run the direct safety-off check from the preconditions.
- Confirm CH1, CH3, and CH4 report `OUTP=0`, `0.0 V`, and `0.0 mA`.
- Save the validation artifact folder and note which step failed.

## Artifact Review Checklist

Current Annealing:

- Output file path and row count.
- Current, voltage, and resistance ranges.
- `metadata/<data-file-stem>/metadata.json`: `supply.profile_id`, `supply.channel`, `supply.voltage_limit_v`, `supply.broker_source`, and `recipe.current_ramp_rate_mA_s`.
- Optional metadata index: run `launcher.py --current-annealing-index-source annealing=<output-root> --current-annealing-index-output-dir <review-folder>` to write `current_annealing_index.csv` and `.jsonl` from the metadata sidecar tree.

Mini DMA:

- `metadata.json`: shared-HMP heating/supply profile, current channel, motor channel, motor supply enabled flag, voltage limit, session state, point count.
- `measurement.csv`: row count, current setpoint, measured current, voltage, resistance, power.
- `control_trace.csv`: row count and any wait/correction/hold/fault phases.
- Optional `ui_telemetry.csv`: session/plot heartbeat when diagnosing UI responsiveness.
- Optional metadata index: run `launcher.py --mini-dma-index-source mini=<output-root> --mini-dma-index-output-dir <review-folder>` to write `runs_index.csv` and `.jsonl`.

HMP final state:

- Direct post-test readback for CH1, CH3, CH4.
- Broker leases released.
