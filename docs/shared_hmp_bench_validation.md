# Shared HMP Bench Validation Runbook

Use this runbook when validating that Current Annealing Logger and TMA Logger can safely share the same HMP4030/HMP4040 family supply. It is written for the current Košice HMP4030 bench wiring and is meant to be executed only when no real TMA measurement is running.

## Current Bench Wiring

| HMP channel | Role | Expected connection |
| --- | --- | --- |
| CH1 | Current Annealing Logger | Current annealing wire |
| CH2 | TMA motor supply | Tic VIN rail, normally `12 V` with `0.5 A` rail-current limit on this bench |
| CH3 | TMA current sweep | TMA sample/current path, or open circuit for no-wire checks |

The shared broker profile must confirm CH1 as `current_annealing` if current annealing is in use, CH2 as `mini_dma_motor_supply`, and CH3 as `mini_dma_current_sweep` before any output is enabled.

TMA direct HMP profiles intentionally have no profile-default output channel. In shared-broker mode, manual auto-connect and recipe preflight can fill the current Košice default of **Current-sweep channel** CH3 and **Motor supply** CH2 before checking Tic VIN. Review those selectors after any rewiring.

## Preconditions

- Confirm no TMA recipe or Current Annealing process is already running.
- Coordinate Codex/hardware automation threads with the shared bench guard before touching the HMP. Use `python scripts/hmp_bench_guard.py status --probe` to inspect the default shared lock at `C:\tmp\pyplot_hmp_bench.lock` and the HMP readback state. Use `python scripts/hmp_bench_guard.py acquire --owner <thread-or-user> --purpose "<test>" --timeout <seconds>` when a script needs a short exclusive hardware window. TMA bench automation takes this lock automatically for execute-mode plans unless the plan explicitly sets `"bench_lock": {"enabled": false}`.
- Confirm HMP4030/HMP4040 is on `COM3` at `115200` baud.
- Confirm the Current Annealing wire is connected to CH1 before any CH1 current test.
- Confirm whether the TMA current path is connected to CH3. If no wire/sample is connected, keep CH3 at a low voltage limit and treat the expected result as open-circuit behavior.
- Confirm whether the TMA motor can safely be powered. CH2 motor-supply tests should energize the Tic rail but should not move the stage unless the operator explicitly wants the small motion smoke.
- Keep a direct safety-off check ready:

```powershell
@'
from data_logging.shared_power_supply.driver import HmpSerialDriver

driver = HmpSerialDriver(port_name="COM3", baudrate=115200, timeout_s=0.7)
driver.connect()
try:
    print(driver.identify())
    for ch in (1, 2, 3):
        driver.configure_channel(channel=ch, voltage_v=1.0, current_a=0.001, output_on=False)
    for ch in (1, 2, 3):
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
- Assign and confirm CH1, CH2, and CH3 roles.
- In TMA, explicitly select CH3 for the current-sweep channel and CH2 for motor supply before preparing outputs.
- Lease CH1 as Current Annealing, CH2 as TMA motor supply, and CH3 as TMA current sweep.
- Attempt one intentional wrong-role lease, such as CH1 as TMA current sweep, and expect rejection.
- Configure CH2 and CH3 output off.

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

### 3. TMA CH3 No-Wire Logging

Goal: prove TMA shared-broker logging works even with CH3 open circuit.

Recommended open-circuit settings:

- CH3 voltage limit: `1 V`
- CH3 setpoints: `1-3 mA`
- Motor supply disabled for this step

Expected no-wire behavior:

- Measured voltage reaches approximately the low voltage limit.
- Measured current stays near zero or the HMP readback floor.
- `measurement.csv` still records current, voltage, resistance, and power columns.

Pass criteria:

- `metadata.json` says `heating.profile == "shared_hmp_broker"`.
- `metadata.json` says current-sweep channel `3`.
- `measurement.csv` contains rows with voltage/resistance/power fields present.
- CH3 is off and lease-free after stop.

### 4. TMA CH2 Motor-Supply Path

Goal: prove TMA can control the motor supply rail through the broker.

Recommended settings:

- CH2 voltage: `12 V`
- CH2 rail-current limit: `0.5 A` on the current bench.
- CH3 output off unless the current path is intentionally being tested.

Pass criteria:

- TMA can enable CH2 without taking over CH1 or CH3.
- Manual hardware auto-connect reports completion only after CH2 is enabled and Tic VIN is rechecked.
- HMP CH2 readback shows approximately `12 V` and a plausible Tic rail current.
- TMA metadata records motor supply enabled, channel `2`, and shared broker profile.
- TMA can disable CH2 cleanly, and direct readback confirms `OUTP=0`.

### 5. Small Tic Motion Smoke

Goal: prove CH2 motor power, Tic communication, and shared HMP ownership work together.

Only run this with explicit operator permission.

Recommended motion:

- Move `0.01-0.05 mm`.
- Return to the original position.
- Keep CH3 current output off unless a sample is intentionally connected.

Pass criteria:

- Tic position changes and returns as expected.
- HMP CH2 remains stable during the small move.
- CH1 Current Annealing logging is unaffected if CH1 is active.
- No unexpected current appears on CH3.

### 6. Connected CH3 Current Smoke

Goal: prove TMA can deliver current through a connected CH3 sample in shared mode.

Only run this when the TMA current path is connected.

Recommended settings:

- CH3 voltage limit: start at `1 V`.
- CH3 current setpoints: `1 mA`, `2 mA`, `3 mA`.
- Do not run a full recipe yet.

Pass criteria:

- CH3 measured current follows the setpoint within expected HMP/sample tolerance.
- `measurement.csv` records finite resistance.
- CH1 Current Annealing still logs independently if active.
- CH3 is off and lease-free after stop.

### 7. Short Full Shared Run

Goal: prove both applications can do a realistic concurrent run.

Only run this after steps 1-6 pass.

Recommended recipe:

- Current Annealing CH1: low-current short run.
- TMA: one target only, small current range, conservative voltage limit.
- CH2 motor supply enabled.
- CH3 connected to the TMA current path.

Pass criteria:

- Current Annealing log contains CH1 rows.
- TMA `measurement.csv`, `metadata.json`, and `control_trace.csv` are written.
- TMA metadata records shared broker, CH2 motor supply, and CH3 current sweep.
- Final direct HMP readback confirms expected output state, normally all relevant outputs off after cleanup.

## Failure Cleanup

If any step fails:

- Stop the active app process through its normal UI path if possible.
- Run the direct safety-off check from the preconditions.
- Confirm CH1, CH2, and CH3 report `OUTP=0`, `0.0 V`, and `0.0 mA`.
- Save the validation artifact folder and note which step failed.

## Artifact Review Checklist

Current Annealing:

- Output file path and row count.
- Current, voltage, and resistance ranges.

TMA:

- `metadata.json`: `heating.profile`, current channel, motor channel, motor supply enabled flag, voltage limit, session state, point count.
- `measurement.csv`: row count, current setpoint, measured current, voltage, resistance, power.
- `control_trace.csv`: row count and any wait/correction/hold/fault phases.
- Optional `ui_telemetry.csv`: session/plot heartbeat when diagnosing UI responsiveness.

HMP final state:

- Direct post-test readback for CH1, CH2, CH3.
- Broker leases released.
