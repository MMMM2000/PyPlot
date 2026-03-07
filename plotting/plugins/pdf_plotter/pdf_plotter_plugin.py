from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from . import core as pdf_core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("PDF Plotter")
class PdfPlotterPlugin(PyPlotPlugin):
    """Shared-native PyPlot wrapper for PDF plotting without the legacy dialog."""

    exposes_load_data = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data: list[tuple[str, list[pdf_core.NumberRow]]] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._y_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._x_combo: QtWidgets.QComboBox | None = None
        self._mode_combo: QtWidgets.QComboBox | None = None
        self._zero_cb: QtWidgets.QCheckBox | None = None
        self._line_style: QtWidgets.QComboBox | None = None
        self._marker_style: QtWidgets.QComboBox | None = None
        self._line_width: QtWidgets.QDoubleSpinBox | None = None
        self._marker_size: QtWidgets.QDoubleSpinBox | None = None
        self._grid_cb: QtWidgets.QCheckBox | None = None
        self._legend_cb: QtWidgets.QCheckBox | None = None
        self._legend_loc: QtWidgets.QComboBox | None = None
        self._legend_fs: QtWidgets.QSpinBox | None = None
        self._title_fs: QtWidgets.QSpinBox | None = None
        self._label_fs: QtWidgets.QSpinBox | None = None
        self._tick_fs: QtWidgets.QSpinBox | None = None

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
        section, section_layout = window_module.create_toolbar_section("Overview", parent=container)
        summary = QtWidgets.QLabel(
            "Select PDF files, then generate shared PyPlot graphs for T1/T2, force, and strain relationships."
        )
        summary.setWordWrap(True)
        section_layout.addWidget(summary)
        section_layout.addStretch(1)
        layout.addWidget(section)
        layout.addStretch(1)
        self._summary_label = summary
        self._panel_widget = container
        return container

    def settings_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        window_module = window_api()

        vars_section, vars_layout = window_module.create_toolbar_section("Y variables", parent=container)
        for name in ["T1+T2", "T1", "T2", "T2-T1"]:
            checkbox = QtWidgets.QCheckBox(name, vars_section)
            checkbox.setChecked(name == "T1+T2")
            self._y_checks[name] = checkbox
            vars_layout.addWidget(checkbox)
        vars_layout.addStretch(1)
        layout.addWidget(vars_section)

        opts_section, opts_layout = window_module.create_toolbar_section("Plot options", parent=container)
        x_combo = QtWidgets.QComboBox(opts_section)
        x_combo.addItems(["Force (N)", "Strain (mm)", "Force & Strain"])
        mode_combo = QtWidgets.QComboBox(opts_section)
        mode_combo.addItems(["Combined", "Separate"])
        zero_cb = QtWidgets.QCheckBox("First point at zero", opts_section)
        line_style = QtWidgets.QComboBox(opts_section)
        line_style.addItems(["-", "--", ":", "-.", "None"])
        marker_style = QtWidgets.QComboBox(opts_section)
        marker_style.addItems(["o", "s", "d", "^", "v", "x", "+", ".", "None"])
        line_width = QtWidgets.QDoubleSpinBox(opts_section)
        line_width.setRange(0.1, 10.0)
        line_width.setValue(1.5)
        marker_size = QtWidgets.QDoubleSpinBox(opts_section)
        marker_size.setRange(0.5, 30.0)
        marker_size.setValue(5.0)
        grid_cb = QtWidgets.QCheckBox("Show grid", opts_section)
        grid_cb.setChecked(True)
        legend_cb = QtWidgets.QCheckBox("Show legend", opts_section)
        legend_cb.setChecked(True)
        legend_loc = QtWidgets.QComboBox(opts_section)
        legend_loc.addItems(["best", "upper right", "upper left", "lower left", "lower right", "center"])
        legend_fs = QtWidgets.QSpinBox(opts_section)
        legend_fs.setRange(6, 48)
        legend_fs.setValue(10)
        title_fs = QtWidgets.QSpinBox(opts_section)
        title_fs.setRange(6, 72)
        title_fs.setValue(12)
        label_fs = QtWidgets.QSpinBox(opts_section)
        label_fs.setRange(6, 72)
        label_fs.setValue(11)
        tick_fs = QtWidgets.QSpinBox(opts_section)
        tick_fs.setRange(6, 48)
        tick_fs.setValue(10)
        for label, widget in (
            ("X axis:", x_combo),
            ("Mode:", mode_combo),
            ("", zero_cb),
            ("Line style:", line_style),
            ("Marker:", marker_style),
            ("Line width:", line_width),
            ("Marker size:", marker_size),
            ("", grid_cb),
            ("", legend_cb),
            ("Legend location:", legend_loc),
            ("Legend font:", legend_fs),
            ("Title size:", title_fs),
            ("Label size:", label_fs),
            ("Tick size:", tick_fs),
        ):
            if label:
                opts_layout.addWidget(QtWidgets.QLabel(label, opts_section))
            opts_layout.addWidget(widget)
        opts_layout.addStretch(1)
        self._x_combo = x_combo
        self._mode_combo = mode_combo
        self._zero_cb = zero_cb
        self._line_style = line_style
        self._marker_style = marker_style
        self._line_width = line_width
        self._marker_size = marker_size
        self._grid_cb = grid_cb
        self._legend_cb = legend_cb
        self._legend_loc = legend_loc
        self._legend_fs = legend_fs
        self._title_fs = title_fs
        self._label_fs = label_fs
        self._tick_fs = tick_fs
        layout.addWidget(opts_section)
        layout.addStretch(1)
        self._settings_widget = container
        return container

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot PDF Data"

    def load_data(self) -> None:  # type: ignore[override]
        selected = [path for path in self.host._selected_paths() if path.is_file()]  # type: ignore[attr-defined]
        pdf_paths = [path for path in selected if path.suffix.lower() == ".pdf"]
        if not pdf_paths:
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self.host,
                "Select PDF files",
                str(self.preferred_import_directory()),
                "PDF files (*.pdf);;All files (*)",
            )
            if not files:
                return
            pdf_paths = [Path(path) for path in files]
        self._data = pdf_core.load_pdf_data(pdf_paths)
        self.host._commit_selected_paths(pdf_paths)  # type: ignore[attr-defined]
        self._log(f"Loaded {len(pdf_paths)} PDF file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._data:
            self.load_data()
        if not self._data:
            return
        self.clear_plot_tabs(self._plot_tabs)
        x_choice = self._x_combo.currentText() if self._x_combo is not None else "Force (N)"
        targets = ["Force (N)", "Strain (mm)"] if x_choice == "Force & Strain" else [x_choice]
        selected = [name for name, checkbox in self._y_checks.items() if checkbox.isChecked()]
        if not selected:
            selected = ["T1+T2"]
        style = self._style()
        window_module = window_api()
        for x_name in targets:
            lines_by_file = pdf_core.collect_lines_by_file(
                self._data,
                x_name=x_name,
                selected_vars=selected,
                zero_first=bool(self._zero_cb.isChecked()) if self._zero_cb is not None else False,
            )
            if not lines_by_file:
                continue
            y_label = f"{' / '.join(selected)} (arb. u.)"
            if self._mode_combo is not None and self._mode_combo.currentText() == "Separate":
                for path, sets in lines_by_file.items():
                    base = Path(path).stem
                    lines = [(f"{base} {y_name}", xs, ys) for y_name, xs, ys in sets]
                    fig = pdf_core.create_matplotlib_figure(lines, title=f"{y_label} vs {x_name} - {base}", x_label=x_name, y_label=y_label, style=style)
                    self._register_figure_tab(fig, root_label=base, window_module=window_module)
            else:
                lines = []
                for path, sets in lines_by_file.items():
                    base = Path(path).stem
                    for y_name, xs, ys in sets:
                        label = f"{base} {y_name}" if len(lines_by_file) > 1 else y_name
                        lines.append((label, xs, ys))
                title = (
                    f"{y_label} vs {x_name} - {Path(next(iter(lines_by_file))).stem}"
                    if len(lines_by_file) == 1
                    else f"{y_label} vs {x_name} - {len(lines_by_file)} files"
                )
                fig = pdf_core.create_matplotlib_figure(lines, title=title, x_label=x_name, y_label=y_label, style=style)
                self._register_figure_tab(fig, root_label=title, window_module=window_module)
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {len(self._plot_tabs)} PDF plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        def _task() -> None:
            if not self._data:
                raise RuntimeError("Load PDF files before exporting.")
            x_choice = self._x_combo.currentText() if self._x_combo is not None else "Force (N)"
            targets = ["Force (N)", "Strain (mm)"] if x_choice == "Force & Strain" else [x_choice]
            selected = [name for name, checkbox in self._y_checks.items() if checkbox.isChecked()]
            if not selected:
                selected = ["T1+T2"]
            for x_name in targets:
                lines_by_file = pdf_core.collect_lines_by_file(
                    self._data,
                    x_name=x_name,
                    selected_vars=selected,
                    zero_first=bool(self._zero_cb.isChecked()) if self._zero_cb is not None else False,
                )
                if not lines_by_file:
                    continue
                y_label = f"{' / '.join(selected)} (arb. u.)"
                if self._mode_combo is not None and self._mode_combo.currentText() == "Separate":
                    for path, sets in lines_by_file.items():
                        base = Path(path).stem
                        lines = [(f"{base} {y_name}", xs, ys) for y_name, xs, ys in sets]
                        pdf_core.plot_lines_to_origin(lines, title=f"{y_label} vs {x_name} - {base}", x_label=x_name, y_label=y_label)
                else:
                    lines = []
                    for path, sets in lines_by_file.items():
                        base = Path(path).stem
                        for y_name, xs, ys in sets:
                            label = f"{base} {y_name}" if len(lines_by_file) > 1 else y_name
                            lines.append((label, xs, ys))
                    title = (
                        f"{y_label} vs {x_name} - {Path(next(iter(lines_by_file))).stem}"
                        if len(lines_by_file) == 1
                        else f"{y_label} vs {x_name} - {len(lines_by_file)} files"
                    )
                    pdf_core.plot_lines_to_origin(lines, title=title, x_label=x_name, y_label=y_label)

        self.run_origin_export(
            ready=bool(self._data),
            missing_message="Load PDF files before exporting to Origin.",
            task=_task,
            success_log="Sent PDF plots to Origin.",
        )

    def update_ui(self) -> None:  # type: ignore[override]
        has_data = bool(self._data)
        has_plots = bool(self._plot_tabs)
        if self._summary_label is not None:
            if has_data:
                self._summary_label.setText(
                    f"Loaded {len(self._data)} PDF file(s). Plot to generate shared PyPlot graph tabs."
                )
            else:
                self._summary_label.setText(
                    "Select PDF files, then generate shared PyPlot graphs for T1/T2, force, and strain relationships."
                )
        self.apply_shared_action_state(
            can_plot=has_data or bool(self.host._selected_paths()),
            can_export_txt=has_plots,
            can_open_origin=has_plots,
            update_project_actions=False,
        )
        updater = getattr(self.host, "_update_project_actions", None)
        if callable(updater):
            updater()

    def _style(self) -> pdf_core.PdfPlotStyle:
        return pdf_core.PdfPlotStyle(
            line_style=self._line_style.currentText() if self._line_style is not None else "-",
            marker_style=self._marker_style.currentText() if self._marker_style is not None else "o",
            line_width=float(self._line_width.value()) if self._line_width is not None else 1.5,
            marker_size=float(self._marker_size.value()) if self._marker_size is not None else 5.0,
            show_grid=bool(self._grid_cb.isChecked()) if self._grid_cb is not None else True,
            show_legend=bool(self._legend_cb.isChecked()) if self._legend_cb is not None else True,
            legend_loc=self._legend_loc.currentText() if self._legend_loc is not None else "best",
            legend_font_size=int(self._legend_fs.value()) if self._legend_fs is not None else 10,
            title_font_size=int(self._title_fs.value()) if self._title_fs is not None else 12,
            label_font_size=int(self._label_fs.value()) if self._label_fs is not None else 11,
            tick_font_size=int(self._tick_fs.value()) if self._tick_fs is not None else 10,
        )

    def _register_figure_tab(self, fig, *, root_label: str, window_module) -> None:
        canvas = FigureCanvas(fig)
        canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        tab = QtWidgets.QWidget()
        tab_layout = QtWidgets.QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(canvas)
        axes = fig.axes[0] if fig.axes else None
        lines: dict[tuple[str, float | str], GraphLineState] = {}
        if axes is not None:
            for index, line in enumerate(axes.get_lines(), start=1):
                label = line.get_label() or f"Series {index}"
                state = window_module.GraphLineState(
                    key=(label, float(index)),
                    label=label,
                    line=line,
                    base_x=line.get_xdata(),
                    base_y=line.get_ydata(),
                )
                lines[state.key] = state
        title = axes.get_title() if axes is not None else root_label
        descriptor = window_module.TabDescriptor(
            kind="pdf_plotter",
            title=title,
            root_label=root_label,
            x_label=axes.get_xlabel() if axes is not None else "",
            y_label=axes.get_ylabel() if axes is not None else "",
            canvas=canvas,
            axes=axes,
            lines=lines,
            metadata={"plugin": "PDF Plotter"},
        )
        self.host.tab_widget.addTab(tab, root_label)
        self.host._register_plot_tab(tab, canvas, axes, descriptor)
        self._plot_tabs.append(tab)


__all__ = ["PdfPlotterPlugin"]
