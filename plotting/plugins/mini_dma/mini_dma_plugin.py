from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from . import core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("Mini DMA")
class MiniDmaPlugin(PyPlotPlugin):
    """Plot Mini DMA logger output inside PyPlot."""

    requires_imported_data = True
    supports_import_folders = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._runs: list[core.MiniDmaRun] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._strain_baseline_combo: QtWidgets.QComboBox | None = None
        self._show_power_top_axis_checkbox: QtWidgets.QCheckBox | None = None
        self._power_axis_mode_combo: QtWidgets.QComboBox | None = None

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel(
            "Import Mini DMA run folders or measurement.csv files, then plot current-sweep and iso-current graphs."
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
        layout.setSpacing(10)

        window_module = window_api()
        section, section_layout = window_module.create_toolbar_section(
            "Mini DMA plots",
            parent=container,
        )
        note = QtWidgets.QLabel(
            "Creates current-sweep graphs by target MPa, plus iso-current stress-strain graphs by current."
        )
        note.setWordWrap(True)
        section_layout.addWidget(note)
        baseline_label = QtWidgets.QLabel("Strain baseline:")
        section_layout.addWidget(baseline_label)
        baseline_combo = QtWidgets.QComboBox()
        baseline_combo.setToolTip(
            "Choose how Mini DMA recalculates the strain-current Y axis. "
            "Global minimum uses one shared l0 from all target-stress curves; "
            "each target minimum gives every stress plateau its own zero."
        )
        baseline_combo.addItem(
            "Global minimum length",
            core.STRAIN_BASELINE_GLOBAL_MINIMUM,
        )
        baseline_combo.addItem(
            "Each target minimum",
            core.STRAIN_BASELINE_PER_TARGET_MINIMUM,
        )
        baseline_combo.addItem("Measured strain", core.STRAIN_BASELINE_RAW)
        section_layout.addWidget(baseline_combo)
        self._strain_baseline_combo = baseline_combo
        show_power = QtWidgets.QCheckBox("Show power top axis")
        show_power.setToolTip(
            "Add a top X axis with P = I^2R labels calculated from the current-sweep curves."
        )
        show_power.setChecked(True)
        section_layout.addWidget(show_power)
        self._show_power_top_axis_checkbox = show_power
        power_axis_label = QtWidgets.QLabel("Power axis:")
        section_layout.addWidget(power_axis_label)
        power_axis_combo = QtWidgets.QComboBox()
        power_axis_combo.setToolTip(
            "Choose whether the top power axis shows absolute electrical power "
            "or power normalized by the Mini DMA initial wire length."
        )
        power_axis_combo.addItem(
            "Normalized by length (mW/cm)",
            core.POWER_AXIS_NORMALIZED_MW_PER_CM,
        )
        power_axis_combo.addItem("Absolute power (mW)", core.POWER_AXIS_ABSOLUTE_MW)
        section_layout.addWidget(power_axis_combo)
        self._power_axis_mode_combo = power_axis_combo
        layout.addWidget(section)
        layout.addStretch(1)

        self._settings_widget = container
        return container

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot Mini DMA"

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return

        measurement_paths = core.iter_measurement_paths(Path(path) for path in paths)
        candidate_paths = measurement_paths or [Path(path) for path in paths]

        runs: list[core.MiniDmaRun] = []
        failures: list[str] = []
        for path in candidate_paths:
            try:
                run = core.load_run(path)
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")
                continue
            runs.append(run)

        self._runs = runs
        self._data = runs
        if runs:
            self.host._plugin_last_directories[self.name] = runs[0].path
        if self._summary_label is not None:
            if runs:
                self._summary_label.setText(
                    f"Loaded {len(runs)} Mini DMA run(s). Click Plot to build Mini DMA graphs."
                )
            else:
                self._summary_label.setText(
                    "No compatible Mini DMA runs loaded. Select run folders or measurement.csv files."
                )
        if failures:
            short = "\n".join(failures[:8])
            suffix = "\n..." if len(failures) > 8 else ""
            self._log(f"Some Mini DMA inputs were skipped:\n{short}{suffix}", level="error")
        self._log(f"Loaded {len(runs)} Mini DMA run(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._runs:
            self.load_data()
        if not self._runs:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load one or more Mini DMA run folders or measurement.csv files before plotting.",
            )
            return

        self.clear_plot_tabs(self._plot_tabs)
        plots_created = 0
        strain_baseline_mode = self._strain_baseline_mode()
        for run in self._runs:
            if core.is_iso_current_run(run):
                plot_specs = (("iso_current", core.make_iso_current_figure),)
            else:
                plot_specs = (
                    (
                        "strain_current",
                        lambda current_run: core.make_strain_current_figure(
                            current_run,
                            strain_baseline_mode=strain_baseline_mode,
                            show_power_top_axis=self._show_power_top_axis_enabled(),
                            power_axis_mode=self._power_axis_mode(),
                        ),
                    ),
                    (
                        "resistance_current",
                        lambda current_run: core.make_resistance_current_figure(
                            current_run,
                            show_power_top_axis=self._show_power_top_axis_enabled(),
                            power_axis_mode=self._power_axis_mode(),
                        ),
                    ),
                )
            for plot_kind, figure_factory in plot_specs:
                try:
                    figure = figure_factory(run)
                except Exception as exc:
                    self._log(f"Failed to plot {run.path.name}: {exc}", level="error")
                    continue
                self._create_plot_tab(run, figure, plot_kind=plot_kind)
                plots_created += 1
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {plots_created} Mini DMA plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._plot_tabs and self._runs:
            self.generate()
        if not self._plot_tabs:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Generate at least one Mini DMA graph before exporting to Origin.",
            )
            return
        super().open_origin()

    def update_ui(self) -> None:
        has_data = bool(self._runs)
        self.apply_shared_action_state(
            can_plot=True,
            can_save_graph=bool(self._plot_tabs),
            can_normalize=False,
            can_export_txt=False,
            can_open_origin=has_data or bool(self._plot_tabs),
            can_popout=bool(self._plot_tabs),
        )

    def graph_option_defaults(self) -> dict[str, float] | None:  # type: ignore[override]
        return {
            "title_font": 14,
            "label_font": 11,
            "tick_font": 9,
            "figure_width": 8.0,
            "figure_height": 5.0,
        }

    def _create_plot_tab(
        self,
        run: core.MiniDmaRun,
        figure: Figure,
        *,
        plot_kind: str,
    ) -> None:
        canvas = FigureCanvas(figure)
        canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        canvas.setMinimumSize(0, 0)
        canvas.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)

        axes = figure.axes[0] if figure.axes else None
        title = axes.get_title() if axes is not None else run.sample_name
        x_label = axes.get_xlabel() if axes is not None else ""
        y_label = axes.get_ylabel() if axes is not None else ""
        lines: dict[tuple[str, float | str], GraphLineState] = {}
        window_module = window_api()
        if axes is not None:
            for index, line in enumerate(axes.get_lines(), start=1):
                label = line.get_label() or f"Series {index}"
                state = window_module.GraphLineState(
                    key=(plot_kind, label),
                    label=label,
                    line=line,
                    base_x=line.get_xdata(),
                    base_y=line.get_ydata(),
                )
                lines[state.key] = state

        power_axis_current_mA: list[float] = []
        power_axis_resistance_ohm: list[float] = []
        power_axis_label = "Power [mW]"
        power_axis_scale = 1.0
        if self._show_power_top_axis_enabled() and plot_kind in {
            "strain_current",
            "resistance_current",
        }:
            power_axis_current_mA, power_axis_resistance_ohm = core.power_axis_points(run)
            power_axis_label, power_axis_scale = core.power_axis_label_and_scale(
                run,
                self._power_axis_mode(),
            )

        descriptor = window_module.TabDescriptor(
            kind="mini_dma",
            title=title,
            root_label=run.sample_name,
            x_label=x_label,
            y_label=y_label,
            canvas=canvas,
            axes=axes,
            lines=lines,
            metadata={
                "plugin": self.name,
                "source_file": str(run.measurement_path),
                "plot_kind": plot_kind,
                "sample_name": run.sample_name,
                "zero_minimum_strain": (
                    self._strain_baseline_mode() != core.STRAIN_BASELINE_RAW
                    if plot_kind == "strain_current"
                    else False
                ),
                "strain_baseline_mode": (
                    self._strain_baseline_mode()
                    if plot_kind == "strain_current"
                    else core.STRAIN_BASELINE_RAW
                ),
                "show_power_top_axis": (
                    self._show_power_top_axis_enabled()
                    if plot_kind in {"strain_current", "resistance_current"}
                    else False
                ),
                "power_axis_current_mA": power_axis_current_mA,
                "power_axis_resistance_ohm": power_axis_resistance_ohm,
                "power_axis_label": power_axis_label,
                "power_axis_scale": power_axis_scale,
                "origin_legend_position": (
                    "inside_upper_right"
                    if plot_kind == "strain_current"
                    else "inside_upper_left"
                ),
                "origin_layer_width": 54.0,
            },
        )
        suffix = {
            "strain_current": "Strain",
            "resistance_current": "Resistance",
            "iso_current": "Iso-current",
        }.get(plot_kind, "Graph")
        tab_label = f"{run.sample_name} - {suffix}"
        self.host.tab_widget.addTab(tab, tab_label)
        self.host._register_plot_tab(tab, canvas, axes, descriptor)
        self._plot_tabs.append(tab)

    def _show_power_top_axis_enabled(self) -> bool:
        checkbox = self._show_power_top_axis_checkbox
        return bool(checkbox is not None and checkbox.isChecked())

    def _power_axis_mode(self) -> str:
        combo = self._power_axis_mode_combo
        if combo is None:
            self.settings_widget()
            combo = self._power_axis_mode_combo
        if combo is not None:
            value = combo.currentData()
            if isinstance(value, str) and value in core.POWER_AXIS_MODES:
                return value
        return core.POWER_AXIS_NORMALIZED_MW_PER_CM

    def _strain_baseline_mode(self) -> str:
        combo = self._strain_baseline_combo
        if combo is None:
            self.settings_widget()
            combo = self._strain_baseline_combo
        if combo is not None:
            value = combo.currentData()
            if isinstance(value, str) and value in core.STRAIN_BASELINE_MODES:
                return value
        return core.STRAIN_BASELINE_GLOBAL_MINIMUM

    def _set_show_power_top_axis(self, enabled: bool) -> None:
        if self._show_power_top_axis_checkbox is None:
            self.settings_widget()
        if self._show_power_top_axis_checkbox is not None:
            self._show_power_top_axis_checkbox.setChecked(bool(enabled))

    def _set_power_axis_mode(self, mode: str) -> None:
        if self._power_axis_mode_combo is None:
            self.settings_widget()
        combo = self._power_axis_mode_combo
        if combo is None:
            return
        index = combo.findData(mode)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_zero_minimum_strain(self, enabled: bool) -> None:
        self._set_strain_baseline_mode(
            core.STRAIN_BASELINE_PER_TARGET_MINIMUM
            if enabled
            else core.STRAIN_BASELINE_RAW
        )

    def _set_strain_baseline_mode(self, mode: str) -> None:
        if self._strain_baseline_combo is None:
            self.settings_widget()
        combo = self._strain_baseline_combo
        if combo is None:
            return
        index = combo.findData(mode)
        if index >= 0:
            combo.setCurrentIndex(index)
