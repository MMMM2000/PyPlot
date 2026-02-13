from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from matplotlib import ticker as mticker
from PyQt6 import QtCore, QtWidgets

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from plotting.shared.origin import origin_session
from .parser import parse_dma_txt


@dataclass
class DmaIsoStressEntry:
    path: Path
    sample: str
    datasets: Dict[int, Tuple[List[float], List[float]]]


@register_plugin("DMA Iso-Stress")
class DmaIsoStressPlugin(PyPlotPlugin):
    """PyPlot integration for DMA iso-stress TXT files."""

    requires_imported_data = True
    _DEFAULT_X_LABEL = "Temperature [°C]"
    _DEFAULT_Y_LABEL = "Strain [%]"

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._dataset: List[DmaIsoStressEntry] = []
        self._plot_tabs: List[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._markers_checkbox: QtWidgets.QCheckBox | None = None
        self._sort_checkbox: QtWidgets.QCheckBox | None = None
        self._grid_checkbox: QtWidgets.QCheckBox | None = None
        self._legend_checkbox: QtWidgets.QCheckBox | None = None
        self._legend_location_combo: QtWidgets.QComboBox | None = None
        self._line_width_spin: QtWidgets.QDoubleSpinBox | None = None
        self._font_size_spin: QtWidgets.QSpinBox | None = None
        self._title_edit: QtWidgets.QLineEdit | None = None
        self._xlabel_edit: QtWidgets.QLineEdit | None = None
        self._ylabel_edit: QtWidgets.QLineEdit | None = None
        self._show_title_checkbox: QtWidgets.QCheckBox | None = None
        self._show_xlabel_checkbox: QtWidgets.QCheckBox | None = None
        self._show_ylabel_checkbox: QtWidgets.QCheckBox | None = None
        self._auto_x_limits_checkbox: QtWidgets.QCheckBox | None = None
        self._auto_y_limits_checkbox: QtWidgets.QCheckBox | None = None
        self._x_min_spin: QtWidgets.QDoubleSpinBox | None = None
        self._x_max_spin: QtWidgets.QDoubleSpinBox | None = None
        self._y_min_spin: QtWidgets.QDoubleSpinBox | None = None
        self._y_max_spin: QtWidgets.QDoubleSpinBox | None = None
        self._x_tick_mode_combo: QtWidgets.QComboBox | None = None
        self._y_tick_mode_combo: QtWidgets.QComboBox | None = None
        self._x_tick_step_edit: QtWidgets.QLineEdit | None = None
        self._y_tick_step_edit: QtWidgets.QLineEdit | None = None
        self._x_tick_count_spin: QtWidgets.QSpinBox | None = None
        self._y_tick_count_spin: QtWidgets.QSpinBox | None = None
        self._loaded_paths: List[Path] = []

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel(
            "Import DMA iso-stress TXT files, then plot temperature vs strain for each stress level."
        )
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

        window_module = window_api()
        options_section, options_layout = window_module.create_toolbar_section(
            "Plot options",
            parent=container,
        )
        self._markers_checkbox = QtWidgets.QCheckBox("Show markers", options_section)
        self._markers_checkbox.setChecked(False)
        options_layout.addWidget(self._markers_checkbox)
        self._sort_checkbox = QtWidgets.QCheckBox(
            "Sort stress levels ascending", options_section
        )
        self._sort_checkbox.setChecked(True)
        options_layout.addWidget(self._sort_checkbox)
        layout.addWidget(options_section)

        formatting_section, formatting_layout = window_module.create_toolbar_section(
            "Graph formatting",
            parent=container,
        )
        formatting_form = QtWidgets.QFormLayout()
        formatting_form.setContentsMargins(0, 0, 0, 0)
        formatting_form.setSpacing(6)
        formatting_layout.addLayout(formatting_form)

        self._title_edit = QtWidgets.QLineEdit(formatting_section)
        self._title_edit.setPlaceholderText("{sample} - DMA Iso-Stress")
        self._show_title_checkbox = QtWidgets.QCheckBox("Show", formatting_section)
        self._show_title_checkbox.setChecked(True)
        formatting_form.addRow("Title", self._with_show_checkbox(self._title_edit, self._show_title_checkbox))

        self._xlabel_edit = QtWidgets.QLineEdit(formatting_section)
        self._xlabel_edit.setText(self._DEFAULT_X_LABEL)
        self._show_xlabel_checkbox = QtWidgets.QCheckBox("Show", formatting_section)
        self._show_xlabel_checkbox.setChecked(True)
        formatting_form.addRow("X label", self._with_show_checkbox(self._xlabel_edit, self._show_xlabel_checkbox))

        self._ylabel_edit = QtWidgets.QLineEdit(formatting_section)
        self._ylabel_edit.setText(self._DEFAULT_Y_LABEL)
        self._show_ylabel_checkbox = QtWidgets.QCheckBox("Show", formatting_section)
        self._show_ylabel_checkbox.setChecked(True)
        formatting_form.addRow("Y label", self._with_show_checkbox(self._ylabel_edit, self._show_ylabel_checkbox))

        self._line_width_spin = QtWidgets.QDoubleSpinBox(formatting_section)
        self._line_width_spin.setRange(0.2, 12.0)
        self._line_width_spin.setSingleStep(0.1)
        self._line_width_spin.setValue(1.4)
        formatting_form.addRow("Line width", self._line_width_spin)

        self._font_size_spin = QtWidgets.QSpinBox(formatting_section)
        self._font_size_spin.setRange(6, 36)
        self._font_size_spin.setValue(12)
        formatting_form.addRow("Font size", self._font_size_spin)

        self._grid_checkbox = QtWidgets.QCheckBox("Show grid", formatting_section)
        self._grid_checkbox.setChecked(False)
        formatting_form.addRow("", self._grid_checkbox)

        self._legend_checkbox = QtWidgets.QCheckBox("Show legend", formatting_section)
        self._legend_checkbox.setChecked(True)
        formatting_form.addRow("", self._legend_checkbox)

        self._legend_location_combo = QtWidgets.QComboBox(formatting_section)
        self._legend_location_combo.addItem("Best", "best")
        self._legend_location_combo.addItem("Upper right", "upper right")
        self._legend_location_combo.addItem("Upper left", "upper left")
        self._legend_location_combo.addItem("Lower left", "lower left")
        self._legend_location_combo.addItem("Lower right", "lower right")
        self._legend_location_combo.addItem("Right", "right")
        self._legend_location_combo.addItem("Center left", "center left")
        self._legend_location_combo.addItem("Center right", "center right")
        self._legend_location_combo.addItem("Lower center", "lower center")
        self._legend_location_combo.addItem("Upper center", "upper center")
        self._legend_location_combo.addItem("Center", "center")
        formatting_form.addRow("Legend location", self._legend_location_combo)

        legend_labels_row = QtWidgets.QHBoxLayout()
        edit_legend_labels_button = QtWidgets.QPushButton("Edit legend entries…", formatting_section)
        edit_legend_labels_button.clicked.connect(self._edit_current_legend_entries)
        legend_labels_row.addWidget(edit_legend_labels_button)
        reset_legend_labels_button = QtWidgets.QPushButton("Reset legend entries", formatting_section)
        reset_legend_labels_button.clicked.connect(self._reset_current_legend_entries)
        legend_labels_row.addWidget(reset_legend_labels_button)
        formatting_form.addRow("Legend labels", legend_labels_row)

        self._x_tick_mode_combo = QtWidgets.QComboBox(formatting_section)
        self._x_tick_mode_combo.addItem("Auto", "auto")
        self._x_tick_mode_combo.addItem("By increment", "step")
        self._x_tick_mode_combo.addItem("By count", "count")
        self._x_tick_step_edit = QtWidgets.QLineEdit(formatting_section)
        self._x_tick_step_edit.setPlaceholderText("auto")
        self._x_tick_count_spin = QtWidgets.QSpinBox(formatting_section)
        self._x_tick_count_spin.setRange(2, 20)
        self._x_tick_count_spin.setValue(5)
        formatting_form.addRow("X ticks", self._x_tick_mode_combo)
        formatting_form.addRow("X increment", self._x_tick_step_edit)
        formatting_form.addRow("X count", self._x_tick_count_spin)

        self._y_tick_mode_combo = QtWidgets.QComboBox(formatting_section)
        self._y_tick_mode_combo.addItem("Auto", "auto")
        self._y_tick_mode_combo.addItem("By increment", "step")
        self._y_tick_mode_combo.addItem("By count", "count")
        self._y_tick_step_edit = QtWidgets.QLineEdit(formatting_section)
        self._y_tick_step_edit.setPlaceholderText("auto")
        self._y_tick_count_spin = QtWidgets.QSpinBox(formatting_section)
        self._y_tick_count_spin.setRange(2, 20)
        self._y_tick_count_spin.setValue(5)
        formatting_form.addRow("Y ticks", self._y_tick_mode_combo)
        formatting_form.addRow("Y increment", self._y_tick_step_edit)
        formatting_form.addRow("Y count", self._y_tick_count_spin)

        self._auto_x_limits_checkbox = QtWidgets.QCheckBox("Auto X limits", formatting_section)
        self._auto_x_limits_checkbox.setChecked(True)
        formatting_form.addRow("", self._auto_x_limits_checkbox)

        x_limits_row = QtWidgets.QWidget(formatting_section)
        x_limits_layout = QtWidgets.QHBoxLayout(x_limits_row)
        x_limits_layout.setContentsMargins(0, 0, 0, 0)
        x_limits_layout.setSpacing(4)
        self._x_min_spin = QtWidgets.QDoubleSpinBox(x_limits_row)
        self._x_min_spin.setRange(-1_000_000.0, 1_000_000.0)
        self._x_min_spin.setDecimals(3)
        self._x_min_spin.setValue(-200.0)
        self._x_max_spin = QtWidgets.QDoubleSpinBox(x_limits_row)
        self._x_max_spin.setRange(-1_000_000.0, 1_000_000.0)
        self._x_max_spin.setDecimals(3)
        self._x_max_spin.setValue(600.0)
        x_limits_layout.addWidget(QtWidgets.QLabel("X min", x_limits_row))
        x_limits_layout.addWidget(self._x_min_spin)
        x_limits_layout.addWidget(QtWidgets.QLabel("X max", x_limits_row))
        x_limits_layout.addWidget(self._x_max_spin)
        formatting_form.addRow("", x_limits_row)

        self._auto_y_limits_checkbox = QtWidgets.QCheckBox("Auto Y limits", formatting_section)
        self._auto_y_limits_checkbox.setChecked(True)
        formatting_form.addRow("", self._auto_y_limits_checkbox)

        y_limits_row = QtWidgets.QWidget(formatting_section)
        y_limits_layout = QtWidgets.QHBoxLayout(y_limits_row)
        y_limits_layout.setContentsMargins(0, 0, 0, 0)
        y_limits_layout.setSpacing(4)
        self._y_min_spin = QtWidgets.QDoubleSpinBox(y_limits_row)
        self._y_min_spin.setRange(-1_000_000.0, 1_000_000.0)
        self._y_min_spin.setDecimals(3)
        self._y_min_spin.setValue(-1.0)
        self._y_max_spin = QtWidgets.QDoubleSpinBox(y_limits_row)
        self._y_max_spin.setRange(-1_000_000.0, 1_000_000.0)
        self._y_max_spin.setDecimals(3)
        self._y_max_spin.setValue(20.0)
        y_limits_layout.addWidget(QtWidgets.QLabel("Y min", y_limits_row))
        y_limits_layout.addWidget(self._y_min_spin)
        y_limits_layout.addWidget(QtWidgets.QLabel("Y max", y_limits_row))
        y_limits_layout.addWidget(self._y_max_spin)
        formatting_form.addRow("", y_limits_row)

        if self._auto_x_limits_checkbox is not None:
            self._auto_x_limits_checkbox.toggled.connect(
                lambda checked: self._set_limit_controls_enabled("x", not checked)
            )
        if self._auto_y_limits_checkbox is not None:
            self._auto_y_limits_checkbox.toggled.connect(
                lambda checked: self._set_limit_controls_enabled("y", not checked)
            )
        if self._x_tick_mode_combo is not None:
            self._x_tick_mode_combo.currentIndexChanged.connect(self._sync_tick_mode_inputs)
        if self._y_tick_mode_combo is not None:
            self._y_tick_mode_combo.currentIndexChanged.connect(self._sync_tick_mode_inputs)
        self._sync_tick_mode_inputs()
        self._set_limit_controls_enabled("x", False)
        self._set_limit_controls_enabled("y", False)

        apply_row = QtWidgets.QHBoxLayout()
        apply_current_button = QtWidgets.QPushButton("Apply to current graph", formatting_section)
        apply_current_button.clicked.connect(self._apply_formatting_to_current_plot)
        apply_row.addWidget(apply_current_button)
        apply_all_button = QtWidgets.QPushButton("Apply to all DMA graphs", formatting_section)
        apply_all_button.clicked.connect(self._apply_formatting_to_all_plots)
        apply_row.addWidget(apply_all_button)
        apply_selected_button = QtWidgets.QPushButton("Apply selected formatting…", formatting_section)
        apply_selected_button.clicked.connect(self._apply_selected_formatting_to_chosen_plots)
        apply_row.addWidget(apply_selected_button)
        formatting_layout.addLayout(apply_row)
        formatting_layout.addStretch(1)

        options_layout.addStretch(1)
        layout.addWidget(formatting_section)
        layout.addStretch(1)

        self._settings_widget = container
        return container

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        entries: List[DmaIsoStressEntry] = []
        for path in paths:
            try:
                datasets = parse_dma_txt(Path(path))
            except Exception as exc:
                self._log(f"Failed to parse {path.name}: {exc}", level="error")
                continue
            if not datasets:
                self._log(f"No iso-stress data found in {path.name}.", level="error")
                continue
            entries.append(DmaIsoStressEntry(path=Path(path), sample=path.stem, datasets=datasets))

        self._dataset = entries
        self._data = entries  # satisfy host readiness checks
        self._loaded_paths = list(paths)
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        self._log(f"Loaded {len(entries)} DMA iso-stress file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._dataset:
            self.load_data()
        if not self._dataset:
            return

        self._clear_tabs()
        show_markers = bool(
            self._markers_checkbox.isChecked() if self._markers_checkbox is not None else False
        )
        sort_stress = bool(
            self._sort_checkbox.isChecked() if self._sort_checkbox is not None else True
        )
        line_width = self._line_width_value()

        for entry in self._dataset:
            self._create_plot_tab_for_entry(
                entry,
                show_markers=show_markers,
                sort_stress=sort_stress,
                line_width=line_width,
            )
        self._set_tab_bar_visible(False)

    def _create_plot_tab_for_entry(
        self,
        entry: DmaIsoStressEntry,
        *,
        show_markers: bool,
        sort_stress: bool,
        line_width: float,
    ) -> tuple[QtWidgets.QWidget, Any]:
        window_module = window_api()
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

        fig = Figure(figsize=(8.5, 5))
        ax = fig.add_subplot(111)
        stresses = list(entry.datasets.keys())
        if sort_stress:
            stresses.sort()
        for stress in stresses:
            temps, strains = entry.datasets[stress]
            label = f"{stress} MPa"
            marker = "o" if show_markers else None
            line = ax.plot(temps, strains, linewidth=line_width, marker=marker, label=label)[0]
            setattr(line, "_mw_dma_base_label", label)
        self._apply_axes_formatting(ax, sample=entry.sample)
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        canvas = FigureCanvas(fig)
        canvas.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(canvas)
        title_text = self._title_for_sample(entry.sample)
        x_label = self._x_label_text()
        y_label = self._y_label_text()
        descriptor = window_module.TabDescriptor(
            kind="dma_iso_stress",
            title=title_text,
            root_label=f"{entry.sample} - IsoStress",
            x_label=x_label,
            y_label=y_label,
            canvas=canvas,
            axes=ax,
            lines={},
            metadata={
                "sample": entry.sample,
                "path": str(entry.path),
                "legend_label_overrides": {},
            },
        )
        index = self.host.tab_widget.addTab(tab, descriptor.root_label or "Plot")
        setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
        if callable(setter):
            setter(index)
        self.host._register_plot_tab(tab, canvas, ax, descriptor)
        self._plot_tabs.append(tab)
        return tab, descriptor

    def update_ui(self) -> None:  # type: ignore[override]
        has_data = bool(self._dataset)
        has_plots = any(True for _ in self._iter_dma_descriptors())
        self.apply_shared_action_state(
            can_plot=has_data or self._host_has_data_selection(),
            can_save_graph=has_plots,
            can_normalize=has_plots,
            can_export_txt=False,
            can_open_origin=has_data,
            can_export_workbooks=False,
        )
        if self._summary_label is not None:
            if not has_data:
                self._summary_label.setText(
                    "Import DMA iso-stress TXT files, then plot temperature vs strain."
                )
            else:
                self._summary_label.clear()

    def _set_limit_controls_enabled(self, axis: str, enabled: bool) -> None:
        widgets: Iterable[QtWidgets.QDoubleSpinBox | None]
        if axis == "x":
            widgets = (self._x_min_spin, self._x_max_spin)
        else:
            widgets = (self._y_min_spin, self._y_max_spin)
        for widget in widgets:
            if widget is not None:
                widget.setEnabled(enabled)

    def _sync_tick_mode_inputs(self) -> None:
        mode_combo: QtWidgets.QComboBox | None
        step_edit: QtWidgets.QLineEdit | None
        count_spin: QtWidgets.QSpinBox | None
        for axis in ("x", "y"):
            if axis == "x":
                mode_combo = self._x_tick_mode_combo
                step_edit = self._x_tick_step_edit
                count_spin = self._x_tick_count_spin
            else:
                mode_combo = self._y_tick_mode_combo
                step_edit = self._y_tick_step_edit
                count_spin = self._y_tick_count_spin
            mode = str(mode_combo.currentData() if mode_combo is not None else "auto")
            if step_edit is not None:
                step_edit.setEnabled(mode == "step")
            if count_spin is not None:
                count_spin.setEnabled(mode == "count")

    @staticmethod
    def _multiple_locator_step(locator: Any) -> float | None:
        for attr in ("_base", "base"):
            value = getattr(locator, attr, None)
            try:
                value_f = float(value)
            except Exception:
                continue
            if math.isfinite(value_f) and value_f > 0:
                return value_f
        try:
            values = locator.tick_values(0.0, 1.0)
            if len(values) >= 2:
                step = float(values[1] - values[0])
                if math.isfinite(step) and step > 0:
                    return step
        except Exception:
            pass
        return None

    @staticmethod
    def _tick_mode_from_axis(axis_obj: Any) -> tuple[str, float | None, int]:
        locator = None
        getter = getattr(axis_obj, "get_major_locator", None)
        if callable(getter):
            try:
                locator = getter()
            except Exception:
                locator = None
        if isinstance(locator, mticker.MultipleLocator):
            return "step", DmaIsoStressPlugin._multiple_locator_step(locator), 5
        if isinstance(locator, mticker.AutoLocator):
            return "auto", None, 5
        if isinstance(locator, mticker.MaxNLocator):
            nbins = int(getattr(locator, "_nbins", 5) or 5)
            return "count", None, max(2, nbins + 1)
        return "auto", None, 5

    @staticmethod
    def _apply_tick_locator(
        axis_obj: Any,
        mode: str,
        *,
        step: float | None = None,
        count: int = 5,
    ) -> None:
        if mode == "step" and step is not None and math.isfinite(step) and step > 0:
            axis_obj.set_major_locator(mticker.MultipleLocator(step))
            return
        if mode == "count":
            axis_obj.set_major_locator(
                mticker.MaxNLocator(nbins=max(1, count - 1), min_n_ticks=max(2, count))
            )
            return
        axis_obj.set_major_locator(mticker.AutoLocator())

    @staticmethod
    def _tick_grid_visible(axes: Any) -> bool:
        try:
            x_lines = list(axes.get_xgridlines())
            y_lines = list(axes.get_ygridlines())
        except Exception:
            return False
        for line in x_lines + y_lines:
            try:
                if bool(line.get_visible()):
                    return True
            except Exception:
                continue
        return False

    def _line_width_value(self) -> float:
        if self._line_width_spin is None:
            return 1.4
        return float(self._line_width_spin.value())

    @staticmethod
    def _with_show_checkbox(
        line_edit: QtWidgets.QLineEdit,
        checkbox: QtWidgets.QCheckBox,
    ) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget(line_edit.parentWidget())
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(line_edit, 1)
        layout.addWidget(checkbox, 0)
        return row

    def _font_size_value(self) -> int:
        if self._font_size_spin is None:
            return 12
        return int(self._font_size_spin.value())

    def _x_label_text(self) -> str:
        text = self._xlabel_edit.text().strip() if self._xlabel_edit is not None else ""
        return text or self._DEFAULT_X_LABEL

    def _y_label_text(self) -> str:
        text = self._ylabel_edit.text().strip() if self._ylabel_edit is not None else ""
        return text or self._DEFAULT_Y_LABEL

    def _title_for_sample(self, sample: str) -> str:
        template = self._title_edit.text().strip() if self._title_edit is not None else ""
        if not template:
            return f"{sample} - DMA Iso-Stress"
        if "{sample}" in template:
            try:
                return template.format(sample=sample)
            except Exception:
                return template
        return template

    def _axis_limits(self, axis: str) -> tuple[float, float] | None:
        if axis == "x":
            auto_checkbox = self._auto_x_limits_checkbox
            min_spin = self._x_min_spin
            max_spin = self._x_max_spin
        else:
            auto_checkbox = self._auto_y_limits_checkbox
            min_spin = self._y_min_spin
            max_spin = self._y_max_spin
        if auto_checkbox is None or bool(auto_checkbox.isChecked()):
            return None
        if min_spin is None or max_spin is None:
            return None
        low = float(min_spin.value())
        high = float(max_spin.value())
        if low == high:
            high = low + 1.0
        if low > high:
            low, high = high, low
        return (low, high)

    @staticmethod
    def _parse_positive_float(text: str) -> float | None:
        cleaned = text.strip().replace(",", ".")
        if not cleaned:
            return None
        try:
            value = float(cleaned)
        except Exception:
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        return value

    def _tick_settings(self, axis: str) -> tuple[str, float | None, int]:
        if axis == "x":
            mode_combo = self._x_tick_mode_combo
            step_edit = self._x_tick_step_edit
            count_spin = self._x_tick_count_spin
        else:
            mode_combo = self._y_tick_mode_combo
            step_edit = self._y_tick_step_edit
            count_spin = self._y_tick_count_spin
        mode = str(mode_combo.currentData() if mode_combo is not None else "auto")
        step = self._parse_positive_float(step_edit.text()) if step_edit is not None else None
        count = int(count_spin.value()) if count_spin is not None else 5
        return mode, step, max(2, count)

    def _set_tick_settings(
        self,
        axis: str,
        *,
        mode: str,
        step: float | None,
        count: int,
    ) -> None:
        if axis == "x":
            mode_combo = self._x_tick_mode_combo
            step_edit = self._x_tick_step_edit
            count_spin = self._x_tick_count_spin
        else:
            mode_combo = self._y_tick_mode_combo
            step_edit = self._y_tick_step_edit
            count_spin = self._y_tick_count_spin
        if mode_combo is not None:
            index = mode_combo.findData(mode)
            if index < 0:
                index = mode_combo.findData("auto")
            if index >= 0:
                mode_combo.setCurrentIndex(index)
        if step_edit is not None:
            step_edit.setText(
                f"{step:.6g}" if step is not None and math.isfinite(step) and step > 0 else ""
            )
        if count_spin is not None:
            count_spin.setValue(max(2, min(int(count), 20)))

    def _legend_location(self) -> str:
        if self._legend_location_combo is None:
            return "best"
        value = self._legend_location_combo.currentData()
        return str(value) if value else "best"

    def _current_dma_descriptor(self) -> tuple[QtWidgets.QWidget, Any] | None:
        tab = self.host.tab_widget.currentWidget()
        if tab is None:
            return None
        descriptors = getattr(self.host, "_tab_descriptors", {})
        descriptor = descriptors.get(tab) if isinstance(descriptors, dict) else None
        if descriptor is None or getattr(descriptor, "kind", "") != "dma_iso_stress":
            return None
        return tab, descriptor

    @staticmethod
    def _legend_overrides_for_descriptor(descriptor: Any) -> dict[str, str]:
        metadata = getattr(descriptor, "metadata", None)
        if not isinstance(metadata, dict):
            return {}
        raw = metadata.get("legend_label_overrides")
        if not isinstance(raw, dict):
            return {}
        cleaned: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            clean_key = key.strip()
            clean_value = value.strip()
            if not clean_key:
                continue
            cleaned[clean_key] = clean_value or clean_key
        return cleaned

    @staticmethod
    def _set_legend_overrides_for_descriptor(
        descriptor: Any, overrides: dict[str, str]
    ) -> None:
        metadata = getattr(descriptor, "metadata", None)
        if not isinstance(metadata, dict):
            return
        metadata["legend_label_overrides"] = dict(overrides)

    @staticmethod
    def _iter_legend_lines(axes: Any) -> list[tuple[Any, str, str]]:
        try:
            lines = list(axes.get_lines())
        except Exception:
            lines = []
        entries: list[tuple[Any, str, str]] = []
        for line in lines:
            try:
                current_label = str(line.get_label() or "").strip()
            except Exception:
                current_label = ""
            if not current_label or current_label.startswith("_") or current_label == "_nolegend_":
                continue
            base_label = str(getattr(line, "_mw_dma_base_label", "") or "").strip()
            if not base_label or base_label.startswith("_"):
                base_label = current_label
                try:
                    setattr(line, "_mw_dma_base_label", base_label)
                except Exception:
                    pass
            entries.append((line, base_label, current_label))
        return entries

    def _apply_legend_overrides_to_axes(
        self,
        axes: Any,
        overrides: dict[str, str],
    ) -> None:
        for line, base_label, _ in self._iter_legend_lines(axes):
            new_label = overrides.get(base_label, base_label).strip() or base_label
            try:
                line.set_label(new_label)
            except Exception:
                continue

    def _refresh_legend_after_label_change(self, axes: Any) -> None:
        sync_legend = getattr(self.host, "_sync_axes_legend_with_visible_lines", None)
        if callable(sync_legend):
            try:
                sync_legend(axes, plugin_name=self.name)
                return
            except Exception:
                pass
        try:
            legend = axes.get_legend()
        except Exception:
            legend = None
        if legend is not None:
            try:
                legend.remove()
            except Exception:
                pass
        try:
            axes.legend(loc=self._legend_location())
        except Exception:
            pass

    def _edit_current_legend_entries(self) -> None:
        current = self._current_dma_descriptor()
        if current is None:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Select a DMA graph before editing legend entries.",
            )
            return
        tab, descriptor = current
        axes = getattr(descriptor, "axes", None)
        if axes is None:
            return
        rows = self._iter_legend_lines(axes)
        if not rows:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "No visible legend entries are available to edit.",
            )
            return

        dialog = QtWidgets.QDialog(self.host)
        dialog.setWindowTitle("Edit legend entries")
        dialog.resize(460, 360)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QtWidgets.QLabel(
            "Rename legend entries for the current DMA graph. Leave a field empty to use the original label.",
            dialog,
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form_host = QtWidgets.QWidget(dialog)
        form = QtWidgets.QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        edits: list[tuple[str, QtWidgets.QLineEdit]] = []
        for _line, base_label, current_label in rows:
            edit = QtWidgets.QLineEdit(form_host)
            edit.setText(current_label)
            edit.setPlaceholderText(base_label)
            form.addRow(base_label, edit)
            edits.append((base_label, edit))
        layout.addWidget(form_host, 1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        overrides: dict[str, str] = {}
        for base_label, edit in edits:
            value = edit.text().strip()
            if not value:
                value = base_label
            if value != base_label:
                overrides[base_label] = value
        self._set_legend_overrides_for_descriptor(descriptor, overrides)
        self._apply_legend_overrides_to_axes(axes, overrides)
        self._refresh_legend_after_label_change(axes)

        canvas = getattr(descriptor, "canvas", None)
        if canvas is not None:
            try:
                canvas.draw_idle()
            except Exception:
                pass
        self.host._rebuild_object_manager_for_tab(tab)
        self._log("Updated DMA legend entry labels for current graph.")

    def _reset_current_legend_entries(self) -> None:
        current = self._current_dma_descriptor()
        if current is None:
            return
        tab, descriptor = current
        axes = getattr(descriptor, "axes", None)
        if axes is None:
            return
        self._set_legend_overrides_for_descriptor(descriptor, {})
        self._apply_legend_overrides_to_axes(axes, {})
        self._refresh_legend_after_label_change(axes)
        canvas = getattr(descriptor, "canvas", None)
        if canvas is not None:
            try:
                canvas.draw_idle()
            except Exception:
                pass
        self.host._rebuild_object_manager_for_tab(tab)
        self._log("Reset DMA legend entries to default labels.")

    def _checked_groups(self) -> set[str]:
        return set(self._FORMAT_GROUP_LABELS.keys())

    def _show_title(self) -> bool:
        return bool(
            self._show_title_checkbox.isChecked()
            if self._show_title_checkbox is not None
            else True
        )

    def _show_x_label(self) -> bool:
        return bool(
            self._show_xlabel_checkbox.isChecked()
            if self._show_xlabel_checkbox is not None
            else True
        )

    def _show_y_label(self) -> bool:
        return bool(
            self._show_ylabel_checkbox.isChecked()
            if self._show_ylabel_checkbox is not None
            else True
        )

    def _apply_axes_formatting(
        self,
        axes: Any,
        *,
        sample: str,
        groups: set[str] | None = None,
    ) -> None:
        if axes is None:
            return
        active_groups = groups if groups is not None else self._checked_groups()
        title_text = self._title_for_sample(sample)
        x_label = self._x_label_text()
        y_label = self._y_label_text()
        font_size = self._font_size_value()
        line_width = self._line_width_value()
        show_title = self._show_title()
        show_x_label = self._show_x_label()
        show_y_label = self._show_y_label()
        show_markers = bool(
            self._markers_checkbox.isChecked() if self._markers_checkbox is not None else False
        )
        show_grid = bool(self._grid_checkbox.isChecked() if self._grid_checkbox is not None else False)

        if "title" in active_groups:
            axes.set_title(title_text)
            try:
                axes.title.set_visible(show_title)
            except Exception:
                pass
        if "x_label" in active_groups:
            axes.set_xlabel(x_label)
            try:
                axes.xaxis.label.set_visible(show_x_label)
            except Exception:
                pass
        if "y_label" in active_groups:
            axes.set_ylabel(y_label)
            try:
                axes.yaxis.label.set_visible(show_y_label)
            except Exception:
                pass
        if "font" in active_groups:
            try:
                axes.title.set_fontsize(font_size + 1)
                axes.xaxis.label.set_fontsize(font_size)
                axes.yaxis.label.set_fontsize(font_size)
                axes.tick_params(axis="both", labelsize=max(6, font_size - 1))
            except Exception:
                pass
        if "grid" in active_groups:
            try:
                axes.grid(show_grid, which="major", axis="both")
            except Exception:
                pass

        try:
            lines = list(axes.get_lines())
        except Exception:
            lines = []
        if "line_style" in active_groups:
            for line in lines:
                try:
                    line.set_linewidth(line_width)
                except Exception:
                    pass
                if show_markers:
                    try:
                        marker = line.get_marker()
                    except Exception:
                        marker = None
                    if str(marker).strip().lower() in {"", "none"}:
                        try:
                            line.set_marker("o")
                        except Exception:
                            pass
                    try:
                        if float(line.get_markersize()) <= 0.0:
                            line.set_markersize(5.5)
                    except Exception:
                        pass
                else:
                    try:
                        line.set_marker(None)
                    except Exception:
                        pass

        x_limits = self._axis_limits("x")
        y_limits = self._axis_limits("y")
        x_tick_mode, x_tick_step, x_tick_count = self._tick_settings("x")
        y_tick_mode, y_tick_step, y_tick_count = self._tick_settings("y")
        if "x_limits" in active_groups:
            if x_limits is None:
                try:
                    axes.autoscale(enable=True, axis="x", tight=False)
                except Exception:
                    pass
            else:
                axes.set_xlim(*x_limits)
        if "y_limits" in active_groups:
            if y_limits is None:
                try:
                    axes.autoscale(enable=True, axis="y", tight=False)
                except Exception:
                    pass
            else:
                axes.set_ylim(*y_limits)
        if "ticks" in active_groups:
            self._apply_tick_locator(
                axes.xaxis,
                x_tick_mode,
                step=x_tick_step,
                count=x_tick_count,
            )
            self._apply_tick_locator(
                axes.yaxis,
                y_tick_mode,
                step=y_tick_step,
                count=y_tick_count,
            )

        show_legend = bool(self._legend_checkbox.isChecked() if self._legend_checkbox is not None else True)
        if "legend" in active_groups:
            legend = None
            if show_legend:
                sync_legend = getattr(self.host, "_sync_axes_legend_with_visible_lines", None)
                if callable(sync_legend):
                    try:
                        legend = sync_legend(axes, plugin_name=self.name)
                    except Exception:
                        legend = None
                if legend is None:
                    try:
                        legend = axes.legend(loc=self._legend_location())
                    except Exception:
                        legend = None
                if legend is not None:
                    try:
                        legend.set_loc(self._legend_location())
                    except Exception:
                        pass
                    try:
                        legend.set_fontsize(font_size)
                        legend.get_title().set_fontsize(font_size)
                    except Exception:
                        pass
            else:
                try:
                    legend = axes.get_legend()
                except Exception:
                    legend = None
                if legend is not None:
                    try:
                        legend.remove()
                    except Exception:
                        pass

    def _iter_dma_descriptors(self) -> Iterable[tuple[QtWidgets.QWidget, Any]]:
        descriptors = getattr(self.host, "_tab_descriptors", {})
        if not isinstance(descriptors, dict):
            return []
        return (
            (tab, descriptor)
            for tab, descriptor in descriptors.items()
            if getattr(descriptor, "kind", "") == "dma_iso_stress"
        )

    def _apply_formatting_to_descriptor(
        self,
        tab: QtWidgets.QWidget,
        descriptor: Any,
        *,
        groups: set[str] | None = None,
        source_legend_overrides: dict[str, str] | None = None,
    ) -> None:
        sample = ""
        metadata = getattr(descriptor, "metadata", None)
        if isinstance(metadata, dict):
            sample_value = metadata.get("sample")
            if isinstance(sample_value, str):
                sample = sample_value
        if not sample:
            sample = getattr(descriptor, "root_label", "") or "DMA Iso-Stress"

        axes = getattr(descriptor, "axes", None)
        active_groups = groups if groups is not None else self._checked_groups()
        if axes is not None and "legend_labels" in active_groups:
            legend_overrides = (
                dict(source_legend_overrides)
                if source_legend_overrides is not None
                else self._legend_overrides_for_descriptor(descriptor)
            )
            self._set_legend_overrides_for_descriptor(descriptor, legend_overrides)
            self._apply_legend_overrides_to_axes(axes, legend_overrides)
        self._apply_axes_formatting(axes, sample=sample, groups=active_groups)
        if axes is not None and "legend_labels" in active_groups and "legend" not in active_groups:
            self._refresh_legend_after_label_change(axes)
        if "title" in active_groups:
            descriptor.title = self._title_for_sample(sample)
        if "x_label" in active_groups:
            descriptor.x_label = self._x_label_text()
        if "y_label" in active_groups:
            descriptor.y_label = self._y_label_text()

        graph_items = getattr(self.host, "_graph_tree_items", {})
        if isinstance(graph_items, dict) and "title" in active_groups:
            item = graph_items.get(tab)
            if isinstance(item, QtWidgets.QTreeWidgetItem):
                item.setText(1, descriptor.title or "")

        canvas = getattr(descriptor, "canvas", None)
        if canvas is not None:
            try:
                canvas.draw_idle()
            except Exception:
                pass

    def _apply_formatting_to_current_plot(self) -> None:
        tab = self.host.tab_widget.currentWidget()
        if tab is None:
            return
        descriptors = getattr(self.host, "_tab_descriptors", {})
        descriptor = descriptors.get(tab) if isinstance(descriptors, dict) else None
        if descriptor is None or getattr(descriptor, "kind", "") != "dma_iso_stress":
            return
        self._apply_formatting_to_descriptor(tab, descriptor)
        self.host._rebuild_object_manager_for_tab(tab)
        self._log("Applied DMA formatting to current graph.")

    def _apply_formatting_to_all_plots(self) -> None:
        count = 0
        for tab, descriptor in self._iter_dma_descriptors():
            self._apply_formatting_to_descriptor(tab, descriptor)
            count += 1
        current_tab = self.host.tab_widget.currentWidget()
        if current_tab is not None:
            self.host._rebuild_object_manager_for_tab(current_tab)
        if count:
            self._log(f"Applied DMA formatting to {count} graph(s).")

    def _apply_selected_formatting_to_chosen_plots(self) -> None:
        selected = self._prompt_selected_formatting_targets()
        if selected is None:
            return
        targets, groups = selected
        source_overrides: dict[str, str] | None = None
        if "legend_labels" in groups:
            current = self._current_dma_descriptor()
            if current is not None:
                _tab, current_descriptor = current
                source_overrides = self._legend_overrides_for_descriptor(
                    current_descriptor
                )
        count = 0
        for tab, descriptor in targets:
            self._apply_formatting_to_descriptor(
                tab,
                descriptor,
                groups=groups,
                source_legend_overrides=source_overrides,
            )
            count += 1
        current_tab = self.host.tab_widget.currentWidget()
        if current_tab is not None:
            self.host._rebuild_object_manager_for_tab(current_tab)
        if count:
            self._log(
                f"Applied selected DMA formatting groups to {count} graph(s)."
            )

    def _prompt_selected_formatting_targets(
        self,
    ) -> tuple[list[tuple[QtWidgets.QWidget, Any]], set[str]] | None:
        current_tab = self.host.tab_widget.currentWidget()
        candidates: list[tuple[QtWidgets.QWidget, Any]] = []
        for tab, descriptor in self._iter_dma_descriptors():
            if tab is current_tab:
                continue
            candidates.append((tab, descriptor))
        if not candidates:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Open at least one additional DMA graph before applying selected formatting.",
            )
            return None

        dialog = QtWidgets.QDialog(self.host)
        dialog.setWindowTitle("Apply selected formatting")
        dialog.resize(500, 430)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QtWidgets.QLabel(
            "Choose target graph(s) and formatting groups to copy from the current settings.",
            dialog,
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        target_list = QtWidgets.QListWidget(dialog)
        target_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection
        )
        for index, (tab, descriptor) in enumerate(candidates, start=1):
            root_label = str(getattr(descriptor, "root_label", "") or "").strip()
            title = str(getattr(descriptor, "title", "") or "").strip()
            label = root_label or title or f"DMA graph {index}"
            item = QtWidgets.QListWidgetItem(label, target_list)
            item.setFlags(
                item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if index == 1
                else QtCore.Qt.CheckState.Unchecked
            )
        layout.addWidget(target_list, 2)

        group_box = QtWidgets.QGroupBox("Formatting groups", dialog)
        group_layout = QtWidgets.QGridLayout(group_box)
        group_layout.setContentsMargins(8, 8, 8, 8)
        group_layout.setHorizontalSpacing(12)
        group_layout.setVerticalSpacing(4)

        group_checkboxes: dict[str, QtWidgets.QCheckBox] = {}
        for idx, (key, label) in enumerate(self._FORMAT_GROUP_LABELS.items()):
            cb = QtWidgets.QCheckBox(label, group_box)
            cb.setChecked(True)
            row = idx // 2
            col = idx % 2
            group_layout.addWidget(cb, row, col)
            group_checkboxes[key] = cb
        layout.addWidget(group_box, 1)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return None

        targets: list[tuple[QtWidgets.QWidget, Any]] = []
        for row, candidate in enumerate(candidates):
            item = target_list.item(row)
            if item is None:
                continue
            if item.checkState() == QtCore.Qt.CheckState.Checked:
                targets.append(candidate)
        if not targets:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Select at least one target graph.",
            )
            return None

        groups = {
            key for key, checkbox in group_checkboxes.items() if checkbox.isChecked()
        }
        if not groups:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Select at least one formatting group.",
            )
            return None
        return targets, groups

    def _portable_path(self, path: Path | None, base_path: Path | None) -> str | None:
        helper = getattr(self.host, "_portable_path", None)
        if callable(helper):
            try:
                return helper(path, base_path)
            except Exception:
                pass
        if path is None:
            return None
        return str(path)

    def _resolve_portable_path(self, value: str | None, project_dir: Path) -> Path | None:
        helper = getattr(self.host, "_resolve_portable_path", None)
        if callable(helper):
            try:
                return helper(value, project_dir)
            except Exception:
                pass
        if not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (project_dir / candidate).resolve()
        return candidate

    def _collect_loaded_paths(self) -> list[Path]:
        if self._loaded_paths:
            return list(self._loaded_paths)
        selected_paths = []
        getter = getattr(self.host, "_selected_paths", None)
        if callable(getter):
            try:
                selected_paths = [Path(path) for path in getter() if isinstance(path, Path)]
            except Exception:
                selected_paths = []
        return selected_paths

    def _serialize_formatting_state(self) -> Dict[str, Any]:
        x_tick_mode, x_tick_step, x_tick_count = self._tick_settings("x")
        y_tick_mode, y_tick_step, y_tick_count = self._tick_settings("y")
        return {
            "show_markers": bool(self._markers_checkbox.isChecked()) if self._markers_checkbox else False,
            "sort_stress": bool(self._sort_checkbox.isChecked()) if self._sort_checkbox else True,
            "title_template": self._title_edit.text() if self._title_edit is not None else "",
            "x_label": self._x_label_text(),
            "y_label": self._y_label_text(),
            "show_title": self._show_title(),
            "show_x_label": self._show_x_label(),
            "show_y_label": self._show_y_label(),
            "line_width": self._line_width_value(),
            "font_size": self._font_size_value(),
            "show_grid": bool(self._grid_checkbox.isChecked()) if self._grid_checkbox else False,
            "show_legend": bool(self._legend_checkbox.isChecked()) if self._legend_checkbox else True,
            "legend_location": self._legend_location(),
            "auto_x_limits": bool(self._auto_x_limits_checkbox.isChecked()) if self._auto_x_limits_checkbox else True,
            "auto_y_limits": bool(self._auto_y_limits_checkbox.isChecked()) if self._auto_y_limits_checkbox else True,
            "x_min": float(self._x_min_spin.value()) if self._x_min_spin is not None else None,
            "x_max": float(self._x_max_spin.value()) if self._x_max_spin is not None else None,
            "y_min": float(self._y_min_spin.value()) if self._y_min_spin is not None else None,
            "y_max": float(self._y_max_spin.value()) if self._y_max_spin is not None else None,
            "x_tick_mode": x_tick_mode,
            "x_tick_step": x_tick_step,
            "x_tick_count": x_tick_count,
            "y_tick_mode": y_tick_mode,
            "y_tick_step": y_tick_step,
            "y_tick_count": y_tick_count,
        }

    def _apply_formatting_state(self, state: Dict[str, Any]) -> None:
        if self._markers_checkbox is not None:
            self._markers_checkbox.setChecked(bool(state.get("show_markers", False)))
        if self._sort_checkbox is not None:
            self._sort_checkbox.setChecked(bool(state.get("sort_stress", True)))
        if self._title_edit is not None:
            self._title_edit.setText(str(state.get("title_template", "")))
        if self._xlabel_edit is not None:
            self._xlabel_edit.setText(str(state.get("x_label", self._DEFAULT_X_LABEL)))
        if self._ylabel_edit is not None:
            self._ylabel_edit.setText(str(state.get("y_label", self._DEFAULT_Y_LABEL)))
        if self._show_title_checkbox is not None:
            self._show_title_checkbox.setChecked(bool(state.get("show_title", True)))
        if self._show_xlabel_checkbox is not None:
            self._show_xlabel_checkbox.setChecked(bool(state.get("show_x_label", True)))
        if self._show_ylabel_checkbox is not None:
            self._show_ylabel_checkbox.setChecked(bool(state.get("show_y_label", True)))
        if self._line_width_spin is not None:
            try:
                self._line_width_spin.setValue(float(state.get("line_width", 1.4)))
            except Exception:
                pass
        if self._font_size_spin is not None:
            try:
                self._font_size_spin.setValue(int(state.get("font_size", 12)))
            except Exception:
                pass
        if self._grid_checkbox is not None:
            self._grid_checkbox.setChecked(bool(state.get("show_grid", False)))
        if self._legend_checkbox is not None:
            self._legend_checkbox.setChecked(bool(state.get("show_legend", True)))
        if self._legend_location_combo is not None:
            legend_loc = str(state.get("legend_location", "best"))
            idx = self._legend_location_combo.findData(legend_loc)
            if idx >= 0:
                self._legend_location_combo.setCurrentIndex(idx)
        if self._auto_x_limits_checkbox is not None:
            self._auto_x_limits_checkbox.setChecked(bool(state.get("auto_x_limits", True)))
        if self._auto_y_limits_checkbox is not None:
            self._auto_y_limits_checkbox.setChecked(bool(state.get("auto_y_limits", True)))
        for key, widget in (
            ("x_min", self._x_min_spin),
            ("x_max", self._x_max_spin),
            ("y_min", self._y_min_spin),
            ("y_max", self._y_max_spin),
        ):
            if widget is None:
                continue
            try:
                value = state.get(key)
                if isinstance(value, (int, float)):
                    widget.setValue(float(value))
            except Exception:
                continue
        self._set_tick_settings(
            "x",
            mode=str(state.get("x_tick_mode", "auto")),
            step=state.get("x_tick_step") if isinstance(state.get("x_tick_step"), (int, float)) else None,
            count=int(state.get("x_tick_count", 5)) if isinstance(state.get("x_tick_count"), int) else 5,
        )
        self._set_tick_settings(
            "y",
            mode=str(state.get("y_tick_mode", "auto")),
            step=state.get("y_tick_step") if isinstance(state.get("y_tick_step"), (int, float)) else None,
            count=int(state.get("y_tick_count", 5)) if isinstance(state.get("y_tick_count"), int) else 5,
        )
        self._sync_tick_mode_inputs()
        self._set_limit_controls_enabled(
            "x",
            not bool(self._auto_x_limits_checkbox.isChecked()) if self._auto_x_limits_checkbox else False,
        )
        self._set_limit_controls_enabled(
            "y",
            not bool(self._auto_y_limits_checkbox.isChecked()) if self._auto_y_limits_checkbox else False,
        )

    def _serialize_plot_state(
        self,
        descriptor: Any,
        *,
        base_path: Path | None,
    ) -> Dict[str, Any]:
        axes = getattr(descriptor, "axes", None)
        metadata = getattr(descriptor, "metadata", None)
        sample = metadata.get("sample") if isinstance(metadata, dict) else ""
        path_value = metadata.get("path") if isinstance(metadata, dict) else None
        source_path = Path(path_value) if isinstance(path_value, str) and path_value else None
        x_tick_mode, x_tick_step, x_tick_count = self._tick_mode_from_axis(axes.xaxis) if axes is not None else ("auto", None, 5)
        y_tick_mode, y_tick_step, y_tick_count = self._tick_mode_from_axis(axes.yaxis) if axes is not None else ("auto", None, 5)
        lines_payload: List[Dict[str, Any]] = []
        if axes is not None:
            for line in list(axes.get_lines()):
                try:
                    label = str(line.get_label() or "")
                except Exception:
                    label = ""
                base_label = str(getattr(line, "_mw_dma_base_label", "") or "").strip() or label
                lines_payload.append(
                    {
                        "base_label": base_label,
                        "label": label,
                        "visible": bool(line.get_visible()),
                        "linewidth": float(line.get_linewidth()),
                        "markersize": float(line.get_markersize()),
                        "marker": str(line.get_marker()),
                        "linestyle": str(line.get_linestyle()),
                        "color": str(line.get_color()),
                    }
                )
        x_limits = None
        y_limits = None
        if axes is not None:
            try:
                x_limits = [float(v) for v in axes.get_xlim()]
            except Exception:
                x_limits = None
            try:
                y_limits = [float(v) for v in axes.get_ylim()]
            except Exception:
                y_limits = None
        return {
            "sample": str(sample or ""),
            "source": self._portable_path(source_path, base_path),
            "title": str(axes.get_title()) if axes is not None else "",
            "x_label": str(axes.get_xlabel()) if axes is not None else "",
            "y_label": str(axes.get_ylabel()) if axes is not None else "",
            "show_title": bool(axes.title.get_visible()) if axes is not None else True,
            "show_x_label": bool(axes.xaxis.label.get_visible()) if axes is not None else True,
            "show_y_label": bool(axes.yaxis.label.get_visible()) if axes is not None else True,
            "show_grid": self._tick_grid_visible(axes) if axes is not None else False,
            "x_limits": x_limits,
            "y_limits": y_limits,
            "legend_visible": bool(axes.get_legend().get_visible()) if axes is not None and axes.get_legend() is not None else False,
            "legend_loc": self._legend_location(),
            "legend_overrides": self._legend_overrides_for_descriptor(descriptor),
            "x_tick_mode": x_tick_mode,
            "x_tick_step": x_tick_step,
            "x_tick_count": x_tick_count,
            "y_tick_mode": y_tick_mode,
            "y_tick_step": y_tick_step,
            "y_tick_count": y_tick_count,
            "lines": lines_payload,
        }

    def serialize_project_state(self, *, base_path: Path | None) -> Dict[str, Any] | None:  # type: ignore[override]
        plot_states: List[Dict[str, Any]] = []
        current_source: str | None = None
        current_tab = self.host.tab_widget.currentWidget()
        for tab, descriptor in self._iter_dma_descriptors():
            state = self._serialize_plot_state(descriptor, base_path=base_path)
            plot_states.append(state)
            if tab is current_tab:
                current_source = state.get("source")

        loaded_paths = [
            self._portable_path(path, base_path)
            for path in self._collect_loaded_paths()
            if isinstance(path, Path)
        ]
        loaded_paths = [path for path in loaded_paths if isinstance(path, str) and path]
        return {
            "loaded_paths": loaded_paths,
            "formatting": self._serialize_formatting_state(),
            "plots": plot_states,
            "current_plot_source": current_source,
        }

    def _load_dataset_from_paths(self, paths: Iterable[Path]) -> list[DmaIsoStressEntry]:
        entries: list[DmaIsoStressEntry] = []
        for path in paths:
            if not isinstance(path, Path) or not path.exists() or not path.is_file():
                continue
            try:
                datasets = parse_dma_txt(path)
            except Exception as exc:
                self._log(f"Failed to parse {path.name}: {exc}", level="error")
                continue
            if not datasets:
                continue
            entries.append(
                DmaIsoStressEntry(
                    path=path,
                    sample=path.stem,
                    datasets=datasets,
                )
            )
        return entries

    def _apply_restored_plot_state(self, tab: QtWidgets.QWidget, descriptor: Any, state: Dict[str, Any]) -> None:
        axes = getattr(descriptor, "axes", None)
        if axes is None:
            return

        title = state.get("title")
        if isinstance(title, str):
            axes.set_title(title)
        x_label = state.get("x_label")
        if isinstance(x_label, str):
            axes.set_xlabel(x_label)
        y_label = state.get("y_label")
        if isinstance(y_label, str):
            axes.set_ylabel(y_label)

        axes.title.set_visible(bool(state.get("show_title", True)))
        axes.xaxis.label.set_visible(bool(state.get("show_x_label", True)))
        axes.yaxis.label.set_visible(bool(state.get("show_y_label", True)))
        axes.grid(bool(state.get("show_grid", False)), which="major", axis="both")

        x_limits = state.get("x_limits")
        if isinstance(x_limits, list) and len(x_limits) == 2:
            try:
                axes.set_xlim(float(x_limits[0]), float(x_limits[1]))
            except Exception:
                pass
        y_limits = state.get("y_limits")
        if isinstance(y_limits, list) and len(y_limits) == 2:
            try:
                axes.set_ylim(float(y_limits[0]), float(y_limits[1]))
            except Exception:
                pass

        self._apply_tick_locator(
            axes.xaxis,
            str(state.get("x_tick_mode", "auto")),
            step=float(state.get("x_tick_step")) if isinstance(state.get("x_tick_step"), (int, float)) else None,
            count=int(state.get("x_tick_count", 5)) if isinstance(state.get("x_tick_count"), int) else 5,
        )
        self._apply_tick_locator(
            axes.yaxis,
            str(state.get("y_tick_mode", "auto")),
            step=float(state.get("y_tick_step")) if isinstance(state.get("y_tick_step"), (int, float)) else None,
            count=int(state.get("y_tick_count", 5)) if isinstance(state.get("y_tick_count"), int) else 5,
        )

        lines_state = state.get("lines")
        line_map: dict[str, Any] = {}
        for line in list(axes.get_lines()):
            try:
                label = str(line.get_label() or "")
            except Exception:
                label = ""
            base_label = str(getattr(line, "_mw_dma_base_label", "") or "").strip() or label
            if base_label:
                line_map[base_label] = line
            if label and label not in line_map:
                line_map[label] = line
        if isinstance(lines_state, list):
            for item in lines_state:
                if not isinstance(item, dict):
                    continue
                key = item.get("base_label")
                if not isinstance(key, str) or not key:
                    continue
                line = line_map.get(key)
                if line is None:
                    continue
                if isinstance(item.get("label"), str):
                    try:
                        line.set_label(str(item.get("label")))
                    except Exception:
                        pass
                if "visible" in item:
                    try:
                        line.set_visible(bool(item.get("visible")))
                    except Exception:
                        pass
                if isinstance(item.get("linewidth"), (int, float)):
                    try:
                        line.set_linewidth(float(item.get("linewidth")))
                    except Exception:
                        pass
                if isinstance(item.get("markersize"), (int, float)):
                    try:
                        line.set_markersize(float(item.get("markersize")))
                    except Exception:
                        pass
                if isinstance(item.get("marker"), str):
                    try:
                        line.set_marker(str(item.get("marker")))
                    except Exception:
                        pass
                if isinstance(item.get("linestyle"), str):
                    try:
                        line.set_linestyle(str(item.get("linestyle")))
                    except Exception:
                        pass
                if isinstance(item.get("color"), str):
                    try:
                        line.set_color(str(item.get("color")))
                    except Exception:
                        pass

        overrides = state.get("legend_overrides")
        if isinstance(overrides, dict):
            clean_overrides = {
                str(key): str(value)
                for key, value in overrides.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            self._set_legend_overrides_for_descriptor(descriptor, clean_overrides)
            self._apply_legend_overrides_to_axes(axes, clean_overrides)
        legend_visible = bool(state.get("legend_visible", True))
        if legend_visible:
            self._refresh_legend_after_label_change(axes)
            try:
                legend = axes.get_legend()
            except Exception:
                legend = None
            if legend is not None:
                try:
                    legend.set_visible(True)
                except Exception:
                    pass
        else:
            try:
                legend = axes.get_legend()
            except Exception:
                legend = None
            if legend is not None:
                try:
                    legend.remove()
                except Exception:
                    pass

        descriptor.title = axes.get_title()
        descriptor.x_label = axes.get_xlabel()
        descriptor.y_label = axes.get_ylabel()
        canvas = getattr(descriptor, "canvas", None)
        if canvas is not None:
            try:
                canvas.draw_idle()
            except Exception:
                pass
        self.host._rebuild_object_manager_for_tab(tab)

    def restore_project_state(self, state: Dict[str, Any], *, project_dir: Path) -> None:  # type: ignore[override]
        formatting = state.get("formatting")
        if isinstance(formatting, dict):
            self._apply_formatting_state(formatting)

        loaded_paths_payload = state.get("loaded_paths")
        loaded_paths: list[Path] = []
        if isinstance(loaded_paths_payload, list):
            for entry in loaded_paths_payload:
                if not isinstance(entry, str) or not entry.strip():
                    continue
                resolved = self._resolve_portable_path(entry, project_dir)
                if isinstance(resolved, Path):
                    loaded_paths.append(resolved)
        if loaded_paths:
            commit_paths = getattr(self.host, "_commit_selected_paths", None)
            if callable(commit_paths):
                try:
                    commit_paths(loaded_paths)
                except Exception:
                    pass

        self._loaded_paths = list(loaded_paths)
        self._dataset = self._load_dataset_from_paths(loaded_paths)
        self._data = self._dataset

        plots_payload = state.get("plots")
        current_source = state.get("current_plot_source")
        if isinstance(plots_payload, list) and plots_payload and self._dataset:
            self._clear_tabs()
            show_markers = bool(self._markers_checkbox.isChecked()) if self._markers_checkbox else False
            sort_stress = bool(self._sort_checkbox.isChecked()) if self._sort_checkbox else True
            line_width = self._line_width_value()
            remaining = list(self._dataset)
            restored_tabs: list[tuple[QtWidgets.QWidget, Any, Dict[str, Any]]] = []
            for plot_state in plots_payload:
                if not isinstance(plot_state, dict):
                    continue
                source = plot_state.get("source")
                sample = plot_state.get("sample")
                match: DmaIsoStressEntry | None = None
                if isinstance(source, str) and source.strip():
                    resolved = self._resolve_portable_path(source, project_dir)
                    if isinstance(resolved, Path):
                        for idx, entry in enumerate(remaining):
                            if entry.path == resolved:
                                match = remaining.pop(idx)
                                break
                if match is None and isinstance(sample, str) and sample:
                    for idx, entry in enumerate(remaining):
                        if entry.sample == sample:
                            match = remaining.pop(idx)
                            break
                if match is None:
                    continue
                tab, descriptor = self._create_plot_tab_for_entry(
                    match,
                    show_markers=show_markers,
                    sort_stress=sort_stress,
                    line_width=line_width,
                )
                self._apply_restored_plot_state(tab, descriptor, plot_state)
                restored_tabs.append((tab, descriptor, plot_state))
            if restored_tabs:
                if isinstance(current_source, str) and current_source.strip():
                    resolved_current = self._resolve_portable_path(current_source, project_dir)
                    for tab, _descriptor, plot_state in restored_tabs:
                        source_value = plot_state.get("source")
                        resolved_source = (
                            self._resolve_portable_path(source_value, project_dir)
                            if isinstance(source_value, str)
                            else None
                        )
                        if (
                            isinstance(resolved_current, Path)
                            and isinstance(resolved_source, Path)
                            and resolved_current == resolved_source
                        ):
                            index = self.host.tab_widget.indexOf(tab)
                            if index >= 0:
                                self.host.tab_widget.setCurrentIndex(index)
                            break
                self._set_tab_bar_visible(False)

        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._dataset:
            self.load_data()
        exported_holder = {"count": 0}

        def _task() -> None:
            with origin_session(keep_open=True) as op:
                exported = 0
                sort_stress = bool(
                    self._sort_checkbox.isChecked() if self._sort_checkbox is not None else True
                )
                show_markers = bool(
                    self._markers_checkbox.isChecked() if self._markers_checkbox is not None else False
                )
                marker_size = 6.0 if show_markers else 0.0
                line_width = self._line_width_value()
                for entry in self._dataset or []:
                    if not entry.datasets:
                        continue
                    try:
                        book = op.new_book("w")
                    except Exception:
                        continue
                    try:
                        book.lname = entry.sample
                    except Exception:
                        pass
                    sheet = book[0] if len(book) else book.add_sheet()
                    sheet.name = "Data"
                    graph = op.new_graph(template="line")
                    layer = graph[0] if graph else None
                    if layer is None:
                        continue
                    graph_title = self._title_for_sample(entry.sample)
                    try:
                        graph.set_str("title", graph_title)
                        graph.lname = graph_title
                    except Exception:
                        pass
                    try:
                        graph.activate()
                        safe_title = graph_title.replace('"', "'")
                        op.lt_exec(f'title -s "{safe_title}";')
                    except Exception:
                        pass
                    try:
                        graph.name = self._origin_graph_name(graph_title)
                    except Exception:
                        pass

                    col_index = 0
                    stresses = list(entry.datasets.keys())
                    if sort_stress:
                        stresses.sort()
                    for stress in stresses:
                        temps, strains = entry.datasets[stress]
                        if not temps:
                            continue
                        label = f"{stress} MPa"
                        try:
                            sheet.from_list(col_index, temps)
                            col_t = sheet.obj.Columns(col_index)
                            col_t.LongName = "Temperature"
                            col_t.Units = "°C"
                            col_t.Comment = label
                            col_t.Type = 3  # X
                        except Exception:
                            pass
                        try:
                            sheet.from_list(col_index + 1, strains)
                            col_e = sheet.obj.Columns(col_index + 1)
                            col_e.LongName = "Strain"
                            col_e.Units = "%"
                            col_e.Comment = label
                            col_e.Type = 4  # Y
                        except Exception:
                            pass
                        try:
                            plot_obj = layer.add_plot(
                                sheet, coly=col_index + 1, colx=col_index, type="y"
                            )
                        except Exception:
                            plot_obj = None
                        if plot_obj is not None:
                            try:
                                plot_obj.line_width = line_width
                            except Exception:
                                pass
                            if show_markers:
                                try:
                                    plot_obj.symbol_shape = 2
                                    plot_obj.symbol_size = marker_size
                                except Exception:
                                    pass
                            else:
                                try:
                                    plot_obj.symbol_size = 0
                                    plot_obj.symbol_shape = 0
                                except Exception:
                                    pass
                        col_index += 2

                    try:
                        sheet.header_rows("LUC")
                    except Exception:
                        pass
                    try:
                        layer.rescale()
                    except Exception:
                        pass
                    try:
                        layer.set_int("antialias", 1)
                        layer.set_int("use_speed_mode", 0)
                        layer.set_int("speedmode", 0)
                    except Exception:
                        pass
                    try:
                        layer.axis(0).title = self._x_label_text()
                        layer.axis(1).title = self._y_label_text()
                    except Exception:
                        try:
                            safe_x_label = self._x_label_text().replace('"', "'")
                            safe_y_label = self._y_label_text().replace('"', "'")
                            op.lt_exec(f'lab -xb "{safe_x_label}";')
                            op.lt_exec(f'lab -yl "{safe_y_label}";')
                        except Exception:
                            pass
                    try:
                        layer.add_legend()
                    except Exception:
                        try:
                            op.lt_exec('legend;')
                        except Exception:
                            pass
                    exported += 1
                exported_holder["count"] = exported

        ok = self.run_origin_export(
            ready=bool(self._dataset),
            missing_message="Load DMA iso-stress data before exporting to Origin.",
            task=_task,
            success_log=None,
            failure_message="Origin export failed",
            failure_log_prefix="Origin export failed",
        )
        if not ok:
            return
        exported = int(exported_holder.get("count", 0))
        if exported > 0:
            self._log(f"Sent {exported} DMA iso-stress graph(s) to Origin.")
        else:
            self._log("No DMA iso-stress graphs were exported to Origin.", level="error")

    @staticmethod
    def _origin_graph_name(label: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in label).strip("_")
        return (cleaned or "DMA_IsoStress")[:18]

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
    _FORMAT_GROUP_LABELS: Dict[str, str] = {
        "title": "Title text/visibility",
        "x_label": "X label text/visibility",
        "y_label": "Y label text/visibility",
        "line_style": "Line width + markers",
        "font": "Font sizes",
        "grid": "Grid visibility",
        "legend": "Legend visibility/location",
        "legend_labels": "Legend entry text",
        "ticks": "Tick spacing/count",
        "x_limits": "X limits (auto/manual)",
        "y_limits": "Y limits (auto/manual)",
    }
