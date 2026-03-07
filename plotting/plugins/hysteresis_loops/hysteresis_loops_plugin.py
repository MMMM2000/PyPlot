from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins._window import window_api
from plotting.plugins.base import PyPlotPlugin, register_plugin

from . import core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState, TabDescriptor


@register_plugin("Hysteresis Loops")
class HysteresisLoopsPlugin(PyPlotPlugin):
    """Modern PyPlot integration for legacy hysteresis loop `.dat` / `.txt` files."""

    requires_imported_data = True
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._records: list[core.HysteresisLoopRecord] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._panel_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._mode_combo: QtWidgets.QComboBox | None = None
        self._settings = QtCore.QSettings("MicrowireLab", "HysteresisLoops")

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
        overview_section, overview_layout = window_module.create_toolbar_section(
            "Overview",
            parent=container,
        )
        summary = QtWidgets.QLabel(
            "Import hysteresis `.dat` or `.txt` files, then plot them with the shared PyPlot graph tools."
        )
        summary.setWordWrap(True)
        overview_layout.addWidget(summary)
        overview_layout.addStretch(1)

        layout.addWidget(overview_section)
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
        mode_combo.addItems(["Combined", "Separate", "Stacked"])
        stored_mode = self._settings.value("plot_mode", core.MODE)
        if isinstance(stored_mode, str):
            index = mode_combo.findText(stored_mode)
            if index >= 0:
                mode_combo.setCurrentIndex(index)
        mode_combo.currentTextChanged.connect(self._store_mode_preference)
        mode_layout.addRow("Mode:", mode_combo)

        note = QtWidgets.QLabel(
            "Combined and stacked plots are grouped by sample name inferred from the filename. "
            "Use shared Graph formatting for all styling.",
            mode_section,
        )
        note.setWordWrap(True)
        mode_layout.addRow(note)
        layout.addWidget(mode_section)

        layout.addStretch(1)
        self._mode_combo = mode_combo
        self._settings_widget = container
        return container

    def plot_action_label(self) -> str:  # type: ignore[override]
        return "Plot Hysteresis Loops"

    def has_loaded_data(self) -> bool:
        return bool(self._records)

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            selected = self._choose_files()
            if not selected:
                self.update_ui()
                return
            self.host._import_paths(selected)
            return

        try:
            records = core.load_records(paths)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to load hysteresis files:\n{exc}",
            )
            self._records = []
            self.update_ui()
            return

        self._records = records
        self._ensure_source_workbooks(records)
        self._log(f"Loaded {len(records)} hysteresis loop file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._records:
            self.load_data()
        if not self._records:
            return

        mode = self._plot_mode()
        self._clear_plot_tabs()
        grouped = core.group_records(self._records)

        if mode == "Separate":
            total = len(self._records)
        else:
            total = len(grouped)

        begin = getattr(self.host, "_begin_task_progress", None)
        update = getattr(self.host, "_update_task_progress", None)
        end = getattr(self.host, "_end_task_progress", None)
        if callable(begin):
            begin("Generating hysteresis plots...", maximum=max(1, total), value=0)

        try:
            completed = 0
            if mode == "Separate":
                for index, record in enumerate(core.sort_records(self._records), start=1):
                    title = f"{record.base_name} - {record.anneal_label}"
                    figure = core.separate_figures([record])[0]
                    self._register_figure_tab(
                        figure,
                        title=title,
                        root_label=record.path.name,
                        x_label=core.X_AXIS_LABEL,
                        y_label=core.Y_AXIS_LABEL,
                        kind="hysteresis_loops",
                        metadata={
                            "mode": mode,
                            "source_file": str(record.path),
                            "base_name": record.base_name,
                            "anneal_label": record.anneal_label,
                        },
                    )
                    completed = index
                    if callable(update):
                        update(
                            value=index,
                            maximum=max(1, total),
                            title=f"Generating {record.path.name} ({index}/{total})",
                        )
            else:
                builder = core.combined_figure if mode == "Combined" else core.stacked_figure
                for index, (base_name, records) in enumerate(grouped.items(), start=1):
                    figure = builder(records)
                    self._register_figure_tab(
                        figure,
                        title=base_name,
                        root_label=base_name,
                        x_label=core.X_AXIS_LABEL,
                        y_label=core.Y_AXIS_LABEL,
                        kind="hysteresis_loops",
                        metadata={
                            "mode": mode,
                            "base_name": base_name,
                            "source_files": [str(record.path) for record in records],
                        },
                    )
                    completed = index
                    if callable(update):
                        update(
                            value=index,
                            maximum=max(1, total),
                            title=f"Generating {base_name} ({index}/{total})",
                        )
            if self._plot_tabs:
                self.host.tab_widget.setCurrentWidget(self._plot_tabs[0])
            self._log(f"Generated {len(self._plot_tabs)} hysteresis plot(s).")
        finally:
            if callable(end):
                end()
        self.update_ui()

    def update_ui(self) -> None:  # type: ignore[override]
        has_selection = bool(self.host._selected_paths()) or bool(self.host.path_edit.text().strip())
        has_data = bool(self._records)
        has_plots = bool(self._plot_tabs)
        if self._summary_label is not None:
            if has_data:
                groups = len(core.group_records(self._records))
                self._summary_label.setText(
                    f"Loaded {len(self._records)} file(s) across {groups} sample group(s). "
                    "Use Plot Hysteresis Loops to regenerate graphs, then use shared Graph formatting for styling."
                )
            else:
                self._summary_label.setText(
                    "Import hysteresis `.dat` or `.txt` files, then plot them with the shared PyPlot graph tools."
                )
        self.apply_shared_action_state(
            can_plot=has_selection or has_data,
            can_export_txt=has_plots,
            can_open_origin=has_plots,
            update_project_actions=False,
        )
        update_save = getattr(self.host, "_update_save_graph_enabled", None)
        if callable(update_save):
            update_save()
        update_normalize = getattr(self.host, "_update_normalize_enabled", None)
        if callable(update_normalize):
            update_normalize()
        update_project_actions = getattr(self.host, "_update_project_actions", None)
        if callable(update_project_actions):
            update_project_actions()

    def serialize_project_state(self, *, base_path: Path | None) -> dict[str, object] | None:  # type: ignore[override]
        _ = base_path
        return {"plot_mode": self._plot_mode()}

    def restore_project_state(self, state: dict[str, object], *, project_dir: Path) -> None:  # type: ignore[override]
        _ = project_dir
        if not isinstance(state, dict):
            return
        mode = state.get("plot_mode")
        if isinstance(mode, str):
            self.settings_widget()
            if isinstance(self._mode_combo, QtWidgets.QComboBox):
                combo_index = self._mode_combo.findText(mode)
                if combo_index >= 0:
                    self._mode_combo.setCurrentIndex(combo_index)

    def _plot_mode(self) -> str:
        combo = self._mode_combo
        if isinstance(combo, QtWidgets.QComboBox):
            text = combo.currentText().strip()
            if text:
                return text
        return "Combined"

    def _store_mode_preference(self, mode: str) -> None:
        if isinstance(mode, str) and mode.strip():
            self._settings.setValue("plot_mode", mode.strip())

    def _choose_files(self) -> list[Path]:
        start_dir = self.preferred_import_directory()
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self.host,
            "Select hysteresis loop files",
            str(start_dir),
            "Hysteresis files (*.dat *.txt);;All files (*)",
        )
        if not files:
            return []
        paths = [Path(path) for path in files]
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        return paths

    def _clear_plot_tabs(self) -> None:
        self.clear_plot_tabs(self._plot_tabs)

    def _ensure_source_workbooks(
        self,
        records: list[core.HysteresisLoopRecord],
    ) -> None:
        host = self.host
        window_module = window_api()
        existing_sources: set[str] = set()
        for workbook in host._workbooks.values():
            source = getattr(workbook, "source", None)
            if isinstance(source, Path):
                try:
                    existing_sources.add(str(source.resolve()))
                except Exception:
                    existing_sources.add(str(source))

        for record in records:
            try:
                source_key = str(record.path.resolve())
            except Exception:
                source_key = str(record.path)
            if source_key in existing_sources:
                continue
            frame = pd.DataFrame(
                {
                    core.X_AXIS_LABEL: record.x,
                    core.Y_AXIS_LABEL: record.y,
                }
            )
            workbook_key = ("hysteresis_loops_raw", source_key)
            worksheet = window_module.WorksheetData(
                key=("hysteresis_loops_raw_sheet", source_key),
                name=record.path.name,
                dataframe=frame,
                columns={
                    core.X_AXIS_LABEL: window_module.WorksheetColumnMeta(
                        long_name="Magnetic field",
                        units="A/m",
                        comments=record.anneal_label,
                    ),
                    core.Y_AXIS_LABEL: window_module.WorksheetColumnMeta(
                        long_name="Flux",
                        units="Wb",
                        comments=record.anneal_label,
                    ),
                },
                source=record.path,
                workbook_key=workbook_key,
                axis_roles="XY",
            )
            workbook = window_module.WorkbookData(
                key=workbook_key,
                name=record.path.name,
                source=record.path,
                folder=record.path.parent,
                worksheets=[worksheet.key],
            )
            host._register_imported_workbook(workbook, [worksheet])
            existing_sources.add(source_key)

    def _register_figure_tab(
        self,
        figure: object,
        *,
        title: str,
        root_label: str,
        x_label: str,
        y_label: str,
        kind: str,
        metadata: dict[str, object],
    ) -> None:
        window_module = window_api()
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
        axes = figure.axes[0] if getattr(figure, "axes", None) else None
        line_states: dict[tuple[str, float | str], GraphLineState] = {}
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
                line_states[state.key] = state
        descriptor = window_module.TabDescriptor(
            kind=kind,
            title=title,
            root_label=root_label,
            x_label=x_label,
            y_label=y_label,
            canvas=canvas,
            axes=axes,
            lines=line_states,
            metadata=metadata,
        )
        self.host.tab_widget.addTab(tab, root_label)
        self.host._register_plot_tab(tab, canvas, axes, descriptor)
        self._plot_tabs.append(tab)


__all__ = ["HysteresisLoopsPlugin"]
