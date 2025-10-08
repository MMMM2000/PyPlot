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
point so figures start with the first real sample, trims the abrupt dip that
appears when a wire burns through, and ramps are coloured red
while current increases and blue while it decreases to mirror the live logger.
A small smoothing pass filters out measurement jitter so the Matplotlib
outputs keep their red/blue segments even when the current wiggles while
holding at the peak. Origin exports now build the figure directly through
Origin’s embedded Python API and expose an **Origin style** selector: the
default **Experimental** mode mirrors the Matplotlib view by splitting the ramp
into rising and falling passes, colouring them red and blue with matching
markers, and trimming the legend to two entries. The **Simple** mode mirrors the
lightweight LabTalk macro by plotting a single black line+symbol trace that
carries the filename (without the `.txt` suffix) as both the legend entry and
the graph long name, then hides the populated workbook so a multi-file run
doesn’t flood the workspace with sheets. Both variants set axis labels and
titles through the API so the generated workbooks and graphs appear immediately
in the Project Explorer without relying on template-specific LabTalk, and the
text sizes honour the Readability font controls for titles, axes, ticks, and the
legend. The legend and page title now reuse the sample description with Origin
rich-text markup—element counts become subscripts and wire presets such as
`1_10` are rewritten as `1/10`—so the figure heading matches the legend entry
without manual editing, while tick styling stays on the Origin side without
triggering property warnings. Origin
connections stay attached while the plotting dialogs remain
open—detaching is scheduled for the Qt application shutdown (or immediate when
running headless)—so Python windows no longer disappear the moment an Origin
export finishes, yet the Origin application can still be closed cleanly
afterwards.
Batch runs stay resilient: the loader now accepts whitespace- or comma-separated
current logs, normalises decimal commas, and drops empty rows before plotting so
each dataset yields a clean curve.  Every run ends with a summary that lists
files that failed to parse and how many plots were generated successfully, and
when **Show plots** is unchecked the tool switches Matplotlib into a headless
mode that saves and closes each figure immediately.  Generating hundreds of
plots no longer trips Matplotlib’s “too many open figures” warning or blocks the
UI while dozens of windows try to render.
Plotting dialogs keep their
windows open after running and display settings, file list and console side by
side within a single resizable window.

The **PDF T1/T2 plotter** now adopts the same two-column control layout as the
other plotting scripts so its settings feel familiar, and its Matplotlib preview
opens at a modest 160 × 120 mm canvas by default (with centimetre and inch
options on demand) while using Matplotlib’s standard window so it matches the
rest of the plotting tools.  The figure size controls remain available for
larger exports, and new unit-aware ranges keep adjustments in practical bounds.
Saving defaults to the user’s `Downloads` folder yet still remembers the most
recent directory you chose for subsequent sessions.

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
Origin sessions are closed automatically after plots are generated—and even if a
run aborts—so the Origin application can be closed independently from the
Python tools without lingering automation locks.

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
prototype utilities such as the PyVISA annealing logger and the Microwire Data
Builder. Keep the toggle off for day-to-day work to focus on production tools
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

### Microwire data builder

`experiments/microwire_data_builder` assembles fabrication spreadsheets and
current-annealing text files into a single analytics-ready table. Enable the
**Experiments** tab from the launcher’s **Developer** menu and choose
**Microwire Data Builder** or start it directly:

```bash
python -m experiments.microwire_data_builder
```

The PyQt6 window splits the settings and status widgets on the left from the
file pickers on the right so the full control set stays visible on short
displays while still mirroring the file-picking pattern used by the existing
plotting tools:

* **Fabrication spreadsheets (.xlsx)** – add the composition workbook and any
  per-draw piece workbooks for the measurements you want to combine. The
  program maps Slovak headers to English fields, keeps a raw text copy for
  ambiguous numeric cells, and joins draw/piece data by composition and draw X /
  piece Y. Added paths are remembered between runs.
* **Microscope images (.jpg/.png)** – drop calibrated microscope captures for
  the same draw/piece identifiers and the builder OCRs the red overlay labels to
  fill the core diameter \(d\), glass diameter \(D\), and their ratio instead of
  trusting fabrication spreadsheet entries. Every diameter cell in the export is
  therefore sourced directly from microscope evidence: when OCR fails the
  spreadsheet is left blank so questionable measurements stand out, and when a
  value is produced the Excel export highlights it (with the toggle enabled) and
  links the microscope overlay automatically. Crops focus on the recognised
  overlay region when Tesseract reports bounding boxes and gracefully fall back
  to the full capture whenever the bounding box is missing so there is always a
  picture beside the diameter. The reader prioritises `*core*` images for
  \(d\) and `*glass*` images for \(D\), normalises the overlay text so `[1]`
  markers without a closing bracket (for example `"[116.7µm"`) are repaired to
  `[1] 6.7µm`, filters out the `[2]` annotations that often describe a secondary
  feature in glass captures, ignores stray scale-bar values and other
  unrealistic measurements that lack the `[1]` marker, and now scans every
  `[1]` match in a capture before committing to a value so partial `187µm`
  fallbacks no longer override later `8.7µm` readings. The OCR pass runs a
  multi-pass sweep that resizes, sharpens, binarises, and inverts captures while
  trying several page-segmentation modes, capturing bounding boxes for each
  candidate measurement so the source overlay can be cropped automatically.
  The tool also accepts draw/piece tokens written with spaces or hyphens (for
  example, `5-4` or `5 4`) in addition to the original underscore format when
  matching images to measurements and records how close each OCR result is to a
  manually verified reference list. Those references now stay in the log — if a
  capture is missing or the OCR value drifts beyond tolerance the builder emits
  a summary so you know which microscope passes still need attention without
  back-filling the spreadsheet with manual numbers.
  Install Tesseract so the OCR layer can run (`brew install tesseract` on macOS,
  `choco install tesseract` on Windows, or `apt install tesseract-ocr` on
  Ubuntu) and keep the English trained data up to date for crisp overlays (Homebrew users can run
  `brew install tesseract-lang`). The OCR pass now forces Tesseract’s LSTM engine
  in numeric mode to stabilise bracketed diameter readings. The builder now searches PATH, Homebrew/MacPorts trees,
  Windows Program Files, Chocolatey, user-level installs, and even the `.app`
  bundles that ship a private copy of the binary before giving up with a single
  warning. Setting the `TESSERACT_CMD` environment variable still overrides the
  autodetection when you have a custom install location.
* **Current-annealing files (.txt)** – add one or more three-column annealing
  logs. The loader auto-detects delimiters, validates that \(R \approx V/I\), and
  logs warnings whenever the check drifts beyond tolerance. File selections are
  restored on launch so repeat builds are quick.
* When you click **Run** the builder searches for fabrication sheets,
  microscope captures, and draw videos on a background thread. The progress bar
  advances throughout the entire job without jumping backwards when new stages
  begin: it steps through the preparation work while directories are scanned,
  continues through microscope OCR and video analysis with per-image updates,
  tracks each annealing file as the database is assembled, and finishes by
  reporting export/plot work alongside a live time-remaining estimate. The ETA
  now refreshes once per second, blends the live moving average with per-stage
  timings saved from previous sessions, and treats sudden slowdowns as sticky so
  brief OCR pauses or long exports immediately push the remaining time upward
  while later speed-ups bleed in gradually. A status line above the progress bar
  now names the active stage (for example, **Analysing microscope/video data**)
  together with its per-stage counters, and the log echoes every stage change so
  long OCR or export passes still feel alive instead of stalled. Familiar
  datasets still settle on steady remaining times after the first few runs. A
  **Cancel** button next to **Run** stops the background worker, aborts the build
  cleanly, and returns the window to an idle state once the cancellation
  propagates.
* **Fabrication videos (.mp4/.mkv/.avi/.mov)** – place draw recordings next to
  the annealing data and the builder samples one frame every 30 seconds from the
  steady-state portion of the clip (ignoring the first 50 % and final 10 %). OCR
  is applied to each sampled frame and the median temperature/underpressure is
  used to back-fill any missing fabrication readings. The video pass now also
  extracts winding speed and glass-feeding rates when the on-screen overlays
  advertise them, so the spreadsheet columns for those metrics can be populated
  directly from footage even when the fabrication sheet is incomplete. Fields
  sourced from video OCR are tracked alongside the microscope highlights so you
  can instantly see which values originated outside the fabrication workbook.
  Prefer to skip the scan on long builds? Uncheck **Extract fabrication metrics
  from videos** in the Options column and the run stays focused on spreadsheets
  and microscope evidence only.
* **Options** – the left column offers **Matplotlib plots (PNG)** to reuse the familiar
  red/blue annealing style or tick **Origin plots** to push the data to an Origin
  session using the simple single-trace template. The **Microscope review** box
  underneath keeps both toggles enabled by default: **Attach microscope crops to
  Excel** inserts two columns immediately after \(d\) and \(D\) and embeds the
  microscope evidence beside each measurement so you can validate or correct
  them in-place, and **Highlight OCR-sourced values** tints any cells where OCR
  (microscope or video) provided the measurement instead of the fabrication
  spreadsheets, covering both diameter readings and fabrication metrics harvested
  from draw videos. Disable either checkbox when you only need the numeric
  values. When you need a quick spreadsheet refresh without the extra pass over
  draw recordings, clear **Extract fabrication metrics from videos** and the
  builder skips the video search entirely. Use the **Figure width/height**
  controls to choose the Matplotlib canvas size in inches (the defaults are now
  10.0 × 6.0 in so the Excel workbook shows large, legible charts without manual
  resizing); the values are saved between runs and the axis labels, ticks,
  markers, and line widths rescale so larger canvases stay crisp. Embedded Excel
  rows and columns now track the scaled PNG size exactly, so shrinking or growing
  the figure controls immediately resizes the worksheet cells to match on both
  the XlsxWriter and openpyxl export paths without leaving spare whitespace or
  stale dimensions from previous runs. High-DPI Matplotlib renders keep their
  full resolution while Excel now honours the requested physical dimensions, so
  exported figures stay 1:1 with the configured width/height on Windows and macOS
  alike. Matplotlib still trims the burn-through
  sample that collapses to low current or spikes sharply in resistance and bridges
  the final increasing/decreasing points with a blue segment so the trace ends
  cleanly, and the measurement loader applies the same trimming so the exported
  tables no longer inherit the burn-through spike that forces the Y-axis to
  stretch. Excel thumbnails are
  scaled from the chosen figure size and embedded directly in the “Figure”
  columns instead of leaving the PNG filename behind. Selecting **Origin plots**
  alongside Excel export now drops each graph into the worksheet as an Origin
  OLE object (stored under `origin_objects/`), while CSV output continues to list
  the corresponding filenames. Choose **Export CSV** and/or **Export Excel** to
  control the output formats; every toggle, folder, and size preference is
  remembered between runs.
* **Strain worksheet** – the optional strain selector loads martensite/austenite
  length pairs and their measured shortening from the standalone worksheet. The
  builder inserts a new **Strain** column immediately after the Matplotlib figure
  fields, formats the percentage to three decimals, and carries over any `broke`
  flags so reviewers can see failed samples at a glance. When an export already
  exists the overwrite prompt now offers **Update** in addition to **Replace** and
  **Append**. Choosing **Update** refreshes the strain column (and repositions the
  Matplotlib figure fields next to \(d/D\)) inside the existing Excel/CSV files
  without rebuilding the entire workbook, preserving embedded microscope crops,
  Origin OLE objects, and any annotations made during review.

### Strain worksheet updater

The database builder now understands the strain worksheet directly, but you can
also update the worksheet without rerunning a full build. Enable the
**Experiments** tab and launch **Strain Worksheet Updater** (or run

```bash
python -m experiments.strain_worksheet_updater
```

Select the current strain workbook, the latest microwire database export, and an
output filename. The tool merges the database **Strain** column back into the
worksheet, adds rows for new composition/microwire pairs, carries over existing
martensite/austenite lengths, and flags any samples that broke during the strain
test. Rows that do not parse into draw/piece identifiers are preserved exactly
as written so reviewer notes are never lost.

### Strain Plot Explorer

When you want to inspect relationships between strain measurements, microscope
diameters, and fabrication data, open the **Strain Plot Explorer** from the
**Experiments** tab (or launch it manually):

```bash
python -m experiments.strain_3d_plotter
```

Select either a strain worksheet or a full microwire database export. The
explorer filters out any samples that broke, parses the Ni/Fe/Ga/Co percentages
from the composition, and gathers every numeric column except the raw `M length`
and `A length` inputs. File-path columns such as **File 1000 mA** and
**File Low mA** together with the embedded Matplotlib figure columns are
suppressed automatically so they never appear as axis choices. Automatic mode
generates scatter plots for every available combination that includes strain—
toggle 2D and 3D passes independently in the left-hand control pane. Manual mode
lets you pick the dimensionality and specify the exact X / Y (/ Z) axes so you
can focus on a single relationship such as strain versus draw temperature.

Controls now live in a dedicated panel on the left, keeping the plot tabs and log
console spacious on the right. Each tab hosts a large interactive Matplotlib
figure labelled with the microwire ID so you can compare shortening, diameters,
fabrication metrics, and elemental content in context. Use the worksheet’s
latest export to pick up new strain entries without tweaking plot settings—the
tool remembers the last file you opened and keeps a console log of how many rows
were plotted and which combinations were generated.

Drag the splitter handle between the plot area and the log console to resize the
view in real time. When you want to take a closer look, highlight a tab and
press **Open selected plot in new window** to spawn a maximised window; it
launches full-screen but remains resizable so you can position it on a secondary
monitor.

Switch the **Output backend** combo box to **Origin** (or **Both**) to stream
the same combinations into OriginPro. The explorer opens a workbook for each
pair or triplet, pushes the axis data alongside the microwire labels, and issues
the appropriate LabTalk command (`worksheet -t plot scatter` for 2D or
`worksheet -t plot3d scatter` for 3D). Extra worksheet columns are added before
writing the labels so Origin no longer crashes as graphs are generated. If the
`originpro` package is not available the request is logged without interrupting
the Matplotlib tabs, making it safe to use on systems without Origin installed.

The menu bar keeps **Help** as the right-most entry; open it to read an
in-window guide that walks through input preparation, export options, and
troubleshooting tips. Keep it pinned while onboarding a new operator—the dialog
remembers its last position so the reference never gets in the way of the run
controls.

Each microwire (composition + draw/piece) becomes a single row with English
headers tailored for analytics. The builder selects the 1000 mA measurement and
the lowest available current for each microwire, generates optional plots with
the familiar red/blue styling, and records provenance back to the source files.
The exported columns are:

* Composition
* Microwire (e.g. `4/1`)
* d (µm)
* D (µm)
* d/D (rounded to three decimal places)
* Length (m)
* Production datetime
* Mass (g)
* Resistance (Ω)
* Temperature (°C)
* Winding speed (m/min)
* Glass feeding (mm/min)
* Underpressure
* Notes (combined bistable status and any note fields)
* Figure — 1000 mA (Matplotlib) / File 1000 mA
* Figure — low mA (Matplotlib) / File low mA
* Figure — 1000 mA (Origin)
* Figure — low mA (Origin)
* Low mA value (mA)

 The log pane summarises skipped files, missing joins, and cases where a 1000 mA
 or low-current measurement is absent so you can investigate without re-running
 the whole build. The exported table keeps just the source and plot filenames,
 so combine them with the chosen output directory when you want to re-open the
 artefacts later. Matplotlib figures are embedded directly into the Excel sheet
 and the temporary `plots/` staging folder is deleted once the workbook is
 written.

### VSM Plot Explorer

Magnetic hysteresis runs captured with the Lakeshore VSM can now be reviewed in
bulk from the **VSM Plot Explorer** (launcher **Experiments** tab or `python -m
experiments.vsm_plotter`). Point the tool at a folder of `VSM-Hys-Data` files or
pick individual measurements, then load them into the session. Filenames are
parsed for the acquisition temperature (`T±XX`) and rotation angle (`aXXX`) so
each loop is grouped with peers recorded at the same temperature. Tokens such as
`T-30-00` are converted to `-30.00 °C`, so the dash-separated format produced by
the Lakeshore export is recognised without manual editing. These filename tokens
are consumed directly, so runs named with the standard `a000`/`T-30-00` pattern
register the expected angle/temperature without triggering the TXT-only warning.
Zero-angle runs like `a000` (and other filenames that append formatting dashes
before the next token) now resolve to a numeric angle of `0 °` instead of being
skipped. When filenames
are missing the `a`/`T` tokens, the loader now scans the header metadata (for
example the `@Filename` lines embedded by the VSM software) so rotations and
temperatures are still recovered automatically.

The loader understands both tidy column descriptions (`Column 0: …`) and the
free-form headers produced by newer VSM exports, ignoring the lengthy
instrumentation metadata that surrounds the numeric blocks. When filenames lack
tokens entirely, the parser now falls back to lines such as `Set Field Angle
to …` and `Set Sample Temperature to …`, so even noisy Lakeshore exports still
recover the rotation and temperature metadata embedded in the recipe. Those
action block values are rounded to sensible integers, so instrument readbacks
like `9.9998°` and `-30.1037 °C` surface as `10°` and `-30 °C` throughout the
UI, logs, and TXT exports. If the headers are stripped as well, the plotter
inspects the `Field Angle [deg]` and `Sample Temperature [degC]` columns and
infers a representative value from the data stream, which means runs remain
plottable even when only the raw numeric table is present. Drop the sample files
from `sample_data/VSM_data/` into the explorer to see how the parser
automatically selects the final manipulated data section and labels each column
with a friendly name. Column headings preserve
the units advertised in the Lakeshore `@@Columns` block (for example `Applied
Field [Oe]` and `Signal parallel with sample [emu]`), so the exported TXT files
import cleanly into Origin, pandas, or any other analysis suite without manual
relabelling. The inline header detection now triggers only after the
`@@End of Header.` marker, which prevents the lengthy instrument configuration
tables that precede the columns block from overriding the real column labels.

Choose your preferred backend (Matplotlib, Origin, or both) and select the axes
to plot—defaults focus on **Applied Field** versus **Signal parallel with
sample**, but every numeric column advertised in the header is available. Set the
temperature filter to `All temperatures` to build a tab per temperature, each
containing every angle trace for that run, or pick a specific temperature to
concentrate on a single group. The Matplotlib tabs appear on the right-hand side
of the window with a console log below; they are fully resizable and inherit the
project’s dark/light theme settings.

OriginPro exports mirror the same grouping, creating a workbook for each
temperature, writing a sheet per angle with the selected axes, and building a
line graph that overlays every angle trace with a labelled legend. A dedicated
**Export TXT** button also writes the parsed data to plain tab-separated files
with the detected column names, so Origin (or any analysis tool) can import the
clean tables without the surrounding instrumentation metadata. Even if a run is
missing angle/temperature metadata, the loader still enables TXT export while
noting that plotting is disabled, making it possible to salvage data from noisy
recipes. If Origin is not installed the exporter logs a short reminder while
leaving the Matplotlib tabs intact, so the workflow stays consistent on
machines without Origin.

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

