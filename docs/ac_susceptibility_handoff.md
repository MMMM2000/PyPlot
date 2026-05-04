# AC Susceptibility Handoff

Created 2026-05-04 for continuing the LCR-6200 / AC susceptibility work on a
different PC.

## Current Repo State

Use branch:

```powershell
git checkout codex/add-ac-susceptibility-logger
```

Important files:

- `data_logging/ac_susceptibility_logger/lcr6000.py` - GW Instek LCR-6000 serial protocol helper.
- `data_logging/ac_susceptibility_logger/ac_susceptibility_logger.py` - new logger UI that reuses the current annealing logger and appends LCR data.
- `scripts/probe_lcr6000.py` - small command-line probe for the meter.
- `docs/ac_susceptibility_logger.md` - user-facing logger notes.
- `tests/test_ac_susceptibility_logger.py` - parser/command tests for the LCR helper.

The logger is registered in `launcher.py` as **AC Susceptibility Logger**.

## Hardware Identified

Meter:

```text
GW Instek LCR-6200 Precision LCR Meter
Remote ID: LCR-6200,REV E8.13,GEZ883931,Good Will Instrument Co., Ltd.
USB VID/PID: 2184:005F
```

On the original PC, Windows exposed it as:

```text
LCR Meter Virtual COM Port (COM5)
FTDIBUS\VID_2184+PID_005F+GWAIHMULA\0000
```

The COM number may change on another PC. Always probe rather than hard-coding
`COM5`.

Official resources:

- Manual: https://www.gwinstek.com/en-US/products/downloadSeriesDownNew/10211/757
- Product page and USB driver: https://www.gwinstek.com/en-global/products/detail/LCR-6000

## Driver Setup On A New PC

Install the GW Instek / FTDI virtual COM port driver before trying to probe.

1. Download and extract `USB_VCP_driver_LCR-6000_GBM-3000.zip`.
2. Set the meter communication interface to **USB** in the meter system menu.
3. In an Administrator PowerShell, run from the extracted driver folder:

```powershell
pnputil /add-driver "C:\Users\<user>\Downloads\USB_VCP_driver_LCR-6000_GBM-3000\LCR-6000 GBM-3000 USB Driver\USB VCP drivers\*.inf" /install
pnputil /scan-devices
```

Device Manager should then show the meter under **Ports (COM & LPT)**.

If the meter appears as `LCR Meter Virtual COM Port` with an error and no COM
number, the driver is not installed or did not bind. If the COM port exists but
queries time out, check that the meter interface is set to **USB**, not
**RS-232**.

## First Communication Check

Use the project virtual environment:

```powershell
.\.venv\Scripts\python.exe scripts\probe_lcr6000.py --list
.\.venv\Scripts\python.exe scripts\probe_lcr6000.py COM5 --configure --fetch
```

Replace `COM5` with the port found by `--list`.

Expected successful ID line:

```text
*IDN?: LCR-6200,REV E8.13,GEZ883931,Good Will Instrument Co., Ltd.
```

The tested configuration command sequence is:

```text
DISP:PAGE MEAS
FUNC Ls-Q
FUNC:RANG:AUTO AUTO
FUNC:MON1 Z
FUNC:MON2 IAC
FREQ 1000
LEV:VOLT 0.1
APER FAST
FETC:IMP?
```

Readback on the original PC confirmed:

```text
FUNC?      => Ls-Q
FREQ?      => 1.000000e+03
LEV:VOLT?  => 1.000e-01V
APER?      => FAST,1
FUNC:MON1? => z
FUNC:MON2? => iac
```

Example no-sample `FETC:IMP?` response:

```text
-3.11391e-03,+2.33504e-02,+8.38128e+02,+1.06598e-04,OUT ,AUX-NG,NG
```

With `Ls-Q`, monitor `Z`, and monitor `IAC`, interpret this as:

- primary: Ls
- secondary: Q
- monitor1: Z
- monitor2: IAC
- trailing fields: comparator/status flags

The `AUX-NG,NG` status appeared during disconnected/no-sample tests. Treat it as
a fixture/sample state warning, not a communication failure, if numeric values
are still returned.

## Baseline Already Tested

On 2026-04-30, the meter was tested on the original PC with no sample across:

```text
frequencies: 100, 1k, 10k, 100k Hz
AC levels: 0.1, 0.3 V
function: Ls-Q
monitors: Z, IAC
aperture: FAST
```

The raw ignored artifact was:

```text
artifacts/lcr_meter/lcr6200_no_sample_matrix.tsv
```

That file is not expected to travel with the branch because `artifacts/` is
ignored. Recreate it on the new PC if needed.

The responses were repeatable enough for plumbing tests. Representative repeat
summary:

```text
100 Hz, 0.1 V     primary mean -6.283e-1, monitor1 mean 937.69
1 kHz, 0.1 V      primary mean -3.025e-3, monitor1 mean 846.25
10 kHz, 0.1 V     primary mean  2.543e-3, monitor1 mean 929.33
100 kHz, 0.1 V    primary mean  5.751e-4, monitor1 mean 1507.46
```

## Verification Already Run

Interpreter used:

```text
C:\Users\Martin\.codex\worktrees\e27a\PyPlot\.venv\Scripts\python.exe
```

Commands passed:

```powershell
.\.venv\Scripts\python.exe -m py_compile data_logging\ac_susceptibility_logger\lcr6000.py data_logging\ac_susceptibility_logger\ac_susceptibility_logger.py scripts\probe_lcr6000.py launcher.py
.\.venv\Scripts\python.exe -m pytest tests\test_ac_susceptibility_logger.py tests\test_current_annealing_logger.py tests\test_launcher.py
.\.venv\Scripts\python.exe launcher.py --help
.\.venv\Scripts\python.exe scripts\probe_lcr6000.py COM5 --configure --fetch
```

Focused test result:

```text
30 passed, 17 warnings
```

Offscreen GUI smoke also passed:

```text
AC Susceptibility Logger
COM5 - LCR Meter Virtual COM Port (COM5) [GW Instek LCR-6000]
```

## Logger Behavior

The new logger subclasses the current annealing logger, so the DC current ramp,
hold, reverse-to-zero, voltage-limit handling, contact-loss logic, naming
workflow, live plot, and logfile preparation come from the existing current
annealing implementation.

The AC panel adds:

- LCR port selection and identify/connect actions.
- Frequency list, e.g. `100, 1k, 10k, 100k`.
- AC level list, e.g. `0.1, 0.3, 1.0`.
- Level mode: voltage or current. Voltage mode is the tested path.
- LCR function, default `Ls-Q`.
- Monitor 1 and 2, default `Z` and `IAC`.
- Aperture, default `FAST`.
- **One current sweep per AC setting**. When enabled, the logger sets loop count
  to the number of frequency-by-level combinations and enables reverse-to-zero.

Log rows keep the first three current annealing columns:

```text
Current (mA)    Voltage (V)    Resistance (Ohm)
```

Then append:

```text
AC plan index
LCR frequency (Hz)
LCR level mode
LCR level
LCR function
LCR primary
LCR secondary
LCR monitor1
LCR monitor2
LCR comparator
LCR raw
```

This keeps existing current annealing plot parsing viable because it reads the
first three numeric columns.

## Measurement Safety And Fixture Notes

Do not put the DC annealing current through the LCR meter input.

Preferred concept:

- DC annealing current goes through the microwire from the power supply.
- LCR meter applies a small AC excitation to a separate coil/fixture around or
near the wire.
- The LCR response is tracked while the DC current sweep drives heating through
the martensite/austenite transition.

Before a real transition sweep:

1. Measure empty fixture/coil.
2. Measure fixture/coil with microwire inserted, no DC annealing current.
3. Run a very low-current DC sweep to prove logging and wiring.
4. Only then run the transition-range sweep.

## Suggested First Experiment

Start conservative:

```text
LCR function: Ls-Q
Monitor 1: Z
Monitor 2: IAC
Aperture: FAST
Frequencies: 100, 1k, 10k, 100k
Voltage levels: 0.1, 0.3, 1.0
```

For the current annealing side, choose a low safe max current first. Once the
fixture is proven, use the known transition-current range for the material/wire.

Goal of the first sweep:

- Identify which frequency and AC level give the largest reproducible change in
  Ls, Z, or another useful derived signal during the transition.
- Check whether signal contrast improves with AC level or whether higher level
  perturbs/heats/saturates the response.
- Decide whether to refine with `FAST`, `MED`, or `SLOW` aperture.

## Literature Context

The relevant scientific idea is to track magnetic/inductive response through the
martensitic transition in shape-memory microwires and optimize AC excitation
conditions for maximum contrast.

Starting references:

- https://doi.org/10.1021/acsmaterialsau.5c00113
- https://doi.org/10.1016/j.jnoncrysol.2006.12.075
- https://www.nature.com/articles/s41598-017-19032-z

## Known Caveats

- `LEV:CURR?` timed out during read-only probing. Voltage excitation mode
  (`LEV:VOLT`) is the tested and recommended starting path.
- The meter needed short settle delays between configuration commands before
  `FETC:IMP?`; these are now built into `Lcr6000Serial.configure()`.
- The no-sample status was `OUT ,AUX-NG,NG`. Do not use the status alone as a
  failure indicator until the real fixture behavior is understood.
- On a different PC, the COM port will likely differ from `COM5`.
- Absolute susceptibility calibration is not implemented. Current work logs raw
  LCR quantities so the best frequency/amplitude can be found empirically first.

