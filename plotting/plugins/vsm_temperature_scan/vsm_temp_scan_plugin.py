from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd
from PyQt6 import QtCore, QtWidgets

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api

try:
    from experiments.simple_scripts.vsm_temperature_scan import (
        PlotSeries,
        VSMEntry,
        VSMTemperatureScanProcessor,
    )
except Exception:  # pragma: no cover - fallback when optional dependency missing
    PlotSeries = Any  # type: ignore
    VSMEntry = Any  # type: ignore
    VSMTemperatureScanProcessor = None  # type: ignore


@register_plugin("VSM Temperature Scan")
class VSMTemperatureScanPlugin(PyPlotPlugin):
    """PyPlot integration for VSM temperature scans (Signal X vs Temperature)."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        if VSMTemperatureScanProcessor is None:  # pragma: no cover - defensive import guard
            raise RuntimeError("VSMTemperatureScanProcessor is not available.")
        self._processor = VSMTemperatureScanProcessor()
        attach = getattr(self._processor, "attach_logger", None)
        if callable(attach):
            attach(lambda message: self._log(message))
        self._dataset: list[VSMEntry] | None = None
        self._loaded_paths: list[Path] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._derivative_cb: QtWidgets.QCheckBox | None = None
        self._smooth_cb: QtWidgets.QCheckBox | None = None
        self._split_cb: QtWidgets.QCheckBox | None = None
        self._median_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        self._last_export_dir: Path | None = None
        self._managed_workbooks: set[str] = set()
        self._plot_tabs: list[QtWidgets.QWidget] = []

    # ------------------------------------------------------------------ lifecycle
    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    # ------------------------------------------------------------------ UI helpers
    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel_widget is not None:
            return self._panel_widget

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        summary = QtWidgets.QLabel(
            "Load VSM temperature scan files, then plot, export TXT, or send workbooks to Origin."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
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

        options_section, options_layout = window_module.create_toolbar_section(
            "Plot options", parent=container
        )
        split_cb = QtWidgets.QCheckBox("Separate heating/cooling", options_section)
        split_cb.setChecked(bool(getattr(self._processor, "split_directions", True)))
        split_cb.toggled.connect(lambda checked: self._on_split_changed(bool(checked)))
        options_layout.addWidget(split_cb)
        self._split_cb = split_cb

        derivative_cb = QtWidgets.QCheckBox("Plot derivatives", options_section)
        derivative_cb.setChecked(bool(self._processor.show_derivative))
        derivative_cb.toggled.connect(lambda checked: self._on_derivative_changed(bool(checked)))
        options_layout.addWidget(derivative_cb)
        self._derivative_cb = derivative_cb

        smooth_cb = QtWidgets.QCheckBox("Show smoothed plots", options_section)
        smooth_cb.setChecked(bool(self._processor.show_smoothed_plot))
        smooth_cb.toggled.connect(lambda checked: self._on_smoothed_changed(bool(checked)))
        options_layout.addWidget(smooth_cb)
        self._smooth_cb = smooth_cb
        options_layout.addStretch(1)
        layout.addWidget(options_section)

        def _form_layout(parent: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
            form = QtWidgets.QFormLayout(parent)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)
            form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            return form

        smoothing_section, smoothing_layout = window_module.create_toolbar_section(
            "Smoothing (applied before derivative)", parent=container, layout_factory=_form_layout
        )
        median_spin = QtWidgets.QSpinBox(smoothing_section)
        median_spin.setRange(1, 9999)
        median_spin.setValue(int(self._processor.median_window))
        self._median_spin = median_spin
        smoothing_layout.addRow("Median window:", median_spin)

        ma_spin = QtWidgets.QSpinBox(smoothing_section)
        ma_spin.setRange(1, 9999)
        ma_spin.setValue(int(self._processor.moving_avg_window))
        self._ma_spin = ma_spin
        smoothing_layout.addRow("Moving average window:", ma_spin)

        apply_btn = QtWidgets.QPushButton("Apply smoothing", smoothing_section)
        apply_btn.clicked.connect(self._apply_smoothing_settings)
        smoothing_layout.addRow(apply_btn)

        layout.addWidget(smoothing_section)
        layout.addStretch(1)

        self._settings_widget = container
        return container

    # ------------------------------------------------------------------ host actions
    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        try:
            dataset = self._processor.load(list(paths))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to load VSM scan data:\n{exc}")
            self._dataset = None
            return
        self._dataset = dataset
        self._data = dataset  # satisfy host readiness checks
        self._loaded_paths = list(paths)
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        self._register_workbooks()
        self._log(f"Loaded {len(paths)} VSM temperature file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._dataset is None:
            self.load_data()
        if self._dataset is None:
            return
        self._apply_smoothing_settings()
        self._clear_tabs()
        window_module = window_api()
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

        for entry in self._dataset:
            series = self._processor._build_series(entry.dataframe.copy())
            if not series:
                continue
            colors = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0ea5e9"]
            x_label = "Temperature (°C)"
            y_label = "Signal X (emu)"

            def _plot_main(smoothed: bool = False) -> None:
                fig = Figure(figsize=(8.5, 5))
                ax_left = fig.add_subplot(111)
                axes_map: dict[float, Any] = {}
                ax_right = None
                for idx, entry_series in enumerate(series):
                    frame = entry_series.frame if not smoothed else self._processor._smooth_frame(entry_series.frame)
                    temps = frame["temperature"]
                    signal = frame["signal"]
                    color = colors[idx % len(colors)]
                    label = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)}"
                    if entry_series.field not in axes_map:
                        axes_map[entry_series.field] = ax_left if not axes_map else ax_left.twinx()
                        if axes_map[entry_series.field] is not ax_left:
                            ax_right = axes_map[entry_series.field]
                    axis = axes_map[entry_series.field]
                    axis.plot(temps, signal, color=color, linewidth=1.4, label=label)
                ax_left.set_xlabel(x_label)
                ax_left.set_ylabel(y_label)
                if ax_right is not None:
                    ax_right.set_ylabel(f"{y_label} (secondary)")
                ax_left.legend(loc="best")
                tab = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(tab)
                layout.setContentsMargins(0, 0, 0, 0)
                canvas = FigureCanvas(fig)
                layout.addWidget(canvas)
                descriptor = window_module.TabDescriptor(
                    kind="vsm_temperature_scan",
                    title=f"{entry.sample} - {'Smoothed' if smoothed else 'VSM Temperature Scan'}",
                    root_label="Smoothed" if smoothed else "TScan",
                    x_label=x_label,
                    y_label=y_label,
                    canvas=canvas,
                    axes=ax_left,
                    lines={},
                    metadata={"sample": entry.sample, "smoothed": smoothed, "fields": [s.field for s in series]},
                )
                index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Plot")
                setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
                if callable(setter):
                    setter(index)
                self.host._register_plot_tab(tab, canvas, ax_left, descriptor)
                self._plot_tabs.append(tab)

            _plot_main(smoothed=False)
            if self._processor.show_smoothed_plot:
                _plot_main(smoothed=True)

            if self._processor.show_derivative:
                fig = Figure(figsize=(8.5, 5))
                ax = fig.add_subplot(111)
                for idx, entry_series in enumerate(series):
                    frame = self._processor._smooth_frame(entry_series.frame)
                    temps = frame["temperature"]
                    derivs = self._processor._compute_derivative(frame)
                    color = colors[idx % len(colors)]
                    label = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)}"
                    ax.plot(temps, derivs, linestyle="--", linewidth=1.2, color=color, label=label)
                ax.set_xlabel(x_label)
                ax.set_ylabel("d(Signal X)/dT (emu/°C)")
                ax.legend(loc="best")
                tab = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(tab)
                layout.setContentsMargins(0, 0, 0, 0)
                canvas = FigureCanvas(fig)
                layout.addWidget(canvas)
                descriptor = window_module.TabDescriptor(
                    kind="vsm_temperature_scan_derivative",
                    title=f"{entry.sample} - d(Signal X)/dT",
                    root_label="d(Signal X)/dT",
                    x_label=x_label,
                    y_label="d(Signal X)/dT (emu/°C)",
                    canvas=canvas,
                    axes=ax,
                    lines={},
                    metadata={"sample": entry.sample, "derivative": True, "fields": [s.field for s in series]},
                )
                index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Derivative")
                setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
                if callable(setter):
                    setter(index)
                self.host._register_plot_tab(tab, canvas, ax, descriptor)
                self._plot_tabs.append(tab)

    def export_txt(self) -> None:  # type: ignore[override]
        if self._dataset is None:
            self.load_data()
        if self._dataset is None:
            return
        self._apply_smoothing_settings()
        start_dir = str(self._last_export_dir) if self._last_export_dir else str(Path.home())
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self.host,
            "Select TXT export folder",
            start_dir,
        )
        if not directory:
            return
        target = Path(directory)
        try:
            self._processor.export_txt(self._dataset, target)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"TXT export failed:\n{exc}")
            self._log(f"TXT export failed: {exc}", level="error")
            return
        self._last_export_dir = target
        self._log(f"Exported TXT files to {target}")

    def open_origin(self) -> None:  # type: ignore[override]
        if self._dataset is None:
            self.load_data()
        if self._dataset is None:
            return
        self._apply_smoothing_settings()
        try:
            self._processor.plot_origin(self._dataset)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to send data to Origin:\n{exc}")
            self._log(f"Origin export failed: {exc}", level="error")
            return
        self._log("Sent VSM temperature scan data to Origin.")

    def _clear_tabs(self) -> None:
        if not self._plot_tabs:
            return
        host = self.host
        for tab in list(self._plot_tabs):
            remover = getattr(host, "_remove_tab_internal", None)
            if callable(remover):
                try:
                    remover(tab)
                    continue
                except Exception:
                    pass
            index = host.tab_widget.indexOf(tab)
            if index >= 0:
                host.tab_widget.removeTab(index)
        self._plot_tabs.clear()
        host._rebuild_object_manager_for_tab(host.tab_widget.currentWidget())

    # ------------------------------------------------------------------ UI state
    def update_ui(self) -> None:
        has_data = self._dataset is not None
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(has_data or self._host_has_data_selection())
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(has_data)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_data)
        if hasattr(self.host, "export_origin_button"):
            self.host.export_origin_button.setEnabled(has_data)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(False)
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if self._summary_label is not None:
            if not has_data:
                self._summary_label.setText("Import VSM temperature scan files, then plot or export.")
            else:
                self._summary_label.clear()
        self.host._update_project_actions()

    # ------------------------------------------------------------------ internal helpers
    def _apply_smoothing_settings(self) -> None:
        median = self._processor.median_window
        ma = self._processor.moving_avg_window
        if isinstance(self._median_spin, QtWidgets.QSpinBox):
            median = int(self._median_spin.value())
        if isinstance(self._ma_spin, QtWidgets.QSpinBox):
            ma = int(self._ma_spin.value())
        self._processor.set_smoothing_windows(median, ma)
        self._register_workbooks()

    def _on_derivative_changed(self, enabled: bool) -> None:
        self._processor.set_show_derivative(enabled)
        self._register_workbooks()

    def _on_smoothed_changed(self, enabled: bool) -> None:
        self._processor.set_show_smoothed(enabled)
        self._register_workbooks()

    def _on_split_changed(self, enabled: bool) -> None:
        self._processor.set_split_directions(enabled)
        self._register_workbooks()

    def _workbook_key(self, entry: VSMEntry, kind: str) -> str:
        try:
            resolved = entry.path.resolve()
        except Exception:
            resolved = entry.path
        return f"vsm_temp_scan::{resolved}::{kind}"

    def _remove_workbook(self, key: str) -> None:
        host = self.host
        workbook = host._workbooks.pop(key, None)
        if workbook is not None:
            remover = getattr(host, "_remove_worksheet", None)
            for sheet_key in list(getattr(workbook, "worksheets", [])):
                if callable(remover):
                    try:
                        remover(sheet_key)
                    except Exception:
                        continue
        tree_item = getattr(host, "_data_workbook_items", {}).pop(key, None)
        if tree_item is not None:
            parent = tree_item.parent()
            if parent is not None:
                index = parent.indexOfChild(tree_item)
                if index >= 0:
                    parent.takeChild(index)
        cleanup = getattr(host, "_remove_workbook_root_if_empty", None)
        if callable(cleanup):
            cleanup()

    def _register_workbooks(self) -> None:
        if self._dataset is None:
            return
        host = self.host
        window_module = window_api()
        new_keys: set[str] = set()

        for entry in self._dataset:
            series = self._processor._build_series(entry.dataframe.copy())
            if not series:
                continue
            main_sheet = self._build_sheet(entry, series, mode="main")
            if main_sheet is not None:
                frame, meta, roles = main_sheet
                wb_key = self._workbook_key(entry, "main")
                new_keys.add(wb_key)
                workbook = window_module.WorkbookData(
                    key=wb_key,
                    name=f"{entry.sample} (TScan)",
                    worksheets=[],
                    source=None,
                    folder=None,
                )
                sheet_key = host._worksheet_key(wb_key, "Data")
                worksheet = window_module.WorksheetData(
                    key=sheet_key,
                    name="Data",
                    dataframe=frame,
                    columns=meta,
                    source=entry.path,
                    workbook_key=wb_key,
                    axis_roles=roles,
                )
                workbook.worksheets.append(sheet_key)
                host._register_imported_workbook(workbook, [worksheet])

            if self._processor.show_derivative:
                deriv_sheet = self._build_sheet(entry, series, mode="derivative")
                if deriv_sheet is not None:
                    frame, meta, roles = deriv_sheet
                    wb_key = self._workbook_key(entry, "derivative")
                    new_keys.add(wb_key)
                    workbook = window_module.WorkbookData(
                        key=wb_key,
                        name=f"{entry.sample} (TScan Derivatives)",
                        worksheets=[],
                        source=None,
                        folder=None,
                    )
                    sheet_key = host._worksheet_key(wb_key, "dSignal/dT")
                    worksheet = window_module.WorksheetData(
                        key=sheet_key,
                        name="dSignal/dT",
                        dataframe=frame,
                        columns=meta,
                        source=entry.path,
                        workbook_key=wb_key,
                        axis_roles=roles,
                    )
                    workbook.worksheets.append(sheet_key)
                    host._register_imported_workbook(workbook, [worksheet])

            if self._processor.show_smoothed_plot:
                sm_sheet = self._build_sheet(entry, series, mode="smoothed")
                if sm_sheet is not None:
                    frame, meta, roles = sm_sheet
                    wb_key = self._workbook_key(entry, "smoothed")
                    new_keys.add(wb_key)
                    workbook = window_module.WorkbookData(
                        key=wb_key,
                        name=f"{entry.sample} (TScan Smoothed)",
                        worksheets=[],
                        source=None,
                        folder=None,
                    )
                    sheet_key = host._worksheet_key(wb_key, "Smoothed")
                    worksheet = window_module.WorksheetData(
                        key=sheet_key,
                        name="Smoothed",
                        dataframe=frame,
                        columns=meta,
                        source=entry.path,
                        workbook_key=wb_key,
                        axis_roles=roles,
                    )
                    workbook.worksheets.append(sheet_key)
                    host._register_imported_workbook(workbook, [worksheet])

        obsolete = self._managed_workbooks - new_keys
        for key in obsolete:
            self._remove_workbook(key)
        self._managed_workbooks = new_keys
        self.host._update_project_actions()

    def _build_sheet(
        self,
        entry: VSMEntry,
        series: Iterable[PlotSeries],
        *,
        mode: str,
    ) -> tuple[pd.DataFrame, Dict[str, Any], str] | None:
        columns: Dict[str, List[float]] = {}
        meta: Dict[str, Any] = {}
        axis_roles = ""
        prepared: List[Tuple[List[float], List[float], str, str, str, str]] = []
        window_module = window_api()

        for entry_series in series:
            frame, _ = self._processor._dedupe_temperatures(entry_series.frame)
            working = frame.sort_values("temperature")
            units = "emu"
            long_name = "Signal X"
            if mode in {"smoothed", "derivative"}:
                working = self._processor._smooth_frame(working)
                long_name = "Signal X (smoothed)"
            values = working["signal"].astype(float).tolist()
            if mode == "derivative":
                values = self._processor._compute_derivative(working)
                long_name = "dSignal/dT"
                units = "emu/°C"
            temps = working["temperature"].astype(float).tolist()
            section_comment = f"Section {entry_series.segment_index + 1}"
            comment = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)} ({section_comment})"
            prepared.append((temps, values, long_name, units, comment, section_comment))

        if not prepared:
            return None

        max_len = max(len(item[0]) for item in prepared)
        max_len = max(max_len, max(len(item[1]) for item in prepared))
        for idx, (temps, values, long_name, units, comment, section_comment) in enumerate(prepared, start=1):
            tcol = f"T{idx}"
            vcol = f"S{idx}"
            columns[tcol] = temps + [np.nan] * (max_len - len(temps))
            columns[vcol] = values + [np.nan] * (max_len - len(values))
            meta[tcol] = window_module.WorksheetColumnMeta(
                long_name="Temperature",
                units="°C",
                comments=section_comment,
            )
            meta[vcol] = window_module.WorksheetColumnMeta(
                long_name=long_name,
                units=units,
                comments=comment,
            )
            axis_roles += "XY"

        frame = pd.DataFrame(columns)
        return frame, meta, axis_roles
