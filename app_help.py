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

            ### Developer menu essentials
            * **Keep File Selections** – when iterating on a dataset, enable this so the dialog
              reopens with your previous file list intact.
            * **Show Experiments Tab** – exposes prototypes (the PyVISA annealing logger and the
              microwire data builder). Leave the toggle off during routine work to keep the
              launcher tidy.

            ### Menu bar highlights
            * **View → Theme** flips between system, light, and dark palettes across every open
              window. Changes apply instantly, even to existing loggers.
            * **View → Reset Layout** restores splitter positions if you shrink a pane too far.
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
            3. Use **Developer → Show Experiments Tab** to reveal or hide the prototype list and
               **Developer → Keep File Selections** if you want plotting dialogs to reopen with
               the same input files pre-selected. The experiments currently bundle the PyVISA
               annealing logger and the Microwire Data Builder.
            4. The **View** menu mirrors other windows—switch theme, collapse the file browser or
               console, and reset splitter sizes when needed. **File → Exit** quits the launcher
               after confirming there are no unsaved child windows.
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
            * Press **Run** to begin. The progress bar covers preparation, OCR analysis, table
              assembly, plotting, and export steps; the ETA blends per-stage averages from previous
              runs with the live moving average so it steadies quickly without large swings.
            * Watch the log panel for missing metadata, OCR warnings, or skipped files. Use
              **Cancel** to stop safely—the builder tidies partial exports and keeps all of your
              settings for the next attempt.

            ### After the run
            * When the dialog reports success, choose **Open** to jump straight to the primary
              export. The log also lists every generated plot and export path for quick follow-up.
            * Stage timings are stored between sessions so future batches of similar size produce
              tighter ETAs.

            ### Troubleshooting
            * If microscope images are not detected automatically, add their parent folder under
              **Microscope images**; the builder merges manual and discovered files before OCR.
            * When Tesseract is not available the builder falls back to the lab’s verified
              diameter table for reference microwires so the spreadsheet keeps `d`/`D` values,
              but installing Tesseract is still recommended for full coverage.
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
    "logger_pyvisa_current_annealing": {
        "title": "PyVISA current annealing logger",
        "body": dedent(
            """
            ### Connect to the instrument
            * Open the logger from the launcher’s **Experiments** tab (enable it from the
              **Developer** menu). The window mirrors the serial logger but communicates via
              VISA.
            1. Click **Refresh** to enumerate VISA resources. Devices discovered by NI-VISA or
               ``pyvisa-py`` appear alongside serial bridges (`ASRL…`).
            2. Pick the instrument, adjust the log directory and filename if required, and press
               **Connect**. The dialog remembers the previous selections between sessions.

            ### Configure the annealing sequence
            * Set **Max**, **Step**, and **Interval** to describe the ramp. The live time estimate
              updates immediately when you adjust any parameter or loop count.
            * Specify the **Dwell** time at the peak and choose how many loops to execute. Set the
              loop count to `∞` for continuous operation. Toggle **Reverse to zero after max** to
              force a return to zero between loops.

            ### Run and monitor
            * Press **Start annealing** to begin the scripted ramp. Voltage, current, and
              resistance are logged with timestamps, and resistance traces update live.
            * **Reverse current now** initiates an immediate ramp-down. **Stop annealing** ends the
              process gracefully while keeping the data file intact.
            * Contact-loss detection mirrors the serial logger: it requires sustained zero current
              readings before aborting and prompts you when the supply reaches 30 V.

            ### Extras
            * The standalone **Start Log/Stop Log** buttons let you capture instrument telemetry
              without running a ramp.
            * Appearance and layout controls live under the **View** menu, and this manual is
              always available from **Help → View Help**.
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
