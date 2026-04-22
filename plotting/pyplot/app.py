from __future__ import annotations

import ast
import math
import sys
import uuid
from dataclasses import asdict
import json
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
import logging
import time

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib import ticker as mticker
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

import numpy as np
import pandas as pd

from .window import (
    GraphLineState,
    PlotFigureCanvas,
    PyPlotWindow,
    WorksheetColumnMeta,
    WorksheetData,
    WorkbookData,
    TabDescriptor,
    create_plot_tab_container,
    create_toolbar_section,
)
from plotting.shared.utils import (
    ensure_app_theme,
    get_last_used_dir,
    set_last_used_dir,
    install_standard_menu,
)
from plotting.plugins import (
    PyPlotPlugin,
    ExternalPlotterPlugin,
    EmbeddedWidgetPlugin,
    builtin_plugin_registry,
)

LOGGER = logging.getLogger(__name__)

MM_PER_INCH = 25.4
DEFAULT_PAPER_WIDTH_MM = 85.0
DEFAULT_FIGURE_ASPECT_RATIO = 1.5


def _builtin_plugin_factories() -> Dict[str, Callable[["PyPlotWorkbench"], PyPlotPlugin]]:
    """Create factories for each registered built-in plugin class."""

    factories: Dict[str, Callable[["PyPlotWorkbench"], PyPlotPlugin]] = {}
    for name, cls in builtin_plugin_registry().items():
        factories[name] = lambda host, cls=cls, n=name: cls(host, n)
    return factories


class PyPlotWorkbench(PyPlotWindow):
    """Lightweight harness for exercising shared PyPlotWindow features."""

    help_topic = "pyplot"
    PROJECT_EXTENSION = ".pypj"
    PROJECT_CODE = "pyplot"
    PROJECT_SETTINGS_PREFIX = "pyplot"
    GRAPH_DOCK_ENABLED = False
    PLUGIN_SHARED_STATE_KEY = "__pyplot_shared__"
    GRAPH_OPTION_DEFAULTS: Dict[str, Any] = {
        "show_grid": False,
        "show_legend": True,
        "title_font": 16,
        "label_font": 12,
        "tick_font": 10,
        "line_width": 1.5,
        "marker_size": 6.0,
        "figure_width": DEFAULT_PAPER_WIDTH_MM / MM_PER_INCH,
        "figure_height": (DEFAULT_PAPER_WIDTH_MM / MM_PER_INCH) / DEFAULT_FIGURE_ASPECT_RATIO,
        "figure_width_auto": False,
        "figure_height_auto": True,
        "figure_aspect_mode": "auto",
        "figure_aspect_ratio": DEFAULT_FIGURE_ASPECT_RATIO,
        "legend_location": "best",
        "legend_orientation": "auto",
        "legend_font_size": 10,
        "legend_columns": 1,
        "legend_show_symbols": True,
        "legend_text_follow_colors": True,
        "legend_draggable": True,
    }

    def __init__(
        self,
        *,
        plotters: Dict[str, Callable[["PyPlotWorkbench"], PyPlotPlugin]] | None = None,
        initial_plotter: str | None = None,
    ) -> None:
        self.settings = QtCore.QSettings("MicrowireLab", "PyPlotWorkbench")
        self._shared_settings = self.settings
        raw_dirs = self.settings.value("plugin_last_dirs", "{}")
        try:
            parsed_dirs = json.loads(raw_dirs) if isinstance(raw_dirs, str) else {}
        except json.JSONDecodeError:
            parsed_dirs = {}
        self._plugin_last_directories: Dict[str, Path] = {
            key: Path(value) for key, value in parsed_dirs.items() if isinstance(value, str)
        }
        raw_export_dirs = self.settings.value("plugin_last_export_dirs", "{}")
        try:
            parsed_export_dirs = json.loads(raw_export_dirs) if isinstance(raw_export_dirs, str) else {}
        except json.JSONDecodeError:
            parsed_export_dirs = {}
        self._plugin_last_export_dirs: Dict[str, Path] = {
            key: Path(value) for key, value in parsed_export_dirs.items() if isinstance(value, str)
        }
        self._last_directory: Path | None = None
        self._last_source_dir: Path | None = None
        self._selected_path_entries: List[Path] = []
        combined_factories = _builtin_plugin_factories()
        if plotters:
            combined_factories.update(plotters)
        self._plugin_factories: Dict[
            str, Callable[["PyPlotWorkbench"], PyPlotPlugin]
        ] = dict(sorted(combined_factories.items()))
        self._plugin_instances: Dict[str, PyPlotPlugin] = {}
        self._current_plugin: PyPlotPlugin | None = None
        self._current_plotter_name: str | None = None
        self._plotter_combo: QtWidgets.QComboBox | None = None
        self._plot_scope_combo: QtWidgets.QComboBox | None = None
        self._active_plugin_updater: Callable[[], None] | None = None
        self._initial_plotter = initial_plotter
        self._plotter_history: list[str] = self._load_plotter_history()
        self._spawned_windows: list[PyPlotWorkbench] = []
        self._last_graph_dir: Path | None = None
        self._graph_format_controls: Dict[str, QtWidgets.QWidget] = {}
        self._graph_format_updating = False
        self._graph_format_anchor_section: QtWidgets.QWidget | None = None
        self._graph_format_dialog: QtWidgets.QDialog | None = None
        self._graph_format_dialog_container: QtWidgets.QWidget | None = None
        self._graph_format_dialog_layout: QtWidgets.QVBoxLayout | None = None
        self._graph_format_dialog_root_layout: QtWidgets.QVBoxLayout | None = None
        self._graph_options_action: QtGui.QAction | None = None
        self._graph_option_defaults_global: Dict[str, Any] = {}
        self._graph_option_defaults_by_plugin: Dict[str, Dict[str, Any]] = {}
        self._plot_request_mode: str | None = None
        self._plot_request_paths_snapshot: List[Path] | None = None
        super().__init__(title="PyPlot")
        self.setObjectName("PyPlotWorkbench")
        if not sys.platform.startswith("win") and sys.platform != "darwin":
            try:
                self.setWindowState(
                    self.windowState() | QtCore.Qt.WindowState.WindowMaximized
                )
            except Exception:
                pass
        self.tab_widget.currentChanged.connect(lambda _: self._update_action_states())
        self.tab_widget.currentChanged.connect(
            lambda _: self._sync_graph_format_controls_from_current_axes()
        )

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
        stored_graph_format = self.settings.value("last_graph_format", ".png")
        if isinstance(stored_graph_format, str):
            token = stored_graph_format.strip().lower()
        else:
            token = ".png"
        if token not in {".png", ".pdf", ".svg"}:
            token = ".png"
        self._last_graph_format = token
        self._load_graph_option_settings()

        self._update_action_states()
        self._set_data_sources_visible(False)
        self._select_initial_plotter()
        self._update_window_title()
        QtCore.QTimer.singleShot(0, self._show_primary_docks)
        QtCore.QTimer.singleShot(0, self._sync_graph_format_controls_from_current_axes)

    def _shared_qsettings(self) -> QtCore.QSettings | None:
        settings = getattr(self, "_shared_settings", None)
        if isinstance(settings, QtCore.QSettings):
            return settings
        fallback = getattr(self, "settings", None)
        return fallback if isinstance(fallback, QtCore.QSettings) else None


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

    def _show_primary_docks(self) -> None:
        for dock in (
            getattr(self, "project_dock", None),
            getattr(self, "object_dock", None),
        ):
            if not isinstance(dock, QtWidgets.QDockWidget):
                continue
            try:
                dock.show()
                dock.raise_()
            except Exception:
                pass
        self._refresh_primary_dock_layout()
        try:
            QtCore.QTimer.singleShot(50, self._refresh_primary_dock_layout)
            QtCore.QTimer.singleShot(200, self._refresh_primary_dock_layout)
            QtCore.QTimer.singleShot(500, self._refresh_primary_dock_layout)
        except Exception:
            pass

    def _register_plot_tab(
        self,
        tab: QtWidgets.QWidget,
        canvas: Any,
        axes: Any,
        descriptor: TabDescriptor | None = None,
    ) -> None:
        super()._register_plot_tab(tab, canvas, axes, descriptor)
        plugin_name = self._tab_plugin_name(descriptor)
        self._apply_graph_options_to_axes(
            axes,
            plugin_name=plugin_name,
            adjust_subwindow=False,
            preserve_figure_size=bool(descriptor is not None and descriptor.kind == "layout_graph"),
        )

    def _load_plotter_history(self) -> list[str]:
        settings = self._shared_qsettings()
        stored = settings.value("plotter_history", "[]") if settings is not None else "[]"
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
        settings = self._shared_qsettings()
        if settings is None:
            return
        try:
            settings.setValue("plotter_history", json.dumps(self._plotter_history))
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

        plot_action = toolbar.addAction("Plot graphs")
        plot_action.setEnabled(False)
        plot_action.triggered.connect(self._generate_plots)
        self.plot_button = plot_action
        self._style_toolbar_button(toolbar, plot_action, object_name="mw_plot_action")

        plot_scope_combo = QtWidgets.QComboBox(toolbar)
        plot_scope_combo.setObjectName("mw_plot_scope_combo")
        plot_scope_combo.addItem("Replot all", "all")
        plot_scope_combo.addItem("Plot new", "new")
        plot_scope_combo.setToolTip("Choose whether to regenerate every graph or only graph newly imported files.")
        toolbar.addWidget(plot_scope_combo)
        self._plot_scope_combo = plot_scope_combo

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

    def _requested_plot_scope(self) -> str:
        combo = self._plot_scope_combo
        if isinstance(combo, QtWidgets.QComboBox):
            token = combo.currentData()
            if isinstance(token, str) and token in {"all", "new"}:
                return token
        return "all"

    def _recent_new_plot_paths(self) -> List[Path]:
        return [
            path for path in getattr(self, "_pending_new_plot_paths", [])
            if isinstance(path, Path) and path.exists() and path.is_file()
        ]

    def _plot_request_paths(self) -> List[Path]:
        if isinstance(self._plot_request_paths_snapshot, list):
            return list(self._plot_request_paths_snapshot)
        return list(self._selected_paths())

    def _plot_request_is_incremental(self) -> bool:
        return str(self._plot_request_mode or "").strip().lower() == "new"
    def _dialog_start_directory(self) -> Path:
        if self._current_plotter_name:
            stored = self._plugin_last_directories.get(self._current_plotter_name)
            if stored is not None and stored.exists():
                return stored
        return self._project_dialog_start_directory()

    def _sync_plugin_directory_settings(self) -> None:
        settings = self._shared_qsettings()
        if settings is None:
            return
        try:
            import_payload = {key: str(value) for key, value in self._plugin_last_directories.items()}
            settings.setValue("plugin_last_dirs", json.dumps(import_payload))
        except Exception:
            pass
        try:
            export_dirs = getattr(self, "_plugin_last_export_dirs", {})
            export_payload = {
                key: str(value) for key, value in export_dirs.items() if isinstance(value, Path)
            }
            settings.setValue("plugin_last_export_dirs", json.dumps(export_payload))
        except Exception:
            pass
        try:
            settings.sync()
        except Exception:
            pass

    def _ensure_settings_menu(self) -> None:  # type: ignore[override]
        super()._ensure_settings_menu()
        menu_bar = self.menuBar() if hasattr(self, "menuBar") else None
        if not isinstance(menu_bar, QtWidgets.QMenuBar):
            return
        settings_menu: QtWidgets.QMenu | None = None
        for action in menu_bar.actions():
            menu = action.menu()
            if isinstance(menu, QtWidgets.QMenu) and menu.title().replace("&", "").strip().lower() == "settings":
                settings_menu = menu
                break
        if settings_menu is None:
            return
        if isinstance(self._graph_options_action, QtGui.QAction):
            return
        self._graph_options_action = settings_menu.addAction("Graph options...")
        self._graph_options_action.triggered.connect(self._open_graph_options_dialog)

    @staticmethod
    def _legend_location_choices() -> list[tuple[str, str]]:
        return [
            ("Best", "best"),
            ("Upper right", "upper right"),
            ("Upper left", "upper left"),
            ("Lower left", "lower left"),
            ("Lower right", "lower right"),
            ("Right", "right"),
            ("Center left", "center left"),
            ("Center right", "center right"),
            ("Lower center", "lower center"),
            ("Upper center", "upper center"),
            ("Center", "center"),
        ]

    @staticmethod
    def _legend_orientation_choices() -> list[tuple[str, str]]:
        return [
            ("Auto", "auto"),
            ("Horizontal", "horizontal"),
            ("Vertical", "vertical"),
        ]

    def _clean_graph_option_payload(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        defaults = dict(self.GRAPH_OPTION_DEFAULTS)
        source = payload if isinstance(payload, dict) else {}
        choices = {value for _, value in self._legend_location_choices()}
        legend_orientation_choices = {value for _, value in self._legend_orientation_choices()}
        figure_aspect_choices = {"auto", "custom"}
        cleaned: Dict[str, Any] = {}
        for key, default in defaults.items():
            value = source.get(key, default)
            if key == "figure_width" and "figure_width" not in source and "figure_width_mm" in source:
                try:
                    value = float(source.get("figure_width_mm")) / MM_PER_INCH
                except Exception:
                    value = default
            if key == "figure_height" and "figure_height" not in source and "figure_height_mm" in source:
                try:
                    value = float(source.get("figure_height_mm")) / MM_PER_INCH
                except Exception:
                    value = default
            if isinstance(default, bool):
                cleaned[key] = bool(value)
                continue
            if key == "legend_location":
                token = str(value).strip().lower()
                cleaned[key] = token if token in choices else str(default)
                continue
            if key == "legend_orientation":
                token = str(value).strip().lower()
                cleaned[key] = token if token in legend_orientation_choices else str(default)
                continue
            if key == "figure_aspect_mode":
                token = str(value).strip().lower()
                cleaned[key] = token if token in figure_aspect_choices else str(default)
                continue
            if isinstance(default, int):
                try:
                    parsed = int(round(float(value)))
                except Exception:
                    parsed = int(default)
                if key == "legend_columns":
                    parsed = max(1, min(parsed, 12))
                else:
                    parsed = max(6, min(parsed, 96))
                cleaned[key] = parsed
                continue
            if isinstance(default, float):
                try:
                    parsed = float(value)
                except Exception:
                    parsed = float(default)
                if not math.isfinite(parsed):
                    parsed = float(default)
                if key == "line_width":
                    parsed = max(0.1, min(parsed, 20.0))
                elif key == "marker_size":
                    parsed = max(0.1, min(parsed, 30.0))
                elif key == "figure_width":
                    parsed = max(0.5, min(parsed, 20.0))
                elif key == "figure_height":
                    parsed = max(0.5, min(parsed, 20.0))
                elif key == "figure_aspect_ratio":
                    parsed = max(0.2, min(parsed, 20.0))
                cleaned[key] = parsed
                continue
            cleaned[key] = value
        return cleaned

    def _load_graph_option_settings(self) -> None:
        settings = self._shared_qsettings()
        if settings is None:
            self._graph_option_defaults_global = self._clean_graph_option_payload({})
            self._graph_option_defaults_by_plugin = {}
            return

        raw_global = settings.value("graph_options_global", "{}")
        try:
            parsed_global = json.loads(raw_global) if isinstance(raw_global, str) else {}
        except Exception:
            parsed_global = {}
        self._graph_option_defaults_global = self._clean_graph_option_payload(parsed_global)

        raw_plugins = settings.value("graph_options_by_plugin", "{}")
        try:
            parsed_plugins = json.loads(raw_plugins) if isinstance(raw_plugins, str) else {}
        except Exception:
            parsed_plugins = {}
        cleaned_plugins: Dict[str, Dict[str, Any]] = {}
        if isinstance(parsed_plugins, dict):
            for name, payload in parsed_plugins.items():
                if not isinstance(name, str) or not name.strip() or not isinstance(payload, dict):
                    continue
                cleaned_plugins[name] = self._clean_graph_option_payload(payload)
        self._graph_option_defaults_by_plugin = cleaned_plugins

    def _save_graph_option_settings(self) -> None:
        settings = self._shared_qsettings()
        if settings is None:
            return
        try:
            settings.setValue("graph_options_global", json.dumps(self._graph_option_defaults_global))
            settings.setValue("graph_options_by_plugin", json.dumps(self._graph_option_defaults_by_plugin))
            settings.sync()
        except Exception:
            pass

    def _effective_graph_options(self, plugin_name: str | None) -> Dict[str, Any]:
        effective = self._clean_graph_option_payload(self._graph_option_defaults_global)
        if plugin_name:
            plugin = self._plugin_instances.get(plugin_name)
            if plugin is None and self._current_plotter_name == plugin_name:
                plugin = self._current_plugin
            if plugin is not None:
                plugin_defaults_getter = getattr(plugin, "graph_option_defaults", None)
                if callable(plugin_defaults_getter):
                    try:
                        plugin_defaults = plugin_defaults_getter()
                    except Exception:
                        plugin_defaults = None
                    if isinstance(plugin_defaults, dict):
                        merged = dict(effective)
                        merged.update(plugin_defaults)
                        effective = self._clean_graph_option_payload(merged)
            override = self._graph_option_defaults_by_plugin.get(plugin_name)
            if isinstance(override, dict):
                merged = dict(effective)
                merged.update(override)
                effective = self._clean_graph_option_payload(merged)
        return effective

    def _has_plugin_graph_option_override(self, plugin_name: str | None) -> bool:
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            return False
        override = self._graph_option_defaults_by_plugin.get(plugin_name)
        return isinstance(override, dict) and bool(override)

    def _set_graph_options_widgets(
        self,
        widgets: Dict[str, QtWidgets.QWidget],
        payload: Dict[str, Any],
    ) -> None:
        options = self._clean_graph_option_payload(payload)
        for key, value in options.items():
            widget = widgets.get(key)
            if isinstance(widget, QtWidgets.QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QtWidgets.QComboBox):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QtWidgets.QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QtWidgets.QDoubleSpinBox):
                if key in {"figure_width", "figure_height"}:
                    widget.setValue(float(value) * MM_PER_INCH)
                else:
                    widget.setValue(float(value))
        self._sync_graph_option_figure_controls(widgets)

    def _graph_options_from_widgets(
        self,
        widgets: Dict[str, QtWidgets.QWidget],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for key in self.GRAPH_OPTION_DEFAULTS:
            widget = widgets.get(key)
            if isinstance(widget, QtWidgets.QCheckBox):
                payload[key] = bool(widget.isChecked())
            elif isinstance(widget, QtWidgets.QComboBox):
                payload[key] = str(widget.currentData() or "")
            elif isinstance(widget, QtWidgets.QSpinBox):
                payload[key] = int(widget.value())
            elif isinstance(widget, QtWidgets.QDoubleSpinBox):
                if key in {"figure_width", "figure_height"}:
                    payload[key] = float(widget.value()) / MM_PER_INCH
                else:
                    payload[key] = float(widget.value())
        return self._clean_graph_option_payload(payload)

    @staticmethod
    def _sync_graph_option_figure_controls(widgets: Dict[str, QtWidgets.QWidget]) -> None:
        width_spin = widgets.get("figure_width")
        height_spin = widgets.get("figure_height")
        width_auto = widgets.get("figure_width_auto")
        height_auto = widgets.get("figure_height_auto")
        figure_aspect_mode = widgets.get("figure_aspect_mode")
        figure_aspect_ratio = widgets.get("figure_aspect_ratio")
        if isinstance(width_spin, QtWidgets.QDoubleSpinBox) and isinstance(width_auto, QtWidgets.QCheckBox):
            width_spin.setEnabled(not width_auto.isChecked())
        if isinstance(height_spin, QtWidgets.QDoubleSpinBox) and isinstance(height_auto, QtWidgets.QCheckBox):
            height_spin.setEnabled(not height_auto.isChecked())
        mode = (
            str(figure_aspect_mode.currentData())
            if isinstance(figure_aspect_mode, QtWidgets.QComboBox)
            else "auto"
        )
        if isinstance(figure_aspect_ratio, QtWidgets.QDoubleSpinBox):
            figure_aspect_ratio.setEnabled(mode == "custom")

    def _build_graph_options_widgets(
        self,
        parent: QtWidgets.QWidget,
    ) -> Dict[str, QtWidgets.QWidget]:
        form = QtWidgets.QFormLayout(parent)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        show_grid_cb = QtWidgets.QCheckBox("Show grid by default", parent)
        show_legend_cb = QtWidgets.QCheckBox("Show legend by default", parent)
        form.addRow(show_grid_cb)
        form.addRow(show_legend_cb)

        title_font_spin = QtWidgets.QSpinBox(parent)
        title_font_spin.setRange(6, 96)
        label_font_spin = QtWidgets.QSpinBox(parent)
        label_font_spin.setRange(6, 96)
        tick_font_spin = QtWidgets.QSpinBox(parent)
        tick_font_spin.setRange(6, 96)
        form.addRow("Title font:", title_font_spin)
        form.addRow("Label font:", label_font_spin)
        form.addRow("Tick label font:", tick_font_spin)

        line_width_spin = QtWidgets.QDoubleSpinBox(parent)
        line_width_spin.setRange(0.1, 20.0)
        line_width_spin.setSingleStep(0.1)
        marker_size_spin = QtWidgets.QDoubleSpinBox(parent)
        marker_size_spin.setRange(0.1, 30.0)
        marker_size_spin.setSingleStep(0.2)
        figure_width_spin = QtWidgets.QDoubleSpinBox(parent)
        figure_width_spin.setRange(10.0, 600.0)
        figure_width_spin.setSingleStep(1.0)
        figure_width_spin.setDecimals(1)
        figure_height_spin = QtWidgets.QDoubleSpinBox(parent)
        figure_height_spin.setRange(10.0, 600.0)
        figure_height_spin.setSingleStep(1.0)
        figure_height_spin.setDecimals(1)
        figure_width_auto_cb = QtWidgets.QCheckBox("auto", parent)
        figure_height_auto_cb = QtWidgets.QCheckBox("auto", parent)
        figure_aspect_mode_combo = QtWidgets.QComboBox(parent)
        figure_aspect_mode_combo.addItem("Auto", "auto")
        figure_aspect_mode_combo.addItem("Custom", "custom")
        figure_aspect_ratio_spin = QtWidgets.QDoubleSpinBox(parent)
        figure_aspect_ratio_spin.setRange(0.2, 20.0)
        figure_aspect_ratio_spin.setSingleStep(0.05)
        figure_aspect_ratio_spin.setDecimals(3)
        figure_aspect_ratio_spin.setValue(DEFAULT_FIGURE_ASPECT_RATIO)

        figure_width_row = QtWidgets.QWidget(parent)
        figure_width_layout = QtWidgets.QHBoxLayout(figure_width_row)
        figure_width_layout.setContentsMargins(0, 0, 0, 0)
        figure_width_layout.setSpacing(6)
        figure_width_layout.addWidget(figure_width_spin, 1)
        figure_width_layout.addWidget(figure_width_auto_cb, 0)

        figure_height_row = QtWidgets.QWidget(parent)
        figure_height_layout = QtWidgets.QHBoxLayout(figure_height_row)
        figure_height_layout.setContentsMargins(0, 0, 0, 0)
        figure_height_layout.setSpacing(6)
        figure_height_layout.addWidget(figure_height_spin, 1)
        figure_height_layout.addWidget(figure_height_auto_cb, 0)

        form.addRow("Line width:", line_width_spin)
        form.addRow("Marker size:", marker_size_spin)
        form.addRow("Figure width (mm):", figure_width_row)
        form.addRow("Figure height (mm):", figure_height_row)
        form.addRow("Figure aspect:", figure_aspect_mode_combo)
        form.addRow("Aspect ratio (W/H):", figure_aspect_ratio_spin)

        legend_location_combo = QtWidgets.QComboBox(parent)
        for label, token in self._legend_location_choices():
            legend_location_combo.addItem(label, token)
        legend_orientation_combo = QtWidgets.QComboBox(parent)
        for label, token in self._legend_orientation_choices():
            legend_orientation_combo.addItem(label, token)
        legend_font_size_spin = QtWidgets.QSpinBox(parent)
        legend_font_size_spin.setRange(6, 96)
        legend_columns_spin = QtWidgets.QSpinBox(parent)
        legend_columns_spin.setRange(1, 12)
        legend_show_symbols_cb = QtWidgets.QCheckBox("Show legend symbols", parent)
        legend_text_follow_colors_cb = QtWidgets.QCheckBox(
            "Legend text follows line colours",
            parent,
        )
        legend_draggable_cb = QtWidgets.QCheckBox("Legend draggable", parent)
        form.addRow("Legend location:", legend_location_combo)
        form.addRow("Legend orientation:", legend_orientation_combo)
        form.addRow("Legend font size:", legend_font_size_spin)
        form.addRow("Legend columns:", legend_columns_spin)
        form.addRow(legend_show_symbols_cb)
        form.addRow(legend_text_follow_colors_cb)
        form.addRow(legend_draggable_cb)

        widgets = {
            "show_grid": show_grid_cb,
            "show_legend": show_legend_cb,
            "title_font": title_font_spin,
            "label_font": label_font_spin,
            "tick_font": tick_font_spin,
            "line_width": line_width_spin,
            "marker_size": marker_size_spin,
            "figure_width": figure_width_spin,
            "figure_height": figure_height_spin,
            "figure_width_auto": figure_width_auto_cb,
            "figure_height_auto": figure_height_auto_cb,
            "figure_aspect_mode": figure_aspect_mode_combo,
            "figure_aspect_ratio": figure_aspect_ratio_spin,
            "legend_location": legend_location_combo,
            "legend_orientation": legend_orientation_combo,
            "legend_font_size": legend_font_size_spin,
            "legend_columns": legend_columns_spin,
            "legend_show_symbols": legend_show_symbols_cb,
            "legend_text_follow_colors": legend_text_follow_colors_cb,
            "legend_draggable": legend_draggable_cb,
        }
        figure_width_auto_cb.toggled.connect(
            lambda _checked=False, _widgets=widgets: self._sync_graph_option_figure_controls(_widgets)
        )
        figure_height_auto_cb.toggled.connect(
            lambda _checked=False, _widgets=widgets: self._sync_graph_option_figure_controls(_widgets)
        )
        figure_aspect_mode_combo.currentIndexChanged.connect(
            lambda _index=0, _widgets=widgets: self._sync_graph_option_figure_controls(_widgets)
        )
        self._sync_graph_option_figure_controls(widgets)
        return widgets

    def _apply_graph_option_defaults_to_controls(self, plugin_name: str | None = None) -> None:
        if not self._graph_format_controls:
            return
        options = self._effective_graph_options(plugin_name or self._current_plotter_name)
        control_key_map = {
            "show_grid": "show_grid_cb",
            "show_legend": "show_legend_cb",
            "title_font": "title_font_spin",
            "label_font": "label_font_spin",
            "tick_font": "tick_font_spin",
            "line_width": "line_width_spin",
            "marker_size": "marker_size_spin",
            "figure_width": "figure_width_spin",
            "figure_height": "figure_height_spin",
            "figure_width_auto": "figure_width_auto_cb",
            "figure_height_auto": "figure_height_auto_cb",
            "figure_aspect_mode": "figure_aspect_mode_combo",
            "figure_aspect_ratio": "figure_aspect_ratio_spin",
            "legend_location": "legend_location_combo",
            "legend_orientation": "legend_orientation_combo",
            "legend_font_size": "legend_font_spin",
            "legend_columns": "legend_columns_spin",
            "legend_show_symbols": "legend_show_symbols_cb",
            "legend_text_follow_colors": "legend_text_follow_colors_cb",
            "legend_draggable": "legend_draggable_cb",
        }
        mapped_widgets: Dict[str, QtWidgets.QWidget] = {}
        for option_key, control_key in control_key_map.items():
            widget = self._graph_format_controls.get(control_key)
            if isinstance(widget, QtWidgets.QWidget):
                mapped_widgets[option_key] = widget
        self._graph_format_updating = True
        try:
            self._set_graph_options_widgets(mapped_widgets, options)
        finally:
            self._graph_format_updating = False
        self._sync_tick_mode_inputs()
        self._sync_aspect_controls()

    def _apply_graph_options_to_all_open_graphs(self) -> int:
        applied = 0
        seen: set[int] = set()
        for axes in self._axes_by_tab.values():
            if axes is None:
                continue
            marker = id(axes)
            if marker in seen:
                continue
            seen.add(marker)
            plugin_name = self._plugin_name_for_axes(axes)
            self._apply_graph_options_to_axes(
                axes,
                plugin_name=plugin_name,
                adjust_subwindow=True,
            )
            applied += 1
        return applied

    def _store_graph_option_defaults(
        self,
        *,
        global_payload: Dict[str, Any],
        plugin_key: str,
        plugin_override_enabled: bool,
        plugin_payload: Dict[str, Any] | None,
        refresh_open_graphs: bool,
    ) -> None:
        self._graph_option_defaults_global = self._clean_graph_option_payload(global_payload)
        if plugin_key:
            if plugin_override_enabled and isinstance(plugin_payload, dict):
                self._graph_option_defaults_by_plugin[plugin_key] = self._clean_graph_option_payload(plugin_payload)
            else:
                self._graph_option_defaults_by_plugin.pop(plugin_key, None)
        self._save_graph_option_settings()
        self._apply_graph_option_defaults_to_controls(self._current_plotter_name)
        refreshed = self._apply_graph_options_to_all_open_graphs() if refresh_open_graphs else 0
        if refreshed > 0:
            self._append_log(f"Updated shared graph option defaults and refreshed {refreshed} open graph(s).")
        else:
            self._append_log("Updated shared graph option defaults.")

    def _open_graph_options_dialog(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Graph options")
        dialog.setModal(True)
        dialog.resize(520, 500)
        root_layout = QtWidgets.QVBoxLayout(dialog)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        tabs = QtWidgets.QTabWidget(dialog)
        root_layout.addWidget(tabs, 1)

        global_tab = QtWidgets.QWidget(tabs)
        global_layout = QtWidgets.QVBoxLayout(global_tab)
        global_layout.setContentsMargins(8, 8, 8, 8)
        global_layout.setSpacing(8)
        global_widgets_holder = QtWidgets.QWidget(global_tab)
        global_widgets = self._build_graph_options_widgets(global_widgets_holder)
        global_layout.addWidget(global_widgets_holder)
        global_layout.addStretch(1)
        tabs.addTab(global_tab, "Global defaults")
        self._set_graph_options_widgets(
            global_widgets,
            self._effective_graph_options(None),
        )

        plugin_tab = QtWidgets.QWidget(tabs)
        plugin_layout = QtWidgets.QVBoxLayout(plugin_tab)
        plugin_layout.setContentsMargins(8, 8, 8, 8)
        plugin_layout.setSpacing(8)
        plugin_selector = QtWidgets.QComboBox(plugin_tab)
        plugin_names = self._ordered_plotter_names()
        for name in plugin_names:
            plugin_selector.addItem(name, name)
        if self._current_plotter_name:
            idx = plugin_selector.findData(self._current_plotter_name)
            if idx >= 0:
                plugin_selector.setCurrentIndex(idx)
        plugin_layout.addWidget(plugin_selector)
        plugin_override_cb = QtWidgets.QCheckBox("Enable plugin override", plugin_tab)
        plugin_layout.addWidget(plugin_override_cb)
        plugin_widgets_holder = QtWidgets.QWidget(plugin_tab)
        plugin_widgets = self._build_graph_options_widgets(plugin_widgets_holder)
        plugin_layout.addWidget(plugin_widgets_holder)
        plugin_layout.addStretch(1)
        tabs.addTab(plugin_tab, "Plugin override")

        def _sync_plugin_editor() -> None:
            plugin_name = plugin_selector.currentData()
            plugin_key = str(plugin_name) if isinstance(plugin_name, str) else ""
            override = self._graph_option_defaults_by_plugin.get(plugin_key)
            has_override = isinstance(override, dict)
            plugin_override_cb.blockSignals(True)
            plugin_override_cb.setChecked(has_override)
            plugin_override_cb.blockSignals(False)
            payload = (
                override
                if has_override
                else self._effective_graph_options(plugin_key)
            )
            self._set_graph_options_widgets(plugin_widgets, payload)
            plugin_widgets_holder.setEnabled(plugin_override_cb.isChecked())

        plugin_selector.currentIndexChanged.connect(_sync_plugin_editor)
        plugin_override_cb.toggled.connect(plugin_widgets_holder.setEnabled)
        _sync_plugin_editor()

        buttons = QtWidgets.QDialogButtonBox(parent=dialog)
        apply_btn = buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.Apply)
        reset_btn = buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.RestoreDefaults)
        cancel_btn = buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        apply_btn.setDefault(True)
        cancel_btn.clicked.connect(dialog.reject)
        root_layout.addWidget(buttons, 0)

        def _apply_changes() -> None:
            plugin_name = plugin_selector.currentData()
            plugin_key = str(plugin_name) if isinstance(plugin_name, str) else ""
            global_payload = self._graph_options_from_widgets(global_widgets)
            plugin_payload = (
                self._graph_options_from_widgets(plugin_widgets)
                if plugin_override_cb.isChecked()
                else None
            )
            self._store_graph_option_defaults(
                global_payload=global_payload,
                plugin_key=plugin_key,
                plugin_override_enabled=bool(plugin_override_cb.isChecked()),
                plugin_payload=plugin_payload,
                refresh_open_graphs=True,
            )

        def _reset_defaults() -> None:
            defaults = self._clean_graph_option_payload(self.GRAPH_OPTION_DEFAULTS)
            self._set_graph_options_widgets(global_widgets, defaults)
            plugin_override_cb.blockSignals(True)
            plugin_override_cb.setChecked(False)
            plugin_override_cb.blockSignals(False)
            plugin_widgets_holder.setEnabled(False)
            self._set_graph_options_widgets(plugin_widgets, defaults)

        def _safe_apply() -> None:
            try:
                _apply_changes()
            except Exception as exc:
                LOGGER.exception("Failed to update shared graph option defaults")
                self._append_log(f"Failed to update shared graph option defaults: {exc}", level="error")
                QtWidgets.QMessageBox.warning(
                    self,
                    "Graph options",
                    f"Could not save graph options:\n{exc}",
                )

        apply_btn.clicked.connect(_safe_apply)
        reset_btn.clicked.connect(_reset_defaults)
        dialog.exec()

    @staticmethod
    def _figure_ratio_from_figure(figure: Any) -> float | None:
        if figure is None:
            return None
        try:
            size = figure.get_size_inches()
        except Exception:
            return None
        if size is None or not hasattr(size, "__len__") or len(size) < 2:
            return None
        try:
            width = float(size[0])
            height = float(size[1])
        except Exception:
            return None
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0.0 or height <= 0.0:
            return None
        return width / height

    def _resolve_figure_size_inches(
        self,
        *,
        figure: Any,
        width_in: float,
        height_in: float,
        width_auto: bool,
        height_auto: bool,
        aspect_mode: str,
        aspect_ratio: float,
    ) -> tuple[float, float]:
        fallback_width_in = float(DEFAULT_PAPER_WIDTH_MM / MM_PER_INCH)
        fallback_ratio = float(DEFAULT_FIGURE_ASPECT_RATIO)
        width = float(width_in) if math.isfinite(width_in) and width_in > 0.0 else fallback_width_in
        height = float(height_in) if math.isfinite(height_in) and height_in > 0.0 else fallback_width_in / fallback_ratio
        current_ratio = self._figure_ratio_from_figure(figure)
        ratio = (
            float(aspect_ratio)
            if str(aspect_mode).strip().lower() == "custom"
            and math.isfinite(aspect_ratio)
            and aspect_ratio > 0.0
            else (current_ratio if current_ratio is not None else fallback_ratio)
        )
        if not math.isfinite(ratio) or ratio <= 0.0:
            ratio = fallback_ratio

        if width_auto and height_auto:
            width = fallback_width_in
            height = width / ratio
        elif width_auto:
            width = max(0.5, height * ratio)
        elif height_auto:
            height = max(0.5, width / ratio)

        width = max(0.5, min(width, 20.0))
        height = max(0.5, min(height, 20.0))
        return width, height

    def _apply_graph_options_to_axes(
        self,
        axes: Any,
        *,
        plugin_name: str | None,
        adjust_subwindow: bool = True,
        preserve_figure_size: bool = False,
    ) -> None:
        if axes is None:
            return
        options = self._effective_graph_options(plugin_name)
        figure = getattr(axes, "figure", None)
        try:
            figure_width = float(options.get("figure_width", self.GRAPH_OPTION_DEFAULTS["figure_width"]))
        except Exception:
            figure_width = float(self.GRAPH_OPTION_DEFAULTS["figure_width"])
        try:
            figure_height = float(options.get("figure_height", self.GRAPH_OPTION_DEFAULTS["figure_height"]))
        except Exception:
            figure_height = float(self.GRAPH_OPTION_DEFAULTS["figure_height"])
        figure_width_auto = bool(options.get("figure_width_auto", self.GRAPH_OPTION_DEFAULTS["figure_width_auto"]))
        figure_height_auto = bool(options.get("figure_height_auto", self.GRAPH_OPTION_DEFAULTS["figure_height_auto"]))
        figure_aspect_mode = str(options.get("figure_aspect_mode", self.GRAPH_OPTION_DEFAULTS["figure_aspect_mode"]))
        try:
            figure_aspect_ratio = float(
                options.get("figure_aspect_ratio", self.GRAPH_OPTION_DEFAULTS["figure_aspect_ratio"])
            )
        except Exception:
            figure_aspect_ratio = float(self.GRAPH_OPTION_DEFAULTS["figure_aspect_ratio"])
        if preserve_figure_size and figure is not None:
            try:
                existing_size = figure.get_size_inches()
                figure_width = float(existing_size[0])
                figure_height = float(existing_size[1])
                figure_width_auto = False
                figure_height_auto = False
            except Exception:
                preserve_figure_size = False
        if not preserve_figure_size:
            figure_width, figure_height = self._resolve_figure_size_inches(
                figure=figure,
                width_in=figure_width,
                height_in=figure_height,
                width_auto=figure_width_auto,
                height_auto=figure_height_auto,
                aspect_mode=figure_aspect_mode,
                aspect_ratio=figure_aspect_ratio,
            )
        if figure is not None:
            try:
                figure.set_size_inches(
                    figure_width,
                    figure_height,
                    forward=True,
                )
            except Exception:
                pass
            sync_display_reference = getattr(self, "_sync_canvas_display_reference", None)
            if callable(sync_display_reference):
                try:
                    sync_display_reference(
                        axes=axes,
                        width_in=figure_width,
                        height_in=figure_height,
                    )
                except Exception:
                    pass
        targets: list[Any]
        if figure is not None:
            try:
                targets = [candidate for candidate in figure.axes if candidate is not None]
            except Exception:
                targets = [axes]
        else:
            targets = [axes]

        for target_axes in targets:
            try:
                target_axes.grid(bool(options["show_grid"]))
            except Exception:
                pass
            try:
                target_axes.title.set_fontsize(float(options["title_font"]))
            except Exception:
                pass
            try:
                target_axes.xaxis.label.set_fontsize(float(options["label_font"]))
                target_axes.yaxis.label.set_fontsize(float(options["label_font"]))
            except Exception:
                pass
            try:
                target_axes.tick_params(
                    axis="both",
                    which="both",
                    labelsize=float(options["tick_font"]),
                )
            except Exception:
                pass
            try:
                for line in target_axes.get_lines():
                    try:
                        line.set_linewidth(float(options["line_width"]))
                    except Exception:
                        pass
                    try:
                        marker = str(line.get_marker()).strip().lower()
                    except Exception:
                        marker = ""
                    if marker and marker != "none":
                        try:
                            line.set_markersize(float(options["marker_size"]))
                        except Exception:
                            pass
            except Exception:
                pass

            show_legend = bool(options["show_legend"])
            if show_legend:
                legend = self._sync_axes_legend_with_visible_lines(
                    target_axes,
                    plugin_name=plugin_name,
                )
                if legend is not None:
                    state = self._legend_state_snapshot(legend, plugin_name=plugin_name)
                    state.update(
                        {
                            "visible": True,
                            "loc": str(options["legend_location"]),
                            "orientation": str(options["legend_orientation"]),
                            "font_size": float(options["legend_font_size"]),
                            "ncol": int(options["legend_columns"]),
                            "show_symbols": bool(options["legend_show_symbols"]),
                            "text_follows_handles": bool(options["legend_text_follow_colors"]),
                            "draggable": bool(options["legend_draggable"]),
                        }
                    )
                    self._apply_legend_snapshot(legend, state)
            else:
                legend = None
                try:
                    legend = target_axes.get_legend()
                except Exception:
                    legend = None
                if legend is not None:
                    try:
                        legend.set_visible(False)
                    except Exception:
                        pass

        self._fit_figure_to_content(figure)
        if not adjust_subwindow:
            return
        tab = self._tab_for_axes(axes)  # noqa: SLF001 - shared helper
        if tab is None:
            return
        subwindow_for = getattr(self.tab_widget, "_subwindow_for", None)
        if not callable(subwindow_for):
            return
        try:
            sub = subwindow_for(tab)
        except Exception:
            sub = None
        if sub is None:
            return
        try:
            aspect = float(figure_width) / float(figure_height)
        except Exception:
            aspect = 0.0
        if math.isfinite(aspect) and aspect > 0.0:
            setter = getattr(sub, "set_aspect_ratio", None)
            if callable(setter):
                try:
                    setter(aspect)
                except Exception:
                    pass
        is_maximized_target = getattr(self.tab_widget, "_is_maximized_target", None)
        maybe_maximize = getattr(self.tab_widget, "_maybe_apply_maximize", None)
        if callable(is_maximized_target) and callable(maybe_maximize):
            try:
                if bool(is_maximized_target(sub)):
                    maybe_maximize(sub)
                    return
            except Exception:
                pass
        try:
            dpi = float(getattr(figure, "dpi", 100.0) or 100.0) if figure is not None else 100.0
        except Exception:
            dpi = 100.0
        target_w = int(max(360.0, figure_width * dpi + 48.0))
        target_h = int(max(260.0, figure_height * dpi + 72.0))
        fitted = False
        fitter = getattr(self.tab_widget, "_fit_subwindow", None)
        if callable(fitter):
            try:
                fitter(
                    sub,
                    use_half_width=False,
                    preferred_width=target_w,
                    remember_manual=False,
                )
                fitted = True
            except Exception:
                fitted = False
        if not fitted:
            try:
                sub.resize(target_w, target_h)
            except Exception:
                pass
        arrange = getattr(self.tab_widget, "_arrange_subwindows", None)
        arrangement_mode_getter = getattr(self.tab_widget, "arrangement_mode", None)
        arrangement_mode = "cascade"
        if callable(arrangement_mode_getter):
            try:
                arrangement_mode = str(arrangement_mode_getter() or "cascade").strip().lower()
            except Exception:
                arrangement_mode = "cascade"
        if callable(arrange) and arrangement_mode in {"tile_vertical", "tile_horizontal"}:
            try:
                QtCore.QTimer.singleShot(0, arrange)
            except Exception:
                pass

    # ------------------------------------------------------------------ Qt hooks
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self._current_plugin is not None:
            try:
                self._current_plugin.deactivate()
            except Exception:
                pass
        for child in self.findChildren(QtWidgets.QMenu):
            try:
                child.close()
            except Exception:
                pass
        for child in self.findChildren(QtWidgets.QDialog):
            try:
                child.close()
            except Exception:
                pass
        graph_format_dialog = getattr(self, "_graph_format_dialog", None)
        if isinstance(graph_format_dialog, QtWidgets.QDialog):
            try:
                graph_format_dialog.close()
            except Exception:
                pass
        settings = self._shared_qsettings()
        self._sync_plugin_directory_settings()
        if settings is not None:
            settings.setValue("sources", self.path_edit.text())
            if self._last_directory is not None:
                settings.setValue("last_directory", str(self._last_directory))
            if self._last_graph_dir is not None:
                settings.setValue("last_graph_dir", str(self._last_graph_dir))
            else:
                settings.remove("last_graph_dir")
            graph_format = getattr(self, "_last_graph_format", ".png")
            if isinstance(graph_format, str) and graph_format in {".png", ".pdf", ".svg"}:
                settings.setValue("last_graph_format", graph_format)
            else:
                settings.setValue("last_graph_format", ".png")
            try:
                legend_settings = getattr(self, "_legend_settings_by_plugin", {})
                settings.setValue("legend_settings_by_plugin", json.dumps(legend_settings))
            except Exception:
                pass
        self._save_graph_option_settings()
        if settings is not None:
            settings.sync()
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
        request_paths = getattr(self, "_plot_request_paths", None)
        if callable(request_paths):
            try:
                requested = [path for path in request_paths() if isinstance(path, Path) and path.is_file()]
            except Exception:
                requested = []
            if requested:
                return requested
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

        if warn_on_missing:
            title = plugin.name if isinstance(plugin, PyPlotPlugin) else "PyPlot"
            QtWidgets.QMessageBox.information(
                self,
                title,
                "Import data through the toolbar before plotting in this plugin.",
            )
            try:
                self._prompt_import_data()
            except Exception:
                pass
        return []

    def _update_action_states(self) -> None:
        export_txt_action = getattr(self, "export_button", None)
        if isinstance(export_txt_action, QtGui.QAction):
            export_txt_action.setEnabled(False)
        export_origin_action = getattr(self, "export_origin_button", None)
        if isinstance(export_origin_action, QtGui.QAction):
            export_origin_action.setEnabled(False)
        outlier_action = getattr(self, "check_outliers_button", None)
        if isinstance(outlier_action, QtGui.QAction):
            outlier_action.setEnabled(False)
        open_origin_action = getattr(self, "open_origin_button", None)
        if isinstance(open_origin_action, QtGui.QAction):
            open_origin_action.setEnabled(False)

        if self._current_plugin is not None:
            self._set_plot_button_label(self._current_plugin)
            try:
                self._current_plugin.update_ui()
            except Exception:
                pass
            button = getattr(self, "plot_button", None)
            if isinstance(button, (QtGui.QAction, QtWidgets.QWidget)):
                button.setEnabled(self._plugin_ready_to_plot(self._current_plugin))
            combo = self._plot_scope_combo
            if isinstance(combo, QtWidgets.QComboBox):
                combo.setEnabled(True)
            self._sync_shared_action_states()
            save_sync = getattr(self, "_update_save_graph_enabled", None)
            if callable(save_sync):
                try:
                    save_sync()
                except Exception:
                    pass
            norm_sync = getattr(self, "_update_normalize_enabled", None)
            if callable(norm_sync):
                try:
                    norm_sync()
                except Exception:
                    pass
            return
        button = getattr(self, "plot_button", None)
        if isinstance(button, (QtGui.QAction, QtWidgets.QWidget)):
            button.setEnabled(False)
        combo = self._plot_scope_combo
        if isinstance(combo, QtWidgets.QComboBox):
            combo.setEnabled(False)
        self._set_plot_button_label(None)
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
        self._sync_shared_action_states()
        save_sync = getattr(self, "_update_save_graph_enabled", None)
        if callable(save_sync):
            try:
                save_sync()
            except Exception:
                pass
        norm_sync = getattr(self, "_update_normalize_enabled", None)
        if callable(norm_sync):
            try:
                norm_sync()
            except Exception:
                pass

    def _has_imported_data(self) -> bool:
        if getattr(self, "_session_has_imports", False):
            return True
        worksheets = getattr(self, "_worksheets", None)
        return bool(worksheets)

    def _plugin_has_loaded_data(self, plugin: PyPlotPlugin | None) -> bool:
        if plugin is None:
            return False
        checker = getattr(plugin, "has_loaded_data", None)
        if callable(checker):
            try:
                if bool(checker()):
                    return True
            except Exception:
                LOGGER.debug("Plugin has_loaded_data() failed for %s", plugin.name, exc_info=True)
        for attr in ("_data", "_dataset"):
            data = getattr(plugin, attr, None)
            if data is None:
                continue
            if isinstance(data, pd.DataFrame):
                if not data.empty:
                    return True
                continue
            if isinstance(data, (list, tuple, set, dict, str, bytes)):
                if len(data) > 0:
                    return True
                continue
            length = getattr(data, "__len__", None)
            if callable(length):
                try:
                    if int(length()) > 0:
                        return True
                    continue
                except Exception:
                    pass
            if bool(data):
                return True
        return False

    @staticmethod
    def _plugin_has_runtime_data(plugin: PyPlotPlugin | None) -> bool:
        if plugin is None:
            return False
        for attr in ("_data", "_dataset"):
            data = getattr(plugin, attr, None)
            if data is None:
                continue
            if isinstance(data, pd.DataFrame):
                if not data.empty:
                    return True
                continue
            if isinstance(data, (list, tuple, set, dict, str, bytes)):
                if len(data) > 0:
                    return True
                continue
            length = getattr(data, "__len__", None)
            if callable(length):
                try:
                    if int(length()) > 0:
                        return True
                    continue
                except Exception:
                    pass
            if bool(data):
                return True
        return False

    def _plugin_tab_count(self, plugin_name: str | None) -> int:
        token = str(plugin_name or "").strip()
        if not token:
            return 0
        descriptors = getattr(self, "_tab_descriptors", {})
        if not isinstance(descriptors, dict):
            return 0
        count = 0
        for descriptor in descriptors.values():
            if self._tab_plugin_name(descriptor) == token:
                count += 1
        return count

    def _plugin_ready_to_plot(self, plugin: PyPlotPlugin | None) -> bool:
        if plugin is None:
            return False
        if self._plugin_has_loaded_data(plugin):
            return True
        requires = bool(getattr(plugin, "requires_imported_data", False))
        if requires:
            return False
        return bool(self._selected_paths())

    def _run_with_shared_progress(
        self,
        *,
        title: str,
        task: Callable[[], None],
    ) -> None:
        begin = getattr(self, "_begin_task_progress", None)
        end = getattr(self, "_end_task_progress", None)
        started = False
        if callable(begin):
            try:
                begin(title, maximum=None, value=0)
                started = True
            except Exception:
                started = False
        try:
            task()
        finally:
            if started and callable(end):
                try:
                    end()
                except Exception:
                    pass

    def _import_paths(self, paths: Iterable[Path]) -> None:
        path_list = [Path(p) for p in paths]
        super()._import_paths(path_list)
        if path_list:
            self._commit_selected_paths(path_list)
            self._session_has_imports = True
        self._sync_selected_paths_with_imports()
        if self._current_plotter_name and self._last_directory is not None:
            self._plugin_last_directories[self._current_plotter_name] = self._last_directory
            self._sync_plugin_directory_settings()
        auto_loader = (
            self._current_plugin
            if self._current_plugin is not None
            and getattr(self._current_plugin, "auto_load_on_import", False)
            else None
        )
        should_auto_load = (
            auto_loader is not None
            and not getattr(self, "_restoring_imports", False)
        )
        if should_auto_load:
            try:
                self._run_with_shared_progress(
                    title=f"{self._current_plotter_name or 'Plugin'}: loading data...",
                    task=auto_loader.load_data,
                )
            except Exception:
                LOGGER.warning("Automatic data load failed for %s", self._current_plotter_name, exc_info=True)
        self._update_action_states()
        self._update_project_actions()
        refresh = getattr(self, "_refresh_primary_dock_layout", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass
        settings = self._shared_qsettings()
        if settings is not None:
            settings.setValue("sources", self.path_edit.text())
            settings.sync()
    def _has_project_data_to_save(self) -> bool:
        measurements = getattr(self, "measurements", None)
        if isinstance(measurements, list) and measurements:
            return True
        if bool(getattr(self, "_connected_data_folders", [])):
            return True
        if any(
            descriptor.kind in {"manual_graph", "composed_graph", "layout_graph"}
            for descriptor in getattr(self, "_tab_descriptors", {}).values()
        ):
            return True
        return bool(self._worksheets)

    def _reset_project_state(self) -> None:
        super()._reset_project_state()
        for tab in list(self._tab_descriptors.keys()):
            remover = getattr(self, "_remove_tab_internal", None)
            if callable(remover):
                try:
                    remover(tab)
                except Exception:
                    pass
        self._clear_imported_data()
        self._selected_path_entries = []
        self._connected_data_folders = []
        self._connected_folder_seen_files = set()
        self._update_connected_folder_state()
        self._pending_new_plot_paths = []
        self._last_imported_file_paths = []
        self.path_edit.clear()
        self._update_action_states()
        self._update_project_actions()

    def _serialize_shared_plugin_project_state(
        self,
        plugin: PyPlotPlugin,
        *,
        base_path: Path | None,
    ) -> Dict[str, Any]:
        selected_paths_payload = [
            self._portable_path(path, base_path)
            for path in self._selected_paths()
            if isinstance(path, Path)
        ]
        selected_paths_payload = [
            entry for entry in selected_paths_payload if isinstance(entry, str) and entry
        ]
        payload: Dict[str, Any] = {
            "selected_paths": selected_paths_payload,
            "auto_load_on_import": bool(getattr(plugin, "auto_load_on_import", False)),
            "had_plots": self._plugin_tab_count(plugin.name) > 0,
        }
        connected_payload = [
            self._portable_path(path, base_path)
            for path in getattr(self, "_connected_data_folders", [])
            if isinstance(path, Path)
        ]
        connected_payload = [
            entry for entry in connected_payload if isinstance(entry, str) and entry
        ]
        if connected_payload:
            payload["connected_folders"] = connected_payload
        plugin_last_dir = self._plugin_last_directories.get(plugin.name)
        plugin_last_dir_payload = self._portable_path(plugin_last_dir, base_path)
        if isinstance(plugin_last_dir_payload, str) and plugin_last_dir_payload:
            payload["plugin_last_directory"] = plugin_last_dir_payload
        return payload

    def _apply_shared_plugin_project_state(
        self,
        plugin: PyPlotPlugin,
        shared_state: Dict[str, Any] | None,
        *,
        project_dir: Path,
    ) -> bool:
        if not isinstance(shared_state, dict):
            return bool(getattr(plugin, "auto_load_on_import", False))

        selected_payload = shared_state.get("selected_paths")
        resolved_paths: list[Path] = []
        if isinstance(selected_payload, list):
            for entry in selected_payload:
                if not isinstance(entry, str) or not entry:
                    continue
                resolved = self._resolve_portable_path(entry, project_dir)
                if isinstance(resolved, Path):
                    resolved_paths.append(resolved)
        if resolved_paths:
            commit_paths = getattr(self, "_commit_selected_paths", None)
            if callable(commit_paths):
                try:
                    commit_paths(resolved_paths)
                except Exception:
                    pass

        connected_payload = shared_state.get("connected_folders")
        connected_paths: list[Path] = []
        if isinstance(connected_payload, list):
            for entry in connected_payload:
                if not isinstance(entry, str) or not entry:
                    continue
                resolved = self._resolve_portable_path(entry, project_dir)
                if isinstance(resolved, Path):
                    connected_paths.append(resolved)
        if connected_paths:
            connector = getattr(self, "_connect_data_folders", None)
            if callable(connector):
                try:
                    connector(connected_paths)
                except Exception:
                    pass

        last_dir_entry = shared_state.get("plugin_last_directory")
        if isinstance(last_dir_entry, str) and last_dir_entry:
            resolved_last = self._resolve_portable_path(last_dir_entry, project_dir)
            if isinstance(resolved_last, Path):
                try:
                    directory = resolved_last if resolved_last.is_dir() else resolved_last.parent
                except Exception:
                    directory = resolved_last.parent
                if isinstance(directory, Path):
                    self._plugin_last_directories[plugin.name] = directory

        auto_load = shared_state.get("auto_load_on_import")
        if isinstance(auto_load, bool):
            return auto_load
        return bool(getattr(plugin, "auto_load_on_import", False))

    def _descriptor_fingerprint(self, descriptor: TabDescriptor) -> dict[str, Any]:
        axes = getattr(descriptor, "axes", None)
        line_labels: list[str] = []
        if axes is not None:
            try:
                for line in axes.get_lines():
                    if not isinstance(line, Line2D):
                        continue
                    if getattr(self, "_graph_object_kind", lambda _value: None)(line) == "shape_line":
                        continue
                    label = str(line.get_label() or "").strip()
                    if label and label != "_nolegend_" and not label.startswith("_"):
                        line_labels.append(label)
            except Exception:
                pass
        metadata = descriptor.metadata if isinstance(descriptor.metadata, dict) else {}
        return {
            "kind": descriptor.kind,
            "title": descriptor.title,
            "root_label": descriptor.root_label,
            "x_label": descriptor.x_label,
            "y_label": descriptor.y_label,
            "plugin": metadata.get("plugin"),
            "line_labels": sorted(line_labels),
        }

    def _serialize_manual_graph_tabs(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for tab, descriptor in self._tab_descriptors.items():
            if descriptor.kind not in {"manual_graph", "composed_graph", "layout_graph"}:
                continue
            if descriptor.kind == "layout_graph":
                figure = getattr(getattr(descriptor, "axes", None), "figure", None)
                if figure is None:
                    continue
                try:
                    axes_list = [axis for axis in list(figure.axes) if axis is not None and bool(axis.get_visible())]
                except Exception:
                    axes_list = []
                panels: list[dict[str, Any]] = []
                for axes in axes_list:
                    panels.append(self._panel_spec_from_axes(axes))
                payloads.append(
                    {
                        "kind": descriptor.kind,
                        "tab_label": self.tab_widget.tabText(self.tab_widget.indexOf(tab)),
                        "title": descriptor.title,
                        "metadata": dict(descriptor.metadata or {}),
                        "panels": panels,
                    }
                )
                continue
            axes = getattr(descriptor, "axes", None)
            if axes is None:
                continue
            series_payload: list[dict[str, Any]] = []
            try:
                source_lines = list(axes.get_lines())
            except Exception:
                source_lines = []
            for line in source_lines:
                if not isinstance(line, Line2D):
                    continue
                if getattr(self, "_graph_object_kind", lambda _value: None)(line) == "shape_line":
                    continue
                x_data = np.asarray(line.get_xdata())
                y_data = np.asarray(line.get_ydata())
                if x_data.size == 0 or y_data.size == 0:
                    continue
                series_payload.append(
                    {
                        "label": str(line.get_label() or ""),
                        "x": x_data.tolist(),
                        "y": y_data.tolist(),
                        "color": line.get_color(),
                        "linestyle": str(line.get_linestyle() or "-"),
                        "linewidth": float(line.get_linewidth()),
                        "marker": str(line.get_marker() or "None"),
                        "markersize": float(line.get_markersize()),
                        "visible": bool(line.get_visible()),
                    }
                )
            payloads.append(
                {
                    "kind": descriptor.kind,
                    "tab_label": self.tab_widget.tabText(self.tab_widget.indexOf(tab)),
                    "title": str(getattr(axes, "get_title", lambda: descriptor.title)() or descriptor.title),
                    "x_label": str(getattr(axes, "get_xlabel", lambda: descriptor.x_label)() or descriptor.x_label),
                    "y_label": str(getattr(axes, "get_ylabel", lambda: descriptor.y_label)() or descriptor.y_label),
                    "series": series_payload,
                    "metadata": dict(descriptor.metadata or {}),
                    "graph_objects": self._serialize_graph_object_payloads_for_axes(axes),
                }
            )
        return payloads

    def _restore_manual_graph_tabs(self, payloads: Sequence[dict[str, Any]]) -> None:
        for entry in payloads:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("kind") or "") == "layout_graph":
                panels = entry.get("panels")
                metadata = dict(entry.get("metadata") or {})
                config = metadata.get("layout_config") if isinstance(metadata.get("layout_config"), dict) else {}
                if isinstance(panels, list) and panels:
                    self._build_layout_graph_from_panels(
                        panels,
                        title=str(entry.get("title") or "Figure Layout"),
                        rows=int(config.get("rows") or 1),
                        cols=int(config.get("cols") or max(1, len(panels))),
                        share_x=bool(config.get("share_x", False)),
                        share_y=bool(config.get("share_y", False)),
                        panel_label_mode=str(config.get("panel_labels") or "none"),
                        figure_width=float(config.get("figure_width") or 7.10),
                        figure_height=float(config.get("figure_height") or 4.80),
                        external_legend=bool(config.get("external_legend", False)),
                        legend_placement=str(config.get("legend_placement") or "right"),
                        panel_titles=list(config.get("panel_titles") or []),
                        wspace=float(config.get("wspace") or 0.18),
                        hspace=float(config.get("hspace") or 0.25),
                        left_margin=float(config.get("left_margin") or 0.10),
                        right_margin=float(config.get("right_margin") or 0.96),
                        top_margin=float(config.get("top_margin") or 0.90),
                        bottom_margin=float(config.get("bottom_margin") or 0.12),
                        metadata=metadata,
                        style_preset=str(config.get("style_preset") or "default"),
                        minor_ticks=bool(config.get("minor_ticks", False)),
                        tick_direction=str(config.get("tick_direction") or "out"),
                        notation=str(config.get("notation") or "plain"),
                        panel_label_position=str(config.get("panel_label_position") or "tl"),
                        panel_label_size=float(config.get("panel_label_size") or 14.0),
                        x_decimals=int(config.get("x_decimals", -1)),
                        y_decimals=int(config.get("y_decimals", -1)),
                        x_ticks=list(config.get("x_ticks") or []),
                        y_ticks=list(config.get("y_ticks") or []),
                    )
                continue
            series_entries = entry.get("series")
            if not isinstance(series_entries, list):
                continue
            fig = Figure(figsize=(6.2, 4.2))
            ax = fig.add_subplot(111)
            lines: Dict[tuple[str, float | str], GraphLineState] = {}
            for index, series in enumerate(series_entries):
                if not isinstance(series, dict):
                    continue
                x_values = np.asarray(series.get("x") or [])
                y_values = np.asarray(series.get("y") or [])
                if x_values.size == 0 or y_values.size == 0:
                    continue
                try:
                    line = ax.plot(
                        x_values,
                        y_values,
                        label=str(series.get("label") or f"Series {index + 1}"),
                        color=series.get("color"),
                        linestyle=str(series.get("linestyle") or "-"),
                        linewidth=float(series.get("linewidth") or 1.5),
                        marker=str(series.get("marker") or "None"),
                        markersize=float(series.get("markersize") or 6.0),
                    )[0]
                except Exception:
                    line = ax.plot(x_values, y_values, label=str(series.get("label") or f"Series {index + 1}"))[0]
                try:
                    line.set_visible(bool(series.get("visible", True)))
                except Exception:
                    pass
                key = (str(series.get("label") or f"Series {index + 1}"), index)
                lines[key] = GraphLineState(
                    key=key,
                    label=str(series.get("label") or f"Series {index + 1}"),
                    line=line,
                    base_x=x_values,
                    base_y=y_values,
                    full_x=x_values,
                    full_y=y_values,
                )
            ax.set_title(str(entry.get("title") or "Graph"))
            ax.set_xlabel(str(entry.get("x_label") or "X"))
            ax.set_ylabel(str(entry.get("y_label") or "Y"))
            ax.grid(True)
            try:
                if any(str(line.get_label() or "").strip() and str(line.get_label() or "").strip() != "_nolegend_" for line in ax.get_lines()):
                    ax.legend(loc="best")
            except Exception:
                pass
            canvas = PlotFigureCanvas(fig)
            tab = create_plot_tab_container(canvas)
            descriptor = TabDescriptor(
                kind=str(entry.get("kind") or "manual_graph"),
                title=str(ax.get_title() or "Graph"),
                root_label="Composed Graph" if str(entry.get("kind")) == "composed_graph" else "Graph",
                x_label=str(ax.get_xlabel() or ""),
                y_label=str(ax.get_ylabel() or ""),
                canvas=canvas,
                axes=ax,
                lines=lines,
                metadata=dict(entry.get("metadata") or {}),
            )
            index = self.tab_widget.addTab(tab, str(entry.get("tab_label") or descriptor.title or "Graph"))
            self.tab_widget.setCurrentIndex(index)
            self._register_plot_tab(tab, canvas, ax, descriptor)
            graph_objects = entry.get("graph_objects")
            if isinstance(graph_objects, list):
                self._restore_graph_object_payloads_for_axes(ax, graph_objects)

    def _serialize_overlay_payloads(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for descriptor in self._tab_descriptors.values():
            if descriptor.kind in {"manual_graph", "composed_graph"}:
                continue
            axes = getattr(descriptor, "axes", None)
            if axes is None:
                continue
            graph_objects = self._serialize_graph_object_payloads_for_axes(axes)
            if not graph_objects:
                continue
            payloads.append(
                {
                    "fingerprint": self._descriptor_fingerprint(descriptor),
                    "graph_objects": graph_objects,
                }
            )
        return payloads

    def _restore_overlay_payloads(self, payloads: Sequence[dict[str, Any]]) -> None:
        if not payloads:
            return
        available = list(self._tab_descriptors.values())
        for entry in payloads:
            if not isinstance(entry, dict):
                continue
            fingerprint = entry.get("fingerprint")
            graph_objects = entry.get("graph_objects")
            if not isinstance(fingerprint, dict) or not isinstance(graph_objects, list):
                continue
            target_descriptor = None
            for descriptor in available:
                if self._descriptor_fingerprint(descriptor) == fingerprint:
                    target_descriptor = descriptor
                    break
            if target_descriptor is None:
                continue
            axes = getattr(target_descriptor, "axes", None)
            self._restore_graph_object_payloads_for_axes(axes, graph_objects)

    def _build_project_payload(self, *, base_path: Path | None) -> Dict[str, Any]:
        selected_payload = [
            self._portable_path(path, base_path) for path in self._selected_path_entries
        ]
        active_plugin_state: Dict[str, Any] | None = None
        if self._current_plugin is not None:
            shared_state = self._serialize_shared_plugin_project_state(
                self._current_plugin,
                base_path=base_path,
            )
            try:
                state = self._current_plugin.serialize_project_state(base_path=base_path)
            except Exception:
                LOGGER.warning(
                    "Failed to serialize project state for plugin %s",
                    self._current_plotter_name,
                    exc_info=True,
                )
                state = None
            state_payload = dict(state) if isinstance(state, dict) else {}
            state_payload[self.PLUGIN_SHARED_STATE_KEY] = shared_state
            if state_payload:
                active_plugin_state = state_payload
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
        manual_graphs = self._serialize_manual_graph_tabs()
        graph_overlays = self._serialize_overlay_payloads()
        return {
            "selected_paths": selected_payload,
            "workbooks": workbooks_payload,
            "active_plugin": self._current_plotter_name,
            "active_plugin_state": active_plugin_state,
            "manual_graphs": manual_graphs,
            "graph_overlays": graph_overlays,
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

        active_plugin = payload.get("active_plugin")
        if isinstance(active_plugin, str) and active_plugin:
            self._activate_plotter_for_project_load(active_plugin)
        plugin_state = payload.get("active_plugin_state")
        auto_load_on_import = bool(
            getattr(self._current_plugin, "auto_load_on_import", False)
            if self._current_plugin is not None
            else False
        )
        shared_had_plots = False
        if isinstance(plugin_state, dict) and self._current_plugin is not None:
            shared_state = plugin_state.get(self.PLUGIN_SHARED_STATE_KEY)
            if isinstance(shared_state, dict):
                shared_had_plots = bool(shared_state.get("had_plots", False))
            auto_load_on_import = self._apply_shared_plugin_project_state(
                self._current_plugin,
                shared_state if isinstance(shared_state, dict) else None,
                project_dir=project_dir,
            )
            plugin_specific_state = {
                key: value
                for key, value in plugin_state.items()
                if key != self.PLUGIN_SHARED_STATE_KEY
            }
            try:
                self._current_plugin.restore_project_state(
                    plugin_specific_state,
                    project_dir=project_dir,
                )
            except Exception:
                LOGGER.warning(
                    "Failed to restore project state for plugin %s",
                    self._current_plotter_name,
                    exc_info=True,
                )
        should_autoload_plugin_data = (
            self._current_plugin is not None
            and (auto_load_on_import or shared_had_plots)
            and not self._plugin_has_runtime_data(self._current_plugin)
            and bool(self._selected_path_entries)
        )
        if should_autoload_plugin_data and self._current_plugin is not None:
            try:
                self._run_with_shared_progress(
                    title=f"{self._current_plotter_name or 'Plugin'}: loading data...",
                    task=self._current_plugin.load_data,
                )
            except Exception:
                LOGGER.warning(
                    "Automatic project-load data restore failed for plugin %s",
                    self._current_plotter_name,
                    exc_info=True,
                )
        should_regenerate_shared_plots = (
            self._current_plugin is not None
            and shared_had_plots
            and self._plugin_has_loaded_data(self._current_plugin)
            and self._plugin_tab_count(self._current_plugin.name) == 0
        )
        if should_regenerate_shared_plots and self._current_plugin is not None:
            try:
                self._run_with_shared_progress(
                    title=f"{self._current_plotter_name or 'Plugin'}: generating plots...",
                    task=self._current_plugin.generate,
                )
            except Exception:
                LOGGER.warning(
                    "Automatic shared plot regeneration failed for plugin %s",
                    self._current_plotter_name,
                    exc_info=True,
                )

        manual_graphs = payload.get("manual_graphs")
        if isinstance(manual_graphs, list):
            self._restore_manual_graph_tabs(manual_graphs)
        graph_overlays = payload.get("graph_overlays")
        if isinstance(graph_overlays, list):
            self._restore_overlay_payloads(graph_overlays)

        self._sync_selected_paths_with_imports()
        self._update_action_states()
        self._update_project_actions()
        return True

    def _activate_plotter_for_project_load(self, name: str) -> bool:
        if not isinstance(name, str) or not name:
            return False
        if self._current_plotter_name == name and self._current_plugin is not None:
            return True
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        if combo is None:
            return False
        index = combo.findData(name)
        if index < 0:
            index = combo.findText(name)
        if index < 0:
            return False

        if self._current_plugin is not None and self._current_plotter_name != name:
            try:
                self._current_plugin.deactivate()
            except Exception:
                pass
            self._current_plugin = None
            self._current_plotter_name = None
            self._active_plugin_updater = None

        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        self._apply_selected_plotter()
        return self._current_plotter_name == name and self._current_plugin is not None

    def _clear_imported_data(self) -> None:
        for key in list(self._worksheet_tabs_open.keys()):
            self._remove_worksheet(key)
        self._workbooks.clear()
        self._worksheets.clear()
        shared_keys = getattr(self, "_shared_plot_workbook_keys", None)
        if isinstance(shared_keys, set):
            shared_keys.clear()
        shared_map = getattr(self, "_shared_plot_workbook_by_tab", None)
        if isinstance(shared_map, dict):
            shared_map.clear()
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
        self._sync_shared_action_states()
        self._session_has_imports = False
        self._clear_project_dirty()

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
        directories = self._select_directories(
            self,
            title="Select data folder(s)",
            start_dir=start,
        )
        if not directories:
            return
        self._selected_path_entries = [Path(directory) for directory in directories]
        self._remember_directory_from_paths(self._selected_path_entries)
        self.path_edit.setText(self._format_paths(self._selected_path_entries))
        self._update_action_states()

    def _generate_plots(self) -> None:
        if self._current_plugin is not None:
            scope = self._requested_plot_scope()
            if scope == "new":
                new_paths = self._recent_new_plot_paths()
                if not new_paths:
                    QtWidgets.QMessageBox.information(
                        self,
                        self._current_plotter_name or self._current_plugin.name,
                        "No newly imported files are pending. Use Replot all or refresh connected folders first.",
                    )
                    return
                original_selection = list(self._selected_path_entries)
                plugin = self._current_plugin
                original_tabs = list(getattr(plugin, "_plot_tabs", []))
                new_tabs: List[QtWidgets.QWidget] = []
                self._plot_request_mode = "new"
                self._plot_request_paths_snapshot = list(new_paths)
                try:
                    self._commit_selected_paths(new_paths)
                    loader = getattr(plugin, "load_data", None)
                    if callable(loader):
                        loader()
                    if hasattr(plugin, "_plot_tabs"):
                        setattr(plugin, "_plot_tabs", [])
                    self._run_with_shared_progress(
                        title=f"{self._current_plotter_name or plugin.name}: plotting new data...",
                        task=plugin.generate,
                    )
                    new_tabs = list(getattr(plugin, "_plot_tabs", []))
                    if hasattr(plugin, "_plot_tabs"):
                        setattr(plugin, "_plot_tabs", [*original_tabs, *new_tabs])
                    self._pending_new_plot_paths = []
                finally:
                    self._plot_request_mode = None
                    self._plot_request_paths_snapshot = None
                    self._commit_selected_paths(original_selection)
                    self._sync_selected_paths_with_imports()
                    self._update_action_states()
                return

            self._plot_request_mode = "all"
            self._plot_request_paths_snapshot = list(self._selected_paths())
            try:
                self._run_with_shared_progress(
                    title=f"{self._current_plotter_name or self._current_plugin.name}: generating plots...",
                    task=self._current_plugin.generate,
                )
                self._pending_new_plot_paths = []
            finally:
                self._plot_request_mode = None
                self._plot_request_paths_snapshot = None
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
            self._run_with_shared_progress(
                title=f"{self._current_plotter_name or self._current_plugin.name}: exporting TXT...",
                task=self._current_plugin.export_txt,
            )
            return
        QtWidgets.QMessageBox.information(
            self,
            "PyPlot",
            "Generate data before exporting.",
        )
    def _open_origin_prompt(self) -> None:
        if self._current_plugin is not None:
            self._run_with_shared_progress(
                title=f"{self._current_plotter_name or self._current_plugin.name}: exporting to Origin...",
                task=self._current_plugin.open_origin,
            )
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

        graph_section, graph_section_layout = create_toolbar_section(
            "Graph formatting",
            parent=panel or self,
        )
        format_tabs = QtWidgets.QTabWidget(graph_section)
        format_tabs.setObjectName("mw_graph_format_tabs")
        format_tabs.setDocumentMode(True)
        format_tabs.setTabPosition(QtWidgets.QTabWidget.TabPosition.North)
        format_tabs.tabBar().setVisible(True)
        graph_section_layout.addWidget(format_tabs)

        def _create_tab(title: str) -> tuple[QtWidgets.QWidget, QtWidgets.QFormLayout]:
            tab = QtWidgets.QWidget(format_tabs)
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(0)
            scroll = QtWidgets.QScrollArea(tab)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            tab_layout.addWidget(scroll, 1)
            content = QtWidgets.QWidget(scroll)
            scroll.setWidget(content)
            content_layout = QtWidgets.QVBoxLayout(content)
            content_layout.setContentsMargins(6, 6, 6, 6)
            content_layout.setSpacing(6)
            form = QtWidgets.QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(6)
            content_layout.addLayout(form)
            content_layout.addStretch(1)
            format_tabs.addTab(tab, title)
            return tab, form

        text_tab, text_form = _create_tab("Text")
        axes_tab, axes_form = _create_tab("Axes")
        ticks_tab, ticks_form = _create_tab("Ticks")
        legend_tab, legend_form = _create_tab("Legend")

        title_edit = QtWidgets.QLineEdit(text_tab)
        x_label_edit = QtWidgets.QLineEdit(text_tab)
        y_label_edit = QtWidgets.QLineEdit(text_tab)
        title_visible_cb = QtWidgets.QCheckBox("Show", text_tab)
        title_visible_cb.setChecked(True)
        x_label_visible_cb = QtWidgets.QCheckBox("Show", text_tab)
        x_label_visible_cb.setChecked(True)
        y_label_visible_cb = QtWidgets.QCheckBox("Show", text_tab)
        y_label_visible_cb.setChecked(True)

        def _label_row(
            edit: QtWidgets.QLineEdit,
            visible_cb: QtWidgets.QCheckBox,
            parent_widget: QtWidgets.QWidget,
        ) -> QtWidgets.QWidget:
            row = QtWidgets.QWidget(parent_widget)
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.addWidget(visible_cb, 0)
            row_layout.addWidget(edit, 1)
            return row

        text_form.addRow("Title:", _label_row(title_edit, title_visible_cb, text_tab))
        text_form.addRow("X label:", _label_row(x_label_edit, x_label_visible_cb, text_tab))
        text_form.addRow("Y label:", _label_row(y_label_edit, y_label_visible_cb, text_tab))

        title_font_spin = QtWidgets.QSpinBox(text_tab)
        title_font_spin.setRange(6, 96)
        title_font_spin.setValue(16)
        label_font_spin = QtWidgets.QSpinBox(text_tab)
        label_font_spin.setRange(6, 96)
        label_font_spin.setValue(12)
        tick_font_spin = QtWidgets.QSpinBox(text_tab)
        tick_font_spin.setRange(6, 96)
        tick_font_spin.setValue(10)
        text_form.addRow("Title font:", title_font_spin)
        text_form.addRow("Label font:", label_font_spin)
        text_form.addRow("Tick label font:", tick_font_spin)

        line_width_spin = QtWidgets.QDoubleSpinBox(text_tab)
        line_width_spin.setRange(0.1, 20.0)
        line_width_spin.setSingleStep(0.1)
        line_width_spin.setValue(1.5)
        marker_size_spin = QtWidgets.QDoubleSpinBox(text_tab)
        marker_size_spin.setRange(0.1, 30.0)
        marker_size_spin.setSingleStep(0.2)
        marker_size_spin.setValue(6.0)
        text_form.addRow("Line width:", line_width_spin)
        text_form.addRow("Marker size:", marker_size_spin)

        figure_width_spin = QtWidgets.QDoubleSpinBox(axes_tab)
        figure_width_spin.setRange(10.0, 600.0)
        figure_width_spin.setSingleStep(1.0)
        figure_width_spin.setDecimals(1)
        figure_width_spin.setValue(float(DEFAULT_PAPER_WIDTH_MM))
        figure_height_spin = QtWidgets.QDoubleSpinBox(axes_tab)
        figure_height_spin.setRange(10.0, 600.0)
        figure_height_spin.setSingleStep(1.0)
        figure_height_spin.setDecimals(1)
        figure_height_spin.setValue(float(DEFAULT_PAPER_WIDTH_MM / DEFAULT_FIGURE_ASPECT_RATIO))
        figure_width_auto_cb = QtWidgets.QCheckBox("auto", axes_tab)
        figure_height_auto_cb = QtWidgets.QCheckBox("auto", axes_tab)
        figure_width_auto_cb.setChecked(False)
        figure_height_auto_cb.setChecked(True)
        figure_aspect_mode_combo = QtWidgets.QComboBox(axes_tab)
        figure_aspect_mode_combo.addItem("Auto", "auto")
        figure_aspect_mode_combo.addItem("Custom", "custom")
        figure_aspect_ratio_spin = QtWidgets.QDoubleSpinBox(axes_tab)
        figure_aspect_ratio_spin.setRange(0.2, 20.0)
        figure_aspect_ratio_spin.setSingleStep(0.05)
        figure_aspect_ratio_spin.setDecimals(3)
        figure_aspect_ratio_spin.setValue(float(DEFAULT_FIGURE_ASPECT_RATIO))

        figure_width_row = QtWidgets.QWidget(axes_tab)
        figure_width_layout = QtWidgets.QHBoxLayout(figure_width_row)
        figure_width_layout.setContentsMargins(0, 0, 0, 0)
        figure_width_layout.setSpacing(6)
        figure_width_layout.addWidget(figure_width_spin, 1)
        figure_width_layout.addWidget(figure_width_auto_cb, 0)

        figure_height_row = QtWidgets.QWidget(axes_tab)
        figure_height_layout = QtWidgets.QHBoxLayout(figure_height_row)
        figure_height_layout.setContentsMargins(0, 0, 0, 0)
        figure_height_layout.setSpacing(6)
        figure_height_layout.addWidget(figure_height_spin, 1)
        figure_height_layout.addWidget(figure_height_auto_cb, 0)

        axes_form.addRow("Figure width (mm):", figure_width_row)
        axes_form.addRow("Figure height (mm):", figure_height_row)
        axes_form.addRow("Figure aspect:", figure_aspect_mode_combo)
        axes_form.addRow("Aspect ratio (W/H):", figure_aspect_ratio_spin)

        axes_aspect_combo = QtWidgets.QComboBox(axes_tab)
        axes_aspect_combo.addItem("Auto", "auto")
        axes_aspect_combo.addItem("Equal", "equal")
        axes_aspect_combo.addItem("Custom (Y/X)", "custom")
        axes_aspect_ratio_spin = QtWidgets.QDoubleSpinBox(axes_tab)
        axes_aspect_ratio_spin.setRange(0.05, 20.0)
        axes_aspect_ratio_spin.setSingleStep(0.05)
        axes_aspect_ratio_spin.setDecimals(3)
        axes_aspect_ratio_spin.setValue(1.0)
        axes_aspect_combo.currentIndexChanged.connect(self._sync_aspect_controls)
        figure_width_auto_cb.toggled.connect(self._sync_aspect_controls)
        figure_height_auto_cb.toggled.connect(self._sync_aspect_controls)
        figure_aspect_mode_combo.currentIndexChanged.connect(self._sync_aspect_controls)
        axes_form.addRow("Axes aspect:", axes_aspect_combo)
        axes_form.addRow("Aspect ratio (Y/X):", axes_aspect_ratio_spin)

        x_scale_combo = QtWidgets.QComboBox(axes_tab)
        x_scale_combo.addItem("Linear", "linear")
        x_scale_combo.addItem("Log", "log")
        y_scale_combo = QtWidgets.QComboBox(axes_tab)
        y_scale_combo.addItem("Linear", "linear")
        y_scale_combo.addItem("Log", "log")
        axes_form.addRow("X scale:", x_scale_combo)
        axes_form.addRow("Y scale:", y_scale_combo)

        x_value_factor_edit = QtWidgets.QLineEdit(axes_tab)
        x_value_factor_edit.setPlaceholderText("1 (for example: 10^-3)")
        x_value_factor_edit.setText("1")
        y_value_factor_edit = QtWidgets.QLineEdit(axes_tab)
        y_value_factor_edit.setPlaceholderText("1 (for example: 10^-3)")
        y_value_factor_edit.setText("1")
        reflect_x_scale_units_cb = QtWidgets.QCheckBox(
            "Reflect X factor in X label unit",
            axes_tab,
        )
        reflect_y_scale_units_cb = QtWidgets.QCheckBox(
            "Reflect Y factor in Y label unit",
            axes_tab,
        )
        axes_form.addRow("X value factor:", x_value_factor_edit)
        axes_form.addRow("Y value factor:", y_value_factor_edit)
        axes_form.addRow(reflect_x_scale_units_cb)
        axes_form.addRow(reflect_y_scale_units_cb)

        x_min_edit = QtWidgets.QLineEdit(axes_tab)
        x_min_edit.setPlaceholderText("auto")
        x_max_edit = QtWidgets.QLineEdit(axes_tab)
        x_max_edit.setPlaceholderText("auto")
        y_min_edit = QtWidgets.QLineEdit(axes_tab)
        y_min_edit.setPlaceholderText("auto")
        y_max_edit = QtWidgets.QLineEdit(axes_tab)
        y_max_edit.setPlaceholderText("auto")
        axes_form.addRow("X min:", x_min_edit)
        axes_form.addRow("X max:", x_max_edit)
        axes_form.addRow("Y min:", y_min_edit)
        axes_form.addRow("Y max:", y_max_edit)

        show_grid_cb = QtWidgets.QCheckBox("Show grid", axes_tab)
        show_grid_cb.setChecked(False)
        axes_form.addRow(show_grid_cb)

        tick_length_spin = QtWidgets.QDoubleSpinBox(ticks_tab)
        tick_length_spin.setRange(0.0, 40.0)
        tick_length_spin.setSingleStep(0.5)
        tick_length_spin.setValue(3.5)
        tick_width_spin = QtWidgets.QDoubleSpinBox(ticks_tab)
        tick_width_spin.setRange(0.1, 10.0)
        tick_width_spin.setSingleStep(0.1)
        tick_width_spin.setValue(0.8)
        ticks_form.addRow("Tick length:", tick_length_spin)
        ticks_form.addRow("Tick width:", tick_width_spin)

        x_tick_mode_combo = QtWidgets.QComboBox(ticks_tab)
        x_tick_mode_combo.addItem("Auto", "auto")
        x_tick_mode_combo.addItem("By increment", "step")
        x_tick_mode_combo.addItem("By count", "count")
        y_tick_mode_combo = QtWidgets.QComboBox(ticks_tab)
        y_tick_mode_combo.addItem("Auto", "auto")
        y_tick_mode_combo.addItem("By increment", "step")
        y_tick_mode_combo.addItem("By count", "count")
        x_tick_step_edit = QtWidgets.QLineEdit(ticks_tab)
        x_tick_step_edit.setPlaceholderText("auto")
        y_tick_step_edit = QtWidgets.QLineEdit(ticks_tab)
        y_tick_step_edit.setPlaceholderText("auto")
        x_tick_count_spin = QtWidgets.QSpinBox(ticks_tab)
        x_tick_count_spin.setRange(2, 20)
        x_tick_count_spin.setValue(5)
        y_tick_count_spin = QtWidgets.QSpinBox(ticks_tab)
        y_tick_count_spin.setRange(2, 20)
        y_tick_count_spin.setValue(5)
        x_tick_mode_combo.currentIndexChanged.connect(self._sync_tick_mode_inputs)
        y_tick_mode_combo.currentIndexChanged.connect(self._sync_tick_mode_inputs)
        ticks_form.addRow("X ticks:", x_tick_mode_combo)
        ticks_form.addRow("X increment:", x_tick_step_edit)
        ticks_form.addRow("X count:", x_tick_count_spin)
        ticks_form.addRow("Y ticks:", y_tick_mode_combo)
        ticks_form.addRow("Y increment:", y_tick_step_edit)
        ticks_form.addRow("Y count:", y_tick_count_spin)

        show_legend_cb = QtWidgets.QCheckBox("Show legend", legend_tab)
        show_legend_cb.setChecked(True)
        legend_form.addRow(show_legend_cb)

        legend_location_combo = QtWidgets.QComboBox(legend_tab)
        for label, token in self._legend_location_choices():
            legend_location_combo.addItem(label, token)
        legend_form.addRow("Location:", legend_location_combo)
        legend_orientation_combo = QtWidgets.QComboBox(legend_tab)
        for label, token in self._legend_orientation_choices():
            legend_orientation_combo.addItem(label, token)
        legend_form.addRow("Orientation:", legend_orientation_combo)

        legend_font_spin = QtWidgets.QSpinBox(legend_tab)
        legend_font_spin.setRange(6, 96)
        legend_font_spin.setValue(10)
        legend_columns_spin = QtWidgets.QSpinBox(legend_tab)
        legend_columns_spin.setRange(1, 12)
        legend_columns_spin.setValue(1)
        legend_form.addRow("Font size:", legend_font_spin)
        legend_form.addRow("Columns:", legend_columns_spin)

        legend_show_symbols_cb = QtWidgets.QCheckBox("Show symbols", legend_tab)
        legend_show_symbols_cb.setChecked(True)
        legend_text_follow_colors_cb = QtWidgets.QCheckBox(
            "Text follows line colors",
            legend_tab,
        )
        legend_text_follow_colors_cb.setChecked(True)
        legend_draggable_cb = QtWidgets.QCheckBox("Draggable legend", legend_tab)
        legend_draggable_cb.setChecked(True)
        legend_form.addRow(legend_show_symbols_cb)
        legend_form.addRow(legend_text_follow_colors_cb)
        legend_form.addRow(legend_draggable_cb)

        button_panel = QtWidgets.QWidget(panel or self)
        button_row = QtWidgets.QHBoxLayout(button_panel)
        button_row.setContentsMargins(0, 2, 0, 0)
        button_row.setSpacing(6)
        apply_current_btn = QtWidgets.QPushButton("Apply current graph", button_panel)
        apply_current_btn.clicked.connect(lambda: self._apply_graph_format(apply_all=False))
        apply_all_btn = QtWidgets.QPushButton("Apply all graphs", button_panel)
        apply_all_btn.clicked.connect(lambda: self._apply_graph_format(apply_all=True))
        refresh_btn = QtWidgets.QPushButton("Read from current", button_panel)
        refresh_btn.clicked.connect(self._sync_graph_format_controls_from_current_axes)
        button_row.addWidget(apply_current_btn)
        button_row.addWidget(apply_all_btn)
        button_row.addWidget(refresh_btn)
        button_row.addStretch(1)

        graph_anchor_section, graph_anchor_layout = create_toolbar_section(
            "Graph formatting",
            parent=panel or self,
        )
        graph_anchor_note = QtWidgets.QLabel(
            "Opens in a separate movable window (Origin-style).",
            graph_anchor_section,
        )
        graph_anchor_note.setWordWrap(True)
        graph_anchor_layout.addWidget(graph_anchor_note)
        open_graph_format_btn = QtWidgets.QPushButton(
            "Open graph formatting...",
            graph_anchor_section,
        )
        open_graph_format_btn.clicked.connect(self._open_graph_format_dialog)
        graph_anchor_layout.addWidget(open_graph_format_btn)
        layout.addWidget(graph_anchor_section)
        self._graph_format_anchor_section = graph_anchor_section

        self._graph_format_controls = {
            "section": graph_section,
            "format_tabs": format_tabs,
            "title_edit": title_edit,
            "x_label_edit": x_label_edit,
            "y_label_edit": y_label_edit,
            "title_visible_cb": title_visible_cb,
            "x_label_visible_cb": x_label_visible_cb,
            "y_label_visible_cb": y_label_visible_cb,
            "title_font_spin": title_font_spin,
            "label_font_spin": label_font_spin,
            "tick_font_spin": tick_font_spin,
            "tick_length_spin": tick_length_spin,
            "tick_width_spin": tick_width_spin,
            "line_width_spin": line_width_spin,
            "marker_size_spin": marker_size_spin,
            "figure_width_spin": figure_width_spin,
            "figure_height_spin": figure_height_spin,
            "figure_width_auto_cb": figure_width_auto_cb,
            "figure_height_auto_cb": figure_height_auto_cb,
            "figure_aspect_mode_combo": figure_aspect_mode_combo,
            "figure_aspect_ratio_spin": figure_aspect_ratio_spin,
            "axes_aspect_combo": axes_aspect_combo,
            "axes_aspect_ratio_spin": axes_aspect_ratio_spin,
            "x_scale_combo": x_scale_combo,
            "y_scale_combo": y_scale_combo,
            "x_value_factor_edit": x_value_factor_edit,
            "y_value_factor_edit": y_value_factor_edit,
            "reflect_x_scale_units_cb": reflect_x_scale_units_cb,
            "reflect_y_scale_units_cb": reflect_y_scale_units_cb,
            "x_tick_mode_combo": x_tick_mode_combo,
            "y_tick_mode_combo": y_tick_mode_combo,
            "x_tick_step_edit": x_tick_step_edit,
            "y_tick_step_edit": y_tick_step_edit,
            "x_tick_count_spin": x_tick_count_spin,
            "y_tick_count_spin": y_tick_count_spin,
            "x_min_edit": x_min_edit,
            "x_max_edit": x_max_edit,
            "y_min_edit": y_min_edit,
            "y_max_edit": y_max_edit,
            "show_grid_cb": show_grid_cb,
            "show_legend_cb": show_legend_cb,
            "legend_location_combo": legend_location_combo,
            "legend_orientation_combo": legend_orientation_combo,
            "legend_font_spin": legend_font_spin,
            "legend_columns_spin": legend_columns_spin,
            "legend_show_symbols_cb": legend_show_symbols_cb,
            "legend_text_follow_colors_cb": legend_text_follow_colors_cb,
            "legend_draggable_cb": legend_draggable_cb,
            "button_panel": button_panel,
            "apply_current_btn": apply_current_btn,
            "apply_all_btn": apply_all_btn,
            "refresh_btn": refresh_btn,
            "open_graph_format_btn": open_graph_format_btn,
        }
        self._apply_graph_option_defaults_to_controls(self._current_plotter_name)
        self._set_graph_format_dialog_section(graph_section)
        self._sync_tick_mode_inputs()
        self._sync_aspect_controls()

        container = QtWidgets.QFrame(panel or self)
        container.setObjectName("mw_plugin_settings_container")
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(8)
        layout.addWidget(container)

        self._plugin_settings_container = container
        self._plugin_settings_layout = container_layout

    def _control_widget(self, key: str) -> QtWidgets.QWidget | None:
        widget = self._graph_format_controls.get(key)
        return widget if isinstance(widget, QtWidgets.QWidget) else None

    def _graph_section_prefers_window(
        self,
        *,
        title: str,
        anchor: QtWidgets.QWidget | None,
    ) -> bool:
        if anchor is None:
            return False
        return title.strip().lower() == "graph formatting"

    def _is_graph_format_anchor(self, anchor: QtWidgets.QWidget | None) -> bool:
        expected = self._graph_format_anchor_section
        return bool(anchor is not None and expected is not None and anchor is expected)

    def _set_graph_settings_anchor(self, anchor: QtWidgets.QWidget | None) -> None:
        if self._is_graph_format_anchor(anchor):
            self._open_graph_format_dialog()
            return
        super()._set_graph_settings_anchor(anchor)

    def _ensure_graph_format_dialog(self) -> QtWidgets.QDialog:
        dialog = self._graph_format_dialog
        if isinstance(dialog, QtWidgets.QDialog):
            return dialog

        dialog = QtWidgets.QDialog(self, QtCore.Qt.WindowType.Window)
        dialog.setObjectName("mw_graph_format_dialog")
        dialog.setWindowTitle("Graph formatting")
        dialog.setModal(False)
        dialog.resize(760, 680)

        root_layout = QtWidgets.QVBoxLayout(dialog)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        container = QtWidgets.QWidget(dialog)
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(8)
        container_layout.addStretch(1)
        root_layout.addWidget(container, 1)

        self._graph_format_dialog = dialog
        self._graph_format_dialog_container = container
        self._graph_format_dialog_layout = container_layout
        self._graph_format_dialog_root_layout = root_layout
        return dialog

    def _set_graph_format_dialog_section(self, section: QtWidgets.QWidget) -> None:
        dialog = self._ensure_graph_format_dialog()
        container = self._graph_format_dialog_container
        layout = self._graph_format_dialog_layout
        if container is None or layout is None:
            return
        root_layout = self._graph_format_dialog_root_layout

        parent = section.parentWidget()
        if isinstance(parent, QtWidgets.QWidget):
            parent_layout = parent.layout()
            if isinstance(parent_layout, QtWidgets.QLayout):
                parent_layout.removeWidget(section)

        if section.parentWidget() is not container:
            section.setParent(container)
        section.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        section_index = layout.indexOf(section)
        if section_index < 0:
            insert_index = max(0, layout.count() - 1)
            layout.insertWidget(insert_index, section)
        section.show()

        button_panel = self._control_widget("button_panel")
        if isinstance(button_panel, QtWidgets.QWidget) and isinstance(root_layout, QtWidgets.QVBoxLayout):
            panel_parent = button_panel.parentWidget()
            if isinstance(panel_parent, QtWidgets.QWidget):
                panel_parent_layout = panel_parent.layout()
                if isinstance(panel_parent_layout, QtWidgets.QLayout):
                    panel_parent_layout.removeWidget(button_panel)
            if button_panel.parentWidget() is not dialog:
                button_panel.setParent(dialog)
            if root_layout.indexOf(button_panel) < 0:
                root_layout.addWidget(button_panel, 0)
            button_panel.show()
        dialog.adjustSize()

    def _open_graph_format_dialog(
        self,
        checked: bool = False,
        *,
        focus_key: str | None = None,
        select_all: bool = False,
    ) -> bool:
        del checked
        dialog = self._ensure_graph_format_dialog()
        self._sync_graph_format_controls_from_current_axes()
        if dialog.isMinimized():
            dialog.showNormal()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if focus_key:
            self._focus_graph_format_control(focus_key, select_all=select_all)
        return True

    def _set_graph_format_controls_enabled(self, enabled: bool) -> None:
        for key, widget in self._graph_format_controls.items():
            if key in {"section"}:
                continue
            if isinstance(widget, QtWidgets.QWidget):
                widget.setEnabled(enabled)
        if enabled:
            self._sync_tick_mode_inputs()
            self._sync_aspect_controls()

    def _float_from_edit(self, key: str) -> float | None:
        widget = self._control_widget(key)
        if not isinstance(widget, QtWidgets.QLineEdit):
            return None
        text = widget.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _safe_numeric_expression(expression: str) -> float | None:
        text = str(expression or "").strip()
        if not text:
            return 1.0
        normalized = text.replace("^", "**").replace("×", "*")
        try:
            tree = ast.parse(normalized, mode="eval")
        except SyntaxError:
            return None

        def _eval(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return _eval(node.body)
            if isinstance(node, ast.Constant):
                if isinstance(node.value, (int, float)):
                    return float(node.value)
                raise ValueError
            if isinstance(node, ast.UnaryOp):
                value = _eval(node.operand)
                if isinstance(node.op, ast.UAdd):
                    return value
                if isinstance(node.op, ast.USub):
                    return -value
                raise ValueError
            if isinstance(node, ast.BinOp):
                left = _eval(node.left)
                right = _eval(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
                if isinstance(node.op, ast.Pow):
                    return left ** right
                raise ValueError
            raise ValueError

        try:
            value = float(_eval(tree))
        except Exception:
            return None
        if not math.isfinite(value):
            return None
        return value

    @staticmethod
    def _factor_label_text(factor: float) -> str:
        if not math.isfinite(factor):
            return "1"
        if math.isclose(factor, 1.0, rel_tol=1e-12, abs_tol=1e-12):
            return "1"
        sign = "-" if factor < 0 else ""
        abs_factor = abs(factor)
        if abs_factor > 0:
            exponent = math.log10(abs_factor)
            rounded_exponent = int(round(exponent))
            if math.isclose(exponent, rounded_exponent, rel_tol=1e-10, abs_tol=1e-10):
                return f"{sign}10^{rounded_exponent}"
        return f"{factor:.6g}"

    @staticmethod
    def _superscript_number(value: int) -> str:
        translation = str.maketrans("-0123456789", "\u207b\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079")
        return str(int(value)).translate(translation)

    @classmethod
    def _factor_display_text(cls, factor: float) -> str:
        if not math.isfinite(factor):
            return "1"
        if math.isclose(factor, 1.0, rel_tol=1e-12, abs_tol=1e-12):
            return "1"
        sign = "-" if factor < 0 else ""
        abs_factor = abs(factor)
        exponent = math.log10(abs_factor) if abs_factor > 0 else 0.0
        rounded_exponent = int(round(exponent))
        if abs_factor > 0 and math.isclose(exponent, rounded_exponent, rel_tol=1e-10, abs_tol=1e-10):
            return f"{sign}\u00d710{cls._superscript_number(rounded_exponent)}"
        return f"{factor:.6g}"

    @staticmethod
    def _factor_edit_text(factor: float) -> str:
        return PyPlotWorkbench._factor_label_text(factor)

    @staticmethod
    def _format_scaled_tick(value: float, factor: float) -> str:
        scaled = value * factor
        if not math.isfinite(scaled):
            return ""
        if math.isclose(scaled, 0.0, abs_tol=1e-15):
            return "0"
        return f"{scaled:.6g}"

    def _axis_base_label(self, axes: Any, axis: str) -> str:
        key = f"_mw_{axis}_base_label"
        candidate = getattr(axes, key, None)
        if isinstance(candidate, str):
            return candidate
        try:
            if axis == "x":
                label = str(axes.get_xlabel() or "")
            else:
                label = str(axes.get_ylabel() or "")
        except Exception:
            label = ""
        return label

    def _scaled_axis_label(self, base_label: str, factor: float, reflect_in_unit: bool) -> str:
        label = str(base_label or "")
        normalize_units = getattr(self, "_label_units_to_brackets", None)
        if callable(normalize_units):
            try:
                label = normalize_units(label)
            except Exception:
                pass
        if not reflect_in_unit or math.isclose(factor, 1.0, rel_tol=1e-12, abs_tol=1e-12):
            return label
        reflected_factor = 1.0 / factor if not math.isclose(factor, 0.0, abs_tol=1e-15) else 1.0
        factor_text = self._factor_display_text(reflected_factor)
        left = label.rfind("[")
        right = label.rfind("]")
        if left >= 0 and right > left:
            unit = label[left + 1 : right].strip()
            prefix = label[:left].rstrip()
            if unit:
                return f"{prefix} [{unit} {factor_text}]".strip()
            return f"{prefix} [{factor_text}]".strip()
        stripped = label.strip()
        if not stripped:
            return factor_text
        return f"{stripped} [{factor_text}]"

    def _apply_axis_factor_formatter(self, axis_obj: Any, factor: float, *, axis_name: str) -> None:
        offset_text = getattr(axis_obj, "get_offset_text", lambda: None)()
        def _hide_offset_text() -> None:
            if offset_text is None:
                return
            try:
                offset_text.set_visible(False)
                offset_text.set_text("")
            except Exception:
                pass

        if math.isclose(factor, 1.0, rel_tol=1e-12, abs_tol=1e-12):
            owner = getattr(axis_obj, "axes", None)
            scale = "linear"
            try:
                if axis_name == "x":
                    scale = str(owner.get_xscale() if owner is not None else "linear").lower()
                else:
                    scale = str(owner.get_yscale() if owner is not None else "linear").lower()
            except Exception:
                scale = "linear"
            if scale == "log":
                axis_obj.set_major_formatter(mticker.LogFormatterSciNotation())
            else:
                axis_obj.set_major_formatter(mticker.ScalarFormatter())
            return
        axis_obj.set_major_formatter(
            mticker.FuncFormatter(
                lambda value, _pos, _factor=factor: self._format_scaled_tick(value, _factor)
            )
        )
        _hide_offset_text()

    def _sync_tick_mode_inputs(self) -> None:
        if self._graph_format_updating:
            return
        for axis in ("x", "y"):
            mode_widget = self._control_widget(f"{axis}_tick_mode_combo")
            step_widget = self._control_widget(f"{axis}_tick_step_edit")
            count_widget = self._control_widget(f"{axis}_tick_count_spin")
            mode = (
                str(mode_widget.currentData())
                if isinstance(mode_widget, QtWidgets.QComboBox)
                else "auto"
            )
            if isinstance(step_widget, QtWidgets.QLineEdit):
                step_widget.setEnabled(mode == "step")
            if isinstance(count_widget, QtWidgets.QSpinBox):
                count_widget.setEnabled(mode == "count")

    def _sync_aspect_controls(self) -> None:
        if self._graph_format_updating:
            return
        figure_width_spin = self._control_widget("figure_width_spin")
        figure_height_spin = self._control_widget("figure_height_spin")
        figure_width_auto_cb = self._control_widget("figure_width_auto_cb")
        figure_height_auto_cb = self._control_widget("figure_height_auto_cb")
        figure_aspect_mode_combo = self._control_widget("figure_aspect_mode_combo")
        figure_aspect_ratio_spin = self._control_widget("figure_aspect_ratio_spin")
        if isinstance(figure_width_spin, QtWidgets.QDoubleSpinBox) and isinstance(figure_width_auto_cb, QtWidgets.QCheckBox):
            figure_width_spin.setEnabled(not figure_width_auto_cb.isChecked())
        if isinstance(figure_height_spin, QtWidgets.QDoubleSpinBox) and isinstance(figure_height_auto_cb, QtWidgets.QCheckBox):
            figure_height_spin.setEnabled(not figure_height_auto_cb.isChecked())
        figure_mode = (
            str(figure_aspect_mode_combo.currentData())
            if isinstance(figure_aspect_mode_combo, QtWidgets.QComboBox)
            else "auto"
        )
        if isinstance(figure_aspect_ratio_spin, QtWidgets.QDoubleSpinBox):
            figure_aspect_ratio_spin.setEnabled(figure_mode == "custom")

        mode_widget = self._control_widget("axes_aspect_combo")
        ratio_widget = self._control_widget("axes_aspect_ratio_spin")
        mode = (
            str(mode_widget.currentData())
            if isinstance(mode_widget, QtWidgets.QComboBox)
            else "auto"
        )
        if isinstance(ratio_widget, QtWidgets.QDoubleSpinBox):
            ratio_widget.setEnabled(mode == "custom")

    @staticmethod
    def _axes_aspect_from_axes(axes: Any) -> tuple[str, float]:
        try:
            aspect_value = axes.get_aspect()
        except Exception:
            return "auto", 1.0
        if isinstance(aspect_value, str):
            token = aspect_value.strip().lower()
            if token == "auto":
                return "auto", 1.0
            if token == "equal":
                return "equal", 1.0
            try:
                numeric = float(token)
            except Exception:
                return "auto", 1.0
            if math.isfinite(numeric) and numeric > 0:
                return "custom", numeric
            return "auto", 1.0
        try:
            numeric = float(aspect_value)
        except Exception:
            return "auto", 1.0
        if not math.isfinite(numeric) or numeric <= 0:
            return "auto", 1.0
        if math.isclose(numeric, 1.0, rel_tol=1e-3, abs_tol=1e-3):
            return "equal", 1.0
        return "custom", numeric

    @staticmethod
    def _multiple_locator_step(locator: Any) -> float | None:
        for attr in ("base", "_base"):
            value = getattr(locator, attr, None)
            try:
                step = float(value)
            except Exception:
                continue
            if math.isfinite(step) and step > 0:
                return step
        edge = getattr(locator, "_edge", None)
        step = getattr(edge, "step", None)
        try:
            step_value = float(step)
        except Exception:
            return None
        if not math.isfinite(step_value) or step_value <= 0:
            return None
        return step_value

    @staticmethod
    def _tick_mode_from_axis(axis_obj: Any) -> tuple[str, float | None, int]:
        locator = None
        try:
            locator = axis_obj.get_major_locator()
        except Exception:
            locator = None
        if isinstance(locator, mticker.MultipleLocator):
            step = PyPlotWorkbench._multiple_locator_step(locator)
            return "step", step, 5
        if isinstance(locator, mticker.AutoLocator):
            return "auto", None, 5
        if isinstance(locator, mticker.MaxNLocator):
            nbins = getattr(locator, "_nbins", None)
            try:
                count = int(nbins) + 1
            except Exception:
                count = 5
            count = max(2, min(count, 20))
            return "count", None, count
        return "auto", None, 5

    @staticmethod
    def _apply_tick_locator(
        axis_obj: Any,
        mode: str,
        *,
        step: float | None,
        count: int,
    ) -> None:
        resolved = str(mode or "auto").strip().lower()
        if resolved == "step" and step is not None and step > 0:
            axis_obj.set_major_locator(mticker.MultipleLocator(step))
            return
        if resolved == "count":
            count = max(2, int(count))
            axis_obj.set_major_locator(
                mticker.MaxNLocator(nbins=max(1, count - 1), min_n_ticks=count)
            )
            return
        axis_obj.set_major_locator(mticker.AutoLocator())

    @staticmethod
    def _tick_style_from_axes(axes: Any) -> tuple[float, float]:
        try:
            tick = axes.xaxis.get_major_ticks()[0]
            length = float(tick.tick1line.get_markersize())
            width = float(tick.tick1line.get_markeredgewidth())
            if math.isfinite(length) and math.isfinite(width):
                return length, width
        except Exception:
            pass
        return 3.5, 0.8

    @staticmethod
    def _tick_font_from_axes(axes: Any) -> float:
        for getter in (axes.get_xticklabels, axes.get_yticklabels):
            try:
                labels = getter()
            except Exception:
                labels = []
            for label in labels:
                try:
                    size = float(label.get_fontsize())
                except Exception:
                    continue
                if math.isfinite(size) and size > 0:
                    return size
        return 10.0

    def _sync_graph_format_controls_from_current_axes(self) -> None:
        axes = self._current_axes()
        if axes is None:
            self._apply_graph_option_defaults_to_controls(self._current_plotter_name)
            self._set_graph_format_controls_enabled(False)
            return

        self._set_graph_format_controls_enabled(True)
        title_edit = self._control_widget("title_edit")
        x_label_edit = self._control_widget("x_label_edit")
        y_label_edit = self._control_widget("y_label_edit")
        title_visible_cb = self._control_widget("title_visible_cb")
        x_label_visible_cb = self._control_widget("x_label_visible_cb")
        y_label_visible_cb = self._control_widget("y_label_visible_cb")
        title_font_spin = self._control_widget("title_font_spin")
        label_font_spin = self._control_widget("label_font_spin")
        tick_font_spin = self._control_widget("tick_font_spin")
        tick_length_spin = self._control_widget("tick_length_spin")
        tick_width_spin = self._control_widget("tick_width_spin")
        line_width_spin = self._control_widget("line_width_spin")
        marker_size_spin = self._control_widget("marker_size_spin")
        figure_width_spin = self._control_widget("figure_width_spin")
        figure_height_spin = self._control_widget("figure_height_spin")
        figure_width_auto_cb = self._control_widget("figure_width_auto_cb")
        figure_height_auto_cb = self._control_widget("figure_height_auto_cb")
        figure_aspect_mode_combo = self._control_widget("figure_aspect_mode_combo")
        figure_aspect_ratio_spin = self._control_widget("figure_aspect_ratio_spin")
        axes_aspect_combo = self._control_widget("axes_aspect_combo")
        axes_aspect_ratio_spin = self._control_widget("axes_aspect_ratio_spin")
        x_scale_combo = self._control_widget("x_scale_combo")
        y_scale_combo = self._control_widget("y_scale_combo")
        x_value_factor_edit = self._control_widget("x_value_factor_edit")
        y_value_factor_edit = self._control_widget("y_value_factor_edit")
        reflect_x_scale_units_cb = self._control_widget("reflect_x_scale_units_cb")
        reflect_y_scale_units_cb = self._control_widget("reflect_y_scale_units_cb")
        x_tick_mode_combo = self._control_widget("x_tick_mode_combo")
        y_tick_mode_combo = self._control_widget("y_tick_mode_combo")
        x_tick_step_edit = self._control_widget("x_tick_step_edit")
        y_tick_step_edit = self._control_widget("y_tick_step_edit")
        x_tick_count_spin = self._control_widget("x_tick_count_spin")
        y_tick_count_spin = self._control_widget("y_tick_count_spin")
        x_min_edit = self._control_widget("x_min_edit")
        x_max_edit = self._control_widget("x_max_edit")
        y_min_edit = self._control_widget("y_min_edit")
        y_max_edit = self._control_widget("y_max_edit")
        show_grid_cb = self._control_widget("show_grid_cb")
        show_legend_cb = self._control_widget("show_legend_cb")
        legend_location_combo = self._control_widget("legend_location_combo")
        legend_orientation_combo = self._control_widget("legend_orientation_combo")
        legend_font_spin = self._control_widget("legend_font_spin")
        legend_columns_spin = self._control_widget("legend_columns_spin")
        legend_show_symbols_cb = self._control_widget("legend_show_symbols_cb")
        legend_text_follow_colors_cb = self._control_widget("legend_text_follow_colors_cb")
        legend_draggable_cb = self._control_widget("legend_draggable_cb")

        self._graph_format_updating = True
        try:
            if isinstance(title_edit, QtWidgets.QLineEdit):
                title_edit.setText(str(getattr(axes, "get_title", lambda: "")() or ""))
            if isinstance(x_label_edit, QtWidgets.QLineEdit):
                x_label_edit.setText(self._axis_base_label(axes, "x"))
            if isinstance(y_label_edit, QtWidgets.QLineEdit):
                y_label_edit.setText(self._axis_base_label(axes, "y"))
            if isinstance(title_visible_cb, QtWidgets.QCheckBox):
                try:
                    title_visible_cb.setChecked(bool(axes.title.get_visible()))
                except Exception:
                    title_visible_cb.setChecked(True)
            if isinstance(x_label_visible_cb, QtWidgets.QCheckBox):
                try:
                    x_label_visible_cb.setChecked(bool(axes.xaxis.label.get_visible()))
                except Exception:
                    x_label_visible_cb.setChecked(True)
            if isinstance(y_label_visible_cb, QtWidgets.QCheckBox):
                try:
                    y_label_visible_cb.setChecked(bool(axes.yaxis.label.get_visible()))
                except Exception:
                    y_label_visible_cb.setChecked(True)

            if isinstance(title_font_spin, QtWidgets.QSpinBox):
                try:
                    title_font_spin.setValue(int(round(float(axes.title.get_fontsize()))))
                except Exception:
                    pass
            if isinstance(label_font_spin, QtWidgets.QSpinBox):
                try:
                    label_size = float(axes.xaxis.label.get_fontsize())
                except Exception:
                    label_size = 12.0
                if not math.isfinite(label_size) or label_size <= 0:
                    label_size = 12.0
                label_font_spin.setValue(int(round(label_size)))
            if isinstance(tick_font_spin, QtWidgets.QSpinBox):
                tick_font_spin.setValue(int(round(self._tick_font_from_axes(axes))))
            length, width = self._tick_style_from_axes(axes)
            if isinstance(tick_length_spin, QtWidgets.QDoubleSpinBox):
                tick_length_spin.setValue(length)
            if isinstance(tick_width_spin, QtWidgets.QDoubleSpinBox):
                tick_width_spin.setValue(width)

            line_width = 1.5
            marker_size = 6.0
            try:
                lines = list(axes.get_lines())
            except Exception:
                lines = []
            if lines:
                try:
                    line_width = float(lines[0].get_linewidth())
                except Exception:
                    pass
                for line in lines:
                    try:
                        marker = str(line.get_marker()).strip().lower()
                    except Exception:
                        marker = ""
                    if marker and marker != "none":
                        try:
                            marker_size = float(line.get_markersize())
                        except Exception:
                            pass
                        break
            if isinstance(line_width_spin, QtWidgets.QDoubleSpinBox):
                line_width_spin.setValue(max(0.1, line_width))
            if isinstance(marker_size_spin, QtWidgets.QDoubleSpinBox):
                marker_size_spin.setValue(max(0.1, marker_size))

            plugin_name = self._plugin_name_for_axes(axes)
            graph_options = self._effective_graph_options(plugin_name)
            figure = getattr(axes, "figure", None)
            try:
                size_inches = figure.get_size_inches() if figure is not None else None
            except Exception:
                size_inches = None
            if (
                size_inches is not None
                and hasattr(size_inches, "__len__")
                and len(size_inches) >= 2
            ):
                try:
                    width_in = float(size_inches[0])
                    height_in = float(size_inches[1])
                except Exception:
                    width_in = height_in = None
                if isinstance(figure_width_spin, QtWidgets.QDoubleSpinBox) and width_in:
                    figure_width_spin.setValue(max(10.0, width_in * MM_PER_INCH))
                if isinstance(figure_height_spin, QtWidgets.QDoubleSpinBox) and height_in:
                    figure_height_spin.setValue(max(10.0, height_in * MM_PER_INCH))
            if isinstance(figure_width_auto_cb, QtWidgets.QCheckBox):
                figure_width_auto_cb.setChecked(bool(graph_options.get("figure_width_auto", False)))
            if isinstance(figure_height_auto_cb, QtWidgets.QCheckBox):
                figure_height_auto_cb.setChecked(bool(graph_options.get("figure_height_auto", True)))
            if isinstance(figure_aspect_mode_combo, QtWidgets.QComboBox):
                mode = str(graph_options.get("figure_aspect_mode", "auto"))
                index = figure_aspect_mode_combo.findData(mode)
                if index >= 0:
                    figure_aspect_mode_combo.setCurrentIndex(index)
            if isinstance(figure_aspect_ratio_spin, QtWidgets.QDoubleSpinBox):
                try:
                    ratio = float(graph_options.get("figure_aspect_ratio", DEFAULT_FIGURE_ASPECT_RATIO))
                except Exception:
                    ratio = float(DEFAULT_FIGURE_ASPECT_RATIO)
                if not math.isfinite(ratio) or ratio <= 0:
                    ratio = float(DEFAULT_FIGURE_ASPECT_RATIO)
                figure_aspect_ratio_spin.setValue(max(0.2, min(ratio, 20.0)))

            aspect_mode, aspect_ratio = self._axes_aspect_from_axes(axes)
            if isinstance(axes_aspect_combo, QtWidgets.QComboBox):
                index = axes_aspect_combo.findData(aspect_mode)
                if index >= 0:
                    axes_aspect_combo.setCurrentIndex(index)
            if isinstance(axes_aspect_ratio_spin, QtWidgets.QDoubleSpinBox):
                axes_aspect_ratio_spin.setValue(max(0.05, min(float(aspect_ratio), 20.0)))

            x_scale = str(getattr(axes, "get_xscale", lambda: "linear")() or "linear").lower()
            y_scale = str(getattr(axes, "get_yscale", lambda: "linear")() or "linear").lower()
            if isinstance(x_scale_combo, QtWidgets.QComboBox):
                index = x_scale_combo.findData(x_scale)
                if index >= 0:
                    x_scale_combo.setCurrentIndex(index)
            if isinstance(y_scale_combo, QtWidgets.QComboBox):
                index = y_scale_combo.findData(y_scale)
                if index >= 0:
                    y_scale_combo.setCurrentIndex(index)
            x_factor = getattr(axes, "_mw_x_value_factor", 1.0)
            y_factor = getattr(axes, "_mw_y_value_factor", 1.0)
            try:
                x_factor_value = float(x_factor)
            except Exception:
                x_factor_value = 1.0
            try:
                y_factor_value = float(y_factor)
            except Exception:
                y_factor_value = 1.0
            if not math.isfinite(x_factor_value) or math.isclose(x_factor_value, 0.0, abs_tol=1e-15):
                x_factor_value = 1.0
            if not math.isfinite(y_factor_value) or math.isclose(y_factor_value, 0.0, abs_tol=1e-15):
                y_factor_value = 1.0
            if isinstance(x_value_factor_edit, QtWidgets.QLineEdit):
                x_value_factor_edit.setText(self._factor_edit_text(x_factor_value))
            if isinstance(y_value_factor_edit, QtWidgets.QLineEdit):
                y_value_factor_edit.setText(self._factor_edit_text(y_factor_value))
            if isinstance(reflect_x_scale_units_cb, QtWidgets.QCheckBox):
                reflect_x_scale_units_cb.setChecked(bool(getattr(axes, "_mw_x_reflect_scale_in_unit", False)))
            if isinstance(reflect_y_scale_units_cb, QtWidgets.QCheckBox):
                reflect_y_scale_units_cb.setChecked(bool(getattr(axes, "_mw_y_reflect_scale_in_unit", False)))

            x_tick_mode, x_tick_step, x_tick_count = self._tick_mode_from_axis(axes.xaxis)
            y_tick_mode, y_tick_step, y_tick_count = self._tick_mode_from_axis(axes.yaxis)
            if isinstance(x_tick_mode_combo, QtWidgets.QComboBox):
                index = x_tick_mode_combo.findData(x_tick_mode)
                if index >= 0:
                    x_tick_mode_combo.setCurrentIndex(index)
            if isinstance(y_tick_mode_combo, QtWidgets.QComboBox):
                index = y_tick_mode_combo.findData(y_tick_mode)
                if index >= 0:
                    y_tick_mode_combo.setCurrentIndex(index)
            if isinstance(x_tick_step_edit, QtWidgets.QLineEdit):
                x_tick_step_edit.setText(
                    f"{x_tick_step:.6g}" if x_tick_step is not None and math.isfinite(x_tick_step) else ""
                )
            if isinstance(y_tick_step_edit, QtWidgets.QLineEdit):
                y_tick_step_edit.setText(
                    f"{y_tick_step:.6g}" if y_tick_step is not None and math.isfinite(y_tick_step) else ""
                )
            if isinstance(x_tick_count_spin, QtWidgets.QSpinBox):
                x_tick_count_spin.setValue(max(2, min(int(x_tick_count), 20)))
            if isinstance(y_tick_count_spin, QtWidgets.QSpinBox):
                y_tick_count_spin.setValue(max(2, min(int(y_tick_count), 20)))

            try:
                x_min, x_max = axes.get_xlim()
                y_min, y_max = axes.get_ylim()
            except Exception:
                x_min = x_max = y_min = y_max = None
            if isinstance(x_min_edit, QtWidgets.QLineEdit):
                x_min_edit.setText(f"{float(x_min):.6g}" if x_min is not None else "")
            if isinstance(x_max_edit, QtWidgets.QLineEdit):
                x_max_edit.setText(f"{float(x_max):.6g}" if x_max is not None else "")
            if isinstance(y_min_edit, QtWidgets.QLineEdit):
                y_min_edit.setText(f"{float(y_min):.6g}" if y_min is not None else "")
            if isinstance(y_max_edit, QtWidgets.QLineEdit):
                y_max_edit.setText(f"{float(y_max):.6g}" if y_max is not None else "")

            if isinstance(show_grid_cb, QtWidgets.QCheckBox):
                try:
                    grid_lines = list(axes.get_xgridlines()) + list(axes.get_ygridlines())
                    show_grid_cb.setChecked(any(bool(line.get_visible()) for line in grid_lines))
                except Exception:
                    show_grid_cb.setChecked(False)
            legend = None
            try:
                legend = axes.get_legend()
            except Exception:
                legend = None
            legend_state = self._legend_state_snapshot(legend, plugin_name=plugin_name)
            if isinstance(show_legend_cb, QtWidgets.QCheckBox):
                show_legend_cb.setChecked(bool(legend_state.get("visible", True)))
            if isinstance(legend_location_combo, QtWidgets.QComboBox):
                loc = str(legend_state.get("loc", "best"))
                idx = legend_location_combo.findData(loc)
                if idx < 0:
                    idx = legend_location_combo.findData("best")
                if idx >= 0:
                    legend_location_combo.setCurrentIndex(idx)
            if isinstance(legend_orientation_combo, QtWidgets.QComboBox):
                orientation = str(legend_state.get("orientation", "auto"))
                idx = legend_orientation_combo.findData(orientation)
                if idx < 0:
                    idx = legend_orientation_combo.findData("auto")
                if idx >= 0:
                    legend_orientation_combo.setCurrentIndex(idx)
            if isinstance(legend_font_spin, QtWidgets.QSpinBox):
                legend_font_spin.setValue(int(legend_state.get("font_size", 10)))
            if isinstance(legend_columns_spin, QtWidgets.QSpinBox):
                legend_columns_spin.setValue(max(1, int(legend_state.get("ncol", 1))))
            if isinstance(legend_show_symbols_cb, QtWidgets.QCheckBox):
                legend_show_symbols_cb.setChecked(bool(legend_state.get("show_symbols", True)))
            if isinstance(legend_text_follow_colors_cb, QtWidgets.QCheckBox):
                legend_text_follow_colors_cb.setChecked(
                    bool(legend_state.get("text_follows_handles", True))
                )
            if isinstance(legend_draggable_cb, QtWidgets.QCheckBox):
                legend_draggable_cb.setChecked(bool(legend_state.get("draggable", True)))
        finally:
            self._graph_format_updating = False
        self._sync_tick_mode_inputs()
        self._sync_aspect_controls()

    def _tab_for_axes(self, axes: Any) -> QtWidgets.QWidget | None:
        if axes is None:
            return None
        target_figure = getattr(axes, "figure", None)
        for tab, candidate in self._axes_by_tab.items():
            if not isinstance(tab, QtWidgets.QWidget):
                continue
            if candidate is axes:
                return tab
            candidate_figure = getattr(candidate, "figure", None)
            if target_figure is not None and candidate_figure is target_figure:
                return tab
        return None

    def _focus_graph_format_control(self, key: str, *, select_all: bool = False) -> None:
        widget = self._control_widget(key)
        if not isinstance(widget, QtWidgets.QWidget):
            return
        tabs = self._control_widget("format_tabs")
        if isinstance(tabs, QtWidgets.QTabWidget):
            for index in range(tabs.count()):
                page = tabs.widget(index)
                if page is None:
                    continue
                if page is widget or page.isAncestorOf(widget):
                    tabs.setCurrentIndex(index)
                    break

        def _apply_focus() -> None:
            try:
                if not isinstance(widget, QtWidgets.QWidget):
                    return
                widget.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
                if select_all and isinstance(widget, QtWidgets.QLineEdit):
                    widget.selectAll()
            except Exception:
                return

        QtCore.QTimer.singleShot(0, _apply_focus)

    def _open_shared_graph_format_from_double_click(
        self,
        *,
        axes: Any,
        text_field: str | None = None,
        axis: str | None = None,
        legend: bool = False,
        line: bool = False,
    ) -> bool:
        tab = self._tab_for_axes(axes)
        if isinstance(tab, QtWidgets.QWidget):
            index = self.tab_widget.indexOf(tab)
            if index >= 0 and self.tab_widget.currentIndex() != index:
                self.tab_widget.setCurrentIndex(index)

        focus_key: str | None = None
        select_all = False
        field_token = str(text_field or "").strip().lower()
        axis_token = str(axis or "").strip().lower()
        if field_token == "title":
            focus_key = "title_edit"
            select_all = True
        elif field_token == "x_label":
            focus_key = "x_label_edit"
            select_all = True
        elif field_token == "y_label":
            focus_key = "y_label_edit"
            select_all = True
        elif legend:
            focus_key = "show_legend_cb"
        elif line:
            focus_key = "line_width_spin"
        elif axis_token == "x":
            focus_key = "x_scale_combo"
        elif axis_token == "y":
            focus_key = "y_scale_combo"
        return self._open_graph_format_dialog(
            focus_key=focus_key,
            select_all=select_all,
        )

    def _target_axes(self, apply_all: bool) -> List[Any]:
        if not apply_all:
            axes = self._current_axes()
            return [axes] if axes is not None else []
        targets: List[Any] = []
        seen: set[int] = set()
        for axes in self._axes_by_tab.values():
            if axes is None:
                continue
            marker = id(axes)
            if marker in seen:
                continue
            seen.add(marker)
            targets.append(axes)
        return targets

    def _plugin_name_for_axes(self, axes: Any) -> str | None:
        axes_by_tab = getattr(self, "_axes_by_tab", {})
        tab_descriptors = getattr(self, "_tab_descriptors", {})
        for tab, candidate in axes_by_tab.items():
            if candidate is not axes:
                continue
            descriptor = tab_descriptors.get(tab)
            return self._tab_plugin_name(descriptor)
        return None

    def _apply_plugin_graph_option_override_for_axes(
        self,
        axes: Any,
        recommendations: Dict[str, float],
    ) -> bool:
        if axes is None or not isinstance(recommendations, dict):
            return False
        plugin_name = self._plugin_name_for_axes(axes) or self._current_plotter_name
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            return False

        existing_override = self._graph_option_defaults_by_plugin.get(str(plugin_name))
        payload = dict(existing_override) if isinstance(existing_override, dict) else {}
        updated = False
        for source_key, target_key in (
            ("title_font", "title_font"),
            ("label_font", "label_font"),
            ("tick_font", "tick_font"),
            ("legend_font_size", "legend_font_size"),
        ):
            value = recommendations.get(source_key)
            if value is None:
                continue
            try:
                numeric = float(value)
            except Exception:
                continue
            if not math.isfinite(numeric) or numeric <= 0.0:
                continue
            payload[target_key] = numeric
            updated = True
        if not updated:
            return False

        global_payload = dict(self._effective_graph_options(None))
        self._store_graph_option_defaults(
            global_payload=global_payload,
            plugin_key=str(plugin_name),
            plugin_override_enabled=True,
            plugin_payload=payload,
            refresh_open_graphs=True,
        )
        self._append_log(
            f"Applied graph option override for {plugin_name} using layout-fit font recommendations."
        )
        return True

    def _apply_graph_format(self, *, apply_all: bool) -> None:
        if self._graph_format_updating:
            return
        targets = self._target_axes(apply_all)
        if not targets:
            QtWidgets.QMessageBox.information(
                self,
                "Graph formatting",
                "Select a graph before applying formatting.",
            )
            return

        title_edit = self._control_widget("title_edit")
        x_label_edit = self._control_widget("x_label_edit")
        y_label_edit = self._control_widget("y_label_edit")
        title_visible_cb = self._control_widget("title_visible_cb")
        x_label_visible_cb = self._control_widget("x_label_visible_cb")
        y_label_visible_cb = self._control_widget("y_label_visible_cb")
        title_font_spin = self._control_widget("title_font_spin")
        label_font_spin = self._control_widget("label_font_spin")
        tick_font_spin = self._control_widget("tick_font_spin")
        tick_length_spin = self._control_widget("tick_length_spin")
        tick_width_spin = self._control_widget("tick_width_spin")
        line_width_spin = self._control_widget("line_width_spin")
        marker_size_spin = self._control_widget("marker_size_spin")
        figure_width_spin = self._control_widget("figure_width_spin")
        figure_height_spin = self._control_widget("figure_height_spin")
        figure_width_auto_cb = self._control_widget("figure_width_auto_cb")
        figure_height_auto_cb = self._control_widget("figure_height_auto_cb")
        figure_aspect_mode_combo = self._control_widget("figure_aspect_mode_combo")
        figure_aspect_ratio_spin = self._control_widget("figure_aspect_ratio_spin")
        axes_aspect_combo = self._control_widget("axes_aspect_combo")
        axes_aspect_ratio_spin = self._control_widget("axes_aspect_ratio_spin")
        x_scale_combo = self._control_widget("x_scale_combo")
        y_scale_combo = self._control_widget("y_scale_combo")
        x_value_factor_edit = self._control_widget("x_value_factor_edit")
        y_value_factor_edit = self._control_widget("y_value_factor_edit")
        reflect_x_scale_units_cb = self._control_widget("reflect_x_scale_units_cb")
        reflect_y_scale_units_cb = self._control_widget("reflect_y_scale_units_cb")
        x_tick_mode_combo = self._control_widget("x_tick_mode_combo")
        y_tick_mode_combo = self._control_widget("y_tick_mode_combo")
        x_tick_count_spin = self._control_widget("x_tick_count_spin")
        y_tick_count_spin = self._control_widget("y_tick_count_spin")
        show_grid_cb = self._control_widget("show_grid_cb")
        show_legend_cb = self._control_widget("show_legend_cb")
        legend_location_combo = self._control_widget("legend_location_combo")
        legend_orientation_combo = self._control_widget("legend_orientation_combo")
        legend_font_spin = self._control_widget("legend_font_spin")
        legend_columns_spin = self._control_widget("legend_columns_spin")
        legend_show_symbols_cb = self._control_widget("legend_show_symbols_cb")
        legend_text_follow_colors_cb = self._control_widget("legend_text_follow_colors_cb")
        legend_draggable_cb = self._control_widget("legend_draggable_cb")

        title = title_edit.text() if isinstance(title_edit, QtWidgets.QLineEdit) else ""
        x_label = x_label_edit.text() if isinstance(x_label_edit, QtWidgets.QLineEdit) else ""
        y_label = y_label_edit.text() if isinstance(y_label_edit, QtWidgets.QLineEdit) else ""
        show_title = (
            bool(title_visible_cb.isChecked())
            if isinstance(title_visible_cb, QtWidgets.QCheckBox)
            else True
        )
        show_x_label = (
            bool(x_label_visible_cb.isChecked())
            if isinstance(x_label_visible_cb, QtWidgets.QCheckBox)
            else True
        )
        show_y_label = (
            bool(y_label_visible_cb.isChecked())
            if isinstance(y_label_visible_cb, QtWidgets.QCheckBox)
            else True
        )
        title_font = float(title_font_spin.value()) if isinstance(title_font_spin, QtWidgets.QSpinBox) else 16.0
        label_font = float(label_font_spin.value()) if isinstance(label_font_spin, QtWidgets.QSpinBox) else 12.0
        tick_font = float(tick_font_spin.value()) if isinstance(tick_font_spin, QtWidgets.QSpinBox) else 10.0
        tick_length = (
            float(tick_length_spin.value())
            if isinstance(tick_length_spin, QtWidgets.QDoubleSpinBox)
            else 3.5
        )
        tick_width = (
            float(tick_width_spin.value())
            if isinstance(tick_width_spin, QtWidgets.QDoubleSpinBox)
            else 0.8
        )
        line_width = (
            float(line_width_spin.value())
            if isinstance(line_width_spin, QtWidgets.QDoubleSpinBox)
            else 1.5
        )
        marker_size = (
            float(marker_size_spin.value())
            if isinstance(marker_size_spin, QtWidgets.QDoubleSpinBox)
            else 6.0
        )
        figure_width_mm = (
            float(figure_width_spin.value())
            if isinstance(figure_width_spin, QtWidgets.QDoubleSpinBox)
            else float(DEFAULT_PAPER_WIDTH_MM)
        )
        figure_height_mm = (
            float(figure_height_spin.value())
            if isinstance(figure_height_spin, QtWidgets.QDoubleSpinBox)
            else float(DEFAULT_PAPER_WIDTH_MM / DEFAULT_FIGURE_ASPECT_RATIO)
        )
        figure_width = float(figure_width_mm / MM_PER_INCH)
        figure_height = float(figure_height_mm / MM_PER_INCH)
        figure_width_auto = (
            bool(figure_width_auto_cb.isChecked())
            if isinstance(figure_width_auto_cb, QtWidgets.QCheckBox)
            else False
        )
        figure_height_auto = (
            bool(figure_height_auto_cb.isChecked())
            if isinstance(figure_height_auto_cb, QtWidgets.QCheckBox)
            else True
        )
        figure_aspect_mode = (
            str(figure_aspect_mode_combo.currentData())
            if isinstance(figure_aspect_mode_combo, QtWidgets.QComboBox)
            else "auto"
        )
        figure_aspect_ratio = (
            float(figure_aspect_ratio_spin.value())
            if isinstance(figure_aspect_ratio_spin, QtWidgets.QDoubleSpinBox)
            else float(DEFAULT_FIGURE_ASPECT_RATIO)
        )
        axes_aspect_mode = (
            str(axes_aspect_combo.currentData())
            if isinstance(axes_aspect_combo, QtWidgets.QComboBox)
            else "auto"
        )
        axes_aspect_ratio = (
            float(axes_aspect_ratio_spin.value())
            if isinstance(axes_aspect_ratio_spin, QtWidgets.QDoubleSpinBox)
            else 1.0
        )
        x_scale = (
            str(x_scale_combo.currentData())
            if isinstance(x_scale_combo, QtWidgets.QComboBox)
            else "linear"
        )
        y_scale = (
            str(y_scale_combo.currentData())
            if isinstance(y_scale_combo, QtWidgets.QComboBox)
            else "linear"
        )
        x_factor_text = (
            x_value_factor_edit.text().strip()
            if isinstance(x_value_factor_edit, QtWidgets.QLineEdit)
            else "1"
        )
        y_factor_text = (
            y_value_factor_edit.text().strip()
            if isinstance(y_value_factor_edit, QtWidgets.QLineEdit)
            else "1"
        )
        x_factor = self._safe_numeric_expression(x_factor_text)
        y_factor = self._safe_numeric_expression(y_factor_text)
        reflect_x_scale_units = (
            bool(reflect_x_scale_units_cb.isChecked())
            if isinstance(reflect_x_scale_units_cb, QtWidgets.QCheckBox)
            else False
        )
        reflect_y_scale_units = (
            bool(reflect_y_scale_units_cb.isChecked())
            if isinstance(reflect_y_scale_units_cb, QtWidgets.QCheckBox)
            else False
        )
        x_tick_mode = (
            str(x_tick_mode_combo.currentData())
            if isinstance(x_tick_mode_combo, QtWidgets.QComboBox)
            else "auto"
        )
        y_tick_mode = (
            str(y_tick_mode_combo.currentData())
            if isinstance(y_tick_mode_combo, QtWidgets.QComboBox)
            else "auto"
        )
        x_tick_count = (
            int(x_tick_count_spin.value())
            if isinstance(x_tick_count_spin, QtWidgets.QSpinBox)
            else 5
        )
        y_tick_count = (
            int(y_tick_count_spin.value())
            if isinstance(y_tick_count_spin, QtWidgets.QSpinBox)
            else 5
        )
        x_tick_step = self._float_from_edit("x_tick_step_edit")
        y_tick_step = self._float_from_edit("y_tick_step_edit")
        show_grid = bool(show_grid_cb.isChecked()) if isinstance(show_grid_cb, QtWidgets.QCheckBox) else False
        show_legend = (
            bool(show_legend_cb.isChecked()) if isinstance(show_legend_cb, QtWidgets.QCheckBox) else True
        )
        legend_location = (
            str(legend_location_combo.currentData())
            if isinstance(legend_location_combo, QtWidgets.QComboBox)
            else "best"
        )
        legend_font_size = (
            float(legend_font_spin.value())
            if isinstance(legend_font_spin, QtWidgets.QSpinBox)
            else 10.0
        )
        legend_orientation = (
            str(legend_orientation_combo.currentData())
            if isinstance(legend_orientation_combo, QtWidgets.QComboBox)
            else "auto"
        )
        legend_columns = (
            int(legend_columns_spin.value())
            if isinstance(legend_columns_spin, QtWidgets.QSpinBox)
            else 1
        )
        legend_show_symbols = (
            bool(legend_show_symbols_cb.isChecked())
            if isinstance(legend_show_symbols_cb, QtWidgets.QCheckBox)
            else True
        )
        legend_text_follow_colors = (
            bool(legend_text_follow_colors_cb.isChecked())
            if isinstance(legend_text_follow_colors_cb, QtWidgets.QCheckBox)
            else True
        )
        legend_draggable = (
            bool(legend_draggable_cb.isChecked())
            if isinstance(legend_draggable_cb, QtWidgets.QCheckBox)
            else True
        )

        x_min = self._float_from_edit("x_min_edit")
        x_max = self._float_from_edit("x_max_edit")
        y_min = self._float_from_edit("y_min_edit")
        y_max = self._float_from_edit("y_max_edit")
        if x_min is not None and x_max is not None and x_min >= x_max:
            QtWidgets.QMessageBox.warning(self, "Graph formatting", "X min must be less than X max.")
            return
        if y_min is not None and y_max is not None and y_min >= y_max:
            QtWidgets.QMessageBox.warning(self, "Graph formatting", "Y min must be less than Y max.")
            return
        if x_scale == "log" and ((x_min is not None and x_min <= 0) or (x_max is not None and x_max <= 0)):
            QtWidgets.QMessageBox.warning(self, "Graph formatting", "Log X scale requires positive X limits.")
            return
        if y_scale == "log" and ((y_min is not None and y_min <= 0) or (y_max is not None and y_max <= 0)):
            QtWidgets.QMessageBox.warning(self, "Graph formatting", "Log Y scale requires positive Y limits.")
            return
        if x_factor is None or math.isclose(x_factor, 0.0, abs_tol=1e-15):
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "X value factor must be a valid non-zero number/expression (for example: 10^-3).",
            )
            return
        if y_factor is None or math.isclose(y_factor, 0.0, abs_tol=1e-15):
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "Y value factor must be a valid non-zero number/expression (for example: 10^-3).",
            )
            return
        if x_tick_mode == "step" and (x_tick_step is None or x_tick_step <= 0):
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "X tick increment must be a positive number when using 'By increment'.",
            )
            return
        if y_tick_mode == "step" and (y_tick_step is None or y_tick_step <= 0):
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "Y tick increment must be a positive number when using 'By increment'.",
            )
            return
        if not figure_width_auto and figure_width <= 0:
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "Figure width must be positive.",
            )
            return
        if not figure_height_auto and figure_height <= 0:
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "Figure height must be positive.",
            )
            return
        if figure_aspect_mode == "custom" and figure_aspect_ratio <= 0:
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "Figure custom aspect ratio must be positive.",
            )
            return
        if axes_aspect_mode == "custom" and axes_aspect_ratio <= 0:
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "Custom aspect ratio must be positive.",
            )
            return

        touched = 0
        for axes in targets:
            if axes is None:
                continue
            try:
                figure = getattr(axes, "figure", None)
                sibling_axes: List[Any] = []
                if figure is not None:
                    try:
                        sibling_axes = [entry for entry in figure.axes if entry is not None]
                    except Exception:
                        sibling_axes = []
                if not sibling_axes:
                    sibling_axes = [axes]

                resolved_figure_width, resolved_figure_height = self._resolve_figure_size_inches(
                    figure=figure,
                    width_in=figure_width,
                    height_in=figure_height,
                    width_auto=figure_width_auto,
                    height_auto=figure_height_auto,
                    aspect_mode=figure_aspect_mode,
                    aspect_ratio=figure_aspect_ratio,
                )

                if figure is not None:
                    try:
                        figure.set_size_inches(
                            resolved_figure_width,
                            resolved_figure_height,
                            forward=True,
                        )
                    except Exception:
                        pass
                    sync_display_reference = getattr(self, "_sync_canvas_display_reference", None)
                    if callable(sync_display_reference):
                        try:
                            sync_display_reference(
                                axes=axes,
                                width_in=resolved_figure_width,
                                height_in=resolved_figure_height,
                            )
                        except Exception:
                            pass

                x_label_base = self._label_units_to_brackets(str(x_label))
                y_label_base = self._label_units_to_brackets(str(y_label))
                axes.set_title(title)
                axes.set_xlabel(self._scaled_axis_label(x_label_base, x_factor, reflect_x_scale_units))
                axes.set_ylabel(self._scaled_axis_label(y_label_base, y_factor, reflect_y_scale_units))
                axes.title.set_fontsize(title_font)
                axes.title.set_visible(show_title)
                axes.xaxis.label.set_fontsize(label_font)
                axes.xaxis.label.set_visible(show_x_label)
                axes.yaxis.label.set_fontsize(label_font)
                axes.yaxis.label.set_visible(show_y_label)
                setattr(axes, "_mw_x_base_label", x_label_base)
                setattr(axes, "_mw_y_base_label", y_label_base)
                setattr(axes, "_mw_x_value_factor", float(x_factor))
                setattr(axes, "_mw_y_value_factor", float(y_factor))
                setattr(axes, "_mw_x_reflect_scale_in_unit", bool(reflect_x_scale_units))
                setattr(axes, "_mw_y_reflect_scale_in_unit", bool(reflect_y_scale_units))
                plugin_name = self._plugin_name_for_axes(axes)
                for axis in sibling_axes:
                    setattr(axis, "_mw_x_base_label", x_label_base)
                    setattr(axis, "_mw_y_base_label", y_label_base)
                    setattr(axis, "_mw_x_value_factor", float(x_factor))
                    setattr(axis, "_mw_y_value_factor", float(y_factor))
                    setattr(axis, "_mw_x_reflect_scale_in_unit", bool(reflect_x_scale_units))
                    setattr(axis, "_mw_y_reflect_scale_in_unit", bool(reflect_y_scale_units))
                    axis.set_xlabel(self._scaled_axis_label(x_label_base, x_factor, reflect_x_scale_units))
                    axis.set_ylabel(self._scaled_axis_label(y_label_base, y_factor, reflect_y_scale_units))
                    try:
                        axis.title.set_visible(show_title)
                    except Exception:
                        pass
                    try:
                        axis.xaxis.label.set_visible(show_x_label)
                    except Exception:
                        pass
                    try:
                        axis.yaxis.label.set_visible(show_y_label)
                    except Exception:
                        pass
                    axis.tick_params(
                        axis="both",
                        which="both",
                        labelsize=tick_font,
                        length=tick_length,
                        width=tick_width,
                    )
                    axis.set_xscale(x_scale)
                    axis.set_yscale(y_scale)
                    self._apply_axis_factor_formatter(axis.xaxis, x_factor, axis_name="x")
                    self._apply_axis_factor_formatter(axis.yaxis, y_factor, axis_name="y")
                    if x_min is not None and x_max is not None:
                        axis.set_xlim(x_min, x_max)
                    if y_min is not None and y_max is not None:
                        axis.set_ylim(y_min, y_max)
                    self._apply_tick_locator(
                        axis.xaxis,
                        x_tick_mode,
                        step=x_tick_step,
                        count=x_tick_count,
                    )
                    self._apply_tick_locator(
                        axis.yaxis,
                        y_tick_mode,
                        step=y_tick_step,
                        count=y_tick_count,
                    )
                    try:
                        if axes_aspect_mode == "equal":
                            axis.set_aspect("equal", adjustable="box")
                        elif axes_aspect_mode == "custom":
                            axis.set_aspect(max(0.05, axes_aspect_ratio), adjustable="box")
                        else:
                            axis.set_aspect("auto")
                    except Exception:
                        pass
                    axis.grid(show_grid)
                    for line in axis.get_lines():
                        try:
                            line.set_linewidth(line_width)
                        except Exception:
                            pass
                        try:
                            marker = str(line.get_marker()).strip().lower()
                        except Exception:
                            marker = ""
                        if marker and marker != "none":
                            try:
                                line.set_markersize(marker_size)
                            except Exception:
                                pass

                for axis in sibling_axes:
                    if show_legend:
                        legend = None
                        try:
                            legend = self._sync_axes_legend_with_visible_lines(
                                axis,
                                plugin_name=plugin_name,
                            )
                        except Exception:
                            legend = None
                        if legend is None:
                            try:
                                legend = axis.get_legend()
                            except Exception:
                                legend = None
                        if legend is not None:
                            try:
                                state = self._legend_state_snapshot(legend, plugin_name=plugin_name)
                                state.update(
                                    {
                                        "visible": True,
                                        "loc": legend_location,
                                        "orientation": legend_orientation,
                                        "font_size": legend_font_size,
                                        "ncol": max(1, legend_columns),
                                        "show_symbols": legend_show_symbols,
                                        "text_follows_handles": legend_text_follow_colors,
                                        "draggable": legend_draggable,
                                    }
                                )
                                self._apply_legend_snapshot(legend, state)
                            except Exception:
                                try:
                                    legend.set_visible(True)
                                except Exception:
                                    pass
                    else:
                        legend = None
                        try:
                            legend = axis.get_legend()
                        except Exception:
                            legend = None
                        if legend is not None:
                            legend.set_visible(False)
                self._fit_figure_to_content(figure)

                tab = self._tab_for_axes(axes)  # noqa: SLF001 - shared helper
                if tab is not None:
                    subwindow_for = getattr(self.tab_widget, "_subwindow_for", None)
                    if callable(subwindow_for):
                        try:
                            sub = subwindow_for(tab)
                        except Exception:
                            sub = None
                        if sub is not None:
                            try:
                                aspect = float(resolved_figure_width) / float(resolved_figure_height)
                            except Exception:
                                aspect = 0.0
                            if math.isfinite(aspect) and aspect > 0.0:
                                setter = getattr(sub, "set_aspect_ratio", None)
                                if callable(setter):
                                    try:
                                        setter(aspect)
                                    except Exception:
                                        pass
                            is_maximized_target = getattr(self.tab_widget, "_is_maximized_target", None)
                            maybe_maximize = getattr(self.tab_widget, "_maybe_apply_maximize", None)
                            if callable(is_maximized_target) and callable(maybe_maximize):
                                try:
                                    if bool(is_maximized_target(sub)):
                                        maybe_maximize(sub)
                                        touched += 1
                                        continue
                                except Exception:
                                    pass
                            try:
                                dpi = float(getattr(figure, "dpi", 100.0) or 100.0) if figure is not None else 100.0
                            except Exception:
                                dpi = 100.0
                            target_w = int(max(360.0, resolved_figure_width * dpi + 48.0))
                            target_h = int(max(260.0, resolved_figure_height * dpi + 72.0))
                            fitted = False
                            fitter = getattr(self.tab_widget, "_fit_subwindow", None)
                            if callable(fitter):
                                try:
                                    fitter(
                                        sub,
                                        use_half_width=False,
                                        preferred_width=target_w,
                                        remember_manual=False,
                                    )
                                    fitted = True
                                except Exception:
                                    fitted = False
                            if not fitted:
                                try:
                                    sub.resize(target_w, target_h)
                                except Exception:
                                    pass
                            arrange = getattr(self.tab_widget, "_arrange_subwindows", None)
                            arrangement_mode_getter = getattr(self.tab_widget, "arrangement_mode", None)
                            arrangement_mode = "cascade"
                            if callable(arrangement_mode_getter):
                                try:
                                    arrangement_mode = str(arrangement_mode_getter() or "cascade").strip().lower()
                                except Exception:
                                    arrangement_mode = "cascade"
                            if callable(arrange) and arrangement_mode in {"tile_vertical", "tile_horizontal"}:
                                try:
                                    QtCore.QTimer.singleShot(0, arrange)
                                except Exception:
                                    pass
                touched += 1
            except Exception:
                continue
        if touched == 0:
            QtWidgets.QMessageBox.warning(
                self,
                "Graph formatting",
                "Could not apply formatting to the selected graph(s).",
            )

    def _export_current_graph_pdf(self) -> None:
        self._save_graph_for_current_tab(preferred_suffix=".pdf")

    def _fit_figure_to_content(self, figure: Any) -> None:
        if figure is None:
            return
        constrained_layout = False
        get_constrained_layout = getattr(figure, "get_constrained_layout", None)
        if callable(get_constrained_layout):
            try:
                constrained_layout = bool(get_constrained_layout())
            except Exception:
                constrained_layout = False
        if constrained_layout:
            set_pads = getattr(figure, "set_constrained_layout_pads", None)
            if callable(set_pads):
                try:
                    set_pads(w_pad=0.04, h_pad=0.04, wspace=0.02, hspace=0.02)
                except Exception:
                    pass
        tight_layout = getattr(figure, "tight_layout", None)
        if callable(tight_layout) and not constrained_layout:
            axes_count = 0
            try:
                axes_count = len(getattr(figure, "axes", []))
            except Exception:
                axes_count = 0
            kwargs: Dict[str, Any]
            if axes_count > 1:
                kwargs = {"pad": 1.1, "rect": (0.08, 0.10, 0.92, 0.90)}
            else:
                kwargs = {"pad": 1.0, "rect": (0.06, 0.08, 0.96, 0.92)}
            apply_with_feedback = getattr(self, "_tight_layout_with_feedback", None)
            if callable(apply_with_feedback):
                applied = False
                try:
                    applied = bool(
                        apply_with_feedback(
                            figure,
                            context="Graph formatting",
                            **kwargs,
                        )
                    )
                except Exception:
                    applied = False
                if not applied:
                    try:
                        apply_with_feedback(
                            figure,
                            context="Graph formatting",
                            pad=1.0,
                        )
                    except Exception:
                        pass
            else:
                try:
                    tight_layout(**kwargs)
                except Exception:
                    try:
                        tight_layout(pad=1.0)
                    except Exception:
                        pass
        canvas = getattr(figure, "canvas", None)
        if canvas is None:
            return
        try:
            canvas.draw_idle()
        except Exception:
            try:
                canvas.draw()
            except Exception:
                pass

    def _apply_selected_plotter(self) -> None:
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        name = combo.currentData() if combo is not None else None
        current_name = self._current_plotter_name
        if (
            name
            and current_name
            and name != current_name
            and self._current_plugin is not None
        ):
            handled = self._prompt_plugin_window_choice(name)
            self._restore_plotter_combo_selection()
            if handled:
                return
        if not name:
            if self._current_plugin is not None:
                self._current_plugin.deactivate()
            self._current_plugin = None
            self._current_plotter_name = None
            self._set_script_panel(None)
            self._set_plugin_settings_widget(None)
            self._active_plugin_updater = None
            self._set_plot_button_label(None)
            self._apply_graph_option_defaults_to_controls(None)
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
        else:
            try:
                # If a placeholder base plugin slipped through, rebuild it from the factory.
                if getattr(type(plugin), "generate", None) is PyPlotPlugin.generate:
                    factory = self._plugin_factories.get(name)
                    if factory is not None:
                        plugin = factory(self)
                        self._plugin_instances[name] = plugin
            except Exception:
                pass

        if self._current_plugin is not plugin:
            if self._current_plugin is not None:
                self._current_plugin.deactivate()
            self._current_plugin = plugin
            self._current_plotter_name = name
            self._set_script_panel(plugin.panel_widget())
            self._set_plugin_settings_widget(plugin.settings_widget())
            plugin.activate()
            self._set_plot_button_label(plugin)
            self._apply_graph_option_defaults_to_controls(name)
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

    def _create_new_pyplot_window(
        self,
        checked: bool = False,
        *,
        initial: str | None = None,
        imported_paths: Iterable[Path] | None = None,
    ) -> PyPlotWorkbench | None:
        _ = checked
        factories = dict(self._plugin_factories)
        target = initial if initial is not None else self._current_plotter_name
        window = PyPlotWorkbench(plotters=factories, initial_plotter=target)
        window.show()
        self._register_spawned_window(window)
        if imported_paths:
            paths: list[Path] = []
            for source in imported_paths:
                try:
                    candidate = Path(source)
                except Exception:
                    continue
                if candidate.exists():
                    paths.append(candidate)
            if paths:
                QtCore.QTimer.singleShot(0, lambda w=window, pts=paths: w._import_paths(pts))
        return window

    def _register_spawned_window(self, window: "PyPlotWorkbench") -> None:
        self._spawned_windows.append(window)
        window.destroyed.connect(
            lambda *_w, ref=window: self._spawned_windows.remove(ref)
            if ref in self._spawned_windows
            else None
        )

    def _restore_plotter_combo_selection(self) -> None:
        combo = self._plotter_combo if isinstance(self._plotter_combo, QtWidgets.QComboBox) else None
        if combo is None:
            return
        combo.blockSignals(True)
        target = self._current_plotter_name
        if target:
            index = combo.findData(target)
            if index < 0:
                index = 0
            combo.setCurrentIndex(index)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _prompt_plugin_window_choice(self, target: str) -> bool:
        if self._current_plugin is None:
            return False
        message = QtWidgets.QMessageBox(self)
        message.setWindowTitle("Open plugin in new window?")
        message.setIcon(QtWidgets.QMessageBox.Icon.Question)
        message.setText(f"Open {target} in a new PyPlot window?")
        message.setInformativeText(
            "Choose whether to bring the currently imported files into the new window."
        )
        keep_button = message.addButton(
            "New window (keep imports)",
            QtWidgets.QMessageBox.ButtonRole.AcceptRole,
        )
        discard_button = message.addButton(
            "New window (empty)",
            QtWidgets.QMessageBox.ButtonRole.ActionRole,
        )
        cancel_button = message.addButton(
            QtWidgets.QMessageBox.StandardButton.Cancel
        )
        message.setDefaultButton(cancel_button)
        message.exec()
        clicked = message.clickedButton()
        if clicked is keep_button:
            self._open_plugin_in_new_window(target, include_imports=True)
        elif clicked is discard_button:
            self._open_plugin_in_new_window(target, include_imports=False)
        return True

    def _open_plugin_in_new_window(self, target: str, *, include_imports: bool) -> None:
        paths = self._selected_paths() if include_imports else []
        self._create_new_pyplot_window(
            initial=target,
            imported_paths=paths if paths else None,
        )

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
            plugin_name = self._current_plotter_name
            if plugin_name:
                self._plugin_last_directories[plugin_name] = last
                self._sync_plugin_directory_settings()

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

def main(
    available_plotters: Dict[str, Callable[[], QtWidgets.QWidget | None]] | None = None,
    initial_plotter: str | None = None,
) -> QtWidgets.QWidget | None:
    """Entry-point used by the launcher."""

    plugin_factories = _builtin_plugin_factories()
    for name, launcher in sorted((available_plotters or {}).items()):
        if name not in plugin_factories:
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
    "PyPlotWorkbench",
    "main",
]


if __name__ == "__main__":
    main()
