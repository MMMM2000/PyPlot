from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api

from . import core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("Mini DMA")
class MiniDmaPlugin(PyPlotPlugin):
    """Plot Mini DMA current-sweep logger output inside PyPlot."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._runs: list[core.MiniDmaRun] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel(
            "Import Mini DMA run folders or measurement.csv files, then plot current sweeps by target MPa."
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
            "Creates strain-current and resistance-current graphs. Each curve is one target MPa plateau."
        )
        note.setWordWrap(True)
        section_layout.addWidget(note)
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
                    f"Loaded {len(runs)} Mini DMA run(s). Click Plot to build current-sweep graphs."
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
        for run in self._runs:
            for plot_kind, figure_factory in (
                ("strain_current", core.make_strain_current_figure),
                ("resistance_current", core.make_resistance_current_figure),
            ):
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

        descriptor = window_module.TabDescriptor(
            kind="mini_dma",
            title=title,
            root_label=run.sample_name,
            x_label="Measured current [mA]",
            y_label=y_label,
            canvas=canvas,
            axes=axes,
            lines=lines,
            metadata={
                "plugin": self.name,
                "source_file": str(run.measurement_path),
                "plot_kind": plot_kind,
                "sample_name": run.sample_name,
            },
        )
        suffix = "Strain" if plot_kind == "strain_current" else "Resistance"
        tab_label = f"{run.sample_name} - {suffix}"
        self.host.tab_widget.addTab(tab, tab_label)
        self.host._register_plot_tab(tab, canvas, axes, descriptor)
        self._plot_tabs.append(tab)
