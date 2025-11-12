from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from . import core as temp_sens_core

def _format_units(units: str | None) -> str | None:
    if not units:
        return None
    value = str(units).strip()
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        return value
    return f"[{value}]"

if TYPE_CHECKING:
    from plotting.pyplot.window import (
        GraphLineState,
        WorksheetColumnMeta,
        WorkbookData,
        TabDescriptor,
    )


@register_plugin("Temperature Sensitivity")
class TemperatureSensitivityPlugin(PyPlotPlugin):
    """Embed the temperature sensitivity workflow directly inside PyPlot."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data: pd.DataFrame | None = None
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._panel_widget: QtWidgets.QWidget | None = None
        self._var_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._baseline_combo: QtWidgets.QComboBox | None = None
        self._include_continuous_checkbox: QtWidgets.QCheckBox | None = None
        self._med_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        self._workbook_keys: dict[str, str] = {}
        self._managed_workbooks: set[str] = set()

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel_widget is not None:
            return self._panel_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        window_module = window_api()
        overview_section, overview_layout = window_module.create_toolbar_section("Overview", parent=container)
        summary = QtWidgets.QLabel("Select temperature sensitivity files then click Generate workbooks.")
        summary.setWordWrap(True)
        overview_layout.addWidget(summary)
        overview_layout.addStretch(1)
        layout.addWidget(overview_section)
        self._summary_label = summary

        layout.addStretch(1)
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
        for key, label in temp_sens_core.TS_LABELS.items():
            checkbox = QtWidgets.QCheckBox(label, var_section)
            checkbox.setChecked(key in temp_sens_core.PLOT_VARS)
            self._var_checks[key] = checkbox
            var_layout.addWidget(checkbox)
        var_layout.addStretch(1)
        layout.addWidget(var_section)

        def _form_layout(parent: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
            form = QtWidgets.QFormLayout(parent)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)
            form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            return form

        baseline_section, baseline_layout = window_module.create_toolbar_section(
            "Baseline options",
            parent=container,
            layout_factory=_form_layout,
        )
        baseline_combo = QtWidgets.QComboBox(baseline_section)
        baseline_combo.addItem("Do not shift baseline", "none")
        baseline_combo.addItem("Shift to zero at 25°C", "zero_25")
        baseline_combo.addItem("Plot both baselines", "both")
        baseline_value = (
            temp_sens_core.BASELINE_MODE
            if temp_sens_core.BASELINE_MODE in {"none", "zero_25", "both"}
            else "none"
        )
        baseline_combo.setCurrentIndex({"none": 0, "zero_25": 1, "both": 2}[baseline_value])
        self._baseline_combo = baseline_combo
        baseline_layout.addRow("Baseline mode:", baseline_combo)

        include_box = QtWidgets.QCheckBox("Include continuous sweeps", baseline_section)
        include_box.setChecked(bool(temp_sens_core.INCLUDE_CONTINUOUS))
        self._include_continuous_checkbox = include_box
        baseline_layout.addRow(include_box)
        layout.addWidget(baseline_section)

        smoothing_section, smoothing_layout = window_module.create_toolbar_section(
            "Smoothing",
            parent=container,
            layout_factory=_form_layout,
        )
        med_spin = QtWidgets.QSpinBox(smoothing_section)
        med_spin.setRange(1, 9999)
        med_spin.setValue(int(temp_sens_core.MED_WINDOW))
        self._med_spin = med_spin
        smoothing_layout.addRow("Median window:", med_spin)

        ma_spin = QtWidgets.QSpinBox(smoothing_section)
        ma_spin.setRange(1, 9999)
        ma_spin.setValue(int(temp_sens_core.MA_WINDOW))
        self._ma_spin = ma_spin
        smoothing_layout.addRow("Moving average window:", ma_spin)
        layout.addWidget(smoothing_section)

        layout.addStretch(1)
        self._settings_widget = container
        return container

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
        temp_sens_core.SAVE_PLOTS = False
        temp_sens_core.OUTPUT_DIR = str(temp_sens_core.OUTPUT_DIR)
        temp_sens_core.SHOW_PLOTS = False
        temp_sens_core.BACKEND = "matplotlib"
        return {
            "variables": vars_selected,
            "baseline_mode": baseline_value,
            "include_continuous": include_cont,
            "save": False,
            "output_dir": "",
            "med_window": temp_sens_core.MED_WINDOW,
            "ma_window": temp_sens_core.MA_WINDOW,
        }

    def load_data(self) -> None:  # type: ignore[override]
        host = self.host
        window_module = window_api()

        paths = host.ensure_data_selection(self, warn_on_missing=True)
        if not paths:
            return
        valid_paths: list[Path] = []
        skipped: list[str] = []
        for path in paths:
            if temp_sens_core.parse_metadata(path.stem) is None:
                skipped.append(path.name)
                continue
            valid_paths.append(path)
        if skipped:
            self._log(
                "Ignoring files that do not follow the temperature sensitivity naming pattern:\n"
                + ", ".join(skipped)
            )
        if not valid_paths:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "None of the selected files match the temperature sensitivity format.",
            )
            return
        if hasattr(host, "_commit_selected_paths"):
            try:
                host._commit_selected_paths(valid_paths)  # type: ignore[attr-defined]
            except Exception:
                pass
        string_paths = [str(path) for path in valid_paths]
        try:
            self._data = temp_sens_core.load_data(string_paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to load data:\n{exc}")
            self._data = None
            return
        self._loaded_files = string_paths
        if valid_paths:
            self.host._plugin_last_directories[self.name] = valid_paths[0].parent
        self._register_workbooks(valid_paths)
        if self._summary_label is not None:
            self._summary_label.setText(
                "Data loaded. Adjust settings and click Plot Temperature Sensitivity to generate graphs."
            )
        self._log(
            "Loaded {count} temperature sensitivity file(s): {names}".format(
                count=len(valid_paths),
                names=", ".join(path.name for path in valid_paths),
            )
        )
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._data is None:
            self.load_data()
        if self._data is None:
            return
        window_module = window_api()
        config = self._apply_settings_to_core()
        dataframe = temp_sens_core.maybe_handle_outliers(self._data.copy())
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
        mode_labels = {"none": "Raw", "zero_25": "Zero @25°C"}
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
                    lines: dict[tuple[str, float | str], GraphLineState] = {}
                    if ax is not None:
                        for index, line in enumerate(ax.get_lines(), start=1):
                            label = line.get_label() or f"Series {index}"
                            state = window_module.GraphLineState(
                                key=(label, float(index)),
                                label=label,
                                line=line,
                                base_x=line.get_xdata(),
                                base_y=line.get_ydata(),
                            )
                            lines[state.key] = state
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
                    descriptor = window_module.TabDescriptor(
                        kind="temperature_sensitivity",
                        title=title,
                        root_label=tab_label,
                        x_label=x_label,
                        y_label=y_label,
                        canvas=canvas,
                        axes=ax,
                        lines=lines,
                        metadata=metadata,
                    )
                    self.host.tab_widget.addTab(tab, tab_label)
                    self.host._register_plot_tab(tab, canvas, ax, descriptor)
                    self._plot_tabs.append(tab)
                    plots_created += 1
        if self._plot_tabs:
            first_tab = self._plot_tabs[0]
            index = self.host.tab_widget.indexOf(first_tab)
            if index >= 0:
                self.host.tab_widget.setCurrentIndex(index)
        self._log(f"Generated {plots_created} temperature sensitivity plot(s).")
        self.update_ui()

    def _register_workbooks(self, paths: list[Path]) -> None:
        data = self._data
        if data is None:
            return
        host = self.host
        window_module = window_api()
        active_keys: set[str] = set()
        created: list[str] = []
        if "filename" not in data.columns:
            return
        grouped = data.groupby("filename", dropna=False)
        meta_map = {
            "temp": ("Temperature", "°C"),
            "T1": ("T1", "µs"),
            "T2": ("T2", "µs"),
            "dT": ("T2-T1", "µs"),
            "sum": ("T1+T2", "µs"),
        }
        for path in paths:
            file_name = path.name
            if file_name not in grouped.groups:
                continue
            subset = grouped.get_group(file_name).copy()
            subset = subset.reset_index(drop=True)
            columns_order = [
                "line",
                "temp",
                "T1",
                "T2",
                "dT",
                "sum",
                "continuous",
                "composition",
                "sample",
                "anneal",
            ]
            available = [column for column in columns_order if column in subset.columns]
            extras = [column for column in subset.columns if column not in available and column != "filename"]
            frame = subset[available + extras] if available or extras else subset
            key = self._workbook_keys.get(str(path))
            if not key:
                try:
                    resolved = path.resolve()
                except Exception:
                    resolved = path
                key = f"temperature_sensitivity::{resolved}"
                self._workbook_keys[str(path)] = key
            workbook = window_module.WorkbookData(
                key=key,
                name=f"{path.stem} (temperature)",
                worksheets=[],
                source=path,
                folder=path.parent,
            )
            worksheet = host._create_worksheet_from_frame(workbook, "Temperature data", frame)
            for column, (long_name, units) in meta_map.items():
                meta = worksheet.columns.get(column)
                if isinstance(meta, window_module.WorksheetColumnMeta):
                    meta.long_name = long_name
                    meta.units = _format_units(units)
            workbook.worksheets = [worksheet.key]
            host._register_imported_workbook(workbook, [worksheet])
            active_keys.add(workbook.key)
            created.append(path.name)
            try:
                root = host._ensure_data_root()
                if root is not None:
                    root.setExpanded(True)
                node = host._data_workbook_items.get(workbook.key)
                if node is not None:
                    node.setExpanded(True)
            except Exception:
                pass
        stale = self._managed_workbooks - active_keys
        if stale:
            self._remove_managed_workbooks(stale)
        self._managed_workbooks = active_keys
        if created or stale:
            host._refresh_imported_data_summary()
            host._sync_selected_paths_with_imports()

    def _remove_managed_workbooks(self, keys: Iterable[str]) -> None:
        host = self.host
        for key in keys:
            workbook = host._workbooks.get(key)
            if workbook is not None:
                for sheet_key in list(workbook.worksheets):
                    host._remove_worksheet(sheet_key)
            host._workbooks.pop(key, None)
            item = host._data_workbook_items.pop(key, None)
            if item is not None:
                parent = item.parent()
                if parent is not None:
                    index = parent.indexOfChild(item)
                    if index >= 0:
                        parent.takeChild(index)
            self._managed_workbooks.discard(key)

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Generate workbooks before exporting to Origin.",
            )
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
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(has_data)
            self.host.plot_button.setText("Plot Temperature Sensitivity")
        if self._summary_label is not None:
            if self._plot_tabs:
                self._summary_label.clear()
                self._summary_label.setVisible(False)
            else:
                self._summary_label.setVisible(True)
                if not has_data:
                    self._summary_label.setText(
                        "Import temperature sensitivity files to load them automatically."
                    )
                elif not self._summary_label.text().strip():
                    self._summary_label.setText(
                        "Data loaded. Adjust settings and click Plot Temperature Sensitivity to generate graphs."
                    )
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
