from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api

from . import core

LAYOUT_SEPARATE_TABS = "separate_tabs"
LAYOUT_DUAL_AXIS = "dual_axis_overlay"
LAYOUT_MODE_SETTINGS_KEY = "shape_memory_stress_strain/layout_mode"


@dataclass
class ShapeMemoryEntry:
    path: Path
    frame: pd.DataFrame


@register_plugin("Shape Memory Stress/Strain")
class ShapeMemoryStressStrainPlugin(PyPlotPlugin):
    """Plot segmented loading/unloading loops from manual stress/strain logs."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._dataset: list[ShapeMemoryEntry] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._summary_label: QtWidgets.QLabel | None = None
        self._layout_mode_combo: QtWidgets.QComboBox | None = None
        self._seg_tolerance_spin: QtWidgets.QDoubleSpinBox | None = None

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        summary = QtWidgets.QLabel(
            "Import manual stress/strain logger TXT files and plot segmented loading/unloading loops."
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

        def _form_layout(parent: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
            form = QtWidgets.QFormLayout(parent)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)
            form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            return form

        section, form = window_module.create_toolbar_section(
            "Loop segmentation",
            parent=container,
            layout_factory=_form_layout,
        )
        layout_mode_combo = QtWidgets.QComboBox(section)
        layout_mode_combo.addItem("Separate tabs", LAYOUT_SEPARATE_TABS)
        layout_mode_combo.addItem("Dual-axis overlay (one graph)", LAYOUT_DUAL_AXIS)
        stored_mode = self._stored_layout_mode()
        stored_index = layout_mode_combo.findData(stored_mode)
        if stored_index >= 0:
            layout_mode_combo.setCurrentIndex(stored_index)
        layout_mode_combo.currentIndexChanged.connect(self._persist_layout_mode_setting)
        self._layout_mode_combo = layout_mode_combo
        form.addRow("Graph layout:", layout_mode_combo)

        tolerance_spin = QtWidgets.QDoubleSpinBox(section)
        tolerance_spin.setDecimals(8)
        tolerance_spin.setRange(0.0, 1.0)
        tolerance_spin.setSingleStep(0.0001)
        tolerance_spin.setValue(core.STRAIN_DIRECTION_TOLERANCE)
        tolerance_spin.setToolTip(
            "Minimum |delta strain| required to mark a loading/unloading direction change."
        )
        self._seg_tolerance_spin = tolerance_spin
        form.addRow("Direction tolerance:", tolerance_spin)

        hint = QtWidgets.QLabel(
            "Segments are labeled automatically as Loading 1, Unloading 1, Loading 2, ..."
        )
        hint.setWordWrap(True)
        form.addRow(hint)

        layout.addWidget(section)
        layout.addStretch(1)
        self._settings_widget = container
        return container

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot Shape Memory Stress/Strain"

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return

        entries: list[ShapeMemoryEntry] = []
        failures: list[str] = []
        for path in paths:
            try:
                frame = core.load_manual_stress_strain_file(Path(path))
            except Exception as exc:
                failures.append(f"{Path(path).name}: {exc}")
                continue
            if frame.empty:
                failures.append(f"{Path(path).name}: file has no usable rows.")
                continue
            entries.append(ShapeMemoryEntry(path=Path(path), frame=frame))

        self._dataset = entries
        self._data = entries

        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent

        if self._summary_label is not None:
            if entries:
                self._summary_label.setText(
                    f"Loaded {len(entries)} shape-memory file(s). Click Plot to build segmented curves."
                )
            else:
                self._summary_label.setText(
                    "No compatible files loaded. Use manual stress/strain logger TXT exports."
                )

        if failures:
            short = "\n".join(failures[:8])
            suffix = "\n..." if len(failures) > 8 else ""
            self._log(f"Some files were skipped:\n{short}{suffix}", level="error")
        self._log(f"Loaded {len(entries)} shape-memory file(s).")
        self.update_ui()

    def _segmentation_tolerance(self) -> float:
        if isinstance(self._seg_tolerance_spin, QtWidgets.QDoubleSpinBox):
            return float(self._seg_tolerance_spin.value())
        return core.STRAIN_DIRECTION_TOLERANCE

    def _plot_layout_mode(self) -> str:
        if isinstance(self._layout_mode_combo, QtWidgets.QComboBox):
            mode = self._layout_mode_combo.currentData()
            if isinstance(mode, str):
                return mode
        return self._stored_layout_mode()

    def _stored_layout_mode(self) -> str:
        settings = getattr(self.host, "settings", None)
        if isinstance(settings, QtCore.QSettings):
            stored = settings.value(LAYOUT_MODE_SETTINGS_KEY, LAYOUT_SEPARATE_TABS)
            if isinstance(stored, str) and stored in {LAYOUT_SEPARATE_TABS, LAYOUT_DUAL_AXIS}:
                return stored
        return LAYOUT_SEPARATE_TABS

    def _persist_layout_mode_setting(self, *_: object) -> None:
        mode = self._plot_layout_mode()
        settings = getattr(self.host, "settings", None)
        if isinstance(settings, QtCore.QSettings):
            settings.setValue(LAYOUT_MODE_SETTINGS_KEY, mode)
            settings.sync()

    def _clear_tabs(self) -> None:
        self.clear_plot_tabs(self._plot_tabs)

    def _set_tab_bar_visible(self, visible: bool) -> None:
        tab_bar = self.host.tab_widget.tabBar()
        if tab_bar is not None:
            tab_bar.setVisible(visible)

    def generate(self) -> None:  # type: ignore[override]
        if not self._dataset:
            self.load_data()
        if not self._dataset:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load one or more manual stress/strain TXT files before plotting.",
            )
            return

        self._clear_tabs()
        tolerance = self._segmentation_tolerance()
        layout_mode = self._plot_layout_mode()
        window_module = window_api()
        plots_created = 0

        for entry in self._dataset:
            if layout_mode == LAYOUT_DUAL_AXIS:
                try:
                    figure = core.make_dual_axis_overlay_figure(
                        entry.frame,
                        title=entry.path.stem,
                        tolerance=tolerance,
                    )
                except Exception as exc:
                    self._log(
                        f"Failed to plot dual-axis overlay for {entry.path.name}: {exc}",
                        level="error",
                    )
                    continue

                canvas = FigureCanvas(figure)
                canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)

                tab = QtWidgets.QWidget()
                tab_layout = QtWidgets.QVBoxLayout(tab)
                tab_layout.setContentsMargins(0, 0, 0, 0)
                tab_layout.addWidget(canvas)

                axes = figure.axes[0] if figure.axes else None
                descriptor = window_module.TabDescriptor(
                    kind="shape_memory_dual_axis_overlay",
                    title=f"{entry.path.stem} - Dual-axis overlay",
                    root_label=f"{entry.path.stem} Dual-axis overlay",
                    x_label="Displacement (mm) / Strain (%)",
                    y_label="Load (g) / Stress (MPa)",
                    canvas=canvas,
                    axes=axes,
                    lines={},
                    metadata={
                        "path": str(entry.path),
                        "plot_kind": "shape_memory_dual_axis_overlay",
                        "layout_mode": layout_mode,
                        "tolerance": tolerance,
                        "segments": len(
                            core.split_segments_by_strain_direction(
                                entry.frame["strain_pct"].tolist(),
                                tolerance=tolerance,
                            )
                        ),
                    },
                )

                tab_label = f"{entry.path.stem} - Dual-axis overlay"
                index = self.host.tab_widget.addTab(tab, tab_label)
                self.host.tab_widget.setCurrentIndex(index)
                self.host._register_plot_tab(tab, canvas, axes, descriptor)
                self._plot_tabs.append(tab)
                plots_created += 1
                continue

            plot_builders = (
                (
                    "shape_memory_load_displacement",
                    "Load vs Displacement",
                    core.make_load_displacement_figure,
                ),
                (
                    "shape_memory_stress_strain",
                    "Stress vs Strain",
                    core.make_stress_strain_figure,
                ),
            )
            for plot_kind, label, builder in plot_builders:
                try:
                    figure = builder(
                        entry.frame,
                        title=entry.path.stem,
                        tolerance=tolerance,
                    )
                except Exception as exc:
                    self._log(
                        f"Failed to plot {label} for {entry.path.name}: {exc}",
                        level="error",
                    )
                    continue

                canvas = FigureCanvas(figure)
                canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)

                tab = QtWidgets.QWidget()
                tab_layout = QtWidgets.QVBoxLayout(tab)
                tab_layout.setContentsMargins(0, 0, 0, 0)
                tab_layout.addWidget(canvas)

                axes = figure.axes[0] if figure.axes else None
                descriptor = window_module.TabDescriptor(
                    kind=plot_kind,
                    title=f"{entry.path.stem} - {label}",
                    root_label=f"{entry.path.stem} {label}",
                    x_label=axes.get_xlabel() if axes is not None else "",
                    y_label=axes.get_ylabel() if axes is not None else "",
                    canvas=canvas,
                    axes=axes,
                    lines={},
                    metadata={
                        "path": str(entry.path),
                        "plot_kind": plot_kind,
                        "layout_mode": layout_mode,
                        "tolerance": tolerance,
                        "segments": len(
                            core.split_segments_by_strain_direction(
                                entry.frame["strain_pct"].tolist(),
                                tolerance=tolerance,
                            )
                        ),
                    },
                )

                tab_label = f"{entry.path.stem} - {label}"
                index = self.host.tab_widget.addTab(tab, tab_label)
                self.host.tab_widget.setCurrentIndex(index)
                self.host._register_plot_tab(tab, canvas, axes, descriptor)
                self._plot_tabs.append(tab)
                plots_created += 1

        self._set_tab_bar_visible(len(self._plot_tabs) > 1)
        self._log(f"Generated {plots_created} shape-memory graph(s).")
        self.update_ui()

    def update_ui(self) -> None:  # type: ignore[override]
        count = len(self._dataset)
        if self._summary_label is not None and count == 0:
            self._summary_label.setText(
                "Import manual stress/strain logger TXT files and plot segmented loading/unloading loops."
            )
        has_tabs = any(self.host.tab_widget.indexOf(tab) >= 0 for tab in self._plot_tabs)
        self.apply_shared_action_state(
            can_plot=count > 0,
            can_save_graph=has_tabs,
            can_normalize=has_tabs,
            can_export_txt=has_tabs,
            can_open_origin=has_tabs,
            can_popout=has_tabs,
        )
        save_sync = getattr(self.host, "_update_save_graph_enabled", None)
        if callable(save_sync):
            save_sync()
