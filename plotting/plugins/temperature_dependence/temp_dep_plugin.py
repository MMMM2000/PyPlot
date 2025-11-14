from __future__ import annotations

from pathlib import Path
from typing import Iterable, TYPE_CHECKING

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins.base import PyPlotPlugin, register_plugin
from . import core as temp_core
from plotting.plugins._window import window_api

if TYPE_CHECKING:
    from plotting.pyplot.window import (
        WorksheetColumnMeta,
        WorkbookData,
        TabDescriptor,
    )


@register_plugin("Temperature Dependence")
class TemperatureDependencePlugin(PyPlotPlugin):
    """Embed the temperature dependence workflow directly inside PyPlot."""

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
        self._mode_combo: QtWidgets.QComboBox | None = None
        self._med_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        self._last_export_dir: Path | None = None

    # ------------------------------------------------------------------ lifecycle
    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    # ------------------------------------------------------------------ UI
    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel_widget is not None:
            return self._panel_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        window_module = window_api()
        overview_section, overview_layout = window_module.create_toolbar_section("Overview", parent=container)
        summary = QtWidgets.QLabel("Select temperature data files, then click Plot Temperature Dependence.")
        summary.setWordWrap(True)
        summary.setObjectName("mw_temp_dep_overview_text")
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
            checkbox.setChecked(key in temp_core.PLOT_VARS)
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

        processing_section, processing_layout = window_module.create_toolbar_section(
            "Processing",
            parent=container,
            layout_factory=_form_layout,
        )
        mode_combo = QtWidgets.QComboBox(processing_section)
        mode_combo.addItems(["Raw", "Processed", "Both"])
        mode_combo.setCurrentIndex({"raw": 0, "processed": 1, "both": 2}.get(temp_core.PLOT_MODE, 0))
        self._mode_combo = mode_combo
        processing_layout.addRow("Mode:", mode_combo)

        med_spin = QtWidgets.QSpinBox(processing_section)
        med_spin.setRange(1, 9999)
        med_spin.setValue(int(temp_core.MED_WINDOW))
        self._med_spin = med_spin
        processing_layout.addRow("Median window:", med_spin)

        ma_spin = QtWidgets.QSpinBox(processing_section)
        ma_spin.setRange(1, 9999)
        ma_spin.setValue(int(temp_core.MA_WINDOW))
        self._ma_spin = ma_spin
        processing_layout.addRow("Moving average window:", ma_spin)
        layout.addWidget(processing_section)

        layout.addStretch(1)
        self._settings_widget = container
        return container

    # ------------------------------------------------------------------ helpers
    def _selected_variables(self) -> list[str]:
        selected = [key for key, cb in self._var_checks.items() if cb.isChecked() and cb.isEnabled()]
        return selected or ["sum"]

    def _apply_settings_to_core(self) -> list[str]:
        vars_selected = self._selected_variables()
        temp_core.PLOT_VARS = list(vars_selected)
        if self._mode_combo is not None:
            temp_core.PLOT_MODE = {0: "raw", 1: "processed", 2: "both"}.get(self._mode_combo.currentIndex(), "raw")
        if self._med_spin is not None:
            temp_core.MED_WINDOW = int(self._med_spin.value())
        if self._ma_spin is not None:
            temp_core.MA_WINDOW = int(self._ma_spin.value())
        temp_core.SAVE_PLOTS = False
        temp_core.SHOW_PLOTS = False
        temp_core.BACKEND = "matplotlib"
        return vars_selected

    # ------------------------------------------------------------------ host actions
    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
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
        if self._summary_label is not None and not self._summary_label.text().strip():
            self._summary_label.setText(
                "Select one or more temperature dependence files, then click Plot Temperature Dependence."
            )
        self._log(f"Loaded {len(paths)} temperature dependence file(s).")
        self._register_workbooks(paths)
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._data is None:
            self.load_data()
        if self._data is None:
            return
        window_module = window_api()
        variables = self._apply_settings_to_core()
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
        for variable in variables:
            try:
                fig, _ = temp_core.plot_variable(dataframe, variable, False, "")
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
            descriptor = window_module.TabDescriptor(
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
                    "saved_path": "",
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
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load temperature dependence data before exporting to Origin.",
            )
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
        ready_to_plot = has_data or self._host_has_data_selection()
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(ready_to_plot)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(bool(self._plot_tabs))
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(has_data)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_data)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(bool(self._plot_tabs))
        if self._summary_label is not None:
            if not ready_to_plot:
                self._summary_label.setText("Import temperature dependence files, then click Plot Temperature Dependence.")
            elif not has_data:
                self._summary_label.setText(
                    "Click Plot Temperature Dependence to load data and generate graphs/workbooks with the current settings."
                )
            elif self._plot_tabs:
                self._summary_label.clear()
            else:
                self._summary_label.setText(
                    "Data loaded. Adjust settings and click Plot Temperature Dependence to generate graphs."
                )
        self.host._update_project_actions()

    def export_txt(self) -> None:  # type: ignore[override]
        if self._data is None:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load temperature dependence data before exporting TXT files.",
            )
            return
        self._apply_settings_to_core()
        start_dir = (
            str(self._last_export_dir)
            if self._last_export_dir is not None
            else ""
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
            dataframe = temp_core.maybe_handle_outliers(self._data.copy())
            for (_, _), group in dataframe.groupby(
                ["composition", "anneal"], dropna=False
            ):
                temp_core.export_group_to_txt(
                    group,
                    target,
                    include_processed=temp_core.PLOT_MODE in {"processed", "both"},
                    med_window=temp_core.MED_WINDOW,
                    ma_window=temp_core.MA_WINDOW,
                )
                exported += 1
            self._last_export_dir = target
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
                "No temperature sensitivity groups were available to export.",
            )
            return
        self._last_export_dir = target
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            f"Exported {exported} temperature sensitivity table(s) to {target}",
        )
        self._log(f"Exported {exported} temperature sensitivity table(s) to {target}.")

    # ------------------------------------------------------------------ workbook helpers
    def _register_workbooks(self, paths: Iterable[Path]) -> None:
        data = self._data
        if data is None or "filename" not in data.columns:
            return
        host = self.host
        window_module = window_api()
        grouped = data.groupby("filename", dropna=False)
        active_keys: set[str] = set()
        created: list[str] = []
        meta_map = {
            "temp": ("Temperature", "°C"),
            "T1": ("T1", "µs"),
            "T2": ("T2", "µs"),
            "dT": ("T2–T1", "µs"),
            "sum": ("T1+T2", "µs"),
        }
        columns_order = [
            "line",
            "temp",
            "continuous",
            "T1",
            "T2",
            "dT",
            "sum",
            "composition",
            "sample",
            "anneal",
        ]
        for path in paths:
            file_name = path.name
            if file_name not in grouped.groups:
                continue
            subset = grouped.get_group(file_name).copy().reset_index(drop=True)
            available = [column for column in columns_order if column in subset.columns]
            extras = [column for column in subset.columns if column not in available and column != "filename"]
            frame = subset[available + extras] if available or extras else subset
            key = self._workbook_keys.get(str(path))
            if not key:
                try:
                    resolved = path.resolve()
                except Exception:
                    resolved = path
                key = f"temperature_dependence::{resolved}"
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
                    meta.units = units
            workbook.worksheets = [worksheet.key]
            host._register_imported_workbook(workbook, [worksheet])
            active_keys.add(workbook.key)
            created.append(path.name)
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
