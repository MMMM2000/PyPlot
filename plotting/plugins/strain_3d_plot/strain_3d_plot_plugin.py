from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from . import core as strain_core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("Strain 3D Plot")
class Strain3DPlotPlugin(PyPlotPlugin):
    """Shared-native PyPlot integration for the Strain 3D plotter."""

    requires_imported_data = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._mode_combo: QtWidgets.QComboBox | None = None
        self._auto_2d_check: QtWidgets.QCheckBox | None = None
        self._auto_3d_check: QtWidgets.QCheckBox | None = None
        self._manual_dimension_combo: QtWidgets.QComboBox | None = None
        self._axis_x_combo: QtWidgets.QComboBox | None = None
        self._axis_y_combo: QtWidgets.QComboBox | None = None
        self._axis_z_combo: QtWidgets.QComboBox | None = None
        self._selected_input: Path | None = None

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
            "Import a worksheet or database export and generate 2D/3D scatter combinations inside the shared PyPlot shell."
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
        panel = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        window_module = window_api()

        usage_section, usage_layout = window_module.create_toolbar_section("Usage", parent=panel)
        label = QtWidgets.QLabel(
            "Configure automatic or manual scatter combinations here. Generated plots appear as shared PyPlot graph tabs."
        )
        label.setWordWrap(True)
        usage_layout.addWidget(label)
        layout.addWidget(usage_section)

        source_section, source_layout = window_module.create_toolbar_section("Source", parent=panel)
        browse_btn = QtWidgets.QPushButton("Choose worksheet…", source_section)
        browse_btn.clicked.connect(self.load_data)
        source_layout.addWidget(browse_btn)
        source_layout.addStretch(1)
        layout.addWidget(source_section)

        controls_section, controls_layout = window_module.create_toolbar_section("Plot options", parent=panel)
        mode_combo = QtWidgets.QComboBox(controls_section)
        mode_combo.addItems(["Automatic combinations", "Manual axes"])
        auto_2d_check = QtWidgets.QCheckBox("Generate 2D plots", controls_section)
        auto_3d_check = QtWidgets.QCheckBox("Generate 3D plots", controls_section)
        auto_3d_check.setChecked(True)
        manual_dimension_combo = QtWidgets.QComboBox(controls_section)
        manual_dimension_combo.addItems(["2D", "3D"])
        axis_x_combo = QtWidgets.QComboBox(controls_section)
        axis_y_combo = QtWidgets.QComboBox(controls_section)
        axis_z_combo = QtWidgets.QComboBox(controls_section)
        controls_layout.addWidget(QtWidgets.QLabel("Mode:"))
        controls_layout.addWidget(mode_combo)
        controls_layout.addWidget(auto_2d_check)
        controls_layout.addWidget(auto_3d_check)
        controls_layout.addWidget(QtWidgets.QLabel("Manual dimension:"))
        controls_layout.addWidget(manual_dimension_combo)
        controls_layout.addWidget(QtWidgets.QLabel("X axis:"))
        controls_layout.addWidget(axis_x_combo)
        controls_layout.addWidget(QtWidgets.QLabel("Y axis:"))
        controls_layout.addWidget(axis_y_combo)
        controls_layout.addWidget(QtWidgets.QLabel("Z axis:"))
        controls_layout.addWidget(axis_z_combo)
        controls_layout.addStretch(1)
        layout.addWidget(controls_section)
        layout.addStretch(1)

        self._mode_combo = mode_combo
        self._auto_2d_check = auto_2d_check
        self._auto_3d_check = auto_3d_check
        self._manual_dimension_combo = manual_dimension_combo
        self._axis_x_combo = axis_x_combo
        self._axis_y_combo = axis_y_combo
        self._axis_z_combo = axis_z_combo

        self._settings_widget = panel
        return panel

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Generate Strain Scatter Plots"

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        selected = [path for path in paths if path.suffix.lower() in {".xlsx", ".xls", ".xlsm"}]
        if not selected:
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                self.host,
                "Select worksheet or database",
                str(self.preferred_import_directory()),
                "Excel files (*.xlsx *.xlsm *.xls)",
            )
            if not filename:
                return
            selected = [Path(filename)]
        self._selected_input = selected[0]
        self.host._commit_selected_paths(selected)  # type: ignore[attr-defined]
        self._log(f"Selected strain data source: {selected[0].name}")
        try:
            _plot_df, strain_label, valid_labels = strain_core.build_plot_dataframe(self._selected_input)
        except Exception:
            valid_labels = []
            strain_label = "Strain (%)"
        for combo in (self._axis_x_combo, self._axis_y_combo, self._axis_z_combo):
            if combo is None:
                continue
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(valid_labels)
            if current and combo.findText(current) >= 0:
                combo.setCurrentText(current)
            combo.blockSignals(False)
        if self._axis_x_combo is not None and not self._axis_x_combo.currentText():
            self._axis_x_combo.setCurrentText(strain_label)
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if self._selected_input is None:
            self.load_data()
        if self._selected_input is None:
            return
        self.clear_plot_tabs(self._plot_tabs)
        plot_df, strain_label, valid_labels = strain_core.build_plot_dataframe(self._selected_input)
        configs = strain_core.build_plot_configs(
            valid_labels=valid_labels,
            strain_label=strain_label,
            automatic=(self._mode_combo.currentText() != "Manual axes") if self._mode_combo is not None else True,
            include_2d=bool(self._auto_2d_check.isChecked()) if self._auto_2d_check is not None else False,
            include_3d=bool(self._auto_3d_check.isChecked()) if self._auto_3d_check is not None else True,
            manual_dimension=3 if self._manual_dimension_combo is not None and self._manual_dimension_combo.currentText() == "3D" else 2,
            manual_labels=(
                self._axis_x_combo.currentText() if self._axis_x_combo is not None else "",
                self._axis_y_combo.currentText() if self._axis_y_combo is not None else "",
                self._axis_z_combo.currentText() if self._axis_z_combo is not None else None,
            ),
        )
        window_module = window_api()
        for config in configs:
            subset = plot_df[["Microwire", *config.labels]].dropna()
            if subset.empty:
                continue
            fig = Figure(figsize=(10.5, 7.6))
            ax = fig.add_subplot(111, projection="3d" if config.dimension == 3 else None)
            strain_core.draw_scatter(ax, subset, config.labels)
            fig.tight_layout()
            canvas = FigureCanvas(fig)
            canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            title = " vs ".join(config.labels)
            descriptor = window_module.TabDescriptor(
                kind="strain_3d_plot",
                title=title,
                root_label=title,
                x_label=config.labels[0],
                y_label=config.labels[1] if len(config.labels) > 1 else "",
                canvas=canvas,
                axes=ax,
                lines={},
                metadata={"dimension": config.dimension, "labels": list(config.labels)},
            )
            self.host.tab_widget.addTab(tab, title)
            self.host._register_plot_tab(tab, canvas, ax, descriptor)
            self._plot_tabs.append(tab)
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {len(self._plot_tabs)} strain scatter plot(s).")
        self.update_ui()

    def update_ui(self) -> None:  # type: ignore[override]
        has_input = self._selected_input is not None
        has_plots = bool(self._plot_tabs)
        if self._summary_label is not None:
            if has_input:
                self._summary_label.setText(
                    f"Ready to generate scatter plots from {self._selected_input.name}."
                )
            else:
                self._summary_label.setText(
                    "Import a worksheet or database export and generate 2D/3D scatter combinations inside the shared PyPlot shell."
                )
        self.apply_shared_action_state(
            can_plot=has_input or bool(self.host._selected_paths()),
            can_export_txt=has_plots,
            can_open_origin=has_plots,
            update_project_actions=False,
        )
        updater = getattr(self.host, "_update_project_actions", None)
        if callable(updater):
            updater()


__all__ = ["Strain3DPlotPlugin"]
