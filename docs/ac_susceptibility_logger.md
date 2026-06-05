# AC Susceptibility Logger

The AC Susceptibility Logger is a focused two-step tool for coil-based AC
susceptibility runs with a GW Instek LCR-6200/LCR-6000 series meter and a DC
current source:

1. **Measure empty-coil baseline** with no microwire installed.
2. **Run microwire current sweep** after inserting the wire, using the same LCR
   frequency/excitation settings while sweeping DC current up and back down.

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
runs the selected LCR model/frequency/excitation matrix for the configured
measurement time per point and writes a timestamped
`ac_susc_empty_coil_baseline_YYYYMMDD_HHMMSS.tsv` file next to the selected log
file. The filename intentionally does not include sample or microwire identity
because no sample is installed. Baseline does not enable, set, read, or
otherwise command the power supply.

Use **Run microwire current sweep** after inserting the microwire. The logger
uses the same selected LCR settings, configures the LCR meter first, then runs
the current loop at each AC setting. The default loop is up-down, for example
`20 mA -> 80 mA -> 20 mA`. Enable **Also measure 0 mA reference** when a
wire-installed no-current reference is needed before the PSU current loop; the
0 mA point is part of the microwire sweep rather than a separate baseline
action, and avoids asking the OWON to regulate currents below its useful
minimum. Sweep files use an AC-specific base such as
`ac_susc_current_sweep_YYYYMMDD_HHMMSS.tsv` and are flushed after every LCR read
for overnight recovery.

Use **Continue from previous sweep...** to restart after an interrupted
microwire sweep. Select one or more previous sweep TSV files, keep the current
UI plan set to the intended full sweep, and the logger compares the selected
files against that plan. AC settings whose full current loop was already
recorded are skipped. Any partially recorded AC setting is measured again from
its first current point, then the missing settings continue into a new
timestamped TSV file.

Use **Open** and **Short** under **Fixture correction** when the LCR fixture,
clips, coil wiring, or cable routing changes. These actions run the LCR-6000
series full-range AC open/short correction commands only; they do not lease,
enable, disable, or otherwise command the PSU or shared HMP broker. Open
correction is done with the fixture connected but no DUT attached. Short
correction is done with the measurement terminals shorted at the fixture
contacts. The LCR-6000 manual describes full open/short correction as measuring
the meter's preset trimming points and interpolating other frequencies, so the
correction is separate from the logger's selected frequency chips. Use
**Disable** to turn the stored LCR open/short correction state off without
clearing the logger's empty-coil baseline workflow.

The AC Susceptibility Logger keeps its output directory and sweep-base setting
separate from the Current Annealing Logger. By default, AC files go under
`Downloads/ac_susceptibility` with the sweep base `ac_susc_current_sweep`.

Point acquisition controls are named for the AC experiment:

- **Settle time** waits after changing the current or LCR setting.
- **Measure time/point** controls how long the logger keeps fetching LCR data
  at each setting. The default is `10 s`, and the same duration applies to
  empty-coil baseline settings and microwire
  model/frequency/excitation/current points. The actual number of rows depends
  on how fast the LCR meter responds at that condition.
- The run estimate shows separate empty-coil baseline and microwire sweep
  durations, plus the local clock time when each run would finish if started
  now. The estimate uses the selected settle time and a rough LCR-read
  allowance; real serial communication overhead can still add time.
- During a run, the sticky task line reports the active LCR model, frequency,
  excitation level, read number, and microwire current when applicable. The progress
  bar reports elapsed/total measurement time, estimated time remaining, and
  the expected finish clock time/date.
  Empty-coil baseline readings are also added to the live plots as 0 mA points
  so the dashboard visibly updates before a microwire sweep is started.
- Acquisition and file writing run in a worker thread, separate from the
  PyQtGraph dashboard. Each reading is flushed to disk before the UI plot buffer
  is updated, and plot refreshes are throttled to about once per second so graph
  rendering cannot slow the LCR logging loop.
- The Stop button stops after the current LCR read. Empty-coil baseline stops
  save a partial TSV with the rows already collected. Baseline rows are flushed
  as they are measured, matching the microwire sweep behavior, so a PC or
  instrument interruption still leaves the completed rows on disk.
- The Developer menu can mirror AC diagnostics to a JSONL file. Those records
  include task changes, plot refresh timing, displayed-point counts, and UI
  timer telemetry, which is useful when checking whether the four-panel
  dashboard is slowing down the PC.

Every reading is saved as its own row. Averaging or baseline normalization can
be done later from the raw TSV files.

Each generated TSV begins with commented metadata lines. Both empty-coil
baseline and microwire sweep files include a compact `config_json` snapshot with
the selected LCR settings, acquisition timing, current-loop points and
directions, the last queried LCR open/short correction state, and, for current
sweeps, the selected PSU backend/resource/voltage limit and retry settings. This
makes partial or overnight files self-describing for debugging even if UI
settings are changed later.

Suggested precision-baseline settings for the 1 cm, roughly 1 mm coil around a
Ni50Fe27Ga23 microwire:

- LCR model: `Ls-Rs`
- Optional diagnostic model: `Lp-Rp`
- LCR excitation: current mode, starting with all front-panel current presets,
  `0.1, 0.5, 1, 5, 10, 20 mA`
- Monitor 1/2 in current excitation mode: `IAC` and `VAC`, so the actual AC
  coil drive can be recovered from the raw LCR monitor fields.
- LCR setup state: auto range on, auto LCZ off, source resistance `30 ohm`,
  ALC on, DC bias off, and comparator off. The logger applies these explicitly
  at each LCR setting instead of inheriting the front-panel state.
- Frequencies: the full practical scan list, `10, 20, 50, 100, 200, 500, 1k,
  2k, 5k, 10k, 20k, 50k, 100k, 200k Hz`
- Voltage excitation mode remains available for comparison or legacy runs, with
  presets `0.01, 0.1, 0.3, 0.5, 1.0, 1.5, 2.0 V`.
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

The UI shows the standard frequency and excitation presets as checkable chips
so common scan matrices can be edited without retyping comma-separated lists.
Use **Custom lists** when an odd frequency or excitation value is needed; the
custom text fields remain the source of truth saved into run metadata.

The UI includes one-click preset selectors:

- **Default full scan**: the full practical frequency scan and all presets for
  the selected excitation mode.
- **All practical frequencies**: `10, 20, 50, 100, 200, 500, 1k, 2k, 5k, 10k,
  20k, 50k, 100k, 200k Hz`.
- **All currents** in current excitation mode: `0.1, 0.5, 1, 5, 10, 20 mA`.
- **All voltages** in voltage excitation mode: `0.01, 0.1, 0.3, 0.5, 1.0,
  1.5, 2.0 V`.

Current excitation is the default because the coil field is set by coil current,
not by the LCR source voltage. Bare current values in the UI are interpreted as
mA, so `1, 5, 20` means `1 mA, 5 mA, 20 mA`; explicit suffixes such as
`500 uA`, `5 mA`, or `0.02 A` are also accepted. In voltage mode, bare values
remain volts.

Because the LCR-6200 frequency setting is continuous, "all frequencies" means
the practical scan list above, not every possible value. The logger validates
entered values against the LCR-6200 range before configuring the meter.

## Overnight Sweep Output

Each AC sweep row includes:

```text
timestamp, elapsed_s, setting index/count, LCR model, frequency, level mode/level,
current_set_a, current_actual_a, voltage_actual_v, PSU resistance, PSU power,
direction, repeat, LCR primary/secondary/monitors/status/raw,
PSU backend/resource/status/error
```

The writer flushes every row so a long unattended run still leaves partial data
if the PC, meter, or supply stops responding.

For microwire current sweeps, PSU resistance is calculated from the power-supply
readback as `voltage_actual_v / current_actual_a` when the actual current is
non-zero. This is a diagnostic of the DC current path and wire/contact state,
separate from the LCR `Rs` value.

The logger polls `FETC:IMP?` and records every valid reply it receives during
the configured time window. A complete file with no empty replies means no
requested fetch failed; it does not mean every internal LCR conversion was
captured if the meter converted faster than the serial polling loop.

For unattended runs, the logger also watches for the abnormal high-frequency
FAST-mode state observed on the bench, where the LCR meter keeps returning
valid readings but only at a few readings per second instead of the expected
FAST cadence. For settings at `1 kHz` and above, if the early read cadence is
below the retry threshold, the logger records a warning, reconfigures the same
LCR setting, discards a short recovery window, and retries automatically. If the
slow state persists after the bounded retries, the file keeps the warning and
the logger still measures the requested point duration at the slower cadence
instead of waiting for operator confirmation or truncating the setting.

The LCR `comparator/status` field is the meter's bin/comparator result. Empty
coil runs can legitimately show values such as `OUT,AUX-NG,NG` while still
returning valid `Ls` and `Rs` readings; this is not treated as a communication
failure unless the raw LCR reply is empty or cannot be parsed.

## Power Supply Backends

The AC sweep can use the existing HMP4030-style SCPI path, an OWON
SPE6102-style backend, or the opt-in shared HMP broker profile. The AC logger keeps its own supply profile, serial port,
baud rate, and voltage-limit settings, separate from the Current Annealing
Logger. It also remembers hardware settings per AC supply profile, so switching
between OWON and HMP restores that profile's last port, baud rate, and voltage
limit instead of overwriting one shared PSU slot. If the AC current supply is
OWON SPE6102, the AC sweep selects the OWON backend automatically and defaults
the voltage setpoint to `61 V`. The bench
SPE6102 is nominally a 62 V supply, but its SCPI voltage setpoint accepted
values up to `61 V`; sending `62 V` left the setpoint at zero on the tested
unit. Older saved OWON defaults such as `5 V`, `60 V`, or `62 V` are lifted or
lowered to `61 V` when OWON is selected; non-OWON supplies keep their own lower
defaults.

Use **Auto-connect hardware** as the normal setup action. It refreshes LCR ports,
connects to the detected LCR-6200, scans serial ports with the safe `*IDN?`
query, ignores the LCR meter as a PSU candidate, and selects a recognized HMP4030
or OWON SPE6102 backend automatically. The scan does not enable PSU output or
change current. COM ports, baud rate, and backend selection are kept in a
collapsed hardware-details panel for troubleshooting, but they are not part of
the normal workflow. If no supported PSU responds, the status text reports which
ports were tried and leaves the advanced hardware controls available for manual
selection. If the AC current-supply connection is already open, auto-connect
trusts that connected selection instead of trying to open the same COM port a
second time.

The logger no longer runs PSU auto-detection during normal launch. This avoids
waiting on serial ports that do not answer `*IDN?`; probing only happens when
the operator explicitly asks for auto-detection.

The sweep engine treats the supply generically: connect, identify the selected
SCPI backend, initialize with the selected voltage limit, set current, and wait
briefly for actual-current readback before starting LCR reads at each current
point. HMP4030/HMP4040-style direct serial and shared-broker runs keep the
configured voltage limit available for the whole sweep and change only the
current setpoint at each current point, matching the Current Annealing and Mini
DMA logger HMP pattern. For supplies that expose voltage control but are not
HMP-style current sources, especially the OWON SPE6102, the voltage limit is
treated as an automatic compliance value rather than a fixed experiment
setting. Before each current point the logger estimates a reasonable voltage
from the last measured wire resistance, then trims the voltage from PSU readback
until the measured current is close to the requested current. If the requested
current cannot be reached before the short ready timeout, the run logs a `WARN`
row and continues with the measured current rather than stopping the overnight
sweep.

The logged `current_actual_a`, `voltage_actual_v`, PSU resistance, and PSU power
columns are the source of truth for later analysis; the requested current is
recorded separately as `current_set_a`. A missing actual-current readback during
a 0 mA reference, or a brief missing readback after the requested non-zero
current has already been accepted, is logged as `WARN` and the run continues.
If the supply never returns actual-current readback before the point starts, the
run aborts because the logger cannot know whether output is active. On normal
completion, user stop, or error, the shutdown sequence sets current and voltage
to zero before turning output off. If the existing serial handle fails during
shutdown, the logger closes and reopens the selected PSU port once and repeats
the zero-current, zero-voltage, output-off sequence. During active microwire
current sweeps, the app refuses ordinary window close requests until the worker
has stopped cleanly. It also starts a detached PSU watchdog that watches the GUI
process and a heartbeat file; if the parent process disappears or the heartbeat
stops updating, the watchdog reopens the selected PSU port and sends the same
zero-current, zero-voltage, output-off sequence. During active microwire current
sweeps on Windows, the worker also requests that the system stay awake so USB
serial connections are not suspended mid-run. Baseline measurement does not
create or command a power-supply backend.

When **Shared HMP broker** is selected, the serial port is replaced by broker
host, TCP port, and confirmed HMP channel settings. The logger leases that
channel with the `ac_susceptibility` role, sends voltage/current/output commands
through the broker, reads back only that channel, and turns off/releases only
that leased channel when the sweep stops or fails. The detached serial watchdog
is not armed in broker mode because it cannot safely address a broker lease;
the worker shutdown path performs the broker channel shutdown/release instead.
The broker channel starts as **Select channel...** on first use, and a sweep will
not start until the operator explicitly chooses the wired HMP channel. On the
current shared bench, choose `CH1` for AC susceptibility; Mini DMA uses `CH3`
for its motor rail and `CH4` for its current-sweep channel. Channels saved by
older development builds before this confirmation marker are treated as
unconfirmed and must be selected again.

Shared-broker bench setup still has to be confirmed outside the AC logger with
the shared HMP setup/guard tools before a live sweep. The AC logger does not
auto-start hardware or infer channel wiring.

## Bounded LCR Debug Stream

Enable **Write LCR debug stream** only when debugging transitions or LCR cadence
around a current step. The logger writes a sidecar
`<sweep>_lcr_debug.jsonl` next to the main sweep TSV. The stream is bounded by
cadence and a maximum row count per setting/current/phase, and is flushed after
each row. It records cadence-sampled settle-window and measurement-window rows
with the LCR setting, current setpoint, PSU readback, broker metadata when used,
aggregation metadata, and a close record with the stop reason.

The default debug cadence is `1 s` and the default cap is `120` rows per
setting/current/phase. Debug logging is off by default so ordinary overnight
sweeps keep their existing TSV output and plot behavior.

MED/SLOW aperture and LCR AVG choices should be tuned on live hardware. A safe
bench comparison plan is:

1. Run the same current transition with FAST, MED, and SLOW at the same
   frequency/excitation/current loop while the debug stream is enabled.
2. Repeat the most promising aperture with candidate AVG counts if the front
   panel/SCPI setup supports averaging without hiding the transition.
3. Compare debug-stream cadence, point-to-point scatter, and transition shape
   against the extra measurement time before changing defaults.

## Live Plots

The right-side graph area follows the Mini DMA dashboard pattern: use
**Configure plots** to choose which plot tiles are visible and what each tile
uses for bottom X, left Y, optional right Y, and optional far-right Y.
Live plots intentionally separate the raw time trace from parameter summaries
so overnight sweeps do not spend minutes redrawing old points. The TSV file
still contains every acquired LCR and PSU readback row.

By default it shows:

- `Rs + Ls vs elapsed time`
- `Rs + Ls + wire resistance vs measured current`
- `Rs + Ls vs frequency`
- `Rs + Ls vs LCR excitation current`

Current plots distinguish `Current measured [mA]` from `Current set [mA]`.
Measured current is the default X axis because it is the physical value returned
by the power supply. The legacy saved `DC current` plot key is interpreted as
measured current when old settings are loaded.

The live dashboard uses PyQtGraph so it can update persistent plot objects
instead of rebuilding a Matplotlib figure. The raw TSV logging path is separate
from the dashboard: the logger can keep writing every valid LCR/PSU read while
the display shows reduced views for responsiveness.

Additional selectable channels include elapsed time, measured current, set
current, frequency, voltage amplitude, LCR excitation current, `Rs`, `Ls`, wire resistance, and PSU power. The
plot renderer follows the Qt palette so dark mode labels, ticks, and titles
remain readable.

Elapsed-time plots are raw recent traces. All other plot X axes use a deeper
history window and show one median point for each
model/frequency/excitation/current setting, which keeps dense parameter scans
readable without hiding the complete raw TSV data. Plot gridlines are hidden to
keep dense multi-axis views cleaner. `Rs` and `Ls` are scatter-only by default
and use separate Y scales. Wire resistance is drawn as line plus symbols when
selected because it follows the DC current path through the microwire and
contacts, using the same per-condition median reduction so noisy PSU readback
does not dominate the dashboard.

When frequency is selected as the X axis, the logger uses a logarithmic scale.
Current, frequency, and amplitude scatter plots can use display-space
horizontal spread for repeated X values so dense repeated points do not collapse
into a single vertical stripe. Configure the spread from **Configure plots**;
the default is **Small**, and **Off** keeps exact stacked positions. The spread
is deterministic and based on screen pixels rather than the numeric data range,
so it improves readability without changing the logged values. The median
reduction is display-only; the TSV logging remains complete.
Combined plots use colored Y-axis labels/ticks instead of in-plot legends,
keeping dense traces readable while still identifying left, right, and
far-right axis data.

When AC diagnostics mirroring is enabled from the Developer menu, the logger
writes plot redraw timing and lightweight UI timer telemetry. These diagnostics
help confirm that plot refresh work stays separate from the acquisition/logging
loop during long runs.

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
