from __future__ import annotations

from textwrap import dedent
from typing import Optional

from PyQt6 import QtCore, QtWidgets


_HELP_CONTENT: dict[str, dict[str, str]] = {
    "default": {
        "title": "Microwire tools",
        "body": dedent(
            """
            ### Quick start for first-time users
            1. Install the requirements (``pip install -r requirements.txt``) and launch the
               hub with ``python -m launcher``.
            2. Leave the launcher running. It keeps track of every child window so you can
               open a logger, switch to a plotter, or return to the experiments without
               restarting the program.
            3. Whenever you open a dialog, pull down **Help → View Help** to keep this guide
               beside you. The help viewer is specific to the tool you opened.

            ### Understanding each window
            * **Left column – file browser.** Add or remove datasets, drag the splitter to gain
              room, or hide the pane entirely from **View → Show File Browser** when you just
              want to focus on options.
            * **Right column – configuration.** Options are grouped into collapsible sections
              (readability, export, backends). Scroll the panel; the **Run/Plot** row is pinned
              just below the options so it never disappears off-screen.
            * **Console – live status.** Messages from file parsing, instrument polling, and
              export steps appear here. Clear the console between runs to keep logs readable.
            * **Double-click** any file in the list to open it in Explorer/Finder for quick
              inspection.

            ### Develop menu essentials
            * **Keep File Selections** – when iterating on a dataset, enable this so the dialog
              reopens with your previous file list intact.
            * **Show Experiments Tab** – exposes prototypes such as thermal-camera viewing,
              conversion helpers, and folder exporters. Leave the toggle off during routine work
              to keep the launcher tidy.

            ### Menu bar highlights
            * **File → Open File… / Open Folder…** call the same loaders as the toolbar buttons
              and remember your most recent paths. Use **Close Window** or **Quit** when you are
              done.
            * **Edit → Undo / Redo / Cut / Copy / Paste / Select All** target whichever widget
              currently has focus, so you can edit text boxes or tables without leaving the
              keyboard.
            * **View → Theme** flips between system, light, and dark palettes across every open
              window. Changes apply instantly, even to existing loggers.
            * **View → Reset Layout** restores splitter positions if you shrink a pane too far.
            * **Window** mirrors native macOS and Windows behaviour—minimise, zoom, move/resize,
              enter full screen, jump between open tools, or bring them all to the front.
            * **Help → View Help** keeps this step-by-step manual at hand for whichever dialog
              you are learning.

            ### Typical workflow
            1. Choose or load input files with **Add Files/Folders** (or type a VISA resource in
               loggers).
            2. Walk through the configuration sections from top to bottom—backend selection,
               export directories, readability controls. Settings persist per tool.
            3. Start the run. Watch the console for prompts (missing files, contact-loss
               warnings, Origin export status).
            4. Adjust the theme or enable experiments from the menu bar without closing the
               window, then iterate on the same dataset using the retained file list.

            ### Origin integration
            * OriginPro maintains its own Python environment. Open **Connectivity → Python
              Packages** inside Origin and install the automation stack (`originpro`, `numpy`,
              `pandas`, `python-dateutil`, `pytz`, `six`, `tzdata`) before selecting the
              **Origin** backend in any tool.
            * The launcher's console logs a reminder if those packages are missing, but Origin
              exports will only appear once the embedded environment has been prepared.

            Installation, hardware wiring, and packaging notes live in the project README. This
            in-app help focuses on day-to-day operation once the environment is set up.
            """
        ).strip(),
    },
    "launcher": {
        "title": "Master launcher",
        "body": dedent(
            """
            ### Using the launcher
            1. Pick a tab to browse available tools. **Loggers** covers production data
               acquisition, **Plotting** lists the figure generators, **Emulators** hosts the
               serial bridge, and the optional **Experiments** tab exposes prototypes.
            2. Highlight a utility in the list and click **Run**. The launcher keeps running
               while the selected window opens so you can return here without relaunching the
               program. New windows are tracked automatically and the launcher warns you if
               closing it would also close child dialogs.
            3. Use **Develop → Show Experiments Tab** to reveal or hide the prototype list and
               **Develop → Keep File Selections** if you want plotting dialogs to reopen with
               the same input files pre-selected. The experiments currently bundle prototype
               hardware viewers, conversion helpers, and folder exporters.
            4. The **View** menu mirrors other windows—switch theme, collapse the file browser or
               console, and reset splitter sizes when needed. **File → Exit** quits the launcher
               after confirming there are no unsaved child windows.
            """
        ).strip(),
    },
    "experiment_thermal_camera_viewer": {
        "title": "Thermal camera viewer",
        "body": dedent(
            """
            ### NUCLEO-H753ZI wiring
            Power the Nucleo from its ST-LINK USB connector and turn power off before
            rewiring the sensor. Connect only one thermal module to the checked-in
            firmware at a time.

            **Dratek MLX90614ESF-DCI spot thermometer module** (`0x5A`,
            STM32Cube MLX90614 probe firmware):

            * Nucleo **3V3** -> module **VCC/VIN** input that accepts 3.3 V
            * Nucleo **GND** -> module **GND**
            * Nucleo **D14 / PB9 / I2C1_SDA** -> module **SDA**
            * Nucleo **D15 / PB8 / I2C1_SCL** -> module **SCL**

            **MLX90640 110 degree, 32 x 24 thermal camera module** (`0x33`,
            STM32Cube MLX90640 raw-stream firmware):

            * Nucleo **3V3** -> camera **VCC/VIN** input that accepts 3.3 V
            * Nucleo **GND** -> camera **GND**
            * Nucleo **D14 / PB9 / I2C1_SDA** -> camera **SDA**
            * Nucleo **D15 / PB8 / I2C1_SCL** -> camera **SCL**
            * Leave **INT**, **AD**, or other optional breakout pins unconnected unless
              you are using a different firmware that explicitly needs them.

            Do not power these modules from **5V** or add 5 V I2C pullups unless a
            level shifter is installed. The firmware enables weak internal pullups on
            PB8/PB9, and most breakouts already include suitable 3.3 V I2C pullups.
            The PC serial connection is the Nucleo ST-LINK virtual COM port; no module
            UART pins need to be connected.

            ### Live MLX90640 view
            1. Keep the Nucleo running the STM32Cube MLX90640 raw-stream firmware for the
               fastest live diagnostic view, or the older Arduino firmware for calibrated
               binary/text modes.
            2. Select the Nucleo serial port, normally `COM10`, use **Cube raw** at
               `2000000` baud for the STM32Cube firmware, choose the requested refresh
               rate, and press **Connect**. The current 400 kHz I2C firmware is clean at
               16 Hz and 32 Hz; 64 Hz remains experimental and may overrun. Use
               **Arduino binary** at `921600` baud or **Text frame dump** at `115200` baud only
               with the older Arduino firmware.
            3. The viewer reconstructs the 32 x 24 frame, scales it into a heatmap, and updates
               the min, mean, max, ambient, and frame-rate readouts. Cube raw mode loads the
               camera EEPROM when available and attempts host-side Celsius conversion; if the
               calibration sanity checks fail, it falls back to raw sensor counts.
            4. Use **Auto scale** while aligning the camera. Switch to fixed min/max scaling when
               comparing before/after current frames so colour changes are easier to judge.
            5. Press **Export frame** to save the current PNG heatmap and raw text frame to
               Downloads.

            ### Live MLX90614 spot thermometer
            1. Keep the Nucleo running the STM32Cube MLX90614 probe firmware with the
               module connected on PB9/PB8 I2C.
            2. Select **MLX90614 spot thermometer**, use the Nucleo serial port at
               `2000000` baud, choose an interval, and press **Connect**.
            3. The MLX90614 reports one object-temperature spot plus sensor ambient
               temperature. It is not a 32 x 24 camera, so the display is a single live
               temperature cell rather than a thermal image.
            """
        ).strip(),
    },
    "mini_dma_logger": {
        "title": "Mini DMA Logger",
        "body": dedent(
            """
            ### IR thermometer wiring
            Use the NUCLEO-H753ZI ST-LINK USB connector for board power and the PC
            virtual COM port. Turn the Nucleo power off before rewiring, and connect one
            thermal module at a time to the checked-in firmware.

            **Dratek MLX90614ESF-DCI spot thermometer module** (`0x5A`):

            * Nucleo **3V3** -> module **VCC/VIN** input that accepts 3.3 V
            * Nucleo **GND** -> module **GND**
            * Nucleo **D14 / PB9 / I2C1_SDA** -> module **SDA**
            * Nucleo **D15 / PB8 / I2C1_SCL** -> module **SCL**

            **MLX90640 110 degree, 32 x 24 thermal camera module** (`0x33`):

            * Nucleo **3V3** -> camera **VCC/VIN** input that accepts 3.3 V
            * Nucleo **GND** -> camera **GND**
            * Nucleo **D14 / PB9 / I2C1_SDA** -> camera **SDA**
            * Nucleo **D15 / PB8 / I2C1_SCL** -> camera **SCL**
            * Leave **INT**, **AD**, and other optional breakout pins unconnected unless
              a custom firmware explicitly needs them.

            Do not use **5V** power or 5 V I2C pullups unless a level shifter is
            installed. The Nucleo side is a 3.3 V I2C bus; most breakout modules already
            include suitable pullups.

            ### Choosing the sensor
            * Choose the connected sensor explicitly. Mini DMA does not auto-detect the
              sensor because the current Nucleo firmware images are sensor-specific.
            * **MLX90614 spot thermometer** expects STM32Cube MLX90614 probe lines at
              `2000000` baud. Its setting is the probe sample interval. The logger
              records the single apparent object temperature plus sensor ambient
              temperature.
            * **MLX90640 Cube raw camera** expects the STM32Cube raw-stream camera
              firmware at `2000000` baud. Its setting is the camera refresh code. The
              logger records the frame maximum as the apparent object temperature and
              saves frame min/mean/center/hotspot details in `ir_temperature.csv`.

            The NUCLEO-H753ZI hardware can use either module on the same pins because the
            sensors have different I2C addresses (`0x5A` and `0x33`). The checked-in
            firmware images are separate, though: flash the MLX90614 probe firmware for
            the spot thermometer, or the MLX90640 firmware/bridge for the camera. A
            combined firmware is possible later, but that is not the current bench setup.

            Use **Live camera** in the IR panel to open Thermal Camera Viewer on the same
            Nucleo port, preconfigured for MLX90640 Cube raw at `2000000` baud. Disconnect
            Mini DMA IR logging first, because the COM port can be owned by only one
            window at a time.

            Use **Flash firmware** after selecting **MLX90614 spot thermometer** or
            **MLX90640 Cube raw camera** to build and flash the matching STM32Cube
            firmware over SWD.

            The high-speed STM32Cube MLX90640 raw-stream firmware is for the separate
            Thermal Camera Viewer heatmap. Mini DMA logging intentionally stores a
            compact temperature summary instead of every pixel.
            """
        ).strip(),
    },
    "plot_temperature_sensitivity": {
        "title": "Temperature sensitivity plotter",
        "body": dedent(
            """
            ### Prepare your data
            * Organise each trace folder by composition and annealing step. Filenames should
              follow the existing convention (`<composition> <sample> <anneal> <temp>C.txt`) so
              the parser can group wires correctly.
            * Use **Add Files/Folders** to ingest every trace for the batch. With the developer
              *Keep File Selections* toggle active the list will persist between sessions.

            ### Configure the run
            1. Choose which curves to plot (T1, T2, T1+T2, T2−T1). Your choices are remembered
               individually, so each return trip keeps the previous mix of variables.
            2. Select the baseline treatment: *None* for raw values, *Zero 25 °C* to subtract the
               25 °C mean per sample, or *Both* to produce two plots per variable.
            3. Pick Matplotlib, Origin, or both as the backend. Set the export directory, format,
               and PNG DPI. **Create subfolder** drops results into `<script> data YYYY-MM-DD`.
            4. Adjust moving-average windows for continuous sweeps. A five-sample median followed
               by a 200-sample moving average keeps contact loss visible while calming noise.
            5. Tidy titles, legends, fonts, and tick labels from the **Readability** pane before
               running—the plot updates respect all of these switches.

            ### Running and reviewing output
            * Press **Run** once files and settings look right. The console records progress and
              any outlier removals. Toggle the outlier check from the file list if you want the
              tool to suggest exclusions before plotting.
            * Matplotlib windows appear immediately (if **Show plots** is enabled) and are saved
              alongside their Origin counterparts. Axes, legends, and colours match across both
              backends.
            * Origin exports paint `2/1`, `2/2`, … tick labels directly onto the graph so the
              workbook and plotted figure stay in sync even after reopening the project.
              Continuous overlays are offset slightly around each microwire to avoid obscuring
              the mean markers, and Δ(100 °C−25 °C) annotations sit just above the 100 °C point
              for each sample.

            ### Troubleshooting
            * If a sample is missing from the Origin plot, confirm its filename follows the
              expected pattern and that the wire appears in both 25 °C and 100 °C groups.
            * When using the developer file retention toggle, prune the list with **Remove
              Selected** to avoid accidentally reusing stale traces.
            """
        ).strip(),
    },
    "plot_temperature_dependence": {
        "title": "Temperature dependence plotter",
        "body": dedent(
            """
            ### Workflow
            1. Load processed temperature-dependence logs via **Add Files/Folders**. Group files
               by composition so the tool can overlay the correct microwires.
            2. Choose which statistics to emphasise (mean, delta, continuous traces) and adjust
               the smoothing windows for continuous data as needed.
            3. Configure export settings exactly as in the sensitivity plotter—backend, output
               folder, format, and DPI are all remembered per tool.
            4. Use the **Readability** panel to position the legend, scale fonts, or hide axes
               before plotting.

            ### Tips
            * Enabling continuous data provides context for mean markers. Try a median window of
              five samples followed by a longer moving average for stable overlays.
            * Toggle the developer file-retention option when iterating on the same dataset over
              multiple sessions.
            """
        ).strip(),
    },
    "plot_current_annealing": {
        "title": "Current annealing plotter",
        "body": dedent(
            """
            ### Steps
            1. Add one or more logged annealing sessions (three-column text files produced by
               the serial or PyVISA loggers).
            2. Decide whether to plot resistance against current, sample index, or both. Each
               figure includes per-loop colouring so up/down ramps are easy to distinguish.
            3. Select the backend and export directory. PNG exports obey the stored DPI while
               PDF/SVG remain vector-based.
            4. Use readability options to toggle legends, axis labels, or tick marks before
               generating the plot.

            ### Notes
            * The initial zero-current sample is skipped automatically so curves start at the
              first real measurement.
            * When plotting multiple sessions the legend reflects the logfile names, making it
              easy to compare runs from different days.
            """
        ).strip(),
    },
    "plot_hysteresis_loops": {
        "title": "Hysteresis loops",
        "body": dedent(
            """
            ### Usage
            1. Select pre-processed hysteresis loop files (one per microwire). Each plot overlays
               the forward and reverse sweeps and can include optional fits if present in the
               dataset.
            2. Tweak axis ranges, legend placement, and font sizes from the **Readability** pane.
            3. Use the data table to hide or reveal individual curves before plotting, keeping the
               output focused on the comparisons you need.
            """
        ).strip(),
    },
    "vsm_hysteresis_loops": {
        "title": "VSM hysteresis loops",
        "body": dedent(
            """
            ### Plot loops and workbooks
            1. Use **Browse files…** or **Browse folder…** to point at your Lakeshore
               `VSM-Hys-Data` exports. Selected items load immediately and populate every dock—click
               **Plot VSM Hysteresis Loops** whenever you want to rebuild the graphs and per-graph
               workbooks.
            2. Tune the X/Y axis selectors in the **Graph Settings** dock. Your axis choices,
               backend preference, plot style, dark-theme toggle, and normalise-endpoints option are
               remembered across sessions.

            ### Explore the workspace
            * The left auto-hide docks mirror Origin’s layout with a **Project Explorer** (double
              click a measurement to jump to its temperature tab) and a running **Message Log** of
              parser decisions. Unread errors (missing files, parsing issues, or metrics that could
              not be computed) turn the dock tab red until you hover or pin it open, making it easy
              to spot issues even when the panel is collapsed.
            * The right-side **Object Manager** mirrors whichever plot tab is active—temperature
              loops, overlay comparisons, or derived metrics—so ticking/unticking a node only
              affects the visible curves while keeping Matplotlib pop-outs and Origin exports in
              sync without regenerating plots. Angles and temperatures are sorted numerically to
              match the legends shown on each tab.
            * The **Worksheets** dock exposes editable pandas-backed tables for each measurement.
              Right-click rows to delete them, then hit **Plot VSM Hysteresis Loops** to refresh curves and
              metrics with the cleaned data.

            ### Configure plots and overlays
            * The **Graph Settings** dock bundles backend selection, rescale toggles, dark mode,
              TXT export preferences, and the overlay picker. Multi-select angles in the overlay
              list and press **Plot selected angles across temperatures** to add comparison tabs
              directly to the main viewer.
            * Press **Plot VSM Hysteresis Loops** once the inputs look right. **Normalize Y** (available on
              hysteresis-loop and angle-overlay tabs) toggles the active curves between raw and
              unit-scaled data, automatically rescales the axis around the span of the normalised
              samples (symmetrically whenever the curves cross zero), and restores the previous
              limits when you click it again. Derived-metric tabs remain in their native units so
              comparisons such as saturation versus angle stay meaningful. **Open in Matplotlib**
              re-plots just the selected tab with constrained layout when you want desktop zoom
              tools, and **Save graph…** captures that same tab (loops, overlays, or metrics) as
              PNG, PDF, or SVG.

            ### Derived metrics
            * Coercivity and remanence pair the closest positive and negative zero crossings
              before symmetrising them, averaging the absolute magnitude of the two nearest
              intercepts even when they land on the same side of zero. The analysis walks the
              samples in acquisition order so the intercepts mirror what you see on screen even
              when sweeps double back. Noisy outer segments cannot inflate the reported
              magnitudes while asymmetric loops still produce balanced ±Hc and ±Mr for plotting.
            * Zero-crossing detection adapts to each loop’s scale—if the data only grazes the axis
              the plotter interpolates from the nearest neighbours, and the tolerance follows the
              actual field/moment magnitudes instead of assuming at least ±1 units. Micro-emu traces
              therefore keep their intercepts instead of collapsing to zero, and when no trustworthy
              value exists an error entry is pushed to the Message Log (highlighting the dock) so
              you can inspect the worksheet straight away.
            * Need to double-check those numbers? Choose **Develop → Coercivity debug…** or
              **Develop → Remanence debug…** to open a floating inspector with one tab per
              temperature listing the source X/Y columns, both raw zero-crossing values (even if
              they share the same sign), the symmetrised ± pairs, and a quick plot comparing the
              original and corrected curves versus angle.
            * **Plot metrics vs angle** produces one tab per metric (coercivity, remanence,
              saturation) showing how each temperature behaves across rotations.
            * Pick one or more angles in the overlay list and choose **Plot metrics vs temperature**
              to build another set of tabs driven by the selected rotations. All tabs obey the same
              styling, theme, and save controls as the main plots.

            ### Exporting
            * **Export TXT…** writes Origin-ready tables for either the entire measurement columns or
              just the axes used in the current plots, with short/long names, units, comments, and
              axis roles set automatically. Enable the subfolder option to group multi-file exports.
            * **Export metrics** saves the derived coercivity/remanence/saturation tables grouped by
              temperature and by angle so downstream Origin sessions pick up long names, units, and
              comments without manual edits.
            """
        ).strip(),
    },
    "plot_hsw_distribution": {
        "title": "HSW distribution",
        "body": dedent(
            """
            ### Steps
            1. Load switching-field CSV exports for the wires you want to compare.
            2. Configure bin widths, enable or disable Gaussian fits, and decide whether to show
               cumulative fractions.
            3. Overlay multiple groups to compare treatments—the legend is positioned outside the
               axes by default to keep bars clear, but you can reposition it from the readability
               settings.
            """
        ).strip(),
    },
    "plot_hsw_load_compare": {
        "title": "HSW load comparison",
        "body": dedent(
            """
            ### Workflow
            1. Drop in switching-field datasets recorded at different mechanical loads.
            2. Enable or disable averages, standard deviations, and cumulative fractions with the
               checkboxes above the list.
            3. Reflow the legend or resize fonts from the readability pane when overlaying many
               load levels so the figure stays legible.
            """
        ).strip(),
    },
    "plot_maxion": {
        "title": "Maxion continuous measurements",
        "body": dedent(
            """
            ### How to use
            1. Load long-running Maxion measurement logs.
            2. Choose whether to scale the axis (×10³/×10⁴) or centre the data on the median to
               emphasise drift.
            3. Combine continuous traces with statistical summaries if you want both context and
               compact overlays.
            4. Enable “Colour legend text” to match legend entries to their traces.
            """
        ).strip(),
    },
    "plot_pdf": {
        "title": "PDF plotter",
        "body": dedent(
            """
            ### Steps
            1. Import a delimited text file (comma- or tab-separated). Choose the X and Y columns
               to plot; multiple Y selections produce multiple curves.
            2. Configure line/marker styling, enable grid lines, and decide whether to invert the
               colours for dark backgrounds.
            3. Adjust titles, legends, and tick fonts before exporting. Figures save as PNG, PDF,
               or SVG using the specified size and DPI.
            """
        ).strip(),
    },
    "plot_stress_dependence": {
        "title": "Stress dependence",
        "body": dedent(
            """
            ### Workflow
            1. Load magnetisation-versus-stress datasets for each microwire.
            2. Choose whether to plot raw measurements, polynomial fits, and/or error bars.
            3. Crop stress ranges or group datasets before plotting to focus on the regions of
               interest.
            4. Use readability options to maintain legible legends and tick labels when comparing
               many wires at once.
            """
        ).strip(),
    },
    "plot_stress_sensitivity": {
        "title": "Stress sensitivity",
        "body": dedent(
            """
            ### Steps
            1. Add processed stress-sensitivity measurements. Each dataset can include fitted
               trends as well as raw values.
            2. Enable logarithmic axes if the spread is large, and configure marker styles before
               exporting.
            3. Overlay multiple wires to compare behaviour; the readability pane keeps the figure
               tidy even with numerous overlays.
            """
        ).strip(),
    },
    "builder_database": {
        "title": "Microwire data builder",
        "body": dedent(
            """
            ### Prepare your sources
            1. Click **Microwire data folder → Add folder** and choose the root directory that
               holds fabrication spreadsheets, microscope overlays, and videos. The builder follows
               the existing “Microwire data/…” layout automatically.
            2. Add annealing logs with **Annealing files → Add files/folder**. Enable **Recursive
               scan** when you point at a directory so every draw/piece is collected.
            3. Drop microscope overlays and any manual captures into **Microscope images** if the
               automatic search misses them. The folder picker also supports **Recursive scan** for
               batch imports.

            ### Configure outputs
            1. Leave the export directory at your Downloads folder unless you need a different
               location; the field remembers the last path you used.
            2. Confirm the base filename and tick the export formats (CSV and/or Excel). The
               builder prompts before overwriting existing files and stores your choice per format.
            3. Decide whether to generate Matplotlib and/or Origin plots, adjusting the figure size
               if you want a different layout inside the spreadsheet exports.

            ### Run and monitor
            * Press **Run** to begin. The progress bar covers preparation, data collection, table
              assembly, plotting, and export steps; the ETA blends per-stage averages from previous
              runs with the live moving average so it steadies quickly without large swings.
            * Watch the log panel for missing metadata or skipped files. Use
              **Cancel** to stop safely—the builder tidies partial exports and keeps all of your
              settings for the next attempt.

            ### After the run
            * When the dialog reports success, choose **Open** to jump straight to the primary
              export. The log also lists every generated plot and export path for quick follow-up.
            * Stage timings are stored between sessions so future batches of similar size produce
              tighter ETAs.

            ### Troubleshooting
            * If microscope images are not detected automatically, add their parent folder under
              **Microscope images**; the builder merges manual and discovered files before you
              review and enter the stored values.
            * Use **Clear** in any section to reset the list before loading a different batch, and
              revisit **Help → View Help** at any time for this guide.
            """
        ).strip(),
    },
    "logger_current_annealing": {
        "title": "Serial current annealing logger",
        "body": dedent(
            """
            ### Connect and prepare
            1. Select the serial port manually or discover it using the drop-down next to
               **Connect**. The logger remembers the most recently used port, log directory, and
               file name to minimise setup time.
            2. Configure the current ramp: maximum current, step size, interval between steps,
               dwell time at the peak, number of loops, and whether automatic reversal to zero is
               allowed. Choosing ∞ for the loop count locks the control and displays the total time
               as infinity.

            ### Running the process
            * Click **Start annealing process** to begin. Voltage and current readings are sampled
              continuously, resistance plots update live, and every sample is written to the chosen
              logfile.
            * When the shared HMP broker is running, choose **Shared HMP broker** as the supply,
              enter the broker host/port, and select the channel confirmed for current annealing.
              Raw serial commands are disabled in this mode.
            * Contact-loss detection waits for the first non-zero current, applies a short grace
              period, and then requires several consecutive zero readings before halting. If the
              supply reaches 30 V a dialog lets you hold, reverse, or abort the ramp safely.
            * Use **Reverse now** to begin the downward ramp immediately or **Stop** to terminate
              the process and flush the log to disk.

            ### Notes
            * The shared menu bar provides theme controls, console visibility toggles, and this
              help entry for quick reference.
            """
        ).strip(),
    },
    "logger_ac_susceptibility": {
        "title": "AC susceptibility logger",
        "body": dedent(
            """
            ### Connect and prepare
            1. Connect the annealing power supply as usual, then connect the LCR-6200 virtual
               COM port in the LCR panel. The LCR-6200 needs the GW Instek USB VCP driver before
               Windows exposes the COM port.
            2. Enter the LCR frequency list and AC level list. The logger builds the full
               frequency by level matrix and can run one current sweep per AC setting.
            3. Choose the LCR function and monitor channels. For coil-based susceptibility scans,
               `Ls-Q` with monitor `Z`/`IAC` is a useful starting point.
            4. Use **Measure baseline** to run the LCR matrix without current annealing. This
               writes a timestamped baseline TSV for empty-fixture or wire/no-current checks.

            ### Running the process
            * The current ramp, hold, reverse, voltage-limit handling, and contact-loss logic are
              reused from the current annealing logger.
            * When one sweep per AC setting is enabled, the logger uses reverse-to-zero so each
              frequency/level pair gets a complete current sweep before the next pair starts.
            * Log rows keep the first three current-annealing columns, then append LCR setting
              metadata and the latest `FETC:IMP?` values so existing annealing plots can still
              read the file.
            """
        ).strip(),
    },
    "logger_serial_data": {
        "title": "Generic serial data logger",
        "body": dedent(
            """
            * Record arbitrary serial streams with timestamped entries.  Configure the port
              parameters, choose an output file, and start logging.
            * Use the inline file-name builder to assemble descriptive filenames; presets
              are stored between runs.
            * Real-time plots update as data arrives so anomalies can be spotted early.
            * The shared menu bar exposes theme controls, layout reset, and these help notes.
            """
        ).strip(),
    },
    "logger_manual_stress_strain": {
        "title": "Manual stress/strain logger",
        "body": dedent(
            """
            ### Workflow
            1. Set the output directory and file name. The built-in name builder mirrors the
               stress logger naming pattern.
            2. Enter sample geometry (initial length `L0` and wire diameter). Stress is
               calculated from load and cross-section area; strain is calculated relative to the
               last zero-load displacement point before loading starts.
               Optionally connect a Microwire Database Builder project (`.pydpj` / `.pypdj`) and
               use **Auto-fill diameter** to pull `d (µm)` directly from the project data.
            3. Click **Start**, then manually enter displacement and load values point-by-point.
               Use **Add Point** to append each measurement.

            ### Live plots
            * Plot 1 shows the raw input curve: load vs displacement.
            * Plot 2 shows the converted curve: stress (MPa) vs strain (%).
            * Every new point updates both plots immediately and is written to the TXT log.
            * Both plots are split automatically into `Loading n` / `Unloading n` segments
              using strain direction changes, so repeated loops stay separated.
            * You can switch displacement display between millimeters and micrometer points
              (`10^-2 mm`) for direct comparison with the linear stage. In points mode, a live
              micrometer-display box shows the wrapped 0..45 dial value and a `Micrometer at d=0`
              field lets you set the dial offset for displacement zero.
            * A live idle timer shows seconds since the last logged load change and warns as it
              approaches the 60-second balance timeout.
            * The **Logged Data** table updates row-by-row during measurement and is shown under
              the graph area for easier monitoring while logging.
            * Use **Plot view** to switch between both graphs and `Load vs Displacement` only.
            * **Plot view** also supports a dual-axis overlay: load/displacement on the left+bottom
              axes and stress/strain on the right+top axes within one combined graph.
            * Use **Reset d=0** to quickly return displacement input to zero without clearing data.

            ### Output format
            * Logs are saved as `.txt`.
            * The first row contains long names and the second row contains units.
            * Use **Stop** to finish the session cleanly.
            * Use **Scale Re-zero** when the balance resets to zero; the logger keeps continuity
              by offsetting subsequent load input values.
            """
        ).strip(),
    },
    "emulator_serial": {
        "title": "Universal serial emulator",
        "body": dedent(
            """
            * Bridge two serial endpoints for testing.  Point the emulator at one side of a
              virtual COM-pair (or a `loop://` port on Windows) and forward traffic to the
              other.
            * Useful when exercising the loggers without physical hardware—feed scripted
              responses or replay recorded sessions through the emulator to validate
              parsing.
            * Use the menu bar to toggle the theme or to revisit these instructions from the
              **Help** menu.
            """
        ).strip(),
    },
}


def _resolve_topic(topic: str) -> tuple[str, str]:
    content = _HELP_CONTENT.get(topic)
    if content is None:
        content = _HELP_CONTENT["default"]
    return content["title"], content["body"]


def show_help(topic: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
    title, body = _resolve_topic(topic)
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(f"Help — {title}")
    dialog.setModal(True)
    layout = QtWidgets.QVBoxLayout(dialog)

    view = QtWidgets.QTextBrowser(dialog)
    view.setOpenExternalLinks(True)
    try:
        view.setMarkdown(body)
    except Exception:
        view.setPlainText(body)
    layout.addWidget(view, stretch=1)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Close,
        QtCore.Qt.Orientation.Horizontal,
        dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    dialog.resize(720, 520)
    dialog.setMinimumSize(520, 360)
    dialog.exec()


def make_help_button(topic: str, parent: Optional[QtWidgets.QWidget] = None) -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton("Help")
    button.setAutoDefault(False)
    button.setDefault(False)

    def _show() -> None:
        target = parent
        if target is None:
            target = button.window()
        show_help(topic, target)

    button.clicked.connect(_show)
    return button


__all__ = ["make_help_button", "show_help"]
