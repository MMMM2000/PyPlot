from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd
from PyQt6 import QtCore, QtWidgets

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from .core import PlotSeries, VSMEntry, VSMTemperatureScanProcessor, VSM_TEMP_SCAN_COLORS


@register_plugin("VSM Temperature Scan")
class VSMTemperatureScanPlugin(PyPlotPlugin):
    """PyPlot integration for VSM temperature scans (Signal X vs Temperature)."""

    requires_imported_data = True
    auto_load_on_import = True
    uses_shared_plot_workbooks = False

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        if VSMTemperatureScanProcessor is None:  # pragma: no cover - defensive import guard
            raise RuntimeError("VSMTemperatureScanProcessor is not available.")
        self._processor = VSMTemperatureScanProcessor()
        attach = getattr(self._processor, "attach_logger", None)
        if callable(attach):
            attach(lambda message: self._log(message))
        self._processor.set_origin_style("main", line_width=1.4, show_markers=False, marker_size=0.0)
        self._processor.set_origin_style("smoothed", line_width=1.4, show_markers=False, marker_size=0.0)
        self._processor.set_origin_style("derivative", line_width=1.2, show_markers=False, marker_size=0.0)
        self._processor.set_origin_style("derivative_smoothed", line_width=1.2, show_markers=False, marker_size=0.0)
        self._dataset: list[VSMEntry] | None = None
        self._loaded_paths: list[Path] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._derivative_cb: QtWidgets.QCheckBox | None = None
        self._smoothed_derivative_cb: QtWidgets.QCheckBox | None = None
        self._smooth_cb: QtWidgets.QCheckBox | None = None
        self._split_cb: QtWidgets.QCheckBox | None = None
        self._combine_fields_cb: QtWidgets.QCheckBox | None = None
        self._median_spin: QtWidgets.QSpinBox | None = None
        self._ma_spin: QtWidgets.QSpinBox | None = None
        self._deriv_median_spin: QtWidgets.QSpinBox | None = None
        self._deriv_ma_spin: QtWidgets.QSpinBox | None = None
        self._overlay_cb: QtWidgets.QCheckBox | None = None
        stored_export = getattr(host, "_plugin_last_export_dirs", {}).get(name)
        self._last_export_dir: Path | None = stored_export if isinstance(stored_export, Path) else None
        self._managed_workbooks: set[str] = set()
        self._plot_tabs: list[QtWidgets.QWidget] = []

    # ------------------------------------------------------------------ lifecycle
    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(True)
        self._set_tab_bar_visible(False)
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)
        self._set_tab_bar_visible(True)

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

        combine_cb = QtWidgets.QCheckBox("Combine low/high field runs (dual-axis)", options_section)
        combine_cb.setChecked(bool(getattr(self._processor, "combine_fields", False)))
        combine_cb.toggled.connect(lambda checked: self._on_combine_fields_changed(bool(checked)))
        options_layout.addWidget(combine_cb)
        self._combine_fields_cb = combine_cb

        derivative_cb = QtWidgets.QCheckBox("Plot raw derivatives", options_section)
        derivative_cb.setChecked(bool(self._processor.show_derivative))
        derivative_cb.toggled.connect(lambda checked: self._on_derivative_changed(bool(checked)))
        options_layout.addWidget(derivative_cb)
        self._derivative_cb = derivative_cb

        sm_deriv_cb = QtWidgets.QCheckBox("Plot smoothed derivatives", options_section)
        sm_deriv_cb.setChecked(bool(getattr(self._processor, "show_smoothed_derivative", False)))
        sm_deriv_cb.toggled.connect(lambda checked: self._on_smoothed_derivative_changed(bool(checked)))
        options_layout.addWidget(sm_deriv_cb)
        self._smoothed_derivative_cb = sm_deriv_cb

        smooth_cb = QtWidgets.QCheckBox("Show smoothed plots", options_section)
        smooth_cb.setChecked(bool(self._processor.show_smoothed_plot))
        smooth_cb.toggled.connect(lambda checked: self._on_smoothed_changed(bool(checked)))
        options_layout.addWidget(smooth_cb)
        self._smooth_cb = smooth_cb
        # smoothing of derivatives is implied when plotting smoothed derivatives
        overlay_cb = QtWidgets.QCheckBox("Plot raw/smoothed + smoothed d/dT overlay", options_section)
        overlay_cb.setChecked(bool(getattr(self._processor, "show_overlay_derivative", False)))
        overlay_cb.toggled.connect(lambda checked: self._on_overlay_changed(bool(checked)))
        options_layout.addWidget(overlay_cb)
        self._overlay_cb = overlay_cb
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
            "Signal smoothing (before derivative)", parent=container, layout_factory=_form_layout
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

        layout.addWidget(smoothing_section)

        deriv_section, deriv_layout = window_module.create_toolbar_section(
            "Derivative smoothing (applied after d/dT)", parent=container, layout_factory=_form_layout
        )
        deriv_median_spin = QtWidgets.QSpinBox(deriv_section)
        deriv_median_spin.setRange(1, 9999)
        deriv_median_spin.setValue(int(getattr(self._processor, "derivative_median_window", 5)))
        self._deriv_median_spin = deriv_median_spin
        deriv_layout.addRow("Median window:", deriv_median_spin)

        deriv_ma_spin = QtWidgets.QSpinBox(deriv_section)
        deriv_ma_spin.setRange(1, 9999)
        deriv_ma_spin.setValue(int(getattr(self._processor, "derivative_moving_avg_window", 20)))
        self._deriv_ma_spin = deriv_ma_spin
        deriv_layout.addRow("Moving average window:", deriv_ma_spin)

        apply_btn = QtWidgets.QPushButton("Apply smoothing", deriv_section)
        apply_btn.clicked.connect(self._apply_smoothing_settings)
        deriv_layout.addRow(apply_btn)

        layout.addWidget(deriv_section)
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
            color_map = self._processor.series_color_map(series)
            include_raw_derivative = bool(self._processor.show_derivative)
            field_order = self._processor.field_axis_order([s.field for s in series])
            fields = [s.field for s in series]
            field_label = self._processor._format_field_label(fields)
            x_label = "Temperature (°C)"
            y_label = "Signal X (emu)"
            use_magnetization = len({round(val, 6) for val in fields}) > 1

            def _plot_main(smoothed: bool = False) -> None:
                fig = Figure(figsize=(8.5, 5))
                ax_left = fig.add_subplot(111)
                base_title = "Smoothed Signal X" if smoothed else "VSM Temperature Scan"
                plot_title = self._processor._plot_title(entry.sample, base_title, fields)
                ax_left.set_title(plot_title)
                axes_map: dict[float, Any] = {}
                ax_right = None
                for idx, entry_series in enumerate(series):
                    frame = entry_series.frame if not smoothed else self._processor._smooth_frame(entry_series.frame)
                    temps = frame["temperature"]
                    signal = frame["signal"]
                    color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                    color = color_map.get(color_key, VSM_TEMP_SCAN_COLORS[idx % len(VSM_TEMP_SCAN_COLORS)])
                    label = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)}"
                    primary = field_order[0] if field_order else entry_series.field
                    secondary = field_order[1] if len(field_order) > 1 else None
                    if entry_series.field not in axes_map:
                        if entry_series.field == primary or not axes_map:
                            axes_map[entry_series.field] = ax_left
                        else:
                            if ax_right is None:
                                ax_right = ax_left.twinx()
                            axes_map[entry_series.field] = ax_right
                            if ax_right is None:
                                ax_right = axes_map[entry_series.field]
                    if secondary is not None and entry_series.field == secondary and ax_right is None:
                        ax_right = ax_left.twinx()
                        axes_map[entry_series.field] = ax_right
                    axis = axes_map[entry_series.field]
                    axis.plot(temps, signal, color=color, linewidth=1.4, label=label)
                    ax_left.set_xlabel(x_label)
                left_fields = [field for field, axis in axes_map.items() if axis is ax_left]
                base_label = "Magnetization" if use_magnetization else None
                if left_fields:
                    y_label_left = self._processor._axis_label_for_fields(left_fields, base=base_label)
                else:
                    y_label_left = y_label
                ax_left.set_ylabel(y_label_left)
                if ax_right is not None:
                    right_fields = [field for field, axis in axes_map.items() if axis is ax_right]
                    if right_fields:
                        y_label_right = self._processor._axis_label_for_fields(
                            right_fields,
                            base=base_label,
                        )
                    else:
                        y_label_right = y_label
                    ax_right.set_ylabel(y_label_right)
                legend_handles: list[Any] = []
                legend_labels: list[str] = []
                for legend_axis in (ax_left, ax_right):
                    if legend_axis is None:
                        continue
                    handles, labels = legend_axis.get_legend_handles_labels()
                    for handle, label in zip(handles, labels):
                        if not label:
                            continue
                        legend_handles.append(handle)
                        legend_labels.append(label)
                if legend_handles:
                    ax_left.legend(legend_handles, legend_labels, loc="best")
                tab = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(tab)
                layout.setContentsMargins(0, 0, 0, 0)
                canvas = FigureCanvas(fig)
                canvas.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Expanding,
                    QtWidgets.QSizePolicy.Policy.Expanding,
                )
                layout.addWidget(canvas)
                descriptor = window_module.TabDescriptor(
                    kind="vsm_temperature_scan",
                    title=plot_title,
                    root_label=f"{entry.sample} - {'Smoothed' if smoothed else 'TScan'}{f' ({field_label})' if field_label else ''}",
                    x_label=x_label,
                    y_label=y_label_left,
                    canvas=canvas,
                    axes=ax_left,
                    lines={},
                    metadata={"sample": entry.sample, "smoothed": smoothed, "fields": fields},
                )
                index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Plot")
                setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
                if callable(setter):
                    setter(index)
                self.host._register_plot_tab(tab, canvas, ax_left, descriptor)
                self._plot_tabs.append(tab)
                self._set_tab_bar_visible(False)

            _plot_main(smoothed=False)
            if self._processor.show_smoothed_plot:
                _plot_main(smoothed=True)

            if include_raw_derivative:
                fig = Figure(figsize=(8.5, 5))
                ax = fig.add_subplot(111)
                deriv_title = self._processor._plot_title(entry.sample, "d(Signal X)/dT", fields)
                for idx, entry_series in enumerate(series):
                    frame = self._processor._smooth_frame(entry_series.frame)
                    temps = frame["temperature"]
                    derivs = self._processor._compute_derivative(frame, smooth=False)
                    color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                    color = color_map.get(color_key, VSM_TEMP_SCAN_COLORS[idx % len(VSM_TEMP_SCAN_COLORS)])
                    label = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)}"
                    ax.plot(temps, derivs, linestyle="--", linewidth=1.2, color=color, label=label)
                ax.set_xlabel(x_label)
                ax.set_ylabel("d(Signal X)/dT (emu/°C)")
                ax.legend(loc="best")
                tab = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(tab)
                layout.setContentsMargins(0, 0, 0, 0)
                canvas = FigureCanvas(fig)
                canvas.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Expanding,
                    QtWidgets.QSizePolicy.Policy.Expanding,
                )
                layout.addWidget(canvas)
                descriptor = window_module.TabDescriptor(
                    kind="vsm_temperature_scan_derivative",
                    title=deriv_title,
                    root_label=f"{entry.sample} - d(Signal X)/dT{f' ({field_label})' if field_label else ''}",
                    x_label=x_label,
                    y_label="d(Signal X)/dT (emu/°C)",
                    canvas=canvas,
                    axes=ax,
                    lines={},
                    metadata={"sample": entry.sample, "derivative": True, "fields": fields},
                )
                index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Derivative")
                setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
                if callable(setter):
                    setter(index)
                self.host._register_plot_tab(tab, canvas, ax, descriptor)
                self._plot_tabs.append(tab)
                self._set_tab_bar_visible(False)
            if getattr(self._processor, "show_smoothed_derivative", False) and getattr(self._processor, "smooth_derivative", False):
                fig = Figure(figsize=(8.5, 5))
                ax = fig.add_subplot(111)
                deriv_sm_title = self._processor._plot_title(entry.sample, "Smoothed d(Signal X)/dT", fields)
                for idx, entry_series in enumerate(series):
                    frame = self._processor._smooth_frame(entry_series.frame)
                    temps = frame["temperature"]
                    derivs = self._processor._compute_derivative(frame, smooth=True)
                    color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                    color = color_map.get(color_key, VSM_TEMP_SCAN_COLORS[idx % len(VSM_TEMP_SCAN_COLORS)])
                    label = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)}"
                    ax.plot(temps, derivs, linestyle="-", linewidth=1.2, color=color, label=label)
                ax.set_xlabel(x_label)
                ax.set_ylabel("d(Signal X)/dT (smoothed) (emu/°C)")
                ax.legend(loc="best")
                tab = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(tab)
                layout.setContentsMargins(0, 0, 0, 0)
                canvas = FigureCanvas(fig)
                canvas.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Expanding,
                    QtWidgets.QSizePolicy.Policy.Expanding,
                )
                layout.addWidget(canvas)
                descriptor = window_module.TabDescriptor(
                    kind="vsm_temperature_scan_derivative_smoothed",
                    title=deriv_sm_title,
                    root_label=f"{entry.sample} - d(Signal X)/dT (smoothed){f' ({field_label})' if field_label else ''}",
                    x_label=x_label,
                    y_label="d(Signal X)/dT (emu/°C)",
                    canvas=canvas,
                    axes=ax,
                    lines={},
                    metadata={"sample": entry.sample, "derivative": True, "smoothed": True, "fields": fields},
                )
                index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Derivative")
                setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
                if callable(setter):
                    setter(index)
                self.host._register_plot_tab(tab, canvas, ax, descriptor)
                self._plot_tabs.append(tab)
                self._set_tab_bar_visible(False)
            if getattr(self._processor, "show_overlay_derivative", False):
                for entry_series in series:
                    frame_raw, _ = self._processor._dedupe_temperatures(entry_series.frame)
                    frame_raw = frame_raw.sort_values("temperature")
                    frame_smoothed = self._processor._smooth_frame(frame_raw)
                    temps = frame_raw["temperature"]
                    smoothed_signal = frame_smoothed["signal"]
                    deriv_smoothed = self._processor._compute_derivative(frame_smoothed, smooth=True)
                    fig = Figure(figsize=(8.5, 5))
                    ax_left = fig.add_subplot(111)
                    color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                    color = color_map.get(color_key, VSM_TEMP_SCAN_COLORS[entry_series.segment_index % len(VSM_TEMP_SCAN_COLORS)])
                    raw_label = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)} (raw)"
                    sm_label = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)} (smoothed)"
                    d_label = f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)} d/dT (smoothed)"
                    h_raw = ax_left.plot(temps, frame_raw["signal"], color=color, alpha=0.6, linewidth=1.1, label=raw_label)[0]
                    h_sm = ax_left.plot(frame_smoothed["temperature"], smoothed_signal, color=color, linestyle="--", linewidth=1.4, label=sm_label)[0]
                    ax_left.set_xlabel(x_label)
                    ax_left.set_ylabel("Signal X (emu)")
                    ax_right = ax_left.twinx()
                    h_d = ax_right.plot(frame_smoothed["temperature"], deriv_smoothed, color=color, linewidth=1.2, label=d_label)[0]
                    ax_right.set_ylabel("d(Signal X)/dT (emu/°C)")
                    legend_handles = [h_raw, h_sm, h_d]
                    legend_labels = [raw_label, sm_label, d_label]
                    ax_left.legend(legend_handles, legend_labels, loc="best")
                    ax_left.set_title(f"{entry.sample} - Overlay {entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)}")
                    tab = QtWidgets.QWidget()
                    layout = QtWidgets.QVBoxLayout(tab)
                    layout.setContentsMargins(0, 0, 0, 0)
                    canvas = FigureCanvas(fig)
                    canvas.setSizePolicy(
                        QtWidgets.QSizePolicy.Policy.Expanding,
                        QtWidgets.QSizePolicy.Policy.Expanding,
                    )
                    layout.addWidget(canvas)
                    descriptor = window_module.TabDescriptor(
                        kind="vsm_temperature_scan_overlay",
                        title=f"{entry.sample} - Overlay {entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)}",
                        root_label=f"{entry.sample} - Overlay {entry_series.field:.0f} Oe",
                        x_label=x_label,
                        y_label="Signal X (emu)",
                        canvas=canvas,
                        axes=ax_left,
                        lines={},
                        metadata={"sample": entry.sample, "overlay": True, "field": entry_series.field, "direction": entry_series.direction, "segment": entry_series.segment_index},
                    )
                    index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Overlay")
                    setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
                    if callable(setter):
                        setter(index)
                    self.host._register_plot_tab(tab, canvas, ax_left, descriptor)
                    self._plot_tabs.append(tab)
                    self._set_tab_bar_visible(False)

    def export_txt(self) -> None:  # type: ignore[override]
        if self._dataset is None:
            self.load_data()
        if self._dataset is None:
            return
        self._apply_smoothing_settings()
        start_dir = self.host._preferred_export_directory(self.name, self._last_export_dir)
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self.host,
            "Select TXT export folder",
            str(start_dir),
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
        self.host._remember_plugin_export_dir(self.name, target)
        self._log(f"Exported TXT files to {target}")

    def open_origin(self) -> None:  # type: ignore[override]
        if self._dataset is None:
            self.load_data()
        self._apply_smoothing_settings()
        dataset = self._dataset

        def _task() -> None:
            if dataset is None:
                return
            self._processor.plot_origin(dataset)

        self.run_origin_export(
            ready=dataset is not None,
            missing_message="Load VSM temperature scan data before exporting to Origin.",
            task=_task,
            success_log="Sent VSM temperature scan data to Origin.",
            failure_message="Failed to send data to Origin",
        )

    def _clear_tabs(self) -> None:
        self.clear_plot_tabs(self._plot_tabs)

    def _set_tab_bar_visible(self, visible: bool) -> None:
        bar_getter = getattr(self.host.tab_widget, "tabBar", None)
        if callable(bar_getter):
            try:
                bar = bar_getter()
            except Exception:
                return
            try:
                bar.setVisible(visible)
                bar.setMaximumHeight(0 if not visible else 16777215)
                auto_hide = getattr(self.host.tab_widget, "setTabBarAutoHide", None)
                if callable(auto_hide):
                    try:
                        auto_hide(not visible)
                    except Exception:
                        pass
            except Exception:
                pass

    # ------------------------------------------------------------------ UI state
    def update_ui(self) -> None:
        has_data = self._dataset is not None
        self.apply_shared_action_state(
            can_plot=has_data or self._host_has_data_selection(),
            can_save_graph=False,
            can_normalize=False,
            can_export_txt=has_data,
            can_open_origin=has_data,
            can_export_workbooks=has_data,
        )
        if self._summary_label is not None:
            if not has_data:
                self._summary_label.setText("Import VSM temperature scan files, then plot or export.")
            else:
                self._summary_label.clear()

    def graph_option_defaults(self) -> dict[str, float] | None:  # type: ignore[override]
        return {
            "title_font": 14,
            "label_font": 11,
            "tick_font": 9,
            "figure_width": 9.5,
            "figure_height": 5.8,
        }

    # ------------------------------------------------------------------ project persistence
    def serialize_project_state(self, *, base_path: Path | None) -> Dict[str, Any] | None:  # type: ignore[override]
        _ = base_path
        median_window = (
            int(self._median_spin.value())
            if isinstance(self._median_spin, QtWidgets.QSpinBox)
            else int(getattr(self._processor, "median_window", 5))
        )
        moving_avg_window = (
            int(self._ma_spin.value())
            if isinstance(self._ma_spin, QtWidgets.QSpinBox)
            else int(getattr(self._processor, "moving_avg_window", 15))
        )
        derivative_median_window = (
            int(self._deriv_median_spin.value())
            if isinstance(self._deriv_median_spin, QtWidgets.QSpinBox)
            else int(getattr(self._processor, "derivative_median_window", 5))
        )
        derivative_moving_avg_window = (
            int(self._deriv_ma_spin.value())
            if isinstance(self._deriv_ma_spin, QtWidgets.QSpinBox)
            else int(getattr(self._processor, "derivative_moving_avg_window", 20))
        )
        return {
            "split_directions": bool(getattr(self._processor, "split_directions", True)),
            "combine_fields": bool(getattr(self._processor, "combine_fields", False)),
            "show_derivative": bool(getattr(self._processor, "show_derivative", False)),
            "show_smoothed_derivative": bool(
                getattr(self._processor, "show_smoothed_derivative", False)
            ),
            "show_smoothed_plot": bool(getattr(self._processor, "show_smoothed_plot", False)),
            "show_overlay_derivative": bool(
                getattr(self._processor, "show_overlay_derivative", False)
            ),
            "median_window": median_window,
            "moving_avg_window": moving_avg_window,
            "derivative_median_window": derivative_median_window,
            "derivative_moving_avg_window": derivative_moving_avg_window,
        }

    def restore_project_state(  # type: ignore[override]
        self, state: Dict[str, Any], *, project_dir: Path
    ) -> None:
        _ = project_dir
        if not isinstance(state, dict):
            return

        split_directions = self._state_bool(
            state.get("split_directions"),
            default=bool(getattr(self._processor, "split_directions", True)),
        )
        combine_fields = self._state_bool(
            state.get("combine_fields"),
            default=bool(getattr(self._processor, "combine_fields", False)),
        )
        show_derivative = self._state_bool(
            state.get("show_derivative"),
            default=bool(getattr(self._processor, "show_derivative", False)),
        )
        show_smoothed_derivative = self._state_bool(
            state.get("show_smoothed_derivative"),
            default=bool(getattr(self._processor, "show_smoothed_derivative", False)),
        )
        show_smoothed_plot = self._state_bool(
            state.get("show_smoothed_plot"),
            default=bool(getattr(self._processor, "show_smoothed_plot", False)),
        )
        show_overlay_derivative = self._state_bool(
            state.get("show_overlay_derivative"),
            default=bool(getattr(self._processor, "show_overlay_derivative", False)),
        )
        median_window = self._state_int(
            state.get("median_window"),
            default=int(getattr(self._processor, "median_window", 5)),
        )
        moving_avg_window = self._state_int(
            state.get("moving_avg_window"),
            default=int(getattr(self._processor, "moving_avg_window", 15)),
        )
        derivative_median_window = self._state_int(
            state.get("derivative_median_window"),
            default=int(getattr(self._processor, "derivative_median_window", 5)),
        )
        derivative_moving_avg_window = self._state_int(
            state.get("derivative_moving_avg_window"),
            default=int(getattr(self._processor, "derivative_moving_avg_window", 20)),
        )

        self._processor.set_split_directions(split_directions)
        setter = getattr(self._processor, "set_combine_fields", None)
        if callable(setter):
            setter(combine_fields)
        else:
            setattr(self._processor, "combine_fields", bool(combine_fields))
        self._processor.set_show_derivative(show_derivative)
        setter = getattr(self._processor, "set_show_smoothed_derivative", None)
        if callable(setter):
            setter(show_smoothed_derivative)
        else:
            setattr(self._processor, "show_smoothed_derivative", bool(show_smoothed_derivative))
        self._processor.set_show_smoothed(show_smoothed_plot)
        setter = getattr(self._processor, "set_show_overlay_derivative", None)
        if callable(setter):
            setter(show_overlay_derivative)
        else:
            setattr(self._processor, "show_overlay_derivative", bool(show_overlay_derivative))
        self._processor.set_smoothing_windows(median_window, moving_avg_window)
        setter = getattr(self._processor, "set_derivative_smoothing_windows", None)
        if callable(setter):
            setter(derivative_median_window, derivative_moving_avg_window)
        else:
            setattr(
                self._processor,
                "derivative_median_window",
                int(derivative_median_window),
            )
            setattr(
                self._processor,
                "derivative_moving_avg_window",
                int(derivative_moving_avg_window),
            )

        self._sync_settings_controls()
        if self._dataset is not None:
            self._register_workbooks()
        self.update_ui()

    # ------------------------------------------------------------------ internal helpers
    @staticmethod
    def _state_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
        return bool(default)

    @staticmethod
    def _state_int(value: Any, *, default: int, minimum: int = 1, maximum: int = 9999) -> int:
        parsed: int | None = None
        if isinstance(value, bool):
            parsed = None
        elif isinstance(value, int):
            parsed = value
        elif isinstance(value, float):
            if np.isfinite(value):
                parsed = int(value)
        elif isinstance(value, str):
            try:
                numeric = float(value.strip())
            except ValueError:
                numeric = float("nan")
            if np.isfinite(numeric):
                parsed = int(numeric)
        if parsed is None:
            parsed = int(default)
        parsed = max(int(minimum), parsed)
        parsed = min(int(maximum), parsed)
        return parsed

    def _sync_settings_controls(self) -> None:
        toggles: list[tuple[QtWidgets.QCheckBox | None, bool]] = [
            (self._split_cb, bool(getattr(self._processor, "split_directions", True))),
            (self._combine_fields_cb, bool(getattr(self._processor, "combine_fields", False))),
            (self._derivative_cb, bool(getattr(self._processor, "show_derivative", False))),
            (
                self._smoothed_derivative_cb,
                bool(getattr(self._processor, "show_smoothed_derivative", False)),
            ),
            (self._smooth_cb, bool(getattr(self._processor, "show_smoothed_plot", False))),
            (
                self._overlay_cb,
                bool(getattr(self._processor, "show_overlay_derivative", False)),
            ),
        ]
        for widget, value in toggles:
            if not isinstance(widget, QtWidgets.QCheckBox):
                continue
            widget.blockSignals(True)
            widget.setChecked(bool(value))
            widget.blockSignals(False)
        spins: list[tuple[QtWidgets.QSpinBox | None, int]] = [
            (self._median_spin, int(getattr(self._processor, "median_window", 5))),
            (self._ma_spin, int(getattr(self._processor, "moving_avg_window", 15))),
            (
                self._deriv_median_spin,
                int(getattr(self._processor, "derivative_median_window", 5)),
            ),
            (
                self._deriv_ma_spin,
                int(getattr(self._processor, "derivative_moving_avg_window", 20)),
            ),
        ]
        for widget, value in spins:
            if not isinstance(widget, QtWidgets.QSpinBox):
                continue
            widget.blockSignals(True)
            widget.setValue(int(value))
            widget.blockSignals(False)

    def _apply_smoothing_settings(self) -> None:
        median = self._processor.median_window
        ma = self._processor.moving_avg_window
        d_median = getattr(self._processor, "derivative_median_window", median)
        d_ma = getattr(self._processor, "derivative_moving_avg_window", ma)
        if isinstance(self._median_spin, QtWidgets.QSpinBox):
            median = int(self._median_spin.value())
        if isinstance(self._ma_spin, QtWidgets.QSpinBox):
            ma = int(self._ma_spin.value())
        if isinstance(self._deriv_median_spin, QtWidgets.QSpinBox):
            d_median = int(self._deriv_median_spin.value())
        if isinstance(self._deriv_ma_spin, QtWidgets.QSpinBox):
            d_ma = int(self._deriv_ma_spin.value())
        self._processor.set_smoothing_windows(median, ma)
        setter = getattr(self._processor, "set_derivative_smoothing_windows", None)
        if callable(setter):
            setter(d_median, d_ma)
        self._register_workbooks()

    def _on_derivative_changed(self, enabled: bool) -> None:
        self._processor.set_show_derivative(enabled)
        self._register_workbooks()

    def _on_smoothed_changed(self, enabled: bool) -> None:
        self._processor.set_show_smoothed(enabled)
        self._register_workbooks()

    def _on_smoothed_derivative_changed(self, enabled: bool) -> None:
        setter = getattr(self._processor, "set_show_smoothed_derivative", None)
        if callable(setter):
            setter(enabled)
        else:
            setattr(self._processor, "show_smoothed_derivative", bool(enabled))
        if enabled:
            if isinstance(self._deriv_median_spin, QtWidgets.QSpinBox):
                self._deriv_median_spin.setEnabled(True)
            if isinstance(self._deriv_ma_spin, QtWidgets.QSpinBox):
                self._deriv_ma_spin.setEnabled(True)
        self._register_workbooks()

    def _on_overlay_changed(self, enabled: bool) -> None:
        setter = getattr(self._processor, "set_show_overlay_derivative", None)
        if callable(setter):
            setter(enabled)
        else:
            setattr(self._processor, "show_overlay_derivative", bool(enabled))
        if enabled and isinstance(self._smoothed_derivative_cb, QtWidgets.QCheckBox) and not self._smoothed_derivative_cb.isChecked():
            self._smoothed_derivative_cb.setChecked(True)
        self._register_workbooks()

    def _on_split_changed(self, enabled: bool) -> None:
        self._processor.set_split_directions(enabled)
        self._register_workbooks()

    def _on_combine_fields_changed(self, enabled: bool) -> None:
        setter = getattr(self._processor, "set_combine_fields", None)
        if callable(setter):
            setter(enabled)
        else:
            setattr(self._processor, "combine_fields", bool(enabled))
        if not self._loaded_paths:
            return
        try:
            dataset = self._processor.load(list(self._loaded_paths))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to reload VSM scan data:\n{exc}",
            )
            return
        self._dataset = dataset
        self._data = dataset
        self._register_workbooks()
        self.update_ui()

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
            include_raw_derivative = bool(
                getattr(self._processor, "show_derivative", False)
            )
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

            if self._processor.show_derivative and include_raw_derivative:
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
            if getattr(self._processor, "show_smoothed_derivative", False) and getattr(self._processor, "smooth_derivative", False):
                deriv_sm_sheet = self._build_sheet(entry, series, mode="derivative_smoothed")
                if deriv_sm_sheet is not None:
                    frame, meta, roles = deriv_sm_sheet
                    wb_key = self._workbook_key(entry, "derivative_smoothed")
                    new_keys.add(wb_key)
                    workbook = window_module.WorkbookData(
                        key=wb_key,
                        name=f"{entry.sample} (TScan Derivatives Smoothed)",
                        worksheets=[],
                        source=None,
                        folder=None,
                    )
                    sheet_key = host._worksheet_key(wb_key, "dSignal/dT (smoothed)")
                    worksheet = window_module.WorksheetData(
                        key=sheet_key,
                        name="dSignal/dT (smoothed)",
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
            if getattr(self._processor, "show_overlay_derivative", False):
                overlay_frames: list[window_module.WorksheetData] = []
                wb_key = self._workbook_key(entry, "overlay")
                for idx, entry_series in enumerate(series, start=1):
                    frame_raw, _ = self._processor._dedupe_temperatures(entry_series.frame)
                    frame_raw = frame_raw.sort_values("temperature")
                    frame_sm = self._processor._smooth_frame(frame_raw)
                    deriv_sm = self._processor._compute_derivative(frame_sm, smooth=True)
                    max_len = max(len(frame_raw), len(frame_sm), len(deriv_sm))
                    temp_col = frame_raw["temperature"].astype(float).tolist() + [np.nan] * (max_len - len(frame_raw))
                    raw_col = frame_raw["signal"].astype(float).tolist() + [np.nan] * (max_len - len(frame_raw))
                    sm_col = frame_sm["signal"].astype(float).tolist() + [np.nan] * (max_len - len(frame_sm))
                    deriv_col = deriv_sm + [np.nan] * (max_len - len(deriv_sm))
                    cols = {
                        f"T{idx}": temp_col,
                        f"Raw{idx}": raw_col,
                        f"Smooth{idx}": sm_col,
                        f"dSmooth{idx}": deriv_col,
                    }
                    color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                    color = getattr(self._processor, "series_color_map", lambda s: {})(series).get(
                        color_key,
                        VSM_TEMP_SCAN_COLORS[(entry_series.segment_index + int(entry_series.field)) % len(VSM_TEMP_SCAN_COLORS)],
                    )
                    meta = {
                        f"T{idx}": window_module.WorksheetColumnMeta(
                            long_name="Temperature",
                            units="°C",
                            comments=f"Section {entry_series.segment_index + 1}",
                        ),
                        f"Raw{idx}": window_module.WorksheetColumnMeta(
                            long_name="Signal X",
                            units="emu",
                            comments=f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)} (raw) [{color}]",
                        ),
                        f"Smooth{idx}": window_module.WorksheetColumnMeta(
                            long_name="Signal X (smoothed)",
                            units="emu",
                            comments=f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)} (smoothed) [{color}]",
                        ),
                        f"dSmooth{idx}": window_module.WorksheetColumnMeta(
                            long_name="dSignal/dT (smoothed)",
                            units="emu/°C",
                            comments=f"{entry_series.field:.0f} Oe{self._processor._direction_label(entry_series.direction, entry_series.segment_index)} d/dT (smoothed) [{color}]",
                        ),
                    }
                    frame_df = pd.DataFrame(cols)
                    sheet_key = host._worksheet_key(wb_key, f"Overlay {idx}")
                    overlay_frames.append(
                        window_module.WorksheetData(
                            key=sheet_key,
                            name=f"Overlay {idx}",
                            dataframe=frame_df,
                            columns=meta,
                            source=entry.path,
                            workbook_key=wb_key,
                            axis_roles="XYXY",
                        )
                    )
                if overlay_frames:
                    wb_key = self._workbook_key(entry, "overlay")
                    new_keys.add(wb_key)
                    workbook = window_module.WorkbookData(
                        key=wb_key,
                        name=f"{entry.sample} (TScan Overlay)",
                        worksheets=[ws.key for ws in overlay_frames],
                        source=None,
                        folder=None,
                    )
                    host._register_imported_workbook(workbook, overlay_frames)

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
            color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
            color = getattr(self._processor, "series_color_map", lambda s: {})(series).get(
                color_key,
                VSM_TEMP_SCAN_COLORS[(entry_series.segment_index + int(entry_series.field)) % len(VSM_TEMP_SCAN_COLORS)],
            )
            smooth_signal = mode in {"smoothed"}
            if smooth_signal or mode.startswith("derivative"):
                working = self._processor._smooth_frame(working)
                if smooth_signal:
                    long_name = "Signal X (smoothed)"
            values = working["signal"].astype(float).tolist()
            if mode.startswith("derivative"):
                smooth_deriv = mode == "derivative_smoothed"
                values = self._processor._compute_derivative(working, smooth=smooth_deriv)
                long_name = "dSignal/dT" if not smooth_deriv else "dSignal/dT (smoothed)"
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
                comments=f"{comment} [{color}]",
            )
            axis_roles += "XY"

        frame = pd.DataFrame(columns)
        return frame, meta, axis_roles
