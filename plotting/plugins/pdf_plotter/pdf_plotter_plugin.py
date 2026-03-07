from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from . import dialog as pdf_dialog

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("PDF Plotter")
class PdfPlotterPlugin(PyPlotPlugin):
    """Shared-native PyPlot wrapper for the PDF plotter workflow."""

    exposes_load_data = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._controller: pdf_dialog.PdfPlotterWindow | None = None
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None

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
        controller = self._ensure_controller()
        # Reuse the controller controls as the settings surface, but do not embed
        # the legacy top-level window in the plugin panel.
        panel = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        window_module = window_api()

        overview_section, overview_layout = window_module.create_toolbar_section("PDF settings", parent=panel)
        note = QtWidgets.QLabel(
            "These controls are backed by the PDF plotter engine but rendered through the shared PyPlot shell."
        )
        note.setWordWrap(True)
        overview_layout.addWidget(note)
        layout.addWidget(overview_section)

        buttons_section, buttons_layout = window_module.create_toolbar_section("Quick actions", parent=panel)
        choose_btn = QtWidgets.QPushButton("Choose PDF files…", buttons_section)
        choose_btn.clicked.connect(self.load_data)
        buttons_layout.addWidget(choose_btn)
        buttons_layout.addStretch(1)
        layout.addWidget(buttons_section)

        settings_map = [
            controller.x_combo,
            controller.mode_combo,
            controller.zero_cb,
            controller.line_style,
            controller.marker_style,
            controller.line_width,
            controller.marker_size,
            controller.grid_cb,
            controller.legend_cb,
            controller.legend_loc,
            controller.legend_fs,
            controller.title_fs,
            controller.label_fs,
            controller.tick_fs,
        ]
        group = QtWidgets.QGroupBox("Plot settings", panel)
        form = QtWidgets.QFormLayout(group)
        form.setContentsMargins(8, 8, 8, 8)
        form.addRow("X axis:", controller.x_combo)
        form.addRow("Mode:", controller.mode_combo)
        form.addRow(controller.zero_cb)
        form.addRow("Line style:", controller.line_style)
        form.addRow("Marker:", controller.marker_style)
        form.addRow("Line width:", controller.line_width)
        form.addRow("Marker size:", controller.marker_size)
        form.addRow(controller.grid_cb)
        form.addRow(controller.legend_cb)
        form.addRow("Legend location:", controller.legend_loc)
        form.addRow("Legend font:", controller.legend_fs)
        form.addRow("Title size:", controller.title_fs)
        form.addRow("Label size:", controller.label_fs)
        form.addRow("Tick size:", controller.tick_fs)
        layout.addWidget(group)
        layout.addStretch(1)
        self._settings_widget = panel
        return panel

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot PDF Data"

    def load_data(self) -> None:  # type: ignore[override]
        controller = self._ensure_controller()
        selected = [path for path in self.host._selected_paths() if path.is_file()]  # type: ignore[attr-defined]
        pdf_paths = [path for path in selected if path.suffix.lower() == ".pdf"]
        if not pdf_paths:
            start_dir = self.preferred_import_directory()
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self.host,
                "Select PDF files",
                str(start_dir),
                "PDF files (*.pdf);;All files (*)",
            )
            if not files:
                return
            pdf_paths = [Path(path) for path in files]
        controller.files = [str(path) for path in pdf_paths]
        controller._reload_selected_files(show_feedback=False)
        self.host._commit_selected_paths(pdf_paths)  # type: ignore[attr-defined]
        self._log(f"Loaded {len(pdf_paths)} PDF file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        controller = self._ensure_controller()
        if not controller.files:
            self.load_data()
        if not controller.files:
            return
        self.clear_plot_tabs(self._plot_tabs)
        x_choice = controller.x_combo.currentText()
        targets = ["Force (N)", "Strain (mm)"] if x_choice == "Force & Strain" else [x_choice]
        window_module = window_api()

        for x_name in targets:
            lines_by_file = controller._collect_lines_by_file(x_name)
            if not lines_by_file:
                continue
            selected = [cb.text() for cb in controller.y_checks if cb.isChecked()]
            if not selected:
                selected = ["T1+T2"]
            y_label = f"{' / '.join(selected)} (arb. u.)"
            if controller.mode_combo.currentText() == "Combined":
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
                self._register_figure_tab(
                    controller._create_matplotlib_fig(lines, title, x_name, y_label),
                    root_label=title,
                    window_module=window_module,
                )
            else:
                for path, sets in lines_by_file.items():
                    base = Path(path).stem
                    lines = [(f"{base} {y_name}", xs, ys) for y_name, xs, ys in sets]
                    title = f"{y_label} vs {x_name} - {base}"
                    self._register_figure_tab(
                        controller._create_matplotlib_fig(lines, title, x_name, y_label),
                        root_label=base,
                        window_module=window_module,
                    )
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {len(self._plot_tabs)} PDF plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        controller = self._ensure_controller()

        def _task() -> None:
            if not controller.files:
                raise RuntimeError("Load PDF files before exporting.")
            x_choice = controller.x_combo.currentText()
            targets = ["Force (N)", "Strain (mm)"] if x_choice == "Force & Strain" else [x_choice]
            for x_name in targets:
                lines_by_file = controller._collect_lines_by_file(x_name)
                if not lines_by_file:
                    continue
                selected = [cb.text() for cb in controller.y_checks if cb.isChecked()]
                if not selected:
                    selected = ["T1+T2"]
                y_label = f"{' / '.join(selected)} (arb. u.)"
                if controller.mode_combo.currentText() == "Combined":
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
                    controller._plot_to_origin(lines, title, x_name, y_label)
                else:
                    for path, sets in lines_by_file.items():
                        base = Path(path).stem
                        lines = [(f"{base} {y_name}", xs, ys) for y_name, xs, ys in sets]
                        title = f"{y_label} vs {x_name} - {base}"
                        controller._plot_to_origin(lines, title, x_name, y_label)

        self.run_origin_export(
            ready=bool(controller.files),
            missing_message="Load PDF files before exporting to Origin.",
            task=_task,
            success_log="Sent PDF plots to Origin.",
        )

    def update_ui(self) -> None:  # type: ignore[override]
        controller = self._ensure_controller()
        has_data = bool(controller.data)
        has_plots = bool(self._plot_tabs)
        if self._summary_label is not None:
            if has_data:
                self._summary_label.setText(
                    f"Loaded {len(controller.files)} PDF file(s). Plot to generate shared PyPlot graph tabs."
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

    def _ensure_controller(self) -> pdf_dialog.PdfPlotterWindow:
        controller = getattr(self, "_controller", None)
        if isinstance(controller, pdf_dialog.PdfPlotterWindow):
            return controller
        controller = pdf_dialog.PdfPlotterWindow()
        controller.hide()
        self._controller = controller
        return controller

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
