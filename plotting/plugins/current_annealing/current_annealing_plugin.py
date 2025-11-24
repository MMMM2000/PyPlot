from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from . import core as anneal_core
from plotting.shared.toolkit import format_annealing_title

if TYPE_CHECKING:
    from plotting.pyplot.window import (
        GraphLineState,
        WorksheetColumnMeta,
        WorksheetData,
        WorkbookData,
        TabDescriptor,
    )


@register_plugin("Current Annealing")
class CurrentAnnealingPlugin(PyPlotPlugin):
    """Embed current annealing plotting inside PyPlot."""

    requires_imported_data = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data_by_file: dict[str, pd.DataFrame] = {}
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
        self._origin_mode_combo: QtWidgets.QComboBox | None = None
        self._workbook_keys: dict[str, str] = {}
        self._panel_widget: QtWidgets.QWidget | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)
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
        overview_section, overview_layout = window_module.create_toolbar_section("Overview", parent=container)
        summary = QtWidgets.QLabel("Load current annealing files to plot traces or export to Origin.")
        summary.setWordWrap(True)
        overview_layout.addWidget(summary)
        overview_layout.addStretch(1)
        layout.addWidget(overview_section)
        layout.addStretch(1)
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

        origin_section, origin_layout = window_module.create_toolbar_section(
            "Origin export",
            parent=container,
            layout_factory=_form_layout,
        )
        origin_combo = QtWidgets.QComboBox(origin_section)
        for mode in anneal_core.ORIGIN_MODES:
            label = "Experimental (directional)" if mode == "experimental" else "Simple (single trace)"
            origin_combo.addItem(label, mode)
        index = origin_combo.findData(anneal_core.ORIGIN_MODE)
        origin_combo.setCurrentIndex(index if index >= 0 else 0)
        self._origin_mode_combo = origin_combo
        origin_layout.addRow("Mode:", origin_combo)
        layout.addWidget(origin_section)
        layout.addStretch(1)
        self._settings_widget = container
        return container

    def _apply_settings_to_core(self) -> None:
        if isinstance(self._origin_mode_combo, QtWidgets.QComboBox):
            mode = self._origin_mode_combo.currentData()
            if isinstance(mode, str) and mode:
                anneal_core.ORIGIN_MODE = mode
        anneal_core.SHOW_PLOTS = False
        anneal_core.BACKEND = "matplotlib"

    def load_data(self) -> None:  # type: ignore[override]
        host = self.host
        paths = host.ensure_data_selection(self)
        if not paths:
            return
        data_by_file: dict[str, pd.DataFrame] = {}
        errors: list[str] = []
        for path in paths:
            try:
                data = anneal_core.load_file(str(path))
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            data_by_file[str(path)] = data
        if errors:
            QtWidgets.QMessageBox.warning(self.host, self.name, "Some files could not be loaded:\n" + "\n".join(errors[:10]))
        if not data_by_file:
            self._data_by_file = {}
            self._loaded_files = []
            self.update_ui()
            return
        self._data_by_file = data_by_file
        self._loaded_files = list(data_by_file.keys())
        if paths:
            self.host._plugin_last_directories[self.name] = paths[0].parent
        self._log(f"Loaded {len(data_by_file)} current annealing file(s).")
        self._register_workbooks()
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._data_by_file:
            self.load_data()
        if not self._data_by_file:
            return
        window_module = window_api()
        self._apply_settings_to_core()
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(self._plot_tabs)
        else:
            for tab in self._plot_tabs:
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        self._plot_tabs.clear()
        plots_created = 0
        for path_str in sorted(self._data_by_file.keys()):
            df = self._data_by_file[path_str]
            title = format_annealing_title(Path(path_str).stem)
            try:
                fig, _ = anneal_core.plot_one(df, title)
            except Exception as exc:
                self._log(f"Failed to plot {Path(path_str).name}: {exc}", level="error")
                continue
            canvas = FigureCanvas(fig)
            canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            canvas.setMinimumSize(900, 560)
            tab_layout.addWidget(canvas)
            ax = fig.axes[0] if fig.axes else None
            lines: dict[tuple[str, float | str], GraphLineState] = {}
            if ax is not None:
                for index, line in enumerate(ax.get_lines(), start=1):
                    label = line.get_label() or f"Series {index}"
                    state = window_module.GraphLineState(
                        key=(label, float(index)),
                        label=label,
                        line=line,
                        base_x=line.get_xdata(),
                        base_y=line.get_ydata(),
                    )
                    lines[state.key] = state
            descriptor = window_module.TabDescriptor(
                kind="current_annealing",
                title=ax.get_title() if ax else title,
                root_label=Path(path_str).name,
                x_label=ax.get_xlabel() if ax else "Current (mA)",
                y_label=ax.get_ylabel() if ax else "Resistance",
                canvas=canvas,
                axes=ax,
                lines=lines,
                metadata={
                    "source_file": path_str,
                    "saved_path": "",
                    "origin_mode": anneal_core.ORIGIN_MODE,
                },
            )
            self.host.tab_widget.addTab(tab, Path(path_str).name)
            self.host._register_plot_tab(tab, canvas, ax, descriptor)
            self._plot_tabs.append(tab)
            plots_created += 1
        if self._plot_tabs:
            setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
            if callable(setter):
                index = self.host.tab_widget.indexOf(self._plot_tabs[0])
                if index >= 0:
                    setter(index)
        self._log(f"Generated {plots_created} current annealing plot(s).")
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._loaded_files:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Load current annealing data before exporting to Origin.",
            )
            return
        try:
            self._apply_settings_to_core()
            anneal_core.SHOW_PLOTS = False
            anneal_core.main(self._loaded_files, backend="origin")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self.host, self.name, f"Failed to export to Origin:\n{exc}")
            self._log(f"Origin export failed: {exc}", level="error")
        else:
            self._log("Sent current annealing plots to Origin.")

    def update_ui(self) -> None:
        has_data = bool(self._data_by_file)
        ready_to_plot = has_data or self._host_has_data_selection()
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(ready_to_plot)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(bool(self._plot_tabs))
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(False)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(has_data)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(bool(self._plot_tabs))
        self.host._update_project_actions()

    def _register_workbooks(self) -> None:
        host = self.host
        window_module = window_api()
        created: list[str] = []
        for path_str, df in self._data_by_file.items():
            path = Path(path_str)
            key = self._workbook_keys.get(path_str)
            if not key:
                key = f"annealing::{path_str}"
                self._workbook_keys[path_str] = key
            workbook = window_module.WorkbookData(
                key=key,
                name=f"{path.stem} (annealing)",
                worksheets=[],
                source=None,
                folder=None,
            )
            worksheet_objects: list[WorksheetData] = []
            for sheet_name, frame in self._split_by_direction(df):
                worksheet = host._create_worksheet_from_frame(workbook, sheet_name, frame)
                columns = worksheet.columns
                current_meta = columns.get("Current (mA)")
                if isinstance(current_meta, window_module.WorksheetColumnMeta):
                    current_meta.units = "mA"
                    current_meta.long_name = "Current"
                resistance_meta = columns.get("Resistance (Ω)")
                if isinstance(resistance_meta, window_module.WorksheetColumnMeta):
                    resistance_meta.units = "Ω"
                    resistance_meta.long_name = "Resistance"
                worksheet_objects.append(worksheet)
            if not worksheet_objects:
                continue
            workbook.worksheets = [ws.key for ws in worksheet_objects]
            host._register_imported_workbook(workbook, worksheet_objects)
            created.append(path.name)
        if created:
            host._refresh_imported_data_summary()
            host._sync_selected_paths_with_imports()
            self._log(
                "Created worksheets for: " + ", ".join(created),
            )

    def _split_by_direction(self, df: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
        if df.empty:
            return []
        if not {"I_mA", "R_Ohm"}.issubset(df.columns):
            renamed = df.rename(
                columns={"Current (mA)": "I_mA", "Resistance (Ω)": "R_Ohm"}
            )
        else:
            renamed = df
        currents = renamed["I_mA"].to_numpy(dtype=float)
        _, segments = anneal_core._direction_profile(currents)
        sheets: list[tuple[str, pd.DataFrame]] = []
        inc = 0
        dec = 0
        for start, end, direction in segments:
            if end <= start:
                continue
            segment = renamed.iloc[start:end].copy()
            segment = segment.rename(
                columns={"I_mA": "Current (mA)", "R_Ohm": "Resistance (Ω)"}
            )
            segment = segment.reset_index(drop=True)
            for column in ("Current (mA)", "Resistance (Ω)"):
                segment[column] = pd.to_numeric(segment[column], errors="coerce")
            segment = segment.dropna(subset=["Current (mA)", "Resistance (Ω)"]).reset_index(
                drop=True
            )
            if direction >= 0:
                inc += 1
                label = f"Increasing {inc}"
            else:
                dec += 1
                label = f"Decreasing {dec}"
            sheets.append((label, segment))
        if not sheets:
            fallback = renamed.rename(
                columns={"I_mA": "Current (mA)", "R_Ohm": "Resistance (Ω)"}
            )
            fallback = fallback.reset_index(drop=True)
            for column in ("Current (mA)", "Resistance (Ω)"):
                fallback[column] = pd.to_numeric(fallback[column], errors="coerce")
            fallback = fallback.dropna(subset=["Current (mA)", "Resistance (Ω)"]).reset_index(
                drop=True
            )
            sheets.append(("Samples", fallback))
        return sheets
