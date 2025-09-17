from __future__ import annotations

from textwrap import dedent
from typing import Optional

from PyQt6 import QtCore, QtWidgets


_HELP_CONTENT: dict[str, dict[str, str]] = {
    "default": {
        "title": "Microwire tools",
        "body": dedent(
            """
            ### Window layout basics
            * The file list on the left keeps track of every dataset you load. Double-click
              an entry to open it in your operating system. Use **View → Show File Browser**
              if you want to hide or reveal the list temporarily.
            * The right-hand panel holds the script-specific options. A console beneath the
              list records status messages; toggle it from **View → Show Console** when you
              need more space.
            * The footer pins the primary action button so **Run** never scrolls off-screen.

            ### Menu bar highlights
            * **View → Theme** switches between the system appearance, a light palette, or a
              dark palette. The choice applies immediately to every open tool.
            * **View → Reset Layout** restores the default splitter sizes if the panes are
              resized awkwardly.
            * **Help → View Help** opens this guide for the current dialog. Additional
              entries in the Help menu link to documentation or keyboard shortcuts when
              available.

            Preferences—readability, export directories, backend selections, theme
            overrides—are remembered per tool. The README includes step-by-step setup
            instructions, packaging notes, and troubleshooting advice.
            """
        ).strip(),
    },
    "launcher": {
        "title": "Master launcher",
        "body": dedent(
            """
            ### Using the launcher
            * Pick a tab to choose between loggers, plotting scripts, or emulators.  The
              list on each tab highlights the currently selected tool.
            * Click **Run** to open the highlighted utility.  The launcher stays alive
              while other windows are open, so you can return here without relaunching the
              application.
            * Use the menu bar to drive appearance and housekeeping: **View → Theme** toggles
              light/dark/system palettes, while **File → Exit** quits the launcher if you
              are finished.
            * Closing the launcher warns you about any additional windows that would be
              closed at the same time.
            """
        ).strip(),
    },
    "plot_temperature_sensitivity": {
        "title": "Temperature sensitivity plotter",
        "body": dedent(
            """
            ### Getting started
            1. Click **Add Files/Folders** to load exported `.txt` traces. Group your files by
               composition and annealing step; the plotter separates variables automatically.
            2. Tick which curves to include (T1, T2, their sum, or T2–T1). Each selection is
               remembered the next time you open the tool.
            3. Choose a baseline mode. *None* plots absolute values, *Zero 25 °C* subtracts
               the 25 °C mean per sample, and *Both* generates one plot with each treatment.
            4. Pick the backend (Matplotlib, Origin, or both) and the export directory. PNG
               renders honour the stored DPI value; PDF/SVG use vector output. Enable
               **Create subfolder** if you want a dated directory per run.
            5. Configure moving-average windows for continuous data. The default 200-sample
               window smooths the trace without hiding the trend.

            ### Tips
            * Sample ticks show `2/1`, `2/2`, … on both Matplotlib and Origin exports. If
              Origin cannot bind to the label dataset the dialog falls back to fixed text.
            * Use the **Readability** panel to reposition legends, adjust font sizes, and
              colour match legend entries to their curves.
            * The menu bar offers shortcuts: **View → Theme** to adjust the palette, **View →
              Show Console** to hide status messages while reviewing settings, and **View →
              Reset Layout** to restore the default pane sizes.

            ### Origin-specific behaviour
            * Exports disable speed mode, enable anti-aliasing, and reuse the Matplotlib
              title. Raw scatter markers shrink to size 1 for parity with the Matplotlib
              figure.
            * 100 °C–25 °C delta labels float above each sample. Continuous data is plotted as
              a smoothed overlay offset near the relevant sample so it remains distinguishable.
            """
        ).strip(),
    },
    "plot_temperature_dependence": {
        "title": "Temperature dependence plotter",
        "body": dedent(
            """
            * Combine multiple measurements taken at different temperatures.  The tool
              overlays the mean hysteresis shift per microwire and highlights the
              difference between 25 °C and 100 °C.
            * Enable **Include continuous data** to plot smoothed background traces.  Use
              the moving-average controls to tame noisy runs.
            * Legends, axis labels, and titles follow the same readability controls used by
              other plotters.
            """
        ).strip(),
    },
    "plot_current_annealing": {
        "title": "Current annealing plotter",
        "body": dedent(
            """
            * Add one or more logged annealing sessions (plain three-column text files).
            * Choose the backend and output directory; PNG exports respect the stored DPI.
            * The plotter skips the initial zero-current sample so curves begin with the
              first real measurement.
            * Use the readability controls to expose only the legend or axes you need.
            """
        ).strip(),
    },
    "plot_hysteresis_loops": {
        "title": "Hysteresis loops",
        "body": dedent(
            """
            * Select pre-processed hysteresis loop files.  Each plot overlays the forward
              and reverse sweep with optional fits.
            * The **Readability** panel manages legend placement, fonts, and axis labels.
            * Toggle curve visibility directly in the data table before plotting.
            """
        ).strip(),
    },
    "plot_hsw_distribution": {
        "title": "HSW distribution",
        "body": dedent(
            """
            * Load switching-field CSV exports.  The dialog bins events by microwire and
              displays histograms with optional Gaussian fits.
            * You can overlay multiple groups to compare treatments.  Legends sit outside
              the axes by default to keep the bars visible.
            """
        ).strip(),
    },
    "plot_hsw_load_compare": {
        "title": "HSW load comparison",
        "body": dedent(
            """
            * Compare switching-field distributions across different mechanical loads.
            * Enable or disable averages, standard deviations, and cumulative fractions via
              the checkboxes above the file list.
            * Use the readability controls to reflow legends when adding many load levels.
            """
        ).strip(),
    },
    "plot_maxion": {
        "title": "Maxion continuous measurements",
        "body": dedent(
            """
            * Plot long-running Maxion measurements with optional axis scaling (×10³/×10⁴)
              and median centring.
            * Continuous traces can be combined with their statistical summaries, and
              legend text can inherit the trace colours when needed.
            """
        ).strip(),
    },
    "plot_pdf": {
        "title": "PDF plotter",
        "body": dedent(
            """
            * Import a delimited text file and choose columns to plot against each other.
            * Configure line/marker styling, enable grids, and decide whether the output
              should be inverted for dark backgrounds.
            * Legends, axis labels, and tick fonts are adjustable; exports can be saved as
              PNG, PDF, or SVG using the chosen figure size and DPI.
            """
        ).strip(),
    },
    "plot_stress_dependence": {
        "title": "Stress dependence",
        "body": dedent(
            """
            * Compare magnetisation versus stress for multiple wires.  The tool plots raw
              measurements, polynomial fits, and optional error bars.
            * Stress ranges can be cropped, and datasets grouped, before plotting.
            * Apply readability tweaks to keep dense legends or tick labels legible.
            """
        ).strip(),
    },
    "plot_stress_sensitivity": {
        "title": "Stress sensitivity",
        "body": dedent(
            """
            * Visualise how sensitivity changes with applied stress.  Measurements from
              different wires can be overlaid with their fitted trends.
            * Enable logarithmic axes when needed and tune marker styles prior to export.
            """
        ).strip(),
    },
    "logger_current_annealing": {
        "title": "Serial current annealing logger",
        "body": dedent(
            """
            * Choose the serial port (or use the discovery combo box) and click **Connect**.
              The logger remembers the last directory and file name separately so repeated
              runs require fewer clicks.
            * Configure the ramp: set the maximum current, dwell time at the peak, current
              step, loop count, and whether automatic reversal is allowed.  Infinite looping
              locks the loop counter and displays ∞ in the time estimate.
            * **Start annealing process** runs the scripted ramp.  The logger measures
              voltage and current continuously, plots resistance in real time, and stores
              samples to the chosen log file.
            * Contact-loss detection waits until the supply has delivered a non-zero
              current, adds a short grace period, and requires several consecutive zero
              readings before stopping.  Hitting 30 V opens a dialog so you can hold,
              reverse, or abort safely.
            * Use **Reverse now** for an immediate ramp down and **Stop** to terminate the
              process while saving the collected data.
            * The menu bar mirrors the PyVISA version: switch appearance from **View → Theme**,
              collapse the console via **View → Show Console**, and revisit these notes from
              **Help → View Help**.
            """
        ).strip(),
    },
    "logger_pyvisa_current_annealing": {
        "title": "PyVISA current annealing logger",
        "body": dedent(
            """
            ### Connecting to your supply
            * Click **Refresh** to scan for VISA resources. The list includes local serial
              bridges (e.g. `ASRL…`) and LAN/USB instruments reported by NI-VISA/pyvisa.
            * Select the instrument and press **Connect**. The current log directory and file
              name are remembered between sessions—adjust them before starting a run.

            ### Configuring the ramp
            * **Max**, **Step**, and **Interval** define the up/down ramp. The live estimate
              below the controls updates automatically so you know how long the run will
              take for the selected number of loops.
            * **Dwell** keeps the current at the peak for the specified number of seconds
              before the ramp reverses or stops. Set the **Loops** spin box to `∞` for a
              continuous anneal or choose an exact count.
            * Enable **Reverse to zero after max** to bring the current back to zero after
              each loop. Use **Reverse current now** if you need to begin the downward ramp
              immediately.

            ### During acquisition
            * Press **Start annealing** to begin; the logger writes timestamped voltage,
              current, and resistance values to the chosen file and updates the resistance
              plots in real time.
            * If the instrument reports zero current after delivering a non-zero value the
              logger assumes contact loss, stops the ramp, and notifies you. Hitting 30 V
              presents options to hold, reverse, or abort.
            * Use **Stop annealing** to finish the sequence cleanly. Logging can be toggled
              independently via **Start Log/Stop Log**.

            ### Menu shortcuts
            * **View → Theme** controls the palette; **View → Show Console** collapses the
              message pane when you want a taller plot. **Help → View Help** opens this
              walkthrough.
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
