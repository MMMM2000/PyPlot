from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    auto_load_on_import = True

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._data_by_file: dict[str, pd.DataFrame] = {}
        self._loaded_files: list[str] = []
        self._plot_tabs: list[QtWidgets.QWidget] = []
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
        origin_note = QtWidgets.QLabel(
            "Uses shared PyPlot Origin export (direction-separated traces, shared title placement).",
            origin_section,
        )
        origin_note.setWordWrap(True)
        origin_layout.addRow(origin_note)
        layout.addWidget(origin_section)
        layout.addStretch(1)
        self._settings_widget = container
        return container

    def _apply_settings_to_core(self) -> None:
        anneal_core.SHOW_PLOTS = False
        anneal_core.BACKEND = "matplotlib"

    def _portable_path(self, path: Path | None, base_path: Path | None) -> str | None:
        helper = getattr(self.host, "_portable_path", None)
        if callable(helper):
            try:
                value = helper(path, base_path)
            except Exception:
                value = None
            if isinstance(value, str):
                return value
        if path is None:
            return None
        return str(path)

    def _resolve_portable_path(self, entry: Any, project_dir: Path) -> Path | None:
        helper = getattr(self.host, "_resolve_portable_path", None)
        if callable(helper):
            try:
                value = helper(entry, project_dir)
            except Exception:
                value = None
            if isinstance(value, Path):
                return value
        if not isinstance(entry, str) or not entry.strip():
            return None
        path = Path(entry)
        if not path.is_absolute():
            path = project_dir / path
        return path

    def _loaded_source_key_for_path(self, path: Path) -> str | None:
        try:
            target = path.resolve()
        except Exception:
            target = path
        for source in self._data_by_file.keys():
            source_path = Path(source)
            try:
                source_resolved = source_path.resolve()
            except Exception:
                source_resolved = source_path
            if source_resolved == target:
                return source
        return None

    def _clear_plot_tabs(self) -> None:
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(self._plot_tabs)
        else:
            for tab in self._plot_tabs:
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        self._plot_tabs.clear()

    def _load_data_from_paths(
        self,
        paths: list[Path],
        *,
        show_errors: bool,
    ) -> bool:
        data_by_file: dict[str, pd.DataFrame] = {}
        errors: list[str] = []
        resolved_paths: list[Path] = []
        for path in paths:
            if not isinstance(path, Path):
                continue
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            if not resolved.exists() or not resolved.is_file():
                errors.append(f"{resolved.name}: file not found")
                continue
            try:
                data = anneal_core.load_file(str(resolved))
            except Exception as exc:
                errors.append(f"{resolved.name}: {exc}")
                continue
            data_by_file[str(resolved)] = data
            resolved_paths.append(resolved)
        if errors and show_errors:
            QtWidgets.QMessageBox.warning(
                self.host,
                self.name,
                "Some files could not be loaded:\n" + "\n".join(errors[:10]),
            )
        if not data_by_file:
            self._data_by_file = {}
            self._loaded_files = []
            self._data = None
            return False
        self._data_by_file = data_by_file
        self._loaded_files = list(data_by_file.keys())
        self._data = data_by_file
        if resolved_paths:
            self.host._plugin_last_directories[self.name] = resolved_paths[0].parent
        self._register_workbooks()
        return True

    def _create_plot_tab(self, path_str: str, df: pd.DataFrame) -> QtWidgets.QWidget | None:
        window_module = window_api()
        title = format_annealing_title(Path(path_str).stem)
        try:
            fig, _ = anneal_core.plot_one(df, title)
        except Exception as exc:
            self._log(f"Failed to plot {Path(path_str).name}: {exc}", level="error")
            return None
        canvas = FigureCanvas(fig)
        canvas.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        canvas.setMinimumSize(0, 0)
        canvas.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        tab = QtWidgets.QWidget()
        tab_layout = QtWidgets.QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
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
            x_label=ax.get_xlabel() if ax else "Current [mA]",
            y_label=ax.get_ylabel() if ax else "Resistance [Ω]",
            canvas=canvas,
            axes=ax,
            lines=lines,
            metadata={
                "source_file": path_str,
                "saved_path": "",
            },
        )
        self.host.tab_widget.addTab(tab, Path(path_str).name)
        self.host._register_plot_tab(tab, canvas, ax, descriptor)
        self._plot_tabs.append(tab)
        return tab

    def load_data(self) -> None:  # type: ignore[override]
        host = self.host
        paths = host.ensure_data_selection(self)
        if not paths:
            return
        loaded = self._load_data_from_paths(list(paths), show_errors=True)
        if not loaded:
            self.update_ui()
            return
        self._log(f"Loaded {len(self._data_by_file)} current annealing file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._data_by_file:
            self.load_data()
        if not self._data_by_file:
            return
        self._apply_settings_to_core()
        self._clear_plot_tabs()
        plots_created = 0
        paths_sorted = sorted(self._data_by_file.keys())
        total_paths = len(paths_sorted)
        begin_shared_progress = getattr(self.host, "_begin_task_progress", None)
        update_shared_progress = getattr(self.host, "_update_task_progress", None)
        end_shared_progress = getattr(self.host, "_end_task_progress", None)
        if callable(begin_shared_progress):
            begin_shared_progress(
                "Generating current annealing plots...",
                maximum=max(1, total_paths),
                value=0,
            )
        completed = 0
        progress_interval = 1 if total_paths <= 25 else max(1, total_paths // 100)
        detail_interval = 1 if total_paths <= 40 else max(1, total_paths // 24)
        event_interval = 1 if total_paths <= 20 else max(2, total_paths // 48)
        suspend_tree_updates = getattr(self.host, "_suspend_project_tree_updates", None)
        tree_context = suspend_tree_updates() if callable(suspend_tree_updates) else nullcontext(None)
        tab_updates_prev: bool | None = None
        try:
            tab_widget = getattr(self.host, "tab_widget", None)
            if isinstance(tab_widget, QtWidgets.QWidget):
                tab_updates_prev = tab_widget.updatesEnabled()
                tab_widget.setUpdatesEnabled(False)
            with tree_context:
                for idx, path_str in enumerate(paths_sorted, start=1):
                    df = self._data_by_file[path_str]
                    tab = self._create_plot_tab(path_str, df)
                    completed = idx
                    if tab is not None:
                        plots_created += 1
                    if callable(update_shared_progress):
                        should_update = (
                            idx == total_paths
                            or idx <= 2
                            or idx % progress_interval == 0
                        )
                        if should_update:
                            title: str | None = None
                            if (
                                idx == total_paths
                                or idx <= 2
                                or idx % detail_interval == 0
                            ):
                                title = f"Generating {Path(path_str).name} ({idx}/{total_paths})"
                            update_shared_progress(
                                value=idx,
                                maximum=max(1, total_paths),
                                title=title,
                            )
                    if idx == total_paths or idx % event_interval == 0:
                        QtWidgets.QApplication.processEvents(
                            QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                        )
        finally:
            tab_widget = getattr(self.host, "tab_widget", None)
            if isinstance(tab_widget, QtWidgets.QWidget) and tab_updates_prev is not None:
                tab_widget.setUpdatesEnabled(tab_updates_prev)
                tab_widget.update()
            final_value = max(0, min(completed, total_paths))
            if callable(update_shared_progress):
                update_shared_progress(
                    value=final_value,
                    maximum=max(1, total_paths),
                    title="Plot generation complete.",
                )
            if callable(end_shared_progress):
                end_shared_progress()
        if self._plot_tabs:
            setter = getattr(self.host.tab_widget, "setCurrentIndex", None)
            if callable(setter):
                index = self.host.tab_widget.indexOf(self._plot_tabs[0])
                if index >= 0:
                    setter(index)
        self._log(f"Generated {plots_created} current annealing plot(s).")
        self.update_ui()

    def serialize_project_state(self, *, base_path: Path | None) -> dict[str, Any] | None:  # type: ignore[override]
        loaded_paths = [
            self._portable_path(Path(source), base_path)
            for source in self._loaded_files
            if isinstance(source, str) and source
        ]
        loaded_paths = [path for path in loaded_paths if isinstance(path, str) and path]

        plots: list[str] = []
        current_source: str | None = None
        current_tab = self.host.tab_widget.currentWidget()
        descriptors = getattr(self.host, "_tab_descriptors", {})
        for tab in self._plot_tabs:
            descriptor = descriptors.get(tab) if isinstance(descriptors, dict) else None
            metadata = getattr(descriptor, "metadata", {}) if descriptor is not None else {}
            source_value = metadata.get("source_file") if isinstance(metadata, dict) else None
            if not isinstance(source_value, str) or not source_value.strip():
                continue
            portable = self._portable_path(Path(source_value), base_path)
            if not isinstance(portable, str) or not portable.strip():
                continue
            plots.append(portable)
            if tab is current_tab:
                current_source = portable

        return {
            "loaded_paths": loaded_paths,
            "open_plot_sources": plots,
            "current_plot_source": current_source,
            "had_plots": bool(self._plot_tabs),
        }

    def restore_project_state(self, state: dict[str, Any], *, project_dir: Path) -> None:  # type: ignore[override]
        loaded_paths_payload = state.get("loaded_paths")
        resolved_paths: list[Path] = []
        if isinstance(loaded_paths_payload, list):
            for entry in loaded_paths_payload:
                resolved = self._resolve_portable_path(entry, project_dir)
                if isinstance(resolved, Path):
                    resolved_paths.append(resolved)
        if not resolved_paths:
            selected = getattr(self.host, "_selected_paths", None)
            if callable(selected):
                try:
                    resolved_paths = [path for path in selected() if isinstance(path, Path)]
                except Exception:
                    resolved_paths = []
        if resolved_paths:
            commit_paths = getattr(self.host, "_commit_selected_paths", None)
            if callable(commit_paths):
                try:
                    commit_paths(resolved_paths)
                except Exception:
                    pass

        loaded = False
        if resolved_paths:
            loaded = self._load_data_from_paths(resolved_paths, show_errors=False)
        else:
            self._data_by_file = {}
            self._loaded_files = []
            self._data = None

        self._clear_plot_tabs()
        if loaded and self._data_by_file:
            plot_sources_payload = state.get("open_plot_sources")
            plot_source_paths: list[Path] = []
            if isinstance(plot_sources_payload, list):
                for entry in plot_sources_payload:
                    resolved = self._resolve_portable_path(entry, project_dir)
                    if isinstance(resolved, Path):
                        plot_source_paths.append(resolved)
            if not plot_source_paths and bool(state.get("had_plots", False)):
                plot_source_paths = [Path(path) for path in self._loaded_files]

            for source_path in plot_source_paths:
                source_key = self._loaded_source_key_for_path(source_path)
                if not source_key:
                    continue
                frame = self._data_by_file.get(source_key)
                if frame is None:
                    continue
                self._create_plot_tab(source_key, frame)

            current_source = state.get("current_plot_source")
            current_path = self._resolve_portable_path(current_source, project_dir)
            if isinstance(current_path, Path):
                source_key = self._loaded_source_key_for_path(current_path)
                if source_key:
                    for tab in self._plot_tabs:
                        descriptor = getattr(self.host, "_tab_descriptors", {}).get(tab)
                        metadata = getattr(descriptor, "metadata", {}) if descriptor is not None else {}
                        source_value = metadata.get("source_file") if isinstance(metadata, dict) else None
                        if source_value == source_key:
                            index = self.host.tab_widget.indexOf(tab)
                            if index >= 0:
                                self.host.tab_widget.setCurrentIndex(index)
                            break
        self.update_ui()

    def open_origin(self) -> None:  # type: ignore[override]
        if not self._plot_tabs and self._data_by_file:
            self.generate()
        if not self._plot_tabs:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                "Generate at least one graph before exporting to Origin.",
            )
            return
        super().open_origin()

    def update_ui(self) -> None:
        has_data = bool(self._data_by_file)
        ready_to_plot = True
        self.apply_shared_action_state(
            can_plot=ready_to_plot,
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
            "figure_width": 9.2,
            "figure_height": 5.6,
        }

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
