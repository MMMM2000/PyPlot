from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Hashable, Iterable

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from plotting.plugins.base import PyPlotPlugin, register_plugin
from plotting.plugins._window import window_api
from . import core as rvst_core

if TYPE_CHECKING:
    from plotting.pyplot.window import GraphLineState, WorksheetData


@register_plugin("R vs T")
class RVsTPlugin(PyPlotPlugin):
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
        summary = QtWidgets.QLabel(
            "Load R vs T CSV files to plot measured temperature against resistance "
            "with heating and cooling separated in one graph."
        )
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
        origin_section, origin_layout = window_module.create_toolbar_section("Origin export", parent=container)
        note = QtWidgets.QLabel(
            "Uses shared PyPlot Origin export through generated worksheets and shared graph workbooks."
        )
        note.setWordWrap(True)
        origin_layout.addWidget(note)
        origin_layout.addStretch(1)
        layout.addWidget(origin_section)
        layout.addStretch(1)
        self._settings_widget = container
        return container

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

    def _load_data_from_paths(self, paths: list[Path], *, show_errors: bool) -> bool:
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
                data = rvst_core.load_file(resolved)
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
        title = rvst_core.format_rvst_title(Path(path_str).stem)
        segments = rvst_core.split_heating_cooling(df)
        try:
            fig, _ = rvst_core.plot_one(df, title)
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
            workbook_key = self._workbook_keys.get(path_str, f"rvst::{path_str}")
            plotted_lines = list(ax.get_lines())
            for index, line in enumerate(plotted_lines, start=1):
                label = line.get_label() or f"Series {index}"
                segment = segments[index - 1] if index - 1 < len(segments) else None
                source_row_ids = None
                worksheet_key = None
                if segment is not None:
                    if "_source_row_id" in segment.frame.columns:
                        source_row_ids = (
                            pd.to_numeric(segment.frame["_source_row_id"], errors="coerce")
                            .to_numpy(dtype=float)
                            .astype(int, copy=False)
                        )
                    worksheet_key = self._worksheet_key_for_segment(workbook_key, segment.label)
                state = window_module.GraphLineState(
                    key=(label, float(index)),
                    label=label,
                    line=line,
                    base_x=line.get_xdata(),
                    base_y=line.get_ydata(),
                    full_x=line.get_xdata(),
                    full_y=line.get_ydata(),
                    source_row_ids=source_row_ids,
                    worksheet_key=worksheet_key,
                    source_file=path_str,
                )
                lines[state.key] = state
        descriptor = window_module.TabDescriptor(
            kind="r_vs_t",
            title=ax.get_title() if ax else title,
            root_label=Path(path_str).name,
            x_label=ax.get_xlabel() if ax else "Temperature [°C]",
            y_label=ax.get_ylabel() if ax else "Resistance [Ω]",
            canvas=canvas,
            axes=ax,
            lines=lines,
            metadata={"source_file": path_str, "saved_path": ""},
        )
        self.host.tab_widget.addTab(tab, Path(path_str).name)
        self.host._register_plot_tab(tab, canvas, ax, descriptor)
        self._plot_tabs.append(tab)
        return tab

    def _replace_plot_tab_for_source(self, path_str: str) -> QtWidgets.QWidget | None:
        target_tab: QtWidgets.QWidget | None = None
        target_index = -1
        for tab in list(self._plot_tabs):
            descriptor = self.host._tab_descriptors.get(tab)
            metadata = getattr(descriptor, "metadata", {}) if descriptor is not None else {}
            if isinstance(metadata, dict) and metadata.get("source_file") == path_str:
                target_tab = tab
                target_index = self.host.tab_widget.indexOf(tab)
                break
        if target_tab is not None:
            self.host._clear_tab_list([target_tab])
            try:
                self._plot_tabs.remove(target_tab)
            except ValueError:
                pass
        replacement = self._create_plot_tab(path_str, self._data_by_file[path_str])
        if replacement is None:
            return None
        current_index = self.host.tab_widget.indexOf(replacement)
        if target_index >= 0 and current_index >= 0 and current_index != target_index:
            try:
                self.host.tab_widget.tabBar().moveTab(current_index, target_index)
            except Exception:
                pass
        index = self.host.tab_widget.indexOf(replacement)
        if index >= 0:
            self.host.tab_widget.setCurrentIndex(index)
        return replacement

    def _worksheet_key_for_segment(self, workbook_key: Hashable, segment_label: str) -> str:
        return self.host._worksheet_key(workbook_key, segment_label)

    def load_data(self) -> None:  # type: ignore[override]
        paths = self.host.ensure_data_selection(self)
        if not paths:
            return
        loaded = self._load_data_from_paths(list(paths), show_errors=True)
        if not loaded:
            self.update_ui()
            return
        self._log(f"Loaded {len(self._data_by_file)} R vs T file(s).")
        self.update_ui()

    def generate(self) -> None:  # type: ignore[override]
        if not self._data_by_file:
            self.load_data()
        if not self._data_by_file:
            return
        self.clear_plot_tabs(self._plot_tabs)
        paths_sorted = sorted(self._data_by_file.keys())
        total_paths = len(paths_sorted)
        begin_shared_progress = getattr(self.host, "_begin_task_progress", None)
        update_shared_progress = getattr(self.host, "_update_task_progress", None)
        end_shared_progress = getattr(self.host, "_end_task_progress", None)
        if callable(begin_shared_progress):
            begin_shared_progress(
                "Generating R vs T plots...",
                maximum=max(1, total_paths),
                value=0,
            )
        completed = 0
        progress_interval = 1 if total_paths <= 25 else max(1, total_paths // 100)
        suspend_tree_updates = getattr(self.host, "_suspend_project_tree_updates", None)
        tree_context = suspend_tree_updates() if callable(suspend_tree_updates) else nullcontext(None)
        with tree_context:
            for idx, path_str in enumerate(paths_sorted, start=1):
                self._create_plot_tab(path_str, self._data_by_file[path_str])
                completed = idx
                if callable(update_shared_progress) and (
                    idx == total_paths or idx <= 2 or idx % progress_interval == 0
                ):
                    update_shared_progress(
                        value=idx,
                        maximum=max(1, total_paths),
                        title=f"Generating {Path(path_str).name} ({idx}/{total_paths})",
                    )
                if idx == total_paths or idx % 2 == 0:
                    QtWidgets.QApplication.processEvents(
                        QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                    )
        if callable(update_shared_progress):
            update_shared_progress(value=completed, maximum=max(1, total_paths))
        if callable(end_shared_progress):
            end_shared_progress()
        self._log(f"Generated {len(self._plot_tabs)} R vs T graph(s).")
        self.update_ui()

    def serialize_project_state(self, *, base_path: Path | None) -> dict[str, Any] | None:
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
        loaded = self._load_data_from_paths(resolved_paths, show_errors=False) if resolved_paths else False
        self.clear_plot_tabs(self._plot_tabs)
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
                if source_key:
                    self._create_plot_tab(source_key, self._data_by_file[source_key])
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
            "figure_width": 9.0,
            "figure_height": 5.4,
        }

    def _register_workbook(self, path_str: str, df: pd.DataFrame) -> bool:
        host = self.host
        window_module = window_api()
        path = Path(path_str)
        key = self._workbook_keys.get(path_str)
        if not key:
            key = f"rvst::{path_str}"
            self._workbook_keys[path_str] = key
        workbook = window_module.WorkbookData(
            key=key,
            name=f"{path.stem} (R vs T)",
            worksheets=[],
            source=None,
            folder=None,
        )
        worksheet_objects: list[WorksheetData] = []
        for segment in rvst_core.split_heating_cooling(df):
            frame = segment.frame.copy()
            renamed = frame.rename(
                columns={
                    "iso_time": "Timestamp",
                    "t_elapsed_s": "Elapsed (s)",
                    "sp_c": "Setpoint (°C)",
                    "pv_c": "Temperature (°C)",
                    "resistance_ohm": "Resistance (Ω)",
                }
            )
            worksheet = host._create_worksheet_from_frame(workbook, segment.label, renamed)
            worksheet.axis_roles = "MMMXY"
            columns = worksheet.columns
            for column_name, unit, long_name in (
                ("Elapsed (s)", "s", "Elapsed time"),
                ("Setpoint (°C)", "°C", "Setpoint"),
                ("Temperature (°C)", "°C", "Temperature"),
                ("Resistance (Ω)", "Ω", "Resistance"),
            ):
                meta = columns.get(column_name)
                if isinstance(meta, window_module.WorksheetColumnMeta):
                    meta.units = unit
                    meta.long_name = long_name
            worksheet_objects.append(worksheet)
        if not worksheet_objects:
            return False
        workbook.worksheets = [ws.key for ws in worksheet_objects]
        host._register_imported_workbook(workbook, worksheet_objects)
        return True

    def _register_workbooks(self) -> None:
        host = self.host
        created: list[str] = []
        for path_str, df in self._data_by_file.items():
            if self._register_workbook(path_str, df):
                created.append(Path(path_str).name)
        if created:
            host._refresh_imported_data_summary()
            host._sync_selected_paths_with_imports()
            self._log("Created worksheets for: " + ", ".join(created))

    def supports_graph_point_removal(self) -> bool:  # type: ignore[override]
        return True

    def remove_graph_points(  # type: ignore[override]
        self,
        *,
        descriptor: Any,
        point_refs: Iterable[Any],
    ) -> int:
        metadata = getattr(descriptor, "metadata", {})
        source_file = metadata.get("source_file") if isinstance(metadata, dict) else None
        if not isinstance(source_file, str) or not source_file.strip():
            return 0
        frame = self._data_by_file.get(source_file)
        if frame is None or frame.empty or "_source_row_id" not in frame.columns:
            return 0

        source_ids: set[int] = set()
        for ref in point_refs:
            row_id = getattr(ref, "source_row_id", None)
            if row_id is None:
                continue
            try:
                source_ids.add(int(row_id))
            except Exception:
                continue
        if not source_ids:
            return 0

        keep_mask = ~frame["_source_row_id"].isin(sorted(source_ids))
        filtered = frame.loc[keep_mask].copy().reset_index(drop=True)
        removed = int(len(frame.index) - len(filtered.index))
        if removed <= 0:
            return 0
        if filtered.empty:
            QtWidgets.QMessageBox.warning(
                self.host,
                self.name,
                "Removing those points would leave the graph without any data.",
            )
            return 0

        self._data_by_file[source_file] = filtered
        self._data = self._data_by_file
        self._register_workbook(source_file, filtered)
        self.host._refresh_imported_data_summary()
        self.host._sync_selected_paths_with_imports()
        self._replace_plot_tab_for_source(source_file)
        self._log(f"Removed {removed} bad data point(s) from {Path(source_file).name}.")
        return removed
