from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from .widget import Strain3DPlotter, PlotConfig

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("Strain 3D Plot")
class Strain3DPlotPlugin(PyPlotPlugin):
    """Shared-native PyPlot integration for the Strain 3D plotter."""

    requires_imported_data = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._controller: Strain3DPlotter | None = None
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
        controller = self._ensure_controller()
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
        controls_layout.addWidget(QtWidgets.QLabel("Mode:"))
        controls_layout.addWidget(controller.mode_combo)
        controls_layout.addWidget(controller.auto_2d_check)
        controls_layout.addWidget(controller.auto_3d_check)
        controls_layout.addWidget(QtWidgets.QLabel("Manual dimension:"))
        controls_layout.addWidget(controller.manual_dimension_combo)
        controls_layout.addWidget(QtWidgets.QLabel("X axis:"))
        controls_layout.addWidget(controller.axis_x_combo)
        controls_layout.addWidget(QtWidgets.QLabel("Y axis:"))
        controls_layout.addWidget(controller.axis_y_combo)
        controls_layout.addWidget(QtWidgets.QLabel("Z axis:"))
        controls_layout.addWidget(controller.axis_z_combo)
        controls_layout.addStretch(1)
        layout.addWidget(controls_section)
        layout.addStretch(1)

        self._settings_widget = panel
        return panel

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Generate Strain Scatter Plots"

    def load_data(self) -> None:  # type: ignore[override]
        controller = self._ensure_controller()
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
        controller.input_edit.setText(str(selected[0]))
        self.host._commit_selected_paths(selected)  # type: ignore[attr-defined]
        self._log(f"Selected strain data source: {selected[0].name}")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        controller = self._ensure_controller()
        if not controller.input_edit.text().strip():
            self.load_data()
        if not controller.input_edit.text().strip():
            return
        self.clear_plot_tabs(self._plot_tabs)
        path = Path(controller.input_edit.text().strip())
        if not path.exists():
            raise RuntimeError(f"Missing worksheet: {path}")
        import pandas as pd

        df = pd.read_excel(path).dropna(how="all")
        if df.empty:
            raise RuntimeError("The worksheet does not contain any data.")

        columns = list(df.columns)
        mapping = controller._build_column_map(columns) if hasattr(controller, "_build_column_map") else None
        if mapping is None:
            from . import widget as widget_module

            mapping = widget_module._build_column_map(columns)
        from . import widget as widget_module

        composition_idx = mapping.get("composition") if mapping.get("composition") is not None else (0 if columns else None)
        microwire_idx = mapping.get("microwire") if mapping.get("microwire") is not None else (1 if len(columns) > 1 else None)
        strain_idx = mapping.get("strain")
        status_idx = mapping.get("status")
        if strain_idx is None:
            raise RuntimeError("Could not locate a strain column.")
        strain_label = widget_module._pretty_header(columns[strain_idx], strain_idx)
        numeric_columns = []
        for idx, header in enumerate(columns):
            if idx in {strain_idx, composition_idx, microwire_idx, status_idx}:
                continue
            label = widget_module._pretty_header(header, idx)
            lowered = label.lower()
            if "m length" in lowered or "a length" in lowered or "file" in lowered:
                continue
            numeric_columns.append((idx, label))
        records = []
        for row_index, row in df.iterrows():
            status_value = widget_module._clean_cell(row.iloc[status_idx]) if status_idx is not None else ""
            if status_value.lower().startswith("broke"):
                continue
            strain_value = widget_module._parse_strain_float(row.iloc[strain_idx])
            if strain_value is None:
                continue
            microwire_label = widget_module._clean_cell(row.iloc[microwire_idx]) if microwire_idx is not None else ""
            composition_label = widget_module._clean_cell(row.iloc[composition_idx]) if composition_idx is not None else ""
            record = {
                "Microwire": microwire_label or composition_label or f"Row {row_index}",
                "Composition": composition_label,
                strain_label: strain_value,
            }
            for col_idx, label in numeric_columns:
                record[label] = widget_module._parse_numeric(row.iloc[col_idx])
            for element, value in widget_module._extract_element_counts(composition_label).items():
                record[f"{element} (%)"] = value
            records.append(record)
        plot_df = pd.DataFrame(records)
        valid_labels = [
            label
            for label in plot_df.columns
            if label not in {"Microwire", "Composition"}
            and plot_df[label].notna().sum() >= 2
            and plot_df[label].dropna().nunique() >= 2
        ]
        if strain_label not in valid_labels:
            valid_labels.insert(0, strain_label)
        controller._refresh_axis_options(valid_labels, strain_label)
        configs = controller._build_plot_configs(valid_labels, strain_label)
        window_module = window_api()
        for config in configs:
            subset = plot_df[["Microwire", *config.labels]].dropna()
            if subset.empty:
                continue
            fig = Figure(figsize=(10.5, 7.6))
            ax = fig.add_subplot(111, projection="3d" if config.dimension == 3 else None)
            Strain3DPlotter._draw_scatter(ax, subset, config.labels)
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
        controller = self._ensure_controller()
        has_input = bool(controller.input_edit.text().strip())
        has_plots = bool(self._plot_tabs)
        if self._summary_label is not None:
            if has_input:
                self._summary_label.setText(
                    f"Ready to generate scatter plots from {Path(controller.input_edit.text()).name}."
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

    def _ensure_controller(self) -> Strain3DPlotter:
        controller = getattr(self, "_controller", None)
        if isinstance(controller, Strain3DPlotter):
            return controller
        controller = Strain3DPlotter()
        controller.hide()
        self._controller = controller
        return controller


__all__ = ["Strain3DPlotPlugin"]
