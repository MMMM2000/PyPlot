from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from . import core as stress_core


@register_plugin("Stress Dependence")
class StressDependencePlugin(PyPlotPlugin):
    """Port the stress dependence workflow into the shared PyPlot frame."""

    requires_imported_data = True

    _VAR_LABELS = {
        "sum": "T1+T2",
        "dT": "T2–T1",
        "T1": "T1",
        "T2": "T2",
    }

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data: pd.DataFrame | None = None
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._panel_widget: QtWidgets.QWidget | None = None
        self._var_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._baseline_first: QtWidgets.QRadioButton | None = None
        self._baseline_min: QtWidgets.QRadioButton | None = None
        self._processed_checkbox: QtWidgets.QCheckBox | None = None
        self._med_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        self._last_export_dir: Path | None = None
    # Lifecycle -----------------------------------------------------
    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    # UI helpers ----------------------------------------------------
    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel_widget is not None:
            return self._panel_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        window_module = window_api()
        overview_section, overview_layout = window_module.create_toolbar_section("Overview", parent=container)
        summary = QtWidgets.QLabel(
            "Select stress dependence files, load them, then generate plots."
        )
        summary.setWordWrap(True)
        overview_layout.addWidget(summary)
        overview_layout.addStretch(1)
        layout.addWidget(overview_section)
        layout.addStretch(1)
        self._summary_label = summary
        self._panel_widget = container
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        window_module = window_api()
        var_section, var_layout = window_module.create_toolbar_section("Variables to plot", parent=container)
        for key, label in self._VAR_LABELS.items():
            checkbox = QtWidgets.QCheckBox(label, var_section)
            checkbox.setChecked(key in getattr(stress_core, "PLOT_VARS", []))
            self._var_checks[key] = checkbox
            var_layout.addWidget(checkbox)
        var_layout.addStretch(1)
        layout.addWidget(var_section)

        baseline_section, baseline_layout = window_module.create_toolbar_section(
            "Baseline",
            parent=container,
        )
        first_rb = QtWidgets.QRadioButton("First", baseline_section)
        min_rb = QtWidgets.QRadioButton("Min", baseline_section)
        mode = getattr(stress_core, "BASELINE_MODE", "first")
        if mode == "min":
            min_rb.setChecked(True)
        else:
            first_rb.setChecked(True)
        baseline_layout.addWidget(first_rb)
        baseline_layout.addWidget(min_rb)
        baseline_layout.addStretch(1)
        self._baseline_first = first_rb
        self._baseline_min = min_rb
        layout.addWidget(baseline_section)

        def _form_layout(parent: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
            form = QtWidgets.QFormLayout(parent)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)
            form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            return form

        processed_section, processed_layout = window_module.create_toolbar_section(
            "Processed curve",
            parent=container,
            layout_factory=_form_layout,
        )
        proc_cb = QtWidgets.QCheckBox("Plot processed", processed_section)
        proc_cb.setChecked(bool(getattr(stress_core, "PLOT_PROCESSED", False)))
        self._processed_checkbox = proc_cb
        processed_layout.addRow(proc_cb)

        med_spin = QtWidgets.QSpinBox(processed_section)
        med_spin.setRange(1, 9999)
        med_spin.setValue(int(getattr(stress_core, "MED_WINDOW", 5)))
        self._med_spin = med_spin
        processed_layout.addRow("Med window:", med_spin)

        ma_spin = QtWidgets.QSpinBox(processed_section)
        ma_spin.setRange(1, 9999)
        ma_spin.setValue(int(getattr(stress_core, "MA_WINDOW", 20)))
        self._ma_spin = ma_spin
        processed_layout.addRow("MA window:", ma_spin)
        layout.addWidget(processed_section)

        layout.addStretch(1)
        self._settings_widget = container
        return container

    # Behaviour -----------------------------------------------------
    def _selected_variables(self) -> list[str]:
        selected = [key for key, cb in self._var_checks.items() if cb.isChecked()]
        return selected or ["sum"]

    def _apply_settings_to_core(self) -> dict[str, Any]:
        variables = self._selected_variables()
        stress_core.PLOT_VARS = list(variables)
        stress_core.PLOT_SUM = "sum" in variables
        stress_core.PLOT_DT = "dT" in variables
        stress_core.PLOT_T1 = "T1" in variables
        stress_core.PLOT_T2 = "T2" in variables
        baseline = "first"
        if isinstance(self._baseline_min, QtWidgets.QRadioButton) and self._baseline_min.isChecked():
            baseline = "min"
        stress_core.BASELINE_MODE = baseline

        if isinstance(self._processed_checkbox, QtWidgets.QCheckBox):
            stress_core.PLOT_PROCESSED = self._processed_checkbox.isChecked()
        if isinstance(self._med_spin, QtWidgets.QSpinBox):
            stress_core.MED_WINDOW = int(self._med_spin.value())
        if isinstance(self._ma_spin, QtWidgets.QSpinBox):
            stress_core.MA_WINDOW = int(self._ma_spin.value())

        stress_core.BACKEND = "matplotlib"
        stress_core.SHOW_PLOTS = False
        stress_core.SAVE_PLOTS = False

        return {
            "variables": variables,
            "save": False,
            "output_dir": "",
        }

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        string_paths = [str(path) for path in paths]
        try:
            self._data = stress_core.load_data(string_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to load stress dependence data:\n{exc}",
            )
            self._data = None
            return
        self._loaded_files = string_paths
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        if self._summary_label is not None and not self._summary_label.text().strip():
            self._summary_label.setText(
                "Select stress dependence files, load them, then generate plots."
            )
        self._log(f"Loaded {len(paths)} stress dependence file(s).")
        self.update_ui()

    def _clear_existing_tabs(self) -> None:
        if not self._plot_tabs:
            return
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(self._plot_tabs)
        else:
            for tab in self._plot_tabs:
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        self._plot_tabs.clear()

    def generate(self) -> None:  # type: ignore[override]
        if self._data is None:
            self.load_data()
        if self._data is None:
            return
        window_module = window_api()
        config = self._apply_settings_to_core()
        dataframe = stress_core.maybe_handle_outliers(self._data.copy())
        grouped = list(
            dataframe.groupby(["composition", "title", "sample_end", "anneal"], sort=False)
        )
        if not grouped:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "No valid stress dependence groups were found in the selected files.",
            )
            return
        self._clear_existing_tabs()
        total_steps = len(grouped) * len(config["variables"])
        progress_dialog: QtWidgets.QProgressDialog | None = None
        if total_steps > 1:
            progress_dialog = QtWidgets.QProgressDialog(
                "Generating stress dependence plots…",
                "Cancel",
                0,
                total_steps,
                self.host,
            )
            progress_dialog.setWindowTitle("Processing")
            progress_dialog.setAutoClose(True)
            progress_dialog.setAutoReset(True)
            progress_dialog.show()

        cancelled = False
        plots_created = 0
        for (composition, title, sample_end, anneal), group in grouped:
            for variable in config["variables"]:
                if progress_dialog is not None:
                    QtWidgets.QApplication.processEvents()
                    if progress_dialog.wasCanceled():
                        cancelled = True
                        break
                try:
                    fig, saved_name = stress_core.plot_variable(
                        group.copy(), variable, config["save"], config["output_dir"]
                    )
                except Exception as exc:
                    self._log(
                        f"Failed to plot {variable} for {composition} {title}: {exc}",
                        level="error",
                    )
                    continue
                canvas = FigureCanvas(fig)
                canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
                tab = QtWidgets.QWidget()
                tab_layout = QtWidgets.QVBoxLayout(tab)
                tab_layout.setContentsMargins(0, 0, 0, 0)
                tab_layout.addWidget(canvas)
                ax = fig.axes[0] if fig.axes else None
                tab_label = stress_core.LABELS.get(variable, variable)
                descriptor = window_module.TabDescriptor(
                    kind="stress_dependence",
                    title=fig.axes[0].get_title() if fig.axes else tab_label,
                    root_label=f"{composition} {title} {anneal}",
                    x_label="Applied load (g)",
                    y_label=stress_core.LABELS.get(variable, variable),
                    canvas=canvas,
                    axes=ax,
                    lines={},
                    metadata={
                        "composition": composition,
                        "title": title,
                        "sample_end": stress_core.format_sample_end(sample_end),
                        "anneal": anneal,
                        "variable": variable,
                        "saved_path": saved_name if config["save"] else "",
                        "source_files": list(self._loaded_files),
                    },
                )
                self.host.tab_widget.addTab(tab, tab_label)
                self.host._register_plot_tab(tab, canvas, ax, descriptor)
                self._plot_tabs.append(tab)
                plots_created += 1
                if progress_dialog is not None:
                    progress_dialog.setValue(progress_dialog.value() + 1)
            if cancelled:
                break

        if progress_dialog is not None:
            progress_dialog.close()

        if not self._plot_tabs:
            QtWidgets.QMessageBox.warning(
                self.host,
                self.name,
                "No plots were generated. Check your settings and input files.",
            )
            return

        self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        if self._summary_label is not None:
            self._summary_label.setText(
                f"Generated {plots_created} plot(s) across {len(grouped)} group(s)."
            )
        if cancelled:
            self._log("Plot generation cancelled by user.", level="error")
        else:
            self._log(f"Generated {plots_created} stress dependence plot(s).")

        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load stress dependence data before exporting to Origin.",
            )
            return
        self._apply_settings_to_core()
        try:
            stress_core.SHOW_PLOTS = False
            stress_core.main(self._loaded_files, backend="origin")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to export stress dependence plots to Origin:\n{exc}",
            )
            self._log(f"Origin export failed: {exc}", level="error")
        else:
            self._log("Sent stress dependence plots to Origin.")

    def export_txt(self) -> None:  # type: ignore[override]
        if self._data is None:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load stress dependence data before exporting TXT files.",
            )
            return
        self._apply_settings_to_core()
        start_dir = (
            str(self._last_export_dir)
            if self._last_export_dir is not None
            else ""
            or ""
        )
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self.host,
            "Select TXT export folder",
            start_dir or str(Path.home()),
        )
        if not directory:
            return
        target = Path(directory)
        exported = 0
        try:
            dataframe = stress_core.maybe_handle_outliers(self._data.copy())
            for _, grp in dataframe.groupby(
                ["composition", "title", "sample_end", "anneal"], dropna=False
            ):
                stress_core.export_group_to_txt(grp, target)
                exported += 1
        except Exception as exc:  # pragma: no cover - GUI path
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to export TXT data:\n{exc}",
            )
            self._log(f"TXT export failed: {exc}", level="error")
            return
        if not exported:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "No stress dependence groups were available to export.",
            )
            return
        self._last_export_dir = target
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            f"Exported {exported} stress dependence table(s) to {target}",
        )
        self._log(f"Exported {exported} stress dependence table(s) to {target}.")

    def update_ui(self) -> None:
        has_data = self._data is not None
        has_files = bool(self._loaded_files)
        has_plots = bool(self._plot_tabs)
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(has_data or has_files)
            self.host.plot_button.setText("Generate Stress Dependence")
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(has_plots)
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(has_data)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_files)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(has_plots)
        self.host._update_project_actions()
