from __future__ import annotations

import sys
import uuid
from dataclasses import asdict
import json
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
import inspect
import logging
import types

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import pandas as pd

from plotting.pyplot import (
    PyPlotWindow,
    WorksheetColumnMeta,
    WorksheetData,
    WorkbookData,
    TabDescriptor,
)
from plotting.utils import ensure_app_theme, prepare_output_dir, set_last_output_dir, format_annealing_title
from plotting.vsm_hysteresis_loops import VSMPlotter, _looks_like_vsm_name
from plotting.temperature_dependence import core as temp_core
from plotting.temperature_sensitivity import core as temp_sens_core
from plotting.current_annealing import core as anneal_core
from plotting.stress_dependence import stress_gui
from plotting.stress_sensitivity import sens_gui
from plotting.hsw_load_compare import load_compare_gui
from plotting.maxion_continuous import maxion_gui
from plotting.pdf_plotter import pdf_gui
from plotting.hysteresis_loops import loops_gui
from plotting.hsw_distribution import distribution_gui
from plotting.strain_3d_plot import Strain3DPlotter


class PyPlotPlugin:
    """Base plugin contract for PyPlot script integrations."""

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        self.host = host
        self.name = name
        self._settings_widget: QtWidgets.QWidget | None = None

    # Lifecycle ---------------------------------------------------------
    def activate(self) -> None:
        """Called when the plugin becomes active."""

    def deactivate(self) -> None:
        """Called when the plugin is deselected."""

    # UI helpers --------------------------------------------------------
    def panel_widget(self) -> QtWidgets.QWidget | None:
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QtWidgets.QLabel("Script-specific controls will appear here once implemented.")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        return container

    def settings_widget(self) -> QtWidgets.QWidget:
        if self._settings_widget is None:
            container = QtWidgets.QWidget(self.host)
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            label = QtWidgets.QLabel("No additional settings are exposed for this script yet.")
            label.setWordWrap(True)
            layout.addWidget(label)
            layout.addStretch(1)
            self._settings_widget = container
        return self._settings_widget

    # Host actions ------------------------------------------------------
    def load_data(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "This script does not provide a load handler yet.",
        )

    def generate(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Generation is not implemented for this plotting script yet.",
        )

    def open_matplotlib(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Matplotlib export is not available for this plotting script yet.",
        )

    def save_graph(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Graph saving is not available for this plotting script yet.",
        )

    def normalize(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Normalization is not available for this plotting script yet.",
        )

    def export_txt(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "TXT export is not available for this plotting script yet.",
        )

    def open_origin(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Origin export is not available for this plotting script yet.",
        )


class ExternalPlotterPlugin(PyPlotPlugin):
    """Adapter that launches legacy standalone plotters from within PyPlot."""

    def __init__(
        self,
        host: "PyPlotWorkbench",
        name: str,
        launcher: Callable[[], QtWidgets.QWidget | None],
    ) -> None:
        super().__init__(host, name)
        self._launcher = launcher
        self._panel: QtWidgets.QWidget | None = None
        self._window: QtWidgets.QWidget | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel is not None:
            return self._panel
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QtWidgets.QLabel(
            f"{self.name} opens in its dedicated window. Click Launch to continue."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        launch_btn = QtWidgets.QPushButton(f"Launch {self.name}")
        launch_btn.clicked.connect(self._launch)
        layout.addWidget(launch_btn)
        layout.addStretch(1)
        self._panel = container
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        widget = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QtWidgets.QLabel("No additional settings are available."))
        layout.addStretch(1)
        return widget

    def _launch(self) -> None:
        try:
            window = self._launcher()
        except Exception as exc:  # pragma: no cover - defensive
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to launch legacy plotter:\n{exc}",
            )
            return
        if isinstance(window, QtWidgets.QWidget):
            window.show()
            self._window = window

    def load_data(self) -> None:  # type: ignore[override]
        self._launch()

    def generate(self) -> None:  # type: ignore[override]
        self._launch()

    def open_matplotlib(self) -> None:  # type: ignore[override]
        self._launch()

    def update_ui(self) -> None:
        if hasattr(self.host, "load_data_button"):
            self.host.load_data_button.setEnabled(False)
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(False)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(False)
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(False)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(False)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(False)


class EmbeddedWidgetPlugin(PyPlotPlugin):
    """Embed a legacy dialog or widget directly inside the PyPlot workbench."""

    def __init__(
        self,
        host: "PyPlotWorkbench",
        name: str,
        widget_factory: Callable[[], QtWidgets.QWidget | None],
    ) -> None:
        super().__init__(host, name)
        self._widget_factory = widget_factory
        self._widget: QtWidgets.QWidget | None = None
        self._panel: QtWidgets.QWidget | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)
        self._ensure_widget()
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)
        if self._widget is not None:
            try:
                self._widget.hide()
            except Exception:
                pass

    def _ensure_widget(self) -> QtWidgets.QWidget:
        if self._widget is None:
            widget = self._widget_factory()
            if widget is None:
                widget = QtWidgets.QWidget(self.host)
            self._widget = widget
        return self._widget

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        widget = self._ensure_widget()
        widget.setParent(container)
        widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        if isinstance(widget, QtWidgets.QDialog):
            widget.setModal(False)
            try:
                widget.setSizeGripEnabled(False)
            except Exception:
                pass
        try:
            widget.setWindowFlag(QtCore.Qt.WindowType.Dialog, False)
            widget.setWindowFlag(QtCore.Qt.WindowType.Window, False)
        except Exception:
            pass
        widget.show()
        layout.addWidget(widget)
        self._panel = container
        return container

    def settings_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        return None

    def update_ui(self) -> None:  # type: ignore[override]
        for attr in (
            "load_data_button",
            "plot_button",
            "save_graph_button",
            "normalize_button",
            "export_button",
            "open_origin_button",
            "popout_button",
        ):
            widget = getattr(self.host, attr, None)
            if isinstance(widget, QtWidgets.QAbstractButton):
                widget.setEnabled(False)
                if attr == "plot_button":
                    widget.setText("Generate")


class TemperatureDependencePlugin(PyPlotPlugin):
    """Embed the temperature dependence workflow directly inside PyPlot."""

    _VAR_LABELS = {
        "sum": "T1+T2",
        "dT": "T2Ã¢Ë†â€™T1",
        "T1": "T1",
        "T2": "T2",
    }

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data: pd.DataFrame | None = None
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._var_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._mode_combo: QtWidgets.QComboBox | None = None
        self._med_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        self._save_checkbox: QtWidgets.QCheckBox | None = None
        self._format_combo: QtWidgets.QComboBox | None = None
        self._dpi_spin: QtWidgets.QSpinBox | None = None
        self._output_edit: QtWidgets.QLineEdit | None = None
        self._subfolder_checkbox: QtWidgets.QCheckBox | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel("Select temperature data files then click Load data.")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addStretch(1)
        self._summary_label = summary
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        var_group = QtWidgets.QGroupBox("Variables to plot", container)
        var_layout = QtWidgets.QVBoxLayout(var_group)
        for key, label in self._VAR_LABELS.items():
            checkbox = QtWidgets.QCheckBox(label, var_group)
            checkbox.setChecked(key in temp_core.PLOT_VARS)
            self._var_checks[key] = checkbox
            var_layout.addWidget(checkbox)
        layout.addWidget(var_group)

        mode_group = QtWidgets.QGroupBox("Processing", container)
        mode_layout = QtWidgets.QGridLayout(mode_group)
        mode_combo = QtWidgets.QComboBox(mode_group)
        mode_combo.addItems(["Raw", "Processed", "Both"])
        mode_combo.setCurrentIndex({"raw": 0, "processed": 1, "both": 2}.get(temp_core.PLOT_MODE, 0))
        self._mode_combo = mode_combo
        mode_layout.addWidget(QtWidgets.QLabel("Mode:"), 0, 0)
        mode_layout.addWidget(mode_combo, 0, 1)
        med_spin = QtWidgets.QSpinBox(mode_group)
        med_spin.setRange(1, 9999)
        med_spin.setValue(int(temp_core.MED_WINDOW))
        self._med_spin = med_spin
        ma_spin = QtWidgets.QSpinBox(mode_group)
        ma_spin.setRange(1, 9999)
        ma_spin.setValue(int(temp_core.MA_WINDOW))
        self._ma_spin = ma_spin
        mode_layout.addWidget(QtWidgets.QLabel("Median window:"), 1, 0)
        mode_layout.addWidget(med_spin, 1, 1)
        mode_layout.addWidget(QtWidgets.QLabel("Moving average window:"), 2, 0)
        mode_layout.addWidget(ma_spin, 2, 1)
        layout.addWidget(mode_group)

        output_group = QtWidgets.QGroupBox("Output", container)
        output_layout = QtWidgets.QGridLayout(output_group)
        save_checkbox = QtWidgets.QCheckBox("Save plots to disk", output_group)
        save_checkbox.setChecked(bool(temp_core.SAVE_PLOTS))
        self._save_checkbox = save_checkbox
        output_layout.addWidget(save_checkbox, 0, 0, 1, 2)
        output_layout.addWidget(QtWidgets.QLabel("Directory:"), 1, 0)
        output_edit = QtWidgets.QLineEdit(temp_core.OUTPUT_DIR, output_group)
        self._output_edit = output_edit
        output_layout.addWidget(output_edit, 2, 0, 1, 2)
        browse_btn = QtWidgets.QPushButton("BrowseÃ¢â‚¬Â¦", output_group)
        output_layout.addWidget(browse_btn, 2, 2)
        subfolder_cb = QtWidgets.QCheckBox("Create subfolder per run", output_group)
        subfolder_cb.setChecked(False)
        self._subfolder_checkbox = subfolder_cb
        output_layout.addWidget(subfolder_cb, 3, 0, 1, 3)
        output_layout.addWidget(QtWidgets.QLabel("Format:"), 4, 0)
        fmt_combo = QtWidgets.QComboBox(output_group)
        fmt_combo.addItems(["png", "pdf", "svg"])
        fmt_combo.setCurrentText(temp_core.SAVE_FORMAT)
        self._format_combo = fmt_combo
        output_layout.addWidget(fmt_combo, 4, 1)
        output_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 5, 0)
        dpi_spin = QtWidgets.QSpinBox(output_group)
        dpi_spin.setRange(72, 3000)
        dpi_spin.setValue(int(temp_core.PNG_DPI))
        self._dpi_spin = dpi_spin
        output_layout.addWidget(dpi_spin, 5, 1)
        layout.addWidget(output_group)

        def _browse_output() -> None:
            directory = QtWidgets.QFileDialog.getExistingDirectory(
                self.host,
                "Select output directory",
                output_edit.text() or str(Path.home()),
            )
            if directory:
                output_edit.setText(directory)

        browse_btn.clicked.connect(_browse_output)

        layout.addStretch(1)
        self._settings_widget = container
        return container

    def _log(self, message: str, *, level: str = "info") -> None:
        append = getattr(self.host, "_append_log", None)
        if callable(append):
            try:
                append(message, level=level)
                return
            except Exception:
                pass
        print(message)

    def _selected_variables(self) -> list[str]:
        selected = [key for key, cb in self._var_checks.items() if cb.isChecked() and cb.isEnabled()]
        return selected or ["sum"]

    def _apply_settings_to_core(self) -> dict[str, Any]:
        vars_selected = self._selected_variables()
        temp_core.PLOT_VARS = list(vars_selected)
        if self._mode_combo is not None:
            temp_core.PLOT_MODE = {0: "raw", 1: "processed", 2: "both"}.get(self._mode_combo.currentIndex(), "raw")
        if self._med_spin is not None:
            temp_core.MED_WINDOW = int(self._med_spin.value())
        if self._ma_spin is not None:
            temp_core.MA_WINDOW = int(self._ma_spin.value())
        save_flag = bool(self._save_checkbox and self._save_checkbox.isChecked())
        temp_core.SAVE_PLOTS = save_flag
        if self._format_combo is not None:
            temp_core.SAVE_FORMAT = self._format_combo.currentText()
        if self._dpi_spin is not None:
            temp_core.PNG_DPI = int(self._dpi_spin.value())
        output_dir = temp_core.OUTPUT_DIR
        base_dir = self._output_edit.text().strip() if isinstance(self._output_edit, QtWidgets.QLineEdit) else output_dir
        subfolder = bool(self._subfolder_checkbox and self._subfolder_checkbox.isChecked())
        if save_flag:
            output_dir = prepare_output_dir(base_dir or output_dir, "temperature_dependence", subfolder)
            set_last_output_dir(base_dir or output_dir, key="temperature_dependence")
        temp_core.OUTPUT_DIR = output_dir
        temp_core.SHOW_PLOTS = False
        temp_core.BACKEND = "matplotlib"
        return {
            "variables": vars_selected,
            "save": save_flag,
            "output_dir": output_dir,
        }

    def load_data(self) -> None:  # type: ignore[override]
        paths = [path for path in self.host._selected_paths() if path.is_file()]
        if not paths:
            QtWidgets.QMessageBox.warning(self.host, self.name, "Select one or more data files first.")
            return
        string_paths = [str(path) for path in paths]
        try:
            self._data = temp_core.load_data(string_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to load data:\n{exc}")
            self._data = None
            return
        self._loaded_files = string_paths
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        if self._summary_label is not None:
            self._summary_label.setText(f"Loaded {len(self._data)} rows from {len(paths)} file(s).")
        self._log(f"Loaded {len(paths)} temperature dependence file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._data is None:
            self.load_data()
        if self._data is None:
            return
        config = self._apply_settings_to_core()
        dataframe = temp_core.maybe_handle_outliers(self._data.copy())
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(self._plot_tabs)
        else:
            for tab in self._plot_tabs:
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        self._plot_tabs.clear()
        plots_created = 0
        for variable in config["variables"]:
            try:
                fig, saved_name = temp_core.plot_variable(dataframe, variable, config["save"], config["output_dir"])
            except Exception as exc:
                self._log(f"Failed to plot {variable}: {exc}", level="error")
                continue
            canvas = FigureCanvas(fig)
            canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            ax = fig.axes[0] if fig.axes else None
            title = ax.get_title() if ax else variable
            x_label = ax.get_xlabel() if ax else "Temperature"
            y_label = ax.get_ylabel() if ax else variable
            tab_label = temp_core.LABELS.get(variable, variable)
            descriptor = TabDescriptor(
                kind="temperature_dependence",
                title=title,
                root_label=tab_label,
                x_label=x_label,
                y_label=y_label,
                canvas=canvas,
                axes=ax,
                lines={},
                metadata={
                    "variable": variable,
                    "saved_path": saved_name if config["save"] else "",
                    "source_files": list(self._loaded_files),
                },
            )
            self.host.tab_widget.addTab(tab, tab_label)
            self.host._register_plot_tab(tab, canvas, ax, descriptor)
            self._plot_tabs.append(tab)
            plots_created += 1
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {plots_created} temperature plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            QtWidgets.QMessageBox.information(self.host, self.name, "Load data before exporting to Origin.")
            return
        try:
            self._apply_settings_to_core()
            temp_core.SHOW_PLOTS = False
            temp_core.main(self._loaded_files, backend="origin")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to export to Origin:\n{exc}")
            self._log(f"Origin export failed: {exc}", level="error")
        else:
            self._log("Sent temperature plots to Origin.")

    def update_ui(self) -> None:
        has_data = self._data is not None
        if hasattr(self.host, "load_data_button"):
            self.host.load_data_button.setEnabled(True)
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(has_data)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(bool(self._plot_tabs))
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(False)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_data)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(bool(self._plot_tabs))
        self.host._update_project_actions()


class TemperatureSensitivityPlugin(PyPlotPlugin):
    """Embed the temperature sensitivity workflow directly inside PyPlot."""

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data: pd.DataFrame | None = None
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._var_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._baseline_combo: QtWidgets.QComboBox | None = None
        self._include_continuous_checkbox: QtWidgets.QCheckBox | None = None
        self._med_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        self._save_checkbox: QtWidgets.QCheckBox | None = None
        self._format_combo: QtWidgets.QComboBox | None = None
        self._dpi_spin: QtWidgets.QSpinBox | None = None
        self._output_edit: QtWidgets.QLineEdit | None = None
        self._subfolder_checkbox: QtWidgets.QCheckBox | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel("Select temperature sensitivity files then click Load data.")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addStretch(1)
        self._summary_label = summary
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        var_group = QtWidgets.QGroupBox("Variables to plot", container)
        var_layout = QtWidgets.QVBoxLayout(var_group)
        var_layout.setContentsMargins(8, 8, 8, 8)
        var_layout.setSpacing(4)
        for key, label in temp_sens_core.TS_LABELS.items():
            checkbox = QtWidgets.QCheckBox(label, var_group)
            checkbox.setChecked(key in temp_sens_core.PLOT_VARS)
            self._var_checks[key] = checkbox
            var_layout.addWidget(checkbox)
        layout.addWidget(var_group)

        baseline_group = QtWidgets.QGroupBox("Baseline options", container)
        baseline_layout = QtWidgets.QGridLayout(baseline_group)
        baseline_layout.setContentsMargins(8, 8, 8, 8)
        baseline_layout.setHorizontalSpacing(6)
        baseline_layout.setVerticalSpacing(4)
        baseline_combo = QtWidgets.QComboBox(baseline_group)
        baseline_combo.addItem("Do not shift baseline", "none")
        baseline_combo.addItem("Shift to zero at 25Â°C", "zero_25")
        baseline_combo.addItem("Plot both baselines", "both")
        baseline_value = temp_sens_core.BASELINE_MODE if temp_sens_core.BASELINE_MODE in {"none", "zero_25", "both"} else "none"
        baseline_combo.setCurrentIndex({"none": 0, "zero_25": 1, "both": 2}[baseline_value])
        self._baseline_combo = baseline_combo
        baseline_layout.addWidget(QtWidgets.QLabel("Baseline mode:"), 0, 0)
        baseline_layout.addWidget(baseline_combo, 0, 1)
        include_box = QtWidgets.QCheckBox("Include continuous sweeps", baseline_group)
        include_box.setChecked(bool(temp_sens_core.INCLUDE_CONTINUOUS))
        self._include_continuous_checkbox = include_box
        baseline_layout.addWidget(include_box, 1, 0, 1, 2)
        layout.addWidget(baseline_group)

        smooth_group = QtWidgets.QGroupBox("Smoothing", container)
        smooth_layout = QtWidgets.QGridLayout(smooth_group)
        smooth_layout.setContentsMargins(8, 8, 8, 8)
        smooth_layout.setHorizontalSpacing(6)
        smooth_layout.setVerticalSpacing(4)
        med_spin = QtWidgets.QSpinBox(smooth_group)
        med_spin.setRange(1, 9999)
        med_spin.setValue(int(temp_sens_core.MED_WINDOW))
        self._med_spin = med_spin
        ma_spin = QtWidgets.QSpinBox(smooth_group)
        ma_spin.setRange(1, 9999)
        ma_spin.setValue(int(temp_sens_core.MA_WINDOW))
        self._ma_spin = ma_spin
        smooth_layout.addWidget(QtWidgets.QLabel("Median window:"), 0, 0)
        smooth_layout.addWidget(med_spin, 0, 1)
        smooth_layout.addWidget(QtWidgets.QLabel("Moving average window:"), 1, 0)
        smooth_layout.addWidget(ma_spin, 1, 1)
        layout.addWidget(smooth_group)

        output_group = QtWidgets.QGroupBox("Output", container)
        output_layout = QtWidgets.QGridLayout(output_group)
        output_layout.setContentsMargins(8, 8, 8, 8)
        output_layout.setHorizontalSpacing(6)
        output_layout.setVerticalSpacing(4)
        save_checkbox = QtWidgets.QCheckBox("Save plots to disk", output_group)
        save_checkbox.setChecked(bool(temp_sens_core.SAVE_PLOTS))
        self._save_checkbox = save_checkbox
        output_layout.addWidget(save_checkbox, 0, 0, 1, 2)
        output_layout.addWidget(QtWidgets.QLabel("Directory:"), 1, 0)
        output_edit = QtWidgets.QLineEdit(str(temp_sens_core.OUTPUT_DIR), output_group)
        self._output_edit = output_edit
        output_layout.addWidget(output_edit, 1, 1)
        browse_btn = QtWidgets.QPushButton("BrowseÂ°Â°", output_group)
        output_layout.addWidget(browse_btn, 1, 2)
        subfolder_cb = QtWidgets.QCheckBox("Create subfolder", output_group)
        subfolder_cb.setChecked(False)
        self._subfolder_checkbox = subfolder_cb
        output_layout.addWidget(subfolder_cb, 2, 0, 1, 3)
        output_layout.addWidget(QtWidgets.QLabel("Format:"), 3, 0)
        format_combo = QtWidgets.QComboBox(output_group)
        format_combo.addItems(["png", "pdf", "svg"])
        format_combo.setCurrentText(temp_sens_core.SAVE_FORMAT)
        self._format_combo = format_combo
        output_layout.addWidget(format_combo, 3, 1)
        output_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 4, 0)
        dpi_spin = QtWidgets.QSpinBox(output_group)
        dpi_spin.setRange(72, 3000)
        dpi_spin.setValue(int(temp_sens_core.PNG_DPI))
        self._dpi_spin = dpi_spin
        output_layout.addWidget(dpi_spin, 4, 1)
        layout.addWidget(output_group)

        def _browse_output() -> None:
            directory = QtWidgets.QFileDialog.getExistingDirectory(
                self.host,
                "Select output directory",
                output_edit.text() or str(Path.home()),
            )
            if directory:
                output_edit.setText(directory)

        browse_btn.clicked.connect(_browse_output)

        layout.addStretch(1)
        self._settings_widget = container
        return container

    def _log(self, message: str, *, level: str = "info") -> None:
        append = getattr(self.host, "_append_log", None)
        if callable(append):
            try:
                append(message, level=level)
                return
            except Exception:
                pass
        print(message)

    def _selected_variables(self) -> list[str]:
        selected = [key for key, cb in self._var_checks.items() if cb.isChecked() and cb.isEnabled()]
        return selected or ["sum"]

    def _apply_settings_to_core(self) -> dict[str, Any]:
        vars_selected = self._selected_variables()
        temp_sens_core.PLOT_VARS = list(vars_selected)
        baseline_value = "none"
        if isinstance(self._baseline_combo, QtWidgets.QComboBox):
            baseline_value = self._baseline_combo.currentData() or "none"
        if baseline_value not in {"none", "zero_25", "both"}:
            baseline_value = "none"
        temp_sens_core.BASELINE_MODE = baseline_value
        include_cont = bool(self._include_continuous_checkbox and self._include_continuous_checkbox.isChecked())
        temp_sens_core.INCLUDE_CONTINUOUS = include_cont
        if isinstance(self._med_spin, QtWidgets.QSpinBox):
            temp_sens_core.MED_WINDOW = int(self._med_spin.value())
        if isinstance(self._ma_spin, QtWidgets.QSpinBox):
            temp_sens_core.MA_WINDOW = int(self._ma_spin.value())
        save_flag = bool(self._save_checkbox and self._save_checkbox.isChecked())
        temp_sens_core.SAVE_PLOTS = save_flag
        if isinstance(self._format_combo, QtWidgets.QComboBox):
            temp_sens_core.SAVE_FORMAT = self._format_combo.currentText()
        if isinstance(self._dpi_spin, QtWidgets.QSpinBox):
            temp_sens_core.PNG_DPI = int(self._dpi_spin.value())
        base_dir = self._output_edit.text().strip() if isinstance(self._output_edit, QtWidgets.QLineEdit) else str(temp_sens_core.OUTPUT_DIR)
        subfolder = bool(self._subfolder_checkbox and self._subfolder_checkbox.isChecked())
        output_dir = str(temp_sens_core.OUTPUT_DIR)
        if save_flag:
            output_dir = str(prepare_output_dir(base_dir or output_dir, "temperature_sensitivity", subfolder))
            set_last_output_dir(base_dir or output_dir, key="temperature_sensitivity")
        temp_sens_core.OUTPUT_DIR = output_dir
        temp_sens_core.SHOW_PLOTS = False
        temp_sens_core.BACKEND = "matplotlib"
        return {
            "variables": vars_selected,
            "baseline_mode": baseline_value,
            "include_continuous": include_cont,
            "save": save_flag,
            "output_dir": output_dir,
            "med_window": temp_sens_core.MED_WINDOW,
            "ma_window": temp_sens_core.MA_WINDOW,
        }

    def load_data(self) -> None:  # type: ignore[override]
        paths = [path for path in self.host._selected_paths() if path.is_file()]
        if not paths:
            QtWidgets.QMessageBox.warning(self.host, self.name, "Select one or more data files first.")
            return
        string_paths = [str(path) for path in paths]
        try:
            self._data = temp_sens_core.load_data(string_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to load data:\n{exc}")
            self._data = None
            return
        self._loaded_files = string_paths
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        if self._summary_label is not None:
            self._summary_label.setText(f"Loaded {len(self._data)} rows from {len(paths)} file(s).")
        self._log(f"Loaded {len(paths)} temperature sensitivity file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._data is None:
            self.load_data()
        if self._data is None:
            return
        config = self._apply_settings_to_core()
        dataframe = temp_sens_core.maybe_handle_outliers(self._data.copy())
        temp_sens_core.apply_readability_fonts()
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(self._plot_tabs)
        else:
            for tab in self._plot_tabs:
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        self._plot_tabs.clear()
        modes = [config["baseline_mode"]]
        if config["baseline_mode"] == "both":
            modes = ["none", "zero_25"]
        mode_labels = {"none": "Raw", "zero_25": "Zero @25Â°C"}
        plots_created = 0
        grouped = dataframe.groupby(["composition", "anneal"], dropna=False)
        for (_, _), group in grouped:
            for variable in config["variables"]:
                for mode in modes:
                    try:
                        fig, fname = temp_sens_core.plot_variable(
                            group,
                            variable,
                            config["save"],
                            config["output_dir"],
                            baseline_mode=mode,
                            include_cont=config["include_continuous"],
                            med_window=config["med_window"],
                            ma_window=config["ma_window"],
                        )
                    except Exception as exc:
                        self._log(f"Failed to plot {variable} ({mode}): {exc}", level="error")
                        continue
                    if config["baseline_mode"] == "both":
                        path_obj = Path(fname)
                        saved_name = f"{path_obj.stem}_{mode}{path_obj.suffix}"
                    else:
                        saved_name = fname
                    canvas = FigureCanvas(fig)
                    canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
                    tab = QtWidgets.QWidget()
                    tab_layout = QtWidgets.QVBoxLayout(tab)
                    tab_layout.setContentsMargins(0, 0, 0, 0)
                    tab_layout.addWidget(canvas)
                    ax = fig.axes[0] if fig.axes else None
                    title = ax.get_title() if ax else variable
                    x_label = ax.get_xlabel() if ax else "Temperature"
                    y_label = ax.get_ylabel() if ax else variable
                    tab_label = temp_sens_core.TS_LABELS.get(variable, variable)
                    if config["baseline_mode"] == "both":
                        tab_label = f"{tab_label} ({mode_labels.get(mode, mode)})"
                    metadata = {
                        "variable": variable,
                        "baseline_mode": mode,
                        "source_files": list(self._loaded_files),
                        "saved_path": saved_name if config["save"] else "",
                    }
                    if not group.empty:
                        row0 = group.iloc[0]
                        metadata.update({
                            "composition": row0.get("composition", ""),
                            "anneal": row0.get("anneal", ""),
                        })
                    descriptor = TabDescriptor(
                        kind="temperature_sensitivity",
                        title=title,
                        root_label=tab_label,
                        x_label=x_label,
                        y_label=y_label,
                        canvas=canvas,
                        axes=ax,
                        lines={},
                        metadata=metadata,
                    )
                    self.host.tab_widget.addTab(tab, tab_label)
                    self.host._register_plot_tab(tab, canvas, ax, descriptor)
                    self._plot_tabs.append(tab)
                    plots_created += 1
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {plots_created} temperature sensitivity plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            QtWidgets.QMessageBox.information(self.host, self.name, "Load data before exporting to Origin.")
            return
        try:
            self._apply_settings_to_core()
            temp_sens_core.SHOW_PLOTS = False
            temp_sens_core.main(self._loaded_files, backend="origin")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to export to Origin:\n{exc}")
            self._log(f"Origin export failed: {exc}", level="error")
        else:
            self._log("Sent temperature sensitivity plots to Origin.")

    def update_ui(self) -> None:
        has_data = self._data is not None
        if hasattr(self.host, "load_data_button"):
            self.host.load_data_button.setEnabled(True)
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(has_data)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(bool(self._plot_tabs))
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(False)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_data)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(bool(self._plot_tabs))
        self.host._update_project_actions()


class CurrentAnnealingPlugin(PyPlotPlugin):
    """Embed current annealing plotting inside PyPlot."""

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data_by_file: dict[str, pd.DataFrame] = {}
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._save_checkbox: QtWidgets.QCheckBox | None = None
        self._format_combo: QtWidgets.QComboBox | None = None
        self._dpi_spin: QtWidgets.QSpinBox | None = None
        self._output_edit: QtWidgets.QLineEdit | None = None
        self._subfolder_checkbox: QtWidgets.QCheckBox | None = None
        self._origin_mode_combo: QtWidgets.QComboBox | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel("Select current annealing log files then click Load data.")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addStretch(1)
        self._summary_label = summary
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        output_group = QtWidgets.QGroupBox("Output", container)
        output_layout = QtWidgets.QGridLayout(output_group)
        output_layout.setContentsMargins(8, 8, 8, 8)
        output_layout.setHorizontalSpacing(6)
        output_layout.setVerticalSpacing(4)
        save_checkbox = QtWidgets.QCheckBox("Save plots to disk", output_group)
        save_checkbox.setChecked(bool(anneal_core.SAVE_PLOTS))
        self._save_checkbox = save_checkbox
        output_layout.addWidget(save_checkbox, 0, 0, 1, 3)
        output_layout.addWidget(QtWidgets.QLabel("Directory:"), 1, 0)
        output_edit = QtWidgets.QLineEdit(str(anneal_core.OUTPUT_DIR), output_group)
        self._output_edit = output_edit
        output_layout.addWidget(output_edit, 1, 1)
        browse_btn = QtWidgets.QPushButton("BrowseÂ°Â°", output_group)
        output_layout.addWidget(browse_btn, 1, 2)
        subfolder_cb = QtWidgets.QCheckBox("Create subfolder", output_group)
        subfolder_cb.setChecked(False)
        self._subfolder_checkbox = subfolder_cb
        output_layout.addWidget(subfolder_cb, 2, 0, 1, 3)
        output_layout.addWidget(QtWidgets.QLabel("Format:"), 3, 0)
        format_combo = QtWidgets.QComboBox(output_group)
        format_combo.addItems(["png", "pdf", "svg"])
        format_combo.setCurrentText(anneal_core.SAVE_FORMAT)
        self._format_combo = format_combo
        output_layout.addWidget(format_combo, 3, 1)
        output_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 4, 0)
        dpi_spin = QtWidgets.QSpinBox(output_group)
        dpi_spin.setRange(72, 3000)
        dpi_spin.setValue(int(anneal_core.PNG_DPI))
        self._dpi_spin = dpi_spin
        output_layout.addWidget(dpi_spin, 4, 1)
        output_layout.addWidget(QtWidgets.QLabel("Origin mode:"), 5, 0)
        origin_combo = QtWidgets.QComboBox(output_group)
        for mode in anneal_core.ORIGIN_MODES:
            label = "Experimental" if mode == "experimental" else "Simple"
            origin_combo.addItem(label, mode)
        index = origin_combo.findData(anneal_core.ORIGIN_MODE)
        origin_combo.setCurrentIndex(index if index >= 0 else 0)
        self._origin_mode_combo = origin_combo
        output_layout.addWidget(origin_combo, 5, 1)
        layout.addWidget(output_group)

        def _browse_output() -> None:
            directory = QtWidgets.QFileDialog.getExistingDirectory(
                self.host,
                "Select output directory",
                output_edit.text() or str(Path.home()),
            )
            if directory:
                output_edit.setText(directory)

        browse_btn.clicked.connect(_browse_output)

        layout.addStretch(1)
        self._settings_widget = container
        return container

    def _log(self, message: str, *, level: str = "info") -> None:
        append = getattr(self.host, "_append_log", None)
        if callable(append):
            try:
                append(message, level=level)
                return
            except Exception:
                pass
        print(message)

    def _apply_settings_to_core(self) -> dict[str, Any]:
        save_flag = bool(self._save_checkbox and self._save_checkbox.isChecked())
        anneal_core.SAVE_PLOTS = save_flag
        base_dir = self._output_edit.text().strip() if isinstance(self._output_edit, QtWidgets.QLineEdit) else str(anneal_core.OUTPUT_DIR)
        subfolder = bool(self._subfolder_checkbox and self._subfolder_checkbox.isChecked())
        output_dir = str(anneal_core.OUTPUT_DIR)
        if save_flag:
            output_dir = str(prepare_output_dir(base_dir or output_dir, "current_annealing", subfolder))
            set_last_output_dir(base_dir or output_dir, key="current_annealing")
        anneal_core.OUTPUT_DIR = output_dir
        if isinstance(self._format_combo, QtWidgets.QComboBox):
            anneal_core.SAVE_FORMAT = self._format_combo.currentText()
        if isinstance(self._dpi_spin, QtWidgets.QSpinBox):
            anneal_core.PNG_DPI = int(self._dpi_spin.value())
        if isinstance(self._origin_mode_combo, QtWidgets.QComboBox):
            mode = self._origin_mode_combo.currentData()
            if isinstance(mode, str) and mode:
                anneal_core.ORIGIN_MODE = mode
        anneal_core.SHOW_PLOTS = False
        anneal_core.BACKEND = "matplotlib"
        return {"save": save_flag, "output_dir": output_dir}

    def load_data(self) -> None:  # type: ignore[override]
        paths = [path for path in self.host._selected_paths() if path.is_file()]
        if not paths:
            QtWidgets.QMessageBox.warning(self.host, self.name, "Select one or more data files first.")
            return
        data_by_file: dict[str, pd.DataFrame] = {}
        errors: list[str] = []
        for path in paths:
            try:
                data = anneal_core.load_file(str(path))
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            data_by_file[str(path)] = data
        if errors:
            QtWidgets.QMessageBox.warning(self.host, self.name, "Some files could not be loaded:\n" + "\n".join(errors[:10]))
        if not data_by_file:
            self._data_by_file = {}
            self._loaded_files = []
            self._summary_label.setText("No valid current annealing files were loaded.") if self._summary_label else None
            self.update_ui()
            return
        self._data_by_file = data_by_file
        self._loaded_files = list(data_by_file.keys())
        if self._summary_label is not None:
            self._summary_label.setText(f"Loaded {len(data_by_file)} file(s).")
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        self._log(f"Loaded {len(data_by_file)} current annealing file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._data_by_file:
            self.load_data()
        if not self._data_by_file:
            return
        config = self._apply_settings_to_core()
        anneal_core.apply_readability_fonts()
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(self._plot_tabs)
        else:
            for tab in self._plot_tabs:
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        self._plot_tabs.clear()
        plots_created = 0
        for path_str in sorted(self._data_by_file.keys()):
            df = self._data_by_file[path_str]
            title = format_annealing_title(Path(path_str).stem)
            try:
                fig, fname = anneal_core.plot_one(df, title)
            except Exception as exc:
                self._log(f"Failed to plot {Path(path_str).name}: {exc}", level="error")
                continue
            saved_path = ""
            if config["save"]:
                target_dir = Path(config["output_dir"])
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                try:
                    anneal_core.save_figure(fig, target_dir / fname, anneal_core.SAVE_FORMAT, anneal_core.PNG_DPI)
                    saved_path = str(target_dir / fname)
                except Exception as exc:
                    self._log(f"Failed to save {fname}: {exc}", level="error")
            canvas = FigureCanvas(fig)
            canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            ax = fig.axes[0] if fig.axes else None
            descriptor = TabDescriptor(
                kind="current_annealing",
                title=ax.get_title() if ax else title,
                root_label=Path(path_str).name,
                x_label=ax.get_xlabel() if ax else "Current (mA)",
                y_label=ax.get_ylabel() if ax else "Resistance",
                canvas=canvas,
                axes=ax,
                lines={},
                metadata={
                    "source_file": path_str,
                    "saved_path": saved_path,
                    "origin_mode": anneal_core.ORIGIN_MODE,
                },
            )
            self.host.tab_widget.addTab(tab, Path(path_str).name)
            self.host._register_plot_tab(tab, canvas, ax, descriptor)
            self._plot_tabs.append(tab)
            plots_created += 1
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {plots_created} current annealing plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            QtWidgets.QMessageBox.information(self.host, self.name, "Load data before exporting to Origin.")
            return
        try:
            self._apply_settings_to_core()
            anneal_core.SHOW_PLOTS = False
            anneal_core.main(self._loaded_files, backend="origin")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to export to Origin:\n{exc}")
            self._log(f"Origin export failed: {exc}", level="error")
        else:
            self._log("Sent current annealing plots to Origin.")

    def update_ui(self) -> None:
        has_data = bool(self._data_by_file)
        if hasattr(self.host, "load_data_button"):
            self.host.load_data_button.setEnabled(True)
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(has_data)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(bool(self._plot_tabs))
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(False)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_data)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(bool(self._plot_tabs))
        self.host._update_project_actions()


class VSMHysteresisPlugin(PyPlotPlugin):
    """PyPlot plugin wrapper around :class:`VSMPlotter`."""

    _METHOD_EXCLUDES = {"__init__", "_selected_paths", "_create_dock_widget", "_create_dock_switcher"}

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._initialized = False
        self._menus_ready = False
        self._summary_label: QtWidgets.QLabel | None = None
        self._settings_loaded = False
        self._controls_connected = False

    # Lifecycle -----------------------------------------------------
    def activate(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    # UI helpers ----------------------------------------------------
    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel(
            "Select one or more VSM hysteresis files and click Load data."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addStretch(1)
        self._summary_label = summary
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget

        host = self.host
        container = QtWidgets.QWidget(host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        axes_group = QtWidgets.QGroupBox("Axes and filters", container)
        axes_group.setFlat(True)
        axes_form = QtWidgets.QFormLayout(axes_group)
        axes_form.setContentsMargins(8, 8, 8, 8)
        axes_form.setSpacing(6)
        host.temperature_combo = QtWidgets.QComboBox(axes_group)
        host.temperature_combo.addItem("All temperatures", None)
        axes_form.addRow("Temperature", host.temperature_combo)

        host.x_axis_combo = QtWidgets.QComboBox(axes_group)
        host.y_axis_combo = QtWidgets.QComboBox(axes_group)
        axes_form.addRow("X axis", host.x_axis_combo)
        axes_form.addRow("Y axis", host.y_axis_combo)
        layout.addWidget(axes_group)

        appearance_group = QtWidgets.QGroupBox("Appearance", container)
        appearance_group.setFlat(True)
        appearance_layout = QtWidgets.QVBoxLayout(appearance_group)
        appearance_layout.setContentsMargins(8, 8, 8, 8)
        appearance_layout.setSpacing(6)
        host.style_combo = QtWidgets.QComboBox(appearance_group)
        host.style_combo.addItem("Line", "line")
        host.style_combo.addItem("Line + symbols", "line_markers")
        appearance_layout.addWidget(QtWidgets.QLabel("Matplotlib style", appearance_group))
        appearance_layout.addWidget(host.style_combo)

        host.dark_mode_checkbox = QtWidgets.QCheckBox("Dark plot theme", appearance_group)
        host.dark_mode_checkbox.setToolTip(
            "Render Matplotlib plots using a dark background theme."
        )
        appearance_layout.addWidget(host.dark_mode_checkbox)

        host.field_direction_button = QtWidgets.QPushButton("Highlight field direction", appearance_group)
        host.field_direction_button.setCheckable(True)
        host.field_direction_button.setToolTip(
            "Use solid lines for increasing magnetic field and dashed lines for decreasing segments."
        )
        appearance_layout.addWidget(host.field_direction_button)
        layout.addWidget(appearance_group)

        overlay_group = QtWidgets.QGroupBox("Angle overlays", container)
        overlay_group.setFlat(True)
        overlay_layout = QtWidgets.QVBoxLayout(overlay_group)
        overlay_layout.setContentsMargins(8, 8, 8, 8)
        overlay_layout.setSpacing(6)
        host.angle_overlay_list = QtWidgets.QListWidget(overlay_group)
        host.angle_overlay_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        overlay_layout.addWidget(host.angle_overlay_list, 1)
        overlay_hint = QtWidgets.QLabel(
            "Select rotations to compare across temperatures or when exporting overlays.",
            overlay_group,
        )
        overlay_hint.setWordWrap(True)
        overlay_layout.addWidget(overlay_hint)
        host.angle_overlay_button = QtWidgets.QPushButton(
            "Plot selected angles across temperatures", overlay_group
        )
        host.angle_overlay_button.setEnabled(False)
        overlay_layout.addWidget(host.angle_overlay_button)
        layout.addWidget(overlay_group, 1)

        metrics_group = QtWidgets.QGroupBox("Derived metrics", container)
        metrics_group.setFlat(True)
        metrics_layout = QtWidgets.QVBoxLayout(metrics_group)
        metrics_layout.setContentsMargins(8, 8, 8, 8)
        metrics_layout.setSpacing(6)
        host.metrics_angle_button = QtWidgets.QPushButton("Plot metrics vs angle", metrics_group)
        host.metrics_angle_button.setEnabled(False)
        metrics_layout.addWidget(host.metrics_angle_button)
        host.metrics_temperature_button = QtWidgets.QPushButton(
            "Plot metrics vs temperature", metrics_group
        )
        host.metrics_temperature_button.setEnabled(False)
        metrics_layout.addWidget(host.metrics_temperature_button)
        layout.addWidget(metrics_group)

        layout.addStretch(1)
        self._settings_widget = container
        self._controls_connected = False
        return container

    def _connect_control_signals(self) -> None:
        if self._controls_connected:
            return
        host = self.host
        if hasattr(host, "x_axis_combo") and callable(getattr(host, "_store_axis_selection", None)):
            host.x_axis_combo.currentTextChanged.connect(lambda _: host._store_axis_selection())
        if hasattr(host, "y_axis_combo") and callable(getattr(host, "_store_axis_selection", None)):
            host.y_axis_combo.currentTextChanged.connect(lambda _: host._store_axis_selection())
        if hasattr(host, "style_combo") and callable(getattr(host, "_restyle_plots", None)):
            host.style_combo.currentIndexChanged.connect(lambda _: host._restyle_plots())
        if hasattr(host, "dark_mode_checkbox") and callable(getattr(host, "_restyle_plots", None)):
            host.dark_mode_checkbox.toggled.connect(lambda _: host._restyle_plots())
        if hasattr(host, "field_direction_button") and callable(
            getattr(host, "_handle_field_direction_toggle", None)
        ):
            host.field_direction_button.toggled.connect(host._handle_field_direction_toggle)
        if hasattr(host, "angle_overlay_list") and callable(
            getattr(host, "_update_overlay_button_state", None)
        ):
            host.angle_overlay_list.itemSelectionChanged.connect(host._update_overlay_button_state)
        if hasattr(host, "angle_overlay_button") and callable(
            getattr(host, "_plot_angle_overlays", None)
        ):
            host.angle_overlay_button.clicked.connect(host._plot_angle_overlays)
        if hasattr(host, "metrics_angle_button") and callable(
            getattr(host, "_plot_metrics_vs_angle", None)
        ):
            host.metrics_angle_button.clicked.connect(host._plot_metrics_vs_angle)
        if hasattr(host, "metrics_temperature_button") and callable(
            getattr(host, "_plot_metrics_vs_temperature", None)
        ):
            host.metrics_temperature_button.clicked.connect(host._plot_metrics_vs_temperature)
        self._controls_connected = True

    # Host actions --------------------------------------------------
    def load_data(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        host = self.host
        paths = host._selected_paths()
        if not paths:
            imported = self._collect_imported_vsm_sources()
            if imported:
                formatted = host._format_paths(imported)
                host.path_edit.setText(formatted)
                host._apply_path_text(formatted)
                host._update_action_states()
                paths = host._selected_paths()
        if not paths:
            if not self._open_data_menu():
                try:
                    host._choose_files()
                except Exception:
                    host.logger.exception("Failed to open file dialog for VSM data selection")
            return
        host._load_measurements()

    def generate(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._generate_plots()

    def open_matplotlib(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._open_matplotlib_window()

    def save_graph(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._save_current_graph()

    def normalize(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._normalize_current_graph()

    def export_txt(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._export_txt()

    def open_origin(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._open_origin_prompt()

    # UI state ------------------------------------------------------
    def update_ui(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        host = self.host
        has_paths = bool(host._selected_paths())
        if hasattr(host, "load_data_button"):
            host.load_data_button.setEnabled(True)
        if self._summary_label is not None and not has_paths:
            self._summary_label.setText(
                "Select one or more VSM hysteresis files and click Load data."
            )
        if hasattr(host, "plot_button"):
            host.plot_button.setEnabled(has_paths or bool(host.path_edit.text().strip()))
            host.plot_button.setText("Generate VSM Hysteresis Loops")
        if hasattr(host, "_update_save_graph_enabled"):
            host._update_save_graph_enabled()
        if hasattr(host, "_update_normalize_enabled"):
            host._update_normalize_enabled()
        if hasattr(host, "_update_project_actions"):
            host._update_project_actions()

    # Internal helpers ---------------------------------------------
    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        host = self.host
        self.settings_widget()
        host.logger = logging.getLogger("vsm_hysteresis_loops")
        host.logger.setLevel(logging.INFO)
        host.settings = QtCore.QSettings("MicrowireLab", "VSMHysteresisLoops")

        stored_x = host.settings.value("x_axis")
        stored_y = host.settings.value("y_axis")
        host._stored_axes = (
            stored_x if isinstance(stored_x, str) and stored_x else None,
            stored_y if isinstance(stored_y, str) and stored_y else None,
        )
        host.last_export_path = None

        host.measurements = []
        host._last_rescale_info = {}
        host._last_axes = None
        host._last_rescale_enabled = False
        host._line_visibility = {}
        host._worksheet_models = {}
        host._plotted_series_exports = {}
        host._metrics_by_temperature = {}
        host._metrics_by_angle = {}
        host._metric_column_names = {}
        host._metric_results = {}
        host._metric_debug_tables = {}
        host._metric_debug_columns = {}
        host._metric_debug_windows = {}
        host._last_graph_dir = None
        host._field_direction_enabled = False
        host._direction_legends = {}
        host._last_source_dir = None
        host.last_export_path = None
        host._base_title = "VSM Hysteresis Loops"
        host.PROJECT_EXTENSION = VSMPlotter.PROJECT_EXTENSION
        host.PROJECT_VERSION = VSMPlotter.PROJECT_VERSION
        host.PROJECT_CODE = VSMPlotter.PROJECT_CODE
        host.PROJECT_SETTINGS_PREFIX = VSMPlotter.PROJECT_SETTINGS_PREFIX

        self._bind_methods()
        if hasattr(host, "_retabify_primary_docks"):
            try:
                host._retabify_primary_docks()
            except Exception:
                host.logger.exception("Failed to retabify primary docks")
        self._connect_control_signals()
        if not self._menus_ready:
            VSMPlotter._extend_menus(host, host.menuBar())
            self._menus_ready = True

        if not self._settings_loaded:
            try:
                previous = bool(getattr(host, "_suppress_window_persistence", False))
                host._suppress_window_persistence = True
                host._load_settings()
            except Exception:
                host.logger.exception("Failed to load saved VSM settings")
            else:
                self._settings_loaded = True
            finally:
                if not previous:
                    try:
                        delattr(host, "_suppress_window_persistence")
                    except AttributeError:
                        pass

        if hasattr(host, "_ensure_window_visibility"):
            try:
                host._ensure_window_visibility()
            except Exception:
                host.logger.exception("Failed to clamp PyPlot window to the active screen")

        self._initialized = True

    def _collect_imported_vsm_sources(self) -> List[Path]:
        host = self.host
        worksheets = getattr(host, "_worksheets", {})
        if not isinstance(worksheets, dict) or not worksheets:
            return []
        ordered: List[Path] = []
        seen: set[Path] = set()
        for worksheet in worksheets.values():
            source = getattr(worksheet, "source", None)
            if not isinstance(source, Path):
                continue
            if not _looks_like_vsm_name(source.name):
                continue
            if source in seen:
                continue
            seen.add(source)
            ordered.append(source)
        return ordered

    def _open_data_menu(self) -> bool:
        data_menu = getattr(self.host, "_data_menu", None)
        if not isinstance(data_menu, QtWidgets.QMenu):
            return False
        menu_bar = self.host.menuBar() if hasattr(self.host, "menuBar") else None
        global_pos: QtCore.QPoint
        action = data_menu.menuAction()
        if isinstance(menu_bar, QtWidgets.QMenuBar):
            rect = menu_bar.actionGeometry(action)
            anchor = rect.bottomLeft() if rect.isValid() else QtCore.QPoint(
                0, menu_bar.height()
            )
            global_pos = menu_bar.mapToGlobal(anchor)
            menu_bar.setActiveAction(action)
        else:
            button = getattr(self.host, "load_data_button", None)
            if isinstance(button, QtWidgets.QPushButton):
                global_pos = button.mapToGlobal(button.rect().bottomLeft())
            else:
                global_pos = self.host.mapToGlobal(QtCore.QPoint(0, 0))
        data_menu.popup(global_pos)
        return True

    def _bind_methods(self) -> None:
        host = self.host
        if getattr(host, "_vsm_methods_bound", False):
            return
        for name, func in inspect.getmembers(VSMPlotter, inspect.isfunction):
            if name in self._METHOD_EXCLUDES:
                continue
            setattr(host, name, types.MethodType(func, host))
        host._vsm_methods_bound = True


class StressDependencePlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        try:
            stress_gui.orig.ProgressDialog = stress_gui.ProgressDialog
        except Exception:
            pass
        return stress_gui.SettingsDialog()


class StressSensitivityPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        try:
            sens_gui.orig.ProgressDialog = sens_gui.ProgressDialog
        except Exception:
            pass
        return sens_gui.SettingsDialog()


class HswLoadComparePlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        try:
            load_compare_gui.orig.ProgressDialog = load_compare_gui.ProgressDialog
        except Exception:
            pass
        return load_compare_gui.SettingsDialog()


class MaxionContinuousPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        try:
            maxion_gui.orig.ProgressDialog = maxion_gui.ProgressDialog
        except Exception:
            pass
        return maxion_gui.SettingsDialog()


class PdfPlotterPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        return pdf_gui.PdfPlotterWindow()


class HysteresisLoopsPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        return loops_gui.SettingsDialog()


class HswDistributionPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        return distribution_gui.SettingsDialog()


class Strain3DPlotPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_widget)

    @staticmethod
    def _create_widget() -> QtWidgets.QWidget:
        return Strain3DPlotter()


class PyPlotWorkbench(PyPlotWindow):
    """Lightweight harness for exercising shared PyPlotWindow features."""

    help_topic = "pyplot"
    PROJECT_EXTENSION = ".pypj"
    PROJECT_CODE = "pyplot"
    PROJECT_SETTINGS_PREFIX = "pyplot"

    def __init__(
        self,
        *,
        plotters: Dict[str, Callable[["PyPlotWorkbench"], PyPlotPlugin]] | None = None,
        initial_plotter: str | None = None,
    ) -> None:
        self.settings = QtCore.QSettings("MicrowireLab", "PyPlotWorkbench")
        raw_dirs = self.settings.value("plugin_last_dirs", "{}")
        try:
            parsed_dirs = json.loads(raw_dirs) if isinstance(raw_dirs, str) else {}
        except json.JSONDecodeError:
            parsed_dirs = {}
        self._plugin_last_directories: Dict[str, Path] = {
            key: Path(value) for key, value in parsed_dirs.items() if isinstance(value, str)
        }
        self._last_directory: Path | None = None
        self._last_source_dir: Path | None = None
        self._selected_path_entries: List[Path] = []
        self._plugin_factories: Dict[
            str, Callable[["PyPlotWorkbench"], PyPlotPlugin]
        ] = dict(sorted((plotters or {}).items()))
        self._plugin_instances: Dict[str, PyPlotPlugin] = {}
        self._current_plugin: PyPlotPlugin | None = None
        self._current_plotter_name: str | None = None
        self._plotter_combo: QtWidgets.QComboBox | None = None
        self._plugin_settings_container: QtWidgets.QWidget | None = None
        self._plugin_settings_layout: QtWidgets.QVBoxLayout | None = None
        self._active_plugin_updater: Callable[[], None] | None = None
        self._initial_plotter = initial_plotter
        self._plotter_history: list[str] = self._load_plotter_history()
        self._spawned_windows: list[PyPlotWorkbench] = []
        super().__init__(title="PyPlot")
        self.setObjectName("PyPlotWorkbench")
        try:
            self.setWindowState(
                self.windowState() | QtCore.Qt.WindowState.WindowMaximized
            )
        except Exception:
            pass
        self.tab_widget.currentChanged.connect(lambda _: self._update_action_states())

        stored_sources = self.settings.value("sources", "")
        if isinstance(stored_sources, str) and stored_sources.strip():
            self._apply_path_text(stored_sources)
            self.path_edit.setText(stored_sources)

        stored_directory = self.settings.value("last_directory", "")
        if isinstance(stored_directory, str) and stored_directory:
            candidate = Path(stored_directory)
            if candidate.exists():
                self._last_directory = candidate

        self._update_action_states()
        self._set_data_sources_visible(False)
        self._select_initial_plotter()
        self._update_window_title()


    def _update_window_title(self) -> None:
        parts = ["PyPlot"]
        if self._current_plotter_name:
            parts.append(self._current_plotter_name)
        project_path = getattr(self, "_project_path", None)
        if isinstance(project_path, Path) and project_path.name:
            parts.append(project_path.name)
        else:
            parts.append("UNTITLED")
        self.setWindowTitle(" - ".join(parts))

    def _load_plotter_history(self) -> list[str]:
        stored = self.settings.value("plotter_history", "[]")
        if isinstance(stored, str):
            try:
                parsed = json.loads(stored)
            except Exception:
                parsed = []
        elif isinstance(stored, (list, tuple)):
            parsed = list(stored)
        else:
            parsed = []
        history = [str(name) for name in parsed if isinstance(name, str)]
        return history

    def _save_plotter_history(self) -> None:
        try:
            self.settings.setValue("plotter_history", json.dumps(self._plotter_history))
        except Exception:
            pass

    def _ordered_plotter_names(self) -> list[str]:
        names = list(self._plugin_factories.keys())
        ordered: list[str] = []
        for entry in self._plotter_history:
            if entry in names and entry not in ordered:
                ordered.append(entry)
        for name in sorted(names):
            if name not in ordered:
                ordered.append(name)
        return ordered

    def _refresh_plotter_combo(self) -> None:
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        if combo is None:
            return
        current = self._current_plotter_name
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Select a script.", None)
        for name in self._ordered_plotter_names():
            combo.addItem(name, name)
        if current:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _remember_plotter_usage(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            return
        history = [name]
        history.extend(entry for entry in self._plotter_history if entry != name)
        self._plotter_history = history[:20]
        self._save_plotter_history()
        self._refresh_plotter_combo()

    def _select_initial_plotter(self) -> None:
        if not self._plugin_factories:
            self._apply_selected_plotter()
            return
        target = self._initial_plotter if self._initial_plotter in self._plugin_factories else None
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        if combo is not None:
            combo.blockSignals(True)
            if target is None:
                combo.setCurrentIndex(0)
            else:
                index = combo.findData(target)
                if index < 0:
                    index = 0
                combo.setCurrentIndex(index)
            combo.blockSignals(False)
        if target is not None:
            self._apply_selected_plotter()
        else:
            self._current_plugin = None
            self._current_plotter_name = None
            self._set_script_panel(None)
            self._set_plugin_settings_widget(None)
            self._active_plugin_updater = None
            self._update_action_states()
        return
    def _dialog_start_directory(self) -> Path:
        if self._current_plotter_name:
            stored = self._plugin_last_directories.get(self._current_plotter_name)
            if stored is not None and stored.exists():
                return stored
        return super()._dialog_start_directory()

    # ------------------------------------------------------------------ Qt hooks
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self._current_plugin is not None:
            try:
                self._current_plugin.deactivate()
            except Exception:
                pass
        payload = {key: str(value) for key, value in self._plugin_last_directories.items()}
        self.settings.setValue("plugin_last_dirs", json.dumps(payload))
        self.settings.setValue("sources", self.path_edit.text())
        if self._last_directory is not None:
            self.settings.setValue("last_directory", str(self._last_directory))
        self.settings.sync()
        super().closeEvent(event)

    # ------------------------------------------------------------------ project and data integration
    def _load_data(self) -> None:
        if self._current_plugin is None:
            QtWidgets.QMessageBox.information(
                self,
                "PyPlot",
                "Select a plotting script before loading data.",
            )
            return
        self._current_plugin.load_data()
        self._update_action_states()

    def _update_action_states(self) -> None:
        if self._current_plugin is not None:
            try:
                self._current_plugin.update_ui()
            except Exception:
                pass
            return
        if hasattr(self, "load_data_button"):
            self.load_data_button.setEnabled(False)
        if hasattr(self, "plot_button"):
            self.plot_button.setEnabled(False)
            self.plot_button.setText("Generate")
        if hasattr(self, "popout_button"):
            self.popout_button.setEnabled(False)
        if hasattr(self, "save_graph_button"):
            self.save_graph_button.setEnabled(False)
        if hasattr(self, "normalize_button"):
            self.normalize_button.setEnabled(False)
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(False)
        if hasattr(self, "open_origin_button"):
            self.open_origin_button.setEnabled(False)

    def _import_paths(self, paths: Iterable[Path]) -> None:
        super()._import_paths(paths)
        if self._current_plotter_name and self._last_directory is not None:
            self._plugin_last_directories[self._current_plotter_name] = self._last_directory
            payload = {key: str(value) for key, value in self._plugin_last_directories.items()}
            self.settings.setValue("plugin_last_dirs", json.dumps(payload))
        self._update_action_states()
        self._update_project_actions()
        self.settings.setValue("sources", self.path_edit.text())
        self.settings.sync()
    def _has_project_data_to_save(self) -> bool:
        measurements = getattr(self, "measurements", None)
        if isinstance(measurements, list) and measurements:
            return True
        return bool(self._worksheets)

    def _reset_project_state(self) -> None:
        super()._reset_project_state()
        self._clear_imported_data()
        self._selected_path_entries = []
        self.path_edit.clear()
        self._update_action_states()
        self._update_project_actions()

    def _build_project_payload(self, *, base_path: Path | None) -> Dict[str, Any]:
        selected_payload = [
            self._portable_path(path, base_path) for path in self._selected_path_entries
        ]
        workbooks_payload: List[Dict[str, Any]] = []
        for workbook in self._workbooks.values():
            worksheets_payload: List[Dict[str, Any]] = []
            for worksheet_key in workbook.worksheets:
                worksheet = self._worksheets.get(worksheet_key)
                if worksheet is None:
                    continue
                table = worksheet.dataframe.astype(object).where(
                    pd.notnull(worksheet.dataframe), None
                )
                records = table.to_dict(orient="records")
                metadata_payload = {
                    column: asdict(worksheet.columns.get(column, WorksheetColumnMeta()))
                    for column in table.columns
                }
                index_values = [
                    value if isinstance(value, (int, float, str)) or value is None else str(value)
                    for value in table.index.tolist()
                ]
                worksheet_payload = {
                    "key": str(worksheet.key),
                    "name": worksheet.name,
                    "columns": [str(col) for col in table.columns],
                    "records": records,
                    "index": index_values,
                    "metadata": metadata_payload,
                    "source": self._portable_path(worksheet.source, base_path),
                }
                worksheets_payload.append(worksheet_payload)
            if not worksheets_payload:
                continue
            workbook_payload = {
                "key": str(workbook.key),
                "name": workbook.name,
                "source": self._portable_path(workbook.source, base_path),
                "folder": self._portable_path(workbook.folder, base_path),
                "worksheets": worksheets_payload,
            }
            workbooks_payload.append(workbook_payload)
        return {
            "selected_paths": selected_payload,
            "workbooks": workbooks_payload,
        }

    def _apply_project_payload(self, payload: Dict[str, Any], *, project_dir: Path) -> bool:
        self._clear_imported_data()
        selected_payload = payload.get("selected_paths")
        self._selected_path_entries = []
        if isinstance(selected_payload, list):
            for entry in selected_payload:
                if isinstance(entry, str) and entry:
                    resolved = self._resolve_portable_path(entry, project_dir)
                    if resolved is not None:
                        self._selected_path_entries.append(resolved)
        if self._selected_path_entries:
            self.path_edit.setText(self._format_paths(self._selected_path_entries))
            self._remember_directory_from_paths(self._selected_path_entries)
        else:
            self.path_edit.clear()

        workbooks_payload = payload.get("workbooks")
        imported = False
        if isinstance(workbooks_payload, list):
            for workbook_entry in workbooks_payload:
                if not isinstance(workbook_entry, dict):
                    continue
                workbook_key = workbook_entry.get("key") or str(uuid.uuid4())
                workbook = WorkbookData(
                    key=workbook_key,
                    name=str(workbook_entry.get("name") or "Workbook"),
                    source=self._resolve_portable_path(workbook_entry.get("source"), project_dir),
                    folder=self._resolve_portable_path(workbook_entry.get("folder"), project_dir),
                )
                worksheets_payload = workbook_entry.get("worksheets")
                worksheet_objects: List[WorksheetData] = []
                if isinstance(worksheets_payload, list):
                    for sheet_entry in worksheets_payload:
                        if not isinstance(sheet_entry, dict):
                            continue
                        columns = sheet_entry.get("columns")
                        records = sheet_entry.get("records")
                        df = (
                            pd.DataFrame(records, columns=columns)
                            if isinstance(records, list)
                            else pd.DataFrame(columns=columns or [])
                        )
                        index_values = sheet_entry.get("index")
                        if isinstance(index_values, list) and len(index_values) == len(df):
                            df.index = pd.Index(index_values)
                        metadata_payload = sheet_entry.get("metadata")
                        columns_meta: Dict[str, WorksheetColumnMeta] = {}
                        for column in df.columns:
                            meta_dict = (
                                metadata_payload.get(column)
                                if isinstance(metadata_payload, dict)
                                else None
                            )
                            if isinstance(meta_dict, dict):
                                columns_meta[column] = WorksheetColumnMeta(
                                    long_name=str(meta_dict.get("long_name", "")),
                                    units=str(meta_dict.get("units", "")),
                                    comments=str(meta_dict.get("comments", "")),
                                    formula=str(meta_dict.get("formula", "")),
                                )
                            else:
                                columns_meta[column] = WorksheetColumnMeta(long_name=str(column))
                        worksheet_key = sheet_entry.get("key") or f"{workbook_key}::{sheet_entry.get('name', 'Sheet')}"
                        worksheet = WorksheetData(
                            key=worksheet_key,
                            name=str(sheet_entry.get("name") or "Sheet"),
                            dataframe=df,
                            columns=columns_meta,
                            source=self._resolve_portable_path(sheet_entry.get("source"), project_dir),
                            workbook_key=workbook_key,
                        )
                        worksheet_objects.append(worksheet)
                if worksheet_objects:
                    self._register_imported_workbook(workbook, worksheet_objects)
                    imported = True
        if imported:
            self._refresh_imported_data_summary()
        self._update_action_states()
        self._update_project_actions()
        return True

    def _clear_imported_data(self) -> None:
        for key in list(self._worksheet_tabs_open.keys()):
            self._remove_worksheet(key)
        self._workbooks.clear()
        self._worksheets.clear()
        self._data_workbook_items.clear()
        self._data_folder_items.clear()
        self._worksheet_tree_items.clear()
        if self._data_tree_root is not None:
            index = self.project_tree.indexOfTopLevelItem(self._data_tree_root)
            if index >= 0:
                self.project_tree.takeTopLevelItem(index)
            self._data_tree_root = None
        self._refresh_imported_data_summary()
        if self._refresh_import_action is not None:
            self._refresh_import_action.setEnabled(False)

    def _portable_path(self, path: Path | None, base_path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if base_path is not None:
            try:
                return str(resolved.relative_to(base_path.resolve()))
            except Exception:
                pass
        return str(resolved)

    def _resolve_portable_path(self, value: str | None, project_dir: Path) -> Path | None:
        if not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (project_dir / candidate).resolve()
        return candidate

    # ------------------------------------------------------------------ abstract implementations
    def _handle_manual_path_entry(self) -> None:
        self._apply_path_text(self.path_edit.text())
        self._update_action_states()

    def _choose_files(self) -> None:
        start = self._dialog_start_directory()
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select data files",
            str(start),
        )
        if not paths:
            return
        self._selected_path_entries = [Path(entry) for entry in paths]
        self._remember_directory_from_paths(self._selected_path_entries)
        self.path_edit.setText(self._format_paths(self._selected_path_entries))
        self._update_action_states()

    def _choose_folder(self) -> None:
        start = self._dialog_start_directory()
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select data folder",
            str(start),
        )
        if not directory:
            return
        folder = Path(directory)
        self._selected_path_entries = [folder]
        self._last_directory = folder
        self.path_edit.setText(self._format_paths(self._selected_path_entries))
        self._update_action_states()

    def _generate_plots(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.generate()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Select a plotting script before generating plots.",
        )
    def _open_matplotlib_window(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.open_matplotlib()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Select a plotting script that supports Matplotlib export.",
        )
    def _save_current_graph(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.save_graph()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Plot a graph before saving.",
        )
    def _normalize_current_graph(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.normalize()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Plot a graph before normalizing.",
        )
    def _export_txt(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.export_txt()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Generate data before exporting.",
        )
    def _open_origin_prompt(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.open_origin()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Origin export is not available for the selected script.",
        )
    def _populate_graph_settings(self, layout: QtWidgets.QVBoxLayout) -> None:
        label = QtWidgets.QLabel("Configure graph settings in your plotter subclass.")
        label.setWordWrap(True)
        layout.addWidget(label)

        group = QtWidgets.QGroupBox("Select plotting script")
        group_layout = QtWidgets.QVBoxLayout(group)
        group_layout.setContentsMargins(8, 8, 8, 8)
        group_layout.setSpacing(6)
        self._plotter_combo = QtWidgets.QComboBox(group)
        self._plotter_combo.currentIndexChanged.connect(lambda _: self._apply_selected_plotter())
        group_layout.addWidget(self._plotter_combo)
        self._refresh_plotter_combo()
        if not self._plugin_factories:
            self._plotter_combo.setEnabled(False)
        layout.addWidget(group)

        self._plugin_settings_container = QtWidgets.QWidget(self)
        self._plugin_settings_layout = QtWidgets.QVBoxLayout(self._plugin_settings_container)
        self._plugin_settings_layout.setContentsMargins(0, 0, 0, 0)
        self._plugin_settings_layout.setSpacing(6)
        layout.addWidget(self._plugin_settings_container)
        self._plugin_settings_container.setVisible(False)

    def _set_plugin_settings_widget(self, widget: QtWidgets.QWidget | None) -> None:
        if self._plugin_settings_layout is None or self._plugin_settings_container is None:
            return
        layout = self._plugin_settings_layout
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            child = item.widget()
            if child is not None:
                child.setParent(None)
            del item
        if widget is None:
            self._plugin_settings_container.setVisible(False)
        else:
            layout.addWidget(widget)
            self._plugin_settings_container.setVisible(True)

    def _apply_selected_plotter(self) -> None:
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        name = combo.currentData() if combo is not None else None
        if not name:
            if self._current_plugin is not None:
                self._current_plugin.deactivate()
            self._current_plugin = None
            self._current_plotter_name = None
            self._set_script_panel(None)
            self._set_plugin_settings_widget(None)
            self._active_plugin_updater = None
            self._update_window_title()
            self._update_action_states()
            return

        plugin = self._plugin_instances.get(name)
        if plugin is None:
            factory = self._plugin_factories.get(name)
            if factory is None:
                plugin = ExternalPlotterPlugin(self, name, lambda: None)
            else:
                plugin = factory(self)
            self._plugin_instances[name] = plugin

        if self._current_plugin is not plugin:
            if self._current_plugin is not None:
                self._current_plugin.deactivate()
            self._current_plugin = plugin
            self._current_plotter_name = name
            self._set_script_panel(plugin.panel_widget())
            self._set_plugin_settings_widget(plugin.settings_widget())
            plugin.activate()
            last_dir = self._plugin_last_directories.get(name)
            if last_dir is not None:
                self._last_directory = last_dir if last_dir.exists() else self._last_directory

        self._active_plugin_updater = plugin.update_ui
        if self._active_plugin_updater is not None:
            try:
                self._active_plugin_updater()
            except Exception:
                pass
        self._remember_plotter_usage(name)
        self._update_window_title()
        self._update_action_states()

    def _create_new_pyplot_window(self) -> None:
        factories = dict(self._plugin_factories)
        initial = self._current_plotter_name
        window = PyPlotWorkbench(plotters=factories, initial_plotter=initial)
        window.show()
        self._spawned_windows.append(window)
        window.destroyed.connect(lambda *_w, ref=window: self._spawned_windows.remove(ref) if ref in self._spawned_windows else None)

    def _apply_path_text(self, text: str) -> None:
        paths = [Path(entry) for entry in self._iter_path_entries(text)]
        self._selected_path_entries = paths
        self._remember_directory_from_paths(paths)

    def _iter_path_entries(self, text: str) -> Iterable[str]:
        candidate = text.replace("\r\n", "\n").replace(";", "\n")
        for entry in candidate.split("\n"):
            cleaned = entry.strip().strip('"')
            if cleaned:
                yield cleaned

    def _format_paths(self, paths: Iterable[Path]) -> str:
        return "; ".join(str(path) for path in paths)

    def _selected_paths(self) -> List[Path]:
        return list(self._selected_path_entries)

    def _remember_directory_from_paths(self, paths: Iterable[Path]) -> None:
        last = None
        for path in paths:
            if path.is_dir():
                last = path
            elif path.exists():
                last = path.parent
        if last is not None:
            self._last_directory = last



    def _update_project_title(self) -> None:
        self._update_window_title()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self._current_plugin is not None:
            try:
                self._current_plugin.deactivate()
            except Exception:
                pass
        payload = {key: str(value) for key, value in self._plugin_last_directories.items()}
        self.settings.setValue("plugin_last_dirs", json.dumps(payload))
        self.settings.setValue("sources", self.path_edit.text())
        self.settings.sync()
        super().closeEvent(event)

def main(
    available_plotters: Dict[str, Callable[[], QtWidgets.QWidget | None]] | None = None,
    initial_plotter: str | None = None,
) -> QtWidgets.QWidget | None:
    """Entry-point used by the launcher."""

    plugin_factories: Dict[str, Callable[["PyPlotWorkbench"], PyPlotPlugin]] = {}
    plugin_classes: Dict[str, type[PyPlotPlugin]] = {
        "VSM Hysteresis Loops": VSMHysteresisPlugin,
        "Temperature Dependence": TemperatureDependencePlugin,
        "Temperature Sensitivity": TemperatureSensitivityPlugin,
        "Current Annealing": CurrentAnnealingPlugin,
        "Stress Dependence": StressDependencePlugin,
        "Stress Sensitivity": StressSensitivityPlugin,
        "Hsw Load Compare": HswLoadComparePlugin,
        "Maxion Continuous": MaxionContinuousPlugin,
        "PDF Plotter": PdfPlotterPlugin,
        "Hysteresis Loops": HysteresisLoopsPlugin,
        "Hsw Distribution": HswDistributionPlugin,
        "Strain 3D Plot": Strain3DPlotPlugin,
    }
    for name, launcher in sorted((available_plotters or {}).items()):
        plugin_cls = plugin_classes.get(name)
        if plugin_cls is not None:
            plugin_factories[name] = lambda host, cls=plugin_cls, n=name: cls(host, n)
        else:
            plugin_factories[name] = lambda host, l=launcher, n=name: ExternalPlotterPlugin(host, n, l)

    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    ensure_app_theme(app)
    window = PyPlotWorkbench(plotters=plugin_factories, initial_plotter=initial_plotter)
    window.show()
    if created_app:
        app.exec()
        return None
    return window

if __name__ == "__main__":
    main()
