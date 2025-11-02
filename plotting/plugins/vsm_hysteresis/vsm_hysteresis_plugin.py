from __future__ import annotations

import inspect
import logging
import types
from pathlib import Path
from typing import List

from PyQt6 import QtCore, QtWidgets

from plotting.plugins.base import PyPlotPlugin
from plotting.vsm_hysteresis_loops import VSMPlotter, _looks_like_vsm_name
from plotting.pyplot.window import create_toolbar_section


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
        layout.setSpacing(10)

        def _form_layout(parent: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
            form = QtWidgets.QFormLayout(parent)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)
            form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            return form

        axes_section, axes_layout = create_toolbar_section(
            "Axes and filters",
            parent=container,
            layout_factory=_form_layout,
        )
        host.temperature_combo = QtWidgets.QComboBox(axes_section)
        host.temperature_combo.addItem("All temperatures", None)
        axes_layout.addRow("Temperature:", host.temperature_combo)

        host.x_axis_combo = QtWidgets.QComboBox(axes_section)
        host.y_axis_combo = QtWidgets.QComboBox(axes_section)
        axes_layout.addRow("X axis:", host.x_axis_combo)
        axes_layout.addRow("Y axis:", host.y_axis_combo)
        layout.addWidget(axes_section)

        appearance_section, appearance_layout = create_toolbar_section(
            "Appearance",
            parent=container,
        )
        host.style_combo = QtWidgets.QComboBox(appearance_section)
        host.style_combo.addItem("Line", "line")
        host.style_combo.addItem("Line + symbols", "line_markers")
        appearance_layout.addWidget(QtWidgets.QLabel("Matplotlib style", appearance_section))
        appearance_layout.addWidget(host.style_combo)

        host.dark_mode_checkbox = QtWidgets.QCheckBox("Dark plot theme", appearance_section)
        host.dark_mode_checkbox.setToolTip(
            "Render Matplotlib plots using a dark background theme."
        )
        appearance_layout.addWidget(host.dark_mode_checkbox)

        host.field_direction_button = QtWidgets.QPushButton("Highlight field direction", appearance_section)
        host.field_direction_button.setCheckable(True)
        host.field_direction_button.setToolTip(
            "Use solid lines for increasing magnetic field and dashed lines for decreasing segments."
        )
        appearance_layout.addWidget(host.field_direction_button)
        appearance_layout.addStretch(1)
        layout.addWidget(appearance_section)

        overlay_section, overlay_layout = create_toolbar_section(
            "Angle overlays",
            parent=container,
        )
        host.angle_overlay_list = QtWidgets.QListWidget(overlay_section)
        host.angle_overlay_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        overlay_layout.addWidget(host.angle_overlay_list, 1)
        overlay_hint = QtWidgets.QLabel(
            "Select rotations to compare across temperatures or when exporting overlays.",
            overlay_section,
        )
        overlay_hint.setWordWrap(True)
        overlay_layout.addWidget(overlay_hint)
        host.angle_overlay_button = QtWidgets.QPushButton(
            "Plot selected angles across temperatures", overlay_section
        )
        host.angle_overlay_button.setEnabled(False)
        overlay_layout.addWidget(host.angle_overlay_button)
        layout.addWidget(overlay_section, 1)

        metrics_section, metrics_layout = create_toolbar_section(
            "Derived metrics",
            parent=container,
        )
        host.metrics_angle_button = QtWidgets.QPushButton("Plot metrics vs angle", metrics_section)
        host.metrics_angle_button.setEnabled(False)
        metrics_layout.addWidget(host.metrics_angle_button)
        host.metrics_temperature_button = QtWidgets.QPushButton(
            "Plot metrics vs temperature", metrics_section
        )
        host.metrics_temperature_button.setEnabled(False)
        metrics_layout.addWidget(host.metrics_temperature_button)
        metrics_layout.addStretch(1)
        layout.addWidget(metrics_section)

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
        paths = [path for path in host._selected_paths() if path.is_file()]
        if not paths:
            imported = self._collect_imported_vsm_sources()
            if imported:
                formatted = host._format_paths(imported)
                host.path_edit.setText(formatted)
                host._apply_path_text(formatted)
                host._update_action_states()
                paths = [path for path in host._selected_paths() if path.is_file()]
        if not paths:
            paths = host.ensure_data_selection(self)
            if not paths:
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
        show_menu = getattr(self.host, "_show_data_menu", None)
        if callable(show_menu):
            return bool(show_menu())
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


__all__ = ["VSMHysteresisPlugin"]
