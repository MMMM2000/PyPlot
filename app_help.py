from __future__ import annotations

from textwrap import dedent
from typing import Optional

from PyQt6 import QtCore, QtWidgets


_HELP_CONTENT: dict[str, dict[str, str]] = {
    "default": {
        "title": "Microwire tools",
        "body": dedent(
            """
            These dialogs ship with consistent controls: the file list on the left keeps
            track of every dataset you load, the console underneath reports progress, and
            the right-hand panel contains the script-specific options.  Most settings are
            remembered per tool, including readability choices, export directories, and
            backend preferences.

            * **Add Files/Folders** accepts individual files or entire folders.  Files are
              deduplicated automatically and double-clicking any entry opens it in the OS.
            * Enable **Check Outliers** to pre-process the selected files; choose **Remove
              automatically** if you want suspicious samples discarded without a manual
              confirmation step.  Both flags are stored separately for each plotting
              script.
            * The **Readability** section is always active.  Toggle the legend, titles, or
              labels, resize fonts, and decide where the legend should sit.  Legend symbol
              visibility, size, and colour can also be controlled here.
            * Output directories default to your Downloads folder and can optionally be
              organised into dated subfolders.  PNG exports honour the DPI field so
              high-resolution assets can be produced directly.

            See the README for a complete walkthrough of installation, packaging with
            PyInstaller, and troubleshooting tips.
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
            * The **Theme** combo box can force a light or dark appearance.  Leaving it on
              *System* follows the host operating system and updates automatically when the
              system theme changes.
            * Closing the launcher warns you about any additional windows that would be
              closed at the same time.
            """
        ).strip(),
    },
    "plot_temperature_sensitivity": {
        "title": "Temperature sensitivity plotter",
        "body": dedent(
            """
            ### Workflow
            1. Load exported `.txt` traces.  Files can be grouped by composition and
               annealing step; the plotter separates variables automatically.
            2. Choose which curves to include (T1, T2, their sum, or the delta).
            3. Select a baseline mode: *None* plots absolute values, *Zero 25°C* subtracts
               the 25 °C mean per sample, and *Both* produces two plots per dataset.
            4. Pick the backend (Matplotlib, Origin, or both) and an output directory.  PNG
               exports use the stored DPI value, while PDF/SVG use vector output.
            5. Configure the moving-average windows for continuous data.  The default MA
               window is 200 samples to smooth live traces without hiding the trend.

            ### Notes
            * Sample ticks show `2/1`, `2/2`, … on both Matplotlib and Origin outputs.
            * Legends can be positioned inside the axes or just outside on the right.
              Symbol visibility and legend text colours obey the readability settings.
            * Origin exports disable speed mode, enable anti-aliasing, reuse the Matplotlib
              title, add the 100 °C–25 °C delta labels, and shrink the raw scatter markers
              to size 1 for parity with the Matplotlib figure.
            * Continuous data is shown as a smoothed trace offset near each sample.  Delta
              annotations float above the 100 °C means and stay clear of the continuous
              overlay.
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
            """
        ).strip(),
    },
    "logger_pyvisa_current_annealing": {
        "title": "PyVISA current annealing logger",
        "body": dedent(
            """
            * Select a VISA resource (USB, RS‑232, or TCP/IP) from the discovery list and
              connect.  The logger shares the same ramp controls and safety checks as the
              serial version.
            * The dialog keeps track of log directories, file names, and ramp presets per
              user.  Voltage limits trigger the same protective prompt.
            * Ideal for remote instruments when a direct serial connection is not
              available.
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
