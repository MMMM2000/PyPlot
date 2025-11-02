from __future__ import annotations

import sys
import uuid
from dataclasses import asdict
import json
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
import logging

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.figure import Figure

import pandas as pd

from .window import (
    GraphLineState,
    PyPlotWindow,
    WorksheetColumnMeta,
    WorksheetData,
    WorkbookData,
    TabDescriptor,
    create_toolbar_section,
)
from PyQt6 import QtWidgets, QtCore

from plotting.shared.utils import (
    ensure_app_theme,
    get_last_used_dir,
    set_last_used_dir,
    install_standard_menu,
)
from plotting.plugins import PyPlotPlugin, ExternalPlotterPlugin, EmbeddedWidgetPlugin
from plotting.plugins.temperature_dependence import TemperatureDependencePlugin
from plotting.plugins.temperature_sensitivity import TemperatureSensitivityPlugin
from plotting.plugins.current_annealing import CurrentAnnealingPlugin
from plotting.plugins.stress_dependence import StressDependencePlugin
from plotting.plugins.stress_sensitivity import StressSensitivityPlugin
from plotting.plugins.hsw_load_compare import HswLoadComparePlugin
from plotting.plugins.maxion_continuous import MaxionContinuousPlugin
from plotting.plugins.pdf_plotter import PdfPlotterPlugin
from plotting.plugins.hysteresis_loops import HysteresisLoopsPlugin
from plotting.plugins.hsw_distribution import HswDistributionPlugin
from plotting.plugins.strain_3d_plot import Strain3DPlotPlugin
from plotting.plugins.vsm_hysteresis import VSMHysteresisPlugin

PLUGIN_CLASS_REGISTRY: Dict[str, type["PyPlotPlugin"]] = {
    "VSM Hysteresis Loops": VSMHysteresisPlugin,
    "Temperature Dependence": TemperatureDependencePlugin,
    "Temperature Sensitivity": TemperatureSensitivityPlugin,
    "Current Annealing": CurrentAnnealingPlugin,
    "Stress Dependence": StressDependencePlugin,
    "Stress Sensitivity": StressSensitivityPlugin,
    "Hsw Load Compare": HswLoadComparePlugin,
    "Maxion Continuous": MaxionContinuousPlugin,
    "PDF Plotter": PdfPlotterPlugin,
    "Hysteresis Loops": HysteresisLoopsPlugin,
    "Hsw Distribution": HswDistributionPlugin,
    "Strain 3D Plot": Strain3DPlotPlugin,
}


class PyPlotWorkbench(PyPlotWindow):
    """Lightweight harness for exercising shared PyPlotWindow features."""

    help_topic = "pyplot"
    PROJECT_EXTENSION = ".pypj"
    PROJECT_CODE = "pyplot"
    PROJECT_SETTINGS_PREFIX = "pyplot"
    GRAPH_DOCK_ENABLED = False

    def __init__(
        self,
        *,
        plotters: Dict[str, Callable[["PyPlotWorkbench"], PyPlotPlugin]] | None = None,
        initial_plotter: str | None = None,
    ) -> None:
        self.settings = QtCore.QSettings("MicrowireLab", "PyPlotWorkbench")
        raw_dirs = self.settings.value("plugin_last_dirs", "{}")
        try:
            parsed_dirs = json.loads(raw_dirs) if isinstance(raw_dirs, str) else {}
        except json.JSONDecodeError:
            parsed_dirs = {}
        self._plugin_last_directories: Dict[str, Path] = {
            key: Path(value) for key, value in parsed_dirs.items() if isinstance(value, str)
        }
        self._last_directory: Path | None = None
        self._last_source_dir: Path | None = None
        self._selected_path_entries: List[Path] = []
        self._plugin_factories: Dict[
            str, Callable[["PyPlotWorkbench"], PyPlotPlugin]
        ] = dict(sorted((plotters or {}).items()))
        self._plugin_instances: Dict[str, PyPlotPlugin] = {}
        self._current_plugin: PyPlotPlugin | None = None
        self._current_plotter_name: str | None = None
        self._plotter_combo: QtWidgets.QComboBox | None = None
        self._active_plugin_updater: Callable[[], None] | None = None
        self._initial_plotter = initial_plotter
        self._plotter_history: list[str] = self._load_plotter_history()
        self._spawned_windows: list[PyPlotWorkbench] = []
        self._last_graph_dir: Path | None = None
        super().__init__(title="PyPlot")
        self.setObjectName("PyPlotWorkbench")
        try:
            self.setWindowState(
                self.windowState() | QtCore.Qt.WindowState.WindowMaximized
            )
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

        stored_graph_dir = self.settings.value("last_graph_dir", "")
        if isinstance(stored_graph_dir, str) and stored_graph_dir.strip():
            candidate = Path(stored_graph_dir)
            if candidate.exists():
                self._last_graph_dir = candidate

        self._update_action_states()
        self._set_data_sources_visible(False)
        self._select_initial_plotter()
        self._update_window_title()


    def _update_window_title(self) -> None:
        parts = ["PyPlot"]
        if self._current_plotter_name:
            parts.append(self._current_plotter_name)
        project_path = getattr(self, "_project_path", None)
        if isinstance(project_path, Path) and project_path.name:
            parts.append(project_path.name)
        else:
            parts.append("UNTITLED")
        self.setWindowTitle(" - ".join(parts))

    def _load_plotter_history(self) -> list[str]:
        stored = self.settings.value("plotter_history", "[]")
        if isinstance(stored, str):
            try:
                parsed = json.loads(stored)
            except Exception:
                parsed = []
        elif isinstance(stored, (list, tuple)):
            parsed = list(stored)
        else:
            parsed = []
        history = [str(name) for name in parsed if isinstance(name, str)]
        return history

    def _save_plotter_history(self) -> None:
        try:
            self.settings.setValue("plotter_history", json.dumps(self._plotter_history))
        except Exception:
            pass

    def _ordered_plotter_names(self) -> list[str]:
        names: set[str] = set(self._plugin_factories.keys())
        names.update(self._plugin_instances.keys())
        if not names:
            return []

        history_rank = {name: index for index, name in enumerate(self._plotter_history)}
        alphabetical = {name: idx for idx, name in enumerate(sorted(names, key=str.lower))}
        offset = len(history_rank)

        def sort_key(name: str) -> tuple[int, int]:
            if name in history_rank:
                return (history_rank[name], alphabetical.get(name, 0))
            return (offset + alphabetical.get(name, 0), alphabetical.get(name, 0))

        return sorted(names, key=sort_key)

    def _setup_script_toolbar(self) -> None:  # type: ignore[override]
        toolbar = QtWidgets.QToolBar("Plugin", self)
        toolbar.setObjectName("mw_plugin_toolbar")
        self._configure_toolbar(toolbar)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        self._script_toolbar = toolbar

        plugin_label = QtWidgets.QLabel("Plugin:", toolbar)
        plugin_label.setContentsMargins(4, 0, 6, 0)
        plugin_label.setMinimumHeight(self._toolbar_icon_size.height() + 4)
        toolbar.addWidget(plugin_label)

        combo = QtWidgets.QComboBox(toolbar)
        combo.setObjectName("mw_plugin_selector")
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setMinimumContentsLength(12)
        combo.setToolTip("Select a plugin")
        combo.currentIndexChanged.connect(lambda _: self._apply_selected_plotter())
        combo.setMinimumHeight(self._toolbar_icon_size.height() + 6)
        toolbar.addWidget(combo)
        self._plotter_combo = combo
        self._refresh_plotter_combo()

        toolbar.addSeparator()

        load_action = toolbar.addAction("Load data")
        load_action.setEnabled(False)
        load_action.triggered.connect(self._load_data)
        self.load_data_button = load_action

        generate_action = toolbar.addAction("Generate plots")
        generate_action.setEnabled(False)
        generate_action.triggered.connect(self._generate_plots)
        self.plot_button = generate_action

        self._init_graph_settings_menu(toolbar)

    def _refresh_plotter_combo(self) -> None:
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        if combo is None:
            return
        current = self._current_plotter_name
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Select a plugin.", None)
        for name in self._ordered_plotter_names():
            combo.addItem(name, name)
        if current:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _remember_plotter_usage(self, name: str) -> None:
        if not isinstance(name, str) or not name:
            return
        history = [name]
        history.extend(entry for entry in self._plotter_history if entry != name)
        self._plotter_history = history[:20]
        self._save_plotter_history()
        self._refresh_plotter_combo()

    def _select_initial_plotter(self) -> None:
        if not self._plugin_factories:
            self._apply_selected_plotter()
            return
        target = self._initial_plotter if self._initial_plotter in self._plugin_factories else None
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        if combo is not None:
            combo.blockSignals(True)
            if target is None:
                combo.setCurrentIndex(0)
            else:
                index = combo.findData(target)
                if index < 0:
                    index = 0
                combo.setCurrentIndex(index)
            combo.blockSignals(False)
        if target is not None:
            self._apply_selected_plotter()
        else:
            self._current_plugin = None
            self._current_plotter_name = None
            self._set_script_panel(None)
            self._set_plugin_settings_widget(None)
            self._active_plugin_updater = None
            self._update_action_states()
        return
    def _dialog_start_directory(self) -> Path:
        if self._current_plotter_name:
            stored = self._plugin_last_directories.get(self._current_plotter_name)
            if stored is not None and stored.exists():
                return stored
        return super()._dialog_start_directory()

    # ------------------------------------------------------------------ Qt hooks
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self._current_plugin is not None:
            try:
                self._current_plugin.deactivate()
            except Exception:
                pass
        payload = {key: str(value) for key, value in self._plugin_last_directories.items()}
        self.settings.setValue("plugin_last_dirs", json.dumps(payload))
        self.settings.setValue("sources", self.path_edit.text())
        if self._last_directory is not None:
            self.settings.setValue("last_directory", str(self._last_directory))
        if self._last_graph_dir is not None:
            self.settings.setValue("last_graph_dir", str(self._last_graph_dir))
        else:
            self.settings.remove("last_graph_dir")
        self.settings.sync()
        super().closeEvent(event)

    # ------------------------------------------------------------------ project and data integration
    def _show_data_menu(self) -> bool:
        data_menu = getattr(self, "_data_menu", None)
        if not isinstance(data_menu, QtWidgets.QMenu):
            return False
        menu_bar = self.menuBar() if hasattr(self, "menuBar") else None
        if isinstance(menu_bar, QtWidgets.QMenuBar):
            action = data_menu.menuAction()
            try:
                geometry = menu_bar.actionGeometry(action)
            except Exception:
                geometry = QtCore.QRect()
            anchor_point = (
                geometry.bottomLeft()
                if geometry.isValid()
                else QtCore.QPoint(0, menu_bar.height())
            )
            try:
                global_pos = menu_bar.mapToGlobal(anchor_point)
            except Exception:
                global_pos = None
            if global_pos is not None:
                if action is not None:
                    try:
                        menu_bar.setActiveAction(action)
                    except Exception:
                        pass

                    def _clear_active_action() -> None:
                        try:
                            menu_bar.setActiveAction(None)
                        except Exception:
                            pass

                    connection_type = getattr(
                        QtCore.Qt.ConnectionType,
                        "SingleShotConnection",
                        QtCore.Qt.ConnectionType.AutoConnection,
                    )
                    data_menu.aboutToHide.connect(  # type: ignore[arg-type]
                        _clear_active_action,
                        connection_type,
                    )
                data_menu.popup(global_pos)
                return True
        try:
            fallback_pos = self.mapToGlobal(QtCore.QPoint(0, 0))
        except Exception:
            return False
        data_menu.popup(fallback_pos)
        return True

    def _commit_selected_paths(self, selection: list[Path]) -> None:
        self._selected_path_entries = list(selection)
        formatted = self._format_paths(selection)
        if hasattr(self, "path_edit"):
            try:
                self.path_edit.blockSignals(True)
            except Exception:
                pass
            self.path_edit.setText(formatted)
            try:
                self.path_edit.blockSignals(False)
            except Exception:
                pass
        self._remember_directory_from_paths(selection)

    def ensure_data_selection(
        self,
        plugin: PyPlotPlugin | None = None,
        *,
        warn_on_missing: bool = False,
    ) -> list[Path]:
        selection = [path for path in self._selected_paths() if path.is_file()]
        if selection:
            if len(selection) != len(self._selected_path_entries):
                self._commit_selected_paths(selection)
            return selection

        self._sync_selected_paths_with_imports()
        selection = [path for path in self._selected_paths() if path.is_file()]
        if selection:
            return selection

        sources: list[Path] = []
        for workbook in self._workbooks.values():
            source = getattr(workbook, "source", None)
            if isinstance(source, Path) and source.exists() and source.is_file():
                sources.append(source)
        if sources:
            unique_sources: list[Path] = []
            seen: set[str] = set()
            for source in sources:
                try:
                    resolved = source.resolve()
                except Exception:
                    resolved = source
                key = str(resolved)
                if key in seen:
                    continue
                seen.add(key)
                unique_sources.append(resolved)
            if unique_sources:
                self._commit_selected_paths(unique_sources)
                self._sync_selected_paths_with_imports()
                return [path for path in self._selected_paths() if path.is_file()]

        if self._show_data_menu():
            return []

        if warn_on_missing:
            title = plugin.name if isinstance(plugin, PyPlotPlugin) else "PyPlot"
            QtWidgets.QMessageBox.information(
                self,
                title,
                "Import data via the Data menu before loading it in this plugin.",
            )
        return []

    def _load_data(self) -> None:
        if self._current_plugin is None:
            QtWidgets.QMessageBox.information(
                self,
                "PyPlot",
                "Select a plugin before loading data.",
            )
            return
        plugin = self._current_plugin
        requires_data = bool(getattr(plugin, "requires_imported_data", False))
        if requires_data:
            selection = self.ensure_data_selection(plugin, warn_on_missing=True)
            if not selection:
                return
        plugin.load_data()
        self._update_action_states()

    def _update_action_states(self) -> None:
        if self._current_plugin is not None:
            try:
                self._current_plugin.update_ui()
            except Exception:
                pass
            return
        if hasattr(self, "load_data_button"):
            self.load_data_button.setEnabled(False)
        if hasattr(self, "plot_button"):
            self.plot_button.setEnabled(False)
            self.plot_button.setText("Generate")
        if hasattr(self, "popout_button"):
            self.popout_button.setEnabled(False)
        if hasattr(self, "save_graph_button"):
            self.save_graph_button.setEnabled(False)
        if hasattr(self, "normalize_button"):
            self.normalize_button.setEnabled(False)
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(False)
        if hasattr(self, "open_origin_button"):
            self.open_origin_button.setEnabled(False)

    def _import_paths(self, paths: Iterable[Path]) -> None:
        super()._import_paths(paths)
        self._sync_selected_paths_with_imports()
        if self._current_plotter_name and self._last_directory is not None:
            self._plugin_last_directories[self._current_plotter_name] = self._last_directory
            payload = {key: str(value) for key, value in self._plugin_last_directories.items()}
            self.settings.setValue("plugin_last_dirs", json.dumps(payload))
        self._update_action_states()
        self._update_project_actions()
        self.settings.setValue("sources", self.path_edit.text())
        self.settings.sync()
    def _has_project_data_to_save(self) -> bool:
        measurements = getattr(self, "measurements", None)
        if isinstance(measurements, list) and measurements:
            return True
        return bool(self._worksheets)

    def _reset_project_state(self) -> None:
        super()._reset_project_state()
        self._clear_imported_data()
        self._selected_path_entries = []
        self.path_edit.clear()
        self._update_action_states()
        self._update_project_actions()

    def _build_project_payload(self, *, base_path: Path | None) -> Dict[str, Any]:
        selected_payload = [
            self._portable_path(path, base_path) for path in self._selected_path_entries
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
        self._selected_path_entries = []
        if isinstance(selected_payload, list):
            for entry in selected_payload:
                if isinstance(entry, str) and entry:
                    resolved = self._resolve_portable_path(entry, project_dir)
                    if resolved is not None:
                        self._selected_path_entries.append(resolved)
        if self._selected_path_entries:
            self.path_edit.setText(self._format_paths(self._selected_path_entries))
            self._remember_directory_from_paths(self._selected_path_entries)
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
        self._sync_selected_paths_with_imports()
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
        self._selected_path_entries = [Path(entry) for entry in paths]
        self._remember_directory_from_paths(self._selected_path_entries)
        self.path_edit.setText(self._format_paths(self._selected_path_entries))
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
        self._selected_path_entries = [folder]
        self._last_directory = folder
        self.path_edit.setText(self._format_paths(self._selected_path_entries))
        self._update_action_states()

    def _generate_plots(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.generate()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Select a plugin before generating plots.",
        )
    def _open_matplotlib_window(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.open_matplotlib()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Select a plugin that supports Matplotlib export.",
        )
    def _save_current_graph(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.save_graph()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Plot a graph before saving.",
        )
    def _normalize_current_graph(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.normalize()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Plot a graph before normalizing.",
        )
    def _export_txt(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.export_txt()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Generate data before exporting.",
        )
    def _open_origin_prompt(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.open_origin()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Origin export is not available for the selected plugin.",
        )

    def _populate_graph_settings(self, layout: QtWidgets.QVBoxLayout) -> None:
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        panel = layout.parentWidget()
        if isinstance(panel, QtWidgets.QWidget):
            panel.setObjectName("mw_plugin_settings_panel")
            self._plugin_settings_panel = panel

        container = QtWidgets.QFrame(panel or self)
        container.setObjectName("mw_plugin_settings_container")
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(8)
        layout.addWidget(container)

        self._plugin_settings_container = container
        self._plugin_settings_layout = container_layout

    def _apply_selected_plotter(self) -> None:
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        name = combo.currentData() if combo is not None else None
        if not name:
            if self._current_plugin is not None:
                self._current_plugin.deactivate()
            self._current_plugin = None
            self._current_plotter_name = None
            self._set_script_panel(None)
            self._set_plugin_settings_widget(None)
            self._active_plugin_updater = None
            self._update_window_title()
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
            last_dir = self._plugin_last_directories.get(name)
            if last_dir is not None:
                self._last_directory = last_dir if last_dir.exists() else self._last_directory

        self._active_plugin_updater = plugin.update_ui
        if self._active_plugin_updater is not None:
            try:
                self._active_plugin_updater()
            except Exception:
                pass
        self._remember_plotter_usage(name)
        self._update_window_title()
        self._update_action_states()

    def _create_new_pyplot_window(self) -> None:
        factories = dict(self._plugin_factories)
        initial = self._current_plotter_name
        window = PyPlotWorkbench(plotters=factories, initial_plotter=initial)
        window.show()
        self._spawned_windows.append(window)
        window.destroyed.connect(lambda *_w, ref=window: self._spawned_windows.remove(ref) if ref in self._spawned_windows else None)

    def _apply_path_text(self, text: str) -> None:
        paths = [Path(entry) for entry in self._iter_path_entries(text)]
        self._selected_path_entries = paths
        self._remember_directory_from_paths(paths)

    def _iter_path_entries(self, text: str) -> Iterable[str]:
        candidate = text.replace("\r\n", "\n").replace(";", "\n")
        for entry in candidate.split("\n"):
            cleaned = entry.strip().strip('"')
            if cleaned:
                yield cleaned

    def _format_paths(self, paths: Iterable[Path]) -> str:
        return "; ".join(str(path) for path in paths)

    def _selected_paths(self) -> List[Path]:
        return list(self._selected_path_entries)

    def _remember_directory_from_paths(self, paths: Iterable[Path]) -> None:
        last = None
        for path in paths:
            if path.is_dir():
                last = path
            elif path.exists():
                last = path.parent
        if last is not None:
            self._last_directory = last

    def _sync_selected_paths_with_imports(self) -> None:
        ordered: list[Path] = []
        seen: set[str] = set()

        def _push(candidate: Path | None) -> None:
            if not isinstance(candidate, Path):
                return
            if not candidate.exists():
                return
            if candidate.is_dir():
                return
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            key = str(resolved)
            if key in seen:
                return
            seen.add(key)
            ordered.append(resolved)

        for selected in self._selected_path_entries:
            _push(selected)

        for workbook in self._workbooks.values():
            _push(workbook.source)

        if ordered != self._selected_path_entries:
            self._selected_path_entries = ordered
            formatted = self._format_paths(ordered)
            if hasattr(self, "path_edit"):
                try:
                    self.path_edit.blockSignals(True)
                except Exception:
                    pass
                self.path_edit.setText(formatted)
                try:
                    self.path_edit.blockSignals(False)
                except Exception:
                    pass
        if ordered:
            self._remember_directory_from_paths(ordered)



    def _update_project_title(self) -> None:
        self._update_window_title()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self._current_plugin is not None:
            try:
                self._current_plugin.deactivate()
            except Exception:
                pass
        payload = {key: str(value) for key, value in self._plugin_last_directories.items()}
        self.settings.setValue("plugin_last_dirs", json.dumps(payload))
        self.settings.setValue("sources", self.path_edit.text())
        self.settings.sync()
        super().closeEvent(event)

def main(
    available_plotters: Dict[str, Callable[[], QtWidgets.QWidget | None]] | None = None,
    initial_plotter: str | None = None,
) -> QtWidgets.QWidget | None:
    """Entry-point used by the launcher."""

    plugin_factories: Dict[str, Callable[["PyPlotWorkbench"], PyPlotPlugin]] = {
        name: (lambda host, cls=cls, n=name: cls(host, n)) for name, cls in PLUGIN_CLASS_REGISTRY.items()
    }
    for name, launcher in sorted((available_plotters or {}).items()):
        plugin_cls = PLUGIN_CLASS_REGISTRY.get(name)
        if plugin_cls is not None:
            plugin_factories.setdefault(name, lambda host, cls=plugin_cls, n=name: cls(host, n))
        else:
            plugin_factories[name] = lambda host, l=launcher, n=name: ExternalPlotterPlugin(host, n, l)

    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    ensure_app_theme(app)
    window = PyPlotWorkbench(plotters=plugin_factories, initial_plotter=initial_plotter)
    window.show()
    if created_app:
        app.exec()
        return None
    return window

__all__ = [
    "PyPlotPlugin",
    "ExternalPlotterPlugin",
    "EmbeddedWidgetPlugin",
    "PLUGIN_CLASS_REGISTRY",
    "TemperatureDependencePlugin",
    "TemperatureSensitivityPlugin",
    "CurrentAnnealingPlugin",
    "VSMHysteresisPlugin",
    "StressDependencePlugin",
    "StressSensitivityPlugin",
    "HswLoadComparePlugin",
    "MaxionContinuousPlugin",
    "PdfPlotterPlugin",
    "HysteresisLoopsPlugin",
    "HswDistributionPlugin",
    "Strain3DPlotPlugin",
    "PyPlotWorkbench",
    "main",
]


if __name__ == "__main__":
    main()
