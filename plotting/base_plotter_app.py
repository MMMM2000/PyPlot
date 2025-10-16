from __future__ import annotations

import sys
import uuid
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
import inspect
import logging
import types

from PyQt6 import QtCore, QtGui, QtWidgets

import pandas as pd

from plotting.base_plotter import (
    BasePlotWindow,
    WorksheetColumnMeta,
    WorksheetData,
    WorkbookData,
)
from plotting.utils import ensure_app_theme
from plotting.vsm_hysteresis_loops import VSMPlotter


class BasePlotterPlugin:
    """Interface for script-specific behaviour inside the base plotter."""

    def __init__(self, host: "BasePlotterWorkbench", name: str) -> None:
        self.host = host
        self.name = name

    # Lifecycle ---------------------------------------------------------
    def activate(self) -> None:
        """Called when the plugin becomes active."""

    def deactivate(self) -> None:
        """Called when the plugin is deselected."""

    # UI helpers --------------------------------------------------------
    def panel_widget(self) -> QtWidgets.QWidget | None:
        """Return the widget shown in the script panel."""

        return None

    def settings_widget(self) -> QtWidgets.QWidget | None:
        self._ensure_initialized()
        """Return the widget inserted into the Graph Settings dock."""

        return None

    def update_ui(self) -> None:
        """Refresh button states / summary labels."""

    # Host action handlers ----------------------------------------------
    def generate(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Generation is not implemented for this plotting script yet.",
        )

    def open_matplotlib(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Matplotlib export is not available for this plotting script yet.",
        )

    def save_graph(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Graph saving is not available for this plotting script yet.",
        )

    def normalize(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Normalization is not available for this plotting script yet.",
        )

    def export_txt(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "TXT export is not available for this plotting script yet.",
        )

    def open_origin(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Origin export is not available for this plotting script yet.",
        )


class ExternalPlotterPlugin(BasePlotterPlugin):
    """Placeholder plugin that opens the legacy plotting window."""

    def __init__(
        self,
        host: "BasePlotterWorkbench",
        name: str,
        launcher: Callable[[], QtWidgets.QWidget | None],
    ) -> None:
        super().__init__(host, name)
        self._launcher = launcher
        self._panel: QtWidgets.QWidget | None = None
        self._settings_stub: QtWidgets.QWidget | None = None

    def panel_widget(self) -> QtWidgets.QWidget:
        if self._panel is None:
            container = QtWidgets.QWidget(self.host)
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            title = QtWidgets.QLabel(f"{self.name} (standalone)")
            title.setStyleSheet("font-weight: 600;")
            layout.addWidget(title)
            message = QtWidgets.QLabel(
                "This plotting script has not been migrated to the shared workspace yet. "
                "Use the buttons below to open the original window."
            )
            message.setWordWrap(True)
            layout.addWidget(message)

            launch_button = QtWidgets.QPushButton(f"Open {self.name} window")
            launch_button.clicked.connect(self.generate)
            layout.addWidget(launch_button)
            layout.addStretch(1)
            self._panel = container
        return self._panel

    def settings_widget(self) -> QtWidgets.QWidget | None:
        if self._settings_stub is None:
            label = QtWidgets.QLabel(
                "Script-specific settings will appear here once the workflow is migrated."
            )
            label.setWordWrap(True)
            container = QtWidgets.QWidget(self.host)
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            layout.addWidget(label)
            layout.addStretch(1)
            self._settings_stub = container
        return self._settings_stub

    def update_ui(self) -> None:
        self.host.plot_button.setText(f"Open {self.name}")
        self.host.plot_button.setEnabled(True)
        self.host.popout_button.setEnabled(False)
        self.host.save_graph_button.setEnabled(False)
        self.host.normalize_button.setEnabled(False)
        self.host.export_button.setEnabled(False)
        self.host.open_origin_button.setEnabled(False)

    def generate(self) -> None:
        try:
            result = self._launcher()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to open {self.name}:\n{exc}",
            )
            return
        if isinstance(result, QtWidgets.QWidget):
            result.show()


class VSMHysteresisPlugin(BasePlotterPlugin):
    """Full-featured plugin that reuses the VSM hysteresis loops logic."""

    _METHOD_EXCLUDES = {
        "__init__",
        "_create_dock_widget",
        "_after_base_ui_created",
        "_import_paths",
        "_choose_files",
        "_choose_folder",
        "_handle_manual_path_entry",
        "_open_files_from_menu",
        "_open_folder_from_menu",
        "_selected_paths",
    }

    def __init__(self, host: "BasePlotterWorkbench", name: str = "VSM Hysteresis Loops") -> None:
        super().__init__(host, name)
        self._panel: QtWidgets.QWidget | None = None
        self._settings_widget: QtWidgets.QWidget | None = None
        self._summary_label: QtWidgets.QLabel | None = None
        self._initialized = False
        self._menus_ready = False

    # Lifecycle ---------------------------------------------------------
    def activate(self) -> None:
        self._ensure_initialized()
        self.host.help_topic = VSMPlotter.help_topic
        self.host._update_project_title()
        self.host._update_project_actions()
        self.update_ui()

    def deactivate(self) -> None:
        # keep state so reselecting preserves data
        pass

    # Widgets -----------------------------------------------------------
    def panel_widget(self) -> QtWidgets.QWidget:
        if self._panel is None:
            container = QtWidgets.QWidget(self.host)
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)

            title = QtWidgets.QLabel("VSM Hysteresis Loops configuration")
            title.setStyleSheet("font-weight: 600;")
            layout.addWidget(title)

            description = QtWidgets.QLabel(
                "Script-specific controls for VSM data. Imported data remain in the workspace "
                "while you adjust plotting behaviour."
            )
            description.setWordWrap(True)
            layout.addWidget(description)

            self._summary_label = QtWidgets.QLabel()
            layout.addWidget(self._summary_label)

            layout.addStretch(1)
            self._panel = container
        return self._panel

    def settings_widget(self) -> QtWidgets.QWidget:
        if self._settings_widget is None:
            container = QtWidgets.QWidget(self.host)
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            VSMPlotter._populate_graph_settings(self.host, layout)
            layout.addStretch(1)
            self._settings_widget = container
        return self._settings_widget

    # Host actions ------------------------------------------------------
    def generate(self) -> None:
        if not self.host.path_edit.text().strip():
            if not self.host._selected_paths:
                QtWidgets.QMessageBox.information(
                    self.host,
                    self.name,
                    "Import VSM measurements from the Data menu before generating plots.",
                )
                return
            self.host.path_edit.setText(self.host._format_paths(self.host._selected_paths))
        VSMPlotter._generate_plots(self.host)
        self.host._save_settings()
        self.update_ui()

    def open_matplotlib(self) -> None:
        VSMPlotter._open_matplotlib_window(self.host)

    def save_graph(self) -> None:
        VSMPlotter._save_current_graph(self.host)

    def normalize(self) -> None:
        VSMPlotter._normalize_current_graph(self.host)

    def export_txt(self) -> None:
        VSMPlotter._export_txt(self.host)

    def open_origin(self) -> None:
        VSMPlotter._open_origin_prompt(self.host)

    # UI state ----------------------------------------------------------
    def update_ui(self) -> None:
        self._ensure_initialized()
        host = self.host
        if self._summary_label is not None:
            count = len(host._selected_paths)
            if count == 0:
                self._summary_label.setText("No data sources selected.")
            elif count == 1:
                self._summary_label.setText(f"1 data source selected: {host._selected_paths[0]}")
            else:
                self._summary_label.setText(f"{count} data sources selected.")

        has_paths = bool(host._selected_paths)
        host.plot_button.setEnabled(has_paths or bool(host.path_edit.text().strip()))
        host.plot_button.setText("Generate VSM Hysteresis Loops")

        host._update_save_graph_enabled()
        host._update_normalize_enabled()
        host._update_project_actions()

    # Internal helpers --------------------------------------------------
    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        host = self.host
        host.logger = logging.getLogger("vsm_hysteresis_loops")
        host.logger.setLevel(logging.INFO)
        host.settings = QtCore.QSettings("MicrowireLab", "VSMHysteresisLoops")

        stored_x = host.settings.value("x_axis")
        stored_y = host.settings.value("y_axis")
        host._stored_axes = (
            stored_x if isinstance(stored_x, str) and stored_x else None,
            stored_y if isinstance(stored_y, str) and stored_y else None,
        )
        host.last_export_path = None

        host.measurements: List[VSMPlotter.VSMMeasurement] = []  # type: ignore[attr-defined]
        host._last_rescale_info = {}
        host._last_axes = None
        host._last_rescale_enabled = False
        host._line_visibility = {}
        host._worksheet_models = {}
        host._plotted_series_exports = {}
        host._metrics_by_temperature = {}
        host._metrics_by_angle = {}
        host._metric_column_names = {}
        host._metric_results = {}
        host._metric_debug_tables = {}
        host._metric_debug_columns = {}
        host._metric_debug_windows = {}
        host._last_graph_dir = None
        host._field_direction_enabled = False
        host._direction_legends = {}
        host._base_title = "VSM Hysteresis Loops"
        host.PROJECT_EXTENSION = VSMPlotter.PROJECT_EXTENSION
        host.PROJECT_VERSION = VSMPlotter.PROJECT_VERSION
        host.PROJECT_CODE = VSMPlotter.PROJECT_CODE
        host.PROJECT_SETTINGS_PREFIX = VSMPlotter.PROJECT_SETTINGS_PREFIX

        self._bind_methods()
        if not self._menus_ready:
            VSMPlotter._extend_menus(host, host.menuBar())
            self._menus_ready = True

        host._load_settings()
        self._initialized = True

    def _bind_methods(self) -> None:
        host = self.host
        if getattr(host, "_vsm_methods_bound", False):
            return
        for name, func in inspect.getmembers(VSMPlotter, inspect.isfunction):
            if name in self._METHOD_EXCLUDES:
                continue
            setattr(host, name, types.MethodType(func, host))
        host._vsm_methods_bound = True

class BasePlotterWorkbench(BasePlotWindow):
    """Lightweight harness for exercising shared BasePlotWindow features."""

    help_topic = "base_plotter"
    PROJECT_EXTENSION = ".pypj"
    PROJECT_CODE = "base_plotter"
    PROJECT_SETTINGS_PREFIX = "base_plotter"

    def __init__(
        self,
        *,
        plotters: Dict[str, Callable[["BasePlotterWorkbench"], BasePlotterPlugin]] | None = None,
        initial_plotter: str | None = None,
    ) -> None:
        self.settings = QtCore.QSettings("MicrowireLab", "BasePlotterWorkbench")
        self._last_directory: Path | None = None
        self._selected_paths: List[Path] = []
        self._plugin_factories: Dict[
            str, Callable[["BasePlotterWorkbench"], BasePlotterPlugin]
        ] = dict(sorted((plotters or {}).items()))
        self._plugin_instances: Dict[str, BasePlotterPlugin] = {}
        self._current_plugin: BasePlotterPlugin | None = None
        self._current_plotter_name: str | None = None
        self._plotter_combo: QtWidgets.QComboBox | None = None
        self._plugin_settings_container: QtWidgets.QWidget | None = None
        self._plugin_settings_layout: QtWidgets.QVBoxLayout | None = None
        self._active_plugin_updater: Callable[[], None] | None = None
        self._initial_plotter = initial_plotter
        super().__init__(title="Base Plotter")
        try:
            self.setWindowState(self.windowState() | QtCore.Qt.WindowState.WindowMaximized)
        except Exception:
            pass

        self.tab_widget.currentChanged.connect(lambda _: self._update_action_states())

        stored_sources = self.settings.value("sources", "")
        if isinstance(stored_sources, str) and stored_sources.strip():
            self._apply_path_text(stored_sources)
            self.path_edit.setText(stored_sources)

        stored_directory = self.settings.value("last_directory", "")
        if isinstance(stored_directory, str) and stored_directory:
            candidate = Path(stored_directory)
            if candidate.exists():
                self._last_directory = candidate

        self._update_action_states()
        self._set_data_sources_visible(False)
        self._select_initial_plotter()

    def _select_initial_plotter(self) -> None:
        if not self._plugin_factories:
            self._apply_selected_plotter()
            return
        target = self._initial_plotter
        if not target or target not in self._plugin_factories:
            target = next(iter(self._plugin_factories), None)
        if isinstance(self._plotter_combo, QtWidgets.QComboBox) and target is not None:
            index = self._plotter_combo.findText(target)
            if index >= 0:
                self._plotter_combo.setCurrentIndex(index)
            else:
                self._plotter_combo.setCurrentIndex(0)
        self._apply_selected_plotter()
        self._apply_selected_plotter()
        self._apply_selected_plotter()
        self._select_initial_plotter()
        return
    # ------------------------------------------------------------------ Qt hooks
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self.settings.setValue("sources", self.path_edit.text())
        if self._last_directory is not None:
            self.settings.setValue("last_directory", str(self._last_directory))
        self.settings.sync()
        super().closeEvent(event)

    # ------------------------------------------------------------------ project and data integration
    def _import_paths(self, paths: Iterable[Path]) -> None:
        super()._import_paths(paths)
        seen: list[Path] = []
        for workbook in self._workbooks.values():
            source = workbook.source
            if isinstance(source, Path):
                try:
                    resolved = source.resolve()
                except Exception:
                    resolved = source
                if resolved not in seen:
                    seen.append(resolved)
        self._selected_paths = seen
        if self._selected_paths:
            self.path_edit.setText(self._format_paths(self._selected_paths))
        else:
            self.path_edit.clear()
        self._update_action_states()
        self._update_project_actions()

    def _has_project_data_to_save(self) -> bool:
        return bool(self._worksheets)

    def _reset_project_state(self) -> None:
        super()._reset_project_state()
        self._clear_imported_data()
        self._selected_paths = []
        self.path_edit.clear()
        self._update_action_states()
        self._update_project_actions()

    def _build_project_payload(self, *, base_path: Path | None) -> Dict[str, Any]:
        selected_payload = [
            self._portable_path(path, base_path) for path in self._selected_paths
        ]
        workbooks_payload: List[Dict[str, Any]] = []
        for workbook in self._workbooks.values():
            worksheets_payload: List[Dict[str, Any]] = []
            for worksheet_key in workbook.worksheets:
                worksheet = self._worksheets.get(worksheet_key)
                if worksheet is None:
                    continue
                table = worksheet.dataframe.astype(object).where(
                    pd.notnull(worksheet.dataframe), None
                )
                records = table.to_dict(orient="records")
                metadata_payload = {
                    column: asdict(worksheet.columns.get(column, WorksheetColumnMeta()))
                    for column in table.columns
                }
                index_values = [
                    value if isinstance(value, (int, float, str)) or value is None else str(value)
                    for value in table.index.tolist()
                ]
                worksheet_payload = {
                    "key": str(worksheet.key),
                    "name": worksheet.name,
                    "columns": [str(col) for col in table.columns],
                    "records": records,
                    "index": index_values,
                    "metadata": metadata_payload,
                    "source": self._portable_path(worksheet.source, base_path),
                }
                worksheets_payload.append(worksheet_payload)
            if not worksheets_payload:
                continue
            workbook_payload = {
                "key": str(workbook.key),
                "name": workbook.name,
                "source": self._portable_path(workbook.source, base_path),
                "folder": self._portable_path(workbook.folder, base_path),
                "worksheets": worksheets_payload,
            }
            workbooks_payload.append(workbook_payload)
        return {
            "selected_paths": selected_payload,
            "workbooks": workbooks_payload,
        }

    def _apply_project_payload(self, payload: Dict[str, Any], *, project_dir: Path) -> bool:
        self._clear_imported_data()
        selected_payload = payload.get("selected_paths")
        self._selected_paths = []
        if isinstance(selected_payload, list):
            for entry in selected_payload:
                if isinstance(entry, str) and entry:
                    resolved = self._resolve_portable_path(entry, project_dir)
                    if resolved is not None:
                        self._selected_paths.append(resolved)
        if self._selected_paths:
            self.path_edit.setText(self._format_paths(self._selected_paths))
            self._remember_directory_from_paths(self._selected_paths)
        else:
            self.path_edit.clear()

        workbooks_payload = payload.get("workbooks")
        imported = False
        if isinstance(workbooks_payload, list):
            for workbook_entry in workbooks_payload:
                if not isinstance(workbook_entry, dict):
                    continue
                workbook_key = workbook_entry.get("key") or str(uuid.uuid4())
                workbook = WorkbookData(
                    key=workbook_key,
                    name=str(workbook_entry.get("name") or "Workbook"),
                    source=self._resolve_portable_path(workbook_entry.get("source"), project_dir),
                    folder=self._resolve_portable_path(workbook_entry.get("folder"), project_dir),
                )
                worksheets_payload = workbook_entry.get("worksheets")
                worksheet_objects: List[WorksheetData] = []
                if isinstance(worksheets_payload, list):
                    for sheet_entry in worksheets_payload:
                        if not isinstance(sheet_entry, dict):
                            continue
                        columns = sheet_entry.get("columns")
                        records = sheet_entry.get("records")
                        df = (
                            pd.DataFrame(records, columns=columns)
                            if isinstance(records, list)
                            else pd.DataFrame(columns=columns or [])
                        )
                        index_values = sheet_entry.get("index")
                        if isinstance(index_values, list) and len(index_values) == len(df):
                            df.index = pd.Index(index_values)
                        metadata_payload = sheet_entry.get("metadata")
                        columns_meta: Dict[str, WorksheetColumnMeta] = {}
                        for column in df.columns:
                            meta_dict = (
                                metadata_payload.get(column)
                                if isinstance(metadata_payload, dict)
                                else None
                            )
                            if isinstance(meta_dict, dict):
                                columns_meta[column] = WorksheetColumnMeta(
                                    long_name=str(meta_dict.get("long_name", "")),
                                    units=str(meta_dict.get("units", "")),
                                    comments=str(meta_dict.get("comments", "")),
                                    formula=str(meta_dict.get("formula", "")),
                                )
                            else:
                                columns_meta[column] = WorksheetColumnMeta(long_name=str(column))
                        worksheet_key = sheet_entry.get("key") or f"{workbook_key}::{sheet_entry.get('name', 'Sheet')}"
                        worksheet = WorksheetData(
                            key=worksheet_key,
                            name=str(sheet_entry.get("name") or "Sheet"),
                            dataframe=df,
                            columns=columns_meta,
                            source=self._resolve_portable_path(sheet_entry.get("source"), project_dir),
                            workbook_key=workbook_key,
                        )
                        worksheet_objects.append(worksheet)
                if worksheet_objects:
                    self._register_imported_workbook(workbook, worksheet_objects)
                    imported = True
        if imported:
            self._refresh_imported_data_summary()
        self._update_action_states()
        self._update_project_actions()
        return True

    def _clear_imported_data(self) -> None:
        for key in list(self._worksheet_tabs_open.keys()):
            self._remove_worksheet(key)
        self._workbooks.clear()
        self._worksheets.clear()
        self._data_workbook_items.clear()
        self._data_folder_items.clear()
        self._worksheet_tree_items.clear()
        if self._data_tree_root is not None:
            index = self.project_tree.indexOfTopLevelItem(self._data_tree_root)
            if index >= 0:
                self.project_tree.takeTopLevelItem(index)
            self._data_tree_root = None
        self._refresh_imported_data_summary()
        if self._refresh_import_action is not None:
            self._refresh_import_action.setEnabled(False)

    def _portable_path(self, path: Path | None, base_path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if base_path is not None:
            try:
                return str(resolved.relative_to(base_path.resolve()))
            except Exception:
                pass
        return str(resolved)

    def _resolve_portable_path(self, value: str | None, project_dir: Path) -> Path | None:
        if not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (project_dir / candidate).resolve()
        return candidate

    # ------------------------------------------------------------------ abstract implementations
    def _handle_manual_path_entry(self) -> None:
        self._apply_path_text(self.path_edit.text())
        self._update_action_states()

    def _choose_files(self) -> None:
        start = self._dialog_start_directory()
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select data files",
            str(start),
        )
        if not paths:
            return
        self._selected_paths = [Path(entry) for entry in paths]
        self._remember_directory_from_paths(self._selected_paths)
        self.path_edit.setText(self._format_paths(self._selected_paths))
        self._update_action_states()

    def _choose_folder(self) -> None:
        start = self._dialog_start_directory()
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select data folder",
            str(start),
        )
        if not directory:
            return
        folder = Path(directory)
        self._selected_paths = [folder]
        self._last_directory = folder
        self.path_edit.setText(self._format_paths(self._selected_paths))
        self._update_action_states()

    def _generate_plots(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.generate()
            return
        QtWidgets.QMessageBox.information(
            self,
            "Base Plotter",
            "Select a plotting script before generating plots.",
        )
    def _open_matplotlib_window(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.open_matplotlib()
            return
        QtWidgets.QMessageBox.information(
            self,
            "Base Plotter",
            "Select a plotting script that supports Matplotlib export.",
        )
    def _save_current_graph(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.save_graph()
            return
        QtWidgets.QMessageBox.information(
            self,
            "Base Plotter",
            "Plot a graph before saving.",
        )
    def _normalize_current_graph(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.normalize()
            return
        QtWidgets.QMessageBox.information(
            self,
            "Base Plotter",
            "Plot a graph before normalizing.",
        )
    def _export_txt(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.export_txt()
            return
        QtWidgets.QMessageBox.information(
            self,
            "Base Plotter",
            "Generate data before exporting.",
        )
    def _open_origin_prompt(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.open_origin()
            return
        QtWidgets.QMessageBox.information(
            self,
            "Base Plotter",
            "Origin export is not available for the selected script.",
        )
    def _populate_graph_settings(self, layout: QtWidgets.QVBoxLayout) -> None:
        label = QtWidgets.QLabel("Configure graph settings in your plotter subclass.")
        label.setWordWrap(True)
        layout.addWidget(label)

        group = QtWidgets.QGroupBox("Select plotting script")
        group_layout = QtWidgets.QVBoxLayout(group)
        group_layout.setContentsMargins(8, 8, 8, 8)
        group_layout.setSpacing(6)
        self._plotter_combo = QtWidgets.QComboBox(group)
        if self._plugin_factories:
            for name in self._plugin_factories:
                self._plotter_combo.addItem(name)
            self._plotter_combo.currentIndexChanged.connect(lambda _: self._apply_selected_plotter())
        else:
            self._plotter_combo.addItem("No scripts available")
            self._plotter_combo.setEnabled(False)
        group_layout.addWidget(self._plotter_combo)
        layout.addWidget(group)

        self._plugin_settings_container = QtWidgets.QWidget(self)
        self._plugin_settings_layout = QtWidgets.QVBoxLayout(self._plugin_settings_container)
        self._plugin_settings_layout.setContentsMargins(0, 0, 0, 0)
        self._plugin_settings_layout.setSpacing(6)
        layout.addWidget(self._plugin_settings_container)
        self._plugin_settings_container.setVisible(False)

    def _set_plugin_settings_widget(self, widget: QtWidgets.QWidget | None) -> None:
        if self._plugin_settings_layout is None or self._plugin_settings_container is None:
            return
        layout = self._plugin_settings_layout
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            child = item.widget()
            if child is not None:
                child.setParent(None)
            del item
        if widget is None:
            self._plugin_settings_container.setVisible(False)
        else:
            layout.addWidget(widget)
            self._plugin_settings_container.setVisible(True)

    def _apply_selected_plotter(self) -> None:
        name = (
            self._plotter_combo.currentText()
            if isinstance(self._plotter_combo, QtWidgets.QComboBox)
            else None
        )
        if not name or name not in self._plugin_factories:
            if self._current_plugin is not None:
                self._current_plugin.deactivate()
            self._current_plugin = None
            self._current_plotter_name = None
            self._set_script_panel(None)
            self._set_plugin_settings_widget(None)
            self._active_plugin_updater = None
            self._update_action_states()
            return

        plugin = self._plugin_instances.get(name)
        if plugin is None:
            factory = self._plugin_factories.get(name)
            if factory is None:
                plugin = ExternalPlotterPlugin(self, name, lambda: None)
            else:
                plugin = factory(self)
            self._plugin_instances[name] = plugin

        if self._current_plugin is not plugin:
            if self._current_plugin is not None:
                self._current_plugin.deactivate()
            self._current_plugin = plugin
            self._current_plotter_name = name
            self._set_script_panel(plugin.panel_widget())
            self._set_plugin_settings_widget(plugin.settings_widget())
            plugin.activate()

        self._active_plugin_updater = plugin.update_ui
        if self._active_plugin_updater is not None:
            try:
                self._active_plugin_updater()
            except Exception:
                pass
        self._update_action_states()

    def _apply_path_text(self, text: str) -> None:
        paths = [Path(entry) for entry in self._iter_path_entries(text)]
        self._selected_paths = paths
        self._remember_directory_from_paths(paths)

    def _iter_path_entries(self, text: str) -> Iterable[str]:
        candidate = text.replace("\r\n", "\n").replace(";", "\n")
        for entry in candidate.split("\n"):
            cleaned = entry.strip().strip('"')
            if cleaned:
                yield cleaned

    def _format_paths(self, paths: Iterable[Path]) -> str:
        return "; ".join(str(path) for path in paths)

    def _remember_directory_from_paths(self, paths: Iterable[Path]) -> None:
        last = None
        for path in paths:
            if path.is_dir():
                last = path
            elif path.exists():
                last = path.parent
        if last is not None:
            self._last_directory = last

    def _dialog_start_directory(self) -> Path:
        if self._last_directory is not None and self._last_directory.exists():
            return self._last_directory
        return Path.home()

    def _update_action_states(self) -> None:
        if self._current_plugin is not None:
            try:
                self._current_plugin.update_ui()
            except Exception:
                pass
        else:
            has_selection = bool(self._selected_paths)
            self.plot_button.setEnabled(has_selection)
            self.plot_button.setText("Generate plots")
            self.popout_button.setEnabled(False)
            self.save_graph_button.setEnabled(bool(self.tab_widget.currentWidget()))
            self.normalize_button.setEnabled(False)
            self.export_button.setEnabled(bool(self.tab_widget.currentWidget()))
            self.open_origin_button.setEnabled(False)


def main(
    available_plotters: Dict[str, Callable[[], QtWidgets.QWidget | None]] | None = None,
    initial_plotter: str | None = None,
) -> QtWidgets.QWidget | None:
    """Entry-point used by the launcher."""

    plugin_factories: Dict[str, Callable[["BasePlotterWorkbench"], BasePlotterPlugin]] = {}
    for name, launcher in sorted((available_plotters or {}).items()):
        if name == "VSM Hysteresis Loops":
            plugin_factories[name] = lambda host, n=name: VSMHysteresisPlugin(host, n)
        else:
            plugin_factories[name] = lambda host, l=launcher, n=name: ExternalPlotterPlugin(host, n, l)

    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    ensure_app_theme(app)
    window = BasePlotterWorkbench(plotters=plugin_factories, initial_plotter=initial_plotter)
    window.show()
    if created_app:
        app.exec()
        return None
    return window

if __name__ == "__main__":
    main()


