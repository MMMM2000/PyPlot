from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from . import core as maxion_core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState


@register_plugin("Maxion Continuous")
class MaxionContinuousPlugin(PyPlotPlugin):
    """Shared-native PyPlot wrapper for the Maxion continuous workflow."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data_by_file: dict[str, pd.DataFrame] = {}
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._mode_combo: QtWidgets.QComboBox | None = None
        self._show_raw_cb: QtWidgets.QCheckBox | None = None
        self._show_processed_cb: QtWidgets.QCheckBox | None = None
        self._median_spin: QtWidgets.QSpinBox | None = None
        self._moving_avg_spin: QtWidgets.QSpinBox | None = None
        self._center_cb: QtWidgets.QCheckBox | None = None
        self._center_source_combo: QtWidgets.QComboBox | None = None

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
            "Import Maxion continuous files, then plot channel sums with the shared PyPlot graph tools."
        )
        summary.setWordWrap(True)
        section_layout.addWidget(summary)
        section_layout.addStretch(1)
        layout.addWidget(section)
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

        def _form_layout(parent: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
            form = QtWidgets.QFormLayout(parent)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)
            form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            return form

        mode_section, mode_layout = window_module.create_toolbar_section(
            "Plot mode",
            parent=container,
            layout_factory=_form_layout,
        )
        mode_combo = QtWidgets.QComboBox(mode_section)
        mode_combo.addItems(["Raw", "Processed", "Both"])
        mode_combo.setCurrentIndex({"raw": 0, "processed": 1, "both": 2}.get(maxion_core.PLOT_MODE, 2))
        mode_layout.addRow("Mode:", mode_combo)
        self._mode_combo = mode_combo
        layout.addWidget(mode_section)

        proc_section, proc_layout = window_module.create_toolbar_section(
            "Processing",
            parent=container,
            layout_factory=_form_layout,
        )
        median_spin = QtWidgets.QSpinBox(proc_section)
        median_spin.setRange(1, 9999)
        median_spin.setValue(int(maxion_core.MED_WINDOW))
        moving_avg_spin = QtWidgets.QSpinBox(proc_section)
        moving_avg_spin.setRange(1, 9999)
        moving_avg_spin.setValue(int(maxion_core.MA_WINDOW))
        center_cb = QtWidgets.QCheckBox("Center median at zero", proc_section)
        center_cb.setChecked(bool(maxion_core.CENTER_MEDIAN_Y))
        center_source_combo = QtWidgets.QComboBox(proc_section)
        center_source_combo.addItems(["raw", "processed"])
        center_source_combo.setCurrentText(str(maxion_core.CENTER_MEDIAN_SOURCE))
        center_source_combo.setEnabled(center_cb.isChecked())
        center_cb.toggled.connect(center_source_combo.setEnabled)
        proc_layout.addRow("Median window:", median_spin)
        proc_layout.addRow("Moving average window:", moving_avg_spin)
        proc_layout.addRow(center_cb)
        proc_layout.addRow("Center source:", center_source_combo)
        self._median_spin = median_spin
        self._moving_avg_spin = moving_avg_spin
        self._center_cb = center_cb
        self._center_source_combo = center_source_combo
        layout.addWidget(proc_section)
        layout.addStretch(1)

        self._settings_widget = container
        return container

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot Maxion Continuous"

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        data_by_file: dict[str, pd.DataFrame] = {}
        valid_paths: list[Path] = []
        for path in paths:
            try:
                maxion_core.parse_name(path.stem)
                data = maxion_core.load_file(str(path))
            except Exception:
                continue
            data_by_file[str(path)] = data
            valid_paths.append(path)
        if not data_by_file:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "None of the selected files matched the Maxion continuous naming pattern.",
            )
            self._data_by_file = {}
            self.update_ui()
            return
        self._data_by_file = data_by_file
        self.host._commit_selected_paths(valid_paths)  # type: ignore[attr-defined]
        self._log(f"Loaded {len(valid_paths)} Maxion continuous file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._data_by_file:
            self.load_data()
        if not self._data_by_file:
            return
        self._apply_settings_to_core()
        self.clear_plot_tabs(self._plot_tabs)
        window_module = window_api()

        for file_path in sorted(self._data_by_file.keys()):
            df = self._data_by_file[file_path]
            head, coils = maxion_core.parse_name(Path(file_path).stem)
            for channel in (1, 2, 3):
                series = df[f"ch{channel}_t1"] + df[f"ch{channel}_t2"]
                fig, _name = maxion_core.plot_channel(series, head, coils, channel)
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
                title = axes.get_title() if axes is not None else f"{Path(file_path).name} CH{channel}"
                descriptor = window_module.TabDescriptor(
                    kind="maxion_continuous",
                    title=title,
                    root_label=f"{Path(file_path).name} CH{channel}",
                    x_label=axes.get_xlabel() if axes is not None else "Sample index",
                    y_label=axes.get_ylabel() if axes is not None else "T1+T2 (arb. u.)",
                    canvas=canvas,
                    axes=axes,
                    lines=lines,
                    metadata={"source_file": file_path, "channel": channel},
                )
                self.host.tab_widget.addTab(tab, descriptor.root_label)
                self.host._register_plot_tab(tab, canvas, axes, descriptor)
                self._plot_tabs.append(tab)
        if self._plot_tabs:
            self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
        self._log(f"Generated {len(self._plot_tabs)} Maxion plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        def _task() -> None:
            self._apply_settings_to_core()
            maxion_core.main(list(self._data_by_file.keys()), backend="origin")

        self.run_origin_export(
            ready=bool(self._data_by_file),
            missing_message="Load Maxion continuous data before exporting to Origin.",
            task=_task,
            success_log="Sent Maxion continuous plots to Origin.",
        )

    def update_ui(self) -> None:  # type: ignore[override]
        has_data = bool(self._data_by_file)
        has_plots = bool(self._plot_tabs)
        if self._summary_label is not None:
            if has_data:
                self._summary_label.setText(
                    f"Loaded {len(self._data_by_file)} Maxion file(s). Plot to generate shared PyPlot graph tabs."
                )
            else:
                self._summary_label.setText(
                    "Import Maxion continuous files, then plot channel sums with the shared PyPlot graph tools."
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

    def _apply_settings_to_core(self) -> None:
        if self._mode_combo is not None:
            maxion_core.PLOT_MODE = {
                0: "raw",
                1: "processed",
                2: "both",
            }.get(self._mode_combo.currentIndex(), "both")
        if self._median_spin is not None:
            maxion_core.MED_WINDOW = int(self._median_spin.value())
        if self._moving_avg_spin is not None:
            maxion_core.MA_WINDOW = int(self._moving_avg_spin.value())
        if self._center_cb is not None:
            maxion_core.CENTER_MEDIAN_Y = bool(self._center_cb.isChecked())
        if self._center_source_combo is not None:
            maxion_core.CENTER_MEDIAN_SOURCE = str(self._center_source_combo.currentText())
        maxion_core.SHOW_PLOTS = False
        maxion_core.SAVE_PLOTS = False
        maxion_core.BACKEND = "matplotlib"


__all__ = ["MaxionContinuousPlugin"]
