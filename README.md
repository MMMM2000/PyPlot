# Microwire Data Plotting & Logging

Tools for measuring and visualising data from microwire experiments.  The
repository provides loggers for serial devices and VISA instruments, a generic
data logger and a small launcher that groups all utilities.

## 1. Installation

All commands assume a terminal opened in the repository root.

### 1.1 Windows

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
python -m venv .venv
.\.venv\Scripts\Activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -U pyvisa-py    # optional: update VISA backend
```

### 1.2 macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt
pip install -U pyvisa-py    # optional: update VISA backend
```

### 1.3 Additional tools

The project occasionally uses the Codex CLI for development helpers:

```bash
npm install -g @openai/codex
```

The requirements include `pyvisa`, `pyvisa-py`, `psutil` and `zeroconf` so VISA
resources can be discovered without vendor drivers.

## 2. Master launcher

Run the launcher to access all utilities in a single window:

```bash
python -m launcher
```

Closing the launcher warns about other open windows and will close them.  Plot
settings dialogs can now be closed independently without shutting down the
launcher, so you can move between tools without relaunching the hub.  Origin
control is released immediately after each run, so closing the Temperature
Sensitivity settings while Origin remains open no longer tears down the
launcher window.

The lists on each tab open sorted by the most recently launched tool so the
scripts you rely on are always at the top.  A new **Sort** menu lets you flip to
alphabetical ordering in either direction at any time, and a search bar above
the tabs filters entries across loggers, plotters, emulators and experiments as
you type, making it easy to jump straight to the utility you need.

Keyboard shortcuts make it just as quick to launch tools without reaching for
the mouse.  Use the **Up/Down** arrow keys to move through the current list, tap
**Left/Right** to jump between tabs, and press **Enter** from anywhere in the
window (including the search box) to run the highlighted script immediately.

Plotting scripts opened from the **Plotting** tab keep their settings dialogs
open after generating figures.  Each dialog lists the selected input files and
places **Add Files/Folders**, **Remove Selected**, and **Remove All** buttons above
a wide, non‑scrolling list so datasets can be refined without restarting the
tool.
Settings panels wrap content vertically and disable horizontal scrollbars for a
clean, uncluttered layout. Each settings window embeds a small console and
places it next to the file list in a side panel beside the plot options, so the
terminal never needs to span the full window width.  The file list wraps long
paths to new lines, and file dialogs remember the last directories used—
maintaining separate input and output locations.  If no history exists, they
fall back to the repository’s `sample_data` folder for inputs and the user’s
`Downloads` folder for outputs.  When saving, an optional **Create subfolder** checkbox stores figures under `<script name> data
YYYY-MM-DD` for easier organisation.  Export options include a choice of PNG,
PDF, or SVG format with a configurable DPI (PNG defaults to 1200 dpi).  Each
plotting dialog remembers its most recent backend selection and PNG DPI
independently, so Matplotlib/Origin toggles and export resolution reopen the way
you left them.  The current annealing plotter also omits the initial 0 mA data
point so figures start with the first real sample, and ramps are coloured red
while current increases and blue while it decreases to mirror the live logger.
Origin stays open throughout multi-file exports and names each graph after the
source file (without the `.txt` suffix), so batching datasets no longer causes
Origin to reopen repeatedly.
Plotting dialogs keep their
windows open after running and display settings, file list and console side by
side within a single resizable window.

Outlier detection remains opt-in.  Toggling **Remove automatically** no longer
forces the outlier check to start immediately, and the preference is remembered
per plotting script so each tool can keep its own automatic-removal default.

Each plotting dialog now provides a full **Readability** section with controls
to toggle titles, axis labels, tick labels, and legends, adjust their font
sizes, choose legend orientation, pick whether the legend lives inside the
axes or just outside the figure, show or hide legend symbols, and optionally
match legend text colours to the curves they describe.  The master "Improve
readability" toggle has been removed, so the adjustments are always active and
take effect immediately.  All readability preferences are remembered between
runs, and the legend orientation selector now flips between single-column and
single-row layouts so horizontal legends behave correctly.  The
Maxion plotter adds
optional ×10³/×10⁴ axis scaling and a switch to centre the Y axis on its median
(from raw or processed data).

Every window now includes a shared menu bar.  The **View** menu exposes theme
controls (System/Light/Dark), toggles the file browser or console panes, and
resets splitter sizes if the layout becomes cramped, while **Help** opens a
Markdown guide tailored to the current tool.  When onboarding new colleagues you
can point them to the menu entry for context without having to maintain a
separate manual.  The main action row stays anchored beneath the settings so
Run/Plot buttons remain visible without scrolling through long option lists.
Origin sessions are closed automatically after plots are generated so the Origin
application can be closed independently from the Python tools.

Developer notes: the Qt overrides now accept optional `QPaintEvent`/`QCloseEvent`
arguments to match the PyQt6 stubs, Origin helpers coerce LabTalk worksheets
and plots to dynamic objects before assigning colours or symbols, and pandas
series are converted to NumPy arrays before histogramming so Pylance no longer
flags our plotting pipelines. The PyVISA annealing logger also checks that
`styleHints()` is available before reading the system colour scheme, and the
Maxion controls guard against layouts that omit `rowCount()`. Hysteresis and
load-compare exporters likewise treat Origin handles dynamically, and GUI
wrappers invoke plotting routines through small closures so helpers expecting
`Callable[[], None]` remain satisfied even when plotters return figures.

The menu bar also adds a **Developer** section. Enable **Keep File Selections**
to reopen plotting dialogs with the same files pre-selected—handy when you are
tweaking settings over multiple runs. Toggle **Show Experiments Tab** to expose
prototype utilities such as the PyVISA annealing logger and the liquid-glass UI
concept. Keep the toggle off for day-to-day work to focus on production tools
only.

Temperature-sensitivity plots now match between Matplotlib and Origin: each
sample is annotated beneath the axis with its microwire ID (`2/1`, `2/2`, …)
and the labels track the 25 °C mean positions so the workbook and figure stay
in sync even when continuous traces nudge markers sideways. Legends share the
same ordering and colour coding, symbol visibility follows the **Show symbols**
option, and raw traces advertise `25 °C` instead of `25.0 °C`.  Origin
exports disable speed mode, enable anti-aliasing, shrink raw scatter markers to
size 1, reuse the Matplotlib graph title while centring it along the top edge,
drop the vertical connector bars, and stamp the Δ(100 °C−25 °C) annotations
directly above the 100 °C means, while the Matplotlib legend sits outside the
axes to avoid covering data points.  The default moving-average window now
spans 200 samples for smoother continuous traces straight out of the box, and
the Origin backend applies these tweaks through the Python API so axis labels
no longer rely on LabTalk scripting.

## 3. Loggers

### 3.1 Serial Current Annealing Logger

Connects to an HMP4030 power supply via a serial port.  Features include:

* configurable current ramp with optional automatic reversal
* **Reverse current now** button for an immediate ramp down
* Start/Stop and Reverse controls stay pinned beneath the settings so they remain
  visible without scrolling
* all UI elements and internal variables now use English identifiers for easier maintenance
* Cancelling a start when the selected log file already exists keeps the naming
  controls active so you can adjust the path before retrying
* default **Reverse to zero after max** behaviour
* remembers the last log directory and file separately from input paths
* remembers the last max-current setting and keeps serial controls adjustable after connection
* reapplies the saved max-current limit on launch so the first run honours the configured peak without nudging the control
* the **Sample** field now behaves like a spin box with built-in up/down arrows, matching the other numeric inputs while still honouring the keyboard arrows to bump the `s` index without retyping the name
* the **Hold current now** button spans the main settings columns so its label never collides with the **Step** control
* the process settings grid packs the directory and file pickers alongside their action buttons and pairs the ramp controls across two rows, eliminating the wide blank column and keeping related inputs together
* mode selection lives under **Settings → Mode of operation**, keeping the primary pane focused on run parameters while the shortcuts stay available in the menu bar
* the redundant **Elapsed** readout has been removed to avoid confusion—the hold-resistance percentage continues to track dwell progress in manual mode
* the **Composition** and **Microwire** fields remember the five most recent entries (shared with the serial data logger) and cycle through them with the Up/Down keys
* optional infinite looping displays "∞" and locks the loop count
* configurable response when the supply hits **30 V** (hold, reverse, stop, or ask each time)
* live projection of how long remains until the supply reaches 30 V plus the
  estimated current at that limit, shown alongside the main time estimate
* progress and remaining-time calculations shrink automatically when the
  30 V limit triggers an early reverse so the progress bar reflects the shorter
  descent back to zero
* live display of set current, measured current and voltage
* streamlined start-up sequence that begins logging immediately
* plots of resistance vs. current and sample number that follow the system theme
* briefly shows zero-current placeholders on the live plot when the run begins so you can confirm data is arriving, then removes them as soon as real measurements start while still ignoring the later sudden 0 mA readings that signal a burnt wire
* contact-loss detection waits until the logger has measured a non-zero current,
  then applies a short start-up grace period and requires multiple zeros spread
  over a short delay before stopping, so start-up ramps and momentary dips no
  longer trip the burn-out warning
* serial polling keeps track of the last response time and extends the wait
  before flagging "no response" so the initial ramp commands are no longer
  mistaken for a disconnected supply

Launch from the master launcher or run

```bash
python -m data_logging.current_annealing_logger.current_annealing_logger
```

### 3.2 PyVISA Current Annealing Logger (experimental)

The PyVISA variant now lives under the launcher’s **Experiments** tab. Enable
the tab from **Developer → Show Experiments Tab** to expose it. The GUI mirrors
the serial logger feature-for-feature—configure peak current, step, interval,
dwell, and loop count (including an infinite option) while the time estimate
updates live. The voltage-limit dialog, **Reverse now** button, and contact-loss
guard all behave like the serial tool, and the ramp-down trace is plotted in a
contrasting colour. Select a VISA resource (e.g. `ASRL/ttyV1::INSTR`), choose
where to log, and click **Start annealing**—the first zero sample is skipped
automatically. The logger remembers its last log directory and file name and
shares the same menu shortcuts for themes and layout tweaks.

Run it directly with

```bash
python -m experiments.pyvisa_current_annealing_logger
```

### 3.3 Generic Data Logger

Records arbitrary measurements to structured text files with a built‑in file
name builder.  Real‑time plots update while logging and match the system theme.
Recent updates include:

* the composition and microwire fields share the same five-entry history as the current annealing logger, so pressing Up/Down cycles through the most recent experiment names without retyping
* history tracking persists between runs, making it easy to repeat a series of measurements with consistent naming

## 4. Virtual COM‑port emulator

To test the loggers without hardware start the emulator:

```bash
python -m emulators.virtual_serial_emulator_gui
```

Use **Create Pair (socat)** on Linux/macOS to create `ttyV0` ↔ `ttyV1`.  On
Windows supply a pre‑installed virtual pair or enable the `loop://` option for a
software loopback.  Point the emulator at one side of the pair and the logger at
the other.  All annealing tools default to **115200 baud**.

## 5. Building executables

PyInstaller specifications are provided.  After installing the requirements run

```bash
pyinstaller launcher.spec
```

to create a standalone `dist/launcher` for the current platform.  The
specification bundles the plotting configuration automatically, so no manual
data copying is required before running the command.  When preparing a build for
colleagues:

1. Activate the virtual environment and install everything from
   `requirements.txt` to make sure the frozen app includes the same library
   versions you tested with.
2. Run `pyinstaller launcher.spec` and wait for the `dist/launcher` folder to be
   produced.  This folder contains the executable and all runtime resources.
3. Launch `dist/launcher/launcher.exe` (or the platform equivalent) on your
   machine to smoke-test the build before distributing it.  The help buttons in
   each window double as an onboarding guide.
4. Zip the entire `dist/launcher` directory when sharing the tools so recipients
   can extract and run the launcher without installing Python.

## 6. Experiments

Prototype user interfaces and plotting experiments live in the `experiments`
directory and are independent from the main tools.

### PyVISA current annealing logger

`experiments/pyvisa_current_annealing_logger.py` mirrors the serial logger while
talking to VISA instruments. Enable the Experiments tab from the launcher’s
**Developer** menu to access it, or run the module directly when you want to
exercise VISA hardware without altering the production launcher.

### Liquid glass UI demo

`experiments/liquid_glass_gui.py` embeds the PyVISA annealing logger inside a
macOS 26-inspired glass workspace. Launch the classic logger from the buttons in
the window to compare the production interface against the translucent skin, or
open the serial logger for extra context. Use the concept to gather feedback
before enabling a "liquid glass" appearance toggle in the main tools.

## 7. Repository maintenance

* Temporary Origin HTML exports and scratch files are removed from the
  repository so fresh clones only contain the code, configuration, and sample
  data required to run the tools.
* The outdated Visual Basic logger has been deleted; the modern PyQt loggers are
  the supported workflow going forward.

## 8. Testing

Run the test suite after installing the requirements:

```bash
pytest -q
```

