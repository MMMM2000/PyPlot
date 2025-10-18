from __future__ import annotations

import sys
import uuid
from dataclasses import asdict
import json
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
import inspect
import logging
import types

from PyQt6 import QtCore, QtGui, QtWidgets

import pandas as pd

from plotting.pyplot import (
    PyPlotWindow,
    WorksheetColumnMeta,
    WorksheetData,
    WorkbookData,
)
from plotting.utils import ensure_app_theme
from plotting.vsm_hysteresis_loops import VSMPlotter, _looks_like_vsm_name


class PyPlotPlugin:
    """Base plugin contract for PyPlot script integrations."""

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        self.host = host
        self.name = name
        self._settings_widget: QtWidgets.QWidget | None = None

    # Lifecycle ---------------------------------------------------------
    def activate(self) -> None:
        """Called when the plugin becomes active."""

    def deactivate(self) -> None:
        """Called when the plugin is deselected."""

    # UI helpers --------------------------------------------------------
    def panel_widget(self) -> QtWidgets.QWidget | None:
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QtWidgets.QLabel("Script-specific controls will appear here once implemented.")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        return container

    def settings_widget(self) -> QtWidgets.QWidget:
        if self._settings_widget is None:
            container = QtWidgets.QWidget(self.host)
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            label = QtWidgets.QLabel("No additional settings are exposed for this script yet.")
            label.setWordWrap(True)
            layout.addWidget(label)
            layout.addStretch(1)
            self._settings_widget = container
        return self._settings_widget

    # Host actions ------------------------------------------------------
    def load_data(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "This script does not provide a load handler yet.",
        )

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


class VSMHysteresisPlugin(PyPlotPlugin):
    """PyPlot plugin wrapper around :class:`VSMPlotter`."""

    _METHOD_EXCLUDES = {"__init__", "_selected_paths", "_create_dock_widget", "_create_dock_switcher"}

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name)
        self._initialized = False
        self._menus_ready = False
        self._summary_label: QtWidgets.QLabel | None = None
        self._settings_loaded = False
        self._controls_connected = False

    # Lifecycle -----------------------------------------------------
    def activate(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    # UI helpers ----------------------------------------------------
    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QtWidgets.QLabel(
            "Select one or more VSM hysteresis files and click Load data."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addStretch(1)
        self._summary_label = summary
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        if self._settings_widget is not None:
            return self._settings_widget

        host = self.host
        container = QtWidgets.QWidget(host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        axes_group = QtWidgets.QGroupBox("Axes and filters", container)
        axes_form = QtWidgets.QFormLayout(axes_group)
        axes_form.setContentsMargins(8, 8, 8, 8)
        axes_form.setSpacing(6)
        host.temperature_combo = QtWidgets.QComboBox(axes_group)
        host.temperature_combo.addItem("All temperatures", None)
        axes_form.addRow("Temperature", host.temperature_combo)

        host.x_axis_combo = QtWidgets.QComboBox(axes_group)
        host.y_axis_combo = QtWidgets.QComboBox(axes_group)
        axes_form.addRow("X axis", host.x_axis_combo)
        axes_form.addRow("Y axis", host.y_axis_combo)
        layout.addWidget(axes_group)

        appearance_group = QtWidgets.QGroupBox("Appearance", container)
        appearance_layout = QtWidgets.QVBoxLayout(appearance_group)
        appearance_layout.setContentsMargins(8, 8, 8, 8)
        appearance_layout.setSpacing(6)
        host.style_combo = QtWidgets.QComboBox(appearance_group)
        host.style_combo.addItem("Line", "line")
        host.style_combo.addItem("Line + symbols", "line_markers")
        appearance_layout.addWidget(QtWidgets.QLabel("Matplotlib style", appearance_group))
        appearance_layout.addWidget(host.style_combo)

        host.dark_mode_checkbox = QtWidgets.QCheckBox("Dark plot theme", appearance_group)
        host.dark_mode_checkbox.setToolTip(
            "Render Matplotlib plots using a dark background theme."
        )
        appearance_layout.addWidget(host.dark_mode_checkbox)

        host.field_direction_button = QtWidgets.QPushButton("Highlight field direction", appearance_group)
        host.field_direction_button.setCheckable(True)
        host.field_direction_button.setToolTip(
            "Use solid lines for increasing magnetic field and dashed lines for decreasing segments."
        )
        appearance_layout.addWidget(host.field_direction_button)
        layout.addWidget(appearance_group)

        overlay_group = QtWidgets.QGroupBox("Angle overlays", container)
        overlay_layout = QtWidgets.QVBoxLayout(overlay_group)
        overlay_layout.setContentsMargins(8, 8, 8, 8)
        overlay_layout.setSpacing(6)
        host.angle_overlay_list = QtWidgets.QListWidget(overlay_group)
        host.angle_overlay_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        overlay_layout.addWidget(host.angle_overlay_list, 1)
        overlay_hint = QtWidgets.QLabel(
            "Select rotations to compare across temperatures or when exporting overlays.",
            overlay_group,
        )
        overlay_hint.setWordWrap(True)
        overlay_layout.addWidget(overlay_hint)
        host.angle_overlay_button = QtWidgets.QPushButton(
            "Plot selected angles across temperatures", overlay_group
        )
        host.angle_overlay_button.setEnabled(False)
        overlay_layout.addWidget(host.angle_overlay_button)
        layout.addWidget(overlay_group, 1)

        metrics_group = QtWidgets.QGroupBox("Derived metrics", container)
        metrics_layout = QtWidgets.QVBoxLayout(metrics_group)
        metrics_layout.setContentsMargins(8, 8, 8, 8)
        metrics_layout.setSpacing(6)
        host.metrics_angle_button = QtWidgets.QPushButton("Plot metrics vs angle", metrics_group)
        host.metrics_angle_button.setEnabled(False)
        metrics_layout.addWidget(host.metrics_angle_button)
        host.metrics_temperature_button = QtWidgets.QPushButton(
            "Plot metrics vs temperature", metrics_group
        )
        host.metrics_temperature_button.setEnabled(False)
        metrics_layout.addWidget(host.metrics_temperature_button)
        layout.addWidget(metrics_group)

        layout.addStretch(1)
        self._settings_widget = container
        self._controls_connected = False
        return container

    def _connect_control_signals(self) -> None:
        if self._controls_connected:
            return
        host = self.host
        if hasattr(host, "x_axis_combo") and callable(getattr(host, "_store_axis_selection", None)):
            host.x_axis_combo.currentTextChanged.connect(lambda _: host._store_axis_selection())
        if hasattr(host, "y_axis_combo") and callable(getattr(host, "_store_axis_selection", None)):
            host.y_axis_combo.currentTextChanged.connect(lambda _: host._store_axis_selection())
        if hasattr(host, "style_combo") and callable(getattr(host, "_restyle_plots", None)):
            host.style_combo.currentIndexChanged.connect(lambda _: host._restyle_plots())
        if hasattr(host, "dark_mode_checkbox") and callable(getattr(host, "_restyle_plots", None)):
            host.dark_mode_checkbox.toggled.connect(lambda _: host._restyle_plots())
        if hasattr(host, "field_direction_button") and callable(
            getattr(host, "_handle_field_direction_toggle", None)
        ):
            host.field_direction_button.toggled.connect(host._handle_field_direction_toggle)
        if hasattr(host, "angle_overlay_list") and callable(
            getattr(host, "_update_overlay_button_state", None)
        ):
            host.angle_overlay_list.itemSelectionChanged.connect(host._update_overlay_button_state)
        if hasattr(host, "angle_overlay_button") and callable(
            getattr(host, "_plot_angle_overlays", None)
        ):
            host.angle_overlay_button.clicked.connect(host._plot_angle_overlays)
        if hasattr(host, "metrics_angle_button") and callable(
            getattr(host, "_plot_metrics_vs_angle", None)
        ):
            host.metrics_angle_button.clicked.connect(host._plot_metrics_vs_angle)
        if hasattr(host, "metrics_temperature_button") and callable(
            getattr(host, "_plot_metrics_vs_temperature", None)
        ):
            host.metrics_temperature_button.clicked.connect(host._plot_metrics_vs_temperature)
        self._controls_connected = True

    # Host actions --------------------------------------------------
    def load_data(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        host = self.host
        paths = host._selected_paths()
        if not paths:
            imported = self._collect_imported_vsm_sources()
            if imported:
                formatted = host._format_paths(imported)
                host.path_edit.setText(formatted)
                host._apply_path_text(formatted)
                host._update_action_states()
                paths = host._selected_paths()
        if not paths:
            if not self._open_data_menu():
                try:
                    host._choose_files()
                except Exception:
                    host.logger.exception("Failed to open file dialog for VSM data selection")
            return
        host._load_measurements()

    def generate(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._generate_plots()

    def open_matplotlib(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._open_matplotlib_window()

    def save_graph(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._save_current_graph()

    def normalize(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._normalize_current_graph()

    def export_txt(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._export_txt()

    def open_origin(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        self.host._open_origin_prompt()

    # UI state ------------------------------------------------------
    def update_ui(self) -> None:  # type: ignore[override]
        self._ensure_initialized()
        host = self.host
        has_paths = bool(host._selected_paths())
        if hasattr(host, "load_data_button"):
            host.load_data_button.setEnabled(True)
        if self._summary_label is not None and not has_paths:
            self._summary_label.setText(
                "Select one or more VSM hysteresis files and click Load data."
            )
        if hasattr(host, "plot_button"):
            host.plot_button.setEnabled(has_paths or bool(host.path_edit.text().strip()))
            host.plot_button.setText("Generate VSM Hysteresis Loops")
        if hasattr(host, "_update_save_graph_enabled"):
            host._update_save_graph_enabled()
        if hasattr(host, "_update_normalize_enabled"):
            host._update_normalize_enabled()
        if hasattr(host, "_update_project_actions"):
            host._update_project_actions()

    # Internal helpers ---------------------------------------------
    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        host = self.host
        self.settings_widget()
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

        host.measurements = []
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
        host._last_source_dir = None
        host.last_export_path = None
        host._base_title = "VSM Hysteresis Loops"
        host.PROJECT_EXTENSION = VSMPlotter.PROJECT_EXTENSION
        host.PROJECT_VERSION = VSMPlotter.PROJECT_VERSION
        host.PROJECT_CODE = VSMPlotter.PROJECT_CODE
        host.PROJECT_SETTINGS_PREFIX = VSMPlotter.PROJECT_SETTINGS_PREFIX

        self._bind_methods()
        if hasattr(host, "_retabify_primary_docks"):
            try:
                host._retabify_primary_docks()
            except Exception:
                host.logger.exception("Failed to retabify primary docks")
        self._connect_control_signals()
        if not self._menus_ready:
            VSMPlotter._extend_menus(host, host.menuBar())
            self._menus_ready = True

        if not self._settings_loaded:
            try:
                previous = bool(getattr(host, "_suppress_window_persistence", False))
                host._suppress_window_persistence = True
                host._load_settings()
            except Exception:
                host.logger.exception("Failed to load saved VSM settings")
            else:
                self._settings_loaded = True
            finally:
                if not previous:
                    try:
                        delattr(host, "_suppress_window_persistence")
                    except AttributeError:
                        pass

        if hasattr(host, "_ensure_window_visibility"):
            try:
                host._ensure_window_visibility()
            except Exception:
                host.logger.exception("Failed to clamp PyPlot window to the active screen")

        self._initialized = True

    def _collect_imported_vsm_sources(self) -> List[Path]:
        host = self.host
        worksheets = getattr(host, "_worksheets", {})
        if not isinstance(worksheets, dict) or not worksheets:
            return []
        ordered: List[Path] = []
        seen: set[Path] = set()
        for worksheet in worksheets.values():
            source = getattr(worksheet, "source", None)
            if not isinstance(source, Path):
                continue
            if not _looks_like_vsm_name(source.name):
                continue
            if source in seen:
                continue
            seen.add(source)
            ordered.append(source)
        return ordered

    def _open_data_menu(self) -> bool:
        data_menu = getattr(self.host, "_data_menu", None)
        if not isinstance(data_menu, QtWidgets.QMenu):
            return False
        menu_bar = self.host.menuBar() if hasattr(self.host, "menuBar") else None
        global_pos: QtCore.QPoint
        action = data_menu.menuAction()
        if isinstance(menu_bar, QtWidgets.QMenuBar):
            rect = menu_bar.actionGeometry(action)
            anchor = rect.bottomLeft() if rect.isValid() else QtCore.QPoint(
                0, menu_bar.height()
            )
            global_pos = menu_bar.mapToGlobal(anchor)
            menu_bar.setActiveAction(action)
        else:
            button = getattr(self.host, "load_data_button", None)
            if isinstance(button, QtWidgets.QPushButton):
                global_pos = button.mapToGlobal(button.rect().bottomLeft())
            else:
                global_pos = self.host.mapToGlobal(QtCore.QPoint(0, 0))
        data_menu.popup(global_pos)
        return True

    def _bind_methods(self) -> None:
        host = self.host
        if getattr(host, "_vsm_methods_bound", False):
            return
        for name, func in inspect.getmembers(VSMPlotter, inspect.isfunction):
            if name in self._METHOD_EXCLUDES:
                continue
            setattr(host, name, types.MethodType(func, host))
        host._vsm_methods_bound = True

class PyPlotWorkbench(PyPlotWindow):
    """Lightweight harness for exercising shared PyPlotWindow features."""

    help_topic = "pyplot"
    PROJECT_EXTENSION = ".pypj"
    PROJECT_CODE = "pyplot"
    PROJECT_SETTINGS_PREFIX = "pyplot"

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
        self._plugin_settings_container: QtWidgets.QWidget | None = None
        self._plugin_settings_layout: QtWidgets.QVBoxLayout | None = None
        self._active_plugin_updater: Callable[[], None] | None = None
        self._initial_plotter = initial_plotter
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
        self.settings.sync()
        super().closeEvent(event)

    # ------------------------------------------------------------------ project and data integration
    def _load_data(self) -> None:
        if self._current_plugin is None:
            QtWidgets.QMessageBox.information(
                self,
                "PyPlot",
                "Select a plotting script before loading data.",
            )
            return
        self._current_plugin.load_data()
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
            "Select a plotting script before generating plots.",
        )
    def _open_matplotlib_window(self) -> None:
        if self._current_plugin is not None:
            self._current_plugin.open_matplotlib()
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Select a plotting script that supports Matplotlib export.",
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
        self._plotter_combo.addItem("Select a script…", None)
        if self._plugin_factories:
            for name in self._plugin_factories:
                self._plotter_combo.addItem(name, name)
            self._plotter_combo.currentIndexChanged.connect(lambda _: self._apply_selected_plotter())
        else:
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
        self._update_window_title()
        self._update_action_states()
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

    plugin_factories: Dict[str, Callable[["PyPlotWorkbench"], PyPlotPlugin]] = {}
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
    window = PyPlotWorkbench(plotters=plugin_factories, initial_plotter=initial_plotter)
    window.show()
    if created_app:
        app.exec()
        return None
    return window

if __name__ == "__main__":
    main()
