# AC Susceptibility Logger

The AC Susceptibility Logger is a focused two-step tool for coil-based AC
susceptibility runs with a GW Instek LCR-6200/LCR-6000 series meter and a DC
current source:

1. **Measure empty-coil baseline** with no microwire installed.
2. **Run microwire current sweep** after inserting the wire, using the same LCR
   frequency/amplitude settings while sweeping DC current up and back down.

The older current-annealing controls are hidden or replaced in this logger so
the normal workflow only shows instrument setup, the AC experiment plan,
empty-coil baseline, microwire sweep, and stop actions.

For the full hardware/code handoff, including driver setup, probe results,
verified commands, and next experimental steps, see
`docs/ac_susceptibility_handoff.md`.

The legacy current-annealing-compatible logfile path still writes the first
three columns as:

```text
Current (mA)    Voltage (V)    Resistance (Ohm)
```

The logger then appends the LCR setting and the latest `FETC:IMP?` response:

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

## Hardware Notes

The meter in the lab photo is a GW Instek LCR-6200, part of the LCR-6000 series.
Use the rear USB-B port for remote control. The official manual says the USB
device appears as a CDC virtual COM port after the vendor driver is installed.

Official resources:

- Manual: https://www.gwinstek.com/en-US/products/downloadSeriesDownNew/10211/757
- Product page and USB driver: https://www.gwinstek.com/en-global/products/detail/LCR-6000

On this Windows machine the meter was detected as `USB\VID_2184&PID_005F`, but
Windows reported `CM_PROB_FAILED_INSTALL` until the GW Instek/FTDI VCP driver is
installed with administrator rights. After the driver binds, the meter should
show up under Ports as a normal `COMx` port.

Quick probe after driver installation:

```powershell
.\.venv\Scripts\python.exe scripts\probe_lcr6000.py --list
.\.venv\Scripts\python.exe scripts\probe_lcr6000.py COM7 --configure --fetch
```

## Measurement Workflow

Use **Measure empty-coil baseline** before mounting the microwire. Baseline
runs the selected LCR model/frequency/amplitude matrix with the configured
baseline readings per setting and writes a timestamped
`ac_susc_empty_coil_baseline_YYYYMMDD_HHMMSS.tsv` file next to the selected log
file. The filename intentionally does not include sample or microwire identity
because no sample is installed. Baseline does not enable, set, read, or
otherwise command the power supply.

Use **Run microwire current sweep** after inserting the microwire. The logger
uses the same selected LCR settings, configures the LCR meter first, then runs
the current loop at each AC setting. The default loop is up-down, for example
`20 mA -> 80 mA -> 20 mA`. Include `0 mA` as the start current when a
wire-installed no-current reference is needed; it is part of the microwire
sweep rather than a separate baseline action. Sweep files use an AC-specific
base such as `ac_susc_current_sweep_YYYYMMDD_HHMMSS.tsv` and are flushed after
every LCR read for overnight recovery.

Point acquisition controls are named for the AC experiment:

- **Settle time** waits after changing the current or LCR setting.
- **LCR readings/point** stores repeated LCR reads at the same
  model/frequency/amplitude/current point.
- **Read interval** optionally spaces those repeated reads.
- **Baseline readings/setting** applies the same repeated-read idea to the
  empty-coil baseline.

Every reading is saved as its own row. Averaging or baseline normalization can
be done later from the raw TSV files.

Suggested first-pass settings for the 1 cm, roughly 1 mm coil around a
Ni50Fe27Ga23 microwire:

- LCR model: `Ls-Rs`
- Optional diagnostic model: `Lp-Rp`
- Monitor 1: `Z`
- Frequencies: `100, 1k, 10k, 100k`
- Voltage levels: `0.1, 0.3, 1.0`
- Aperture: `FAST` for searching, `MED` or `SLOW` after the best region is known
- Current loop: start conservatively, for example `20 mA -> 80 mA -> 20 mA`

For each wire, run a conservative low-current sweep first to confirm the coil,
wire, and supply wiring behave normally before trying the full transition range.

`Ls-Rs` is the default and recommended normal mode. The UI exposes `Lp-Rp` only
as an optional advanced diagnostic checkbox. The `-Q` modes are not part of the
normal workflow; Q can be derived later from the logged L/R/frequency values if
needed.

## LCR-6200 Ranges

The official LCR-6000 manual gives these ranges for the lab LCR-6200:

- Frequency: continuous `10 Hz` to `200 kHz`.
- Voltage excitation: `10.00 mV` to `2.00 V` RMS.
- Current excitation: `100.0 uA` to `20.00 mA` RMS.
- Front-panel voltage increment presets: `10 mV`, `100 mV`, `300 mV`,
  `500 mV`, `1.00 V`, `1.50 V`, `2.00 V`.
- Front-panel current increment presets: `100 uA`, `500 uA`, `1.00 mA`,
  `5.00 mA`, `10.00 mA`, `20.00 mA`.

The UI includes one-click preset selectors:

- **Default subset**: `10, 20, 100, 1k, 2k, 10k, 100k, 200k Hz` and all voltage
  presets.
- **All practical frequencies**: `10, 20, 50, 100, 200, 500, 1k, 2k, 5k, 10k,
  20k, 50k, 100k, 200k Hz`.
- **All amplitudes**: `0.01, 0.1, 0.3, 0.5, 1.0, 1.5, 2.0 V`.

Because the LCR-6200 frequency setting is continuous, "all frequencies" means
the practical scan list above, not every possible value. The logger validates
entered values against the LCR-6200 range before configuring the meter.

## Overnight Sweep Output

Each AC sweep row includes:

```text
timestamp, elapsed_s, setting index/count, LCR model, frequency, level,
current_set_a, current_actual_a, voltage_actual_v, direction, repeat,
LCR primary/secondary/monitors/status/raw, PSU backend/resource/status/error
```

The writer flushes every row so a long unattended run still leaves partial data
if the PC, meter, or supply stops responding.

## Power Supply Backends

The AC sweep can use either the existing HMP4030-style SCPI path or an OWON
SPE6102-style backend. The AC panel reuses the shared top PSU controls for
supply type, serial port, and baud rate instead of asking for the same PSU
settings twice. If the shared supply is OWON SPE6102, the AC sweep selects the
OWON backend automatically and defaults the voltage limit to `62 V`. Older
saved OWON defaults such as `5 V` or `60 V` are lifted to `62 V` when OWON is
selected; non-OWON supplies keep their own lower defaults.

Use **Auto setup** to refresh LCR ports, scan serial ports with the safe
`*IDN?` query, ignore the LCR meter as a PSU candidate, and select a recognized
HMP4030 or OWON SPE6102 backend automatically. The scan does not enable output
or change current. If no supported PSU responds, the status text reports which
ports were tried and leaves the shared top PSU controls available for manual
selection.

The sweep engine treats the supply generically: connect, initialize with a
voltage limit, set current, read actual voltage/current when available, then
turn output off on normal completion, user stop, or error. Baseline measurement
does not create or command a power-supply backend.

## Live Plots

The right-side graph area is AC-specific. By default it shows:

- `Rs vs DC current`
- `Ls vs DC current`

Each plot has a selector for switching to `Ls/Rs vs current` or
`Ls/Rs vs frequency`. The labels use the measured equivalent-circuit quantities
instead of inherited current-annealing resistance-history labels.

## Literature Cues

Varga-related AC susceptibility work on shape-memory microwires points toward
tracking the change in magnetic response through the martensitic transition
while sweeping excitation conditions. The 2025 ACS Materials Au paper with
Michal Varga and Rastislav Varga uses temperature-dependent magnetization and AC
magnetic susceptibility to study glass-coated Fe-Mn-Ga microwires. Older
microwire susceptibility spectroscopy work with Rastislav Varga uses amplitude
dependence of complex AC susceptibility to distinguish magnetization processes.

Useful starting points:

- https://doi.org/10.1021/acsmaterialsau.5c00113
- https://doi.org/10.1016/j.jnoncrysol.2006.12.075
- https://www.nature.com/articles/s41598-017-19032-z
