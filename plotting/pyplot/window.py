from __future__ import annotations

import datetime
import hashlib
import logging
import math
import os
import re
import sys
import time
import traceback
import warnings
import weakref
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Hashable,
    Iterable,
    Iterator,
    List,
    Optional,
    Literal,
    Sequence,
    Tuple,
    cast,
)

import json
from contextlib import contextmanager
import uuid
from functools import partial

from PyQt6 import QtCore, QtGui, QtWidgets
import matplotlib as mpl
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt import NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from matplotlib.patches import Ellipse, FancyArrowPatch, Patch, Rectangle
from matplotlib.text import Text
from matplotlib import colors as mcolors
import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype

from ..plugins.base import PyPlotPlugin
from plotting.shared.utils import (
    install_standard_menu,
    developer_options,
    save_figure,
    show_plots,
    create_file_widget,
    prepare_output_dir,
    get_last_output_dir,
    set_last_output_dir,
    run_with_console,
    arrange_top_layout,
    restore_backend_choice,
    store_backend_choice,
    selected_backend,
    restore_png_dpi,
    store_png_dpi,
    restore_combo_choice,
    store_combo_choice,
    format_annealing_title,
)
from plotting.shared.origin import (
    origin_session,
    schedule_origin_release,
    release_origin,
    escape_origin_text as shared_escape_origin_text,
    set_origin_axis_title as shared_set_origin_axis_title,
    set_origin_graph_title as shared_set_origin_graph_title,
)
from plotting.shared.readability import (
    apply_readability,
    apply_readability_fonts,
    create_readability_group,
    sync_readability,
)
from plotting.shared.logfiles import append_text_with_rotation
from .cursor_status import cursor_readout_text


OBJECT_TREE_STATE_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1
PRIMARY_DOCK_DEFAULT_WIDTH = 320
PRIMARY_DOCK_MIN_WIDTH = 160
PRIMARY_DOCK_EXPAND_THRESHOLD = 1300
PRIMARY_DOCK_EXPANDED_FRACTION = 0.18
PRIMARY_DOCK_EXPANDED_MAX = 460
PRIMARY_DOCK_MAX_FRACTION = 0.28
UNIT_SUFFIX_PAREN_RE = re.compile(r"^(?P<prefix>.*?)(?:\s*)\((?P<unit>[^()]{1,24})\)\s*$")
UNIT_SUFFIX_BRACKET_RE = re.compile(r"^(?P<prefix>.*?)(?:\s*)\[(?P<unit>[^\[\]]{1,24})\]\s*$")
OUTLIER_IQR_FACTOR = 1.5
OUTLIER_MIN_POINTS = 8
OUTLIER_ZSCORE_THRESHOLD = 3.5
ORIGIN_EXPORT_COLOR_CYCLE: tuple[str, ...] = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
)
ORIGIN_EXPORT_LAYER_TOP = 20.0
ORIGIN_EXPORT_LAYER_BOTTOM = 20.0

PointerType = QtCore.QObject | weakref.ReferenceType[QtCore.QObject] | object


def _make_qpointer(obj: QtCore.QObject) -> PointerType:
    """Return a Qt QPointer when available, otherwise fall back to a weak reference."""

    pointer_cls = getattr(QtCore, "QPointer", None)
    if callable(pointer_cls):
        try:
            return cast(PointerType, pointer_cls(obj))
        except Exception:
            pass
    return cast(PointerType, weakref.ref(obj))


def _json_friendly_project_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_friendly_project_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_friendly_project_value(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_friendly_project_value(item())
        except Exception:
            pass
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def _deref_qpointer(pointer: Any) -> Optional[QtCore.QObject]:
    """Dereference a Qt QPointer or weak reference safely."""

    if pointer is None:
        return None
    is_null = getattr(pointer, "isNull", None)
    if callable(is_null):
        try:
            if is_null():
                return None
        except Exception:
            return None
    data_method = getattr(pointer, "data", None)
    if callable(data_method):
        try:
            obj = data_method()
        except Exception:
            obj = None
        if isinstance(obj, QtCore.QObject):
            return obj
    if callable(pointer):
        try:
            return pointer()
        except Exception:
            return None
    return pointer if isinstance(pointer, QtCore.QObject) else None


def _should_force_light_text(color: Any) -> bool:
    """Return True when ``color`` is effectively dark/neutral and needs light text."""

    try:
        r, g, b, _ = mcolors.to_rgba(color)  # type: ignore[arg-type]
    except Exception:
        return False
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    spread = max(r, g, b) - min(r, g, b)
    return luminance < 0.6 and spread < 0.12


def _sync_legend_text_colors(legend: Legend) -> None:
    """Apply text-follows-handle behaviour when requested."""

    if not isinstance(legend, Legend):
        return
    follow = getattr(legend, "_mw_text_follows_handles", False)
    if not follow:
        originals = getattr(legend, "_mw_text_original_colors", [])
        if originals:
            for text, color in zip(legend.get_texts(), originals):
                try:
                    text.set_color(color)
                except Exception:
                    continue
        return
    try:
        handles = list(getattr(legend, "legendHandles", []))
    except Exception:
        handles = []
    if not handles:
        try:
            handles = list(legend.legendHandles)
        except Exception:
            handles = []
    texts = list(legend.get_texts())
    if not texts:
        return
    originals = getattr(legend, "_mw_text_original_colors", [])
    if not originals:
        try:
            originals = [text.get_color() for text in texts]
        except Exception:
            originals = []
        setattr(legend, "_mw_text_original_colors", originals)
    for text, handle in zip(texts, handles):
        color = None
        for getter_name in ("get_color", "get_facecolor"):
            getter = getattr(handle, getter_name, None)
            if callable(getter):
                try:
                    color = getter()
                except Exception:
                    color = None
                if color is not None:
                    break
        if isinstance(color, (list, tuple)) and color:
            color = color[0]
        if isinstance(color, (list, tuple)) and len(color) >= 3:
            color = color[:3]
        if color is None:
            continue
        try:
            text.set_color(color)
        except Exception:
            continue
    try:
        legend.figure.canvas.draw_idle()
    except Exception:
        pass


def _legend_handles(legend: Legend) -> list[Any]:
    """Return legend handles across Matplotlib versions."""

    handles: list[Any] = []
    for attr in ("legendHandles", "legend_handles"):
        value = getattr(legend, attr, None)
        if value:
            try:
                handles = list(value)
            except Exception:
                handles = []
            if handles:
                return handles
    try:
        handles = list(legend.legendHandles)
    except Exception:
        handles = []
    return handles


def _set_legend_symbol_visibility(legend: Legend, show_symbols: bool) -> None:
    """Show or hide symbol glyphs while preserving the original alpha."""

    handles = _legend_handles(legend)
    legend._mw_show_symbols = bool(show_symbols)
    for handle in handles:
        original_alpha = getattr(handle, "_mw_original_alpha", None)
        if original_alpha is None:
            try:
                original_alpha = handle.get_alpha()
            except Exception:
                original_alpha = None
            if original_alpha is None:
                original_alpha = 1.0
            setattr(handle, "_mw_original_alpha", float(original_alpha))
        target_alpha = float(original_alpha) if show_symbols else 0.0
        alpha_set = False
        if hasattr(handle, "set_alpha"):
            try:
                handle.set_alpha(target_alpha)
                alpha_set = True
            except Exception:
                alpha_set = False
        if not alpha_set and hasattr(handle, "set_visible"):
            setter = getattr(handle, "set_visible", None)
            if callable(setter):
                try:
                    setter(show_symbols)
                except Exception:
                    pass


def _set_legend_text_follow_handles(legend: Legend, follow_colors: bool) -> None:
    """Toggle whether legend text color follows legend handle color."""

    legend._mw_text_follows_handles = bool(follow_colors)
    try:
        texts = list(legend.get_texts())
    except Exception:
        texts = []
    originals = getattr(legend, "_mw_text_original_colors", [])
    if not originals and texts:
        try:
            originals = [text.get_color() for text in texts]
        except Exception:
            originals = []
        setattr(legend, "_mw_text_original_colors", originals)
    if not follow_colors:
        for text, color in zip(texts, originals):
            try:
                text.set_color(color)
            except Exception:
                continue
    _sync_legend_text_colors(legend)


class _MessageLogHandler(logging.Handler):
    """Logging handler that forwards PyPlot messages into the workspace log view."""

    def __init__(self, window: "PyPlotWindow") -> None:
        super().__init__()
        self._window_ref = weakref.ref(window)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        window = self._window_ref()
        if window is None:
            return
        name = getattr(record, "name", "")
        if not name.startswith("PyPlot"):
            return
        level = "error" if record.levelno >= logging.ERROR else "info"
        message = self.format(record)
        try:
            window._append_log(message, level=level)
        except Exception:
            pass


class _LogViewWatcher(QtCore.QObject):
    """Event filter that clears the log alert when the message log gains focus."""

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() in (
            QtCore.QEvent.Type.Show,
            QtCore.QEvent.Type.FocusIn,
        ):
            try:
                self._callback()
            except Exception:
                pass
        return False


TOOLBAR_SECTION_PROPERTY = "mw_toolbar_section_title"


def create_toolbar_section(
    title: str,
    *,
    parent: QtWidgets.QWidget | None = None,
    layout_factory: Callable[[QtWidgets.QWidget], QtWidgets.QLayout] | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QLayout]:
    """Helper to build toolbar-native settings sections with consistent styling."""

    section = QtWidgets.QFrame(parent)
    section.setObjectName("mw_toolbar_section")
    section.setProperty(TOOLBAR_SECTION_PROPERTY, title)
    section.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    section.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)
    section.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Preferred,
        QtWidgets.QSizePolicy.Policy.Maximum,
    )

    def _default_factory(widget: QtWidgets.QWidget) -> QtWidgets.QLayout:
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        return layout

    factory = layout_factory or _default_factory
    layout = factory(section)
    return section, layout


@dataclass
class FormatToolbarControls:
    toolbar: QtWidgets.QToolBar | None = None
    font_combo: QtWidgets.QComboBox | None = None
    size_spin: QtWidgets.QSpinBox | None = None
    bold_action: QtGui.QAction | None = None
    italic_action: QtGui.QAction | None = None
    underline_action: QtGui.QAction | None = None
    subscript_action: QtGui.QAction | None = None
    superscript_action: QtGui.QAction | None = None
    subsup_action: QtGui.QAction | None = None
    edit_text_action: QtGui.QAction | None = None
    color_button: QtWidgets.QToolButton | None = None
    fill_color_button: QtWidgets.QToolButton | None = None
    width_spin: QtWidgets.QDoubleSpinBox | None = None
    line_group: QtGui.QActionGroup | None = None
    line_action: QtGui.QAction | None = None
    scatter_action: QtGui.QAction | None = None
    line_symbol_action: QtGui.QAction | None = None


@dataclass
class AnnotationToolbarControls:
    toolbar: QtWidgets.QToolBar | None = None
    select_action: QtGui.QAction | None = None
    text_action: QtGui.QAction | None = None
    arrow_action: QtGui.QAction | None = None
    line_action: QtGui.QAction | None = None
    rectangle_action: QtGui.QAction | None = None
    ellipse_action: QtGui.QAction | None = None
    action_group: QtGui.QActionGroup | None = None


@dataclass
class _GraphTextDialogResult:
    text: str
    font_family: str
    font_size: float
    color: Any
    bold: bool
    italic: bool
    underline: bool


@dataclass
class _GraphSectionState:
    """Tracks where a settings section lives so it can be shown inside a menu."""

    anchor: QtWidgets.QWidget
    parent: QtWidgets.QWidget | None
    layout: QtWidgets.QLayout | None
    layout_info: tuple[int, ...] = field(default_factory=tuple)
    detached: bool = False
    menu_layout: QtWidgets.QVBoxLayout | None = None
    menu_panel: QtWidgets.QWidget | None = None
    menu_scroll: QtWidgets.QScrollArea | None = None


class _GraphSectionButton(QtWidgets.QToolButton):
    """QToolButton that records which settings section the user wants to view."""

    def __init__(
        self,
        anchor: QtWidgets.QWidget | None,
        callback: Callable[[QtWidgets.QWidget | None], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._graph_section_anchor = anchor
        self._graph_section_callback = callback

    def _notify_anchor(self) -> None:
        callback = self._graph_section_callback
        if callable(callback):
            try:
                callback(self._graph_section_anchor)
            except Exception:
                pass

    def showMenu(self) -> None:  # type: ignore[override]
        self._notify_anchor()
        super().showMenu()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        self._notify_anchor()
        super().mousePressEvent(event)


class _DockSwitcherWidget(QtWidgets.QWidget):
    """Vertical tab bar that mirrors dock visibility with hover-to-open behaviour."""

    _HOVER_CLOSE_DELAY_MS = 200

    def __init__(
        self,
        docks: Sequence[QtWidgets.QDockWidget],
        *,
        side: Literal["left", "right"],
        enable_hover: bool = True,
        enable_overlay: bool = True,
        auto_collapse: bool = True,
        parent: QtWidgets.QWidget | None = None,
        settings: QtCore.QSettings | None = None,
        pinned_setting_key: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._docks = list(docks)
        self._side = side
        self._syncing = False
        self._expanded_index: int | None = None
        self._pinned_index: int | None = None
        self._floating_indices: set[int] = set()
        self._dock_widths: Dict[QtWidgets.QDockWidget, int] = {}
        self._panel_dock = parent if isinstance(parent, QtWidgets.QDockWidget) else None
        self._tabbed_docks: set[QtWidgets.QDockWidget] = set()
        self._last_hover_index: int | None = None
        self._enable_hover = enable_hover
        self._enable_overlay = enable_overlay
        self._auto_collapse = auto_collapse

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tab_bar = QtWidgets.QTabBar(self)
        shape = (
            QtWidgets.QTabBar.Shape.RoundedWest
            if side == "left"
            else QtWidgets.QTabBar.Shape.RoundedEast
        )
        self._tab_bar.setShape(shape)
        self._tab_bar.setDocumentMode(True)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setUsesScrollButtons(False)
        self._tab_bar.setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self._tab_bar.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._tab_bar.setDrawBase(False)
        self._tab_bar.setMouseTracking(True)
        self._tab_bar.installEventFilter(self)
        self._tab_bar.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)

        layout.addWidget(self._tab_bar, 1)
        layout.addStretch(1)

        metrics = self.fontMetrics()
        estimated_width = metrics.height() + 18
        self.setFixedWidth(estimated_width)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        # Timer must exist before dock visibility changes emit signals during setup.
        self._collapse_timer = QtCore.QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse_if_outside)

        self._settings = settings
        self._pinned_setting_key = pinned_setting_key
        self._cached_pinned_name: str | None = self._load_cached_pinned_name()
        self._dock_index_map: Dict[QtWidgets.QDockWidget, int] = {}
        self._default_tab_colors: Dict[int, QtGui.QColor] = {}

        for idx, dock in enumerate(self._docks):
            tab_index = self._tab_bar.addTab(dock.windowTitle())
            self._dock_index_map[dock] = tab_index
            self._default_tab_colors[tab_index] = self._tab_bar.tabTextColor(tab_index)
            dock.windowTitleChanged.connect(
                lambda title, ti=tab_index: self._tab_bar.setTabText(ti, title)
            )
            dock.visibilityChanged.connect(
                lambda visible, ti=tab_index: self._handle_visibility_change(ti, visible)
            )
            dock.topLevelChanged.connect(
                lambda floating, ti=tab_index, d=dock: self._handle_top_level_change(ti, floating, d)
            )
            dock.installEventFilter(self)
            base_width = dock.sizeHint().width()
            stored_width = 0
            main_window = self._main_window()
            if isinstance(main_window, QtWidgets.QMainWindow) and hasattr(
                main_window, "_primary_dock_widths"
            ):
                try:
                    stored_width = main_window._primary_dock_widths.get(  # type: ignore[attr-defined]
                        dock, 0
                    )
                except Exception:
                    stored_width = 0
            if stored_width > 0:
                width = stored_width
            else:
                width = base_width if base_width > 0 else PRIMARY_DOCK_DEFAULT_WIDTH
                width = min(width, PRIMARY_DOCK_DEFAULT_WIDTH)
            width = max(width, PRIMARY_DOCK_MIN_WIDTH, 220)
            clamp_width = getattr(main_window, "_clamp_primary_dock_width", None)
            if callable(clamp_width):
                try:
                    width = int(clamp_width(dock, width))
                except Exception:
                    pass
            self._dock_widths[dock] = width
            dock.hide()

        self._tab_bar.currentChanged.connect(self._activate_index)
        if self._docks:
            self._tab_bar.setCurrentIndex(0)
        self._collapse_all()


    # Event handling -------------------------------------------------
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self._tab_bar:
            if event.type() == QtCore.QEvent.Type.MouseButtonPress:
                button = getattr(event, "button", lambda: None)()
                if button == QtCore.Qt.MouseButton.LeftButton:
                    index = self._tab_index_from_event(event)
                    if index >= 0:
                        if self._pinned_index == index:
                            self._update_pinned_index(None)
                            self._collapse_all()
                        else:
                            self._update_pinned_index(index)
                            self._activate_index(index)
                            self._tab_bar.setCurrentIndex(index)
                        return True
            if self._enable_hover:
                if event.type() in (
                    QtCore.QEvent.Type.MouseMove,
                    QtCore.QEvent.Type.HoverMove,
                    QtCore.QEvent.Type.HoverEnter,
                ):
                    index = self._tab_index_from_event(event)
                    if index >= 0 and index != self._last_hover_index:
                        self._last_hover_index = index
                        QtCore.QTimer.singleShot(0, lambda idx=index: self._activate_index(idx))
                elif event.type() in (
                    QtCore.QEvent.Type.HoverLeave,
                    QtCore.QEvent.Type.Leave,
                ):
                    self._last_hover_index = None
                    self._schedule_collapse()
        elif obj in self._docks:
            if self._auto_collapse:
                if event.type() == QtCore.QEvent.Type.Enter:
                    self._collapse_timer.stop()
                elif event.type() == QtCore.QEvent.Type.Leave:
                    index = self._docks.index(obj)
                    if not self._is_persistent(index):
                        self._schedule_collapse()
        return super().eventFilter(obj, event)

    def _tab_index_from_event(self, event: QtCore.QEvent) -> int:
        pos_value: QtCore.QPointF | None = None
        if hasattr(event, "position"):
            pos_value = event.position()
        elif hasattr(event, "pos"):
            pos = event.pos()
            if isinstance(pos, QtCore.QPoint):
                pos_value = QtCore.QPointF(pos)
        if pos_value is None:
            return -1
        point = QtCore.QPoint(int(pos_value.x()), int(pos_value.y()))
        return self._tab_bar.tabAt(point)

    # Behaviour ------------------------------------------------------
    def _activate_index(self, index: int) -> None:
        if not self._docks or index < 0 or index >= len(self._docks):
            return
        if self._expanded_index == index and self._docks[index].isVisible():
            return
        self._syncing = True
        self._collapse_all(exclude=index)
        dock = self._docks[index]
        prefer_overlay = bool(dock.property("mwOverlayPreferred")) and self._enable_overlay
        persistent = self._is_persistent(index)
        if prefer_overlay:
            try:
                if persistent and self._pinned_index == index:
                    if dock.isFloating():
                        dock.setFloating(False)
                elif not persistent and not dock.isFloating():
                    dock.setFloating(True)
            except Exception:
                pass
        elif not persistent and dock.isFloating():
            try:
                dock.setFloating(False)
            except Exception:
                pass
        if not self._enable_overlay and dock.isFloating():
            try:
                dock.setFloating(False)
            except Exception:
                pass
        dock.show()
        try:
            QtCore.QTimer.singleShot(0, lambda d=dock: d.raise_())
        except Exception:
            pass
        if prefer_overlay and not persistent and dock.isFloating():
            try:
                main_window = self._main_window()
                if isinstance(main_window, QtWidgets.QMainWindow):
                    origin = main_window.mapToGlobal(QtCore.QPoint(0, 0))
                    dock_width = dock.width() or dock.sizeHint().width()
                    stored_width = self._dock_widths.get(dock, dock_width)
                    if stored_width:
                        dock_width = stored_width
                    dock_height = dock.height() or dock.sizeHint().height()
                    target_x = origin.x()
                    target_y = origin.y()
                    dock.resize(dock_width, dock_height)
                    dock.move(target_x, target_y)
            except Exception:
                pass
        if not dock.isFloating():
            self._ensure_tabbed(dock)
            width = self._target_width_for_dock(dock)
            if width > 0:
                self._apply_dock_width(dock, width)
        self._expanded_index = index
        self._collapse_timer.stop()
        self._syncing = False

    def _handle_visibility_change(self, index: int, visible: bool) -> None:
        if self._syncing or not self._docks:
            return
        current = self._docks[index]
        main = self._main_window()
        if visible:
            if not current.isFloating() and hasattr(main, "_primary_dock_widths"):
                try:
                    stored_width = main._primary_dock_widths.get(current, 0)  # type: ignore[attr-defined]
                except Exception:
                    stored_width = 0
                if stored_width > 0:
                    self._dock_widths[current] = stored_width
            self._expanded_index = index
            self._collapse_timer.stop()
            if self._tab_bar.currentIndex() != index:
                self._syncing = True
                self._tab_bar.setCurrentIndex(index)
                self._syncing = False
            refresher = getattr(main, "_refresh_primary_dock_layout", None)
            if callable(refresher):
                QtCore.QTimer.singleShot(0, refresher)
        else:
            if not current.isFloating():
                measured_width = current.width()
                if measured_width <= 0:
                    measured_width = self._dock_widths.get(current, 0)
                if measured_width > 0:
                    clamped = measured_width
                    clamp_width = getattr(main, "_clamp_primary_dock_width", None)
                    if callable(clamp_width):
                        try:
                            clamped = int(clamp_width(current, measured_width))
                        except Exception:
                            clamped = measured_width
                    clamped = max(clamped, 220)
                    self._dock_widths[current] = clamped
                    if hasattr(main, "_primary_dock_widths"):
                        try:
                            main._primary_dock_widths[current] = clamped  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    remember_width = getattr(main, "_remember_primary_dock_width", None)
                    if callable(remember_width):
                        try:
                            remember_width(current)
                        except Exception:
                            pass
            if self._pinned_index == index:
                self._update_pinned_index(None)
        if self._expanded_index == index:
            self._expanded_index = None
        self._floating_indices.discard(index)
        self._schedule_collapse()

    def _collapse_all(self, *, exclude: int | None = None, keep: Iterable[int] | None = None) -> None:
        previous = self._syncing
        self._syncing = True
        keep_indices = set(keep or [])
        if exclude is not None:
            keep_indices.add(exclude)
        for offset, dock in enumerate(self._docks):
            if exclude is None and self._is_persistent(offset):
                keep_indices.add(offset)
            if offset in keep_indices:
                continue
            dock.hide()
        self._syncing = previous
        if exclude is None:
            if self._pinned_index is not None:
                if 0 <= self._pinned_index < len(self._docks):
                    pinned = self._docks[self._pinned_index]
                    if not pinned.isFloating():
                        pinned.show()
                        try:
                            pinned.raise_()
                        except Exception:
                            pass
                    self._expanded_index = self._pinned_index
            elif not self._floating_indices:
                self._expanded_index = None
        elif not keep_indices and not self._floating_indices:
            self._expanded_index = None

    def set_initial_visible(self, indices: Iterable[int]) -> None:
        """Mark ``indices`` as persistent docks shown on startup."""

        valid = [index for index in indices if 0 <= index < len(self._docks)]
        if not valid:
            return
        # Keep the first dock pinned so it stays visible until toggled off.
        self._update_pinned_index(valid[0], persist=False)
        keep: set[int] = set(valid)
        for index in valid:
            dock = self._docks[index]
            dock.show()
            if not dock.isFloating():
                self._ensure_tabbed(dock)
                width = self._target_width_for_dock(dock)
                if width > 0:
                    self._apply_dock_width(dock, width)
        self._expanded_index = valid[0]
        self._collapse_all(keep=keep)

    def _load_cached_pinned_name(self) -> str | None:
        if not isinstance(self._settings, QtCore.QSettings) or not self._pinned_setting_key:
            return None
        stored = self._settings.value(self._pinned_setting_key, "")
        if stored is None:
            return None
        text = str(stored).strip()
        return text or None

    def _update_pinned_index(self, index: int | None, *, persist: bool = True) -> None:
        if self._pinned_index == index:
            return
        self._pinned_index = index
        if persist:
            self._save_pinned_state()

    def _save_pinned_state(self) -> None:
        if not isinstance(self._settings, QtCore.QSettings) or not self._pinned_setting_key:
            return
        name: str | None = None
        if self._pinned_index is not None and 0 <= self._pinned_index < len(self._docks):
            candidate = self._docks[self._pinned_index]
            obj_name = candidate.objectName()
            if isinstance(obj_name, str) and obj_name.strip():
                name = obj_name.strip()
        try:
            if name:
                self._settings.setValue(self._pinned_setting_key, name)
            else:
                self._settings.remove(self._pinned_setting_key)
        except Exception:
            pass

    def apply_cached_pinned_state(self) -> None:
        cached = self._cached_pinned_name
        if not cached:
            return
        self._cached_pinned_name = None
        for idx, dock in enumerate(self._docks):
            if dock.objectName() != cached:
                continue
            self._update_pinned_index(idx, persist=False)
            try:
                self._tab_bar.setCurrentIndex(idx)
            except Exception:
                pass
            self._activate_index(idx)
            break

    def _schedule_collapse(self) -> None:
        if not self._auto_collapse:
            return
        if self._collapse_timer.isActive() or self._floating_indices:
            return
        self._collapse_timer.start(self._HOVER_CLOSE_DELAY_MS)

    def _collapse_if_outside(self) -> None:
        if not self._auto_collapse:
            return
        if self._floating_indices:
            return
        if self._pointer_over_tab_bar() or self._pointer_over_any_dock():
            return
        self._collapse_all()
        if self._pinned_index is not None:
            if 0 <= self._pinned_index < len(self._docks):
                dock = self._docks[self._pinned_index]
                if not dock.isVisible() and not dock.isFloating():
                    self._activate_index(self._pinned_index)

    def _pointer_over_tab_bar(self) -> bool:
        cursor = QtGui.QCursor.pos()
        local = self._tab_bar.mapFromGlobal(cursor)
        return self._tab_bar.rect().contains(local)

    def _pointer_over_any_dock(self) -> bool:
        cursor = QtGui.QCursor.pos()
        for dock in self._docks:
            local = dock.mapFromGlobal(cursor)
            if dock.isVisible() and dock.rect().contains(local):
                return True
        return False

    def _is_persistent(self, index: int) -> bool:
        return index == self._pinned_index or index in self._floating_indices

    def _handle_top_level_change(
        self,
        index: int,
        floating: bool,
        dock: QtWidgets.QDockWidget,
    ) -> None:
        if floating:
            dock.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
            dock.show()
            self._floating_indices.add(index)
            self._pinned_index = index
            self._expanded_index = index
            self._collapse_timer.stop()
            self._tabbed_docks.discard(dock)
        else:
            dock.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, False)
            dock.show()
            self._floating_indices.discard(index)
            if self._pinned_index == index:
                self._pinned_index = None
            self._ensure_tabbed(dock)
            width = self._target_width_for_dock(dock)
            if width > 0:
                self._apply_dock_width(dock, width)

    def _target_width_for_dock(self, dock: QtWidgets.QDockWidget) -> int:
        width = self._dock_widths.get(dock, 0)
        main = self._main_window()
        if isinstance(main, QtWidgets.QMainWindow):
            target_getter = getattr(main, "_primary_dock_target_width", None)
            if callable(target_getter):
                fallback = max(width, PRIMARY_DOCK_DEFAULT_WIDTH, PRIMARY_DOCK_MIN_WIDTH)
                try:
                    computed = int(target_getter(dock, fallback))
                except Exception:
                    computed = fallback
                if width <= 0 and computed > 0:
                    width = computed
            clamp_width = getattr(main, "_clamp_primary_dock_width", None)
            if callable(clamp_width) and width > 0:
                try:
                    width = int(clamp_width(dock, width))
                except Exception:
                    pass
        if width <= 0:
            width = max(PRIMARY_DOCK_DEFAULT_WIDTH, PRIMARY_DOCK_MIN_WIDTH, 220)
        self._dock_widths[dock] = width
        return width

    def _main_window(self) -> QtWidgets.QMainWindow | None:
        if self._panel_dock is not None:
            window = self._panel_dock.parentWidget()
        else:
            window = self.parentWidget()
        while window is not None and not isinstance(window, QtWidgets.QMainWindow):
            window = window.parentWidget()
        return window if isinstance(window, QtWidgets.QMainWindow) else None

    def _ensure_tabbed(self, dock: QtWidgets.QDockWidget) -> None:
        if dock.isFloating():
            self._tabbed_docks.discard(dock)
            return
        if dock in self._tabbed_docks:
            return
        main_window = self._main_window()
        if main_window is None:
            return
        if self._panel_dock is not None:
            try:
                main_window.splitDockWidget(self._panel_dock, dock, QtCore.Qt.Orientation.Horizontal)
            except Exception:
                pass
            else:
                self._tabbed_docks.add(dock)

    def mark_tabbed(self, dock: QtWidgets.QDockWidget) -> None:
        if isinstance(dock, QtWidgets.QDockWidget):
            self._tabbed_docks.add(dock)

    def set_tab_alert(self, dock: QtWidgets.QDockWidget | None, enabled: bool) -> None:
        if dock is None:
            return
        index = self._dock_index_map.get(dock)
        if index is None:
            return
        if enabled:
            self._tab_bar.setTabTextColor(index, QtGui.QColor("#b3261e"))
            return
        default = self._default_tab_colors.get(index)
        if default is not None:
            self._tab_bar.setTabTextColor(index, default)

    def _apply_dock_width(self, dock: QtWidgets.QDockWidget, width: int) -> None:
        if dock.isFloating() or width <= 0:
            return

        available = None
        try:
            screen = QtGui.QGuiApplication.screenAt(dock.mapToGlobal(dock.rect().center()))
            if screen is None:
                screen = QtGui.QGuiApplication.primaryScreen()
            available = screen.availableGeometry() if screen is not None else None
        except Exception:
            available = None
        if available is not None:
            width = max(120, min(width, available.width()))

        pointer = _make_qpointer(dock)

        def _resize() -> None:
            dock_widget = _deref_qpointer(pointer)
            if not isinstance(dock_widget, QtWidgets.QDockWidget):
                return
            if dock_widget.isFloating():
                return
            try:
                dock_widget.resize(width, dock_widget.height() or dock_widget.sizeHint().height())
            except Exception:
                pass
            main_window = self._main_window()
            if not isinstance(main_window, QtWidgets.QMainWindow):
                return
            try:
                main_window.resizeDocks([dock_widget], [width], QtCore.Qt.Orientation.Horizontal)
            except Exception:
                pass
            try:
                frame = main_window.frameGeometry()
                screen = QtGui.QGuiApplication.screenAt(frame.center())
                if screen is None:
                    screen = QtGui.QGuiApplication.primaryScreen()
                if screen is not None:
                    available_rect = screen.availableGeometry()
                    new_left = max(
                        available_rect.left(),
                        min(frame.left(), available_rect.right() - frame.width()),
                    )
                    new_top = max(
                        available_rect.top(),
                        min(frame.top(), available_rect.bottom() - frame.height()),
                    )
                    main_window.move(new_left, new_top)
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, _resize)


@dataclass
class PlotTabState:
    """Track Matplotlib artefacts for a rendered plot tab."""

    axes: Any
    canvas: FigureCanvas
    lines: Dict[float, Any]


@dataclass
class GraphLineState:
    """Describe a plotted line within the embedded Matplotlib canvas."""

    key: tuple[str, float | str]
    label: str
    line: Any
    base_x: Any
    base_y: Any
    normalized: bool = False
    extra_lines: List[Any] = field(default_factory=list)
    full_x: Any | None = None
    full_y: Any | None = None

    def iter_lines(self) -> Iterable[Any]:
        """Yield all Matplotlib line objects associated with this state."""

        yield self.line
        yield from self.extra_lines

    def x_data(self) -> Any:
        """Return the full X data for this plotted series."""

        return self.full_x if self.full_x is not None else self.line.get_xdata()

    def y_data(self) -> Any:
        """Return the full Y data for this plotted series."""

        return self.full_y if self.full_y is not None else self.line.get_ydata()


@dataclass(frozen=True)
class _TabSeriesExportEntry:
    """Capture one exportable XY series with axis metadata."""

    label: str
    x_values: np.ndarray
    y_values: np.ndarray
    x_label: str
    x_unit: str
    y_label: str
    y_unit: str
    x_scale_factor: float = 1.0
    y_scale_factor: float = 1.0


@dataclass
class _HistoryEntry:
    description: str
    undo: Callable[[], None]
    redo: Callable[[], None]


@dataclass
class WorksheetColumnMeta:
    """Describe Origin-style metadata for a worksheet column."""

    long_name: str = ""
    units: str = ""
    comments: str = ""
    formula: str = ""


@dataclass
class WorksheetData:
    """Represent a worksheet created or imported into the workspace."""

    key: Hashable
    name: str
    dataframe: pd.DataFrame
    columns: Dict[str, WorksheetColumnMeta]
    source: Path | None = None
    workbook_key: Hashable | None = None
    axis_roles: str = ""


@dataclass
class WorkbookData:
    """Group one or more worksheets originating from the same file."""

    key: Hashable
    name: str
    worksheets: List[Hashable] = field(default_factory=list)
    source: Path | None = None
    folder: Path | None = None


@dataclass
class WorksheetOutlierFinding:
    """Describe statistical outliers detected in a worksheet."""

    worksheet_key: Hashable
    worksheet_name: str
    workbook_name: str
    total_rows: int
    row_indices: List[int]
    row_mask: pd.Series
    column_hits: Dict[str, int]

    @property
    def outlier_count(self) -> int:
        return len(self.row_indices)


class WorksheetTableModel(QtCore.QAbstractTableModel):
    """Expose worksheet data with Origin-style metadata rows."""

    METADATA_FIELDS = (
        ("Long Name", "long_name"),
        ("Units", "units"),
        ("Comments", "comments"),
        ("F(x)", "formula"),
    )

    def __init__(self, worksheet: WorksheetData, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._worksheet = worksheet
        self._frame = worksheet.dataframe
        self._columns = [str(column) for column in self._frame.columns]

    @property
    def dataframe(self) -> pd.DataFrame:
        return self._frame

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._frame.index) + len(self.METADATA_FIELDS)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(
        self,
        index: QtCore.QModelIndex,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> Any:  # type: ignore[override]
        if not index.isValid():
            return None
        column_name = self._columns[index.column()]
        if role in {QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.EditRole}:
            if index.row() < len(self.METADATA_FIELDS):
                label, attr = self.METADATA_FIELDS[index.row()]
                meta = self._column_meta(column_name)
                value = getattr(meta, attr, "")
                return "" if value is None else str(value)
            data_row = index.row() - len(self.METADATA_FIELDS)
            value = self._frame.iloc[data_row, self._frame.columns.get_loc(column_name)]
            if pd.isna(value):
                return ""
            return str(value)
        return None

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> str | None:  # type: ignore[override]
        if orientation == QtCore.Qt.Orientation.Horizontal:
            if role == QtCore.Qt.ItemDataRole.DisplayRole:
                if 0 <= section < len(self._columns):
                    return self._column_letter(section)
                return None
            if role == QtCore.Qt.ItemDataRole.ToolTipRole:
                if 0 <= section < len(self._columns):
                    return self._columns[section]
                return None
            return None
        if role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        if section < len(self.METADATA_FIELDS):
            return self.METADATA_FIELDS[section][0]
        return str(section - len(self.METADATA_FIELDS) + 1)

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlag:  # type: ignore[override]
        base = super().flags(index)
        if index.isValid():
            base |= QtCore.Qt.ItemFlag.ItemIsEditable
        return base

    def setData(
        self,
        index: QtCore.QModelIndex,
        value: object,
        role: int = QtCore.Qt.ItemDataRole.EditRole,
    ) -> bool:  # type: ignore[override]
        if role != QtCore.Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        column_name = self._columns[index.column()]
        if index.row() < len(self.METADATA_FIELDS):
            _, attr = self.METADATA_FIELDS[index.row()]
            meta = self._column_meta(column_name)
            setattr(meta, attr, str(value))
            self.dataChanged.emit(index, index, [role])
            return True

        df = self._frame
        column_index = df.columns.get_loc(column_name)
        series = df.iloc[:, column_index]
        data_row = index.row() - len(self.METADATA_FIELDS)
        text = str(value)
        if pd.api.types.is_numeric_dtype(series):
            if text == "":
                df.iat[data_row, column_index] = pd.NA
                self.dataChanged.emit(index, index, [role])
                return True
            try:
                parsed = float(text)
            except ValueError:
                return False
            df.iat[data_row, column_index] = parsed
        else:
            df.iat[data_row, column_index] = text
        self.dataChanged.emit(index, index, [role])
        return True

    def removeRows(
        self,
        row: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if row < len(self.METADATA_FIELDS):
            return False
        data_row = row - len(self.METADATA_FIELDS)
        if data_row < 0 or count <= 0 or data_row + count > len(self._frame.index):
            return False
        self.beginRemoveRows(parent, row, row + count - 1)
        drop_index = self._frame.index[data_row : data_row + count]
        self._frame.drop(index=drop_index, inplace=True)
        self._frame.reset_index(drop=True, inplace=True)
        self.endRemoveRows()
        return True

    def insertRows(
        self,
        row: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if count <= 0:
            return False
        row = max(row, len(self.METADATA_FIELDS))
        data_row = min(row - len(self.METADATA_FIELDS), len(self._frame.index))
        self.beginInsertRows(parent, row, row + count - 1)
        placeholder = pd.DataFrame(
            [[pd.NA] * len(self._columns) for _ in range(count)],
            columns=self._columns,
        )
        top = self._frame.iloc[:data_row]
        bottom = self._frame.iloc[data_row:]
        self._frame = pd.concat([top, placeholder, bottom], ignore_index=True)
        self._worksheet.dataframe = self._frame
        self.endInsertRows()
        return True

    def insertColumns(
        self,
        column: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if count <= 0:
            return False
        column = max(0, min(column, len(self._columns)))
        self.beginInsertColumns(parent, column, column + count - 1)
        for offset in range(count):
            name = self._generate_column_name(column + offset)
            self._frame.insert(column + offset, name, pd.NA)
            self._columns.insert(column + offset, name)
            self._worksheet.columns[name] = WorksheetColumnMeta(long_name=name)
        self.endInsertColumns()
        return True

    def removeColumns(
        self,
        column: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if count <= 0:
            return False
        if column < 0 or column >= len(self._columns):
            return False
        last = min(column + count, len(self._columns))
        self.beginRemoveColumns(parent, column, last - 1)
        drop_columns = self._columns[column:last]
        self._frame.drop(columns=drop_columns, inplace=True)
        for name in drop_columns:
            self._worksheet.columns.pop(name, None)
        del self._columns[column:last]
        self.endRemoveColumns()
        return True

    # Helpers -------------------------------------------------------------
    def _column_meta(self, column: str) -> WorksheetColumnMeta:
        meta = self._worksheet.columns.get(column)
        if meta is None:
            meta = WorksheetColumnMeta(long_name=column)
            self._worksheet.columns[column] = meta
        return meta

    def _generate_column_name(self, position: int) -> str:
        base_index = position + 1
        candidate = f"Col{base_index:02d}"
        existing = set(self._columns)
        while candidate in existing:
            base_index += 1
            candidate = f"Col{base_index:02d}"
        return candidate

    @staticmethod
    def _column_letter(index: int) -> str:
        """Return an Origin-style column letter for ``index``."""

        index += 1
        label = ""
        while index > 0:
            index, remainder = divmod(index - 1, 26)
            label = chr(ord("A") + remainder) + label
        return label or "A"


class WorksheetTableView(QtWidgets.QTableView):
    """Table view that mirrors Origin's header interaction and copy behaviour."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        h_header = self.horizontalHeader()
        h_header.setSectionsClickable(True)
        h_header.sectionPressed.connect(self._select_column)
        v_header = self.verticalHeader()
        v_header.setSectionsClickable(True)
        v_header.sectionPressed.connect(self._select_row)

    # ------------------------------------------------------------------ copy helpers
    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        if event.matches(QtGui.QKeySequence.StandardKey.Copy):
            self.copy_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_selection(self) -> None:
        model = self.model()
        selection = self.selectionModel()
        if model is None or selection is None:
            return
        indexes = selection.selectedIndexes()
        if not indexes:
            return
        ordered = sorted(indexes, key=lambda idx: (idx.row(), idx.column()))
        rows = sorted({index.row() for index in ordered})
        columns = sorted({index.column() for index in ordered})
        if not rows or not columns:
            return
        row_map = {row: offset for offset, row in enumerate(rows)}
        column_map = {column: offset for offset, column in enumerate(columns)}
        grid: List[List[str]] = [["" for _ in columns] for _ in rows]
        for index in ordered:
            value = model.data(index, QtCore.Qt.ItemDataRole.DisplayRole)
            display = "" if value is None else str(value)
            grid[row_map[index.row()]][column_map[index.column()]] = display
        text = "\n".join("\t".join(row) for row in grid)
        QtWidgets.QApplication.clipboard().setText(text)

    # ------------------------------------------------------------------ selection helpers
    def _select_column(self, logical_index: int) -> None:
        model = self.model()
        selection = self.selectionModel()
        if (
            model is None
            or selection is None
            or logical_index < 0
            or logical_index >= model.columnCount()
            or model.rowCount() == 0
        ):
            return
        top_left = model.index(0, logical_index)
        bottom_right = model.index(model.rowCount() - 1, logical_index)
        if not top_left.isValid() or not bottom_right.isValid():
            return
        selection.select(
            QtCore.QItemSelection(top_left, bottom_right),
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QtCore.QItemSelectionModel.SelectionFlag.Columns,
        )
        self.setFocus()

    def _select_row(self, logical_index: int) -> None:
        model = self.model()
        selection = self.selectionModel()
        if (
            model is None
            or selection is None
            or logical_index < 0
            or logical_index >= model.rowCount()
            or model.columnCount() == 0
        ):
            return
        top_left = model.index(logical_index, 0)
        bottom_right = model.index(logical_index, model.columnCount() - 1)
        if not top_left.isValid() or not bottom_right.isValid():
            return
        selection.select(
            QtCore.QItemSelection(top_left, bottom_right),
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QtCore.QItemSelectionModel.SelectionFlag.Rows,
        )
        self.setFocus()


class _HistoryManager:
    """Lightweight undo/redo stack for plot interactions."""

    def __init__(self) -> None:
        self._entries: List[_HistoryEntry] = []
        self._index: int = 0
        self._replaying = False

    def clear(self) -> None:
        if self._replaying:
            return
        self._entries.clear()
        self._index = 0

    @property
    def is_replaying(self) -> bool:
        return self._replaying

    def record(self, description: str, undo: Callable[[], None], redo: Callable[[], None]) -> None:
        if self._replaying:
            return
        entry = _HistoryEntry(description=description, undo=undo, redo=redo)
        if self._index < len(self._entries):
            del self._entries[self._index :]
        self._entries.append(entry)
        self._index += 1

    def can_undo(self) -> bool:
        return not self._replaying and self._index > 0

    def can_redo(self) -> bool:
        return not self._replaying and self._index < len(self._entries)

    def undo(self) -> None:
        if not self.can_undo():
            return
        self._index -= 1
        entry = self._entries[self._index]
        self._replaying = True
        try:
            entry.undo()
        finally:
            self._replaying = False

    def redo(self) -> None:
        if not self.can_redo():
            return
        entry = self._entries[self._index]
        self._index += 1
        self._replaying = True
        try:
            entry.redo()
        finally:
            self._replaying = False


@dataclass
class _RemovedTabInfo:
    tab: QtWidgets.QWidget
    index: int
    title: str
    icon: QtGui.QIcon | None
    descriptor: TabDescriptor | None
    canvas: FigureCanvas | None
    axes: Any | None
    worksheet_key: Hashable | None
    was_hidden: bool
    was_current: bool
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TabDescriptor:
    """Capture metadata for a tabbed plot and its project-manager bindings."""

    kind: str
    title: str
    root_label: str
    x_label: str
    y_label: str
    canvas: FigureCanvas
    axes: Any
    lines: Dict[tuple[str, float | str], GraphLineState]
    metadata: Dict[str, Any]
    layout_initialized: bool = False
    stored_limits: Dict[str, tuple[float, float]] = field(default_factory=dict)


class GraphTextDialog(QtWidgets.QDialog):
    """Edit free text with mathtext-aware helpers."""

    _FONT_CHOICES = (
        "DejaVu Serif",
        "STIXGeneral",
        "Times New Roman",
        "DejaVu Sans",
        "Arial",
    )

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        title: str,
        initial_text: str = "",
        font_family: str = "DejaVu Serif",
        font_size: float = 14.0,
        color: QtGui.QColor | None = None,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(440, 220)
        self._selected_color = QtGui.QColor(color) if color is not None else QtGui.QColor("#111111")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QtWidgets.QLabel(
            "Matplotlib mathtext is supported. Use syntax like $H_{c}$, $10^{-10}$, or Greek letters such as $\\phi$."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.text_edit = QtWidgets.QLineEdit(initial_text, self)
        layout.addWidget(self.text_edit)

        helper_row = QtWidgets.QHBoxLayout()
        helper_row.setSpacing(6)
        sub_button = QtWidgets.QPushButton("Subscript...", self)
        sup_button = QtWidgets.QPushButton("Superscript...", self)
        both_button = QtWidgets.QPushButton("Sub + Sup...", self)
        helper_row.addWidget(sub_button)
        helper_row.addWidget(sup_button)
        helper_row.addWidget(both_button)
        helper_row.addStretch(1)
        layout.addLayout(helper_row)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        layout.addLayout(form)

        self.font_combo = QtWidgets.QComboBox(self)
        self.font_combo.setEditable(True)
        for entry in self._FONT_CHOICES:
            self.font_combo.addItem(entry)
        font_index = self.font_combo.findText(font_family)
        if font_index >= 0:
            self.font_combo.setCurrentIndex(font_index)
        else:
            self.font_combo.setEditText(font_family or "DejaVu Serif")
        form.addRow("Font:", self.font_combo)

        self.size_spin = QtWidgets.QDoubleSpinBox(self)
        self.size_spin.setRange(6.0, 96.0)
        self.size_spin.setDecimals(1)
        self.size_spin.setValue(float(font_size))
        form.addRow("Size:", self.size_spin)

        style_row = QtWidgets.QHBoxLayout()
        style_row.setSpacing(6)
        self.bold_cb = QtWidgets.QCheckBox("Bold", self)
        self.bold_cb.setChecked(bool(bold))
        self.italic_cb = QtWidgets.QCheckBox("Italic", self)
        self.italic_cb.setChecked(bool(italic))
        self.underline_cb = QtWidgets.QCheckBox("Underline", self)
        self.underline_cb.setChecked(bool(underline))
        style_row.addWidget(self.bold_cb)
        style_row.addWidget(self.italic_cb)
        style_row.addWidget(self.underline_cb)
        style_row.addStretch(1)
        form.addRow("Style:", style_row)

        self.color_button = QtWidgets.QToolButton(self)
        self.color_button.setText("Text color…")
        self.color_button.clicked.connect(self._choose_color)
        form.addRow("Color:", self.color_button)
        self._refresh_color_button()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        sub_button.clicked.connect(self._insert_subscript)
        sup_button.clicked.connect(self._insert_superscript)
        both_button.clicked.connect(self._insert_subsuperscript)

    @staticmethod
    def _strip_outer_math(text: str) -> str:
        stripped = str(text or "").strip()
        if len(stripped) >= 2 and stripped.startswith("$") and stripped.endswith("$"):
            return stripped[1:-1]
        return stripped

    def _refresh_color_button(self) -> None:
        pixmap = QtGui.QPixmap(18, 18)
        pixmap.fill(self._selected_color)
        painter = QtGui.QPainter(pixmap)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 90)))
        painter.drawRect(pixmap.rect().adjusted(0, 0, -1, -1))
        painter.end()
        self.color_button.setIcon(QtGui.QIcon(pixmap))

    def _choose_color(self) -> None:
        color = QtWidgets.QColorDialog.getColor(self._selected_color, self, "Select text color")
        if not color.isValid():
            return
        self._selected_color = color
        self._refresh_color_button()

    def _prompt_script(self, title: str, label: str) -> str | None:
        text, ok = QtWidgets.QInputDialog.getText(self, title, label)
        if not ok:
            return None
        token = str(text).strip()
        return token or None

    def _insert_subscript(self) -> None:
        script = self._prompt_script("Subscript", "Subscript text:")
        if script is None:
            return
        base = self._strip_outer_math(self.text_edit.text()) or "x"
        self.text_edit.setText(f"${base}_{{{script}}}$")

    def _insert_superscript(self) -> None:
        script = self._prompt_script("Superscript", "Superscript text:")
        if script is None:
            return
        base = self._strip_outer_math(self.text_edit.text()) or "x"
        self.text_edit.setText(f"${base}^{{{script}}}$")

    def _insert_subsuperscript(self) -> None:
        subscript = self._prompt_script("Subscript", "Subscript text:")
        if subscript is None:
            return
        superscript = self._prompt_script("Superscript", "Superscript text:")
        if superscript is None:
            return
        base = self._strip_outer_math(self.text_edit.text()) or "x"
        self.text_edit.setText(f"${base}_{{{subscript}}}^{{{superscript}}}$")

    def result_payload(self) -> _GraphTextDialogResult:
        return _GraphTextDialogResult(
            text=self.text_edit.text(),
            font_family=self.font_combo.currentText().strip() or "DejaVu Serif",
            font_size=float(self.size_spin.value()),
            color=(
                self._selected_color.redF(),
                self._selected_color.greenF(),
                self._selected_color.blueF(),
                self._selected_color.alphaF(),
            ),
            bold=bool(self.bold_cb.isChecked()),
            italic=bool(self.italic_cb.isChecked()),
            underline=bool(self.underline_cb.isChecked()),
        )


class GraphSelectionDialog(QtWidgets.QDialog):
    """Offer choices for which plotted data series should be processed."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        entries: Sequence[tuple[str, str, QtWidgets.QWidget]],
        title: str,
        prompt: str,
        current: QtWidgets.QWidget | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._entries = list(entries)
        self._current = current if any(tab is current for _, _, tab in entries) else None
        self._selected: List[QtWidgets.QWidget] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        label = QtWidgets.QLabel(prompt)
        label.setWordWrap(True)
        layout.addWidget(label)

        self.current_radio = QtWidgets.QRadioButton("Current tab only")
        self.all_radio = QtWidgets.QRadioButton("All plotted data")
        self.custom_radio = QtWidgets.QRadioButton("Choose specific items")

        radio_column = QtWidgets.QVBoxLayout()
        radio_column.setSpacing(4)
        radio_column.addWidget(self.current_radio)
        radio_column.addWidget(self.all_radio)
        radio_column.addWidget(self.custom_radio)
        layout.addLayout(radio_column)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        for label_text, detail, tab in self._entries:
            item = QtWidgets.QListWidgetItem(label_text or "Graph")
            if detail and detail != label_text:
                item.setToolTip(detail)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, tab)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)

        self.custom_radio.toggled.connect(self._toggle_custom_list)
        self._toggle_custom_list(False)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        if self._current is not None:
            self.current_radio.setChecked(True)
        else:
            self.current_radio.setEnabled(False)
            self.all_radio.setChecked(True)

        if len(self._entries) <= 1:
            self.custom_radio.setEnabled(False)

    def _toggle_custom_list(self, enabled: bool) -> None:
        self.list_widget.setEnabled(enabled)

    def selected_tabs(self) -> List[QtWidgets.QWidget]:
        return list(self._selected)

    def accept(self) -> None:  # type: ignore[override]
        if self.custom_radio.isChecked():
            chosen: List[QtWidgets.QWidget] = []
            for index in range(self.list_widget.count()):
                item = self.list_widget.item(index)
                if item is None or item.checkState() != QtCore.Qt.CheckState.Checked:
                    continue
                tab = item.data(QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(tab, QtWidgets.QWidget):
                    chosen.append(tab)
            if not chosen:
                QtWidgets.QMessageBox.information(
                    self,
                    "Select Data",
                    "Select at least one plotted item.",
                )
                return
            self._selected = chosen
        elif self.current_radio.isChecked():
            if self._current is None:
                QtWidgets.QMessageBox.information(
                    self,
                    "Select Data",
                    "No plotted tab is currently active.",
                )
                return
            self._selected = [self._current]
        else:
            self._selected = [tab for _, _, tab in self._entries]
            if not self._selected:
                QtWidgets.QMessageBox.information(
                    self,
                    "Select Data",
                    "No plotted items are available.",
                )
                return
        super().accept()


class _CreateGraphSeriesRow(QtWidgets.QWidget):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        worksheet_entries: Sequence[tuple[Hashable, str]],
        worksheet_lookup: Dict[Hashable, WorksheetData],
        remove_callback: Callable[["_CreateGraphSeriesRow"], None],
    ) -> None:
        super().__init__(parent)
        self._worksheet_lookup = worksheet_lookup
        self._remove_callback = remove_callback

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.worksheet_combo = QtWidgets.QComboBox(self)
        for key, label in worksheet_entries:
            self.worksheet_combo.addItem(label, key)
        layout.addWidget(self.worksheet_combo, 3)

        self.x_combo = QtWidgets.QComboBox(self)
        layout.addWidget(self.x_combo, 2)
        self.y_combo = QtWidgets.QComboBox(self)
        layout.addWidget(self.y_combo, 2)

        self.label_edit = QtWidgets.QLineEdit(self)
        self.label_edit.setPlaceholderText("Optional label override")
        layout.addWidget(self.label_edit, 2)

        remove_button = QtWidgets.QToolButton(self)
        remove_button.setText("Remove")
        remove_button.clicked.connect(lambda: self._remove_callback(self))
        layout.addWidget(remove_button)

        self.worksheet_combo.currentIndexChanged.connect(self._populate_columns)
        self._populate_columns()

    def _populate_columns(self) -> None:
        self.x_combo.clear()
        self.y_combo.clear()
        worksheet = self.worksheet()
        if worksheet is None:
            return
        for column in worksheet.dataframe.columns:
            meta = worksheet.columns.get(str(column), WorksheetColumnMeta(long_name=str(column)))
            label = meta.long_name or str(column)
            units = str(meta.units or "").strip()
            display = f"{label} [{units}]" if units else label
            self.x_combo.addItem(display, str(column))
            self.y_combo.addItem(display, str(column))
        roles = str(getattr(worksheet, "axis_roles", "") or "")
        if roles:
            try:
                x_index = roles.index("X")
            except ValueError:
                x_index = -1
            if x_index >= 0 and x_index < self.x_combo.count():
                self.x_combo.setCurrentIndex(x_index)
            try:
                y_index = roles.index("Y")
            except ValueError:
                y_index = 1 if self.y_combo.count() > 1 else 0
            if y_index >= 0 and y_index < self.y_combo.count():
                self.y_combo.setCurrentIndex(y_index)
        elif self.y_combo.count() > 1:
            self.y_combo.setCurrentIndex(1)

    def worksheet(self) -> WorksheetData | None:
        key = self.worksheet_combo.currentData()
        return self._worksheet_lookup.get(key)

    def payload(self) -> dict[str, Any] | None:
        worksheet = self.worksheet()
        if worksheet is None:
            return None
        x_column = self.x_combo.currentData()
        y_column = self.y_combo.currentData()
        if not isinstance(x_column, str) or not isinstance(y_column, str):
            return None
        return {
            "worksheet": worksheet,
            "x_column": x_column,
            "y_column": y_column,
            "label": self.label_edit.text().strip(),
        }


class CreateGraphDialog(QtWidgets.QDialog):
    """Choose exact worksheet X/Y series for a new manual graph."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        *,
        worksheet_entries: Sequence[tuple[Hashable, str]],
        worksheet_lookup: Dict[Hashable, WorksheetData],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Graph")
        self.resize(760, 360)
        self._worksheet_entries = list(worksheet_entries)
        self._worksheet_lookup = dict(worksheet_lookup)
        self._rows: list[_CreateGraphSeriesRow] = []
        self._payload: dict[str, Any] | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QtWidgets.QLabel(
            "Build a new graph from worksheet columns. Add one or more series rows and choose the exact X/Y columns for each plotted line."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        layout.addLayout(form)

        self.title_edit = QtWidgets.QLineEdit(self)
        self.title_edit.setPlaceholderText("Optional graph title")
        form.addRow("Title:", self.title_edit)

        self.x_label_edit = QtWidgets.QLineEdit(self)
        self.x_label_edit.setPlaceholderText("Optional X label override")
        form.addRow("X label:", self.x_label_edit)

        self.y_label_edit = QtWidgets.QLineEdit(self)
        self.y_label_edit.setPlaceholderText("Optional Y label override")
        form.addRow("Y label:", self.y_label_edit)

        self.grid_cb = QtWidgets.QCheckBox("Show grid", self)
        self.grid_cb.setChecked(True)
        form.addRow("", self.grid_cb)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel("Worksheet / sheet"))
        header.addStretch(2)
        header.addWidget(QtWidgets.QLabel("X"))
        header.addStretch(1)
        header.addWidget(QtWidgets.QLabel("Y"))
        header.addStretch(1)
        header.addWidget(QtWidgets.QLabel("Legend label"))
        header.addStretch(2)
        layout.addLayout(header)

        self.rows_container = QtWidgets.QWidget(self)
        self.rows_layout = QtWidgets.QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        layout.addWidget(self.rows_container, 1)

        controls = QtWidgets.QHBoxLayout()
        add_row_button = QtWidgets.QPushButton("Add series", self)
        add_row_button.clicked.connect(self._add_row)
        controls.addWidget(add_row_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._add_row()

    def _add_row(self) -> None:
        row = _CreateGraphSeriesRow(
            self.rows_container,
            worksheet_entries=self._worksheet_entries,
            worksheet_lookup=self._worksheet_lookup,
            remove_callback=self._remove_row,
        )
        self._rows.append(row)
        self.rows_layout.addWidget(row)

    def _remove_row(self, row: _CreateGraphSeriesRow) -> None:
        if len(self._rows) <= 1:
            return
        self._rows = [entry for entry in self._rows if entry is not row]
        row.setParent(None)
        row.deleteLater()

    def payload(self) -> dict[str, Any] | None:
        return self._payload

    def accept(self) -> None:  # type: ignore[override]
        series: list[dict[str, Any]] = []
        for row in self._rows:
            payload = row.payload()
            if payload is None:
                continue
            series.append(payload)
        if not series:
            QtWidgets.QMessageBox.information(
                self,
                "Create Graph",
                "Add at least one valid X/Y series before creating the graph.",
            )
            return
        self._payload = {
            "title": self.title_edit.text().strip(),
            "x_label": self.x_label_edit.text().strip(),
            "y_label": self.y_label_edit.text().strip(),
            "show_grid": bool(self.grid_cb.isChecked()),
            "series": series,
        }
        super().accept()


class LegendSettingsDialog(QtWidgets.QDialog):
    """Configure appearance and layout options for a Matplotlib legend."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        legend: Legend,
        *,
        plugin_name: str | None = None,
        defaults: Dict[str, Any] | None = None,
        on_save: Callable[[Dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._legend = legend
        self._on_save = on_save
        self._defaults = defaults or {}
        self._plugin_name = plugin_name
        try:
            original_text_colors = [text.get_color() for text in legend.get_texts()]
        except Exception:
            original_text_colors = []
        setattr(legend, "_mw_text_original_colors", original_text_colors)
        self.setWindowTitle("Legend Settings")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        layout.addLayout(form)

        self.visible_checkbox = QtWidgets.QCheckBox("Visible", self)
        try:
            self.visible_checkbox.setChecked(bool(legend.get_visible()))
        except Exception:
            self.visible_checkbox.setChecked(True)
        form.addRow("", self.visible_checkbox)

        self.title_edit = QtWidgets.QLineEdit(self)
        try:
            self.title_edit.setText(legend.get_title().get_text())
        except Exception:
            self.title_edit.setText("")
        form.addRow("Title", self.title_edit)

        self.font_spin = QtWidgets.QSpinBox(self)
        self.font_spin.setRange(6, 72)
        try:
            sample_size = next((text.get_fontsize() for text in legend.get_texts()), legend.get_title().get_fontsize())
            self.font_spin.setValue(int(round(float(sample_size))))
        except Exception:
            self.font_spin.setValue(14)
        form.addRow("Font size", self.font_spin)

        self.location_combo = QtWidgets.QComboBox(self)
        self._location_keys: list[str] = []
        for key, code in Legend.codes.items():
            self.location_combo.addItem(key.replace("_", " ").title(), key)
            self._location_keys.append(key)
        current_loc = "best"
        try:
            loc_value = legend._get_loc()
        except Exception:
            loc_value = getattr(legend, "_loc", None)
        if isinstance(loc_value, str):
            current_loc = loc_value
        elif isinstance(loc_value, int):
            for key, code in Legend.codes.items():
                if code == loc_value:
                    current_loc = key
                    break
        index = self.location_combo.findData(current_loc)
        if index >= 0:
            self.location_combo.setCurrentIndex(index)
        form.addRow("Location", self.location_combo)

        self.columns_spin = QtWidgets.QSpinBox(self)
        self.columns_spin.setRange(1, 10)
        try:
            ncol = getattr(legend, "_ncol", None)
            if not isinstance(ncol, int) or ncol <= 0:
                raise ValueError
        except Exception:
            ncol = max(1, len(getattr(legend, "legendHandles", [])))
        self.columns_spin.setValue(ncol)
        form.addRow("Columns", self.columns_spin)

        self.frame_checkbox = QtWidgets.QCheckBox("Show frame", self)
        try:
            self.frame_checkbox.setChecked(bool(legend.get_frame().get_visible()))
        except Exception:
            self.frame_checkbox.setChecked(True)
        form.addRow("", self.frame_checkbox)

        self.frame_alpha_spin = QtWidgets.QDoubleSpinBox(self)
        self.frame_alpha_spin.setRange(0.0, 1.0)
        self.frame_alpha_spin.setSingleStep(0.05)
        try:
            self.frame_alpha_spin.setValue(float(legend.get_frame().get_alpha() or 1.0))
        except Exception:
            self.frame_alpha_spin.setValue(1.0)
        form.addRow("Frame opacity", self.frame_alpha_spin)

        self.border_spin = QtWidgets.QDoubleSpinBox(self)
        self.border_spin.setRange(0.0, 10.0)
        self.border_spin.setSingleStep(0.1)
        try:
            self.border_spin.setValue(float(legend.get_borderpad()))
        except Exception:
            self.border_spin.setValue(0.4)
        form.addRow("Border padding", self.border_spin)

        self.label_spacing_spin = QtWidgets.QDoubleSpinBox(self)
        self.label_spacing_spin.setRange(0.0, 10.0)
        self.label_spacing_spin.setSingleStep(0.1)
        try:
            self.label_spacing_spin.setValue(float(legend.get_labelspacing()))
        except Exception:
            self.label_spacing_spin.setValue(0.5)
        form.addRow("Label spacing", self.label_spacing_spin)

        self.handle_length_spin = QtWidgets.QDoubleSpinBox(self)
        self.handle_length_spin.setRange(0.1, 20.0)
        self.handle_length_spin.setSingleStep(0.1)
        try:
            self.handle_length_spin.setValue(float(legend.get_handlelength()))
        except Exception:
            self.handle_length_spin.setValue(2.0)
        form.addRow("Handle length", self.handle_length_spin)

        self.handle_text_pad_spin = QtWidgets.QDoubleSpinBox(self)
        self.handle_text_pad_spin.setRange(0.0, 10.0)
        self.handle_text_pad_spin.setSingleStep(0.1)
        try:
            self.handle_text_pad_spin.setValue(float(legend.get_handletextpad()))
        except Exception:
            self.handle_text_pad_spin.setValue(0.8)
        form.addRow("Handle text pad", self.handle_text_pad_spin)

        self.column_spacing_spin = QtWidgets.QDoubleSpinBox(self)
        self.column_spacing_spin.setRange(0.0, 10.0)
        self.column_spacing_spin.setSingleStep(0.1)
        try:
            self.column_spacing_spin.setValue(float(legend.get_columnspacing()))
        except Exception:
            self.column_spacing_spin.setValue(2.0)
        form.addRow("Column spacing", self.column_spacing_spin)

        self.marker_scale_spin = QtWidgets.QDoubleSpinBox(self)
        self.marker_scale_spin.setRange(0.1, 10.0)
        self.marker_scale_spin.setSingleStep(0.1)
        try:
            self.marker_scale_spin.setValue(float(legend.get_markerscale()))
        except Exception:
            self.marker_scale_spin.setValue(1.0)
        form.addRow("Marker scale", self.marker_scale_spin)

        stored_symbol_setting = getattr(legend, "_mw_show_symbols", True)
        if stored_symbol_setting is None:
            stored_symbol_setting = bool(self._defaults.get("show_symbols", True))
        self.symbol_checkbox = QtWidgets.QCheckBox("Show legend symbols", self)
        self.symbol_checkbox.setChecked(bool(stored_symbol_setting))
        form.addRow("", self.symbol_checkbox)

        stored_follow_setting = getattr(legend, "_mw_text_follows_handles", None)
        if stored_follow_setting is None:
            stored_follow_setting = bool(self._defaults.get("text_follows_handles", True))
        self.text_color_follow_checkbox = QtWidgets.QCheckBox("Text colour follows plots", self)
        self.text_color_follow_checkbox.setChecked(bool(stored_follow_setting))
        form.addRow("", self.text_color_follow_checkbox)

        self.orientation_combo = QtWidgets.QComboBox(self)
        self.orientation_combo.addItem("Auto", "auto")
        self.orientation_combo.addItem("Horizontal", "horizontal")
        self.orientation_combo.addItem("Vertical", "vertical")
        stored_orientation = getattr(legend, "_mw_orientation", None)
        if stored_orientation is None:
            stored_orientation = self._defaults.get("orientation", "auto")
        orientation_index = self.orientation_combo.findData(stored_orientation)
        if orientation_index < 0:
            orientation_index = 0
        self.orientation_combo.setCurrentIndex(orientation_index)
        form.addRow("Orientation", self.orientation_combo)

        self.placement_combo = QtWidgets.QComboBox(self)
        self.placement_combo.addItem("Inside", "inside")
        self.placement_combo.addItem("Outside (right)", "outside")
        stored_placement = getattr(legend, "_mw_placement", None)
        if stored_placement is None:
            stored_placement = self._defaults.get(
                "placement",
                "outside" if getattr(legend, "_bbox_to_anchor", None) else "inside",
            )
        placement_index = self.placement_combo.findData(stored_placement)
        if placement_index < 0:
            placement_index = 0
        self.placement_combo.setCurrentIndex(placement_index)
        form.addRow("Placement", self.placement_combo)

        self.draggable_checkbox = QtWidgets.QCheckBox("Allow drag", self)
        stored_draggable = getattr(legend, "_mw_draggable", None)
        if stored_draggable is None:
            stored_draggable = self._defaults.get("draggable", True)
        self.draggable_checkbox.setChecked(bool(stored_draggable))
        try:
            legend.set_draggable(bool(stored_draggable))
        except Exception:
            pass
        form.addRow("", self.draggable_checkbox)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
            | QtWidgets.QDialogButtonBox.StandardButton.Apply,
            parent=self,
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        apply_button = button_box.button(QtWidgets.QDialogButtonBox.StandardButton.Apply)
        if apply_button is not None:
            apply_button.clicked.connect(self._apply)
        layout.addWidget(button_box)

    def _persist_defaults(self) -> None:
        follow_colors = self.text_color_follow_checkbox.isChecked()
        show_symbols = self.symbol_checkbox.isChecked()
        orientation = self.orientation_combo.currentData()
        placement = self.placement_combo.currentData()
        draggable = self.draggable_checkbox.isChecked()
        payload = {
            "text_follows_handles": follow_colors,
            "show_symbols": show_symbols,
            "orientation": orientation,
            "placement": placement,
            "draggable": draggable,
        }
        if callable(self._on_save):
            try:
                self._on_save(payload)
            except Exception:
                pass

    def _apply(self) -> None:
        legend = self._legend
        try:
            legend.set_visible(self.visible_checkbox.isChecked())
        except Exception:
            pass
        try:
            legend.set_title(self.title_edit.text())
        except Exception:
            title = legend.get_title()
            if title is not None:
                try:
                    title.set_text(self.title_edit.text())
                except Exception:
                    pass
        try:
            legend.set_fontsize(self.font_spin.value())
        except Exception:
            for text in legend.get_texts():
                try:
                    text.set_fontsize(self.font_spin.value())
                except Exception:
                    pass
            try:
                legend.get_title().set_fontsize(self.font_spin.value())
            except Exception:
                pass
        columns_value = self.columns_spin.value()
        try:
            legend.set_ncol(columns_value)
        except Exception:
            try:
                legend._ncol = columns_value
            except Exception:
                pass
        try:
            legend.set_frame_on(self.frame_checkbox.isChecked())
        except Exception:
            pass
        try:
            legend.get_frame().set_alpha(self.frame_alpha_spin.value())
        except Exception:
            pass
        try:
            legend.set_borderpad(self.border_spin.value())
        except Exception:
            pass
        try:
            legend.set_labelspacing(self.label_spacing_spin.value())
        except Exception:
            pass
        try:
            legend.set_handlelength(self.handle_length_spin.value())
        except Exception:
            pass
        try:
            legend.set_handletextpad(self.handle_text_pad_spin.value())
        except Exception:
            pass
        try:
            legend.set_columnspacing(self.column_spacing_spin.value())
        except Exception:
            pass
        try:
            legend.set_markerscale(self.marker_scale_spin.value())
        except Exception:
            pass

        handles = _legend_handles(legend)
        try:
            texts = list(legend.get_texts())
        except Exception:
            texts = []

        orientation = self.orientation_combo.currentData()
        legend._mw_orientation = orientation
        if orientation == "horizontal":
            columns = max(1, len(handles) or len(texts))
            try:
                legend.set_ncol(columns)
            except Exception:
                try:
                    legend._ncol = columns
                except Exception:
                    pass
        elif orientation == "vertical":
            try:
                legend.set_ncol(1)
            except Exception:
                try:
                    legend._ncol = 1
                except Exception:
                    pass

        placement = self.placement_combo.currentData()
        legend._mw_placement = placement
        loc_value = self.location_combo.currentData()
        axes = getattr(legend, "axes", None)
        if placement == "outside" and axes is not None:
            try:
                legend.set_bbox_to_anchor((1.02, 1.0), transform=axes.transAxes)
            except Exception:
                try:
                    legend._bbox_to_anchor = axes.transAxes
                except Exception:
                    pass
            try:
                legend.set_loc(loc_value or "upper left")
            except Exception:
                pass
        else:
            try:
                legend.set_bbox_to_anchor(None)
            except Exception:
                legend._bbox_to_anchor = None
            try:
                legend.set_loc(loc_value)
            except Exception:
                try:
                    legend._loc = Legend.codes.get(loc_value, legend._loc)
                except Exception:
                    pass

        _set_legend_symbol_visibility(legend, self.symbol_checkbox.isChecked())
        _set_legend_text_follow_handles(
            legend,
            self.text_color_follow_checkbox.isChecked(),
        )

        draggable = self.draggable_checkbox.isChecked()
        legend._mw_draggable = draggable
        try:
            legend.set_draggable(draggable)
        except Exception:
            pass

        canvas = getattr(legend, "figure", None)
        if canvas is not None:
            canvas = getattr(canvas, "canvas", None)
        if canvas is not None:
            try:
                # Use an immediate draw here to avoid queued idle-draw callbacks
                # firing after the canvas has already been torn down.
                canvas.draw()
            except Exception:
                pass
        self._persist_defaults()

    def accept(self) -> None:  # type: ignore[override]
        self._apply()
        super().accept()

class PyPlotWindow(QtWidgets.QMainWindow):
    """Shared UI frame used by plotting tools."""

    help_topic: str = "plotter"
    PROJECT_EXTENSION: str = ".pypj"
    PROJECT_VERSION: int = 1
    PROJECT_CODE: str | None = None
    PROJECT_SETTINGS_PREFIX: str = "project"
    GRAPH_DOCK_ENABLED: bool = True
    SUPPORTED_IMPORT_EXTENSIONS: tuple[str, ...] = (
        ".csv",
        ".tsv",
        ".txt",
        ".dat",
        ".xlsx",
        ".xls",
        ".xlsm",
        ".json",
        ".vsm-hys-data",
        ".vsm-tscn-data",
        ".vsm-vir-data",
    )

    def __init__(self, *, title: str) -> None:
        super().__init__()
        self._base_title = getattr(self, "_base_title", title)
        self._project_path: Path | None = None
        self._recent_projects: List[str] = []
        self._recent_projects_menu: QtWidgets.QMenu | None = None
        self._open_project_action: QtGui.QAction | None = None
        self._save_project_action: QtGui.QAction | None = None
        self._save_project_as_action: QtGui.QAction | None = None
        self._project_menu_separator: QtGui.QAction | None = None
        self._hidden_subwindows: set["_ManagedSubWindow"] = set()
        self._maximized_hidden: set["_ManagedSubWindow"] = set()

        if not hasattr(self, "settings"):
            self.settings = QtCore.QSettings("MicrowireLab", self.__class__.__name__)

        self._load_recent_projects_setting()

        self._last_export_dir: Path | None = None
        if isinstance(self.settings, QtCore.QSettings):
            stored_export_dir = self.settings.value("last_export_dir", "")
            if isinstance(stored_export_dir, str) and stored_export_dir.strip():
                candidate = Path(stored_export_dir)
                if candidate.exists():
                    self._last_export_dir = candidate
        if not hasattr(self, "_plugin_last_export_dirs"):
            raw_export_dirs = "{}"
            if isinstance(self.settings, QtCore.QSettings):
                raw_export_dirs = self.settings.value("plugin_last_export_dirs", "{}")
            try:
                parsed_exports = json.loads(raw_export_dirs) if isinstance(raw_export_dirs, str) else {}
            except json.JSONDecodeError:
                parsed_exports = {}
            self._plugin_last_export_dirs: Dict[str, Path] = {
                key: Path(value) for key, value in parsed_exports.items() if isinstance(value, str)
            }
        raw_legend_settings = "{}"
        if isinstance(self.settings, QtCore.QSettings):
            raw_legend_settings = self.settings.value("legend_settings_by_plugin", "{}")
        try:
            parsed_legend_settings = json.loads(raw_legend_settings) if isinstance(raw_legend_settings, str) else {}
        except json.JSONDecodeError:
            parsed_legend_settings = {}
        self._legend_settings_by_plugin: Dict[str, Dict[str, Any]] = (
            parsed_legend_settings if isinstance(parsed_legend_settings, dict) else {}
        )
        self._legend_color_cache: Dict[int, list[Any]] = {}

        # Tab/graph bookkeeping shared by subclasses.
        self._tab_descriptors: Dict[QtWidgets.QWidget, TabDescriptor] = {}
        self._canvas_by_tab: Dict[QtWidgets.QWidget, FigureCanvas] = {}
        self._axes_by_tab: Dict[QtWidgets.QWidget, Any] = {}
        self._plot_tabs: Dict[float, PlotTabState] = {}
        self._object_items: Dict[
            tuple[QtWidgets.QWidget, tuple[str, float | str]],
            QtWidgets.QTreeWidgetItem,
        ] = {}
        self._navigation_helpers: Dict[FigureCanvas, NavigationToolbar2QT] = {}
        self._cursor_connections: Dict[FigureCanvas, int] = {}
        self._edit_connections: Dict[FigureCanvas, int] = {}
        self._resize_connections: Dict[FigureCanvas, int] = {}
        self._canvas_copy_shortcuts: Dict[FigureCanvas, QtGui.QShortcut] = {}
        self._canvas_base_size_inches: Dict[FigureCanvas, tuple[float, float]] = {}
        self._canvas_base_dpi: Dict[FigureCanvas, float] = {}
        self._canvas_scaling_guard: set[FigureCanvas] = set()
        self._canvas_display_scale: Dict[FigureCanvas, float] = {}
        self._cursor_label: QtWidgets.QLabel | None = None
        self._cursor_axes: Any | None = None
        self._nav_mode: Optional[str] = None
        self._nav_active_canvas: FigureCanvas | None = None
        self._nav_toolbar: QtWidgets.QToolBar | None = None
        self._zoom_action: QtGui.QAction | None = None
        self._pan_action: QtGui.QAction | None = None
        self._rescale_action: QtGui.QAction | None = None
        self._rescale_x_action: QtGui.QAction | None = None
        self._rescale_y_action: QtGui.QAction | None = None
        self._rescale_all_action: QtGui.QAction | None = None
        self._dark_mode_action: QtGui.QAction | None = None
        stored_dark = False
        if isinstance(self.settings, QtCore.QSettings):
            try:
                stored_dark = bool(int(self.settings.value("graphs/dark_mode", 0)))
            except Exception:
                stored_dark = bool(self.settings.value("graphs/dark_mode", False))
        self._dark_mode_enabled: bool = bool(stored_dark)
        self._temperature_tab_widgets: List[QtWidgets.QWidget] = []
        self._metrics_angle_tabs: List[QtWidgets.QWidget] = []
        self._metrics_temperature_tabs: List[QtWidgets.QWidget] = []
        self._overlay_tab_widgets: List[QtWidgets.QWidget] = []
        self._graph_tree_root: QtWidgets.QTreeWidgetItem | None = None
        self._worksheet_tree_root: QtWidgets.QTreeWidgetItem | None = None
        self._graph_tree_items: Dict[QtWidgets.QWidget, QtWidgets.QTreeWidgetItem] = {}
        self._worksheet_tree_items: Dict[Hashable, QtWidgets.QTreeWidgetItem] = {}
        self._worksheet_tabs_open: Dict[Hashable, QtWidgets.QWidget] = {}
        self._tab_to_worksheet_key: Dict[QtWidgets.QWidget, Hashable] = {}
        self._hidden_tabs: set[QtWidgets.QWidget] = set()
        self._axes_theme_state: Dict[Any, Dict[str, Any]] = {}
        self._script_panel_container: QtWidgets.QWidget | None = None
        self._script_panel_layout: QtWidgets.QVBoxLayout | None = None
        self._data_sources_widget: QtWidgets.QWidget | None = None
        self._data_tree_root: QtWidgets.QTreeWidgetItem | None = None
        self._workbook_tree_root: QtWidgets.QTreeWidgetItem | None = None
        self._project_tree_search_text = ""
        self._project_tree_filter_refresh_pending = False
        self._project_tree_selection_sync_blocked = False
        self._project_tree_focus_restore_pending = False
        self._data_folder_items: Dict[Path, QtWidgets.QTreeWidgetItem] = {}
        self._data_workbook_items: Dict[Hashable, QtWidgets.QTreeWidgetItem] = {}
        self._workbooks: Dict[Hashable, WorkbookData] = {}
        self._worksheets: Dict[Hashable, WorksheetData] = {}
        self._shared_plot_workbook_keys: set[Hashable] = set()
        self._shared_plot_workbook_by_tab: Dict[QtWidgets.QWidget, Hashable] = {}
        self._pruning_shared_plot_workbooks = False
        self._worksheet_models: Dict[Hashable, WorksheetTableModel] = {}
        self._project_tree_update_depth = 0
        self._project_tree_updates_prev = True
        self._primary_dock_widths: Dict[QtWidgets.QDockWidget, int] = {}
        self._primary_dock_layout_refreshing = False
        self._dock_resize_refresh_pending = False
        self._log_alert_enabled: bool = False
        self._left_dock_switcher_widget: _DockSwitcherWidget | None = None
        self._log_view_watcher: _LogViewWatcher | None = None
        self._last_import_sources: List[str] = []
        self._last_imported_file_paths: List[Path] = []
        self._pending_new_plot_paths: List[Path] = []
        self._connected_data_folders: List[Path] = []
        self._connected_folder_seen_files: set[str] = set()
        self._connected_folder_polling = False
        self._connected_folder_poll_interval_ms = 3000
        self._connected_folders_restoring = False
        self._restoring_imports = False
        self._data_menu: QtWidgets.QMenu | None = None
        self._import_files_action: QtGui.QAction | None = None
        self._import_folder_action: QtGui.QAction | None = None
        self._connect_folder_action: QtGui.QAction | None = None
        self._refresh_connected_action: QtGui.QAction | None = None
        self._refresh_connected_menu_action: QtGui.QAction | None = None
        self._refresh_import_action: QtGui.QAction | None = None
        self._new_workbook_action: QtGui.QAction | None = None
        self._add_column_before_action: QtGui.QAction | None = None
        self._add_column_after_action: QtGui.QAction | None = None
        self._delete_column_action: QtGui.QAction | None = None
        self._reorder_columns_action: QtGui.QAction | None = None

        self._log_has_unread_errors = False
        self._log_capture_enabled = False
        self._log_capture_faulted = False
        self._log_capture_path: Path | None = None
        self._history = _HistoryManager()
        self._retabify_pending = False
        self._import_storage_key = self._project_settings_key("import_sources")
        self._connected_folder_storage_key = self._project_settings_key("connected_folders")
        self._format_controls = FormatToolbarControls()
        self._annotation_controls = AnnotationToolbarControls()
        self._format_selection: tuple[str, Any] | None = None
        self._format_updating = False
        self._object_tree_updating = False
        self._annotation_tool: str = "select"
        self._annotation_tool_per_canvas: Dict[FigureCanvas, str] = {}
        self._annotation_motion_connections: Dict[FigureCanvas, int] = {}
        self._annotation_release_connections: Dict[FigureCanvas, int] = {}
        self._annotation_interaction: Dict[str, Any] | None = None
        self._graph_object_counter = 0
        self._tight_layout_warning_signatures: set[tuple[int, str]] = set()
        self._last_tight_layout_warning_message: str | None = None
        self._tight_layout_bulk_choice_by_context: Dict[str, str] = {}
        self._tight_layout_bulk_choice_expiry_by_context: Dict[str, float] = {}
        self._task_progress_label: QtWidgets.QLabel | None = None
        self._task_progress_bar: QtWidgets.QProgressBar | None = None
        self._task_progress_dialog: QtWidgets.QDialog | None = None
        self.project_tree_search: QtWidgets.QLineEdit | None = None
        self._connected_folder_timer = QtCore.QTimer(self)
        self._connected_folder_timer.setInterval(self._connected_folder_poll_interval_ms)
        self._connected_folder_timer.timeout.connect(self._poll_connected_folders)

        self.graph_dock: QtWidgets.QDockWidget | None = None
        self.graph_panel: QtWidgets.QWidget | None = None
        self._primary_dock_visibility_keys = {
            "projectExplorerDock": "project_dock_visible",
            "objectManagerDock": "object_dock_visible",
        }
        self._primary_dock_width_keys = {
            "projectExplorerDock": "project_dock_width",
            "objectManagerDock": "object_dock_width",
            "messageLogDock": "log_dock_width",
        }
        self._dock_switcher_pinned_keys = {
            "left": "left_dock_pinned",
            "right": "right_dock_pinned",
        }
        self._message_log_handler = _MessageLogHandler(self)
        logging.getLogger().addHandler(self._message_log_handler)
        self._project_dirty = False
        self._session_has_imports = False
        self._undo_action: QtGui.QAction | None = None
        self._redo_action: QtGui.QAction | None = None

        icon_extent = self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ToolBarIconSize)
        if icon_extent <= 0:
            icon_extent = 24
        self._toolbar_icon_size = QtCore.QSize(icon_extent, icon_extent)

        self._graph_settings_menu: QtWidgets.QMenu | None = None
        self._graph_settings_action: QtWidgets.QWidgetAction | None = None
        self._plugin_settings_panel: QtWidgets.QWidget | None = None
        self._plugin_settings_container: QtWidgets.QWidget | None = None
        self._plugin_settings_layout: QtWidgets.QVBoxLayout | None = None
        self._plugin_settings_placeholder: QtWidgets.QWidget | None = None
        self._active_settings_widget: QtWidgets.QWidget | None = None

        self._graph_section_bar: QtWidgets.QWidget | None = None
        self._graph_section_layout: QtWidgets.QHBoxLayout | None = None
        self._graph_section_buttons: list[QtWidgets.QToolButton] = []
        self._graph_settings_sections: list[tuple[str, QtWidgets.QWidget | None]] = []
        self._graph_settings_hidden_widgets: list[tuple[QtWidgets.QWidget, bool]] = []
        self._graph_settings_hidden_tabs: list[
            tuple[QtWidgets.QTabWidget, list[tuple[int, bool]], int]
        ] = []
        self._graph_settings_pending_anchor: QtWidgets.QWidget | None = None
        self._graph_settings_scroll: QtWidgets.QScrollArea | None = None
        self._graph_settings_content: QtWidgets.QWidget | None = None
        self._graph_section_states: dict[QtWidgets.QWidget, _GraphSectionState] = {}

        stored_max_windows = 6
        if isinstance(self.settings, QtCore.QSettings):
            try:
                stored_max_windows = int(self.settings.value("mdi/max_visible", stored_max_windows))
            except Exception:
                pass
        self._max_visible_windows = max(1, stored_max_windows)

        self._build_base_ui()
        self._apply_scientific_text_defaults()
        try:
            self.tab_widget.set_max_visible_windows(self._max_visible_windows)
        except Exception:
            pass
        self._ensure_settings_menu()
        self._update_project_title()
        self._update_project_actions()
        dev_opts = developer_options()
        dev_opts.keep_files_changed.connect(self._handle_keep_files_changed)
        if hasattr(dev_opts, "message_log_capture_changed"):
            try:
                dev_opts.message_log_capture_changed.connect(self._handle_log_capture_changed)
            except Exception:
                pass
            try:
                self._handle_log_capture_changed(bool(dev_opts.capture_message_log()))
            except Exception:
                pass
        QtCore.QTimer.singleShot(0, self._restore_persisted_imports)
        self._apply_toolbar_style_hint()
        self._update_history_actions()

    # ------------------------------------------------------------------ abstract hooks
    def _handle_manual_path_entry(self) -> None:
        raise NotImplementedError

    def _choose_files(self) -> None:
        raise NotImplementedError

    def _choose_folder(self) -> None:
        raise NotImplementedError

    def _load_data(self) -> None:
        raise NotImplementedError

    def _generate_plots(self) -> None:
        raise NotImplementedError

    def _open_matplotlib_window(self) -> None:
        tab_widget = self._tab_widget_like()
        if tab_widget is None:
            QtWidgets.QMessageBox.information(
                self,
                "Open in Matplotlib",
                "No plot area is available to export.",
            )
            return

        tab = tab_widget.currentWidget()
        if tab is None:
            QtWidgets.QMessageBox.information(
                self,
                "Open in Matplotlib",
                "Select a plot tab before opening a Matplotlib window.",
            )
            return

        descriptor = self._tab_descriptors.get(tab)
        if descriptor is None or not descriptor.lines:
            QtWidgets.QMessageBox.information(
                self,
                "Open in Matplotlib",
                "The selected tab does not expose Matplotlib-compatible data.",
            )
            return

        try:
            import matplotlib.pyplot as plt
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Open in Matplotlib",
                f"Matplotlib's interactive backend is unavailable: {exc}",
            )
            return

        try:
            fig, ax = plt.subplots(constrained_layout=True)
        except TypeError:
            fig, ax = plt.subplots()

        plotted = False
        for state in descriptor.lines.values():
            line = state.line
            visible = True
            getter = getattr(line, "get_visible", None)
            if callable(getter):
                try:
                    visible = bool(getter())
                except Exception:
                    visible = True
            if not visible:
                continue
            x_data = state.x_data()
            y_data = state.y_data()
            if x_data is None or y_data is None:
                continue
            label = state.label
            try:
                if not label:
                    label = line.get_label()
            except Exception:
                label = ""
            kwargs: Dict[str, Any] = {}
            for attr, key in (
                ("get_color", "color"),
                ("get_linestyle", "linestyle"),
                ("get_marker", "marker"),
                ("get_linewidth", "linewidth"),
                ("get_markersize", "markersize"),
                ("get_markerfacecolor", "markerfacecolor"),
                ("get_markeredgecolor", "markeredgecolor"),
            ):
                getter = getattr(line, attr, None)
                if not callable(getter):
                    continue
                try:
                    value = getter()
                except Exception:
                    continue
                if value in {None, "None"}:
                    continue
                kwargs[key] = value
            ax.plot(x_data, y_data, label=label or None, **kwargs)
            plotted = True
            for extra in state.extra_lines:
                if extra is None:
                    continue
                extra_visible = True
                getter = getattr(extra, "get_visible", None)
                if callable(getter):
                    try:
                        extra_visible = bool(getter())
                    except Exception:
                        extra_visible = True
                if not extra_visible:
                    continue
                try:
                    extra_x = extra.get_xdata()
                    extra_y = extra.get_ydata()
                except Exception:
                    continue
                if extra_x is None or extra_y is None:
                    continue
                try:
                    extra_label = extra.get_label()
                except Exception:
                    extra_label = label
                extra_kwargs: Dict[str, Any] = {}
                for attr, key in (
                    ("get_color", "color"),
                    ("get_linestyle", "linestyle"),
                    ("get_marker", "marker"),
                    ("get_linewidth", "linewidth"),
                ):
                    getter = getattr(extra, attr, None)
                    if not callable(getter):
                        continue
                    try:
                        value = getter()
                    except Exception:
                        continue
                    if value in {None, "None"}:
                        continue
                    extra_kwargs[key] = value
                ax.plot(extra_x, extra_y, label=extra_label or None, **extra_kwargs)

        source_axes = descriptor.axes
        if source_axes is None:
            source_canvas = self._canvas_by_tab.get(tab)
            if source_canvas is not None:
                try:
                    figure = source_canvas.figure
                    axes_list = list(getattr(figure, "axes", []))
                    if axes_list:
                        source_axes = axes_list[0]
                except Exception:
                    source_axes = None

        if source_axes is not None:
            try:
                ax.set_xlim(source_axes.get_xlim())
                ax.set_ylim(source_axes.get_ylim())
            except Exception:
                pass
            try:
                ax.set_xscale(source_axes.get_xscale())
                ax.set_yscale(source_axes.get_yscale())
            except Exception:
                pass

        x_label = descriptor.x_label or ""
        y_label = descriptor.y_label or ""
        if not x_label and source_axes is not None:
            try:
                x_label = source_axes.get_xlabel()
            except Exception:
                x_label = ""
        if not y_label and source_axes is not None:
            try:
                y_label = source_axes.get_ylabel()
            except Exception:
                y_label = ""
        if x_label:
            ax.set_xlabel(x_label)
        if y_label:
            ax.set_ylabel(y_label)

        title = descriptor.title or ""
        if not title and source_axes is not None:
            try:
                title = source_axes.get_title()
            except Exception:
                title = ""
        if title:
            ax.set_title(title)

        legend = None
        if source_axes is not None:
            try:
                source_legend = source_axes.get_legend()
            except Exception:
                source_legend = None
            if source_legend is not None and source_legend.get_visible():
                loc = "best"
                try:
                    loc_value = source_legend._get_loc()
                except Exception:
                    loc_value = getattr(source_legend, "_loc", "best")
                if isinstance(loc_value, str):
                    loc = loc_value
                elif isinstance(loc_value, int):
                    loc = next(
                        (name for name, code in Legend.codes.items() if code == loc_value),
                        "best",
                    )
                ncol = getattr(source_legend, "_ncol", 1)
                try:
                    legend = ax.legend(loc=loc, ncol=ncol)
                except Exception:
                    legend = ax.legend()
                if legend is not None:
                    try:
                        legend.set_frame_on(source_legend.get_frame().get_visible())
                    except Exception:
                        pass
                    try:
                        legend.get_frame().set_alpha(source_legend.get_frame().get_alpha())
                    except Exception:
                        pass
        if legend is None and plotted:
            try:
                legend = ax.legend()
            except Exception:
                legend = None

        try:
            fig.canvas.manager.set_window_title(title or descriptor.root_label or "Matplotlib")
        except Exception:
            pass

        try:
            fig.show()
        except Exception:
            try:
                plt.show(block=False)
            except Exception:
                pass

        if not plotted:
            QtWidgets.QMessageBox.information(
                self,
                "Open in Matplotlib",
                "No visible data series were available to plot.",
            )

    def _ensure_settings_menu(self) -> None:
        menu_bar = getattr(self, "menuBar", None)
        if not callable(menu_bar):
            return
        bar = menu_bar()
        if not isinstance(bar, QtWidgets.QMenuBar):
            return
        settings_menu: QtWidgets.QMenu | None = None
        for action in bar.actions():
            menu = action.menu()
            if isinstance(menu, QtWidgets.QMenu) and menu.title().replace("&", "").strip().lower() == "settings":
                settings_menu = menu
                break
        if settings_menu is None:
            settings_menu = bar.addMenu("Settings")
        if not hasattr(self, "_max_windows_action") or not isinstance(getattr(self, "_max_windows_action"), QtGui.QAction):
            self._max_windows_action = settings_menu.addAction("Set max visible windows...")
            self._max_windows_action.triggered.connect(self._prompt_max_visible_windows)

    def _prompt_max_visible_windows(self) -> None:
        current = int(getattr(self, "_max_visible_windows", 6))
        value, ok = QtWidgets.QInputDialog.getInt(
            self,
            "Maximum visible windows",
            "Maximum graph/workbook windows to keep visible:",
            current,
            1,
            20,
            1,
        )
        if not ok:
            return
        self._max_visible_windows = int(value)
        try:
            self.tab_widget.set_max_visible_windows(self._max_visible_windows)
        except Exception:
            pass
        if isinstance(self.settings, QtCore.QSettings):
            try:
                self.settings.setValue("mdi/max_visible", int(value))
            except Exception:
                pass

    def _tab_plugin_name(self, descriptor: TabDescriptor | None = None) -> str | None:
        if descriptor is not None and isinstance(descriptor.metadata, dict):
            plugin_name = descriptor.metadata.get("plugin") or descriptor.metadata.get("plugin_name")
            if isinstance(plugin_name, str) and plugin_name.strip():
                return plugin_name.strip()
        current = getattr(self, "_current_plotter_name", None)
        if isinstance(current, str) and current.strip():
            return current.strip()
        return None

    def _plugin_import_directory(self, plugin_name: str | None) -> Path | None:
        if not plugin_name:
            return None
        plugin_dirs = getattr(self, "_plugin_last_directories", None)
        if isinstance(plugin_dirs, dict):
            candidate = plugin_dirs.get(plugin_name)
            if isinstance(candidate, Path) and candidate.exists():
                return candidate
        return None

    def _plugin_export_directory(self, plugin_name: str | None) -> Path | None:
        if not plugin_name:
            return None
        plugin_dirs = getattr(self, "_plugin_last_export_dirs", None)
        if isinstance(plugin_dirs, dict):
            candidate = plugin_dirs.get(plugin_name)
            if isinstance(candidate, Path) and candidate.exists():
                return candidate
            if isinstance(candidate, str):
                path_candidate = Path(candidate)
                if path_candidate.exists():
                    return path_candidate
        return None

    def _preferred_export_directory(
        self,
        plugin_name: str | None,
        *candidates: Path | None,
    ) -> Path:
        options: list[Path | None] = [
            self._plugin_export_directory(plugin_name),
            self._plugin_import_directory(plugin_name),
        ]
        options.extend(candidates)
        options.append(Path.home())
        for candidate in options:
            if isinstance(candidate, Path):
                try:
                    if candidate.exists():
                        return candidate
                except Exception:
                    continue
        return Path.home()

    def _remember_plugin_export_dir(self, plugin_name: str | None, path: Path | None) -> None:
        if not plugin_name or not isinstance(path, Path):
            return
        plugin_dirs = getattr(self, "_plugin_last_export_dirs", None)
        if not isinstance(plugin_dirs, dict):
            plugin_dirs = {}
            self._plugin_last_export_dirs = plugin_dirs
        plugin_dirs[plugin_name] = path
        settings = getattr(self, "settings", None)
        if isinstance(settings, QtCore.QSettings):
            payload = {key: str(value) for key, value in plugin_dirs.items()}
            try:
                settings.setValue("plugin_last_export_dirs", json.dumps(payload))
                settings.sync()
            except Exception:
                pass

    def _legend_settings_for(self, plugin_name: str | None) -> Dict[str, Any]:
        settings = self._legend_settings_by_plugin.get(plugin_name or "", {})
        defaults = {
            "text_follows_handles": True,
            "show_symbols": True,
            "orientation": "auto",
            "placement": "inside",
            "draggable": True,
        }
        merged = {**defaults, **settings} if isinstance(settings, dict) else defaults
        if plugin_name:
            self._legend_settings_by_plugin.setdefault(plugin_name, merged)
        return merged

    def _remember_legend_settings(self, plugin_name: str | None, payload: Dict[str, Any]) -> None:
        if not plugin_name:
            return
        if not isinstance(self._legend_settings_by_plugin, dict):
            self._legend_settings_by_plugin = {}
        self._legend_settings_by_plugin[plugin_name] = dict(payload)
        settings = getattr(self, "settings", None)
        if isinstance(settings, QtCore.QSettings):
            try:
                settings.setValue("legend_settings_by_plugin", json.dumps(self._legend_settings_by_plugin))
                settings.sync()
            except Exception:
                pass

    def _save_current_graph(self) -> None:
        self._save_graph_for_current_tab()

    def _copy_graph_to_clipboard(
        self,
        *,
        canvas: FigureCanvas | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> bool:
        """Copy the current Matplotlib graph as a PNG image to the clipboard."""

        target = parent or self
        active_canvas = canvas or self._current_canvas()
        if active_canvas is None:
            QtWidgets.QMessageBox.information(
                target,
                "Copy Graph",
                "No plot area is available to copy.",
            )
            return False
        try:
            active_canvas.draw()
        except Exception:
            pass
        try:
            pixmap = active_canvas.grab()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                target,
                "Copy Graph",
                f"Failed to copy graph image:\n{exc}",
            )
            return False
        if pixmap.isNull():
            QtWidgets.QMessageBox.warning(
                target,
                "Copy Graph",
                "Failed to copy graph image.",
            )
            return False
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setPixmap(pixmap, QtGui.QClipboard.Mode.Clipboard)
        return True

    def _show_canvas_context_menu(self, event: Any) -> None:
        canvas = getattr(event, "canvas", None)
        if not isinstance(canvas, FigureCanvas):
            return
        tab: QtWidgets.QWidget | None = None
        for candidate_tab, candidate_canvas in self._canvas_by_tab.items():
            if candidate_canvas is canvas:
                tab = candidate_tab
                break
        if isinstance(tab, QtWidgets.QWidget) and self.tab_widget.currentWidget() is not tab:
            self._show_tab(tab)

        global_pos: QtCore.QPoint | None = None
        gui_event = getattr(event, "guiEvent", None)
        if isinstance(gui_event, QtGui.QMouseEvent):
            try:
                global_pos = gui_event.globalPosition().toPoint()
            except Exception:
                try:
                    global_pos = gui_event.globalPos()
                except Exception:
                    global_pos = None
        if global_pos is None:
            try:
                x_pos = int(round(float(getattr(event, "x", 0.0) or 0.0)))
                y_pos = int(round(float(getattr(event, "y", 0.0) or 0.0)))
                global_pos = canvas.mapToGlobal(QtCore.QPoint(x_pos, y_pos))
            except Exception:
                global_pos = QtGui.QCursor.pos()

        menu = QtWidgets.QMenu(self)
        copy_action = menu.addAction("Copy graph as PNG")
        export_action = menu.addAction("Export graph...")
        chosen = menu.exec(global_pos)
        if chosen is copy_action:
            self._copy_graph_to_clipboard(canvas=canvas)
        elif chosen is export_action:
            self._save_graph_for_current_tab(parent=self)

    @staticmethod
    def _is_canvas_context_click(event: Any) -> bool:
        gui_event = getattr(event, "guiEvent", None)
        if isinstance(gui_event, QtGui.QMouseEvent):
            try:
                return gui_event.button() == QtCore.Qt.MouseButton.RightButton
            except Exception:
                return False
        button = getattr(event, "button", None)
        if isinstance(button, int):
            return button == 3
        token = str(button).strip().lower()
        return token == "3" or "right" in token

    def _save_graph_for_current_tab(
        self,
        *,
        parent: QtWidgets.QWidget | None = None,
        preferred_suffix: str | None = None,
    ) -> bool:
        """Persist the currently visible graph to an image file."""

        target = parent or self
        tab_widget = self._tab_widget_like()
        if tab_widget is None:
            QtWidgets.QMessageBox.information(
                target,
                "Save Graph",
                "No plot area is available to save.",
            )
            return False

        tab = tab_widget.currentWidget()
        if tab is None:
            QtWidgets.QMessageBox.information(
                target,
                "Save Graph",
                "Select a plot tab before saving a graph.",
            )
            return False

        descriptor = self._tab_descriptors.get(tab)
        canvas = self._canvas_by_tab.get(tab)
        figure = None
        if canvas is not None:
            figure = getattr(canvas, "figure", None)
        if figure is None and descriptor is not None:
            axes_obj = getattr(descriptor, "axes", None)
            figure = getattr(axes_obj, "figure", None)
        if figure is None:
            QtWidgets.QMessageBox.information(
                target,
                "Save Graph",
                "The selected tab does not contain a Matplotlib graph to save.",
            )
            return False

        plugin_name = self._tab_plugin_name(descriptor)

        def _clean_stem(text: str) -> str:
            stem_chars = [
                ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text
            ]
            stem = "".join(stem_chars).strip("._")
            return stem or "Graph"

        default_name = ""
        if descriptor is not None:
            saved_path = str(descriptor.metadata.get("saved_path", "") or "").strip()
            if saved_path:
                default_name = Path(saved_path).stem
            elif descriptor.title:
                default_name = descriptor.title
            elif descriptor.root_label:
                default_name = descriptor.root_label
        if not default_name:
            default_name = "Graph"
        last_saved_format = getattr(self, "_last_graph_format", ".png")
        if not isinstance(last_saved_format, str):
            last_saved_format = ".png"
        preferred = (preferred_suffix or last_saved_format or ".png").strip().lower()
        if preferred not in {".png", ".pdf", ".svg"}:
            preferred = ".png"
        suggested_filename = _clean_stem(default_name) + preferred

        project_path = getattr(self, "_project_path", None)
        project_parent = project_path.parent if isinstance(project_path, Path) and project_path.exists() else None
        start_dir = self._preferred_export_directory(
            plugin_name,
            getattr(self, "_last_graph_dir", None),
            project_parent,
        )
        suggested_path = Path(start_dir) / suggested_filename

        if preferred == ".pdf":
            filters = "PDF Document (*.pdf);;PNG Image (*.png);;SVG Image (*.svg);;All files (*)"
            initial_filter = "PDF Document (*.pdf)"
        elif preferred == ".svg":
            filters = "SVG Image (*.svg);;PNG Image (*.png);;PDF Document (*.pdf);;All files (*)"
            initial_filter = "SVG Image (*.svg)"
        else:
            filters = "PNG Image (*.png);;PDF Document (*.pdf);;SVG Image (*.svg);;All files (*)"
            initial_filter = "PNG Image (*.png)"
        path_str, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            target,
            "Save Graph",
            str(suggested_path),
            filters,
            initial_filter,
        )
        if not path_str:
            return False

        path = Path(path_str)
        filter_map = {
            "PNG Image (*.png)": ".png",
            "PDF Document (*.pdf)": ".pdf",
            "SVG Image (*.svg)": ".svg",
        }
        valid_exts = {".png", ".pdf", ".svg"}
        suffix = path.suffix.lower()
        if suffix not in valid_exts:
            chosen_ext = filter_map.get(selected_filter, ".png")
            path = path.with_suffix(chosen_ext)
        final_suffix = path.suffix.lower()
        if final_suffix not in valid_exts:
            final_suffix = ".png"

        try:
            figure.savefig(str(path))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                target,
                "Save Graph",
                f"Failed to save the graph:\n{exc}",
            )
            return False

        try:
            status = self.statusBar()
        except Exception:
            status = None
        if status is not None:
            status.showMessage(f"Saved graph to {path}", 5000)

        self._remember_plugin_export_dir(plugin_name, path.parent)
        if descriptor is not None:
            try:
                descriptor.metadata["saved_path"] = str(path)
            except Exception:
                pass

        try:
            self._last_graph_dir = path.parent
        except Exception:
            self._last_graph_dir = None
        settings = getattr(self, "settings", None)
        self._last_graph_format = final_suffix
        if isinstance(settings, QtCore.QSettings):
            if getattr(self, "_last_graph_dir", None):
                settings.setValue("last_graph_dir", str(self._last_graph_dir))
            else:
                settings.remove("last_graph_dir")
            settings.setValue("last_graph_format", final_suffix)
            settings.sync()
        return True

    def _normalize_current_graph(self) -> None:
        raise NotImplementedError

    def _export_txt(self) -> None:
        tab_widget = self._tab_widget_like()
        if tab_widget is None:
            QtWidgets.QMessageBox.information(
                self,
                "Export TXT",
                "No plot area is available to export.",
            )
            return

        tab = tab_widget.currentWidget()
        if tab is None:
            QtWidgets.QMessageBox.information(
                self,
                "Export TXT",
                "Select a plot tab before exporting data.",
            )
            return

        descriptor = self._tab_descriptors.get(tab)
        if descriptor is None:
            QtWidgets.QMessageBox.information(
                self,
                "Export TXT",
                "The selected tab does not expose exportable data.",
            )
            return

        plugin_name = self._tab_plugin_name(descriptor)
        series = self._tab_series_data(descriptor, include_hidden=False)
        if not series:
            series = self._tab_series_data(descriptor, include_hidden=True)
        if not series:
            QtWidgets.QMessageBox.information(
                self,
                "Export TXT",
                "No plotted data is available to export.",
            )
            return

        def _clean_stem(text: str) -> str:
            stem_chars = [
                ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text
            ]
            return "".join(stem_chars).strip("._") or "series"

        default_name = descriptor.root_label or descriptor.title or "graph"
        tree = getattr(self, "project_tree", None)
        if isinstance(tree, QtWidgets.QTreeWidget):
            current = tree.currentItem()
            if isinstance(current, QtWidgets.QTreeWidgetItem):
                label = current.text(0).strip()
                if label:
                    default_name = label
        suggested = _clean_stem(default_name) + ".txt"

        project_path = getattr(self, "_project_path", None)
        project_parent = project_path.parent if isinstance(project_path, Path) and project_path.exists() else None
        start_dir = self._preferred_export_directory(
            plugin_name,
            getattr(self, "_last_export_dir", None),
            getattr(self, "_last_graph_dir", None),
            project_parent,
        )

        path_str, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export TXT",
            str(Path(start_dir) / suggested),
            "Text files (*.txt);;CSV files (*.csv);;All files (*)",
            "Text files (*.txt)",
        )
        if not path_str:
            return

        path = Path(path_str)
        suffix = path.suffix.lower()
        if suffix not in {".txt", ".csv"}:
            suffix = ".csv" if selected_filter.startswith("CSV") else ".txt"
            path = path.with_suffix(suffix)

        columns: Dict[str, pd.Series] = {}
        for label, x_data, y_data in series:
            stem = _clean_stem(label)
            columns[f"{stem}_x"] = pd.Series(x_data)
            columns[f"{stem}_y"] = pd.Series(y_data)
        frame = pd.DataFrame(columns)

        delimiter = "," if path.suffix.lower() == ".csv" else "\t"
        try:
            frame.to_csv(path, index=False, sep=delimiter)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Export TXT",
                f"Failed to export the data:\n{exc}",
            )
            return

        if hasattr(self, "statusBar"):
            try:
                status = self.statusBar()
                if status is not None:
                    status.showMessage(f"Exported data to {path}", 5000)
            except Exception:
                pass

        self._append_log(f"Exported plotted data to {path}")
        try:
            self._last_export_dir = path.parent
        except Exception:
            self._last_export_dir = None
        self._remember_plugin_export_dir(plugin_name, path.parent)
        settings = getattr(self, "settings", None)
        if isinstance(settings, QtCore.QSettings):
            if isinstance(self._last_export_dir, Path):
                settings.setValue("last_export_dir", str(self._last_export_dir))
            settings.sync()

        QtWidgets.QMessageBox.information(
            self,
            "Export TXT",
            f"Exported {len(series)} data series to {path}",
        )

    def _plugin_uses_shared_plot_workbooks(self, plugin_name: str | None) -> bool:
        if not plugin_name:
            return True
        plugin = None
        current_name = getattr(self, "_current_plotter_name", None)
        if isinstance(current_name, str) and current_name == plugin_name:
            plugin = getattr(self, "_current_plugin", None)
        if plugin is None:
            instances = getattr(self, "_plugin_instances", None)
            if isinstance(instances, dict):
                plugin = instances.get(plugin_name)
        if plugin is None:
            return True
        return bool(getattr(plugin, "uses_shared_plot_workbooks", True))

    @staticmethod
    def _safe_series_token(text: str, *, fallback: str) -> str:
        cleaned = "".join(
            ch if ch.isalnum() or ch in {"_", "-"} else "_"
            for ch in str(text)
        ).strip("_")
        return cleaned or fallback

    @staticmethod
    def _label_parts(text: str) -> tuple[str, str]:
        label = str(text or "").strip()
        if not label:
            return "", ""
        for pattern in (UNIT_SUFFIX_BRACKET_RE, UNIT_SUFFIX_PAREN_RE):
            match = pattern.match(label)
            if not match:
                continue
            prefix = str(match.group("prefix") or "").strip()
            unit = str(match.group("unit") or "").strip()
            return prefix or label, unit
        return label, ""

    @staticmethod
    def _paired_numeric_arrays(x_values: Any, y_values: Any) -> tuple[np.ndarray, np.ndarray]:
        try:
            x_raw = np.asarray(x_values).ravel()
            y_raw = np.asarray(y_values).ravel()
        except Exception:
            return np.asarray([], dtype=float), np.asarray([], dtype=float)
        count = min(int(x_raw.size), int(y_raw.size))
        if count <= 0:
            return np.asarray([], dtype=float), np.asarray([], dtype=float)
        x_num = pd.to_numeric(pd.Series(x_raw[:count]), errors="coerce").to_numpy(dtype=float)
        y_num = pd.to_numeric(pd.Series(y_raw[:count]), errors="coerce").to_numpy(dtype=float)
        mask = np.isfinite(x_num) & np.isfinite(y_num)
        if not np.any(mask):
            return np.asarray([], dtype=float), np.asarray([], dtype=float)
        return x_num[mask], y_num[mask]

    def _descriptor_axes(self, descriptor: TabDescriptor) -> list[Any]:
        axes_list: list[Any] = []
        seen: set[int] = set()
        primary = getattr(descriptor, "axes", None)
        if primary is not None:
            axes_list.append(primary)
            seen.add(id(primary))
        figure = getattr(primary, "figure", None)
        figure_axes = []
        if figure is not None:
            try:
                figure_axes = list(getattr(figure, "axes", []))
            except Exception:
                figure_axes = []
        for axes in figure_axes:
            if axes is None:
                continue
            marker = id(axes)
            if marker in seen:
                continue
            seen.add(marker)
            axes_list.append(axes)
        return axes_list

    def _normalize_series_label_for_export(
        self,
        label: str,
        *,
        descriptor: TabDescriptor,
    ) -> str:
        cleaned = str(label or "").strip()
        if not cleaned:
            return cleaned
        metadata = descriptor.metadata if isinstance(descriptor.metadata, dict) else {}
        plot_kind = str(metadata.get("plot_kind") or "").strip().lower()
        if plot_kind == "shape_memory_dual_axis_overlay":
            for prefix in ("load ", "stress "):
                if cleaned.lower().startswith(prefix):
                    trimmed = cleaned[len(prefix) :].strip()
                    if trimmed:
                        return trimmed
        return cleaned

    def _axis_label_parts(self, axes: Any) -> tuple[str, str, str, str]:
        x_label = ""
        y_label = ""
        if axes is not None:
            try:
                x_label = str(axes.get_xlabel() or "")
            except Exception:
                x_label = ""
            try:
                y_label = str(axes.get_ylabel() or "")
            except Exception:
                y_label = ""
        x_name, x_unit = self._label_parts(x_label)
        y_name, y_unit = self._label_parts(y_label)
        return x_name, x_unit, y_name, y_unit

    def _tab_series_entries(
        self,
        descriptor: TabDescriptor,
        *,
        include_hidden: bool = False,
    ) -> list[_TabSeriesExportEntry]:
        entries: list[_TabSeriesExportEntry] = []
        default_x, default_x_unit = self._label_parts(descriptor.x_label)
        default_y, default_y_unit = self._label_parts(descriptor.y_label)

        if isinstance(descriptor.lines, dict) and descriptor.lines:
            for index, state in enumerate(descriptor.lines.values(), start=1):
                raw_label = (state.label or f"Series {index}").strip() or f"Series {index}"
                label = self._normalize_series_label_for_export(raw_label, descriptor=descriptor)
                line_obj = getattr(state, "line", None)
                visible = True
                getter = getattr(line_obj, "get_visible", None)
                if callable(getter):
                    try:
                        visible = bool(getter())
                    except Exception:
                        visible = True
                if not include_hidden and not visible:
                    continue
                x_vals, y_vals = self._paired_numeric_arrays(state.x_data(), state.y_data())
                if x_vals.size == 0 or y_vals.size == 0:
                    continue
                line_axes = getattr(line_obj, "axes", None)
                x_label, x_unit, y_label, y_unit = self._axis_label_parts(line_axes)
                x_factor = self._axis_value_factor(line_axes, axis_name="x")
                y_factor = self._axis_value_factor(line_axes, axis_name="y")
                entries.append(
                    _TabSeriesExportEntry(
                        label=label,
                        x_values=x_vals,
                        y_values=y_vals,
                        x_label=x_label or default_x or "X",
                        x_unit=x_unit or default_x_unit,
                        y_label=y_label or default_y or "Y",
                        y_unit=y_unit or default_y_unit,
                        x_scale_factor=x_factor,
                        y_scale_factor=y_factor,
                    )
                )
        if entries:
            return entries

        line_counter = 0
        seen_lines: set[int] = set()
        collection_counter = 0
        seen_collections: set[int] = set()
        for axes in self._descriptor_axes(descriptor):
            x_label, x_unit, y_label, y_unit = self._axis_label_parts(axes)
            x_factor = self._axis_value_factor(axes, axis_name="x")
            y_factor = self._axis_value_factor(axes, axis_name="y")
            try:
                axis_lines = list(axes.get_lines())
            except Exception:
                axis_lines = []
            try:
                axis_collections = list(getattr(axes, "collections", []) or [])
            except Exception:
                axis_collections = []
            for line in axis_lines:
                marker = id(line)
                if marker in seen_lines:
                    continue
                seen_lines.add(marker)
                line_counter += 1
                raw_label = str(
                    getattr(line, "get_label", lambda: f"Series {line_counter}")() or ""
                ).strip()
                if not raw_label or raw_label.startswith("_"):
                    raw_label = f"Series {line_counter}"
                label = self._normalize_series_label_for_export(raw_label, descriptor=descriptor)
                if not include_hidden:
                    visible_getter = getattr(line, "get_visible", None)
                    if callable(visible_getter):
                        try:
                            if not bool(visible_getter()):
                                continue
                        except Exception:
                            pass
                x_vals, y_vals = self._paired_numeric_arrays(line.get_xdata(), line.get_ydata())
                if x_vals.size == 0 or y_vals.size == 0:
                    continue
                entries.append(
                    _TabSeriesExportEntry(
                        label=label,
                        x_values=x_vals,
                        y_values=y_vals,
                        x_label=x_label or default_x or "X",
                        x_unit=x_unit or default_x_unit,
                        y_label=y_label or default_y or "Y",
                        y_unit=y_unit or default_y_unit,
                        x_scale_factor=x_factor,
                        y_scale_factor=y_factor,
                    )
                )
            for collection in axis_collections:
                marker = id(collection)
                if marker in seen_collections:
                    continue
                seen_collections.add(marker)
                if not include_hidden:
                    visible_getter = getattr(collection, "get_visible", None)
                    if callable(visible_getter):
                        try:
                            if not bool(visible_getter()):
                                continue
                        except Exception:
                            pass
                collection_counter += 1
                raw_label = str(
                    getattr(collection, "get_label", lambda: f"Series {line_counter + collection_counter}")()
                    or ""
                ).strip()
                if not raw_label or raw_label.startswith("_"):
                    raw_label = f"Series {line_counter + collection_counter}"
                label = self._normalize_series_label_for_export(raw_label, descriptor=descriptor)
                offsets_getter = getattr(collection, "get_offsets", None)
                if not callable(offsets_getter):
                    continue
                try:
                    offsets = np.asarray(offsets_getter(), dtype=float)
                except Exception:
                    continue
                if offsets.ndim != 2 or offsets.shape[1] < 2:
                    continue
                x_vals, y_vals = self._paired_numeric_arrays(offsets[:, 0], offsets[:, 1])
                if x_vals.size == 0 or y_vals.size == 0:
                    continue
                entries.append(
                    _TabSeriesExportEntry(
                        label=label,
                        x_values=x_vals,
                        y_values=y_vals,
                        x_label=x_label or default_x or "X",
                        x_unit=x_unit or default_x_unit,
                        y_label=y_label or default_y or "Y",
                        y_unit=y_unit or default_y_unit,
                        x_scale_factor=x_factor,
                        y_scale_factor=y_factor,
                    )
                )
        return entries

    @staticmethod
    def _axis_value_factor(axes: Any, *, axis_name: str) -> float:
        if axes is None:
            return 1.0
        attr = f"_mw_{axis_name}_value_factor"
        try:
            value = float(getattr(axes, attr, 1.0) or 1.0)
        except Exception:
            return 1.0
        if not math.isfinite(value) or math.isclose(value, 0.0, abs_tol=1e-12):
            return 1.0
        return value

    def _tab_series_data(
        self,
        descriptor: TabDescriptor,
        *,
        include_hidden: bool = False,
    ) -> list[tuple[str, np.ndarray, np.ndarray]]:
        entries = self._tab_series_entries(descriptor, include_hidden=include_hidden)
        return [(entry.label, entry.x_values, entry.y_values) for entry in entries]

    def _tab_has_exportable_series(
        self,
        tab: QtWidgets.QWidget | None = None,
    ) -> bool:
        target_tab = tab or self.tab_widget.currentWidget()
        if not isinstance(target_tab, QtWidgets.QWidget):
            return False
        descriptor = self._tab_descriptors.get(target_tab)
        if descriptor is None:
            return False
        if self._tab_series_data(descriptor, include_hidden=False):
            return True
        return bool(self._tab_series_data(descriptor, include_hidden=True))

    def _shared_plot_workbook_key(
        self,
        tab: QtWidgets.QWidget,
        *,
        plugin_name: str | None,
    ) -> str:
        plugin_token = self._safe_series_token(plugin_name or "plugin", fallback="plugin")
        return f"shared_plot::{plugin_token}::{id(tab)}"

    def _build_shared_plot_workbook(
        self,
        tab: QtWidgets.QWidget,
        descriptor: TabDescriptor,
    ) -> tuple[WorkbookData, list[WorksheetData]] | None:
        plugin_name = self._tab_plugin_name(descriptor)
        entries = self._tab_series_entries(descriptor, include_hidden=False)
        if not entries:
            entries = self._tab_series_entries(descriptor, include_hidden=True)
        if not entries:
            return None

        workbook_name = str(descriptor.root_label or descriptor.title or "Plot data").strip() or "Plot data"
        workbook = WorkbookData(
            key=self._shared_plot_workbook_key(tab, plugin_name=plugin_name),
            name=workbook_name,
            source=None,
            folder=None,
        )

        columns: Dict[str, pd.Series] = {}
        metadata: Dict[str, WorksheetColumnMeta] = {}
        axis_roles: list[str] = []
        used_columns: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            label = entry.label
            x_vals = entry.x_values
            y_vals = entry.y_values
            token = self._safe_series_token(label, fallback=f"series_{index:02d}")
            x_name = f"{token}_x"
            y_name = f"{token}_y"
            suffix = 1
            while x_name in used_columns or y_name in used_columns:
                suffix += 1
                x_name = f"{token}_x_{suffix}"
                y_name = f"{token}_y_{suffix}"
            used_columns.add(x_name)
            used_columns.add(y_name)
            columns[x_name] = pd.Series(x_vals * float(entry.x_scale_factor or 1.0))
            columns[y_name] = pd.Series(y_vals * float(entry.y_scale_factor or 1.0))
            metadata[x_name] = WorksheetColumnMeta(
                long_name=entry.x_label or "X",
                units=entry.x_unit,
                comments=label,
            )
            metadata[y_name] = WorksheetColumnMeta(
                long_name=entry.y_label or "Y",
                units=entry.y_unit,
                comments=label,
            )
            axis_roles.extend(["X", "Y"])

        frame = pd.DataFrame(columns)
        worksheet = WorksheetData(
            key=self._worksheet_key(workbook.key, "Plot data"),
            name="Plot data",
            dataframe=frame,
            columns=metadata,
            source=None,
            workbook_key=workbook.key,
            axis_roles="".join(axis_roles),
        )
        workbook.worksheets = [worksheet.key]
        return workbook, [worksheet]

    def _register_shared_plot_workbook_for_tab(
        self,
        tab: QtWidgets.QWidget,
        descriptor: TabDescriptor,
    ) -> None:
        plugin_name = self._tab_plugin_name(descriptor)
        if not self._plugin_uses_shared_plot_workbooks(plugin_name):
            return
        built = self._build_shared_plot_workbook(tab, descriptor)
        if built is None:
            self._remove_shared_plot_workbook_for_tab(tab)
            return
        workbook, worksheets = built
        self._shared_plot_workbook_keys.add(workbook.key)
        self._shared_plot_workbook_by_tab[tab] = workbook.key
        self._register_imported_workbook(workbook, worksheets)

    def _remove_shared_plot_workbook_for_tab(self, tab: QtWidgets.QWidget) -> None:
        key = self._shared_plot_workbook_by_tab.pop(tab, None)
        if key is None:
            return
        self._shared_plot_workbook_keys.discard(key)
        if key in self._workbooks:
            self._remove_imported_workbook(key)

    def _prune_shared_plot_workbooks(self) -> None:
        if self._pruning_shared_plot_workbooks:
            return
        self._pruning_shared_plot_workbooks = True
        try:
            stale_tabs = [
                tab
                for tab in list(self._shared_plot_workbook_by_tab.keys())
                if self.tab_widget.indexOf(tab) < 0
            ]
            for tab in stale_tabs:
                self._remove_shared_plot_workbook_for_tab(tab)
            stale_keys = [
                key for key in list(self._shared_plot_workbook_keys) if key not in self._workbooks
            ]
            for key in stale_keys:
                self._shared_plot_workbook_keys.discard(key)
        finally:
            self._pruning_shared_plot_workbooks = False

    def _shared_plot_workbooks_for_plugin(self, plugin_name: str | None) -> list[WorkbookData]:
        results: list[WorkbookData] = []
        seen: set[Hashable] = set()
        for tab, key in list(self._shared_plot_workbook_by_tab.items()):
            if self.tab_widget.indexOf(tab) < 0:
                continue
            descriptor = self._tab_descriptors.get(tab)
            if descriptor is None:
                continue
            tab_plugin = self._tab_plugin_name(descriptor)
            if plugin_name and tab_plugin != plugin_name:
                continue
            workbook = self._workbooks.get(key)
            if workbook is None:
                continue
            if workbook.key in seen:
                continue
            worksheets = [self._worksheets.get(sheet_key) for sheet_key in workbook.worksheets]
            if not any(sheet is not None for sheet in worksheets):
                continue
            seen.add(workbook.key)
            results.append(workbook)
        return results

    def _open_origin_shared(self) -> None:
        plugin_name = getattr(self, "_current_plotter_name", None)
        plugin_token = plugin_name if isinstance(plugin_name, str) and plugin_name.strip() else None
        self._prune_shared_plot_workbooks()
        workbooks = self._shared_plot_workbooks_for_plugin(plugin_token)
        if not workbooks:
            tab = self.tab_widget.currentWidget()
            descriptor = self._tab_descriptors.get(tab) if tab is not None else None
            if tab is not None and descriptor is not None:
                self._register_shared_plot_workbook_for_tab(tab, descriptor)
                workbooks = self._shared_plot_workbooks_for_plugin(plugin_token)
        if not workbooks:
            QtWidgets.QMessageBox.information(
                self,
                "Open in Origin",
                "Plot at least one graph before exporting to Origin.",
            )
            return

        try:
            exported, plotted, errors = self._push_workbooks_to_origin(
                workbooks,
                create_graphs=True,
            )
        except ModuleNotFoundError:
            QtWidgets.QMessageBox.warning(
                self,
                "Open in Origin",
                "The OriginPro Python package is not available. Install it to export data.",
            )
            return
        except Exception as exc:  # pragma: no cover - GUI error path
            QtWidgets.QMessageBox.critical(
                self,
                "Open in Origin",
                f"Failed to export data to Origin:\n{exc}",
            )
            self._append_log(f"Origin export failed: {exc}", level="error")
            return

        if exported:
            schedule_origin_release()
            message = (
                f"Sent {exported} worksheet{'s' if exported != 1 else ''} to Origin "
                f"and created {plotted} graph{'s' if plotted != 1 else ''}."
            )
            self._append_log(message)
            QtWidgets.QMessageBox.information(self, "Open in Origin", message)
        else:
            QtWidgets.QMessageBox.information(
                self,
                "Open in Origin",
                "No worksheet data was exported to Origin.",
            )

        if errors:
            details = "\n".join(errors)
            self._append_log("Some Origin exports failed:\n" + details, level="error")
            QtWidgets.QMessageBox.warning(
                self,
                "Open in Origin",
                "Some items could not be exported. Check the message log for details.",
            )

    def _open_origin_prompt(self) -> None:
        raise NotImplementedError

    def _export_workbooks_to_origin(self) -> None:
        workbooks: list[WorkbookData] = []
        for workbook in self._workbooks.values():
            worksheets = [self._worksheets.get(key) for key in workbook.worksheets]
            if any(sheet is not None for sheet in worksheets):
                workbooks.append(workbook)
        if not workbooks:
            QtWidgets.QMessageBox.information(
                self,
                "Export workbooks",
                "No worksheets are available to export to Origin.",
            )
            return

        try:
            exported, _plotted, errors = self._push_workbooks_to_origin(workbooks)
        except ModuleNotFoundError:
            QtWidgets.QMessageBox.warning(
                self,
                "Export workbooks",
                "The OriginPro Python package is not available. Install it to export workbooks.",
            )
            return
        except Exception as exc:  # pragma: no cover - GUI error path
            QtWidgets.QMessageBox.critical(
                self,
                "Export workbooks",
                f"Failed to export workbooks to Origin:\n{exc}",
            )
            self._append_log(f"Origin workbook export failed: {exc}", level="error")
            return

        if exported:
            schedule_origin_release()
            message = f"Exported {exported} worksheet{'s' if exported != 1 else ''} to Origin."
            QtWidgets.QMessageBox.information(self, "Export workbooks", message)
            self._append_log(message)
        else:
            QtWidgets.QMessageBox.information(
                self,
                "Export workbooks",
                "No worksheets were exported to Origin.",
            )

        if errors:
            details = "\n".join(errors)
            self._append_log(
                "Some worksheets could not be exported to Origin:\n" + details,
                level="error",
            )
            QtWidgets.QMessageBox.warning(
                self,
                "Export workbooks",
                "Some worksheets could not be exported. Check the message log for details.",
            )

    def _push_workbooks_to_origin(
        self,
        workbooks: Iterable[WorkbookData],
        *,
        create_graphs: bool = False,
        graph_callback: Callable[[Any, WorkbookData, WorksheetData], None] | None = None,
        keep_origin_open: bool = True,
    ) -> tuple[int, int, list[str]]:
        workbook_list = list(workbooks)
        errors: list[str] = []
        exported = 0
        plotted = 0

        self._begin_task_progress(
            "Exporting workbooks to Origin...",
            maximum=max(1, len(workbook_list)),
            value=0,
        )
        try:
            with origin_session(keep_open=keep_origin_open) as origin_any:
                if keep_origin_open:
                    schedule_origin_release()
                workbook_names: set[str] = set()
                for workbook_index, workbook in enumerate(workbook_list, start=1):
                    self._update_task_progress(
                        value=workbook_index - 1,
                        title=(
                            f"Exporting workbook {workbook_index}/{len(workbook_list)}: "
                            f"{workbook.name}"
                        ),
                    )
                    worksheets = [self._worksheets.get(key) for key in workbook.worksheets]
                    worksheet_objs = [sheet for sheet in worksheets if sheet is not None]
                    if not worksheet_objs:
                        continue

                    book_name = self._origin_unique_name(
                        workbook_names,
                        workbook.name,
                        fallback="Workbook",
                        limit=32,
                    )
                    try:
                        book_obj = origin_any.new_book("w", lname=book_name)
                    except Exception as exc:  # pragma: no cover - depends on Origin runtime
                        errors.append(f"{workbook.name}: {exc}")
                        continue
                    if book_obj is None:
                        errors.append(f"{workbook.name}: Origin did not return a workbook")
                        continue

                    book = cast(Any, book_obj)
                    try:
                        book.activate()
                    except Exception:
                        pass
                    for attr, value in (("lname", book_name), ("name", book_name[:13])):
                        try:
                            setattr(book, attr, value)
                        except Exception:
                            pass

                    sheet_names: set[str] = set()
                    for index, worksheet in enumerate(worksheet_objs):
                        sheet_name = self._origin_unique_name(
                            sheet_names,
                            worksheet.name,
                            fallback="Sheet",
                            limit=32,
                        )
                        sheet = None
                        try:
                            if index < len(book):
                                sheet = book[index]
                            else:
                                add_sheet = getattr(book, "add_sheet", None)
                                if callable(add_sheet):
                                    try:
                                        sheet = add_sheet("w", lname=sheet_name)
                                    except TypeError:
                                        sheet = add_sheet()
                                if sheet is None:
                                    sheet = origin_any.new_sheet("w", lname=sheet_name)
                        except Exception as exc:  # pragma: no cover - Origin runtime dependent
                            errors.append(f"{workbook.name}/{worksheet.name}: {exc}")
                            continue

                        if sheet is None:
                            errors.append(
                                f"{workbook.name}/{worksheet.name}: Unable to create worksheet"
                            )
                            continue

                        try:
                            sheet.name = sheet_name
                        except Exception:
                            pass

                        frame = worksheet.dataframe
                        try:
                            sheet.from_df(frame)
                        except Exception as exc:  # pragma: no cover - Origin runtime dependent
                            errors.append(f"{workbook.name}/{worksheet.name}: {exc}")
                            continue

                        roles = ""
                        try:
                            roles = (worksheet.axis_roles or "").strip()
                        except Exception:
                            roles = ""
                        if not roles:
                            roles = self._origin_axis_roles(frame)
                        if roles:
                            try:
                                sheet.cols_axis(roles)
                            except Exception:
                                pass

                        self._apply_origin_metadata(origin_any, sheet, worksheet)
                        exported += 1
                        if create_graphs:
                            try:
                                if self._plot_origin_worksheet(
                                    origin_any,
                                    sheet,
                                    workbook,
                                    worksheet,
                                    roles=roles,
                                    graph_callback=graph_callback,
                                ):
                                    plotted += 1
                            except Exception as exc:  # pragma: no cover - Origin runtime dependent
                                errors.append(f"{workbook.name}/{worksheet.name} graph: {exc}")
        finally:
            self._update_task_progress(
                value=max(1, len(workbook_list)),
                title="Origin export complete.",
            )
            self._end_task_progress()

        return exported, plotted, errors

    def _apply_origin_metadata(
        self,
        origin_any: Any,
        sheet: Any,
        worksheet: WorksheetData,
    ) -> None:
        columns = list(worksheet.dataframe.columns)
        if not columns:
            return
        try:
            sheet.activate()
        except Exception:
            pass
        try:
            header_rows = getattr(sheet, "header_rows", None)
            if callable(header_rows):
                header_rows("LUC")
        except Exception:
            pass

        for index, column in enumerate(columns):
            meta = worksheet.columns.get(
                column, WorksheetColumnMeta(long_name=str(column))
            )
            label = meta.long_name or str(column)
            self._set_origin_column_text(origin_any, sheet, index, label, code="L", field="lname")
            self._set_origin_column_text(
                origin_any,
                sheet,
                index,
                meta.units,
                code="U",
                field="unit",
            )
            self._set_origin_column_text(
                origin_any,
                sheet,
                index,
                meta.comments,
                code="C",
                field="comment",
            )

            if meta.formula:
                safe_formula = self._escape_origin_text(str(meta.formula))
                command = f"wks.col{index + 1}.formula$=\"{safe_formula}\";"
                try:
                    origin_any.lt_exec(command)
                except Exception:
                    continue

    def _set_origin_column_text(
        self,
        origin_any: Any,
        sheet: Any,
        index: int,
        value: str,
        *,
        code: str,
        field: str,
    ) -> None:
        text = str(value or "").strip()
        if not text:
            return

        set_label = getattr(sheet, "set_label", None)
        if callable(set_label):
            try:
                set_label(index, text, code)
                return
            except TypeError:
                if code == "L":
                    try:
                        set_label(index, text)
                        return
                    except Exception:
                        pass
            except Exception:
                pass

        if code == "C":
            set_comment = getattr(sheet, "set_comment", None)
            if callable(set_comment):
                try:
                    set_comment(index, text)
                    return
                except Exception:
                    pass

        safe_text = self._escape_origin_text(text)
        command = f"wks.col{index + 1}.{field}$=\"{safe_text}\";"
        try:
            origin_any.lt_exec(command)
        except Exception:
            return

    def _origin_roles_for_sheet(self, frame: pd.DataFrame, roles: str) -> list[str]:
        columns = list(frame.columns) if frame is not None else []
        if not columns:
            return []
        valid_roles = {"X", "Y", "Z", "N"}
        role_list = [
            char.upper()
            for char in str(roles or "")
            if char.strip() and char.upper() in valid_roles
        ]
        if len(role_list) < len(columns):
            fallback = [char.upper() for char in self._origin_axis_roles(frame)]
            role_list.extend(fallback[len(role_list): len(columns)])
        if len(role_list) < len(columns):
            role_list.extend(["N"] * (len(columns) - len(role_list)))
        role_list = role_list[: len(columns)]
        if "X" not in role_list:
            for index, column in enumerate(columns):
                try:
                    if is_numeric_dtype(frame[column]):
                        role_list[index] = "X"
                        break
                except Exception:
                    continue
        return role_list

    def _origin_plot_pairs(self, frame: pd.DataFrame, roles: str) -> list[tuple[int, int]]:
        columns = list(frame.columns) if frame is not None else []
        if not columns:
            return []
        role_list = self._origin_roles_for_sheet(frame, roles)
        numeric_indices = {
            index
            for index, column in enumerate(columns)
            if index < len(role_list) and is_numeric_dtype(frame[column])
        }
        x_indices = [
            index
            for index, role in enumerate(role_list)
            if role == "X" and index in numeric_indices
        ]
        y_indices = [
            index
            for index, role in enumerate(role_list)
            if role == "Y" and index in numeric_indices
        ]
        if not x_indices:
            x_indices = [index for index in numeric_indices]
            if x_indices:
                x_indices = [x_indices[0]]
        if not y_indices:
            y_indices = [
                index
                for index in sorted(numeric_indices)
                if index not in x_indices
            ]
        if not x_indices or not y_indices:
            return []
        pairs: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for y_index in y_indices:
            if y_index not in numeric_indices:
                continue
            x_index = x_indices[0]
            for candidate in x_indices:
                if candidate <= y_index and candidate in numeric_indices:
                    x_index = candidate
                else:
                    break
            if x_index == y_index:
                continue
            pair = (x_index, y_index)
            if pair in seen:
                continue
            seen.add(pair)
            pairs.append(pair)
        return pairs

    def _origin_axis_title(
        self,
        worksheet: WorksheetData,
        column_name: str,
    ) -> str:
        meta = worksheet.columns.get(column_name)
        if isinstance(meta, WorksheetColumnMeta):
            long_name = str(meta.long_name or "").strip() or str(column_name)
            units = str(meta.units or "").strip()
            if units:
                return f"{long_name} [{units}]"
            return long_name
        return str(column_name)

    def _origin_axis_label_command(self, axis_name: str, title: str) -> str | None:
        token = str(axis_name or "").strip().lower()
        label_switch = {
            "x": "xb",
            "x2": "xt",
            "y": "yl",
            "y2": "yr",
        }.get(token)
        if not label_switch:
            return None
        safe_title = self._escape_origin_text(title)
        # Axis-title switches should be used directly (no string-substitution switch).
        return f'label -s -{label_switch} "{safe_title}";'

    @staticmethod
    def _origin_lt_exec(executor: Any, command: str) -> bool:
        if not callable(executor):
            return False
        try:
            result = executor(command)
        except Exception:
            return False
        # originpro lt_exec may return bool/int or None; only explicit False/0 is failure.
        return bool(result) if result is not None else True

    @staticmethod
    def _activate_origin_layer(layer: Any) -> None:
        activate = getattr(layer, "activate", None)
        if callable(activate):
            try:
                activate()
            except Exception:
                pass

    def _set_origin_axis_title(self, layer: Any, axis_name: str, title: str) -> None:
        try:
            shared_set_origin_axis_title(layer, axis_name, title)
        except Exception:
            command = self._origin_axis_label_command(axis_name, title)
            if not command:
                return
            self._activate_origin_layer(layer)
            lt_exec = getattr(layer, "lt_exec", None)
            self._origin_lt_exec(lt_exec, command)

    def _set_origin_graph_title(
        self,
        origin_any: Any,
        graph: Any,
        primary_layer: Any,
        title: str,
    ) -> None:
        try:
            shared_set_origin_graph_title(origin_any, graph, primary_layer, title)
        except Exception:
            safe_title = self._escape_origin_text(title)
            self._activate_origin_layer(primary_layer)
            layer_lt_exec = getattr(primary_layer, "lt_exec", None)
            if callable(layer_lt_exec):
                self._origin_lt_exec(layer_lt_exec, f'label -s -n title "{safe_title}";')

        label_getter = getattr(primary_layer, "label", None)
        if callable(label_getter):
            for label_name in ("Title", "title", "py_title"):
                try:
                    title_label = label_getter(label_name)
                except Exception:
                    title_label = None
                if title_label is None:
                    continue
                try:
                    self._append_log(
                        f"Origin title snapshot ({title}) via {label_name}: "
                        f"{self._origin_title_label_snapshot(title_label)}"
                    )
                    break
                except Exception:
                    continue

    @staticmethod
    def _origin_title_xy(layer: Any) -> tuple[float, float] | None:
        get_float = getattr(layer, "get_float", None)
        if not callable(get_float):
            return None
        try:
            x_from = float(get_float("x.from"))
            x_to = float(get_float("x.to"))
            y_from = float(get_float("y.from"))
            y_to = float(get_float("y.to"))
        except Exception:
            return None
        if not all(np.isfinite(value) for value in (x_from, x_to, y_from, y_to)):
            return None
        x_span = x_to - x_from
        y_span = y_to - y_from
        if x_span <= 0.0 or y_span <= 0.0:
            return None
        # Keep attach=0 (data coordinates): on this Origin 2026 setup, page/layer
        # attachment modes do not render title labels in exported PNGs reliably.
        # Position the title above the plot box (not inside it), while staying
        # close enough to avoid clipping at the page edge.
        return ((x_from + x_to) / 2.0, y_to + (y_span * 0.03))

    def _position_origin_title_label(self, label_obj: Any, *, layer: Any | None = None) -> None:
        if label_obj is None:
            return
        if hasattr(label_obj, "show"):
            try:
                label_obj.show = True
            except Exception:
                pass
        set_int = getattr(label_obj, "set_int", None)
        if callable(set_int):
            for key, value in (
                ("show", 1),
                ("attach", 0),
                ("horzalign", 1),
                ("vertalign", 2),
            ):
                try:
                    set_int(key, int(value))
                except Exception:
                    continue
        target_x = 50.0
        target_y = 108.0
        if layer is not None:
            computed = self._origin_title_xy(layer)
            if computed is not None:
                target_x, target_y = computed
        set_float = getattr(label_obj, "set_float", None)
        if callable(set_float):
            for key, value in (
                ("x", target_x),
                ("y", target_y),
                ("fsize", 22.0),
            ):
                try:
                    set_float(key, float(value))
                except Exception:
                    continue

    @staticmethod
    def _origin_title_label_snapshot(label_obj: Any) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        if label_obj is None:
            return snapshot
        text = getattr(label_obj, "text", None)
        if isinstance(text, str):
            snapshot["text"] = text
        get_int = getattr(label_obj, "get_int", None)
        if callable(get_int):
            for key in ("attach", "show"):
                try:
                    snapshot[key] = int(get_int(key))
                except Exception:
                    snapshot[key] = "err"
        get_float = getattr(label_obj, "get_float", None)
        if callable(get_float):
            for key in ("x", "y", "fsize"):
                try:
                    snapshot[key] = float(get_float(key))
                except Exception:
                    snapshot[key] = "err"
        return snapshot

    def _origin_add_topx_righty_layer(self, origin_any: Any, graph: Any) -> Any | None:
        add_layer = getattr(graph, "add_layer", None)
        if callable(add_layer):
            # Prefer TopXRightY preset so secondary axes remain linked to
            # primary scales for dual-axis overlays.
            try:
                added = add_layer(4)
                if added is not None:
                    return added
            except Exception:
                logging.getLogger(__name__).exception("Origin add_layer(4) failed for dual-axis export.")
            # Fallback to a plain layer if preset creation fails.
            try:
                added_plain = add_layer()
                if added_plain is not None:
                    return added_plain
            except Exception:
                pass
        return None

    def _set_origin_layer_top_margin(self, layer: Any, top: float = ORIGIN_EXPORT_LAYER_TOP) -> None:
        self._activate_origin_layer(layer)
        set_float = getattr(layer, "set_float", None)
        if callable(set_float):
            try:
                set_float("top", float(top))
            except Exception:
                pass
            try:
                set_float("bottom", float(ORIGIN_EXPORT_LAYER_BOTTOM))
            except Exception:
                pass
        lt_exec = getattr(layer, "lt_exec", None)
        if callable(lt_exec):
            self._origin_lt_exec(
                lt_exec,
                f"layer.top={float(top):.3f}; layer.bottom={float(ORIGIN_EXPORT_LAYER_BOTTOM):.3f};",
            )

    def _configure_origin_layer_axes(
        self,
        layer: Any,
        *,
        secondary_axes_only: bool,
    ) -> None:
        self._activate_origin_layer(layer)
        set_int = getattr(layer, "set_int", None)
        if callable(set_int):
            try:
                if secondary_axes_only:
                    # Show top/right only on secondary layer.
                    set_int("x.showAxes", 2)
                    set_int("y.showAxes", 2)
                    # Origin 2026 quirk: writing showLabels=2 can flip x2/y2 label
                    # mode to a duplicated state. Set side visibility explicitly.
                    set_int("x.showlabel", 0)
                    set_int("x2.showlabel", 1)
                    set_int("y.showlabel", 0)
                    set_int("y2.showlabel", 1)
                else:
                    # Show bottom/left only on primary layer.
                    set_int("x.showAxes", 1)
                    set_int("y.showAxes", 1)
                    set_int("x.showlabel", 1)
                    set_int("x2.showlabel", 0)
                    set_int("y.showlabel", 1)
                    set_int("y2.showlabel", 0)
            except Exception:
                pass
        lt_exec = getattr(layer, "lt_exec", None)
        if not callable(lt_exec):
            return
        if secondary_axes_only:
            commands = (
                # 2 = show opposite axes only (top/right)
                "layer.x.showAxes=2;",
                "layer.y.showAxes=2;",
                "layer.x.showlabel=0;",
                "layer.x2.showlabel=1;",
                "layer.y.showlabel=0;",
                "layer.y2.showlabel=1;",
            )
        else:
            commands = (
                # 1 = show primary axes only (bottom/left)
                "layer.x.showAxes=1;",
                "layer.y.showAxes=1;",
                "layer.x.showlabel=1;",
                "layer.x2.showlabel=0;",
                "layer.y.showlabel=1;",
                "layer.y2.showlabel=0;",
            )
        for command in commands:
            self._origin_lt_exec(lt_exec, command)

    def _origin_layer_axis_snapshot(self, layer: Any) -> dict[str, float | str]:
        snapshot: dict[str, float | str] = {}
        keys = (
            "x.showAxes",
            "y.showAxes",
            "x.showLabels",
            "y.showLabels",
            "x.showlabel",
            "x2.showlabel",
            "y.showlabel",
            "y2.showlabel",
        )
        get_float = getattr(layer, "get_float", None)
        for key in keys:
            value: float | str = "n/a"
            if callable(get_float):
                try:
                    value = float(get_float(key))
                except Exception:
                    value = "err"
            snapshot[key] = value
        lt_range_getter = getattr(layer, "lt_range", None)
        if callable(lt_range_getter):
            try:
                snapshot["layer"] = str(lt_range_getter() or "")
            except Exception:
                snapshot["layer"] = "unknown"
        return snapshot

    def _origin_plot_label(
        self,
        worksheet: WorksheetData,
        y_index: int,
    ) -> str:
        columns = list(worksheet.dataframe.columns)
        if y_index < 0 or y_index >= len(columns):
            return ""
        column_name = columns[y_index]
        meta = worksheet.columns.get(column_name)
        label = ""
        if isinstance(meta, WorksheetColumnMeta):
            label = str(meta.comments or "").strip()
            if not label:
                label = str(meta.long_name or "").strip()
        if not label:
            label = str(column_name)
        return label

    def _origin_graph_templates(self, origin_any: Any) -> list[str]:
        _ = origin_any
        templates: list[str] = ["ORIGIN", "line", "scatter"]
        seen: set[str] = set()
        ordered: list[str] = []
        for template in templates:
            token = template.strip()
            if not token:
                continue
            lowered = token.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(token)
        return ordered

    def _apply_origin_plot_label(
        self,
        plot_obj: Any,
        worksheet: WorksheetData,
        y_index: int,
        *,
        override_label: str | None = None,
    ) -> None:
        label = str(override_label) if override_label is not None else self._origin_plot_label(worksheet, y_index)
        if not label.strip():
            for attr in ("lname", "long_name"):
                try:
                    setattr(plot_obj, attr, "")
                except Exception:
                    continue
            return
        for attr, value in (
            ("lname", label),
            ("long_name", label),
            ("name", self._origin_safe_token(label, fallback="Plot")[:13]),
        ):
            try:
                setattr(plot_obj, attr, value)
            except Exception:
                continue

    @staticmethod
    def _set_origin_plot_visible(plot_obj: Any, visible: bool) -> None:
        for attr in ("show", "visible", "Visible", "Show"):
            if hasattr(plot_obj, attr):
                try:
                    setattr(plot_obj, attr, bool(visible))
                    return
                except Exception:
                    continue

    @staticmethod
    def _apply_origin_plot_style(plot_obj: Any, *, color: str | None = None) -> None:
        if not color:
            return
        for attr, value in (
            ("color", color),
            ("symbol_edge_color", color),
            ("symbol_fill_color", color),
            ("line_width", 1.5),
            ("symbol_kind", 2),
            ("symbol_size", 4.0),
        ):
            if not hasattr(plot_obj, attr):
                continue
            try:
                setattr(plot_obj, attr, value)
            except Exception:
                continue

    def _plot_origin_worksheet(
        self,
        origin_any: Any,
        sheet: Any,
        workbook: WorkbookData,
        worksheet: WorksheetData,
        *,
        roles: str,
        graph_callback: Callable[[Any, WorkbookData, WorksheetData], None] | None = None,
    ) -> bool:
        frame = worksheet.dataframe
        pairs = self._origin_plot_pairs(frame, roles)
        if not pairs:
            return False

        graph_obj = None
        graph_template_used = "<unresolved>"
        for template in self._origin_graph_templates(origin_any):
            try:
                graph_obj = origin_any.new_graph(template=template)
            except Exception:
                continue
            if graph_obj is not None:
                graph_template_used = template
                break
        if graph_obj is None:
            try:
                graph_obj = origin_any.new_graph()
                graph_template_used = "<default>"
            except Exception:
                return False

        graph = cast(Any, graph_obj)
        try:
            graph.activate()
        except Exception:
            pass

        layer = None
        try:
            layer = graph[0]
        except Exception:
            layer = None
        if layer is None:
            add_layer = getattr(graph, "add_layer", None)
            if callable(add_layer):
                try:
                    layer = add_layer()
                except Exception:
                    layer = None
        if layer is None:
            return False

        columns = list(frame.columns)
        pair_groups: list[dict[str, Any]] = []
        for x_index, y_index in pairs:
            if not (0 <= x_index < len(columns) and 0 <= y_index < len(columns)):
                continue
            x_title = self._origin_axis_title(worksheet, columns[x_index]) or "X"
            y_title = self._origin_axis_title(worksheet, columns[y_index]) or "Y"
            group = next(
                (
                    item
                    for item in pair_groups
                    if item.get("x_title") == x_title and item.get("y_title") == y_title
                ),
                None,
            )
            if group is None:
                pair_groups.append(
                    {
                        "x_title": x_title,
                        "y_title": y_title,
                        "pairs": [(x_index, y_index)],
                    }
                )
            else:
                cast(list[tuple[int, int]], group["pairs"]).append((x_index, y_index))
        if not pair_groups:
            return False

        layer_groups: list[tuple[Any, str, str, list[tuple[int, int]]]] = []
        for index, group in enumerate(pair_groups):
            target_layer = layer
            if index > 0:
                extra_layer = self._origin_add_topx_righty_layer(origin_any, graph)
                if extra_layer is not None:
                    target_layer = extra_layer
            layer_groups.append(
                (
                    target_layer,
                    str(group.get("x_title") or "X"),
                    str(group.get("y_title") or "Y"),
                    list(cast(list[tuple[int, int]], group.get("pairs") or [])),
                )
            )
        plotted_any = False
        seen_plot_labels: set[str] = set()
        primary_group_labels: set[str] = set()
        color_by_label: dict[str, str] = {}
        next_color_index = 0
        duplicate_overlay_plots: list[Any] = []
        for group_index, (target_layer, _x_title, _y_title, group_pairs) in enumerate(layer_groups):
            add_plot = getattr(target_layer, "add_plot", None)
            if not callable(add_plot):
                continue
            for x_index, y_index in group_pairs:
                plot_obj = None
                try:
                    plot_obj = add_plot(sheet, coly=y_index, colx=x_index, type='y')
                except TypeError:
                    try:
                        plot_obj = add_plot(sheet, coly=y_index, colx=x_index)
                    except Exception:
                        continue
                except Exception:
                    continue
                if plot_obj is None:
                    continue
                plotted_any = True
                label = self._origin_plot_label(worksheet, y_index)
                normalized = label.strip().casefold()
                duplicate_label = bool(group_index > 0 and normalized and normalized in seen_plot_labels)
                if duplicate_label:
                    label = ""
                elif normalized:
                    seen_plot_labels.add(normalized)
                if group_index == 0 and normalized:
                    primary_group_labels.add(normalized)
                self._apply_origin_plot_label(plot_obj, worksheet, y_index, override_label=label)
                color_key = normalized or f"group{group_index}:{x_index}:{y_index}"
                color = color_by_label.get(color_key)
                if color is None:
                    color = ORIGIN_EXPORT_COLOR_CYCLE[
                        next_color_index % len(ORIGIN_EXPORT_COLOR_CYCLE)
                    ]
                    color_by_label[color_key] = color
                    next_color_index += 1
                self._apply_origin_plot_style(plot_obj, color=color)
                if (
                    group_index > 0
                    and normalized
                    and normalized in primary_group_labels
                ):
                    duplicate_overlay_plots.append(plot_obj)
        if not plotted_any:
            return False

        x_title = layer_groups[0][1] if layer_groups else "X"
        y_title = layer_groups[0][2] if layer_groups else "Y"
        graph_title = str(workbook.name or worksheet.name or "Plot").strip() or "Plot"
        if worksheet.name and worksheet.name != "Plot data":
            graph_title = f"{graph_title} - {worksheet.name}"
        self._append_log(
            f"Origin graph template used for '{graph_title}': {graph_template_used}"
        )

        self._set_origin_graph_title(origin_any, graph, layer, graph_title)
        self._set_origin_axis_title(layer, "x", x_title)
        self._set_origin_axis_title(layer, "y", y_title)
        # Explicitly clear hidden-side titles on primary layer.
        self._set_origin_axis_title(layer, "x2", "")
        self._set_origin_axis_title(layer, "y2", "")
        self._configure_origin_layer_axes(layer, secondary_axes_only=False)

        for group_index, (target_layer, group_x_title, group_y_title, _group_pairs) in enumerate(
            layer_groups[1:],
            start=1,
        ):
            if target_layer is layer:
                if group_index == 1:
                    self._set_origin_axis_title(layer, "x2", group_x_title)
                    self._set_origin_axis_title(layer, "y2", group_y_title)
                    self._configure_origin_layer_axes(layer, secondary_axes_only=False)
                continue
            # Explicitly clear hidden-side titles on secondary layer.
            self._set_origin_axis_title(target_layer, "x", "")
            self._set_origin_axis_title(target_layer, "y", "")
            self._set_origin_axis_title(target_layer, "x2", group_x_title)
            self._set_origin_axis_title(target_layer, "y2", group_y_title)
            self._configure_origin_layer_axes(target_layer, secondary_axes_only=True)

        for target_layer, _group_x_title, _group_y_title, _group_pairs in layer_groups:
            set_int = getattr(target_layer, "set_int", None)
            if callable(set_int):
                try:
                    set_int("antialias", 1)
                except Exception:
                    pass
            # Do not call `layer -aa 1` here: on Origin 2026 this command can
            # implicitly switch axis visibility/labels to both sides (showAxes=3),
            # which causes duplicate/interleaved tick labels on dual-axis graphs.

        # Avoid LabTalk legend reconstruction here: some Origin builds emit
        # LEGEND.SMARTPOS errors for legend commands despite plotting correctly.

        # Explicitly rescale layers after all curves are added so primary and
        # secondary axes use proper data ranges on export.
        for target_layer, _group_x_title, _group_y_title, _group_pairs in layer_groups:
            try:
                target_layer.rescale()
            except Exception:
                lt_exec = getattr(target_layer, "lt_exec", None)
                if callable(lt_exec):
                    self._origin_lt_exec(lt_exec, "rescale;")
        for plot_obj in duplicate_overlay_plots:
            self._set_origin_plot_visible(plot_obj, False)
        self._set_origin_layer_top_margin(layer)
        # Re-apply axis titles after rescale/hide passes to keep axis captions
        # deterministic across Origin templates and layer refreshes.
        self._set_origin_axis_title(layer, "x", x_title)
        self._set_origin_axis_title(layer, "y", y_title)
        self._set_origin_axis_title(layer, "x2", "")
        self._set_origin_axis_title(layer, "y2", "")
        self._configure_origin_layer_axes(layer, secondary_axes_only=False)
        for group_index, (target_layer, group_x_title, group_y_title, _group_pairs) in enumerate(
            layer_groups[1:],
            start=1,
        ):
            if target_layer is layer:
                if group_index == 1:
                    self._set_origin_axis_title(layer, "x2", group_x_title)
                    self._set_origin_axis_title(layer, "y2", group_y_title)
                    self._configure_origin_layer_axes(layer, secondary_axes_only=False)
                continue
            self._set_origin_axis_title(target_layer, "x", "")
            self._set_origin_axis_title(target_layer, "y", "")
            self._set_origin_axis_title(target_layer, "x2", group_x_title)
            self._set_origin_axis_title(target_layer, "y2", group_y_title)
            self._configure_origin_layer_axes(target_layer, secondary_axes_only=True)
        # Re-assert title after rescale/legend updates so template-side smart
        # positioning cannot leave it in data coordinates.
        self._set_origin_graph_title(origin_any, graph, layer, graph_title)
        if len(layer_groups) > 1:
            self._append_log(
                f"Origin dual-axis layer snapshot ({graph_title}) primary: "
                f"{self._origin_layer_axis_snapshot(layer)}"
            )
            for target_layer, _group_x_title, _group_y_title, _group_pairs in layer_groups[1:]:
                self._append_log(
                    f"Origin dual-axis layer snapshot ({graph_title}) secondary: "
                    f"{self._origin_layer_axis_snapshot(target_layer)}"
                )
        if callable(graph_callback):
            try:
                graph_callback(graph, workbook, worksheet)
            except Exception as exc:
                self._append_log(
                    f"Origin graph callback failed for {graph_title}: {exc}",
                    level="error",
                )
        return True

    def _origin_axis_roles(self, frame: pd.DataFrame) -> str:
        if frame is None or frame.empty:
            return ""
        roles: list[str] = []
        x_assigned = False
        for column in frame.columns:
            series = frame[column]
            numeric = False
            try:
                numeric = is_numeric_dtype(series)
            except Exception:
                numeric = False
            if not numeric:
                roles.append("N")
                continue
            if not x_assigned:
                roles.append("X")
                x_assigned = True
            else:
                roles.append("Y")
        if not roles:
            return ""
        if not x_assigned:
            for index, role in enumerate(roles):
                if role == "Y":
                    roles[index] = "X"
                    x_assigned = True
                    break
        if not x_assigned:
            return ""
        return "".join(roles)

    def _origin_unique_name(
        self,
        existing: set[str],
        text: str,
        *,
        fallback: str,
        limit: int,
    ) -> str:
        base = self._origin_safe_token(text, fallback=fallback)
        base = base[:limit] or fallback
        candidate = base
        counter = 2
        while candidate in existing:
            suffix = f"_{counter}"
            candidate = (base[: max(1, limit - len(suffix))] + suffix).strip()
            if not candidate:
                candidate = f"{fallback}_{counter}"
            counter += 1
        existing.add(candidate)
        return candidate

    @staticmethod
    def _origin_safe_token(text: str, *, fallback: str) -> str:
        cleaned = "".join(
            ch for ch in str(text) if ch.isalnum() or ch in {"_", "-", " ", "(", ")"}
        ).strip()
        return cleaned or fallback

    @staticmethod
    def _escape_origin_text(text: str) -> str:
        try:
            return shared_escape_origin_text(str(text))
        except Exception:
            return str(text).replace("\"", "''")

    def _sync_shared_action_states(self) -> None:
        self._prune_shared_plot_workbooks()
        has_worksheets = any(
            self._worksheets.get(key) is not None
            for workbook in self._workbooks.values()
            for key in workbook.worksheets
        )
        export_txt_button = getattr(self, "export_button", None)
        if isinstance(export_txt_button, QtGui.QAction):
            plugin = getattr(self, "_current_plugin", None)
            uses_shared_export = (
                isinstance(plugin, PyPlotPlugin)
                and type(plugin).export_txt is PyPlotPlugin.export_txt
            )
            if uses_shared_export:
                export_txt_button.setEnabled(self._tab_has_exportable_series())
        export_button = getattr(self, "export_origin_button", None)
        if isinstance(export_button, QtGui.QAction):
            export_button.setEnabled(has_worksheets)
        outlier_button = getattr(self, "check_outliers_button", None)
        if isinstance(outlier_button, QtGui.QAction):
            outlier_button.setEnabled(has_worksheets)
        open_button = getattr(self, "open_origin_button", None)
        if isinstance(open_button, QtGui.QAction):
            plugin = getattr(self, "_current_plugin", None)
            uses_shared_open = (
                isinstance(plugin, PyPlotPlugin)
                and type(plugin).open_origin is PyPlotPlugin.open_origin
            )
            if uses_shared_open:
                plugin_name = getattr(self, "_current_plotter_name", None)
                token = plugin_name if isinstance(plugin_name, str) and plugin_name.strip() else None
                open_button.setEnabled(bool(self._shared_plot_workbooks_for_plugin(token)))

    @staticmethod
    def _detect_outlier_rows(
        frame: pd.DataFrame,
        *,
        iqr_factor: float = OUTLIER_IQR_FACTOR,
        min_points: int = OUTLIER_MIN_POINTS,
        zscore_threshold: float = OUTLIER_ZSCORE_THRESHOLD,
    ) -> tuple[pd.Series, Dict[str, int]]:
        """Return a row mask and per-column hit counts for outliers in ``frame``."""

        if frame.empty:
            return pd.Series(False, index=frame.index, dtype=bool), {}

        combined_mask = pd.Series(False, index=frame.index, dtype=bool)
        column_hits: Dict[str, int] = {}
        for column in frame.columns:
            series = frame[column]
            if is_bool_dtype(series):
                continue
            if not is_numeric_dtype(series):
                continue
            values = pd.to_numeric(series, errors="coerce")
            valid = values.dropna()
            if len(valid) < max(2, int(min_points)):
                continue

            q1 = float(valid.quantile(0.25))
            q3 = float(valid.quantile(0.75))
            iqr = q3 - q1
            col_mask = pd.Series(False, index=frame.index, dtype=bool)
            if np.isfinite(iqr) and iqr > 0:
                lower = q1 - float(iqr_factor) * iqr
                upper = q3 + float(iqr_factor) * iqr
                col_mask = values.lt(lower) | values.gt(upper)
            else:
                mean = float(valid.mean())
                std = float(valid.std(ddof=0))
                if np.isfinite(std) and std > 0:
                    z = (values - mean).abs() / std
                    col_mask = z > float(zscore_threshold)
                else:
                    continue
            col_mask = col_mask.fillna(False).astype(bool)
            count = int(col_mask.sum())
            if count <= 0:
                continue
            column_hits[str(column)] = count
            combined_mask = combined_mask | col_mask
        return combined_mask.astype(bool), column_hits

    def _collect_outlier_findings(self) -> List[WorksheetOutlierFinding]:
        findings: List[WorksheetOutlierFinding] = []
        for key, worksheet in self._worksheets.items():
            frame = worksheet.dataframe
            if frame is None or frame.empty:
                continue
            row_mask, column_hits = self._detect_outlier_rows(frame)
            if not column_hits:
                continue
            row_indices = np.flatnonzero(row_mask.to_numpy(dtype=bool)).astype(int).tolist()
            if not row_indices:
                continue
            workbook = self._workbooks.get(worksheet.workbook_key)
            workbook_name = workbook.name if workbook is not None else "Workbook"
            findings.append(
                WorksheetOutlierFinding(
                    worksheet_key=key,
                    worksheet_name=worksheet.name,
                    workbook_name=workbook_name,
                    total_rows=len(frame.index),
                    row_indices=row_indices,
                    row_mask=row_mask,
                    column_hits=column_hits,
                )
            )
        findings.sort(key=lambda item: (item.workbook_name.lower(), item.worksheet_name.lower()))
        return findings

    @staticmethod
    def _format_outlier_findings(findings: Sequence[WorksheetOutlierFinding]) -> str:
        lines: List[str] = []
        for finding in findings:
            lines.append(
                f"{finding.workbook_name} / {finding.worksheet_name}: "
                f"{finding.outlier_count}/{finding.total_rows} row(s)"
            )
            preview_rows = finding.row_indices[:12]
            if preview_rows:
                row_text = ", ".join(str(row + 1) for row in preview_rows)
                if finding.outlier_count > len(preview_rows):
                    row_text += ", ..."
                lines.append(f"Rows: {row_text}")
            if finding.column_hits:
                column_parts = [
                    f"{name} ({count})"
                    for name, count in sorted(
                        finding.column_hits.items(), key=lambda entry: (-entry[1], entry[0])
                    )
                ]
                lines.append("Columns: " + ", ".join(column_parts))
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _outlier_preview_frame(
        frame: pd.DataFrame,
        finding: WorksheetOutlierFinding,
    ) -> pd.DataFrame:
        """Return flagged rows with an explicit 1-based row-number column."""

        if frame.empty:
            return pd.DataFrame()
        mask = finding.row_mask.reindex(frame.index, fill_value=False).astype(bool)
        preview = frame.loc[mask].copy()
        if preview.empty:
            return preview
        row_numbers = [int(index) + 1 for index in preview.index.tolist()]
        preview.insert(0, "Row", row_numbers)
        preview.reset_index(drop=True, inplace=True)
        return preview

    def _build_outlier_preview_tab(
        self,
        finding: WorksheetOutlierFinding,
    ) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        worksheet = self._worksheets.get(finding.worksheet_key)
        frame = worksheet.dataframe if worksheet is not None else pd.DataFrame()
        preview = self._outlier_preview_frame(frame, finding)
        if preview.empty:
            layout.addWidget(QtWidgets.QLabel("No preview rows are available for this worksheet."))
            return container

        table = QtWidgets.QTableWidget(preview.shape[0], preview.shape[1], container)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setHorizontalHeaderLabels([str(column) for column in preview.columns.tolist()])
        outlier_columns = set(finding.column_hits.keys())
        highlight_bg = QtGui.QColor(254, 226, 226)
        highlight_fg = QtGui.QColor(153, 27, 27)

        for row_index in range(preview.shape[0]):
            for col_index, col_name in enumerate(preview.columns):
                value = preview.iat[row_index, col_index]
                display = "" if pd.isna(value) else str(value)
                item = QtWidgets.QTableWidgetItem(display)
                if str(col_name) in outlier_columns:
                    item.setBackground(highlight_bg)
                    item.setForeground(highlight_fg)
                    item.setToolTip("Detected as an outlier in this column.")
                table.setItem(row_index, col_index, item)

        header = table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
        table.resizeColumnsToContents()
        layout.addWidget(table, 1)
        return container

    def _apply_outlier_findings(self, findings: Sequence[WorksheetOutlierFinding]) -> int:
        removed_rows = 0
        changed = 0
        for finding in findings:
            worksheet = self._worksheets.get(finding.worksheet_key)
            if worksheet is None:
                continue
            frame = worksheet.dataframe
            if frame is None or frame.empty:
                continue
            mask = finding.row_mask.reindex(frame.index, fill_value=False).astype(bool)
            count = int(mask.sum())
            if count <= 0:
                continue
            filtered = frame.loc[~mask].reset_index(drop=True)
            worksheet.dataframe = filtered
            model = self._worksheet_models.get(finding.worksheet_key)
            if model is not None:
                model.beginResetModel()
                model._frame = filtered
                model._worksheet.dataframe = filtered
                model._columns = [str(column) for column in filtered.columns]
                model.endResetModel()
            removed_rows += count
            changed += 1

        if changed:
            self._refresh_imported_data_summary()
            self._sync_shared_action_states()
            self._update_worksheet_actions()
            self._mark_project_dirty()
        return removed_rows

    def _show_check_outliers_dialog(self) -> None:
        worksheet_count = len(self._worksheets)
        if worksheet_count <= 0:
            QtWidgets.QMessageBox.information(
                self,
                "Check outliers",
                "No worksheets are available. Import data first.",
            )
            return

        findings = self._collect_outlier_findings()
        if not findings:
            QtWidgets.QMessageBox.information(
                self,
                "Check outliers",
                f"No outliers detected across {worksheet_count} worksheet(s).",
            )
            self._append_log(f"Outlier check: no outliers found in {worksheet_count} worksheet(s).")
            return

        total_rows = sum(finding.outlier_count for finding in findings)
        detail_text = self._format_outlier_findings(findings)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Check outliers")
        dialog.resize(980, 600)
        dialog_layout = QtWidgets.QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(10, 10, 10, 10)
        dialog_layout.setSpacing(8)

        summary = QtWidgets.QLabel(
            f"Detected {total_rows} potential outlier row(s) in "
            f"{len(findings)} of {worksheet_count} worksheet(s)."
        )
        summary.setWordWrap(True)
        dialog_layout.addWidget(summary)

        hint = QtWidgets.QLabel(
            "Preview below shows rows that would be removed. "
            "Columns highlighted in red triggered the outlier detection."
        )
        hint.setWordWrap(True)
        dialog_layout.addWidget(hint)

        tabs = QtWidgets.QTabWidget(dialog)
        for finding in findings:
            title = f"{finding.workbook_name} / {finding.worksheet_name}"
            tabs.addTab(self._build_outlier_preview_tab(finding), title)
        dialog_layout.addWidget(tabs, 1)

        if detail_text:
            detail_box = QtWidgets.QPlainTextEdit(dialog)
            detail_box.setReadOnly(True)
            detail_box.setPlainText(detail_text)
            detail_box.setMaximumHeight(120)
            dialog_layout.addWidget(detail_box)

        buttons = QtWidgets.QDialogButtonBox(dialog)
        remove_button = buttons.addButton(
            "Remove flagged rows",
            QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole,
        )
        keep_button = buttons.addButton(
            "Keep data",
            QtWidgets.QDialogButtonBox.ButtonRole.RejectRole,
        )
        remove_button.setDefault(False)
        keep_button.setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)

        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            self._append_log(
                f"Outlier check complete: {total_rows} row(s) flagged across {len(findings)} worksheet(s)."
            )
            return

        removed = self._apply_outlier_findings(findings)
        self._append_log(
            f"Outlier cleanup removed {removed} row(s) across {len(findings)} worksheet(s)."
        )

    def _show_check_outliers_placeholder(self) -> None:
        """Backward-compatible alias for older action wiring."""

        self._show_check_outliers_dialog()

    def _populate_graph_settings(self, layout: QtWidgets.QVBoxLayout) -> None:
        raise NotImplementedError

    def _update_worksheet_item_state(self, key: Hashable) -> None:
        """Refresh the visual state of a worksheet entry."""

        _ = key

    def _set_script_panel(self, widget: QtWidgets.QWidget | None) -> None:
        """Display ``widget`` inside the shared script panel placeholder."""

        if self._script_panel_layout is None or self._script_panel_container is None:
            return
        layout = self._script_panel_layout
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            child = item.widget()
            if child is not None:
                child.setParent(None)
            del item
        if widget is None:
            self._script_panel_container.setVisible(False)
            return
        layout.addWidget(widget)
        self._script_panel_container.setVisible(True)

    def _set_plot_button_label(self, plugin: "PyPlotPlugin" | None) -> None:
        """Ensure the shared Plot action reflects the active plugin."""

        button = getattr(self, "plot_button", None)
        setter: Callable[[str], None] | None = None
        if isinstance(button, QtGui.QAction):
            setter = button.setText
        elif isinstance(button, QtWidgets.QAbstractButton):
            setter = button.setText
        else:
            return
        if plugin is None:
            setter("Plot graphs")
            return
        label_getter = getattr(plugin, "plot_action_label", None)
        label: str | None = None
        if callable(label_getter):
            try:
                candidate = label_getter()
            except Exception:
                candidate = None
            if isinstance(candidate, str):
                label = candidate.strip()
        if not label:
            name = getattr(plugin, "name", "")
            label = f"Plot {name}".strip() if name else "Plot graphs"
        setter(label)

    def _set_data_sources_visible(self, visible: bool) -> None:
        if self._data_sources_widget is None:
            return
        self._data_sources_widget.setVisible(visible)

    @contextmanager
    def _suspend_project_tree_updates(self) -> Iterator[QtWidgets.QTreeWidget | None]:
        tree = getattr(self, "project_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            yield None
            return
        depth = getattr(self, "_project_tree_update_depth", 0)
        if depth == 0:
            self._project_tree_updates_prev = tree.updatesEnabled()
            tree.setUpdatesEnabled(False)
        self._project_tree_update_depth = depth + 1
        try:
            yield tree
        finally:
            depth = getattr(self, "_project_tree_update_depth", 1) - 1
            self._project_tree_update_depth = depth
            if depth == 0:
                tree.setUpdatesEnabled(self._project_tree_updates_prev)
                tree.viewport().update()

    # ------------------------------------------------------------------ base UI
    def _build_base_ui(self) -> None:
        central = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central)
        central_layout.setContentsMargins(6, 6, 6, 4)
        central_layout.setSpacing(6)

        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("Select files or folders...")
        self.path_edit.editingFinished.connect(self._handle_manual_path_entry)
        self.path_edit.hide()
        self.browse_files_button = None
        self.browse_folder_button = None
        self._data_sources_widget = None

        self._script_panel_container = QtWidgets.QFrame()
        self._script_panel_container.setObjectName("mw_script_panel_container")
        self._script_panel_container.setVisible(False)
        self._script_panel_layout = QtWidgets.QVBoxLayout(self._script_panel_container)
        self._script_panel_layout.setContentsMargins(0, 0, 0, 0)
        self._script_panel_layout.setSpacing(8)
        central_layout.addWidget(self._script_panel_container)

        self.tab_widget = _MdiTabProxy()
        self.tab_widget.currentChanged.connect(self._handle_current_tab_changed)
        central_layout.addWidget(self.tab_widget, 1)

        self.setCentralWidget(central)
        # Shared cursor readout in the status bar.
        try:
            status = self.statusBar()
        except Exception:
            status = None
        if status is not None:
            label = QtWidgets.QLabel("x: --   y: --", self)
            label.setObjectName("mw_cursor_status")
            label.setMinimumWidth(180)
            label.setMinimumHeight(26)
            label.setContentsMargins(6, 2, 6, 2)
            label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Preferred,
            )
            status.addPermanentWidget(label, 1)
            self._cursor_label = label

            try:
                status.setMinimumHeight(30)
                status.setSizeGripEnabled(True)
            except Exception:
                pass
            self._refresh_status_bar_layout()
        progress_dialog = QtWidgets.QDialog(self, QtCore.Qt.WindowType.Tool)
        progress_dialog.setWindowTitle("Working...")
        progress_dialog.setModal(False)
        progress_dialog.setWindowFlag(QtCore.Qt.WindowType.WindowContextHelpButtonHint, False)
        progress_dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        progress_dialog.setMinimumWidth(360)
        progress_layout = QtWidgets.QVBoxLayout(progress_dialog)
        progress_layout.setContentsMargins(12, 12, 12, 12)
        progress_layout.setSpacing(8)
        progress_label = QtWidgets.QLabel("", progress_dialog)
        progress_label.setObjectName("mw_task_progress_label")
        progress_label.setWordWrap(True)
        progress_layout.addWidget(progress_label)
        progress_bar = QtWidgets.QProgressBar(progress_dialog)
        progress_bar.setObjectName("mw_task_progress_bar")
        progress_bar.setTextVisible(True)
        progress_layout.addWidget(progress_bar)
        progress_dialog.hide()
        self._task_progress_dialog = progress_dialog
        self._task_progress_label = progress_label
        self._task_progress_bar = progress_bar

        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setHeaderLabels(["Project Explorer", "Details"])
        project_header = self.project_tree.header()
        project_header.setStretchLastSection(False)
        project_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Interactive)
        project_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.project_tree.setColumnWidth(0, 240)
        self.project_tree.setColumnWidth(1, 320)
        self.project_tree.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        self.project_tree.setUniformRowHeights(True)
        self.project_tree.setAnimated(False)
        self.project_tree.setAlternatingRowColors(True)
        self.project_tree.setWordWrap(False)
        self.project_tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_tree.customContextMenuRequested.connect(self._handle_project_context_menu)
        self.project_tree.itemDoubleClicked.connect(self._dispatch_project_item_activation)
        self.project_tree.itemActivated.connect(self._dispatch_project_item_activation)
        self.project_tree.currentItemChanged.connect(self._handle_project_tree_current_item_changed)
        model = self.project_tree.model()
        if model is not None:
            model.rowsInserted.connect(self._schedule_project_tree_filter_refresh)
            model.rowsRemoved.connect(self._schedule_project_tree_filter_refresh)
            model.modelReset.connect(self._schedule_project_tree_filter_refresh)
            model.layoutChanged.connect(self._schedule_project_tree_filter_refresh)
            model.dataChanged.connect(self._schedule_project_tree_filter_refresh)
        self.project_tree_search = QtWidgets.QLineEdit()
        self.project_tree_search.setObjectName("project_tree_search")
        self.project_tree_search.setPlaceholderText("Search Project Explorer...")
        self.project_tree_search.setClearButtonEnabled(True)
        self.project_tree_search.textChanged.connect(self._handle_project_tree_search_text_changed)
        project_container = QtWidgets.QWidget()
        project_layout = QtWidgets.QVBoxLayout(project_container)
        project_layout.setContentsMargins(4, 4, 4, 4)
        project_layout.setSpacing(4)
        project_layout.addWidget(self.project_tree_search)
        project_layout.addWidget(self.project_tree, 1)
        project_dock = self._create_dock_widget("Project Explorer", "projectExplorerDock")
        project_dock.setWidget(project_container)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, project_dock)
        project_dock.setMinimumWidth(PRIMARY_DOCK_MIN_WIDTH)
        self.project_dock = project_dock

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_dock = self._create_dock_widget("Message Log", "messageLogDock")
        log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, log_dock)
        log_dock.hide()
        self.log_dock = log_dock
        log_dock.visibilityChanged.connect(
            lambda visible: self._clear_log_alert() if visible else None
        )
        self._log_view_watcher = _LogViewWatcher(self._clear_log_alert)
        self.log_view.installEventFilter(self._log_view_watcher)

        self.object_tree = QtWidgets.QTreeWidget()
        self.object_tree.setHeaderLabels(["Object Manager"])
        self.object_tree.setColumnCount(1)
        self.object_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.object_tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.object_tree.itemChanged.connect(self._dispatch_object_item_changed)
        selection_model = self.object_tree.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._handle_object_selection_changed)
        self.object_tree.itemDoubleClicked.connect(self._dispatch_object_item_activation)
        self.object_tree.itemActivated.connect(self._dispatch_object_item_activation)
        self.object_tree.customContextMenuRequested.connect(self._handle_object_context_menu)
        object_dock = self._create_dock_widget("Object Manager", "objectManagerDock")
        object_dock.setWidget(self.object_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, object_dock)
        object_dock.setMinimumWidth(PRIMARY_DOCK_MIN_WIDTH)
        self.object_dock = object_dock
        if sys.platform != "darwin":
            object_dock.visibilityChanged.connect(
                lambda _: QtCore.QTimer.singleShot(50, self._normalize_docks_initial)
            )

        graph_dock: QtWidgets.QDockWidget | None = None
        graph_panel: QtWidgets.QWidget | None = None
        self.graph_dock = None
        self.graph_panel = None

        self._setup_script_toolbar()
        self._setup_action_toolbar()
        self._setup_navigation_toolbar()
        self._setup_format_toolbar()
        self._setup_annotation_toolbar()
        QtCore.QTimer.singleShot(0, self._normalize_docks_initial)
        self._layout_fixed_once = False
        if isinstance(project_dock, QtWidgets.QDockWidget):
            if sys.platform != "darwin":
                project_dock.visibilityChanged.connect(
                    lambda _: QtCore.QTimer.singleShot(50, self._normalize_docks_initial)
                )

        self._dock_switcher_panels: list[QtWidgets.QDockWidget | None] = []
        dock_switcher_enabled = self._dock_switcher_supported()
        left_docks = tuple(
            dock for dock in (project_dock, log_dock, graph_dock) if dock is not None
        )
        if dock_switcher_enabled and left_docks:
            self._dock_switcher_panels.append(
                self._create_dock_switcher(
                    left_docks,
                    side="left",
                    enable_hover=False,
                    enable_overlay=False,
                    auto_collapse=False,
                    initial_visible=(0,),
                )
            )
        else:
            self._dock_switcher_panels.append(None)

        right_docks = tuple(dock for dock in (object_dock,) if dock is not None)
        if dock_switcher_enabled and right_docks:
            self._dock_switcher_panels.append(
                self._create_dock_switcher(
                    right_docks,
                    side="right",
                    enable_hover=False,
                    enable_overlay=False,
                    auto_collapse=False,
                    initial_visible=(0,),
                )
            )
        else:
            self._dock_switcher_panels.append(None)

        QtCore.QTimer.singleShot(0, self._apply_initial_dock_sizes)
        QtCore.QTimer.singleShot(0, self._restore_primary_dock_states)
        QtCore.QTimer.singleShot(0, self._ensure_primary_docks_pinned)

        for tracked in (project_dock, log_dock, graph_dock, object_dock):
            if tracked is None:
                continue
            try:
                tracked.dockLocationChanged.connect(
                    lambda area, dock=tracked: self._handle_primary_dock_location_change(dock, area)
                )
            except Exception:
                pass
            try:
                tracked.visibilityChanged.connect(
                    lambda _visible, dock=tracked: self._handle_primary_dock_visibility_changed(dock)
                )
            except Exception:
                pass

        menu_bar = install_standard_menu(
            self,
            help_topic=self.help_topic,
            console=self.log_view,
            open_file=self._open_files_from_menu,
            open_folder=self._open_folder_from_menu,
            close_window=self.close,
        )
        self._setup_project_menu(menu_bar)
        self._setup_data_menu(menu_bar)
        self._extend_menus(menu_bar)
        self._after_base_ui_created(
            project_dock=project_dock,
            log_dock=log_dock,
            graph_dock=graph_dock,
            graph_panel=graph_panel,
        )
        self._retabify_primary_docks()
    def _setup_project_menu(self, menu_bar: QtWidgets.QMenuBar) -> None:
        """Attach shared project actions (open/save) to the File menu."""

        file_menu: QtWidgets.QMenu | None = None
        for action in menu_bar.actions():
            menu = action.menu()
            if menu is not None and menu.objectName() == "mw_shared_file":
                file_menu = menu
                break
        if file_menu is None:
            return

        # Remove default "Open File/Folder" entries inherited from the shared menu
        removed_placeholder = False
        for action in list(file_menu.actions()):
            text = action.text()
            if not isinstance(text, str):
                continue
            simplified = text.replace("&", "").strip().lower()
            if simplified.startswith("open file") or simplified.startswith("open folder"):
                file_menu.removeAction(action)
                removed_placeholder = True
        if removed_placeholder:
            for action in list(file_menu.actions()):
                if action.isSeparator():
                    file_menu.removeAction(action)
                    break

        insert_before: QtGui.QAction | None = None
        for action in file_menu.actions():
            if action.isSeparator():
                insert_before = action
                break

        project_separator = None
        if insert_before is not None:
            project_separator = file_menu.insertSeparator(insert_before)

        new_menu = QtWidgets.QMenu("New", file_menu)
        new_window_action = new_menu.addAction("PyPlot Window")
        if hasattr(self, "_create_new_pyplot_window"):
            new_window_action.triggered.connect(self._create_new_pyplot_window)  # type: ignore[arg-type]
        else:
            new_window_action.setEnabled(False)
        workbook_action = new_menu.addAction("Workbook")
        workbook_action.triggered.connect(self._create_new_workbook)
        graph_action = new_menu.addAction("Blank Graph")
        create_graph = getattr(self, "_create_blank_graph", None)
        if callable(create_graph):
            graph_action.triggered.connect(create_graph)  # type: ignore[arg-type]
        else:
            graph_action.setEnabled(False)
        create_graph_action = new_menu.addAction("Create Graph...")
        create_graph_action.triggered.connect(self._open_create_graph_dialog)
        compose_graph_action = new_menu.addAction("Compose Graph...")
        compose_graph_action.triggered.connect(self._compose_graph_from_existing)
        if insert_before is not None:
            file_menu.insertMenu(insert_before, new_menu)
        else:
            file_menu.addMenu(new_menu)

        open_project_action = QtGui.QAction("Open...", self)
        try:
            open_project_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Open))
        except Exception:
            pass
        open_project_action.triggered.connect(self._open_project_dialog)
        self._open_project_action = open_project_action
        if insert_before is not None:
            file_menu.insertAction(project_separator or insert_before, open_project_action)
        else:
            file_menu.addAction(open_project_action)

        save_project_action = QtGui.QAction("Save Project", self)
        try:
            save_project_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Save))
        except Exception:
            pass
        save_project_action.triggered.connect(self._save_project)
        save_project_action.setEnabled(False)
        self._save_project_action = save_project_action
        if insert_before is not None:
            file_menu.insertAction(insert_before, save_project_action)
        else:
            file_menu.addAction(save_project_action)

        save_as_action = QtGui.QAction("Save Project As...", self)
        try:
            save_as_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.SaveAs))
        except Exception:
            pass
        save_as_action.triggered.connect(self._save_project_as)
        save_as_action.setEnabled(False)
        self._save_project_as_action = save_as_action
        if insert_before is not None:
            file_menu.insertAction(insert_before, save_as_action)
        else:
            file_menu.addAction(save_as_action)

        recent_menu = QtWidgets.QMenu("Recent Projects", file_menu)
        self._recent_projects_menu = recent_menu
        if insert_before is not None:
            file_menu.insertMenu(insert_before, recent_menu)
        else:
            file_menu.addMenu(recent_menu)
        self._update_recent_projects_menu()

        if project_separator is None and insert_before is not None:
            self._project_menu_separator = file_menu.insertSeparator(insert_before)
        else:
            self._project_menu_separator = project_separator

    def _setup_data_menu(self, menu_bar: QtWidgets.QMenuBar) -> None:
        """Create the shared Data menu with import helpers."""

        data_menu = QtWidgets.QMenu("&Data", menu_bar)
        data_menu.setObjectName("mw_shared_data")
        menu_bar.addMenu(data_menu)
        self._data_menu = data_menu

        import_files_action = data_menu.addAction("Import Files...")
        import_files_action.triggered.connect(self._dispatch_import_data_from_files)
        self._import_files_action = import_files_action

        import_folder_action = data_menu.addAction("Import Folders...")
        import_folder_action.triggered.connect(self._dispatch_import_data_from_folder)
        self._import_folder_action = import_folder_action

        connect_folder_action = data_menu.addAction("Connect Folders...")
        connect_folder_action.triggered.connect(self._connect_data_from_folder)

        refresh_connected_action = data_menu.addAction("Refresh Connected Folders")
        refresh_connected_action.triggered.connect(self._refresh_connected_folders)
        refresh_connected_action.setEnabled(False)
        self._refresh_connected_menu_action = refresh_connected_action

        data_menu.addSeparator()
        recent_action = data_menu.addAction("Refresh Imported Data")
        recent_action.triggered.connect(self._refresh_imported_data_summary)
        recent_action.setEnabled(False)
        self._refresh_import_action = recent_action

        data_menu.addSeparator()
        new_workbook_action = data_menu.addAction("New Workbook")
        new_workbook_action.triggered.connect(self._create_new_workbook)
        self._new_workbook_action = new_workbook_action

        add_before_action = data_menu.addAction("Add Column Before")
        add_before_action.triggered.connect(lambda: self._insert_column(position="before"))
        self._add_column_before_action = add_before_action

        add_after_action = data_menu.addAction("Add Column After")
        add_after_action.triggered.connect(lambda: self._insert_column(position="after"))
        self._add_column_after_action = add_after_action

        delete_column_action = data_menu.addAction("Delete Column")
        delete_column_action.triggered.connect(self._delete_selected_columns)
        self._delete_column_action = delete_column_action

        reorder_action = data_menu.addAction("Reorder Columns...")
        reorder_action.triggered.connect(self._reorder_columns)
        self._reorder_columns_action = reorder_action

        self._update_worksheet_actions()

    def _dispatch_import_data_from_files(self) -> None:
        handler = getattr(self, "_import_data_from_files", None)
        if callable(handler):
            handler()

    def _dispatch_import_data_from_folder(self) -> None:
        handler = getattr(self, "_import_data_from_folder", None)
        if callable(handler):
            handler()

    def _configure_toolbar(self, toolbar: QtWidgets.QToolBar) -> None:
        """Apply shared sizing and behaviour to top-level toolbars."""

        toolbar.setMovable(True)
        toolbar.setFloatable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setToolTip(toolbar.windowTitle())
        toolbar.setIconSize(self._toolbar_icon_size)
        toolbar.setProperty("mwPrimaryToolbar", True)

    def _apply_toolbar_style_hint(self) -> None:
        """Use the platform default toolbar styling with muted disabled buttons."""

        if sys.platform == "darwin":
            # Keep Cocoa-native disabled button styling on macOS.
            return
        rules = """
QToolBar[mwPrimaryToolbar="true"] QToolButton:disabled {
    border: 1px solid transparent;
    background: transparent;
    color: #6b7280;
}
"""
        current = self.styleSheet() or ""
        if rules.strip() in current:
            return
        self.setStyleSheet(f"{current}\n{rules}" if current else rules)

    def _style_toolbar_button(
        self,
        toolbar: QtWidgets.QToolBar,
        target: QtGui.QAction | QtWidgets.QAbstractButton,
        *,
        object_name: str | None = None,
    ) -> None:
        button: QtWidgets.QAbstractButton | None
        if isinstance(target, QtGui.QAction):
            if not isinstance(toolbar, QtWidgets.QToolBar):
                return
            button = toolbar.widgetForAction(target)
            if not isinstance(button, QtWidgets.QToolButton):
                toolbar.update()
                button = toolbar.widgetForAction(target)
        elif isinstance(target, QtWidgets.QAbstractButton):
            button = target
        else:
            button = None
        if not isinstance(button, QtWidgets.QAbstractButton):
            return
        if sys.platform == "darwin":
            button.setAutoRaise(True)
            button.setMinimumHeight(0)
        else:
            button.setAutoRaise(False)
            button.setMinimumHeight(self._toolbar_icon_size.height() + 6)
        button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        if object_name:
            button.setObjectName(object_name)

    def _update_history_actions(self) -> None:
        undo_enabled = self._history.can_undo()
        redo_enabled = self._history.can_redo()
        if isinstance(self._undo_action, QtGui.QAction):
            self._undo_action.setEnabled(undo_enabled)
        if isinstance(self._redo_action, QtGui.QAction):
            self._redo_action.setEnabled(redo_enabled)

    def _init_graph_settings_menu(self, toolbar: QtWidgets.QToolBar) -> None:
        """Embed the graph settings container inside the provided toolbar."""

        if self._graph_section_bar is not None:
            return

        toolbar.addSeparator()

        section_bar = QtWidgets.QWidget(toolbar)
        section_bar.setObjectName("mw_graph_section_bar")
        section_layout = QtWidgets.QHBoxLayout(section_bar)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(6)
        toolbar.addWidget(section_bar)
        self._graph_section_bar = section_bar
        self._graph_section_layout = section_layout

        menu = QtWidgets.QMenu(self)
        menu.setObjectName("mw_graph_settings_menu")
        menu.setToolTipsVisible(True)
        menu.setMinimumWidth(360)

        action = QtWidgets.QWidgetAction(menu)
        panel = QtWidgets.QWidget(menu)
        panel.setObjectName("mw_plugin_settings_panel")
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(8)

        scroll = QtWidgets.QScrollArea(panel)
        scroll.setObjectName("mw_graph_settings_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(320)
        panel_layout.addWidget(scroll, 1)
        self._graph_settings_scroll = scroll

        content = QtWidgets.QWidget(scroll)
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        scroll.setWidget(content)
        self._graph_settings_content = content

        self._plugin_settings_panel = panel
        self._plugin_settings_container = None
        self._plugin_settings_layout = None
        self._plugin_settings_placeholder = None

        try:
            self._populate_graph_settings(content_layout)
        except NotImplementedError:
            pass

        container = self._plugin_settings_container
        layout = self._plugin_settings_layout
        if container is None or layout is None:
            container = QtWidgets.QFrame(content)
            container.setObjectName("mw_plugin_settings_container")
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            content_layout.addWidget(container)
            self._plugin_settings_container = container
            self._plugin_settings_layout = layout
        else:
            container.setVisible(True)

        placeholder = QtWidgets.QLabel("Select a plugin to configure graph settings.", container)
        placeholder.setWordWrap(True)
        placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        placeholder.setObjectName("mw_graph_settings_placeholder")
        layout.addWidget(placeholder)
        layout.addStretch(1)
        self._plugin_settings_placeholder = placeholder

        content_layout.addStretch(1)

        action.setDefaultWidget(panel)
        menu.addAction(action)
        menu.aboutToShow.connect(self._handle_graph_menu_show)
        menu.aboutToHide.connect(self._handle_graph_menu_hide)

        self._graph_settings_menu = menu
        self._graph_settings_action = action

        self._refresh_graph_section_buttons(None)

    def _refresh_graph_section_buttons(self, widget: QtWidgets.QWidget | None) -> None:
        layout = self._graph_section_layout
        menu = self._graph_settings_menu
        if layout is None or menu is None:
            return

        self._graph_section_buttons = []
        self._graph_settings_pending_anchor = widget

        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

        sections = self._discover_settings_sections(widget)
        self._graph_settings_sections = sections
        self._synchronise_graph_section_states(sections)
        if not sections:
            title = "Settings"
            button = self._create_graph_section_button(title, None, menu)
            button.setEnabled(widget is not None)
            layout.addWidget(button)
            self._graph_section_buttons = [button]
            layout.addStretch(1)
            return

        buttons: list[QtWidgets.QToolButton] = []
        for title, anchor in sections:
            uses_popup_menu = self._graph_section_uses_popup_menu(title, anchor)
            if uses_popup_menu:
                if anchor is None:
                    section_menu = menu
                else:
                    section_menu = self._build_graph_section_menu(title, anchor)
            else:
                section_menu = None
            button = self._create_graph_section_button(title, anchor, section_menu)
            layout.addWidget(button)
            buttons.append(button)
        layout.addStretch(1)
        self._graph_section_buttons = buttons

    def _graph_section_uses_popup_menu(
        self,
        title: str,
        anchor: QtWidgets.QWidget | None,
    ) -> bool:
        pref_handler = getattr(self, "_graph_section_prefers_window", None)
        if callable(pref_handler):
            try:
                prefers_window = bool(pref_handler(title=title, anchor=anchor))
            except Exception:
                prefers_window = False
            if prefers_window:
                return False
        return True

    def _discover_settings_sections(
        self,
        widget: QtWidgets.QWidget | None,
    ) -> list[tuple[str, QtWidgets.QWidget | None]]:
        if widget is None:
            return []

        sections: list[tuple[str, QtWidgets.QWidget]] = []
        seen_ids: set[int] = set()

        toolbar_sections: list[tuple[int, str, QtWidgets.QWidget]] = []
        for child in widget.findChildren(
            QtWidgets.QWidget,
            options=QtCore.Qt.FindChildOption.FindChildrenRecursively,
        ):
            title_prop = child.property(TOOLBAR_SECTION_PROPERTY)
            if not isinstance(title_prop, str):
                continue
            title = title_prop.strip()
            if not title:
                continue
            anchor = child
            marker = id(anchor)
            if marker in seen_ids:
                continue
            seen_ids.add(marker)
            pos = anchor.mapTo(widget, QtCore.QPoint(0, 0)).y()
            toolbar_sections.append((pos, title, anchor))
        toolbar_sections.sort(key=lambda entry: entry[0])
        sections.extend((title, anchor) for _, title, anchor in toolbar_sections)

        group_boxes = widget.findChildren(
            QtWidgets.QGroupBox,
            options=QtCore.Qt.FindChildOption.FindChildrenRecursively,
        )
        unique_boxes: list[QtWidgets.QGroupBox] = []
        for box in group_boxes:
            parent_box = box.parent()
            if isinstance(parent_box, QtWidgets.QGroupBox):
                continue
            marker = id(box)
            if marker in seen_ids:
                continue
            unique_boxes.append(box)
            seen_ids.add(marker)
        if unique_boxes:
            sortable: list[tuple[int, str, QtWidgets.QWidget]] = []
            for idx, box in enumerate(unique_boxes):
                title = box.title().strip() or box.objectName().replace("_", " ").title() or f"Section {idx + 1}"
                pos = box.mapTo(widget, QtCore.QPoint(0, 0)).y()
                sortable.append((pos, title, box))
            sortable.sort(key=lambda entry: entry[0])
            sections.extend((title, anchor) for _, title, anchor in sortable)
        else:
            tabs = widget.findChildren(
                QtWidgets.QTabWidget,
                options=QtCore.Qt.FindChildOption.FindChildrenRecursively,
            )
            for tab_widget in tabs:
                for index in range(tab_widget.count()):
                    title = tab_widget.tabText(index).strip() or f"Tab {index + 1}"
                    anchor = tab_widget.widget(index)
                    if anchor is not None:
                        sections.append((title, anchor))
                if sections:
                    break

        if not sections:
            return [("Settings", widget)]

        deduped: list[tuple[str, QtWidgets.QWidget | None]] = []
        seen_titles: set[str] = set()
        for title, anchor in sections:
            key = title.strip().lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            deduped.append((title, anchor))
        return deduped

    def _create_graph_section_button(
        self,
        title: str,
        anchor: QtWidgets.QWidget | None,
        menu: QtWidgets.QMenu | None,
    ) -> QtWidgets.QToolButton:
        button = _GraphSectionButton(anchor, self._set_graph_settings_anchor, self)
        button.setObjectName(f"mw_graph_section_{title.lower().replace(' ', '_')}")
        button.setText(title)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        if isinstance(menu, QtWidgets.QMenu):
            button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
            button.setMenu(menu)
        else:
            button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.DelayedPopup)
        button.setIconSize(self._toolbar_icon_size)
        button.setMinimumHeight(self._toolbar_icon_size.height() + 6)
        button.pressed.connect(lambda anchor=anchor: self._set_graph_settings_anchor(anchor))
        return button

    def _build_graph_section_menu(
        self, title: str, anchor: QtWidgets.QWidget
    ) -> QtWidgets.QMenu:
        state = self._graph_section_states.get(anchor)
        if state is None:
            state = self._create_graph_section_state(anchor)
            self._graph_section_states[anchor] = state

        menu = QtWidgets.QMenu(self)
        menu.setObjectName(f"mw_graph_settings_menu_{title.lower().replace(' ', '_')}")
        menu.setToolTipsVisible(True)
        menu.setMinimumWidth(360)

        action = QtWidgets.QWidgetAction(menu)
        panel = QtWidgets.QWidget(menu)
        panel.setObjectName("mw_graph_section_panel")
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(8)

        scroll = QtWidgets.QScrollArea(panel)
        scroll.setObjectName("mw_graph_settings_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel_layout.addWidget(scroll, 1)

        content = QtWidgets.QWidget(scroll)
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        scroll.setWidget(content)

        holder = QtWidgets.QWidget(content)
        holder_layout = QtWidgets.QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(8)
        content_layout.addWidget(holder)
        content_layout.addStretch(1)

        state.menu_layout = holder_layout
        state.menu_panel = panel
        state.menu_scroll = scroll

        action.setDefaultWidget(panel)
        menu.addAction(action)
        menu.aboutToShow.connect(lambda anchor=anchor: self._prepare_graph_section_menu(anchor))
        menu.aboutToHide.connect(lambda anchor=anchor: self._teardown_graph_section_menu(anchor))
        return menu

    def _prepare_graph_section_menu(self, anchor: QtWidgets.QWidget | None) -> None:
        if anchor is None:
            return
        state = self._graph_section_states.get(anchor)
        if state is None:
            return
        layout = state.menu_layout
        if layout is None:
            return
        self._graph_settings_pending_anchor = anchor
        self._clear_layout(layout)
        self._detach_graph_section_state(state, layout)
        layout.addStretch(1)
        self._adjust_graph_section_panel(state)

    def _teardown_graph_section_menu(self, anchor: QtWidgets.QWidget | None) -> None:
        if anchor is None:
            return
        state = self._graph_section_states.get(anchor)
        if state is None:
            return
        layout = state.menu_layout
        if layout is not None:
            self._clear_layout(layout)
        self._reset_graph_section_panel(state)
        self._restore_graph_section_state(state)
        if self._graph_settings_pending_anchor is anchor:
            self._graph_settings_pending_anchor = None

    def _clear_layout(self, layout: QtWidgets.QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
            child_layout = item.layout()
            if child_layout is not None:
                child_layout.setParent(None)

    def _adjust_graph_section_panel(self, state: _GraphSectionState) -> None:
        panel = state.menu_panel
        scroll = state.menu_scroll
        layout = state.menu_layout
        if panel is None or scroll is None or layout is None:
            return

        holder = layout.parentWidget()
        if holder is not None:
            holder.adjustSize()
        panel.adjustSize()

        holder_height = holder.sizeHint().height() if holder is not None else 0
        panel_layout = panel.layout()
        margins = (
            panel_layout.contentsMargins()
            if isinstance(panel_layout, QtWidgets.QLayout)
            else QtCore.QMargins(0, 0, 0, 0)
        )
        frame_height = scroll.frameWidth() * 2
        extra_padding = 6
        spacing = panel_layout.spacing() if isinstance(panel_layout, QtWidgets.QLayout) else 0
        desired_height = (
            holder_height
            + margins.top()
            + margins.bottom()
            + frame_height
            + spacing
            + extra_padding
        )
        desired_height = max(desired_height, panel.sizeHint().height())
        desired_height = int(max(0, desired_height))

        desired_width = panel.sizeHint().width()
        if holder is not None:
            desired_width = max(desired_width, holder.sizeHint().width() + margins.left() + margins.right())
        panel.setMinimumWidth(desired_width)

        max_height = self._maximum_section_height(panel)
        if max_height is None or desired_height <= max_height:
            scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            panel.setMinimumHeight(desired_height)
            panel.setMaximumHeight(desired_height)
        else:
            scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            panel.setMinimumHeight(0)
            panel.setMaximumHeight(int(max_height))

    def _reset_graph_section_panel(self, state: _GraphSectionState) -> None:
        panel = state.menu_panel
        scroll = state.menu_scroll
        if panel is not None:
            panel.setMinimumHeight(0)
            panel.setMaximumHeight(16777215)
        if scroll is not None:
            scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _maximum_section_height(self, widget: QtWidgets.QWidget) -> int | None:
        screen = widget.screen()
        if screen is None:
            screens = QtWidgets.QApplication.screens()
            screen = screens[0] if screens else None
        if screen is None:
            return None
        available = screen.availableGeometry()
        margin = 120
        max_height = max(240, available.height() - margin)
        return max_height

    def _create_graph_section_state(self, anchor: QtWidgets.QWidget) -> _GraphSectionState:
        parent = anchor.parentWidget()
        layout: QtWidgets.QLayout | None = None
        if isinstance(parent, QtWidgets.QWidget):
            layout = parent.layout()
        return _GraphSectionState(
            anchor=anchor,
            parent=parent,
            layout=layout,
        )

    def _synchronise_graph_section_states(
        self, sections: list[tuple[str, QtWidgets.QWidget | None]]
    ) -> None:
        anchors = {anchor for _, anchor in sections if anchor is not None}
        # Restore any sections that are no longer part of the active widget.
        for anchor, state in list(self._graph_section_states.items()):
            if anchor not in anchors:
                self._teardown_graph_section_menu(anchor)
                self._graph_section_states.pop(anchor, None)
        for anchor in anchors:
            state = self._graph_section_states.get(anchor)
            if state is None:
                state = self._create_graph_section_state(anchor)
                self._graph_section_states[anchor] = state
            parent = anchor.parentWidget()
            layout: QtWidgets.QLayout | None = None
            if isinstance(parent, QtWidgets.QWidget):
                layout = parent.layout()
            state.parent = parent
            state.layout = layout

    def _detach_graph_section_state(
        self, state: _GraphSectionState, target_layout: QtWidgets.QVBoxLayout
    ) -> None:
        if state.detached:
            return
        anchor = state.anchor
        layout = state.layout
        layout_info: tuple[int, ...] = ()
        if isinstance(layout, QtWidgets.QGridLayout):
            index = layout.indexOf(anchor)
            if index >= 0:
                layout_info = layout.getItemPosition(index)
        elif isinstance(layout, QtWidgets.QFormLayout):
            row, role = layout.getWidgetPosition(anchor)
            if row >= 0:
                layout_info = (row, int(role))
        elif isinstance(layout, QtWidgets.QBoxLayout):
            index = layout.indexOf(anchor)
            layout_info = (index,)
        elif isinstance(layout, QtWidgets.QLayout):
            layout_info = (layout.indexOf(anchor),)
        if isinstance(layout, QtWidgets.QLayout):
            layout.removeWidget(anchor)
        anchor.setParent(target_layout.parentWidget())
        target_layout.addWidget(anchor)
        anchor.show()
        state.layout_info = layout_info
        state.detached = True

    def _restore_graph_section_state(self, state: _GraphSectionState) -> None:
        if not state.detached:
            return
        anchor = state.anchor
        parent = state.parent
        layout = state.layout
        layout_info = state.layout_info
        anchor.hide()
        if isinstance(parent, QtWidgets.QWidget):
            anchor.setParent(parent)
        if isinstance(layout, QtWidgets.QGridLayout) and len(layout_info) == 4:
            row, col, row_span, col_span = layout_info
            layout.addWidget(anchor, row, col, row_span, col_span)
        elif isinstance(layout, QtWidgets.QFormLayout) and len(layout_info) == 2:
            row, role = layout_info
            layout.setWidget(row, QtWidgets.QFormLayout.ItemRole(role), anchor)
        elif isinstance(layout, QtWidgets.QBoxLayout) and layout_info:
            index = layout_info[0]
            if index >= 0:
                layout.insertWidget(index, anchor)
            else:
                layout.addWidget(anchor)
        elif isinstance(layout, QtWidgets.QLayout):
            layout.addWidget(anchor)
        anchor.show()
        state.layout_info = tuple()
        state.detached = False

    def _reset_graph_section_states(self) -> None:
        if not self._graph_section_states:
            return
        for anchor in list(self._graph_section_states.keys()):
            self._teardown_graph_section_menu(anchor)
        self._graph_section_states.clear()

    def _set_graph_settings_anchor(self, anchor: QtWidgets.QWidget | None) -> None:
        container = self._plugin_settings_container
        if anchor is not None and isinstance(container, QtWidgets.QWidget):
            root = self._section_root_widget(anchor, container)
            anchor = root if root is not None else anchor
        self._graph_settings_pending_anchor = anchor

    def _handle_graph_menu_show(self) -> None:
        scroll = self._graph_settings_scroll
        if scroll is None:
            return
        content = self._graph_settings_content
        if content is None:
            scroll.ensureVisible(0, 0, 0, 0)
            return
        anchor = self._graph_settings_pending_anchor
        self._apply_graph_section_filter(anchor)
        if anchor is None or not anchor.isVisible():
            scroll.ensureVisible(0, 0, 0, 0)
            return
        top_left = anchor.mapTo(content, QtCore.QPoint(0, 0))
        margin = 12
        scroll.ensureVisible(top_left.x(), top_left.y(), margin, margin)

    def _handle_graph_menu_hide(self) -> None:
        self._restore_graph_section_filter()

    def _apply_graph_section_filter(self, anchor: QtWidgets.QWidget | None) -> None:
        self._restore_graph_section_filter()
        container = self._plugin_settings_container
        if container is None:
            return
        if anchor is None:
            return
        if anchor is container or anchor is self._active_settings_widget:
            return
        if isinstance(anchor, QtWidgets.QWidget) and not anchor.isVisible():
            anchor.setVisible(True)
        sections = list(self._graph_settings_sections)
        if not sections:
            return
        for _, section_widget in sections:
            if section_widget is None or section_widget is anchor:
                continue
            root = self._section_root_widget(section_widget, container)
            targets: tuple[QtWidgets.QWidget, ...]
            if root is not None and root is not section_widget:
                targets = (root, section_widget)
            else:
                targets = (section_widget,)
            for target in targets:
                if target is None or not isinstance(target, QtWidgets.QWidget):
                    continue
                visible = target.isVisible()
                if not visible:
                    continue
                target.setVisible(False)
                self._graph_settings_hidden_widgets.append((target, visible))
        tab_widget = self._find_tab_widget(anchor)
        if tab_widget is not None:
            tab_bar = tab_widget.tabBar()
            visibility_state: list[tuple[int, bool]] = []
            for index in range(tab_widget.count()):
                visible = True
                if tab_bar is not None:
                    try:
                        visible = tab_bar.isTabVisible(index)
                    except Exception:
                        visible = True
                visibility_state.append((index, visible))
                try:
                    tab_widget.setTabVisible(index, tab_widget.widget(index) is anchor)
                except Exception:
                    widget = tab_widget.widget(index)
                    if widget is not None and widget is not anchor:
                        widget.setVisible(False)
            previous_index = tab_widget.currentIndex()
            target_index = tab_widget.indexOf(anchor)
            if target_index >= 0 and previous_index != target_index:
                tab_widget.setCurrentIndex(target_index)
            self._graph_settings_hidden_tabs.append((tab_widget, visibility_state, previous_index))

    def _restore_graph_section_filter(self) -> None:
        if self._graph_settings_hidden_widgets:
            for widget, was_visible in self._graph_settings_hidden_widgets:
                if widget is not None:
                    widget.setVisible(was_visible)
        self._graph_settings_hidden_widgets.clear()

        if self._graph_settings_hidden_tabs:
            for tab_widget, visibility_state, previous_index in self._graph_settings_hidden_tabs:
                if tab_widget is None:
                    continue
                tab_bar = tab_widget.tabBar()
                for index, visible in visibility_state:
                    widget = tab_widget.widget(index)
                    if tab_bar is not None:
                        try:
                            tab_widget.setTabVisible(index, visible)
                        except Exception:
                            if widget is not None:
                                widget.setVisible(visible)
                    elif widget is not None:
                        widget.setVisible(visible)
                if 0 <= previous_index < tab_widget.count():
                    try:
                        tab_widget.setCurrentIndex(previous_index)
                    except Exception:
                        pass
            self._graph_settings_hidden_tabs.clear()

    def _section_root_widget(
        self,
        widget: QtWidgets.QWidget,
        container: QtWidgets.QWidget,
    ) -> QtWidgets.QWidget | None:
        if not isinstance(widget, QtWidgets.QWidget):
            return None

        current: QtWidgets.QWidget | None = widget
        previous: QtWidgets.QWidget | None = widget

        while current is not None and current is not container:
            parent = current.parent()
            if parent is None or not isinstance(parent, QtWidgets.QWidget):
                break
            if parent is container:
                return previous
            previous = current
            current = cast(QtWidgets.QWidget, parent)

        return previous

    def _find_tab_widget(
        self, widget: QtWidgets.QWidget
    ) -> QtWidgets.QTabWidget | None:
        parent = widget.parent()
        while parent is not None:
            if isinstance(parent, QtWidgets.QTabWidget):
                return parent
            parent = parent.parent()
        return None

    def _set_plugin_settings_widget(self, widget: QtWidgets.QWidget | None) -> None:
        layout = self._plugin_settings_layout
        container = self._plugin_settings_container
        placeholder = self._plugin_settings_placeholder
        if layout is None or container is None:
            return
        section_root = self._graph_settings_content

        self._restore_graph_section_filter()
        self._reset_graph_section_states()

        if self._active_settings_widget is not None:
            layout.removeWidget(self._active_settings_widget)
            self._active_settings_widget.setParent(None)
            self._active_settings_widget = None

        has_plugin = bool(getattr(self, "_current_plugin", None))
        if widget is None:
            message = (
                "This plugin does not expose additional graph settings."
                if has_plugin
                else "Select a plugin to configure graph settings."
            )
            if isinstance(placeholder, QtWidgets.QLabel):
                placeholder.setText(message)
                placeholder.setVisible(True)
            self._refresh_graph_section_buttons(
                section_root if isinstance(section_root, QtWidgets.QWidget) else None
            )
            return

        if widget.parent() is not container:
            widget.setParent(container)
        layout.insertWidget(0, widget)
        widget.show()
        self._active_settings_widget = widget
        if isinstance(placeholder, QtWidgets.QWidget):
            placeholder.setVisible(False)
        self._refresh_graph_section_buttons(
            section_root if isinstance(section_root, QtWidgets.QWidget) else widget
        )

    def _setup_script_toolbar(self) -> None:
        """Install the default toolbar for plugin-specific controls."""

        toolbar = QtWidgets.QToolBar("Plugin", self)
        toolbar.setObjectName("mw_plugin_toolbar")
        self._configure_toolbar(toolbar)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        self._script_toolbar = toolbar

        generate_action = toolbar.addAction("Plot graphs")
        generate_action.setEnabled(False)
        generate_action.triggered.connect(self._generate_plots)
        self.plot_button = generate_action
        self._style_toolbar_button(toolbar, generate_action, object_name="mw_plot_action")

        self._init_graph_settings_menu(toolbar)

    def _setup_action_toolbar(self) -> None:
        self.addToolBarBreak(QtCore.Qt.ToolBarArea.TopToolBarArea)
        toolbar = QtWidgets.QToolBar("Plot actions", self)
        toolbar.setObjectName("mw_action_toolbar")
        self._configure_toolbar(toolbar)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        self._action_toolbar = toolbar

        import_action = toolbar.addAction("Import data...")
        import_action.triggered.connect(self._prompt_import_data)
        self.import_data_button = import_action
        self._style_toolbar_button(toolbar, import_action)

        create_graph_action = toolbar.addAction("Create graph...")
        create_graph_action.triggered.connect(self._open_create_graph_dialog)
        self._style_toolbar_button(toolbar, create_graph_action)

        connect_action = toolbar.addAction("Connect folders")
        connect_action.triggered.connect(self._connect_data_from_folder)
        self._connect_folder_action = connect_action
        self._style_toolbar_button(toolbar, connect_action)

        refresh_connected_action = toolbar.addAction("Refresh connected")
        refresh_connected_action.setEnabled(False)
        refresh_connected_action.triggered.connect(self._refresh_connected_folders)
        self._refresh_connected_action = refresh_connected_action
        self._style_toolbar_button(toolbar, refresh_connected_action)

        save_action = toolbar.addAction("Save graph...")
        save_action.setEnabled(False)
        save_action.triggered.connect(self._save_current_graph)
        self.save_graph_button = save_action
        self._style_toolbar_button(toolbar, save_action)

        normalize_action = toolbar.addAction("Normalize Y")
        normalize_action.setEnabled(False)
        normalize_action.triggered.connect(self._normalize_current_graph)
        self.normalize_button = normalize_action
        self._style_toolbar_button(toolbar, normalize_action)

        export_action = toolbar.addAction("Export TXT...")
        export_action.setEnabled(False)
        export_action.triggered.connect(self._export_txt)
        self.export_button = export_action
        self._style_toolbar_button(toolbar, export_action)

        origin_action = toolbar.addAction("Open in Origin...")
        origin_action.setEnabled(False)
        origin_action.triggered.connect(self._open_origin_prompt)
        self.open_origin_button = origin_action
        self._style_toolbar_button(toolbar, origin_action)

        export_workbooks_action = toolbar.addAction("Export workbooks to Origin...")
        export_workbooks_action.setEnabled(False)
        export_workbooks_action.triggered.connect(self._export_workbooks_to_origin)
        self.export_origin_button = export_workbooks_action
        self._style_toolbar_button(toolbar, export_workbooks_action)

        check_outliers_action = toolbar.addAction("Check outliers...")
        check_outliers_action.setEnabled(False)
        check_outliers_action.triggered.connect(self._show_check_outliers_dialog)
        self.check_outliers_button = check_outliers_action
        self._style_toolbar_button(toolbar, check_outliers_action)

    def _setup_navigation_toolbar(self) -> None:
        toolbar = QtWidgets.QToolBar("Navigation", self)
        toolbar.setObjectName("mw_navigation_toolbar")
        self._configure_toolbar(toolbar)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        self._nav_toolbar = toolbar

        mode_group = QtGui.QActionGroup(toolbar)
        mode_group.setExclusive(True)

        zoom_action = toolbar.addAction("Zoom")
        zoom_action.setCheckable(True)
        zoom_action.setEnabled(False)
        zoom_action.setToolTip("Drag a rectangle to zoom the current graph.")
        zoom_action.triggered.connect(self._handle_zoom_triggered)
        mode_group.addAction(zoom_action)
        self._zoom_action = zoom_action
        self._style_toolbar_button(toolbar, zoom_action)

        pan_action = toolbar.addAction("Pan")
        pan_action.setCheckable(True)
        pan_action.setEnabled(False)
        pan_action.setToolTip("Drag to move around the current graph.")
        pan_action.triggered.connect(self._handle_pan_triggered)
        mode_group.addAction(pan_action)
        self._pan_action = pan_action
        self._style_toolbar_button(toolbar, pan_action)

        toolbar.addSeparator()

        rescale_action = toolbar.addAction("Rescale")
        rescale_action.setEnabled(False)
        rescale_action.setToolTip("Autoscale both axes for the current graph.")
        rescale_action.triggered.connect(self._handle_rescale_both)
        self._rescale_action = rescale_action
        self._style_toolbar_button(toolbar, rescale_action)

        rescale_x_action = toolbar.addAction("Rescale X")
        rescale_x_action.setEnabled(False)
        rescale_x_action.setToolTip("Autoscale the X axis for the current graph.")
        rescale_x_action.triggered.connect(self._handle_rescale_x)
        self._rescale_x_action = rescale_x_action
        self._style_toolbar_button(toolbar, rescale_x_action)

        rescale_y_action = toolbar.addAction("Rescale Y")
        rescale_y_action.setEnabled(False)
        rescale_y_action.setToolTip("Autoscale the Y axis for the current graph.")
        rescale_y_action.triggered.connect(self._handle_rescale_y)
        self._rescale_y_action = rescale_y_action
        self._style_toolbar_button(toolbar, rescale_y_action)

        rescale_all_action = toolbar.addAction("Rescale all...")
        rescale_all_action.setEnabled(False)
        rescale_all_action.setToolTip("Rescale multiple graphs at once.")
        rescale_all_action.triggered.connect(self._open_rescale_all_dialog)
        self._rescale_all_action = rescale_all_action
        self._style_toolbar_button(toolbar, rescale_all_action)

        toolbar.addSeparator()

        dark_mode_action = toolbar.addAction("Dark graphs")
        dark_mode_action.setCheckable(True)
        dark_mode_action.setToolTip("Toggle a dark theme for all graphs.")
        dark_mode_action.toggled.connect(self._handle_dark_mode_toggled)
        self._dark_mode_action = dark_mode_action
        dark_mode_action.setChecked(self._dark_mode_enabled)
        self._update_navigation_enabled()

    def _handle_zoom_triggered(self, checked: bool) -> None:
        self._set_navigation_mode("zoom" if checked else None)

    def _handle_pan_triggered(self, checked: bool) -> None:
        self._set_navigation_mode("pan" if checked else None)

    def _handle_rescale_both(self) -> None:
        self._rescale_current_axes("both")

    def _handle_rescale_x(self) -> None:
        self._rescale_current_axes("x")

    def _handle_rescale_y(self) -> None:
        self._rescale_current_axes("y")

    def _tab_widget_like(self) -> Any | None:
        tab_widget = getattr(self, "tab_widget", None)
        if tab_widget is None:
            return None
        required = ("currentWidget", "currentIndex", "count", "indexOf")
        for name in required:
            if not callable(getattr(tab_widget, name, None)):
                return None
        return tab_widget

    def _current_canvas(self) -> FigureCanvas | None:
        tab_widget = self._tab_widget_like()
        if tab_widget is None:
            return None
        tab = tab_widget.currentWidget()
        if tab is None:
            return None
        return self._canvas_by_tab.get(tab)

    def _current_axes(self) -> Any | None:
        tab_widget = self._tab_widget_like()
        if tab_widget is None:
            return None
        tab = tab_widget.currentWidget()
        if tab is None:
            return None
        return self._axes_by_tab.get(tab)

    def _set_navigation_mode(self, mode: Optional[str]) -> None:
        if mode not in {"zoom", "pan"}:
            mode = None
        if mode == self._nav_mode and self._nav_active_canvas is self._current_canvas():
            return
        self._deactivate_navigation_mode()
        if mode is None:
            self._sync_navigation_buttons(None)
            return
        canvas = self._current_canvas()
        if canvas is None:
            self._sync_navigation_buttons(None)
            return
        helper = self._ensure_navigation_helper(canvas)
        if helper is None:
            self._sync_navigation_buttons(None)
            return
        try:
            getattr(helper, mode)()
        except Exception:
            self._sync_navigation_buttons(None)
            return
        self._nav_mode = mode
        self._nav_active_canvas = canvas
        self._sync_navigation_buttons(mode)

    def _deactivate_navigation_mode(self) -> None:
        if self._nav_mode and self._nav_active_canvas is not None:
            helper = self._navigation_helpers.get(self._nav_active_canvas)
            if helper is not None:
                try:
                    getattr(helper, self._nav_mode)()
                except Exception:
                    pass
        self._nav_mode = None
        self._nav_active_canvas = None

    def _sync_navigation_buttons(self, active: Optional[str]) -> None:
        for action, name in ((self._zoom_action, "zoom"), (self._pan_action, "pan")):
            if action is None:
                continue
            action.blockSignals(True)
            action.setChecked(active == name)
            action.blockSignals(False)

    def _ensure_navigation_helper(self, canvas: FigureCanvas | None) -> NavigationToolbar2QT | None:
        if canvas is None:
            return None
        helper = self._navigation_helpers.get(canvas)
        if helper is None:
            try:
                helper = NavigationToolbar2QT(canvas, self)
            except Exception:
                return None
            helper.hide()
            self._navigation_helpers[canvas] = helper
        return helper

    def _update_navigation_enabled(self) -> None:
        has_axes = bool(self._axes_by_tab)
        current_axes_available = self._current_axes() is not None
        if not current_axes_available:
            self._set_navigation_mode(None)
        for action in (
            self._zoom_action,
            self._pan_action,
            self._rescale_action,
            self._rescale_x_action,
            self._rescale_y_action,
        ):
            if action is not None:
                action.setEnabled(current_axes_available)
        if self._rescale_all_action is not None:
            self._rescale_all_action.setEnabled(has_axes)
        if self._dark_mode_action is not None:
            self._dark_mode_action.setEnabled(True)

    def _begin_task_progress(
        self,
        title: str,
        *,
        maximum: int | None = None,
        value: int = 0,
    ) -> None:
        label = self._task_progress_label
        bar = self._task_progress_bar
        if label is None or bar is None:
            return
        try:
            label.setText(str(title or "").strip())
            if isinstance(maximum, int) and maximum > 0:
                bar.setRange(0, int(maximum))
                bar.setValue(max(0, min(int(value), int(maximum))))
                bar.setFormat("%v / %m")
            else:
                bar.setRange(0, 0)
                bar.setFormat("%p%")
            dialog = self._task_progress_dialog
            if isinstance(dialog, QtWidgets.QDialog):
                dialog.show()
                dialog.raise_()
        except Exception:
            return
        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _update_task_progress(
        self,
        *,
        value: int | None = None,
        maximum: int | None = None,
        title: str | None = None,
    ) -> None:
        label = self._task_progress_label
        bar = self._task_progress_bar
        if label is None or bar is None:
            return
        try:
            if title is not None:
                label.setText(str(title).strip())
            if isinstance(maximum, int) and maximum > 0:
                bar.setRange(0, int(maximum))
                bar.setFormat("%v / %m")
            if value is not None:
                if bar.maximum() > 0:
                    bar.setValue(max(0, min(int(value), int(bar.maximum()))))
                else:
                    bar.setValue(0)
            dialog = self._task_progress_dialog
            if isinstance(dialog, QtWidgets.QDialog):
                dialog.show()
        except Exception:
            return
        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _advance_task_progress(self, step: int = 1, *, title: str | None = None) -> None:
        bar = self._task_progress_bar
        if bar is None:
            return
        current = 0
        try:
            current = int(bar.value())
        except Exception:
            current = 0
        self._update_task_progress(value=current + int(step), title=title)

    def _end_task_progress(self) -> None:
        label = self._task_progress_label
        bar = self._task_progress_bar
        if label is None or bar is None:
            return
        try:
            label.clear()
            bar.setRange(0, 1)
            bar.setValue(0)
            dialog = self._task_progress_dialog
            if isinstance(dialog, QtWidgets.QDialog):
                dialog.hide()
        except Exception:
            return
        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _refresh_status_bar_layout(self) -> None:
        status = None
        try:
            status = self.statusBar()
        except Exception:
            status = None
        if not isinstance(status, QtWidgets.QStatusBar):
            return

        cursor_label = self._cursor_label
        progress_label = self._task_progress_label
        progress_bar = self._task_progress_bar
        if cursor_label is None:
            return

        total_width = max(int(status.width()), 0)
        if total_width <= 0:
            return

        has_progress = bool(
            (isinstance(progress_label, QtWidgets.QLabel) and progress_label.isVisible())
            or (isinstance(progress_bar, QtWidgets.QProgressBar) and progress_bar.isVisible())
        )

        cursor_target = max(120, min(320, int(total_width * (0.42 if has_progress else 0.55))))
        cursor_label.setMinimumWidth(cursor_target)

        if not has_progress:
            if isinstance(progress_label, QtWidgets.QLabel):
                progress_label.setMinimumWidth(140)
                progress_label.setMaximumWidth(280)
            if isinstance(progress_bar, QtWidgets.QProgressBar):
                progress_bar.setMinimumWidth(180)
                progress_bar.setMaximumWidth(320)
            return

        available = max(total_width - cursor_target - 28, 0)
        label_target = max(80, min(180, int(available * 0.35)))
        bar_target = max(100, min(280, available - label_target))
        if available <= 200:
            label_target = max(60, min(120, int(available * 0.30)))
            bar_target = max(90, available - label_target)

        if isinstance(progress_label, QtWidgets.QLabel):
            progress_label.setMinimumWidth(label_target)
            progress_label.setMaximumWidth(max(label_target, 220))
        if isinstance(progress_bar, QtWidgets.QProgressBar):
            progress_bar.setMinimumWidth(bar_target)
            progress_bar.setMaximumWidth(max(bar_target, 280))

    def _update_cursor_status(self, event: Any | None) -> None:
        label = self._cursor_label
        if label is None:
            return
        self._refresh_status_bar_layout()
        x_value: float | None = None
        y_value: float | None = None
        if event is not None and getattr(event, "inaxes", None) is not None:
            try:
                x_value = float(event.xdata)
                y_value = float(event.ydata)
            except Exception:
                x_value = None
                y_value = None

        available_px = 0
        try:
            available_px = max(int(label.width()) - 8, 0)
            if available_px <= 0:
                status = self.statusBar()
                if isinstance(status, QtWidgets.QStatusBar):
                    available_px = max(int(status.width() * 0.45), 0)
        except Exception:
            available_px = 0
        metrics = QtGui.QFontMetrics(label.font())
        label.setText(
            cursor_readout_text(
                x_value,
                y_value,
                available_px=available_px,
                metrics=metrics,
            )
        )

    def _rescale_current_axes(self, axis: str) -> None:
        axes = self._current_axes()
        if axes is None:
            QtWidgets.QMessageBox.information(
                self,
                "Rescale",
                "Select a graph before rescaling.",
            )
            return
        try:
            requested_x = axis in {"both", "x"}
            requested_y = axis in {"both", "y"}

            def _axis_data_bounds(target_axes: Any) -> tuple[float | None, float | None, float | None, float | None]:
                xmins: list[float] = []
                xmaxs: list[float] = []
                ymins: list[float] = []
                ymaxs: list[float] = []
                try:
                    lines = list(target_axes.get_lines())
                except Exception:
                    lines = []
                for line in lines:
                    try:
                        if not bool(line.get_visible()):
                            continue
                    except Exception:
                        pass
                    x_arr = self._coerce_numeric_array(line.get_xdata())
                    if x_arr.size:
                        xmins.append(float(np.min(x_arr)))
                        xmaxs.append(float(np.max(x_arr)))
                    y_arr = self._coerce_numeric_array(line.get_ydata())
                    if y_arr.size:
                        ymins.append(float(np.min(y_arr)))
                        ymaxs.append(float(np.max(y_arr)))
                xmin = min(xmins) if xmins else None
                xmax = max(xmaxs) if xmaxs else None
                ymin = min(ymins) if ymins else None
                ymax = max(ymaxs) if ymaxs else None
                return xmin, xmax, ymin, ymax

            def _padded_limits(lower: float, upper: float) -> tuple[float, float]:
                span = upper - lower
                if not np.isfinite(span) or span <= 0:
                    delta = max(abs(lower), 1.0) * 0.05
                    return lower - delta, upper + delta
                pad = span * 0.03
                return lower - pad, upper + pad

            prior_xlim = tuple(axes.get_xlim())
            prior_ylim = tuple(axes.get_ylim())
            axes.relim()
            scalex = requested_x
            scaley = requested_y
            try:
                if scalex:
                    axes.set_autoscalex_on(True)
                if scaley:
                    axes.set_autoscaley_on(True)
            except Exception:
                pass
            try:
                enable_axis = "both"
                if scalex and not scaley:
                    enable_axis = "x"
                elif scaley and not scalex:
                    enable_axis = "y"
                axes.autoscale(enable=True, axis=enable_axis, tight=False)
            except Exception:
                pass
            axes.autoscale_view(scalex=scalex, scaley=scaley)

            # Some plugin plots keep stale/degenerate limits even after relim+autoscale.
            # Fall back to data-driven bounds from visible lines.
            xmin, xmax, ymin, ymax = _axis_data_bounds(axes)
            if requested_x and xmin is not None and xmax is not None:
                x_left, x_right = _padded_limits(float(xmin), float(xmax))
                xscale = "linear"
                try:
                    xscale = str(axes.get_xscale() or "linear").lower()
                except Exception:
                    xscale = "linear"
                if xscale == "log":
                    if x_right > 0:
                        x_left = max(x_left, np.nextafter(0.0, 1.0))
                        if x_left < x_right:
                            axes.set_xlim(x_left, x_right)
                else:
                    if x_left < x_right:
                        axes.set_xlim(x_left, x_right)

            if requested_y and ymin is not None and ymax is not None:
                y_bottom, y_top = _padded_limits(float(ymin), float(ymax))
                yscale = "linear"
                try:
                    yscale = str(axes.get_yscale() or "linear").lower()
                except Exception:
                    yscale = "linear"
                if yscale == "log":
                    if y_top > 0:
                        y_bottom = max(y_bottom, np.nextafter(0.0, 1.0))
                        if y_bottom < y_top:
                            axes.set_ylim(y_bottom, y_top)
                else:
                    if y_bottom < y_top:
                        axes.set_ylim(y_bottom, y_top)

            # If nothing changed, attempt one more direct autoscale pass.
            try:
                current_xlim = tuple(axes.get_xlim())
                current_ylim = tuple(axes.get_ylim())
            except Exception:
                current_xlim = prior_xlim
                current_ylim = prior_ylim
            if current_xlim == prior_xlim and current_ylim == prior_ylim:
                try:
                    axes.autoscale(enable=True, axis="both", tight=False)
                    axes.autoscale_view(scalex=requested_x, scaley=requested_y)
                except Exception:
                    pass

            canvas = getattr(axes, "figure", None)
            if canvas is not None:
                canvas = getattr(canvas, "canvas", None)
            if canvas is not None:
                try:
                    canvas.draw_idle()
                except Exception:
                    canvas.draw()
        except Exception:
            QtWidgets.QMessageBox.warning(
                self,
                "Rescale",
                "Failed to rescale the current graph.",
            )

    @staticmethod
    def _coerce_numeric_array(values: Any) -> np.ndarray:
        if values is None:
            return np.asarray([], dtype=float)
        try:
            arr = np.asarray(values, dtype=float)
        except Exception:
            try:
                arr = np.asarray(pd.to_numeric(values, errors="coerce"), dtype=float)
            except Exception:
                return np.asarray([], dtype=float)
        arr = np.ravel(arr)
        if arr.size == 0:
            return arr
        mask = np.isfinite(arr)
        return arr[mask]

    def _calculate_combined_limits(
        self,
        entries: Sequence[Dict[str, Any]],
    ) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        xmins: list[float] = []
        xmaxs: list[float] = []
        ymins: list[float] = []
        ymaxs: list[float] = []

        for entry in entries:
            axes = entry.get("axes")
            descriptor = entry.get("descriptor")
            if descriptor is not None and isinstance(descriptor, TabDescriptor):
                lines = getattr(descriptor, "lines", {})
                for state in lines.values():
                    x_arr = self._coerce_numeric_array(state.x_data())
                    if x_arr.size:
                        xmins.append(float(np.min(x_arr)))
                        xmaxs.append(float(np.max(x_arr)))
                    y_arr = self._coerce_numeric_array(state.y_data())
                    if y_arr.size:
                        ymins.append(float(np.min(y_arr)))
                        ymaxs.append(float(np.max(y_arr)))
            if axes is not None:
                try:
                    axis_lines = list(axes.get_lines())
                except Exception:
                    axis_lines = []
                for line in axis_lines:
                    x_arr = self._coerce_numeric_array(line.get_xdata())
                    if x_arr.size:
                        xmins.append(float(np.min(x_arr)))
                        xmaxs.append(float(np.max(x_arr)))
                    y_arr = self._coerce_numeric_array(line.get_ydata())
                    if y_arr.size:
                        ymins.append(float(np.min(y_arr)))
                        ymaxs.append(float(np.max(y_arr)))

        if not xmins and not xmaxs and not ymins and not ymaxs and entries:
            # Fallback to current axes limits when no data points were detected.
            for entry in entries:
                axes = entry.get("axes")
                if axes is None:
                    continue
                try:
                    cur_xlim = axes.get_xlim()
                    cur_ylim = axes.get_ylim()
                except Exception:
                    continue
                if cur_xlim:
                    xmins.append(float(cur_xlim[0]))
                    xmaxs.append(float(cur_xlim[1]))
                if cur_ylim:
                    ymins.append(float(cur_ylim[0]))
                    ymaxs.append(float(cur_ylim[1]))

        xmin = float(min(xmins)) if xmins else None
        xmax = float(max(xmaxs)) if xmaxs else None
        ymin = float(min(ymins)) if ymins else None
        ymax = float(max(ymaxs)) if ymaxs else None
        return xmin, xmax, ymin, ymax

    def _open_rescale_all_dialog(self) -> None:
        entries: list[Dict[str, Any]] = []
        for tab, axes in self._axes_by_tab.items():
            if axes is None:
                continue
            descriptor = self._tab_descriptors.get(tab)
            title = ""
            if isinstance(descriptor, TabDescriptor):
                title = descriptor.title or descriptor.root_label or ""
            if not title:
                try:
                    title = axes.get_title()
                except Exception:
                    title = ""
            if not title:
                title = f"Graph {len(entries) + 1}"
            entries.append({"tab": tab, "axes": axes, "descriptor": descriptor, "label": title})

        if not entries:
            QtWidgets.QMessageBox.information(
                self,
                "Rescale all",
                "No graphs are available to rescale.",
            )
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Rescale graphs")
        dialog.resize(420, 460)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        description = QtWidgets.QLabel(
            "Select the graphs to include and choose whether to autoscale both axes or just the X/Y axis."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        list_widget = QtWidgets.QListWidget(dialog)
        list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        for entry in entries:
            item = QtWidgets.QListWidgetItem(entry["label"])
            flags = item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled
            item.setFlags(flags)
            item.setCheckState(QtCore.Qt.CheckState.Checked)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, entry)
            list_widget.addItem(item)
        layout.addWidget(list_widget, 1)

        axes_group = QtWidgets.QGroupBox("Apply to axes", dialog)
        axes_layout = QtWidgets.QHBoxLayout(axes_group)
        axes_layout.setContentsMargins(8, 6, 8, 6)
        apply_x = QtWidgets.QCheckBox("X axis", axes_group)
        apply_x.setChecked(True)
        apply_y = QtWidgets.QCheckBox("Y axis", axes_group)
        apply_y.setChecked(True)
        axes_layout.addWidget(apply_x)
        axes_layout.addWidget(apply_y)
        layout.addWidget(axes_group)

        limits_group = QtWidgets.QGroupBox("Limits", dialog)
        limits_layout = QtWidgets.QFormLayout(limits_group)
        limits_layout.setContentsMargins(8, 6, 8, 6)
        limits_layout.setSpacing(6)
        x_min_edit = QtWidgets.QLineEdit(limits_group)
        x_max_edit = QtWidgets.QLineEdit(limits_group)
        y_min_edit = QtWidgets.QLineEdit(limits_group)
        y_max_edit = QtWidgets.QLineEdit(limits_group)
        limits_layout.addRow("X min", x_min_edit)
        limits_layout.addRow("X max", x_max_edit)
        limits_layout.addRow("Y min", y_min_edit)
        limits_layout.addRow("Y max", y_max_edit)

        auto_button = QtWidgets.QPushButton("Auto", limits_group)
        auto_button.setToolTip("Fill the limits using combined data from the selected graphs.")
        limits_layout.addRow("", auto_button)
        layout.addWidget(limits_group)

        def selected_entries() -> list[Dict[str, Any]]:
            result: list[Dict[str, Any]] = []
            for row in range(list_widget.count()):
                item = list_widget.item(row)
                if item is None:
                    continue
                if item.checkState() != QtCore.Qt.CheckState.Checked:
                    continue
                payload = item.data(QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(payload, dict):
                    result.append(payload)
            return result

        def apply_auto_limits() -> None:
            chosen = selected_entries()
            xmin, xmax, ymin, ymax = self._calculate_combined_limits(chosen)
            if xmin is not None and xmax is not None:
                x_min_edit.setText(f"{xmin:.6g}")
                x_max_edit.setText(f"{xmax:.6g}")
            else:
                x_min_edit.clear()
                x_max_edit.clear()
            if ymin is not None and ymax is not None:
                y_min_edit.setText(f"{ymin:.6g}")
                y_max_edit.setText(f"{ymax:.6g}")
            else:
                y_min_edit.clear()
                y_max_edit.clear()

        auto_button.clicked.connect(apply_auto_limits)
        apply_auto_limits()

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        layout.addWidget(button_box)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)

        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        targets = selected_entries()
        if not targets:
            QtWidgets.QMessageBox.information(
                self,
                "Rescale all",
                "No graphs were selected for rescaling.",
            )
            return

        apply_x_axis = apply_x.isChecked()
        apply_y_axis = apply_y.isChecked()
        if not apply_x_axis and not apply_y_axis:
            QtWidgets.QMessageBox.information(
                self,
                "Rescale all",
                "Select at least one axis to rescale.",
            )
            return

        def parse_value(widget: QtWidgets.QLineEdit) -> Optional[float]:
            text = widget.text().strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None

        x_min_val = parse_value(x_min_edit)
        x_max_val = parse_value(x_max_edit)
        y_min_val = parse_value(y_min_edit)
        y_max_val = parse_value(y_max_edit)

        if apply_x_axis:
            if x_min_val is None or x_max_val is None:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Rescale all",
                    "Provide numeric X axis limits.",
                )
                return
            if x_min_val >= x_max_val:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Rescale all",
                    "X min must be less than X max.",
                )
                return
        if apply_y_axis:
            if y_min_val is None or y_max_val is None:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Rescale all",
                    "Provide numeric Y axis limits.",
                )
                return
            if y_min_val >= y_max_val:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Rescale all",
                    "Y min must be less than Y max.",
                )
                return

        canvases: set[FigureCanvas] = set()
        for entry in targets:
            axes = entry.get("axes")
            if axes is None:
                continue
            try:
                if apply_x_axis and x_min_val is not None and x_max_val is not None:
                    axes.set_xlim(x_min_val, x_max_val)
                if apply_y_axis and y_min_val is not None and y_max_val is not None:
                    axes.set_ylim(y_min_val, y_max_val)
            except Exception:
                continue
            fig = getattr(axes, "figure", None)
            if fig is not None:
                canvas = getattr(fig, "canvas", None)
                if isinstance(canvas, FigureCanvas):
                    canvases.add(canvas)
        for canvas in canvases:
            try:
                canvas.draw_idle()
            except Exception:
                canvas.draw()

    def _handle_dark_mode_toggled(self, enabled: bool) -> None:
        self._dark_mode_enabled = bool(enabled)
        if isinstance(self.settings, QtCore.QSettings):
            try:
                self.settings.setValue("graphs/dark_mode", int(self._dark_mode_enabled))
            except Exception:
                self.settings.setValue("graphs/dark_mode", self._dark_mode_enabled)
        self._apply_dark_mode_to_all_axes()

    def _apply_dark_mode_to_all_axes(self) -> None:
        if not self._axes_by_tab:
            return
        canvases: set[FigureCanvas] = set()
        platform_name = ""
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                platform_name = str(app.platformName() or "").strip().lower()
            except Exception:
                platform_name = ""
        use_immediate_draw = platform_name in {"offscreen", "minimal", "headless"}
        for axes in set(filter(None, self._axes_by_tab.values())):
            canvas = self._apply_dark_mode_to_axes(axes, self._dark_mode_enabled)
            if isinstance(canvas, FigureCanvas):
                canvases.add(canvas)
        for canvas in canvases:
            try:
                if use_immediate_draw:
                    canvas.draw()
                else:
                    canvas.draw_idle()
            except Exception:
                try:
                    canvas.draw()
                except Exception:
                    pass

    def _apply_dark_mode_to_axes(self, axes: Any, enabled: bool) -> FigureCanvas | None:
        if axes is None:
            return None
        figure = getattr(axes, "figure", None)
        if figure is not None:
            canv = getattr(figure, "canvas", None)
            axes_list = list(getattr(figure, "axes", []))
            if not axes_list:
                axes_list = [axes]
            if enabled:
                # Capture the pre-dark figure face once per figure so multi-axis
                # figures restore to the same light frame color.
                try:
                    baseline_figure_face = figure.get_facecolor()
                except Exception:
                    baseline_figure_face = None
                if baseline_figure_face is not None:
                    for ax in axes_list:
                        state = self._axes_theme_state.setdefault(ax, {})
                        state.setdefault("figure_face", baseline_figure_face)
            for ax in axes_list:
                self._apply_dark_mode_to_single_axes(ax, enabled)
            return canv
        return None

    def _apply_dark_mode_to_single_axes(self, axes: Any, enabled: bool) -> None:
        if axes is None:
            return
        figure = getattr(axes, "figure", None)
        canvas = getattr(figure, "canvas", None) if figure is not None else None
        state = self._axes_theme_state.setdefault(axes, {})

        text_state = state.setdefault("text_items", {})
        if enabled:
            if "figure_face" not in state and figure is not None:
                state["figure_face"] = figure.get_facecolor()
            if "axes_face" not in state:
                state["axes_face"] = axes.get_facecolor()
            if "x_label_color" not in state:
                state["x_label_color"] = axes.xaxis.label.get_color()
            if "y_label_color" not in state:
                state["y_label_color"] = axes.yaxis.label.get_color()
            if "title_color" not in state:
                title = axes.title
                if title is not None:
                    state["title_color"] = title.get_color()
            if "spine_colors" not in state:
                state["spine_colors"] = {
                    name: spine.get_edgecolor() for name, spine in axes.spines.items()
                }
            if "grid_color" not in state:
                try:
                    grid_lines = list(axes.get_xgridlines()) + list(axes.get_ygridlines())
                    if grid_lines:
                        state["grid_color"] = grid_lines[0].get_color()
                        state["grid_alpha"] = grid_lines[0].get_alpha()
                except Exception:
                    state.setdefault("grid_color", None)
                    state.setdefault("grid_alpha", None)
            try:
                grid_lines = list(axes.get_xgridlines()) + list(axes.get_ygridlines())
                state["grid_visible"] = any(bool(line.get_visible()) for line in grid_lines)
            except Exception:
                state.setdefault("grid_visible", False)

            legend = None
            try:
                legend = axes.get_legend()
            except Exception:
                legend = None
            if legend is not None and "legend" not in state:
                state["legend"] = {
                    "frame_face": legend.get_frame().get_facecolor(),
                    "frame_edge": legend.get_frame().get_edgecolor(),
                    "text_colors": [text.get_color() for text in legend.get_texts()],
                    "title_color": legend.get_title().get_color() if legend.get_title() else None,
                }

            dark_face = "#202124"
            light_text = "#f1f3f4"
            dark_grid_color = "#4a4d52"

            if figure is not None:
                figure.patch.set_facecolor(dark_face)
            axes.set_facecolor(dark_face)
            for spine in axes.spines.values():
                try:
                    spine.set_color(light_text)
                except Exception:
                    pass
            axes.tick_params(colors=light_text)
            try:
                if bool(state.get("grid_visible", True)):
                    axes.grid(True, color=dark_grid_color, alpha=0.3)
                else:
                    axes.grid(False)
            except Exception:
                pass
            axes.xaxis.label.set_color(light_text)
            axes.yaxis.label.set_color(light_text)
            title = axes.title
            if title is not None:
                title.set_color(light_text)
            for tick in axes.get_xticklabels() + axes.get_yticklabels():
                tick.set_color(light_text)
            for artist in getattr(axes, "texts", []):
                get_color = getattr(artist, "get_color", None)
                if not callable(get_color):
                    continue
                try:
                    current = get_color()
                except Exception:
                    continue
                if not _should_force_light_text(current):
                    continue
                key = id(artist)
                if key not in text_state:
                    text_state[key] = (_make_qpointer(artist), current)
                try:
                    artist.set_color(light_text)
                except Exception:
                    pass
            if legend is not None:
                try:
                    legend.get_frame().set_facecolor(dark_face)
                    legend.get_frame().set_edgecolor(light_text)
                except Exception:
                    pass
                for text in legend.get_texts():
                    if _should_force_light_text(text.get_color()):
                        text.set_color(light_text)
                _sync_legend_text_colors(legend)
                title_artist = legend.get_title()
                if (
                    title_artist is not None
                    and _should_force_light_text(title_artist.get_color())
                ):
                    title_artist.set_color(light_text)
        else:
            light_face = state.get("figure_face", "#ffffff")
            axes_face = state.get("axes_face", "#ffffff")
            label_color_x = state.get("x_label_color", "#202020")
            label_color_y = state.get("y_label_color", "#202020")
            title_color = state.get("title_color", "#202020")
            if _should_force_light_text(light_face):
                light_face = "#ffffff"
            if _should_force_light_text(axes_face):
                axes_face = "#ffffff"
            if _should_force_light_text(label_color_x):
                label_color_x = "#202020"
            if _should_force_light_text(label_color_y):
                label_color_y = "#202020"
            if _should_force_light_text(title_color):
                title_color = "#202020"

            if figure is not None:
                try:
                    figure.patch.set_facecolor(light_face)
                except Exception:
                    try:
                        figure.patch.set_facecolor("#ffffff")
                    except Exception:
                        pass
            try:
                axes.set_facecolor(axes_face)
            except Exception:
                try:
                    axes.set_facecolor("#ffffff")
                except Exception:
                    pass
            for name, spine in axes.spines.items():
                colors = state.get("spine_colors", {})
                if name in colors:
                    try:
                        spine.set_color(colors[name])
                    except Exception:
                        pass
            axes.tick_params(colors=label_color_x)
            axes.xaxis.label.set_color(label_color_x)
            axes.yaxis.label.set_color(label_color_y)
            title = axes.title
            if title is not None:
                title.set_color(title_color)
            for tick in axes.get_xticklabels() + axes.get_yticklabels():
                tick.set_color(label_color_x)

            legend = None
            try:
                legend = axes.get_legend()
            except Exception:
                legend = None
            if legend is not None:
                meta = state.get("legend") if isinstance(state.get("legend"), dict) else {}
                frame_face = meta.get("frame_face", "#ffffff")
                frame_edge = meta.get("frame_edge", "#4c4c4c")
                if _should_force_light_text(frame_face):
                    frame_face = "#ffffff"
                if _should_force_light_text(frame_edge):
                    frame_edge = "#4c4c4c"
                try:
                    legend.get_frame().set_facecolor(frame_face)
                    legend.get_frame().set_edgecolor(frame_edge)
                except Exception:
                    pass

                fallback_text_color = label_color_x
                text_colors = (
                    list(meta.get("text_colors", []))
                    if isinstance(meta, dict)
                    else []
                )
                for index, text in enumerate(legend.get_texts()):
                    color = (
                        text_colors[index]
                        if index < len(text_colors)
                        else fallback_text_color
                    )
                    if _should_force_light_text(color):
                        color = fallback_text_color
                    try:
                        text.set_color(color)
                    except Exception:
                        continue

                title_artist = legend.get_title()
                if title_artist is not None:
                    color = (
                        meta.get("title_color")
                        if isinstance(meta, dict)
                        else None
                    )
                    if color is None or _should_force_light_text(color):
                        color = fallback_text_color
                    try:
                        title_artist.set_color(color)
                    except Exception:
                        pass
                _sync_legend_text_colors(legend)

            original_grid_color = state.get("grid_color")
            original_grid_alpha = state.get("grid_alpha")
            original_grid_visible = bool(state.get("grid_visible", False))
            if original_grid_visible:
                try:
                    if original_grid_color is not None:
                        axes.grid(True, color=original_grid_color)
                    else:
                        axes.grid(True)
                    if original_grid_alpha is not None:
                        for line in axes.get_xgridlines() + axes.get_ygridlines():
                            line.set_alpha(original_grid_alpha)
                except Exception:
                    pass
            else:
                try:
                    axes.grid(False)
                except Exception:
                    pass
            stale: list[int] = []
            for key, data in list(text_state.items()):
                pointer, original = data
                artist = _deref_qpointer(pointer)
                if not isinstance(artist, Text):
                    stale.append(key)
                    continue
                try:
                    artist.set_color(original)
                except Exception:
                    pass
            for key in stale:
                text_state.pop(key, None)
        if canvas is not None:
            try:
                canvas.draw_idle()
            except Exception:
                pass

    def accept(self) -> None:  # type: ignore[override]
        self._apply()
        self._persist_defaults()
        super().accept()

    def _prompt_import_data(self) -> None:
        files_action = getattr(self, "_import_files_action", None)
        folder_action = getattr(self, "_import_folder_action", None)

        menu = QtWidgets.QMenu(self)
        actions_added = False

        if isinstance(files_action, QtGui.QAction):
            label = files_action.text() or "Import files..."
            proxy = menu.addAction(label)
            proxy.triggered.connect(files_action.trigger)
            actions_added = True

        if isinstance(folder_action, QtGui.QAction):
            label = folder_action.text() or "Import folders..."
            proxy = menu.addAction(label)
            proxy.triggered.connect(folder_action.trigger)
            actions_added = True

        connect_action = getattr(self, "_connect_folder_action", None)
        if isinstance(connect_action, QtGui.QAction):
            label = connect_action.text() or "Connect folders..."
            proxy = menu.addAction(label)
            proxy.triggered.connect(connect_action.trigger)
            actions_added = True

        refresh_connected_action = getattr(self, "_refresh_connected_action", None)
        if isinstance(refresh_connected_action, QtGui.QAction):
            label = refresh_connected_action.text() or "Refresh connected folders"
            proxy = menu.addAction(label)
            proxy.setEnabled(refresh_connected_action.isEnabled())
            proxy.triggered.connect(refresh_connected_action.trigger)
            actions_added = True

        if not actions_added:
            self._show_data_menu()
            return

        cursor_pos = QtGui.QCursor.pos()
        menu.exec(cursor_pos)

    def _update_save_graph_enabled(self) -> None:
        button = getattr(self, "save_graph_button", None)
        if hasattr(button, "setEnabled"):
            try:
                button.setEnabled(bool(self._tab_descriptors))
            except Exception:
                pass

    def _update_normalize_enabled(self) -> None:
        button = getattr(self, "normalize_button", None)
        if hasattr(button, "setEnabled"):
            try:
                button.setEnabled(bool(self._tab_descriptors))
            except Exception:
                pass

    def _setup_format_toolbar(self) -> None:
        controls = self._format_controls
        toolbar = QtWidgets.QToolBar("Format", self)
        toolbar.setObjectName("mw_format_toolbar")
        self._configure_toolbar(toolbar)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        controls.toolbar = toolbar

        font_combo = QtWidgets.QComboBox(toolbar)
        font_combo.setEditable(True)
        for entry in ("DejaVu Serif", "STIXGeneral", "Times New Roman", "DejaVu Sans", "Arial"):
            font_combo.addItem(entry)
        font_combo.setCurrentText("DejaVu Serif")
        font_combo.setEnabled(False)
        font_combo.currentTextChanged.connect(self._apply_text_family)
        controls.font_combo = font_combo
        toolbar.addWidget(font_combo)

        size_spin = QtWidgets.QSpinBox(toolbar)
        size_spin.setRange(6, 96)
        size_spin.setValue(18)
        size_spin.setEnabled(False)
        size_spin.valueChanged.connect(self._apply_text_size)
        controls.size_spin = size_spin
        toolbar.addWidget(size_spin)

        width_spin = QtWidgets.QDoubleSpinBox(toolbar)
        width_spin.setRange(0.1, 20.0)
        width_spin.setDecimals(1)
        width_spin.setSingleStep(0.1)
        width_spin.setValue(1.5)
        width_spin.setEnabled(False)
        width_spin.valueChanged.connect(self._apply_stroke_width)
        width_spin.setToolTip("Line width for selected lines/shapes")
        controls.width_spin = width_spin
        toolbar.addWidget(width_spin)

        bold_action = toolbar.addAction("B")
        bold_action.setCheckable(True)
        bold_action.setEnabled(False)
        bold_action.triggered.connect(self._apply_text_bold)
        bold_action.setToolTip("Toggle bold text")
        controls.bold_action = bold_action
        self._style_toolbar_button(toolbar, bold_action)

        italic_action = toolbar.addAction("I")
        italic_action.setCheckable(True)
        italic_action.setEnabled(False)
        italic_action.triggered.connect(self._apply_text_italic)
        italic_action.setToolTip("Toggle italic text")
        controls.italic_action = italic_action
        self._style_toolbar_button(toolbar, italic_action)

        underline_action = toolbar.addAction("U")
        underline_action.setCheckable(True)
        underline_action.setEnabled(False)
        underline_action.triggered.connect(self._apply_text_underline)
        underline_action.setToolTip("Toggle underlined text")
        controls.underline_action = underline_action
        self._style_toolbar_button(toolbar, underline_action)

        subscript_action = toolbar.addAction("Sub")
        subscript_action.setEnabled(False)
        subscript_action.triggered.connect(self._apply_text_subscript)
        subscript_action.setToolTip("Wrap the selected text in a mathtext subscript")
        controls.subscript_action = subscript_action
        self._style_toolbar_button(toolbar, subscript_action)

        superscript_action = toolbar.addAction("Sup")
        superscript_action.setEnabled(False)
        superscript_action.triggered.connect(self._apply_text_superscript)
        superscript_action.setToolTip("Wrap the selected text in a mathtext superscript")
        controls.superscript_action = superscript_action
        self._style_toolbar_button(toolbar, superscript_action)

        subsup_action = toolbar.addAction("Sub+Sup")
        subsup_action.setEnabled(False)
        subsup_action.triggered.connect(self._apply_text_subsuperscript)
        subsup_action.setToolTip("Wrap the selected text in mathtext with both subscript and superscript")
        controls.subsup_action = subsup_action
        self._style_toolbar_button(toolbar, subsup_action)

        edit_text_action = toolbar.addAction("Edit…")
        edit_text_action.setEnabled(False)
        edit_text_action.triggered.connect(self._edit_selected_text)
        edit_text_action.setToolTip("Edit the selected text content with mathtext helpers")
        controls.edit_text_action = edit_text_action
        self._style_toolbar_button(toolbar, edit_text_action)

        color_button = QtWidgets.QToolButton(toolbar)
        color_button.setText("Stroke…")
        color_button.setEnabled(False)
        color_button.clicked.connect(self._choose_format_color)
        color_button.setToolTip("Select an object to adjust its colour")
        controls.color_button = color_button
        toolbar.addWidget(color_button)
        self._style_toolbar_button(toolbar, color_button)

        fill_color_button = QtWidgets.QToolButton(toolbar)
        fill_color_button.setText("Fill…")
        fill_color_button.setEnabled(False)
        fill_color_button.clicked.connect(self._choose_fill_color)
        fill_color_button.setToolTip("Adjust the selected shape fill colour")
        controls.fill_color_button = fill_color_button
        toolbar.addWidget(fill_color_button)
        self._style_toolbar_button(toolbar, fill_color_button)

        toolbar.addSeparator()

        line_group = QtGui.QActionGroup(toolbar)
        line_group.setExclusive(True)
        controls.line_group = line_group

        line_action = toolbar.addAction("Line")
        line_action.setCheckable(True)
        line_action.setEnabled(False)
        line_action.triggered.connect(lambda checked: self._apply_line_style("line", checked))
        line_action.setToolTip("Show the selection as a line")
        line_group.addAction(line_action)
        controls.line_action = line_action
        self._style_toolbar_button(toolbar, line_action)

        scatter_action = toolbar.addAction("Scatter")
        scatter_action.setCheckable(True)
        scatter_action.setEnabled(False)
        scatter_action.triggered.connect(lambda checked: self._apply_line_style("scatter", checked))
        scatter_action.setToolTip("Show only markers for the selection")
        line_group.addAction(scatter_action)
        controls.scatter_action = scatter_action
        self._style_toolbar_button(toolbar, scatter_action)

        line_symbol_action = toolbar.addAction("Line + symbol")
        line_symbol_action.setCheckable(True)
        line_symbol_action.setEnabled(False)
        line_symbol_action.triggered.connect(
            lambda checked: self._apply_line_style("line_symbol", checked)
        )
        line_symbol_action.setToolTip("Show the selection with lines and markers")
        line_group.addAction(line_symbol_action)
        controls.line_symbol_action = line_symbol_action
        self._style_toolbar_button(toolbar, line_symbol_action)

    def _setup_annotation_toolbar(self) -> None:
        controls = self._annotation_controls
        toolbar = QtWidgets.QToolBar("Annotate", self)
        toolbar.setObjectName("mw_annotate_toolbar")
        self._configure_toolbar(toolbar)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        controls.toolbar = toolbar

        group = QtGui.QActionGroup(toolbar)
        group.setExclusive(True)
        controls.action_group = group

        def _add_tool(label: str, token: str, tooltip: str) -> QtGui.QAction:
            action = toolbar.addAction(label)
            action.setCheckable(True)
            action.setToolTip(tooltip)
            action.triggered.connect(lambda checked, t=token: self._set_annotation_tool(t if checked else "select"))
            group.addAction(action)
            self._style_toolbar_button(toolbar, action)
            return action

        controls.select_action = _add_tool("Select", "select", "Select annotation text, lines, arrows, and shapes")
        controls.text_action = _add_tool("Text", "text", "Place a free text label on the graph")
        controls.arrow_action = _add_tool("Arrow", "arrow", "Draw an arrow by dragging on the graph")
        controls.line_action = _add_tool("Line", "shape_line", "Draw a line by dragging on the graph")
        controls.rectangle_action = _add_tool("Rect", "rectangle", "Draw a rectangle by dragging on the graph")
        controls.ellipse_action = _add_tool("Ellipse", "ellipse", "Draw an ellipse by dragging on the graph")
        if controls.select_action is not None:
            controls.select_action.setChecked(True)

    def _apply_scientific_text_defaults(self) -> None:
        try:
            mpl.rcParams["font.family"] = ["DejaVu Serif"]
            mpl.rcParams["mathtext.fontset"] = "stix"
            mpl.rcParams["mathtext.default"] = "regular"
        except Exception:
            pass

    def _set_annotation_tool(self, tool: str) -> None:
        token = str(tool or "select").strip().lower()
        if token not in {"select", "text", "arrow", "shape_line", "rectangle", "ellipse"}:
            token = "select"
        self._annotation_tool = token
        if token == "select":
            return
        self._set_navigation_mode(None)

    def _next_graph_object_id(self) -> str:
        self._graph_object_counter += 1
        return f"graph_object_{self._graph_object_counter}"

    def _mark_graph_object(self, artist: Any, kind: str, *, label: str | None = None) -> None:
        if artist is None:
            return
        try:
            setattr(artist, "_mw_graph_object_kind", str(kind))
            setattr(artist, "_mw_graph_object_id", self._next_graph_object_id())
            if isinstance(label, str) and label.strip():
                setattr(artist, "_mw_graph_object_label", label.strip())
        except Exception:
            return

    @staticmethod
    def _graph_object_kind(artist: Any) -> str | None:
        kind = getattr(artist, "_mw_graph_object_kind", None)
        return str(kind).strip() if isinstance(kind, str) and str(kind).strip() else None

    def _iter_graph_object_texts(self, axes: Any) -> list[Text]:
        texts: list[Text] = []
        try:
            entries = list(getattr(axes, "texts", []))
        except Exception:
            entries = []
        for entry in entries:
            if not isinstance(entry, Text):
                continue
            if self._graph_object_kind(entry) != "text":
                continue
            texts.append(entry)
        return texts

    def _iter_graph_object_shapes(self, axes: Any) -> list[Any]:
        shapes: list[Any] = []
        try:
            for line in list(axes.get_lines()):
                if isinstance(line, Line2D) and self._graph_object_kind(line) == "shape_line":
                    shapes.append(line)
        except Exception:
            pass
        try:
            patches = list(getattr(axes, "patches", []))
        except Exception:
            patches = []
        for patch in patches:
            if self._graph_object_kind(patch) in {"arrow", "rectangle", "ellipse"}:
                shapes.append(patch)
        return shapes

    def _graph_object_label(self, artist: Any, *, fallback: str) -> str:
        explicit = getattr(artist, "_mw_graph_object_label", None)
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        if isinstance(artist, Text):
            try:
                text = str(artist.get_text() or "").strip()
            except Exception:
                text = ""
            if text:
                return text
        kind = self._graph_object_kind(artist)
        labels = {
            "text": "Text",
            "arrow": "Arrow",
            "shape_line": "Line",
            "rectangle": "Rectangle",
            "ellipse": "Ellipse",
        }
        return labels.get(kind or "", fallback)

    def _select_object_in_manager(self, artist: Any) -> None:
        tree = getattr(self, "object_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            return

        def _walk(item: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem | None:
            data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("object") is artist:
                return item
            for index in range(item.childCount()):
                found = _walk(item.child(index))
                if found is not None:
                    return found
            return None

        found_item = None
        for index in range(tree.topLevelItemCount()):
            found_item = _walk(tree.topLevelItem(index))
            if found_item is not None:
                break
        if found_item is None:
            return
        tree.blockSignals(True)
        try:
            tree.clearSelection()
            found_item.setSelected(True)
            tree.setCurrentItem(found_item)
        finally:
            tree.blockSignals(False)
        self._handle_object_selection_changed()

    @staticmethod
    def _strip_outer_math(text: str) -> str:
        token = str(text or "").strip()
        if len(token) >= 2 and token.startswith("$") and token.endswith("$"):
            return token[1:-1]
        return token

    def _serialize_graph_object_payloads_for_axes(self, axes: Any) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        if axes is None:
            return payloads
        for text_artist in self._iter_graph_object_texts(axes):
            try:
                families = list(text_artist.get_fontfamily())
            except Exception:
                families = []
            payloads.append(
                {
                    "kind": "text",
                    "x": float(text_artist.get_position()[0]),
                    "y": float(text_artist.get_position()[1]),
                    "text": str(getattr(text_artist, "_mw_raw_text", text_artist.get_text()) or ""),
                    "font_family": str(families[0]) if families else "DejaVu Serif",
                    "font_size": float(text_artist.get_fontsize()),
                    "color": mcolors.to_rgba(text_artist.get_color()),
                    "bold": self._text_is_bold(text_artist),
                    "italic": self._text_is_italic(text_artist),
                    "underline": bool(getattr(text_artist, "get_underline", lambda: False)()),
                }
            )
        for artist in self._iter_graph_object_shapes(axes):
            kind = self._graph_object_kind(artist)
            if kind == "shape_line" and isinstance(artist, Line2D):
                x_data = np.asarray(artist.get_xdata())
                y_data = np.asarray(artist.get_ydata())
                if x_data.size >= 2 and y_data.size >= 2:
                    payloads.append(
                        {
                            "kind": "shape_line",
                            "x0": float(x_data[0]),
                            "y0": float(y_data[0]),
                            "x1": float(x_data[-1]),
                            "y1": float(y_data[-1]),
                            "color": mcolors.to_rgba(artist.get_color()),
                            "linewidth": float(artist.get_linewidth()),
                            "linestyle": str(artist.get_linestyle() or "-"),
                        }
                    )
            elif kind == "arrow" and isinstance(artist, FancyArrowPatch):
                try:
                    path = artist.get_path().vertices
                except Exception:
                    path = None
                if path is not None and len(path) >= 2:
                    payloads.append(
                        {
                            "kind": "arrow",
                            "x0": float(path[0][0]),
                            "y0": float(path[0][1]),
                            "x1": float(path[-1][0]),
                            "y1": float(path[-1][1]),
                            "color": mcolors.to_rgba(artist.get_edgecolor()),
                            "linewidth": float(artist.get_linewidth()),
                        }
                    )
            elif kind == "rectangle" and isinstance(artist, Rectangle):
                payloads.append(
                    {
                        "kind": "rectangle",
                        "x": float(artist.get_x()),
                        "y": float(artist.get_y()),
                        "width": float(artist.get_width()),
                        "height": float(artist.get_height()),
                        "edgecolor": mcolors.to_rgba(artist.get_edgecolor()),
                        "facecolor": mcolors.to_rgba(artist.get_facecolor()),
                        "linewidth": float(artist.get_linewidth()),
                    }
                )
            elif kind == "ellipse" and isinstance(artist, Ellipse):
                payloads.append(
                    {
                        "kind": "ellipse",
                        "center_x": float(artist.center[0]),
                        "center_y": float(artist.center[1]),
                        "width": float(artist.width),
                        "height": float(artist.height),
                        "edgecolor": mcolors.to_rgba(artist.get_edgecolor()),
                        "facecolor": mcolors.to_rgba(artist.get_facecolor()),
                        "linewidth": float(artist.get_linewidth()),
                    }
                )
        return payloads

    def _restore_graph_object_payloads_for_axes(self, axes: Any, payloads: Sequence[dict[str, Any]]) -> None:
        if axes is None:
            return
        for entry in payloads:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("kind") or "").strip().lower()
            if kind == "text":
                payload = _GraphTextDialogResult(
                    text=str(entry.get("text") or ""),
                    font_family=str(entry.get("font_family") or "DejaVu Serif"),
                    font_size=float(entry.get("font_size") or 14.0),
                    color=entry.get("color") or "#111111",
                    bold=bool(entry.get("bold", False)),
                    italic=bool(entry.get("italic", False)),
                    underline=bool(entry.get("underline", False)),
                )
                artist = self._create_text_annotation(
                    axes,
                    x=float(entry.get("x") or 0.0),
                    y=float(entry.get("y") or 0.0),
                    payload=payload,
                )
                if artist is not None:
                    self._redraw_artist(artist)
            elif kind == "shape_line":
                artist = Line2D(
                    [float(entry.get("x0") or 0.0), float(entry.get("x1") or 0.0)],
                    [float(entry.get("y0") or 0.0), float(entry.get("y1") or 0.0)],
                    color=entry.get("color") or "#111111",
                    linewidth=float(entry.get("linewidth") or 1.2),
                    linestyle=str(entry.get("linestyle") or "-"),
                    zorder=10,
                )
                axes.add_line(artist)
                self._mark_graph_object(artist, "shape_line", label="Line")
                self._redraw_artist(artist)
            elif kind == "arrow":
                artist = FancyArrowPatch(
                    (float(entry.get("x0") or 0.0), float(entry.get("y0") or 0.0)),
                    (float(entry.get("x1") or 0.0), float(entry.get("y1") or 0.0)),
                    arrowstyle="->",
                    mutation_scale=12,
                    color=entry.get("color") or "#111111",
                    linewidth=float(entry.get("linewidth") or 1.2),
                    zorder=10,
                )
                axes.add_patch(artist)
                self._mark_graph_object(artist, "arrow", label="Arrow")
                self._redraw_artist(artist)
            elif kind == "rectangle":
                artist = Rectangle(
                    (float(entry.get("x") or 0.0), float(entry.get("y") or 0.0)),
                    float(entry.get("width") or 0.0),
                    float(entry.get("height") or 0.0),
                    edgecolor=entry.get("edgecolor") or "#111111",
                    facecolor=entry.get("facecolor") or (0.0, 0.0, 0.0, 0.0),
                    linewidth=float(entry.get("linewidth") or 1.2),
                    zorder=10,
                )
                axes.add_patch(artist)
                self._mark_graph_object(artist, "rectangle", label="Rectangle")
                self._redraw_artist(artist)
            elif kind == "ellipse":
                artist = Ellipse(
                    (float(entry.get("center_x") or 0.0), float(entry.get("center_y") or 0.0)),
                    float(entry.get("width") or 0.0),
                    float(entry.get("height") or 0.0),
                    edgecolor=entry.get("edgecolor") or "#111111",
                    facecolor=entry.get("facecolor") or (0.0, 0.0, 0.0, 0.0),
                    linewidth=float(entry.get("linewidth") or 1.2),
                    zorder=10,
                )
                axes.add_patch(artist)
                self._mark_graph_object(artist, "ellipse", label="Ellipse")
                self._redraw_artist(artist)

    # ------------------------------------------------------------------ shared menu helpers
    def _project_settings_key(self, suffix: str) -> str:
        prefix = getattr(self, "PROJECT_SETTINGS_PREFIX", "project") or ""
        prefix = prefix.strip("/")
        if not prefix:
            return suffix
        return f"{prefix}/{suffix}"

    def _load_recent_projects_setting(self) -> None:
        settings_key = self._project_settings_key("recent_projects")
        stored = self.settings.value(settings_key, "[]")
        entries: List[str]
        if isinstance(stored, str):
            try:
                parsed = json.loads(stored)
            except json.JSONDecodeError:
                parsed = []
            entries = [entry for entry in parsed if isinstance(entry, str)]
        elif isinstance(stored, (list, tuple)):
            entries = [str(entry) for entry in stored if isinstance(entry, (str, Path))]
        else:
            entries = []
        self._recent_projects = entries[:10]

    def _save_recent_projects_setting(self) -> None:
        settings_key = self._project_settings_key("recent_projects")
        payload = json.dumps(self._recent_projects[:10], ensure_ascii=False)
        self.settings.setValue(settings_key, payload)
        if self._project_path is not None:
            last_key = self._project_settings_key("last_path")
            self.settings.setValue(last_key, str(self._project_path.parent))
        self.settings.sync()

    def _remember_recent_project(self, path: Path) -> None:
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        self._recent_projects = [entry for entry in self._recent_projects if entry != resolved]
        self._recent_projects.insert(0, resolved)
        self._recent_projects = self._recent_projects[:10]
        self._update_recent_projects_menu()
        self._save_recent_projects_setting()

    # ------------------------------------------------------------------ workbook helpers
    def _worksheet_action_context(
        self,
    ) -> tuple[WorksheetTableModel, WorksheetTableView, Hashable] | None:
        tab = self.tab_widget.currentWidget()
        if tab is None:
            return None
        key = self._tab_to_worksheet_key.get(tab)
        if key is None:
            return None
        model = self._worksheet_models.get(key)
        if model is None:
            worksheet = self._worksheets.get(key)
            if worksheet is None:
                return None
            model = WorksheetTableModel(worksheet, self)
            self._worksheet_models[key] = model
        view = getattr(tab, "_worksheet_view", None)
        if not isinstance(view, WorksheetTableView):
            candidates = tab.findChildren(WorksheetTableView)
            view = candidates[0] if candidates else None
        if not isinstance(view, WorksheetTableView):
            return None
        return model, view, key

    def _create_new_workbook(self) -> None:
        identifier = uuid.uuid4().hex
        workbook_key: Hashable = ("manual", identifier)
        workbook_name = f"Workbook {len(self._workbooks) + 1:02d}"
        sheet_name = "Sheet1"
        initial_column = "Col01"
        dataframe = pd.DataFrame(columns=[initial_column])
        column_meta = {initial_column: WorksheetColumnMeta(long_name=initial_column)}
        worksheet_key: Hashable = (workbook_key, sheet_name)
        worksheet = WorksheetData(
            key=worksheet_key,
            name=sheet_name,
            dataframe=dataframe,
            columns=column_meta,
            source=None,
            workbook_key=workbook_key,
        )
        workbook = WorkbookData(
            key=workbook_key,
            name=workbook_name,
            worksheets=[worksheet_key],
            source=None,
            folder=None,
        )
        self._register_imported_workbook(workbook, [worksheet])
        self._open_worksheet_tab(worksheet_key)
        self._update_project_actions()
        self._update_worksheet_actions()

    def _create_blank_graph(self) -> None:
        fig = Figure(figsize=(6, 4))
        ax = fig.add_subplot(111)
        ax.set_title("Untitled Graph")
        ax.set_xlabel("X")
        ax.set_ylabel("Y")
        ax.grid(True)
        canvas = FigureCanvas(fig)
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)
        descriptor = TabDescriptor(
            kind="manual_graph",
            title=ax.get_title(),
            root_label="Graph",
            x_label=ax.get_xlabel(),
            y_label=ax.get_ylabel(),
            canvas=canvas,
            axes=ax,
            lines={},
            metadata={"blank": True},
        )
        index = self.tab_widget.addTab(tab, "Graph")
        self.tab_widget.setCurrentIndex(index)
        self._register_plot_tab(tab, canvas, ax, descriptor)
        self._mark_project_dirty()

    def _worksheet_display_label(self, worksheet: WorksheetData) -> str:
        workbook = self._workbooks.get(worksheet.workbook_key)
        workbook_name = workbook.name if isinstance(workbook, WorkbookData) else "Workbook"
        return f"{workbook_name} / {worksheet.name}"

    def _build_graph_from_series_specs(
        self,
        series_specs: Sequence[dict[str, Any]],
        *,
        title: str,
        x_label: str,
        y_label: str,
        show_grid: bool,
        kind: str,
        root_label: str,
        tab_label: str,
        metadata: Dict[str, Any] | None = None,
    ) -> QtWidgets.QWidget | None:
        fig = Figure(figsize=(6.2, 4.2))
        ax = fig.add_subplot(111)
        line_states: Dict[tuple[str, float | str], GraphLineState] = {}
        series_index = 0
        for spec in series_specs:
            if not isinstance(spec, dict):
                continue
            x_values = np.asarray(spec.get("x") or [])
            y_values = np.asarray(spec.get("y") or [])
            if x_values.size == 0 or y_values.size == 0:
                continue
            label = str(spec.get("label") or f"Series {series_index + 1}")
            plot_kwargs = {
                "label": label,
                "color": spec.get("color"),
                "linestyle": spec.get("linestyle"),
                "linewidth": spec.get("linewidth"),
                "marker": spec.get("marker"),
                "markersize": spec.get("markersize"),
            }
            try:
                line = ax.plot(x_values, y_values, **{k: v for k, v in plot_kwargs.items() if v is not None})[0]
            except Exception:
                line = ax.plot(x_values, y_values, label=label)[0]
            try:
                line.set_visible(bool(spec.get("visible", True)))
            except Exception:
                pass
            key = (label, series_index)
            line_states[key] = GraphLineState(
                key=key,
                label=label,
                line=line,
                base_x=x_values,
                base_y=y_values,
                full_x=x_values,
                full_y=y_values,
            )
            series_index += 1
        if not line_states:
            return None
        ax.set_title(title or "Graph")
        ax.set_xlabel(x_label or "X")
        ax.set_ylabel(y_label or "Y")
        ax.grid(bool(show_grid))
        try:
            ax.legend(loc="best")
        except Exception:
            pass
        canvas = FigureCanvas(fig)
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)
        descriptor = TabDescriptor(
            kind=kind,
            title=str(ax.get_title() or "Graph"),
            root_label=root_label,
            x_label=str(ax.get_xlabel() or ""),
            y_label=str(ax.get_ylabel() or ""),
            canvas=canvas,
            axes=ax,
            lines=line_states,
            metadata=dict(metadata or {}),
        )
        index = self.tab_widget.addTab(tab, tab_label or descriptor.title or "Graph")
        self.tab_widget.setCurrentIndex(index)
        self._register_plot_tab(tab, canvas, ax, descriptor)
        self._mark_project_dirty()
        return tab

    def _open_create_graph_dialog(self) -> None:
        worksheet_lookup = {
            key: worksheet
            for key, worksheet in self._worksheets.items()
            if isinstance(worksheet, WorksheetData)
        }
        if not worksheet_lookup:
            QtWidgets.QMessageBox.information(
                self,
                "Create Graph",
                "Import or create at least one worksheet before building a graph from worksheet columns.",
            )
            return
        worksheet_entries = [
            (key, self._worksheet_display_label(worksheet))
            for key, worksheet in worksheet_lookup.items()
        ]
        dialog = CreateGraphDialog(
            self,
            worksheet_entries=worksheet_entries,
            worksheet_lookup=worksheet_lookup,
        )
        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return
        payload = dialog.payload()
        if not isinstance(payload, dict):
            return
        series_specs: list[dict[str, Any]] = []
        default_x_label = payload.get("x_label") or ""
        default_y_label = payload.get("y_label") or ""
        for entry in payload.get("series", []):
            if not isinstance(entry, dict):
                continue
            worksheet = entry.get("worksheet")
            if not isinstance(worksheet, WorksheetData):
                continue
            x_column = str(entry.get("x_column") or "")
            y_column = str(entry.get("y_column") or "")
            if not x_column or not y_column:
                continue
            x_series = pd.to_numeric(worksheet.dataframe.get(x_column), errors="coerce")
            y_series = pd.to_numeric(worksheet.dataframe.get(y_column), errors="coerce")
            if x_series is None or y_series is None:
                continue
            mask = x_series.notna() & y_series.notna()
            x_values = x_series[mask].to_numpy()
            y_values = y_series[mask].to_numpy()
            if x_values.size == 0 or y_values.size == 0:
                continue
            x_meta = worksheet.columns.get(x_column, WorksheetColumnMeta(long_name=x_column))
            y_meta = worksheet.columns.get(y_column, WorksheetColumnMeta(long_name=y_column))
            if not default_x_label:
                default_x_label = (
                    f"{x_meta.long_name} [{x_meta.units}]".strip()
                    if str(x_meta.units or "").strip()
                    else str(x_meta.long_name or x_column)
                )
            if not default_y_label:
                default_y_label = (
                    f"{y_meta.long_name} [{y_meta.units}]".strip()
                    if str(y_meta.units or "").strip()
                    else str(y_meta.long_name or y_column)
                )
            series_specs.append(
                {
                    "label": entry.get("label") or str(y_meta.long_name or y_column),
                    "x": x_values.tolist(),
                    "y": y_values.tolist(),
                }
            )
        if not series_specs:
            QtWidgets.QMessageBox.information(
                self,
                "Create Graph",
                "The selected X/Y pairs did not contain any plottable numeric data.",
            )
            return
        self._build_graph_from_series_specs(
            series_specs,
            title=str(payload.get("title") or "Graph"),
            x_label=str(default_x_label or "X"),
            y_label=str(default_y_label or "Y"),
            show_grid=bool(payload.get("show_grid", True)),
            kind="manual_graph",
            root_label="Graph",
            tab_label=str(payload.get("title") or "Graph"),
            metadata={"builder": "worksheet_xy"},
        )

    def _compose_graph_from_existing(self) -> None:
        entries: list[tuple[str, str, QtWidgets.QWidget]] = []
        current = self.tab_widget.currentWidget() if hasattr(self, "tab_widget") else None
        for tab, descriptor in self._tab_descriptors.items():
            if descriptor is None:
                continue
            if descriptor.kind == "worksheet":
                continue
            axes = getattr(descriptor, "axes", None)
            if axes is None:
                continue
            try:
                lines = [line for line in axes.get_lines() if isinstance(line, Line2D) and line.get_label() != "_nolegend_"]
            except Exception:
                lines = []
            if not lines:
                continue
            label = str(descriptor.title or self.tab_widget.tabText(self.tab_widget.indexOf(tab)) or "Graph")
            detail = f"{label} ({len(lines)} series)"
            entries.append((label, detail, tab))
        if not entries:
            QtWidgets.QMessageBox.information(
                self,
                "Compose graph",
                "Open at least one plotted graph before composing a combined graph.",
            )
            return
        dialog = GraphSelectionDialog(
            self,
            entries=entries,
            title="Compose graph",
            prompt="Choose which plotted graphs to overlay into a new combined graph.",
            current=current if isinstance(current, QtWidgets.QWidget) else None,
        )
        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return
        selected_tabs = dialog.selected_tabs()
        if not selected_tabs:
            return

        series_specs: list[dict[str, Any]] = []
        x_label = "X"
        y_label = "Y"
        title = "Composed Graph"
        for tab in selected_tabs:
            descriptor = self._tab_descriptors.get(tab)
            if descriptor is None:
                continue
            source_axes = getattr(descriptor, "axes", None)
            if source_axes is None:
                continue
            if x_label == "X":
                x_label = str(getattr(source_axes, "get_xlabel", lambda: "X")() or "X")
            if y_label == "Y":
                y_label = str(getattr(source_axes, "get_ylabel", lambda: "Y")() or "Y")
            if title == "Composed Graph":
                source_title = str(getattr(source_axes, "get_title", lambda: "")() or "")
                if source_title:
                    title = source_title
            try:
                source_lines = list(source_axes.get_lines())
            except Exception:
                source_lines = []
            for line in source_lines:
                if not isinstance(line, Line2D):
                    continue
                try:
                    visible = bool(line.get_visible())
                except Exception:
                    visible = True
                if not visible:
                    continue
                try:
                    raw_label = str(line.get_label() or "")
                except Exception:
                    raw_label = ""
                if raw_label.startswith("_") and raw_label != "_nolegend_":
                    raw_label = ""
                if raw_label == "_nolegend_":
                    continue
                x_data = np.asarray(line.get_xdata())
                y_data = np.asarray(line.get_ydata())
                if x_data.size == 0 or y_data.size == 0:
                    continue
                series_specs.append(
                    {
                        "label": raw_label or f"Series {len(series_specs) + 1}",
                        "x": x_data.tolist(),
                        "y": y_data.tolist(),
                        "color": line.get_color(),
                        "linestyle": line.get_linestyle(),
                        "linewidth": float(line.get_linewidth()),
                        "marker": line.get_marker(),
                        "markersize": float(line.get_markersize()),
                        "visible": bool(line.get_visible()),
                    }
                )
        if not series_specs:
            QtWidgets.QMessageBox.information(
                self,
                "Compose graph",
                "No visible plotted series were available to combine.",
            )
            return
        self._build_graph_from_series_specs(
            series_specs,
            title=title or "Composed Graph",
            x_label=x_label or "X",
            y_label=y_label or "Y",
            show_grid=True,
            kind="composed_graph",
            root_label="Composed Graph",
            tab_label="Composed Graph",
            metadata={"source_titles": [self._tab_descriptors.get(tab_ref).title for tab_ref in selected_tabs if self._tab_descriptors.get(tab_ref) is not None]},
        )

    def _register_plot_tab(
        self,
        tab: QtWidgets.QWidget,
        canvas: FigureCanvas,
        axes: Any,
        descriptor: TabDescriptor | None = None,
    ) -> None:
        self._canvas_by_tab[tab] = canvas
        self._axes_by_tab[tab] = axes
        self._normalize_axes_unit_labels(axes, descriptor=descriptor)
        try:
            tab.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        except Exception:
            pass
        try:
            tab.setMinimumSize(0, 0)
        except Exception:
            pass
        try:
            canvas.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        except Exception:
            pass
        try:
            canvas.setMinimumSize(0, 0)
        except Exception:
            pass
        # Hook cursor tracking for this canvas
        cursor_handler = getattr(self, "_update_cursor_status", None)
        if callable(cursor_handler):
            try:
                cid = canvas.mpl_connect("motion_notify_event", cursor_handler)
                self._cursor_connections[canvas] = cid
            except Exception:
                pass
        try:
            edit_cid = canvas.mpl_connect(
                "button_press_event", self._handle_canvas_button_press
            )
            self._edit_connections[canvas] = edit_cid
        except Exception:
            pass
        try:
            resize_cid = canvas.mpl_connect(
                "resize_event",
                lambda _event, c=canvas: self._handle_canvas_resize(c),
            )
            self._resize_connections[canvas] = resize_cid
        except Exception:
            pass
        try:
            motion_cid = canvas.mpl_connect(
                "motion_notify_event", self._handle_canvas_motion
            )
            self._annotation_motion_connections[canvas] = motion_cid
        except Exception:
            pass
        try:
            release_cid = canvas.mpl_connect(
                "button_release_event", self._handle_canvas_button_release
            )
            self._annotation_release_connections[canvas] = release_cid
        except Exception:
            pass
        try:
            copy_shortcut = QtGui.QShortcut(
                QtGui.QKeySequence.StandardKey.Copy,
                canvas,
            )
            copy_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            copy_shortcut.activated.connect(
                lambda c=canvas: self._copy_graph_to_clipboard(canvas=c),
            )
            self._canvas_copy_shortcuts[canvas] = copy_shortcut
        except Exception:
            pass
        self._capture_canvas_base_geometry(canvas)
        try:
            QtCore.QTimer.singleShot(
                0,
                lambda c=canvas: self._apply_canvas_display_scale(c, force=True),
            )
        except Exception:
            pass
        if descriptor is not None:
            self._tab_descriptors[tab] = descriptor
            plugin_name = self._tab_plugin_name(descriptor)
            if plugin_name and isinstance(descriptor.metadata, dict) and "plugin" not in descriptor.metadata:
                descriptor.metadata["plugin"] = plugin_name
            item = self._ensure_graph_tree_item(tab, descriptor)
            if item is not None:
                self._graph_tree_items[tab] = item
            if not self._history.is_replaying:
                info_holder: Dict[str, Any] = {"info": None}

                def _undo_creation() -> None:
                    info_holder["info"] = self._remove_tab_internal(tab)

                def _redo_creation() -> None:
                    info = info_holder.get("info")
                    if info is not None:
                        self._restore_tab_from_info(info)

                label = descriptor.root_label or descriptor.title or "Plot"
                self._record_history_action(
                    f"Add tab {label}",
                    undo=_undo_creation,
                    redo=_redo_creation,
                )
            self._register_shared_plot_workbook_for_tab(tab, descriptor)
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        if self._dark_mode_enabled:
            self._apply_dark_mode_to_axes(axes, True)
        self._update_navigation_enabled()
        if self.tab_widget.currentWidget() is tab:
            self._rebuild_object_manager_for_tab(tab)
        self._update_tab_buttons()
        self._mark_project_dirty()

    def _capture_canvas_base_geometry(
        self,
        canvas: FigureCanvas | None,
        *,
        width_in: float | None = None,
        height_in: float | None = None,
        dpi: float | None = None,
        keep_existing_dpi: bool = True,
    ) -> bool:
        if not isinstance(canvas, FigureCanvas):
            return False
        figure = getattr(canvas, "figure", None)
        if figure is None:
            return False
        if width_in is None or height_in is None:
            try:
                size = figure.get_size_inches()
                if len(size) >= 2:
                    if width_in is None:
                        width_in = float(size[0])
                    if height_in is None:
                        height_in = float(size[1])
            except Exception:
                return False
        if width_in is None or height_in is None:
            return False
        try:
            width_value = float(width_in)
            height_value = float(height_in)
        except Exception:
            return False
        if not np.isfinite(width_value) or not np.isfinite(height_value):
            return False
        if width_value <= 0.0 or height_value <= 0.0:
            return False
        self._canvas_base_size_inches[canvas] = (width_value, height_value)

        dpi_value: float | None = None
        if keep_existing_dpi:
            existing_dpi = self._canvas_base_dpi.get(canvas)
            if existing_dpi is not None and existing_dpi > 0.0 and np.isfinite(existing_dpi):
                dpi_value = float(existing_dpi)
        if dpi_value is None and dpi is not None:
            try:
                parsed = float(dpi)
            except Exception:
                parsed = 0.0
            if parsed > 0.0 and np.isfinite(parsed):
                dpi_value = parsed
        if dpi_value is None:
            try:
                parsed = float(getattr(figure, "dpi", 100.0) or 100.0)
            except Exception:
                parsed = 100.0
            if parsed <= 0.0 or not np.isfinite(parsed):
                parsed = 100.0
            dpi_value = float(parsed)
        self._canvas_base_dpi[canvas] = float(dpi_value)
        return True

    def _canvas_for_axes_object(self, axes: Any) -> FigureCanvas | None:
        if axes is None:
            return None
        tab = self._tab_for_axes(axes)
        if tab is not None:
            canvas = self._canvas_by_tab.get(tab)
            if isinstance(canvas, FigureCanvas):
                return canvas
        figure = getattr(axes, "figure", None)
        if figure is None:
            return None
        for canvas in self._canvas_by_tab.values():
            if not isinstance(canvas, FigureCanvas):
                continue
            if getattr(canvas, "figure", None) is figure:
                return canvas
        return None

    def _sync_canvas_display_reference(
        self,
        *,
        axes: Any,
        width_in: float | None = None,
        height_in: float | None = None,
    ) -> None:
        canvas = self._canvas_for_axes_object(axes)
        if not isinstance(canvas, FigureCanvas):
            return
        updated = self._capture_canvas_base_geometry(
            canvas,
            width_in=width_in,
            height_in=height_in,
            keep_existing_dpi=True,
        )
        if not updated:
            return
        self._apply_canvas_display_scale(canvas, force=True)

    def _handle_canvas_resize(self, canvas: FigureCanvas | None) -> None:
        if not isinstance(canvas, FigureCanvas):
            return
        self._apply_canvas_display_scale(canvas)

    def _apply_canvas_display_scale(
        self,
        canvas: FigureCanvas | None,
        *,
        force: bool = False,
    ) -> None:
        if not isinstance(canvas, FigureCanvas):
            return
        if canvas in self._canvas_scaling_guard:
            return
        figure = getattr(canvas, "figure", None)
        if figure is None:
            return
        if canvas not in self._canvas_base_size_inches:
            if not self._capture_canvas_base_geometry(canvas):
                return
        base_size = self._canvas_base_size_inches.get(canvas)
        if not isinstance(base_size, tuple) or len(base_size) < 2:
            return
        base_width, base_height = float(base_size[0]), float(base_size[1])
        if base_width <= 0.0 or base_height <= 0.0:
            return
        base_dpi = float(self._canvas_base_dpi.get(canvas, 100.0) or 100.0)
        if base_dpi <= 0.0:
            base_dpi = 100.0

        try:
            canvas_width = int(canvas.width())
            canvas_height = int(canvas.height())
        except Exception:
            return
        if canvas_width <= 1 or canvas_height <= 1:
            return
        device_ratio = 1.0
        ratio_getter = getattr(canvas, "devicePixelRatioF", None)
        if callable(ratio_getter):
            try:
                ratio_value = float(ratio_getter())
            except Exception:
                ratio_value = 1.0
            if ratio_value > 0.0 and np.isfinite(ratio_value):
                device_ratio = ratio_value
        width_px = float(canvas_width) * device_ratio
        height_px = float(canvas_height) * device_ratio
        if width_px <= 0.0 or height_px <= 0.0:
            return
        scale_x = width_px / max(1e-6, base_width * base_dpi)
        scale_y = height_px / max(1e-6, base_height * base_dpi)
        scale = min(scale_x, scale_y)
        if not np.isfinite(scale) or scale <= 0.0:
            return
        scale = max(0.05, min(float(scale), 20.0))
        target_dpi = max(1.0, float(base_dpi * scale))
        self._canvas_display_scale[canvas] = scale

        should_apply = force
        if not should_apply:
            try:
                current_dpi = float(getattr(figure, "dpi", target_dpi) or target_dpi)
            except Exception:
                current_dpi = target_dpi
            try:
                size = figure.get_size_inches()
                current_width = float(size[0])
                current_height = float(size[1])
            except Exception:
                current_width = base_width
                current_height = base_height
            dpi_diff = abs(current_dpi - target_dpi)
            size_diff = max(abs(current_width - base_width), abs(current_height - base_height))
            should_apply = dpi_diff > max(0.2, target_dpi * 0.003) or size_diff > 1e-4
        if not should_apply:
            return

        self._canvas_scaling_guard.add(canvas)
        try:
            try:
                figure.set_dpi(target_dpi)
            except Exception:
                pass
            try:
                figure.set_size_inches(base_width, base_height, forward=False)
            except Exception:
                pass
            try:
                canvas.draw_idle()
            except Exception:
                try:
                    canvas.draw()
                except Exception:
                    pass
        finally:
            self._canvas_scaling_guard.discard(canvas)

    def _ensure_graph_tree_item(
        self, tab: QtWidgets.QWidget, descriptor: TabDescriptor
    ) -> QtWidgets.QTreeWidgetItem | None:
        tree = getattr(self, "project_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            return None
        root = self._ensure_graph_tree_root()
        if root is None:
            return None
        with self._suspend_project_tree_updates():
            label = descriptor.root_label or descriptor.title or "Plot"
            item = QtWidgets.QTreeWidgetItem([label, descriptor.title or ""])
            self._set_tree_item_text(
                item,
                name=label,
                details=descriptor.title or "",
            )
            root.addChild(item)
            item.setExpanded(True)
            self._assign_project_payload(item, ("graph", tab))
            return item

    def _ensure_graph_tree_root(self) -> QtWidgets.QTreeWidgetItem | None:
        tree = getattr(self, "project_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            return None
        root = self._graph_tree_root
        if root is None:
            with self._suspend_project_tree_updates():
                root = QtWidgets.QTreeWidgetItem(["Plots"])
                self._set_tree_item_text(root, name="Plots", details="")
                root.setFirstColumnSpanned(True)
                root.setExpanded(True)
                tree.insertTopLevelItem(0, root)
                self._graph_tree_root = root
        else:
            root.setExpanded(True)
        return root

    def _insert_column(self, *, position: Literal["before", "after"]) -> None:
        context = self._worksheet_action_context()
        if context is None:
            return
        model, view, key = context
        selection_model = view.selectionModel()
        selected_columns = sorted(
            {index.column() for index in selection_model.selectedColumns()}
        ) if selection_model is not None else []
        if position == "before" and not selected_columns:
            return
        if selected_columns:
            if position == "before":
                insert_at = selected_columns[0]
            else:
                insert_at = selected_columns[-1] + 1
        else:
            insert_at = model.columnCount()
        if not model.insertColumns(insert_at, 1):
            return
        self._worksheet_models[key] = model
        header = view.horizontalHeader()
        try:
            header.setStretchLastSection(True)
        except Exception:
            pass
        sel_model = view.selectionModel()
        if sel_model is not None:
            sel_model.clearSelection()
            target_column = min(insert_at, model.columnCount() - 1)
            target_index = model.index(0, target_column)
            if target_index.isValid():
                sel_model.select(
                    target_index,
                    QtCore.QItemSelectionModel.SelectionFlag.Select
                    | QtCore.QItemSelectionModel.SelectionFlag.Columns,
                )
        self._update_project_actions()
        self._update_worksheet_actions()

    def _delete_selected_columns(self) -> None:
        context = self._worksheet_action_context()
        if context is None:
            return
        model, view, key = context
        selection_model = view.selectionModel()
        if selection_model is None:
            return
        selected_columns = sorted({index.column() for index in selection_model.selectedColumns()})
        if not selected_columns:
            return
        removed = 0
        for column in selected_columns:
            model.removeColumns(column - removed, 1)
            removed += 1
        self._worksheet_models[key] = model
        self._update_project_actions()
        self._update_worksheet_actions()

    def _reorder_columns(self) -> None:
        context = self._worksheet_action_context()
        if context is None:
            return
        model, view, key = context
        columns = list(model.dataframe.columns)
        if len(columns) < 2:
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Reorder Columns")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QtWidgets.QLabel("Drag to reorder columns, then click Done.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        list_widget = QtWidgets.QListWidget(dialog)
        list_widget.addItems(columns)
        list_widget.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        list_widget.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
        list_widget.setAlternatingRowColors(True)
        list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(list_widget, 1)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        layout.addWidget(button_box)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)

        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        new_order = [list_widget.item(idx).text() for idx in range(list_widget.count())]
        if new_order == columns or not new_order:
            return

        model.beginResetModel()
        reordered = model.dataframe.reindex(columns=new_order)
        model._frame = reordered
        model._worksheet.dataframe = reordered
        model._columns = list(new_order)
        column_meta = {
            name: model._worksheet.columns.get(name, WorksheetColumnMeta(long_name=name))
            for name in new_order
        }
        model._worksheet.columns = column_meta
        model.endResetModel()

        self._worksheet_models[key] = model
        try:
            view.horizontalHeader().setStretchLastSection(True)
        except Exception:
            pass
        self._update_project_actions()
        self._update_worksheet_actions()

    def _update_worksheet_actions(self) -> None:
        context = self._worksheet_action_context()
        has_context = context is not None
        column_count = 0
        selected_columns = 0
        if context is not None:
            model, view, _ = context
            column_count = model.columnCount()
            selection_model = view.selectionModel()
            if selection_model is not None:
                selected_columns = len(selection_model.selectedColumns())
        if self._add_column_after_action is not None:
            self._add_column_after_action.setEnabled(has_context)
        if self._add_column_before_action is not None:
            self._add_column_before_action.setEnabled(has_context and selected_columns > 0)
        if self._delete_column_action is not None:
            self._delete_column_action.setEnabled(has_context and selected_columns > 0)
        if self._reorder_columns_action is not None:
            self._reorder_columns_action.setEnabled(has_context and column_count > 1)

    def _update_recent_projects_menu(self) -> None:
        menu = self._recent_projects_menu
        if menu is None:
            return
        menu.clear()
        if not self._recent_projects:
            action = menu.addAction("No recent projects")
            if action is not None:
                action.setEnabled(False)
            return
        for entry in self._recent_projects:
            path = Path(entry)
            label = path.name or entry
            action = menu.addAction(label)
            if action is not None:
                action.triggered.connect(partial(self._load_project_from_recent, path))

    def _load_project_from_recent(self, path: Path) -> None:
        self._load_project_from_path(path)

    def _project_dialog_start_directory(self) -> Path:
        if self._project_path is not None:
            return self._project_path.parent
        last_key = self._project_settings_key("last_path")
        stored = self.settings.value(last_key, "")
        if isinstance(stored, str) and stored:
            candidate = Path(stored)
            if candidate.exists():
                return candidate
        return Path.home()

    def _data_dialog_start_directory(self) -> Path:
        chooser = getattr(self, "_dialog_start_directory", None)
        if callable(chooser):
            try:
                candidate = chooser()
            except Exception:
                candidate = None
            if isinstance(candidate, Path):
                try:
                    if candidate.exists():
                        return candidate
                except Exception:
                    pass
            elif isinstance(candidate, str) and candidate:
                try:
                    path_candidate = Path(candidate)
                except Exception:
                    path_candidate = None
                if isinstance(path_candidate, Path):
                    try:
                        if path_candidate.exists():
                            return path_candidate
                    except Exception:
                        pass
        return self._project_dialog_start_directory()

    def _update_project_title(self) -> None:
        title = self._base_title
        if self._project_path is not None:
            title = f"{self._base_title} - {self._project_path.name}"
        self.setWindowTitle(title)

    def _update_project_actions(self) -> None:
        has_data = self._has_project_data_to_save()
        if self._save_project_action is not None:
            self._save_project_action.setEnabled(has_data)
        if self._save_project_as_action is not None:
            self._save_project_as_action.setEnabled(has_data)

    # ------------------------------------------------------------------ project workflow hooks
    def _has_project_data_to_save(self) -> bool:
        """Return True when the current session can be persisted."""

        return False

    def _build_project_payload(self, *, base_path: Path | None) -> Dict[str, Any]:
        """Return a serialisable payload describing the current session."""

        _ = base_path
        raise NotImplementedError

    def _apply_project_payload(self, payload: Dict[str, Any], *, project_dir: Path) -> bool:
        """Populate the session using ``payload`` from a project file."""

        _ = (payload, project_dir)
        raise NotImplementedError

    def _reset_project_state(self) -> None:
        """Clear session data prior to loading a project."""

        self._clear_project_dirty()
        self._session_has_imports = False
        self._history.clear()
        self._update_history_actions()

    def _after_project_loaded(self, path: Path, payload: Dict[str, Any]) -> None:
        """Hook for subclasses after a project has been applied."""

        _ = (path, payload)

    def _after_project_saved(self, path: Path, payload: Dict[str, Any]) -> None:
        """Hook for subclasses after a project has been written."""

        _ = (path, payload)

    # ------------------------------------------------------------------ project commands
    def _open_project_dialog(self) -> None:
        start_dir = self._project_dialog_start_directory()
        filters = f"Python Plot Project (*{self.PROJECT_EXTENSION});;All files (*)"
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Project",
            str(start_dir),
            filters,
        )
        if not path_str:
            return
        self._load_project_from_path(Path(path_str))

    def _save_project(self) -> None:
        if not self._has_project_data_to_save():
            QtWidgets.QMessageBox.information(
                self,
                "Save Project",
                "There is no data to save yet.",
            )
            return
        if self._project_path is None:
            self._save_project_as()
            return
        self._write_project_file(self._project_path)

    def _save_project_as(self) -> None:
        if not self._has_project_data_to_save():
            QtWidgets.QMessageBox.information(
                self,
                "Save Project As",
                "Load or import data before saving a project.",
            )
            return
        start_dir = self._project_dialog_start_directory()
        suggested = start_dir / self._default_project_filename()
        filters = f"Python Plot Project (*{self.PROJECT_EXTENSION});;All files (*)"
        path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Project As",
            str(suggested),
            filters,
        )
        if not path_str:
            return
        target = Path(path_str)
        if target.suffix.lower() != self.PROJECT_EXTENSION.lower():
            target = target.with_suffix(self.PROJECT_EXTENSION)
        self._write_project_file(target)

    def _write_project_file(self, target: Path) -> None:
        payload = self._build_project_payload(base_path=target.parent)
        if not isinstance(payload, dict):
            QtWidgets.QMessageBox.critical(
                self,
                "Save Project",
                "The project payload is invalid.",
            )
            return
        payload.setdefault("version", self.PROJECT_VERSION)
        if "kind" not in payload:
            payload["kind"] = self.PROJECT_CODE or self.__class__.__name__
        try:
            serializable_payload = _json_friendly_project_value(payload)
            target.write_text(
                json.dumps(serializable_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Save Project",
                f"Failed to write project file:\n{exc}",
            )
            return
        self._project_path = target
        self._persist_connected_folders()
        self._update_project_title()
        self._remember_recent_project(target)
        self._after_project_saved(target, payload)
        self._update_project_actions()
        self._clear_project_dirty()

    def _load_project_from_path(self, path: Path) -> None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            QtWidgets.QMessageBox.warning(
                self,
                "Open Project",
                f"Project file not found:\n{path}",
            )
            return
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Open Project",
                f"Failed to read project file:\n{exc}",
            )
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Open Project",
                f"Project file is not valid JSON:\n{exc}",
            )
            return
        if not isinstance(payload, dict):
            QtWidgets.QMessageBox.critical(
                self,
                "Open Project",
                "Project file did not contain a JSON object.",
            )
            return
        version = payload.get("version")
        if version != self.PROJECT_VERSION:
            QtWidgets.QMessageBox.warning(
                self,
                "Open Project",
                "This project was saved with an incompatible version.",
            )
            return
        self._reset_project_state()
        project_dir = path.parent
        try:
            success = self._apply_project_payload(payload, project_dir=project_dir)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Open Project",
                f"Failed to apply project payload:\n{exc}",
            )
            return
        if not success:
            return
        self._project_path = path
        self._restore_connected_folders(force_local=True)
        self._update_project_title()
        self._remember_recent_project(path)
        self._after_project_loaded(path, payload)
        self._update_project_actions()
        self._clear_project_dirty()

    # ------------------------------------------------------------------ data import helpers
    def _import_data_from_files(self) -> None:
        start_dir = self._data_dialog_start_directory()
        filters = [
            "Data files (" + " ".join(f"*{ext}" for ext in self.SUPPORTED_IMPORT_EXTENSIONS) + ")",
            "All files (*)",
        ]
        dialog_filter = ";;".join(filters)
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Import Data Files",
            str(start_dir),
            dialog_filter,
        )
        if not paths:
            return
        self._import_paths(Path(path) for path in paths)

    def _import_data_from_folder(self) -> None:
        start_dir = self._data_dialog_start_directory()
        directories = self._select_directories(
            self,
            title="Import Data Folder(s)",
            start_dir=start_dir,
        )
        if not directories:
            return
        self._import_paths(Path(directory) for directory in directories)

    def _connect_data_from_folder(self) -> None:
        start_dir = self._data_dialog_start_directory()
        directories = self._select_directories(
            self,
            title="Connect Data Folder(s)",
            start_dir=start_dir,
        )
        if not directories:
            return
        self._connect_data_folders(Path(directory) for directory in directories)

    def _connected_folder_file_candidates(self) -> List[Path]:
        files: List[Path] = []
        for folder in self._connected_data_folders:
            if not isinstance(folder, Path) or not folder.exists() or not folder.is_dir():
                continue
            files.extend(self._iter_supported_files(folder))
        unique: List[Path] = []
        seen: set[str] = set()
        for file_path in files:
            try:
                resolved = str(file_path.resolve())
            except Exception:
                resolved = str(file_path)
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(file_path)
        return unique

    def _known_imported_file_keys(self) -> set[str]:
        keys: set[str] = set()
        for workbook in self._workbooks.values():
            source = getattr(workbook, "source", None)
            if not isinstance(source, Path):
                continue
            try:
                keys.add(str(source.resolve()))
            except Exception:
                keys.add(str(source))
        return keys

    def _update_connected_folder_state(self) -> None:
        headless_platform = str(QtWidgets.QApplication.platformName() or "").strip().lower() in {"offscreen", "minimal"}
        if self._connected_data_folders and not headless_platform:
            if not self._connected_folder_timer.isActive():
                self._connected_folder_timer.start()
        else:
            self._connected_folder_timer.stop()
        action = getattr(self, "_refresh_connected_action", None)
        if isinstance(action, QtGui.QAction):
            action.setEnabled(bool(self._connected_data_folders))
        menu_action = getattr(self, "_refresh_connected_menu_action", None)
        if isinstance(menu_action, QtGui.QAction):
            menu_action.setEnabled(bool(self._connected_data_folders))

    def _persist_connected_folders(self) -> None:
        project_path = getattr(self, "_project_path", None)
        if not isinstance(project_path, Path):
            return
        entries = [str(path) for path in self._connected_data_folders if isinstance(path, Path)]
        key = self._project_local_connected_folder_key(project_path)
        if entries:
            self.settings.setValue(key, entries)
        else:
            self.settings.remove(key)

    def _restore_connected_folders(self, *, force_local: bool = False) -> None:
        if self._connected_data_folders and not force_local:
            self._update_connected_folder_state()
            return
        project_path = getattr(self, "_project_path", None)
        if not isinstance(project_path, Path):
            self._connected_data_folders = []
            self._connected_folder_seen_files = set()
            self._update_connected_folder_state()
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                platform_name = str(app.platformName() or "").strip().lower()
            except Exception:
                platform_name = ""
        if platform_name in {"offscreen", "minimal", "headless"}:
            self._connected_data_folders = []
            self._connected_folder_seen_files = set()
            self._update_connected_folder_state()
            return
        stored = self.settings.value(self._project_local_connected_folder_key(project_path), [])
        if isinstance(stored, str):
            candidates = [stored] if stored.strip() else []
        elif isinstance(stored, (list, tuple, set)):
            candidates = [str(entry) for entry in stored if entry]
        else:
            candidates = []
        folders: List[Path] = []
        seen: set[str] = set()
        for entry in candidates:
            try:
                path = Path(entry)
            except Exception:
                continue
            if not path.exists() or not path.is_dir():
                continue
            try:
                key = str(path.resolve())
            except Exception:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            folders.append(path)
        if force_local and not folders and self._connected_data_folders:
            self._update_connected_folder_state()
            return
        self._connected_data_folders = folders
        self._connected_folder_seen_files = set()
        self._update_connected_folder_state()
        if folders:
            self._connected_folders_restoring = True
            try:
                self._refresh_connected_folders()
            finally:
                self._connected_folders_restoring = False

    def _project_local_connected_folder_key(self, project_path: Path) -> str:
        try:
            resolved = str(project_path.resolve())
        except Exception:
            resolved = str(project_path)
        digest = hashlib.sha1(resolved.encode("utf-8", errors="ignore")).hexdigest()[:16]
        return self._project_settings_key(f"connected_folders_local/{digest}")

    def _connect_data_folders(self, folders: Iterable[Path]) -> None:
        changed = False
        existing = {
            str(path.resolve()) if isinstance(path, Path) else str(path)
            for path in self._connected_data_folders
            if isinstance(path, Path)
        }
        for folder in folders:
            if not isinstance(folder, Path) or not folder.exists() or not folder.is_dir():
                continue
            try:
                key = str(folder.resolve())
            except Exception:
                key = str(folder)
            if key in existing:
                continue
            existing.add(key)
            self._connected_data_folders.append(folder)
            changed = True
        if not changed:
            return
        self._persist_connected_folders()
        self._update_connected_folder_state()
        self._refresh_connected_folders()
        if not self._connected_folders_restoring:
            self._mark_project_dirty()
        update_project_actions = getattr(self, "_update_project_actions", None)
        if callable(update_project_actions):
            update_project_actions()

    def _poll_connected_folders(self) -> None:
        if not self.isVisible():
            return
        if self._connected_folder_polling:
            return
        self._connected_folder_polling = True
        try:
            self._refresh_connected_folders(silent=True)
        finally:
            self._connected_folder_polling = False

    def _refresh_connected_folders(self, silent: bool = False) -> None:
        connected_files = self._connected_folder_file_candidates()
        new_files: List[Path] = []
        for file_path in connected_files:
            try:
                key = str(file_path.resolve())
            except Exception:
                key = str(file_path)
            if key in self._connected_folder_seen_files:
                continue
            new_files.append(file_path)
        if not new_files:
            if not silent:
                self._append_log("No new files found in connected folders.")
            return
        self._import_paths(new_files)
        for file_path in new_files:
            try:
                self._connected_folder_seen_files.add(str(file_path.resolve()))
            except Exception:
                self._connected_folder_seen_files.add(str(file_path))
        if not silent:
            self._append_log(f"Imported {len(new_files)} new file(s) from connected folders.")

    def _import_paths(self, paths: Iterable[Path]) -> None:
        provided_paths: List[Path] = []
        for source in paths:
            if isinstance(source, Path):
                provided_paths.append(source)
        files: List[Path] = []
        for path in provided_paths:
            if path.is_dir():
                files.extend(self._iter_supported_files(path))
            elif path.is_file() and self._is_supported_data_file(path):
                files.append(path)
        if not files:
            QtWidgets.QMessageBox.information(
                self,
                "Import Data",
                "No supported data files were found.",
            )
            return
        errors: List[str] = []
        imported = 0
        imported_files: List[Path] = []
        total_files = len(files)
        self._begin_task_progress(
            "Importing data...",
            maximum=max(1, total_files),
            value=0,
        )
        update_interval = 1 if total_files <= 30 else max(1, total_files // 80)
        event_interval = 1 if total_files <= 24 else max(2, total_files // 40)
        position = 0
        try:
            for position, file_path in enumerate(files, start=1):
                result = self._load_workbook_from_file(file_path)
                if result is None:
                    if (
                        position == total_files
                        or position <= 2
                        or position % update_interval == 0
                    ):
                        self._update_task_progress(
                            value=position,
                            title=f"Importing {file_path.name} ({position}/{total_files})",
                        )
                    if position == total_files or position % event_interval == 0:
                        try:
                            QtWidgets.QApplication.processEvents(
                                QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                            )
                        except Exception:
                            pass
                    continue
                workbook, worksheets = result
                try:
                    self._register_imported_workbook(workbook, worksheets)
                except Exception as exc:  # pragma: no cover - defensive, UI fallback
                    errors.append(f"{file_path}: {exc}")
                    continue
                imported += len(worksheets)
                imported_files.append(file_path)
                if (
                    position == total_files
                    or position <= 2
                    or position % update_interval == 0
                ):
                    self._update_task_progress(
                        value=position,
                        title=f"Importing {file_path.name} ({position}/{total_files})",
                    )
                if position == total_files or position % event_interval == 0:
                    try:
                        QtWidgets.QApplication.processEvents(
                            QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                        )
                    except Exception:
                        pass
        finally:
            self._update_task_progress(
                value=total_files,
                title="Import complete.",
            )
            self._end_task_progress()
        if errors:
            QtWidgets.QMessageBox.warning(
                self,
                "Import Data",
                "Some files could not be imported:\n" + "\n".join(errors[:10]),
            )
        if imported:
            self._last_imported_file_paths = list(imported_files)
            pending: List[Path] = []
            seen_pending: set[str] = set()
            for file_path in [*self._pending_new_plot_paths, *imported_files]:
                try:
                    key = str(file_path.resolve())
                except Exception:
                    key = str(file_path)
                if key in seen_pending:
                    continue
                seen_pending.add(key)
                pending.append(file_path)
            self._pending_new_plot_paths = pending
            self._refresh_imported_data_summary()
            self._update_project_actions()
            if provided_paths:
                self._remember_import_directory(provided_paths[0])
                self._last_import_sources = [str(path) for path in provided_paths]
                self._persist_import_sources(provided_paths)
            self._session_has_imports = True
            self._mark_project_dirty()
        else:
            self._last_imported_file_paths = []
        if self._refresh_import_action is not None:
            self._refresh_import_action.setEnabled(bool(self._worksheets))

    def _remember_import_directory(self, source: Path) -> None:
        try:
            resolved = source.resolve()
        except Exception:
            resolved = source
        target = resolved if resolved.is_dir() else resolved.parent
        if target is None or not target.exists():
            return
        remember_paths = getattr(self, "_remember_directory_from_paths", None)
        if callable(remember_paths):
            try:
                remember_paths([target])
            except Exception:
                pass
        self.settings.setValue(self._project_settings_key("last_path"), str(target))

    def _persist_import_sources(self, sources: Iterable[Path | str]) -> None:
        if self._restoring_imports:
            return
        dev_opts = developer_options()
        if not dev_opts.keep_files():
            self._clear_persisted_imports()
            return
        unique: List[str] = []
        for source in sources:
            text = str(source)
            if not text:
                continue
            if text not in unique:
                unique.append(text)
        if unique:
            self.settings.setValue(self._import_storage_key, unique)
            self._last_import_sources = unique
        else:
            self._clear_persisted_imports()

    def _clear_persisted_imports(self) -> None:
        self.settings.remove(self._import_storage_key)
        self._last_import_sources = []

    def _restore_persisted_imports(self) -> None:
        dev_opts = developer_options()
        if not dev_opts.keep_files():
            self._clear_persisted_imports()
            return
        stored = self.settings.value(self._import_storage_key, [])
        if isinstance(stored, str):
            candidates = [seg for seg in stored.splitlines() if seg]
            candidates = candidates or ([stored] if stored else [])
        elif isinstance(stored, (list, tuple, set)):
            candidates = [str(seg) for seg in stored if seg]
        else:
            candidates = []
        if not candidates:
            return
        paths: List[Path] = []
        for entry in candidates:
            try:
                path = Path(entry)
            except Exception:
                continue
            paths.append(path)
        if not paths:
            return
        self._restoring_imports = True
        try:
            self._import_paths(paths)
        finally:
            self._restoring_imports = False
        self._last_import_sources = [str(path) for path in paths]

    def _handle_keep_files_changed(self, enabled: bool) -> None:
        if enabled:
            if self._last_import_sources:
                self._persist_import_sources(self._last_import_sources)
            else:
                self._restore_persisted_imports()
        else:
            self._clear_persisted_imports()

    def _iter_supported_files(self, root: Path) -> List[Path]:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        results: List[Path] = []
        for candidate in resolved.rglob("*"):
            if candidate.is_file() and self._is_supported_data_file(candidate):
                results.append(candidate)
        return results

    def _is_supported_data_file(self, path: Path) -> bool:
        return path.suffix.lower() in self.SUPPORTED_IMPORT_EXTENSIONS

    def _load_workbook_from_file(
        self,
        path: Path,
    ) -> tuple[WorkbookData, List[WorksheetData]] | None:
        suffix = path.suffix.lower()
        try:
            if suffix in {".csv", ".tsv", ".txt", ".dat"}:
                frame = self._read_delimited_file(path, suffix)
                if frame is None:
                    return None
                workbook = self._build_workbook_shell(path)
                worksheet = self._create_worksheet_from_frame(workbook, path.stem, frame)
                workbook.worksheets = [worksheet.key]
                return workbook, [worksheet]
            if suffix in {".xlsx", ".xls", ".xlsm"}:
                frames = pd.read_excel(path, sheet_name=None)
                workbook = self._build_workbook_shell(path)
                worksheets: List[WorksheetData] = []
                for sheet_name, frame in frames.items():
                    worksheet = self._create_worksheet_from_frame(workbook, str(sheet_name), frame)
                    worksheets.append(worksheet)
                workbook.worksheets = [worksheet.key for worksheet in worksheets]
                return workbook, worksheets
            if suffix == ".json":
                frame = pd.read_json(path)
                workbook = self._build_workbook_shell(path)
                worksheet = self._create_worksheet_from_frame(workbook, path.stem, frame)
                workbook.worksheets = [worksheet.key]
                return workbook, [worksheet]
            if suffix == ".vsm-hys-data":
                try:
                    text = path.read_text(errors="ignore").splitlines()
                except Exception:
                    text = []
                frame = pd.DataFrame({"value": text})
                workbook = self._build_workbook_shell(path)
                worksheet = self._create_worksheet_from_frame(workbook, path.stem, frame)
                workbook.worksheets = [worksheet.key]
                return workbook, [worksheet]
            if suffix == ".vsm-tscn-data":
                try:
                    text = path.read_text(errors="ignore").splitlines()
                except Exception:
                    text = []
                frame = pd.DataFrame({"value": text})
                workbook = self._build_workbook_shell(path)
                worksheet = self._create_worksheet_from_frame(workbook, path.stem, frame)
                workbook.worksheets = [worksheet.key]
                return workbook, [worksheet]
            if suffix == ".vsm-vir-data":
                try:
                    text = path.read_text(errors="ignore").splitlines()
                except Exception:
                    text = []
                frame = pd.DataFrame({"value": text})
                workbook = self._build_workbook_shell(path)
                worksheet = self._create_worksheet_from_frame(workbook, path.stem, frame)
                workbook.worksheets = [worksheet.key]
                return workbook, [worksheet]
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "Import Data",
                f"Failed to import {path.name}:\n{exc}",
            )
            return None
        QtWidgets.QMessageBox.information(
            self,
            "Import Data",
            f"Skipping unsupported file type: {path.name}",
        )
        return None

    def _read_delimited_file(self, path: Path, suffix: str) -> pd.DataFrame | None:
        try:
            if suffix == ".tsv":
                frame = pd.read_csv(path, sep="\t")
            elif suffix == ".csv":
                frame = pd.read_csv(path)
            else:
                frame = pd.read_csv(path, sep=None, engine="python")
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame({path.name: []})
        except pd.errors.ParserError:
            try:
                frame = pd.read_csv(path, header=None, engine="python")
            except Exception:
                frame = pd.DataFrame()
        except Exception:
            frame = pd.DataFrame()
        if frame is None or frame.empty:
            try:
                lines = path.read_text(errors="ignore").splitlines()
            except Exception:
                lines = []
            frame = pd.DataFrame({"value": lines})
        return frame

    def _build_workbook_shell(self, path: Path) -> WorkbookData:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        folder = resolved.parent if resolved.parent != resolved else resolved
        key = self._workbook_key(resolved)
        return WorkbookData(
            key=key,
            name=resolved.stem,
            source=resolved,
            folder=folder,
        )

    def _workbook_key(self, path: Path) -> str:
        return str(path)

    def _worksheet_key(self, workbook_key: Hashable, sheet_name: str) -> str:
        clean = sheet_name.strip() or "Sheet"
        return f"{workbook_key}::{clean}"

    def _create_worksheet_from_frame(
        self,
        workbook: WorkbookData,
        sheet_name: str,
        frame: pd.DataFrame,
    ) -> WorksheetData:
        data = frame.copy()
        data.columns = [str(column) for column in data.columns]
        key = self._worksheet_key(workbook.key, sheet_name)
        columns_meta = {
            column: WorksheetColumnMeta(long_name=column) for column in data.columns
        }
        worksheet = WorksheetData(
            key=key,
            name=sheet_name.strip() or "Sheet",
            dataframe=data,
            columns=columns_meta,
            source=workbook.source,
            workbook_key=workbook.key,
        )
        return worksheet

    def _register_imported_workbook(
        self,
        workbook: WorkbookData,
        worksheets: List[WorksheetData],
    ) -> None:
        self._ensure_data_root()
        with self._suspend_project_tree_updates():
            # Remove any previous representation of this workbook.
            previous = self._workbooks.get(workbook.key)
            if previous is not None:
                for key in list(previous.worksheets):
                    self._remove_worksheet(key)
            old_item = self._data_workbook_items.pop(workbook.key, None)
            if old_item is not None:
                parent = old_item.parent()
                if parent is not None:
                    index = parent.indexOfChild(old_item)
                    if index >= 0:
                        parent.takeChild(index)
            self._workbooks[workbook.key] = workbook

            if workbook.source is None:
                parent_item = self._ensure_workbook_root()
            else:
                parent_item = self._ensure_folder_item(workbook.folder)

            workbook_source_text = str(workbook.source) if workbook.source is not None else ""
            workbook_detail = (
                self._compact_path_text(workbook.source, max_parts=2)
                if workbook.source is not None
                else ""
            )
            workbook_item = QtWidgets.QTreeWidgetItem([workbook.name, workbook_detail])
            self._set_tree_item_text(
                workbook_item,
                name=workbook.name,
                details=workbook_detail,
                details_tooltip=workbook_source_text,
            )
            self._assign_project_payload(workbook_item, ("worksheet_group", workbook.key))
            parent_item.addChild(workbook_item)
            workbook_item.setExpanded(True)
            self._data_workbook_items[workbook.key] = workbook_item

            for worksheet in worksheets:
                worksheet.workbook_key = workbook.key
                self._worksheets[worksheet.key] = worksheet
                sheet_item = QtWidgets.QTreeWidgetItem([worksheet.name, ""])
                self._set_tree_item_text(sheet_item, name=worksheet.name, details="")
                self._assign_project_payload(sheet_item, ("worksheet", worksheet.key))
                workbook_item.addChild(sheet_item)
                self._worksheet_tree_items[worksheet.key] = sheet_item

        self._sync_shared_action_states()

    def _remove_imported_workbook(self, key: Hashable) -> None:
        if key in self._shared_plot_workbook_keys:
            self._shared_plot_workbook_keys.discard(key)
            for tab, workbook_key in list(self._shared_plot_workbook_by_tab.items()):
                if workbook_key == key:
                    self._shared_plot_workbook_by_tab.pop(tab, None)
        workbook = self._workbooks.get(key)
        if workbook is None:
            return
        folder_item = None
        item = self._data_workbook_items.pop(key, None)
        if item is not None:
            if not self._is_live_tree_item(item):
                item = None
            else:
                try:
                    folder_item = item.parent()
                except RuntimeError:
                    folder_item = None
            if folder_item is not None and not self._is_live_tree_item(folder_item):
                folder_item = None
        for sheet_key in list(workbook.worksheets):
            self._remove_worksheet(sheet_key)
        with self._suspend_project_tree_updates():
            if item is not None and folder_item is not None:
                try:
                    index = folder_item.indexOfChild(item)
                except RuntimeError:
                    index = -1
                if index >= 0:
                    try:
                        folder_item.takeChild(index)
                    except RuntimeError:
                        pass
            # Drop empty folder items from Imported Data
            if folder_item is not None and folder_item is not self._data_tree_root:
                try:
                    child_count = folder_item.childCount()
                except RuntimeError:
                    child_count = -1
                if child_count == 0:
                    try:
                        parent = folder_item.parent()
                    except RuntimeError:
                        parent = None
                    if parent is not None:
                        try:
                            idx = parent.indexOfChild(folder_item)
                        except RuntimeError:
                            idx = -1
                        if idx >= 0:
                            try:
                                parent.takeChild(idx)
                            except RuntimeError:
                                pass
                    resolved = None
                    for path, candidate in list(self._data_folder_items.items()):
                        if candidate is folder_item:
                            resolved = path
                            break
                    if resolved is not None:
                        self._data_folder_items.pop(resolved, None)
        self._workbooks.pop(key, None)
        self._remove_data_root_if_empty()
        self._sync_shared_action_states()
        self._mark_project_dirty()

    def _append_log(self, message: str, *, level: Literal["info", "error"] = "info") -> None:
        view = getattr(self, "log_view", None)
        if isinstance(view, QtWidgets.QPlainTextEdit):
            view.appendPlainText(message)
            try:
                view.ensureCursorVisible()
            except Exception:
                pass

        if getattr(self, "_log_capture_enabled", False):
            self._append_log_to_file(level, message)

        dock = getattr(self, "log_dock", None)
        view_visible = isinstance(view, QtWidgets.QPlainTextEdit) and view.isVisible()
        dock_visible = isinstance(dock, QtWidgets.QDockWidget) and dock.isVisible()

        lowered = message.lower()
        effective_level = level
        if "skip" in lowered and effective_level != "error":
            effective_level = "error"

        if effective_level == "error" and not (view_visible and dock_visible):
            self._log_has_unread_errors = True
        elif view_visible and dock_visible:
            self._log_has_unread_errors = False

        self._set_log_alert(self._log_has_unread_errors)

    def _set_log_alert(self, enabled: bool) -> None:
        if self._log_alert_enabled == enabled:
            return
        self._log_alert_enabled = enabled
        dock = getattr(self, "log_dock", None)
        if isinstance(dock, QtWidgets.QDockWidget):
            try:
                dock.setStyleSheet("color: #b3261e; font-weight: 600;" if enabled else "")
            except Exception:
                pass
        switcher = getattr(self, "_left_dock_switcher_widget", None)
        if isinstance(switcher, _DockSwitcherWidget):
            switcher.set_tab_alert(dock, enabled)

    def _clear_log_alert(self) -> None:
        if not getattr(self, "log_dock", None):
            return
        if not self._log_has_unread_errors and not self._log_alert_enabled:
            return
        self._log_has_unread_errors = False
        self._set_log_alert(False)

    def _handle_log_capture_changed(self, enabled: bool) -> None:
        self._log_capture_enabled = bool(enabled)
        if self._log_capture_enabled:
            self._log_capture_path = self._resolve_log_capture_path()
            self._log_capture_faulted = False
            try:
                self._append_log(f"Message log capture enabled: {self._log_capture_path}")
            except Exception:
                pass
        else:
            try:
                self._append_log("Message log capture disabled.")
            except Exception:
                pass

    def _resolve_log_capture_path(self) -> Path:
        try:
            repo_root = Path(__file__).resolve().parents[2]
        except Exception:
            repo_root = Path.cwd()
        return repo_root / "logs" / "message_log.txt"

    def _append_log_to_file(self, level: str, message: str) -> None:
        if not self._log_capture_enabled or self._log_capture_faulted:
            return
        if self._log_capture_path is None:
            self._log_capture_path = self._resolve_log_capture_path()
        path = self._log_capture_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            level_name = "ERROR" if level == "error" else "INFO"
            timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            append_text_with_rotation(path, f"{timestamp} [{level_name}] {message}\n")
        except Exception as exc:
            self._log_capture_faulted = True
            self._log_capture_enabled = False
            try:
                view = getattr(self, "log_view", None)
                if isinstance(view, QtWidgets.QPlainTextEdit):
                    view.appendPlainText(
                        f"WARNING: Failed to write message log capture file: {exc}"
                    )
            except Exception:
                pass

    @staticmethod
    def _compact_path_text(
        path_or_self: Path | str | None | Any,
        path: Path | str | None = None,
        *,
        max_parts: int = 2,
    ) -> str:
        # Backward compatible with both static-style calls:
        #   _compact_path_text(path, max_parts=2)
        # and accidentally bound calls:
        #   self._compact_path_text(path, max_parts=2)
        candidate_input = path_or_self if path is None else path
        if candidate_input is None:
            return ""
        try:
            candidate = Path(candidate_input)
        except Exception:
            return str(candidate_input)
        try:
            parts = list(candidate.parts)
        except Exception:
            return str(candidate)
        if len(parts) <= max_parts:
            return str(candidate)
        tail = "/".join(parts[-max_parts:])
        return f".../{tail}"

    def _select_directories(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        title: str,
        start_dir: Path | str,
    ) -> list[str]:
        # Backward compatible with both invocation styles:
        #   self._select_directories(self, title=..., start_dir=...)
        #   self._select_directories(title=..., start_dir=...)
        if parent is None and isinstance(self, QtWidgets.QWidget):
            parent = self
        selected: list[str] = []
        seen: set[str] = set()
        current_dir = Path(start_dir)
        while True:
            entry = QtWidgets.QFileDialog.getExistingDirectory(
                parent,
                title,
                str(current_dir),
                QtWidgets.QFileDialog.Option.ShowDirsOnly,
            )
            if not entry:
                break
            candidate = Path(entry)
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if not resolved.is_dir():
                break
            key = str(resolved)
            if key not in seen:
                seen.add(key)
                selected.append(key)
            current_dir = resolved
            add_more = QtWidgets.QMessageBox.question(
                parent,
                title,
                "Add another folder?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if add_more != QtWidgets.QMessageBox.StandardButton.Yes:
                break
        return selected

    def _set_tree_item_text(
        self,
        item: QtWidgets.QTreeWidgetItem,
        name: str | None = None,
        details: str = "",
        name_tooltip: str | None = None,
        details_tooltip: str | None = None,
    ) -> None:
        # Backward-compatible signature:
        # older call sites may pass `name` positionally while newer code uses
        # keywords for readability.
        text_name = str(item.text(0) if name is None else name)
        item.setText(0, text_name)
        item.setText(1, str(details))
        item.setToolTip(0, name_tooltip if name_tooltip is not None else text_name)
        item.setToolTip(1, details_tooltip if details_tooltip is not None else str(details))

    @staticmethod
    def _is_live_tree_item(item: QtWidgets.QTreeWidgetItem | None) -> bool:
        if item is None:
            return False
        try:
            item.text(0)
        except RuntimeError:
            return False
        return True

    def _ensure_data_root(self) -> QtWidgets.QTreeWidgetItem:
        if not self._is_live_tree_item(self._data_tree_root):
            self._data_tree_root = None
        if self._data_tree_root is None:
            root = QtWidgets.QTreeWidgetItem(["Imported Data", ""])
            self._set_tree_item_text(root, name="Imported Data", details="")
            root.setExpanded(True)
            self.project_tree.addTopLevelItem(root)
            self._data_tree_root = root
        return self._data_tree_root

    def _remove_data_root_if_empty(self) -> None:
        root = self._data_tree_root
        if not self._is_live_tree_item(root):
            self._data_tree_root = None
            return
        if root.childCount() > 0:
            return
        tree = self.project_tree
        if isinstance(tree, QtWidgets.QTreeWidget):
            with self._suspend_project_tree_updates():
                index = tree.indexOfTopLevelItem(root)
                if index >= 0:
                    tree.takeTopLevelItem(index)
        self._data_tree_root = None

    def _ensure_workbook_root(self) -> QtWidgets.QTreeWidgetItem:
        if not self._is_live_tree_item(self._workbook_tree_root):
            self._workbook_tree_root = None
        if self._workbook_tree_root is None:
            root = QtWidgets.QTreeWidgetItem(["Workbooks", ""])
            self._set_tree_item_text(root, name="Workbooks", details="")
            root.setExpanded(True)
            tree = self.project_tree
            if isinstance(tree, QtWidgets.QTreeWidget):
                insert_index = 0
                if self._data_tree_root is not None:
                    index = tree.indexOfTopLevelItem(self._data_tree_root)
                    if index > 0:
                        insert_index = index
                tree.insertTopLevelItem(insert_index, root)
            self._workbook_tree_root = root
        return self._workbook_tree_root

    def _remove_workbook_root_if_empty(self) -> None:
        root = self._workbook_tree_root
        if not self._is_live_tree_item(root):
            self._workbook_tree_root = None
            return
        if root.childCount() > 0:
            return
        tree = self.project_tree
        if isinstance(tree, QtWidgets.QTreeWidget):
            with self._suspend_project_tree_updates():
                index = tree.indexOfTopLevelItem(root)
                if index >= 0:
                    tree.takeTopLevelItem(index)
        self._workbook_tree_root = None

    def _ensure_folder_item(self, folder: Path | None) -> QtWidgets.QTreeWidgetItem:
        root = self._ensure_data_root()
        if folder is None:
            return root
        try:
            resolved = folder.resolve()
        except Exception:
            resolved = folder
        item = self._data_folder_items.get(resolved)
        if item is not None and not self._is_live_tree_item(item):
            self._data_folder_items.pop(resolved, None)
            item = None
        if item is None:
            label = resolved.name or str(resolved)
            detail = self._compact_path_text(resolved, max_parts=3)
            item = QtWidgets.QTreeWidgetItem([label, detail])
            self._set_tree_item_text(
                item,
                name=label,
                details=detail,
                details_tooltip=str(resolved),
            )
            self._assign_project_payload(item, ("worksheet_group", resolved))
            root.addChild(item)
            item.setExpanded(True)
            self._data_folder_items[resolved] = item
        return item

    def _remove_worksheet(self, key: Hashable) -> None:
        with self._suspend_project_tree_updates():
            tab = self._worksheet_tabs_open.pop(key, None)
            if tab is not None:
                self._tab_to_worksheet_key.pop(tab, None)
                index = self.tab_widget.indexOf(tab)
                if index >= 0:
                    self.tab_widget.removeTab(index)
            item = self._worksheet_tree_items.pop(key, None)
            if item is not None:
                if not self._is_live_tree_item(item):
                    item = None
                parent = None
                if item is not None:
                    try:
                        parent = item.parent()
                    except RuntimeError:
                        parent = None
                if parent is not None and not self._is_live_tree_item(parent):
                    parent = None
                if parent is not None:
                    try:
                        index = parent.indexOfChild(item)
                    except RuntimeError:
                        index = -1
                    if index >= 0:
                        try:
                            parent.takeChild(index)
                        except RuntimeError:
                            pass
            self._worksheets.pop(key, None)
            self._worksheet_models.pop(key, None)
        self._sync_shared_action_states()
        self._mark_project_dirty()

    def _refresh_imported_data_summary(self) -> None:
        for key, item in self._worksheet_tree_items.items():
            worksheet = self._worksheets.get(key)
            if worksheet is None:
                continue
            rows, columns = worksheet.dataframe.shape
            detail = f"{rows} x {columns}"
            source_tooltip = str(worksheet.source) if worksheet.source is not None else detail
            self._set_tree_item_text(
                item,
                name=worksheet.name,
                details=detail,
                details_tooltip=source_tooltip,
            )
        for key, item in self._data_workbook_items.items():
            workbook = self._workbooks.get(key)
            if workbook is None:
                continue
            count = len(workbook.worksheets)
            if workbook.source is not None:
                compact_source = self._compact_path_text(workbook.source, max_parts=2)
                detail = f"{compact_source} ({count} sheet{'s' if count != 1 else ''})"
                tooltip = f"{workbook.source} ({count} sheet{'s' if count != 1 else ''})"
            else:
                detail = f"{count} sheet{'s' if count != 1 else ''}"
                tooltip = detail
            self._set_tree_item_text(
                item,
                name=workbook.name,
                details=detail,
                details_tooltip=tooltip,
            )
        self._remove_data_root_if_empty()

    def _create_worksheet_tab(
        self,
        worksheet: WorksheetData,
        model: WorksheetTableModel | None = None,
    ) -> QtWidgets.QWidget | None:
        if model is None:
            model = self._worksheet_models.get(worksheet.key)
            if model is None:
                model = WorksheetTableModel(worksheet, self)
                self._worksheet_models[worksheet.key] = model
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        container.setMinimumSize(960, 640)

        view = WorksheetTableView()
        view.setModel(model)
        view.setAlternatingRowColors(True)
        view.horizontalHeader().setStretchLastSection(True)
        header = view.verticalHeader()
        header.setVisible(True)
        for row_index in range(len(WorksheetTableModel.METADATA_FIELDS)):
            header.setSectionResizeMode(
                row_index, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
            )
        header.setDefaultSectionSize(max(22, header.defaultSectionSize()))
        layout.addWidget(view, 1)
        container._worksheet_view = view  # type: ignore[attr-defined]
        container._worksheet_model = model  # type: ignore[attr-defined]
        selection_model = view.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(lambda *_: self._update_worksheet_actions())
        self._update_worksheet_actions()
        return container

    def _extend_menus(self, menu_bar: QtWidgets.QMenuBar) -> None:
        """Allow subclasses to customise the main menu."""

        edit_menu = None
        for action in menu_bar.actions():
            menu = action.menu()
            if menu is not None and menu.objectName() == "mw_shared_edit":
                edit_menu = menu
                break
        if edit_menu is None:
            edit_menu = QtWidgets.QMenu("&Edit", menu_bar)
            edit_menu.setObjectName("mw_shared_edit")
            menu_bar.addMenu(edit_menu)
        self._install_history_actions(edit_menu)
        self._install_view_dock_actions(menu_bar)
        self._reorder_shared_menus(menu_bar)

    def _install_view_dock_actions(self, menu_bar: QtWidgets.QMenuBar) -> None:
        if getattr(self, "_dock_view_actions_added", False):
            return
        view_menu = None
        for action in menu_bar.actions():
            menu = action.menu()
            if menu is not None and menu.objectName() == "mw_shared_view":
                view_menu = menu
                break
        if view_menu is None:
            return
        docks = [
            ("Project Explorer", getattr(self, "project_dock", None)),
            ("Object Manager", getattr(self, "object_dock", None)),
            ("Message Log", getattr(self, "log_dock", None)),
        ]
        actions: list[QtGui.QAction] = []
        for label, dock in docks:
            if not isinstance(dock, QtWidgets.QDockWidget):
                continue
            action = dock.toggleViewAction()
            action.setText(label)
            actions.append(action)
        if not actions:
            return
        view_menu.addSeparator()
        for action in actions:
            view_menu.addAction(action)
        self._dock_view_actions_added = True

    def _reorder_shared_menus(self, menu_bar: QtWidgets.QMenuBar) -> None:
        desired = [
            "mw_shared_file",
            "mw_shared_edit",
            "mw_shared_view",
            "mw_shared_window",
            "mw_shared_developer",
            "mw_shared_help",
            "mw_shared_data",
        ]
        actions: dict[str, QtGui.QAction] = {}
        for action in list(menu_bar.actions()):
            menu = action.menu()
            if menu is not None:
                name = menu.objectName()
                if name in desired:
                    actions[name] = action
                    menu_bar.removeAction(action)
        for name in desired:
            action = actions.get(name)
            if action is not None:
                menu_bar.addAction(action)

    def _install_history_actions(self, edit_menu: QtWidgets.QMenu) -> None:
        undo_action = edit_menu.addAction("Undo")
        redo_action = edit_menu.addAction("Redo")
        if undo_action is not None:
            try:
                undo_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Undo))
            except Exception:
                pass
            undo_action.triggered.connect(self.undo)
            undo_action.setEnabled(False)
            self._undo_action = undo_action
        if redo_action is not None:
            try:
                redo_action.setShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Redo))
            except Exception:
                pass
            redo_action.triggered.connect(self.redo)
            redo_action.setEnabled(False)
            self._redo_action = redo_action

    def _after_base_ui_created(
        self,
        *,
        project_dock: QtWidgets.QDockWidget,
        log_dock: QtWidgets.QDockWidget,
        graph_dock: QtWidgets.QDockWidget | None,
        graph_panel: QtWidgets.QWidget | None,
    ) -> None:
        """Hook invoked once base dock widgets have been created."""
        _ = (project_dock, log_dock, graph_dock, graph_panel)

    # ------------------------------------------------------------------ project helpers
    def _default_project_filename(self) -> str:
        """Return a suggested project filename based on the plotter and current date."""

        plugin_name = getattr(self, "_current_plotter_name", None)
        if not plugin_name:
            base_title = getattr(self, "_base_title", "") or "pyplot"
            plugin_name = base_title.strip() or "pyplot"
        sanitized = (
            plugin_name
            .replace("—", " ")
            .replace("-", " ")
        )
        parts = [segment for segment in sanitized.replace("/", " ").split() if segment]
        prefix = " ".join(parts) if parts else "pyplot"
        extension = getattr(self, "PROJECT_EXTENSION", "")
        ext = extension if isinstance(extension, str) else ""
        if ext and not ext.startswith("."):
            ext = f".{ext}"
        date_stamp = datetime.date.today().strftime("%Y-%m-%d")
        return f"{prefix} {date_stamp}{ext}"

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if not self._confirm_close_with_unsaved_data():
            event.ignore()
            return
        handler = getattr(self, "_message_log_handler", None)
        if isinstance(handler, logging.Handler):
            try:
                logging.getLogger().removeHandler(handler)
            except Exception:
                pass
        self._store_side_panel_state()
        super().closeEvent(event)

    def _confirm_close_with_unsaved_data(self) -> bool:
        if not self._project_dirty or not self._has_project_data_to_save():
            return True
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Close PyPlot window?")
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Question)
        dialog.setText("Save this PyPlot session before closing?")
        dialog.setInformativeText(
            "Choose \"Save project\" to keep your current imports, or close without saving to discard them."
        )
        save_button = dialog.addButton("Save project", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        discard_button = dialog.addButton(
            "Close without saving", QtWidgets.QMessageBox.ButtonRole.DestructiveRole
        )
        dialog.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(save_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is save_button:
            return self._save_before_close()
        if clicked is discard_button:
            return True
        return False

    def _save_before_close(self) -> bool:
        previous_dirty = self._project_dirty
        self._save_project()
        # If saving failed or was cancelled, the dirty flag stays True.
        if previous_dirty and self._project_dirty:
            return False
        return True

    def _mark_project_dirty(self) -> None:
        self._project_dirty = True

    def _clear_project_dirty(self) -> None:
        self._project_dirty = False

    def _create_dock_widget(self, title: str, object_name: str) -> QtWidgets.QDockWidget:
        dock = QtWidgets.QDockWidget(title, self)
        dock.setObjectName(object_name)
        return dock

    def _dock_switcher_supported(self) -> bool:
        enable_override = os.environ.get("MW_ENABLE_DOCK_SWITCHER", "")
        if enable_override.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        env_override = os.environ.get("MW_DISABLE_DOCK_SWITCHER", "")
        if env_override.strip().lower() in {"1", "true", "yes", "on"}:
            return False
        # Keep switchers available on all platforms unless explicitly disabled.
        return True

    def _create_dock_switcher(
        self,
        docks: Sequence[QtWidgets.QDockWidget],
        *,
        side: Literal["left", "right"],
        enable_hover: bool = True,
        enable_overlay: bool = True,
        auto_collapse: bool = True,
        initial_visible: Iterable[int] | None = None,
    ) -> QtWidgets.QDockWidget | None:
        if not docks:
            return None
        panel = QtWidgets.QDockWidget("", self)
        panel.setObjectName(f"mw_{side}_dock_switcher")
        panel.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            if side == "left"
            else QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )
        panel.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        panel.setTitleBarWidget(QtWidgets.QWidget(panel))

        pinned_suffix = self._dock_switcher_pinned_keys.get(side)
        pinned_key = self._project_settings_key(pinned_suffix) if pinned_suffix else None
        switcher = _DockSwitcherWidget(
            docks,
            side=side,
            enable_hover=enable_hover,
            enable_overlay=enable_overlay,
            auto_collapse=auto_collapse,
            parent=panel,
            settings=getattr(self, "settings", None),
            pinned_setting_key=pinned_key,
        )
        panel.setWidget(switcher)
        panel.setMinimumWidth(switcher.sizeHint().width())
        panel.setMaximumWidth(switcher.sizeHint().width())

        if initial_visible is not None:
            try:
                switcher.set_initial_visible(initial_visible)
            except Exception:
                pass

        try:
            switcher.apply_cached_pinned_state()
        except Exception:
            pass

        area = (
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            if side == "left"
            else QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(area, panel)
        reference = docks[0]
        try:
            self.splitDockWidget(panel, reference, QtCore.Qt.Orientation.Horizontal)
        except Exception:
            pass
        if side == "left":
            self._left_dock_switcher_widget = switcher
        return panel

    def _apply_initial_dock_sizes(self) -> None:
        self._apply_initial_primary_dock_size(
            getattr(self, "project_dock", None), PRIMARY_DOCK_DEFAULT_WIDTH
        )
        self._apply_initial_primary_dock_size(
            getattr(self, "object_dock", None), PRIMARY_DOCK_DEFAULT_WIDTH
        )
        log_dock = getattr(self, "log_dock", None)
        if isinstance(log_dock, QtWidgets.QDockWidget):
            width = self._load_primary_dock_width(log_dock)
            if width is None or width <= 0:
                width = max(220, PRIMARY_DOCK_MIN_WIDTH)
            self._primary_dock_widths[log_dock] = width

    def _refresh_primary_dock_layout(self) -> None:
        """Force dock sizes/visibility to refresh after import or restore."""

        if self._primary_dock_layout_refreshing:
            return
        all_docks = [
            dock
            for dock in (getattr(self, "project_dock", None), getattr(self, "object_dock", None))
            if isinstance(dock, QtWidgets.QDockWidget)
        ]
        if not all_docks:
            return
        self._primary_dock_layout_refreshing = True
        try:
            docks: list[QtWidgets.QDockWidget] = []
            widths: list[int] = []
            for dock in all_docks:
                if not self._primary_dock_should_show(dock):
                    continue
                if not dock.isVisible():
                    try:
                        dock.show()
                    except Exception:
                        continue
                base_width = self._primary_dock_widths.get(dock, 0)
                if base_width <= 0:
                    base_width = PRIMARY_DOCK_DEFAULT_WIDTH
                docks.append(dock)
                widths.append(self._primary_dock_target_width(dock, base_width))
            if not docks:
                return
            try:
                self.resizeDocks(docks, widths, QtCore.Qt.Orientation.Horizontal)
            except Exception:
                for dock, width in zip(docks, widths):
                    try:
                        dock.resize(width, dock.height())
                    except Exception:
                        continue
        finally:
            self._primary_dock_layout_refreshing = False

    def _apply_initial_primary_dock_size(
        self, dock: QtWidgets.QDockWidget | None, default_width: int
    ) -> None:
        if not isinstance(dock, QtWidgets.QDockWidget):
            return
        width = self._load_primary_dock_width(dock)
        min_width = max(PRIMARY_DOCK_MIN_WIDTH, dock.minimumWidth())
        if width is None or width <= 0:
            width = default_width
        width = max(width, min_width)
        width = self._clamp_primary_dock_width(dock, width)
        self._primary_dock_widths[dock] = width
        try:
            self.resizeDocks([dock], [width], QtCore.Qt.Orientation.Horizontal)
        except Exception:
            try:
                dock.resize(width, dock.height())
            except Exception:
                pass

    def _load_primary_dock_width(self, dock: QtWidgets.QDockWidget | None) -> int | None:
        if not isinstance(dock, QtWidgets.QDockWidget):
            return None
        key = self._primary_dock_width_key(dock)
        if key is None or not isinstance(self.settings, QtCore.QSettings):
            return None
        stored = self.settings.value(key, "")
        if stored is None:
            return None
        try:
            width = int(float(str(stored).strip()))
        except Exception:
            return None
        if width <= 0:
            return None
        width = self._clamp_primary_dock_width(dock, width)
        self._primary_dock_widths[dock] = width
        return width

    def _clamp_primary_dock_width(
        self,
        dock: QtWidgets.QDockWidget | None,
        width: int,
    ) -> int:
        if not isinstance(dock, QtWidgets.QDockWidget):
            return max(int(width), PRIMARY_DOCK_MIN_WIDTH)
        min_width = max(PRIMARY_DOCK_MIN_WIDTH, dock.minimumWidth())
        clamped = max(int(width), min_width)
        if dock.objectName() not in {"projectExplorerDock", "objectManagerDock"}:
            return clamped
        window_width = max(
            self.width(),
            self.size().width(),
            PRIMARY_DOCK_DEFAULT_WIDTH * 4,
        )
        max_width = int(window_width * PRIMARY_DOCK_MAX_FRACTION)
        if PRIMARY_DOCK_EXPANDED_MAX > 0:
            max_width = min(max_width, PRIMARY_DOCK_EXPANDED_MAX)
        max_width = max(max_width, min_width)
        return min(clamped, max_width)

    def _primary_dock_target_width(
        self,
        dock: QtWidgets.QDockWidget | None,
        default_width: int,
    ) -> int:
        if not isinstance(dock, QtWidgets.QDockWidget):
            return default_width
        width = self._load_primary_dock_width(dock)
        if width is None:
            width = self._primary_dock_widths.get(dock, 0)
        if width <= 0:
            width = default_width
        width = max(width, PRIMARY_DOCK_MIN_WIDTH)
        if dock.objectName() in {"projectExplorerDock", "objectManagerDock"}:
            window_width = max(self.width(), self.size().width(), PRIMARY_DOCK_DEFAULT_WIDTH * 4)
            if window_width >= PRIMARY_DOCK_EXPAND_THRESHOLD:
                expanded_min = int(window_width * PRIMARY_DOCK_EXPANDED_FRACTION)
                if PRIMARY_DOCK_EXPANDED_MAX > 0:
                    expanded_min = min(expanded_min, PRIMARY_DOCK_EXPANDED_MAX)
                width = max(width, expanded_min)

        return self._clamp_primary_dock_width(dock, width)

    def _primary_dock_visibility_key(self, dock: QtWidgets.QDockWidget | None) -> str | None:
        if not isinstance(dock, QtWidgets.QDockWidget):
            return None
        name = dock.objectName()
        suffix = self._primary_dock_visibility_keys.get(name)
        if not suffix:
            return None
        return self._project_settings_key(suffix)

    def _primary_dock_width_key(self, dock: QtWidgets.QDockWidget | None) -> str | None:
        if not isinstance(dock, QtWidgets.QDockWidget):
            return None
        name = dock.objectName()
        suffix = self._primary_dock_width_keys.get(name)
        if not suffix:
            return None
        return self._project_settings_key(suffix)

    def _primary_dock_should_show(self, dock: QtWidgets.QDockWidget | None) -> bool:
        key = self._primary_dock_visibility_key(dock)
        if key is None:
            return True
        value = self.settings.value(key, "")
        if isinstance(value, str):
            return value.lower() not in {"0", "false", "no"}
        if isinstance(value, (int, float, bool)):
            return bool(value)
        return True

    def _remember_primary_dock_state(
        self, dock: QtWidgets.QDockWidget | None, *, visible: bool
    ) -> None:
        key = self._primary_dock_visibility_key(dock)
        if key is None:
            return
        try:
            self.settings.setValue(key, "true" if visible else "false")
        except Exception:
            pass

    def _remember_primary_dock_width(self, dock: QtWidgets.QDockWidget | None) -> None:
        if not isinstance(dock, QtWidgets.QDockWidget):
            return
        key = self._primary_dock_width_key(dock)
        if key is None or not isinstance(self.settings, QtCore.QSettings):
            return
        width = dock.width()
        if width <= 0:
            width = self._primary_dock_widths.get(dock, 0)
        if width <= 0:
            return
        width = self._clamp_primary_dock_width(dock, width)
        self._primary_dock_widths[dock] = width
        try:
            self.settings.setValue(key, width)
        except Exception:
            pass

    def _store_side_panel_state(self) -> None:
        if not isinstance(self.settings, QtCore.QSettings):
            return
        for dock in (
            getattr(self, "project_dock", None),
            getattr(self, "log_dock", None),
            getattr(self, "object_dock", None),
        ):
            self._remember_primary_dock_width(dock)
        try:
            self.settings.sync()
        except Exception:
            pass

    def _restore_primary_dock_states(self) -> None:
        for dock in (getattr(self, "project_dock", None), getattr(self, "object_dock", None)):
            if not isinstance(dock, QtWidgets.QDockWidget):
                continue
            should_show = self._primary_dock_should_show(dock)
            try:
                dock.setFloating(False)
            except Exception:
                pass
            if should_show:
                try:
                    dock.show()
                except Exception:
                    pass
            else:
                try:
                    dock.hide()
                except Exception:
                    pass
            self._remember_primary_dock_state(dock, visible=should_show)

    def _ensure_primary_docks_pinned(self) -> None:
        """Keep the primary explorers docked when they are supposed to be visible."""
        for dock in (getattr(self, "project_dock", None), getattr(self, "object_dock", None)):
            if not isinstance(dock, QtWidgets.QDockWidget):
                continue
            if not self._primary_dock_should_show(dock):
                continue
            try:
                if dock.isFloating():
                    dock.setFloating(False)
            except Exception:
                pass
            try:
                dock.show()
                dock.raise_()
            except Exception:
                pass

    def _pin_primary_dock_switchers(self) -> None:
        panels = getattr(self, "_dock_switcher_panels", [])
        if not panels:
            return
        self._pin_dock_switcher_tab(panels[0], 0)
        if len(panels) > 1:
            self._pin_dock_switcher_tab(panels[1], 0)

    def _pin_dock_switcher_tab(
        self,
        panel: QtWidgets.QDockWidget | None,
        index: int,
    ) -> None:
        if not isinstance(panel, QtWidgets.QDockWidget):
            return
        switcher = panel.widget()
        if not isinstance(switcher, _DockSwitcherWidget):
            return
        try:
            switcher._update_pinned_index(index, persist=False)
        except Exception:
            pass
        try:
            switcher._activate_index(index)
        except Exception:
            pass

    def _handle_primary_dock_location_change(
        self,
        dock: QtWidgets.QDockWidget,
        area: QtCore.Qt.DockWidgetArea,
    ) -> None:
        if getattr(self, "_retabbing_docks", False):
            return
        if area in (
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea,
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
        ):
            self._queue_retabify_primary_docks()

    def _handle_primary_dock_visibility_changed(
        self, dock: QtWidgets.QDockWidget
    ) -> None:
        if getattr(self, "_retabbing_docks", False):
            return
        _ = dock
        self._remember_primary_dock_state(dock, visible=dock.isVisible())
        if dock.isVisible():
            self._queue_retabify_primary_docks()
            return
        self._remember_primary_dock_width(dock)

    def _queue_retabify_primary_docks(self) -> None:
        if getattr(self, "_retabify_pending", False):
            return
        self._retabify_pending = True
        QtCore.QTimer.singleShot(0, self._run_queued_retabify)

    def _run_queued_retabify(self) -> None:
        self._retabify_pending = False
        self._retabify_primary_docks()

    def _retabify_primary_docks(self) -> None:
        if getattr(self, "_retabbing_docks", False):
            return
        self._retabify_pending = False
        self._retabbing_docks = True
        try:
            project_dock = getattr(self, "project_dock", None)
            log_dock = getattr(self, "log_dock", None)
            graph_dock = getattr(self, "graph_dock", None)
            object_dock = getattr(self, "object_dock", None)

            tracked_widths: Dict[QtWidgets.QDockWidget, int] = {}
            for candidate in (project_dock, log_dock, graph_dock, object_dock):
                if isinstance(candidate, QtWidgets.QDockWidget):
                    stored_width = self._primary_dock_widths.get(candidate, 0)
                    if stored_width <= 0:
                        continue
                    if not candidate.isVisible() or candidate.isFloating():
                        continue
                    tracked_widths[candidate] = stored_width

            left_switcher = next(
                (
                    panel
                    for panel in getattr(self, "_dock_switcher_panels", [])
                    if isinstance(panel, QtWidgets.QDockWidget)
                    and panel.objectName() == "mw_left_dock_switcher"
                ),
                None,
            )
            right_switcher = next(
                (
                    panel
                    for panel in getattr(self, "_dock_switcher_panels", [])
                    if isinstance(panel, QtWidgets.QDockWidget)
                    and panel.objectName() == "mw_right_dock_switcher"
                ),
                None,
            )

            if (
                isinstance(project_dock, QtWidgets.QDockWidget)
                and project_dock.isVisible()
                and not project_dock.isFloating()
            ):
                if isinstance(left_switcher, QtWidgets.QDockWidget):
                    try:
                        self.splitDockWidget(
                            left_switcher,
                            project_dock,
                            QtCore.Qt.Orientation.Horizontal,
                        )
                    except Exception:
                        pass
                    else:
                        panel = left_switcher.widget()
                        if isinstance(panel, _DockSwitcherWidget):
                            panel.mark_tabbed(project_dock)
                try:
                    project_dock.raise_()
                except Exception:
                    pass

            if (
                isinstance(right_switcher, QtWidgets.QDockWidget)
                and isinstance(object_dock, QtWidgets.QDockWidget)
                and object_dock.isVisible()
                and not object_dock.isFloating()
            ):
                try:
                    self.splitDockWidget(
                        object_dock,
                        right_switcher,
                        QtCore.Qt.Orientation.Horizontal,
                    )
                except Exception:
                    pass
                try:
                    right_switcher.raise_()
                except Exception:
                    pass
                panel = right_switcher.widget()
                if isinstance(panel, _DockSwitcherWidget):
                    panel.mark_tabbed(object_dock)

            for dock, width in tracked_widths.items():
                width = self._clamp_primary_dock_width(dock, width)
                self._primary_dock_widths[dock] = width
                self._apply_dock_width(dock, width)
        finally:
            self._retabbing_docks = False

    def _apply_dock_width(self, dock: QtWidgets.QDockWidget, width: int) -> None:
        if not isinstance(dock, QtWidgets.QDockWidget) or dock.isFloating() or width <= 0:
            return
        width = self._clamp_primary_dock_width(dock, width)
        try:
            screen = QtGui.QGuiApplication.screenAt(dock.mapToGlobal(dock.rect().center()))
            if screen is None:
                screen = QtGui.QGuiApplication.primaryScreen()
            available = screen.availableGeometry() if screen is not None else None
        except Exception:
            available = None
        if available is not None:
            width = max(120, min(width, available.width()))

        pointer = _make_qpointer(dock)

        def _resize() -> None:
            dock_widget = _deref_qpointer(pointer)
            if not isinstance(dock_widget, QtWidgets.QDockWidget):
                return
            if dock_widget.isFloating():
                return
            try:
                dock_widget.resize(width, dock_widget.height() or dock_widget.sizeHint().height())
            except Exception:
                pass
            try:
                self.resizeDocks([dock_widget], [width], QtCore.Qt.Orientation.Horizontal)
            except Exception:
                pass
            try:
                frame = self.frameGeometry()
                screen = QtGui.QGuiApplication.screenAt(frame.center())
                if screen is None:
                    screen = QtGui.QGuiApplication.primaryScreen()
                if screen is not None:
                    available_rect = screen.availableGeometry()
                    new_left = max(
                        available_rect.left(),
                        min(frame.left(), available_rect.right() - frame.width()),
                    )
                    new_top = max(
                        available_rect.top(),
                        min(frame.top(), available_rect.bottom() - frame.height()),
                    )
                    self.move(new_left, new_top)
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, _resize)

    # ------------------------------------------------------------------ menu helpers
    def _open_files_from_menu(self) -> None:
        self._choose_files()

    def _open_folder_from_menu(self) -> None:
        self._choose_folder()

    # ------------------------------------------------------------------ project tree helpers
    def _handle_project_tree_search_text_changed(self, text: str) -> None:
        self._project_tree_search_text = text.strip().casefold()
        self._apply_project_tree_filter()

    def _schedule_project_tree_filter_refresh(self, *_args: object) -> None:
        if self._project_tree_filter_refresh_pending:
            return
        self._project_tree_filter_refresh_pending = True
        QtCore.QTimer.singleShot(0, self._flush_project_tree_filter_refresh)

    def _flush_project_tree_filter_refresh(self) -> None:
        self._project_tree_filter_refresh_pending = False
        self._apply_project_tree_filter()

    def _apply_project_tree_filter(self) -> None:
        tree = getattr(self, "project_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            return
        query = self._project_tree_search_text
        with self._suspend_project_tree_updates():
            if not query:
                for index in range(tree.topLevelItemCount()):
                    item = tree.topLevelItem(index)
                    if item is not None:
                        self._set_project_tree_item_visibility(item, visible=True)
                return
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                if item is None:
                    continue
                has_match = self._filter_project_tree_item(item, query)
                item.setHidden(not has_match)
                if has_match and item.childCount() > 0:
                    item.setExpanded(True)

    def _set_project_tree_item_visibility(
        self,
        item: QtWidgets.QTreeWidgetItem,
        *,
        visible: bool,
    ) -> None:
        item.setHidden(not visible)
        for index in range(item.childCount()):
            child = item.child(index)
            if child is not None:
                self._set_project_tree_item_visibility(child, visible=visible)

    def _filter_project_tree_item(
        self,
        item: QtWidgets.QTreeWidgetItem,
        query: str,
    ) -> bool:
        direct_match = self._project_tree_item_matches(item, query)
        child_match = False
        for index in range(item.childCount()):
            child = item.child(index)
            if child is None:
                continue
            is_child_visible = self._filter_project_tree_item(child, query)
            child.setHidden(not is_child_visible)
            child_match = child_match or is_child_visible
            if is_child_visible and child.childCount() > 0:
                child.setExpanded(True)
        visible = direct_match or child_match
        item.setHidden(not visible)
        return visible

    def _project_tree_item_matches(
        self,
        item: QtWidgets.QTreeWidgetItem,
        query: str,
    ) -> bool:
        if not query:
            return True
        text = " ".join(
            (
                item.text(0),
                item.text(1),
                item.toolTip(0),
                item.toolTip(1),
            )
        ).casefold()
        return query in text

    def _handle_project_item_double_click(
        self,
        item: QtWidgets.QTreeWidgetItem,
        column: int,
    ) -> None:
        data = self._project_item_payload(item, column)
        if not data:
            return
        role = data[0]
        if role == "graph":
            tab = data[1]
            if isinstance(tab, QtWidgets.QWidget):
                index = self.tab_widget.indexOf(tab)
                if index >= 0:
                    self._show_tab(tab)
                else:
                    stale = self._graph_tree_items.pop(tab, None)
                    if stale is not None:
                        parent = stale.parent()
                        if parent is not None:
                            child_index = parent.indexOfChild(stale)
                            if child_index >= 0:
                                parent.takeChild(child_index)
        elif role == "worksheet":
            worksheet_key = data[1]
            if isinstance(worksheet_key, Hashable) and worksheet_key in self._worksheets:
                self._open_worksheet_tab(worksheet_key)
            elif isinstance(worksheet_key, Path):
                self._open_worksheet_tab(worksheet_key)
        elif role == "worksheet_group":
            target = data[1]
            if isinstance(target, Hashable) and target in self._workbooks:
                workbook = self._workbooks.get(target)
                if workbook is not None and workbook.worksheets:
                    self._open_worksheet_tab(workbook.worksheets[0])
                    return
            if isinstance(target, Hashable) and target in self._worksheets:
                self._open_worksheet_tab(target)
                return
            item.setExpanded(not item.isExpanded())

    def _dispatch_project_item_activation(
        self,
        item: QtWidgets.QTreeWidgetItem,
        column: int,
    ) -> None:
        try:
            self._handle_project_item_double_click(item, column)
        except Exception as exc:
            try:
                self._append_log(
                    f"ERROR: Project Explorer activation failed: {exc}",
                    level="error",
                )
                self._append_log(traceback.format_exc().rstrip(), level="error")
            except Exception:
                logging.getLogger(__name__).exception(
                    "Project Explorer activation failed"
                )

    def _handle_project_tree_current_item_changed(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        _ = previous
        if current is None or self._project_tree_selection_sync_blocked:
            return
        tree = getattr(self, "project_tree", None)
        keep_tree_focus = False
        if isinstance(tree, QtWidgets.QTreeWidget):
            focus_widget = QtWidgets.QApplication.focusWidget()
            if focus_widget is tree:
                keep_tree_focus = True
            elif isinstance(focus_widget, QtWidgets.QWidget):
                keep_tree_focus = tree.isAncestorOf(focus_widget)
        data = self._project_item_payload(current, 0)
        if not data or data[0] != "graph":
            return
        tab = data[1]
        if isinstance(tab, QtWidgets.QWidget):
            tab_index = self.tab_widget.indexOf(tab)
            if tab_index < 0:
                stale = self._graph_tree_items.pop(tab, None)
                if stale is not None:
                    parent = stale.parent()
                    if parent is not None:
                        child_index = parent.indexOfChild(stale)
                        if child_index >= 0:
                            parent.takeChild(child_index)
                return
        if isinstance(tab, QtWidgets.QWidget) and self.tab_widget.currentWidget() is not tab:
            if keep_tree_focus:
                self._project_tree_focus_restore_pending = True
            self._show_tab(tab)
            if keep_tree_focus and isinstance(tree, QtWidgets.QTreeWidget):
                self._restore_project_tree_focus(tree)

    @staticmethod
    def _restore_project_tree_focus(tree: QtWidgets.QTreeWidget) -> None:
        def _focus_tree(target: QtWidgets.QTreeWidget) -> None:
            try:
                target.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
            except Exception:
                pass
            try:
                viewport = target.viewport()
            except Exception:
                viewport = None
            if isinstance(viewport, QtWidgets.QWidget):
                try:
                    viewport.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
                except Exception:
                    pass

        _focus_tree(tree)
        for delay in (0,):
            try:
                QtCore.QTimer.singleShot(delay, lambda t=tree: _focus_tree(t))
            except Exception:
                pass
        try:
            QtCore.QTimer.singleShot(
                0,
                lambda t=tree: QtCore.QTimer.singleShot(
                    0,
                    lambda tt=t: _focus_tree(tt),
                ),
            )
        except Exception:
            pass

    def _assign_project_payload(
        self,
        item: QtWidgets.QTreeWidgetItem,
        payload: Tuple[str, Any],
    ) -> None:
        columns = max(1, item.columnCount())
        for index in range(columns):
            item.setData(index, QtCore.Qt.ItemDataRole.UserRole, payload)

    def _project_item_payload(
        self,
        item: QtWidgets.QTreeWidgetItem,
        column: int,
    ) -> Tuple[str, Any] | None:
        candidates: List[int] = []
        if column >= 0:
            candidates.append(column)
        candidates.append(0)
        seen: set[int] = set()
        for idx in candidates:
            if idx in seen or idx < 0:
                continue
            seen.add(idx)
            payload = item.data(idx, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(payload, tuple) and len(payload) >= 2:
                return cast(Tuple[str, Any], payload)
        column_count = item.columnCount()
        for idx in range(column_count):
            if idx in seen:
                continue
            payload = item.data(idx, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(payload, tuple) and len(payload) >= 2:
                return cast(Tuple[str, Any], payload)
        return None

    # Placeholder methods that subclasses may override or extend -----------------
    def _handle_project_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.project_tree.itemAt(pos)
        if item is None:
            return
        payload = self._project_item_payload(item, 0)
        if payload is None:
            return
        role, value = payload
        menu = QtWidgets.QMenu(self)
        action_added = False
        if role in {"worksheet_group", "worksheet"} and self._is_under_data_root(item):
            remove_action = menu.addAction("Remove imported data")
            action_added = True

            def _remove() -> None:
                if role == "worksheet_group":
                    self._remove_imported_workbook(value)
                elif role == "worksheet":
                    self._remove_worksheet(value)
                    self._remove_imported_workbook(self._worksheet_workbook_key(value))

            remove_action.triggered.connect(_remove)
        if not action_added:
            return
        menu.exec(self.project_tree.viewport().mapToGlobal(pos))

    def _is_under_data_root(self, item: QtWidgets.QTreeWidgetItem) -> bool:
        cursor = item
        while cursor is not None:
            if cursor is self._data_tree_root:
                return True
            cursor = cursor.parent()
        return False

    def _worksheet_workbook_key(self, worksheet_key: Hashable) -> Hashable | None:
        worksheet = self._worksheets.get(worksheet_key)
        if worksheet is None:
            return None
        return worksheet.workbook_key

    def _apply_object_item_visibility(
        self,
        item: QtWidgets.QTreeWidgetItem,
        visible: bool,
        *,
        allow_toggle: bool = True,
    ) -> None:
        try:
            flags = item.flags()
        except RuntimeError:
            return
        if allow_toggle:
            if not flags & QtCore.Qt.ItemFlag.ItemIsUserCheckable:
                item.setFlags(flags | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            state = (
                QtCore.Qt.CheckState.Checked
                if visible
                else QtCore.Qt.CheckState.Unchecked
            )
            item.setCheckState(0, state)
            item.setData(0, OBJECT_TREE_STATE_ROLE, state)
        else:
            item.setData(0, OBJECT_TREE_STATE_ROLE, QtCore.Qt.CheckState.Checked)

    def _legend_state_snapshot(
        self,
        legend: Legend | None,
        *,
        plugin_name: str | None = None,
    ) -> Dict[str, Any]:
        defaults = self._legend_settings_for(plugin_name)
        state: Dict[str, Any] = {
            "visible": True,
            "title": "",
            "font_size": 14.0,
            "loc": "best",
            "ncol": 1,
            "frame_on": True,
            "frame_alpha": 1.0,
            "borderpad": 0.4,
            "labelspacing": 0.5,
            "handlelength": 2.0,
            "handletextpad": 0.8,
            "columnspacing": 2.0,
            "markerscale": 1.0,
            "show_symbols": bool(defaults.get("show_symbols", True)),
            "text_follows_handles": bool(defaults.get("text_follows_handles", True)),
            "orientation": defaults.get("orientation", "auto"),
            "placement": defaults.get("placement", "inside"),
            "draggable": bool(defaults.get("draggable", True)),
        }
        if legend is None:
            return state

        try:
            state["visible"] = bool(legend.get_visible())
        except Exception:
            pass
        try:
            state["title"] = legend.get_title().get_text()
        except Exception:
            pass
        try:
            sample_size = next(
                (text.get_fontsize() for text in legend.get_texts()),
                legend.get_title().get_fontsize(),
            )
            state["font_size"] = float(sample_size)
        except Exception:
            pass
        try:
            loc_value = legend._get_loc()
        except Exception:
            loc_value = getattr(legend, "_loc", None)
        if isinstance(loc_value, int):
            for key, code in Legend.codes.items():
                if code == loc_value:
                    loc_value = key
                    break
        if isinstance(loc_value, str) and loc_value.strip():
            state["loc"] = loc_value
        try:
            ncol = getattr(legend, "_ncol", None)
            if isinstance(ncol, int) and ncol > 0:
                state["ncol"] = ncol
            else:
                state["ncol"] = max(1, len(_legend_handles(legend)))
        except Exception:
            pass
        try:
            frame = legend.get_frame()
            state["frame_on"] = bool(frame.get_visible())
            state["frame_alpha"] = float(frame.get_alpha() or 1.0)
        except Exception:
            pass
        for key, getter_name in (
            ("borderpad", "get_borderpad"),
            ("labelspacing", "get_labelspacing"),
            ("handlelength", "get_handlelength"),
            ("handletextpad", "get_handletextpad"),
            ("columnspacing", "get_columnspacing"),
            ("markerscale", "get_markerscale"),
        ):
            getter = getattr(legend, getter_name, None)
            if callable(getter):
                try:
                    state[key] = float(getter())
                except Exception:
                    continue
        state["show_symbols"] = bool(getattr(legend, "_mw_show_symbols", state["show_symbols"]))
        state["text_follows_handles"] = bool(
            getattr(legend, "_mw_text_follows_handles", state["text_follows_handles"])
        )
        state["orientation"] = getattr(legend, "_mw_orientation", state["orientation"])
        state["placement"] = getattr(
            legend,
            "_mw_placement",
            "outside" if getattr(legend, "_bbox_to_anchor", None) else state["placement"],
        )
        state["draggable"] = bool(
            getattr(legend, "_mw_draggable", getattr(legend, "get_draggable", lambda: True)())
        )
        return state

    def _apply_legend_snapshot(self, legend: Legend, state: Dict[str, Any]) -> None:
        def _legend_entry_count() -> int:
            handles = _legend_handles(legend)
            if handles:
                return len(handles)
            try:
                return len(list(legend.get_texts()))
            except Exception:
                return 0

        def _legend_auto_layout() -> tuple[str, int]:
            count = _legend_entry_count()
            if count <= 1:
                return "vertical", 1
            try:
                texts = [str(text.get_text() or "") for text in legend.get_texts()]
            except Exception:
                texts = []
            max_chars = max((len(text.strip()) for text in texts), default=0)
            try:
                font_px = float(state.get("font_size", 10.0))
            except Exception:
                font_px = 10.0
            if font_px <= 0:
                font_px = 10.0
            show_symbols = bool(state.get("show_symbols", True))
            symbol_px = font_px * (1.6 if show_symbols else 0.6)
            entry_px = max(font_px * max(6.0, max_chars) * 0.58 + symbol_px, font_px * 8.0)
            axes_width = 0.0
            try:
                axes = getattr(legend, "axes", None)
                if axes is not None and hasattr(axes, "bbox"):
                    axes_width = float(getattr(axes.bbox, "width", 0.0))
            except Exception:
                axes_width = 0.0
            if axes_width <= 0:
                try:
                    fig = legend.figure
                    axes_width = float(getattr(getattr(fig, "bbox", None), "width", 0.0))
                except Exception:
                    axes_width = 0.0
            horizontal_width = count * entry_px
            horizontal_ok = (
                count <= 3
                and max_chars <= 14
                and axes_width > 0
                and horizontal_width <= axes_width * 0.9
            )
            if horizontal_ok:
                return "horizontal", count
            return "vertical", 1

        try:
            legend.set_visible(bool(state.get("visible", True)))
        except Exception:
            pass
        try:
            legend.set_title(str(state.get("title", "")))
        except Exception:
            pass
        font_size = state.get("font_size")
        if isinstance(font_size, (int, float)):
            try:
                legend.set_fontsize(float(font_size))
            except Exception:
                for text in legend.get_texts():
                    try:
                        text.set_fontsize(float(font_size))
                    except Exception:
                        continue
                try:
                    legend.get_title().set_fontsize(float(font_size))
                except Exception:
                    pass
        ncol_state = state.get("ncol")
        requested_ncol = int(ncol_state) if isinstance(ncol_state, int) and ncol_state > 0 else 1
        orientation_token = str(state.get("orientation", "auto") or "auto").strip().lower()
        if orientation_token not in {"auto", "horizontal", "vertical"}:
            orientation_token = "auto"
        if orientation_token == "auto":
            _resolved_orientation, resolved_ncol = _legend_auto_layout()
        elif orientation_token == "horizontal":
            resolved_ncol = max(1, requested_ncol, _legend_entry_count())
        else:
            resolved_ncol = 1
        try:
            legend.set_ncol(int(resolved_ncol))
        except Exception:
            try:
                legend._ncol = int(resolved_ncol)
            except Exception:
                pass
        try:
            legend.set_frame_on(bool(state.get("frame_on", True)))
        except Exception:
            pass
        frame = legend.get_frame()
        alpha = state.get("frame_alpha")
        if isinstance(alpha, (int, float)):
            try:
                frame.set_alpha(float(alpha))
            except Exception:
                pass
        for key, setter_name in (
            ("borderpad", "set_borderpad"),
            ("labelspacing", "set_labelspacing"),
            ("handlelength", "set_handlelength"),
            ("handletextpad", "set_handletextpad"),
            ("columnspacing", "set_columnspacing"),
            ("markerscale", "set_markerscale"),
        ):
            setter = getattr(legend, setter_name, None)
            value = state.get(key)
            if callable(setter) and isinstance(value, (int, float)):
                try:
                    setter(float(value))
                except Exception:
                    continue

        loc_value = state.get("loc", "best")
        placement = state.get("placement", "inside")
        axes = getattr(legend, "axes", None)
        legend._mw_placement = placement
        if placement == "outside" and axes is not None:
            try:
                legend.set_bbox_to_anchor((1.02, 1.0), transform=axes.transAxes)
            except Exception:
                pass
            try:
                legend.set_loc(loc_value or "upper left")
            except Exception:
                pass
        else:
            try:
                legend.set_bbox_to_anchor(None)
            except Exception:
                legend._bbox_to_anchor = None
            try:
                legend.set_loc(loc_value)
            except Exception:
                pass

        legend._mw_orientation = orientation_token
        _set_legend_symbol_visibility(legend, bool(state.get("show_symbols", True)))
        _set_legend_text_follow_handles(
            legend,
            bool(state.get("text_follows_handles", True)),
        )

        draggable = bool(state.get("draggable", True))
        legend._mw_draggable = draggable
        try:
            legend.set_draggable(draggable)
        except Exception:
            pass

    def _sync_axes_legend_with_visible_lines(
        self,
        axes: Any,
        *,
        plugin_name: str | None = None,
    ) -> Legend | None:
        if axes is None:
            return None
        descriptor: TabDescriptor | None = None
        tab = self._tab_for_axes(axes) if hasattr(self, "_tab_for_axes") else None
        if tab is not None:
            descriptor = self._tab_descriptors.get(tab)
        metadata = descriptor.metadata if isinstance(descriptor, TabDescriptor) and isinstance(descriptor.metadata, dict) else {}
        plot_kind = str(metadata.get("plot_kind") or "").strip().lower()
        dual_axis_overlay = plot_kind == "shape_memory_dual_axis_overlay"
        aggregate_overlay_axes = False

        legend_axes = axes
        legend_sources: list[Any] = [axes]
        figure = getattr(axes, "figure", None)
        if figure is not None:
            try:
                figure_axes = [axis for axis in list(figure.axes) if axis is not None]
            except Exception:
                figure_axes = []
            overlay_axes: list[Any] = []
            if figure_axes:
                try:
                    base_bounds = tuple(float(v) for v in axes.get_position().bounds)
                except Exception:
                    base_bounds = None

                def _same_bounds(candidate: Any) -> bool:
                    if candidate is axes:
                        return True
                    if base_bounds is None:
                        return False
                    try:
                        candidate_bounds = tuple(float(v) for v in candidate.get_position().bounds)
                    except Exception:
                        return False
                    if len(candidate_bounds) != len(base_bounds):
                        return False
                    return all(abs(a - b) <= 1e-6 for a, b in zip(candidate_bounds, base_bounds))

                def _shares_base_axis(candidate: Any) -> bool:
                    if candidate is axes:
                        return True
                    try:
                        shared_x = axes.get_shared_x_axes()
                        if shared_x is not None and shared_x.joined(axes, candidate):
                            return True
                    except Exception:
                        pass
                    try:
                        shared_y = axes.get_shared_y_axes()
                        if shared_y is not None and shared_y.joined(axes, candidate):
                            return True
                    except Exception:
                        pass
                    return False

                for candidate_axis in figure_axes:
                    if not _same_bounds(candidate_axis):
                        continue
                    if not _shares_base_axis(candidate_axis):
                        continue
                    overlay_axes.append(candidate_axis)

            if dual_axis_overlay and figure_axes:
                legend_sources = figure_axes
                aggregate_overlay_axes = True
            elif len(overlay_axes) > 1:
                legend_sources = overlay_axes
                aggregate_overlay_axes = True

            if legend_sources:
                primary_axes = descriptor.axes if isinstance(descriptor, TabDescriptor) else None
                if (
                    primary_axes is not None
                    and getattr(primary_axes, "figure", None) is figure
                    and primary_axes in legend_sources
                ):
                    legend_axes = primary_axes
                else:
                    legend_axes = legend_sources[0]
                for candidate_axis in legend_sources:
                    if candidate_axis is legend_axes:
                        continue
                    try:
                        candidate_legend = candidate_axis.get_legend()
                    except Exception:
                        candidate_legend = None
                    if isinstance(candidate_legend, Legend):
                        try:
                            candidate_legend.remove()
                        except Exception:
                            pass

        old_legend: Legend | None = None
        try:
            candidate = legend_axes.get_legend()
            if isinstance(candidate, Legend):
                old_legend = candidate
        except Exception:
            old_legend = None

        if plugin_name is None:
            plugin_name = self._tab_plugin_name(descriptor)
            if plugin_name is None:
                current_tab = self.tab_widget.currentWidget() if hasattr(self, "tab_widget") else None
                plugin_name = self._tab_plugin_name(self._tab_descriptors.get(current_tab))
        state = self._legend_state_snapshot(old_legend, plugin_name=plugin_name)

        visible_lines: list[Line2D] = []
        visible_labels: list[str] = []
        seen_labels: set[str] = set()
        for source_axes in legend_sources:
            try:
                lines = list(source_axes.get_lines())
            except Exception:
                lines = []
            for line in lines:
                if not isinstance(line, Line2D):
                    continue
                try:
                    raw_label = str(line.get_label() or "")
                except Exception:
                    raw_label = ""
                if not raw_label or raw_label.startswith("_") or raw_label == "_nolegend_":
                    continue
                try:
                    is_visible = bool(line.get_visible())
                except Exception:
                    is_visible = True
                if not is_visible:
                    continue
                label = raw_label
                if dual_axis_overlay and descriptor is not None:
                    normalized = self._normalize_series_label_for_export(raw_label, descriptor=descriptor).strip()
                    if normalized:
                        label = normalized
                if aggregate_overlay_axes:
                    dedupe_key = label.casefold()
                    if dedupe_key in seen_labels:
                        continue
                    seen_labels.add(dedupe_key)
                visible_lines.append(line)
                visible_labels.append(label)

        if old_legend is not None:
            try:
                old_legend.remove()
            except Exception:
                pass
        if not visible_lines:
            return None

        loc_value = state.get("loc", "best")
        ncol = state.get("ncol", 1)
        try:
            legend = legend_axes.legend(
                visible_lines,
                visible_labels,
                loc=loc_value,
                ncol=ncol if isinstance(ncol, int) and ncol > 0 else 1,
            )
        except Exception:
            try:
                legend = legend_axes.legend(visible_lines, visible_labels)
            except Exception:
                return None
        if not isinstance(legend, Legend):
            return None
        self._apply_legend_snapshot(legend, state)
        return legend

    def _handle_object_item_changed(
        self, item: QtWidgets.QTreeWidgetItem, column: int
    ) -> None:
        if self._object_tree_updating or column != 0:
            return
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        kind = data.get("kind")
        target = data.get("object")
        if kind not in {"legend", "line", "shape"}:
            return
        new_state = item.checkState(0)
        old_state = item.data(0, OBJECT_TREE_STATE_ROLE)
        if new_state == old_state:
            return
        visible = new_state == QtCore.Qt.CheckState.Checked
        changed = False
        try:
            if kind == "line" and isinstance(target, Line2D):
                target.set_visible(visible)
                changed = True
            elif kind == "shape" and isinstance(target, (Line2D, Patch)):
                target.set_visible(visible)
                changed = True
            elif kind == "legend" and hasattr(target, "set_visible"):
                target.set_visible(visible)
                changed = True
        except Exception:
            changed = False
        if changed:
            item.setData(0, OBJECT_TREE_STATE_ROLE, new_state)
            current_tab = self.tab_widget.currentWidget() if hasattr(self, "tab_widget") else None
            if kind == "line" and isinstance(target, Line2D):
                axes = getattr(target, "axes", None)
                descriptor = self._tab_descriptors.get(current_tab)
                plugin_name = self._tab_plugin_name(descriptor)
                if axes is not None:
                    self._sync_axes_legend_with_visible_lines(
                        axes,
                        plugin_name=plugin_name,
                    )
            canvas = self._canvas_by_tab.get(self.tab_widget.currentWidget())
            if canvas is not None:
                try:
                    canvas.draw()
                except Exception:
                    try:
                        canvas.draw_idle()
                    except Exception:
                        pass
            if kind in {"line", "legend", "shape"} and current_tab is not None:
                self._rebuild_object_manager_for_tab(current_tab)
            return
        self._object_tree_updating = True
        fallback = (
            QtCore.Qt.CheckState.Checked
            if old_state == QtCore.Qt.CheckState.Checked
            else QtCore.Qt.CheckState.Unchecked
        )
        item.setCheckState(0, fallback)
        self._object_tree_updating = False

    def _handle_object_item_double_click(
        self, item: QtWidgets.QTreeWidgetItem, column: int
    ) -> None:
        if column != 0 or not isinstance(item, QtWidgets.QTreeWidgetItem):
            return
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        kind = data.get("kind")
        target = data.get("object")
        if kind == "text" and isinstance(target, Text):
            self._select_object_in_manager(target)
            self._edit_selected_text()
            return
        if kind == "shape" and isinstance(target, (Line2D, Patch)):
            self._select_object_in_manager(target)
            return
        if kind != "legend":
            return
        legend = target
        if not isinstance(legend, Legend):
            return
        legend_axes = getattr(legend, "axes", None)
        if legend_axes is not None:
            opened = self._open_shared_graph_format_from_canvas_target(
                legend_axes,
                legend=True,
            )
            if opened:
                return
        current_tab = self.tab_widget.currentWidget() if hasattr(self, "tab_widget") else None
        descriptor = self._tab_descriptors.get(current_tab)
        plugin_name = self._tab_plugin_name(descriptor)
        defaults = self._legend_settings_for(plugin_name)
        dialog = LegendSettingsDialog(
            self,
            legend,
            plugin_name=plugin_name,
            defaults=defaults,
            on_save=lambda payload: self._remember_legend_settings(plugin_name, payload),
        )
        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return
        try:
            legend_visible = bool(legend.get_visible())
        except Exception:
            legend_visible = True
        self._apply_object_item_visibility(item, legend_visible)
        try:
            title = legend.get_title().get_text() if legend.get_title() is not None else "Legend"
        except Exception:
            title = "Legend"
        item.setText(0, title or "Legend")

    def _reconstruct_legend_from_item(self, item: QtWidgets.QTreeWidgetItem) -> None:
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        if data.get("kind") != "legend":
            return
        legend = data.get("object")
        if not isinstance(legend, Legend):
            return
        axes = getattr(legend, "axes", None)
        if axes is None:
            return
        current_tab = self.tab_widget.currentWidget() if hasattr(self, "tab_widget") else None
        descriptor = self._tab_descriptors.get(current_tab)
        plugin_name = self._tab_plugin_name(descriptor)
        rebuilt = self._sync_axes_legend_with_visible_lines(
            axes,
            plugin_name=plugin_name,
        )
        if rebuilt is None:
            return
        try:
            rebuilt.set_visible(True)
        except Exception:
            pass
        canvas = self._canvas_by_tab.get(current_tab)
        if canvas is not None:
            try:
                canvas.draw()
            except Exception:
                try:
                    canvas.draw_idle()
                except Exception:
                    pass
        if current_tab is not None:
            self._rebuild_object_manager_for_tab(current_tab)

    def _handle_object_context_menu(self, pos: QtCore.QPoint) -> None:
        tree = getattr(self, "object_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            return
        item = tree.itemAt(pos)
        if not isinstance(item, QtWidgets.QTreeWidgetItem):
            return
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        menu = QtWidgets.QMenu(tree)
        kind = data.get("kind")
        settings_action = None
        reconstruct_action = None
        delete_action = None
        if kind == "legend":
            settings_action = menu.addAction("Legend settings...")
            reconstruct_action = menu.addAction("Reconstruct legend")
        elif kind in {"text", "shape"}:
            delete_action = menu.addAction("Delete")
        else:
            return

        chosen = menu.exec(tree.viewport().mapToGlobal(pos))
        if settings_action is not None and chosen is settings_action:
            self._handle_object_item_double_click(item, 0)
        elif reconstruct_action is not None and chosen is reconstruct_action:
            self._reconstruct_legend_from_item(item)
        elif delete_action is not None and chosen is delete_action:
            self._delete_graph_object_item(item)

    def _delete_graph_object_item(self, item: QtWidgets.QTreeWidgetItem) -> None:
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        kind = data.get("kind")
        target = data.get("object")
        if kind == "text" and isinstance(target, Text):
            axes = getattr(target, "axes", None)
            try:
                target.remove()
            except Exception:
                return
            self._mark_graph_artist_changed(axes)
        elif kind == "shape" and isinstance(target, (Line2D, Patch)):
            axes = getattr(target, "axes", None)
            try:
                target.remove()
            except Exception:
                return
            self._mark_graph_artist_changed(axes)

    def _dispatch_object_item_activation(
        self,
        item: QtWidgets.QTreeWidgetItem,
        column: int,
    ) -> None:
        try:
            self._handle_object_item_double_click(item, column)
        except Exception as exc:
            try:
                self._append_log(
                    f"ERROR: Object Manager activation failed: {exc}",
                    level="error",
                )
                self._append_log(traceback.format_exc().rstrip(), level="error")
            except Exception:
                logging.getLogger(__name__).exception(
                    "Object Manager activation failed"
                )

    def _rebuild_object_manager_for_tab(self, tab: QtWidgets.QWidget | None, *_: Any) -> None:
        """Rebuild the object manager tree for ``tab`` with all axes, legends, and lines."""

        tree = getattr(self, "object_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            return
        descriptor = self._tab_descriptors.get(tab) if hasattr(self, "_tab_descriptors") else None
        plugin_name = self._tab_plugin_name(descriptor)
        legend_defaults = self._legend_settings_for(plugin_name)
        tree.blockSignals(True)
        self._object_tree_updating = True
        tree.clear()
        self._set_format_selection(None)
        if tab is None:
            self._object_tree_updating = False
            tree.blockSignals(False)
            return
        descriptor = self._tab_descriptors.get(tab)
        canvas = self._canvas_by_tab.get(tab)
        figure = None
        if canvas is not None:
            try:
                figure = canvas.figure
            except Exception:
                figure = None
        if figure is None and descriptor is not None:
            axes_obj = getattr(descriptor, "axes", None)
            if axes_obj is not None:
                figure = getattr(axes_obj, "figure", None)
        if figure is None:
            self._object_tree_updating = False
            tree.blockSignals(False)
            return
        title = ""
        if descriptor is not None and descriptor.title:
            title = descriptor.title
        if not title:
            try:
                sup = getattr(figure, "_suptitle", None)
                if sup is not None:
                    title = sup.get_text()
            except Exception:
                title = ""
        if not title:
            title = "Figure"
        root_item = QtWidgets.QTreeWidgetItem([title])
        root_item.setFlags(
            root_item.flags()
            | QtCore.Qt.ItemFlag.ItemIsEnabled
            | QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        root_item.setData(
            0,
            QtCore.Qt.ItemDataRole.UserRole,
            {"kind": "figure", "object": figure},
        )
        self._apply_object_item_visibility(root_item, True, allow_toggle=False)
        tree.addTopLevelItem(root_item)

        try:
            axes_list = list(getattr(figure, "axes", []))
        except Exception:
            axes_list = []
        figure_legends: list[Any] = []
        try:
            figure_legends = list(getattr(figure, "legends", []))
        except Exception:
            figure_legends = []

        def _make_item(label: str, kind: str | None = None, obj: Any | None = None) -> QtWidgets.QTreeWidgetItem:
            item = QtWidgets.QTreeWidgetItem([label])
            item.setFlags(
                item.flags()
                | QtCore.Qt.ItemFlag.ItemIsEnabled
                | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            if kind and obj is not None:
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": kind, "object": obj})
                if kind in {"legend", "line", "shape"}:
                    visible = True
                    getter = getattr(obj, "get_visible", None)
                    if callable(getter):
                        try:
                            visible = bool(getter())
                        except Exception:
                            visible = True
                    self._apply_object_item_visibility(item, visible)
            return item

        legends: list[Any] = []
        for axis_index, axis in enumerate(axes_list, start=1):
            axis_title = ""
            try:
                axis_title = axis.get_title()
            except Exception:
                axis_title = ""
            label = f"Axes {axis_index}"
            if axis_title:
                label = f"{label}: {axis_title}"
            axis_item = _make_item(label, kind="axes", obj=axis)
            root_item.addChild(axis_item)
            title_item = _make_item(f"Title: {axis_title}" if axis_title else "Title")
            title_artist = getattr(axis, "title", None)
            if isinstance(title_artist, Text):
                title_item.setData(
                    0,
                    QtCore.Qt.ItemDataRole.UserRole,
                    {"kind": "text", "object": title_artist},
                )
            axis_item.addChild(title_item)
            try:
                x_label = axis.get_xlabel()
            except Exception:
                x_label = ""
            x_label_text = f"X axis: {x_label}" if x_label else "X axis"
            x_label_item = _make_item(x_label_text)
            x_artist = getattr(getattr(axis, "xaxis", None), "label", None)
            if isinstance(x_artist, Text):
                x_label_item.setData(
                    0,
                    QtCore.Qt.ItemDataRole.UserRole,
                    {"kind": "text", "object": x_artist},
                )
            axis_item.addChild(x_label_item)
            try:
                y_label = axis.get_ylabel()
            except Exception:
                y_label = ""
            y_label_text = f"Y axis: {y_label}" if y_label else "Y axis"
            y_label_item = _make_item(y_label_text)
            y_artist = getattr(getattr(axis, "yaxis", None), "label", None)
            if isinstance(y_artist, Text):
                y_label_item.setData(
                    0,
                    QtCore.Qt.ItemDataRole.UserRole,
                    {"kind": "text", "object": y_artist},
                )
            axis_item.addChild(y_label_item)
            try:
                legend = axis.get_legend()
                if legend is not None:
                    legends.append(legend)
            except Exception:
                pass
            try:
                lines = list(axis.get_lines())
            except Exception:
                lines = []
            for line_index, line in enumerate(lines, start=1):
                try:
                    line_label = line.get_label()
                except Exception:
                    line_label = ""
                if not line_label or line_label.startswith("_line") or line_label == "_nolegend_":
                    line_label = f"Line {line_index}"
                line_item = _make_item(line_label)
                if isinstance(line, Line2D):
                    line_item.setData(
                        0,
                        QtCore.Qt.ItemDataRole.UserRole,
                        {"kind": "line", "object": line},
                    )
                visible = True
                getter = getattr(line, "get_visible", None)
                if callable(getter):
                    try:
                        visible = bool(getter())
                    except Exception:
                        visible = True
                self._apply_object_item_visibility(line_item, visible)
                axis_item.addChild(line_item)
            for text_index, text_artist in enumerate(self._iter_graph_object_texts(axis), start=1):
                text_item = _make_item(
                    self._graph_object_label(text_artist, fallback=f"Text {text_index}"),
                    kind="text",
                    obj=text_artist,
                )
                axis_item.addChild(text_item)
            for shape_index, shape_artist in enumerate(self._iter_graph_object_shapes(axis), start=1):
                shape_item = _make_item(
                    self._graph_object_label(shape_artist, fallback=f"Shape {shape_index}"),
                    kind="shape",
                    obj=shape_artist,
                )
                axis_item.addChild(shape_item)
        # add legends as top-level children under the figure root
        legend_seen: set[int] = set()
        for legend in legends + figure_legends:
            if legend is None or id(legend) in legend_seen:
                continue
            legend_seen.add(id(legend))
            try:
                legend_handles: list[Any] = []
                for attr in ("legendHandles", "legend_handles"):
                    value = getattr(legend, attr, None)
                    if value:
                        try:
                            legend_handles = list(value)
                        except Exception:
                            legend_handles = []
                        if legend_handles:
                            break
                if not legend_handles:
                    try:
                        legend_handles = list(legend.legendHandles)
                    except Exception:
                        legend_handles = []
            except Exception:
                legend_handles = []
            try:
                if not hasattr(legend, "_mw_show_symbols"):
                    legend._mw_show_symbols = bool(legend_defaults.get("show_symbols", True))
                if not hasattr(legend, "_mw_text_follows_handles"):
                    legend._mw_text_follows_handles = bool(legend_defaults.get("text_follows_handles", True))
                if not hasattr(legend, "_mw_orientation"):
                    legend._mw_orientation = legend_defaults.get("orientation", "auto")
                if not hasattr(legend, "_mw_placement"):
                    legend._mw_placement = legend_defaults.get("placement", "inside")
                    if legend._mw_placement == "outside":
                        axes = getattr(legend, "axes", None)
                        if axes is not None:
                            try:
                                legend.set_bbox_to_anchor((1.02, 1.0), transform=axes.transAxes)
                            except Exception:
                                pass
                    else:
                        legend.set_bbox_to_anchor(None)
                if not hasattr(legend, "_mw_draggable"):
                    legend._mw_draggable = bool(legend_defaults.get("draggable", True))
                if not getattr(legend, "get_draggable", lambda: True)():
                    legend.set_draggable(True)
                if legend._mw_text_follows_handles and legend_handles:
                    try:
                        legend_texts = list(legend.get_texts())
                    except Exception:
                        legend_texts = []
                    originals = getattr(legend, "_mw_text_original_colors", [])
                    if not originals:
                        try:
                            originals = [text.get_color() for text in legend_texts]
                        except Exception:
                            originals = []
                        setattr(legend, "_mw_text_original_colors", originals)
                    for text, handle in zip(legend_texts, legend_handles):
                        color = None
                        getter = getattr(handle, "get_color", None)
                        if callable(getter):
                            try:
                                color = getter()
                            except Exception:
                                color = None
                        if color is None:
                            getter = getattr(handle, "get_facecolor", None)
                            if callable(getter):
                                try:
                                    color = getter()
                                except Exception:
                                    color = None
                                if isinstance(color, (list, tuple)) and color:
                                    color = color[0]
                        if isinstance(color, (list, tuple)) and len(color) >= 3:
                            color = color[:3]
                        if color is not None:
                            try:
                                text.set_color(color)
                            except Exception:
                                pass
            except Exception:
                pass
            try:
                legend_title = legend.get_title().get_text() if legend.get_title() else "Legend"
            except Exception:
                legend_title = "Legend"
            legend_item = _make_item(legend_title or "Legend", kind="legend", obj=legend)
            try:
                texts = legend.get_texts()
            except Exception:
                texts = []
            for entry in texts:
                try:
                    text = entry.get_text()
                except Exception:
                    text = ""
                entry_item = _make_item(text or "Entry")
                if isinstance(entry, Text):
                    entry_item.setData(
                        0,
                        QtCore.Qt.ItemDataRole.UserRole,
                        {"kind": "text", "object": entry},
                    )
                legend_item.addChild(entry_item)
            legend_visible = True
            try:
                legend_visible = bool(legend.get_visible())
            except Exception:
                legend_visible = True
            self._apply_object_item_visibility(legend_item, legend_visible)
            root_item.addChild(legend_item)


        tree.expandAll()
        self._object_tree_updating = False
        tree.blockSignals(False)

    def _handle_object_selection_changed(self, *_: Any) -> None:
        tree = getattr(self, "object_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            self._set_format_selection(None)
            return
        text_targets: list[Text] = []
        line_target: Line2D | None = None
        shape_target: Any | None = None
        for item in tree.selectedItems():
            data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            kind: str | None = None
            target: Any | None = None
            if isinstance(data, dict):
                kind = data.get("kind")
                target = data.get("object")
            elif isinstance(data, tuple) and len(data) >= 2:
                kind = cast(str, data[0])
                target = data[1]
            if kind == "text" and isinstance(target, Text):
                text_targets.append(target)
            elif kind == "line" and isinstance(target, Line2D):
                line_target = target
            elif kind == "shape" and isinstance(target, (Line2D, Patch)):
                shape_target = target
        if text_targets:
            self._set_format_selection(("text", tuple(text_targets)))
        elif shape_target is not None:
            self._set_format_selection(("shape", shape_target))
        elif line_target is not None:
            self._set_format_selection(("line", line_target))
        else:
            self._set_format_selection(None)

    def _set_format_selection(self, selection: tuple[str, Any] | None) -> None:
        if selection is not None:
            kind, target = selection
            if kind == "text":
                texts = tuple(target or ())
                if not texts or not all(isinstance(entry, Text) for entry in texts):
                    selection = None
                else:
                    selection = ("text", texts)
            elif kind == "line" and not isinstance(target, Line2D):
                selection = None
            elif kind == "shape" and not isinstance(target, (Line2D, Patch)):
                selection = None
        self._format_selection = selection
        self._update_format_toolbar_state()

    def _update_format_toolbar_state(self) -> None:
        controls = self._format_controls
        if controls.toolbar is None:
            return
        font_combo = controls.font_combo
        size_spin = controls.size_spin
        width_spin = controls.width_spin
        bold_action = controls.bold_action
        italic_action = controls.italic_action
        underline_action = controls.underline_action
        subscript_action = controls.subscript_action
        superscript_action = controls.superscript_action
        subsup_action = controls.subsup_action
        edit_text_action = controls.edit_text_action
        fill_color_button = controls.fill_color_button
        line_actions = (
            (controls.line_action, "line"),
            (controls.scatter_action, "scatter"),
            (controls.line_symbol_action, "line_symbol"),
        )
        self._format_updating = True
        try:
            selection = self._format_selection
            if selection is None:
                if font_combo is not None:
                    font_combo.blockSignals(True)
                    font_combo.setEnabled(False)
                    font_combo.blockSignals(False)
                if size_spin is not None:
                    size_spin.blockSignals(True)
                    size_spin.setEnabled(False)
                    size_spin.blockSignals(False)
                if width_spin is not None:
                    width_spin.blockSignals(True)
                    width_spin.setEnabled(False)
                    width_spin.blockSignals(False)
                for action in (
                    bold_action,
                    italic_action,
                    underline_action,
                    subscript_action,
                    superscript_action,
                    subsup_action,
                    edit_text_action,
                ):
                    if action is not None:
                        action.blockSignals(True)
                        action.setChecked(False)
                        action.blockSignals(False)
                        action.setEnabled(False)
                for action, _ in line_actions:
                    if action is not None:
                        action.blockSignals(True)
                        action.setChecked(False)
                        action.blockSignals(False)
                        action.setEnabled(False)
                self._set_color_button_state(None, None)
                if fill_color_button is not None:
                    fill_color_button.setEnabled(False)
                    fill_color_button.setText("Fill…")
                    fill_color_button.setIcon(QtGui.QIcon())
                return
            kind = selection[0]
            if kind == "text":
                texts = selection[1]
                text = texts[0] if isinstance(texts, tuple) and texts else None
                if text is not None:
                    if font_combo is not None:
                        font_combo.blockSignals(True)
                        families = []
                        try:
                            families = list(text.get_fontfamily())
                        except Exception:
                            families = []
                        font_combo.setCurrentText(str(families[0]) if families else "DejaVu Serif")
                        font_combo.blockSignals(False)
                        font_combo.setEnabled(True)
                    if size_spin is not None:
                        size_spin.blockSignals(True)
                        try:
                            size_spin.setValue(int(round(float(text.get_fontsize()))))
                        except Exception:
                            pass
                        size_spin.blockSignals(False)
                        size_spin.setEnabled(True)
                    if bold_action is not None:
                        bold_action.blockSignals(True)
                        bold_action.setChecked(self._text_is_bold(text))
                        bold_action.blockSignals(False)
                        bold_action.setEnabled(True)
                    if italic_action is not None:
                        italic_action.blockSignals(True)
                        italic_action.setChecked(self._text_is_italic(text))
                        italic_action.blockSignals(False)
                        italic_action.setEnabled(True)
                    if underline_action is not None:
                        underline_action.blockSignals(True)
                        underline = False
                        try:
                            underline = bool(text.get_underline())
                        except Exception:
                            underline = False
                        underline_action.setChecked(underline)
                        underline_action.blockSignals(False)
                        underline_action.setEnabled(True)
                    for action in (subscript_action, superscript_action, subsup_action, edit_text_action):
                        if action is not None:
                            action.setEnabled(True)
                    if width_spin is not None:
                        width_spin.blockSignals(True)
                        width_spin.setEnabled(False)
                        width_spin.blockSignals(False)
                    for action, _ in line_actions:
                        if action is not None:
                            action.blockSignals(True)
                            action.setChecked(False)
                            action.blockSignals(False)
                            action.setEnabled(False)
                    self._set_color_button_state("text", self._qcolor_from_mpl(text.get_color()))
                    if fill_color_button is not None:
                        fill_color_button.setEnabled(False)
                        fill_color_button.setText("Fill…")
                        fill_color_button.setIcon(QtGui.QIcon())
                    return
            elif kind == "line":
                target = selection[1]
                if isinstance(target, Line2D):
                    if font_combo is not None:
                        font_combo.blockSignals(True)
                        font_combo.setEnabled(False)
                        font_combo.blockSignals(False)
                    if size_spin is not None:
                        size_spin.blockSignals(True)
                        size_spin.setEnabled(False)
                        size_spin.blockSignals(False)
                    if width_spin is not None:
                        width_spin.blockSignals(True)
                        try:
                            width_spin.setValue(float(target.get_linewidth()))
                        except Exception:
                            pass
                        width_spin.blockSignals(False)
                        width_spin.setEnabled(True)
                    for action in (bold_action, italic_action, underline_action):
                        if action is not None:
                            action.blockSignals(True)
                            action.setChecked(False)
                            action.blockSignals(False)
                            action.setEnabled(False)
                    for action in (subscript_action, superscript_action, subsup_action, edit_text_action):
                        if action is not None:
                            action.setEnabled(False)
                    style_key = self._line_style_key(target)
                    for action, key in line_actions:
                        if action is None:
                            continue
                        action.blockSignals(True)
                        action.setEnabled(True)
                        action.setChecked(style_key == key)
                        action.blockSignals(False)
                    self._set_color_button_state("line", self._qcolor_from_mpl(target.get_color()))
                    if fill_color_button is not None:
                        fill_color_button.setEnabled(False)
                        fill_color_button.setText("Fill…")
                        fill_color_button.setIcon(QtGui.QIcon())
                    return
            elif kind == "shape":
                target = selection[1]
                if isinstance(target, (Line2D, Patch)):
                    if font_combo is not None:
                        font_combo.blockSignals(True)
                        font_combo.setEnabled(False)
                        font_combo.blockSignals(False)
                    if size_spin is not None:
                        size_spin.blockSignals(True)
                        size_spin.setEnabled(False)
                        size_spin.blockSignals(False)
                    if width_spin is not None:
                        width_spin.blockSignals(True)
                        try:
                            width_spin.setValue(float(getattr(target, "get_linewidth", lambda: 1.0)()))
                        except Exception:
                            pass
                        width_spin.blockSignals(False)
                        width_spin.setEnabled(True)
                    for action in (bold_action, italic_action, underline_action):
                        if action is not None:
                            action.blockSignals(True)
                            action.setChecked(False)
                            action.blockSignals(False)
                            action.setEnabled(False)
                    for action in (subscript_action, superscript_action, subsup_action, edit_text_action):
                        if action is not None:
                            action.setEnabled(False)
                    for action, _ in line_actions:
                        if action is not None:
                            action.blockSignals(True)
                            action.setChecked(False)
                            action.blockSignals(False)
                            action.setEnabled(False)
                    stroke_color = None
                    try:
                        stroke_color = target.get_color() if isinstance(target, Line2D) else target.get_edgecolor()
                    except Exception:
                        stroke_color = None
                    self._set_color_button_state("shape", self._qcolor_from_mpl(stroke_color))
                    if fill_color_button is not None:
                        fill_color_button.setEnabled(isinstance(target, (Rectangle, Ellipse)))
                        fill_color_button.setText("Fill…")
                        fill_color_button.setIcon(QtGui.QIcon())
                        if isinstance(target, (Rectangle, Ellipse)):
                            try:
                                fill_color = target.get_facecolor()
                            except Exception:
                                fill_color = None
                            if isinstance(fill_color, (list, tuple)) and fill_color:
                                fill_color = fill_color[0]
                            if fill_color is not None:
                                pixmap = QtGui.QPixmap(16, 16)
                                pixmap.fill(self._qcolor_from_mpl(fill_color))
                                fill_color_button.setIcon(QtGui.QIcon(pixmap))
                    return
            if font_combo is not None:
                font_combo.blockSignals(True)
                font_combo.setEnabled(False)
                font_combo.blockSignals(False)
            if size_spin is not None:
                size_spin.blockSignals(True)
                size_spin.setEnabled(False)
                size_spin.blockSignals(False)
            if width_spin is not None:
                width_spin.blockSignals(True)
                width_spin.setEnabled(False)
                width_spin.blockSignals(False)
            for action in (
                bold_action,
                italic_action,
                underline_action,
                subscript_action,
                superscript_action,
                subsup_action,
                edit_text_action,
            ):
                if action is not None:
                    action.blockSignals(True)
                    action.setChecked(False)
                    action.blockSignals(False)
                    action.setEnabled(False)
            for action, _ in line_actions:
                if action is not None:
                    action.blockSignals(True)
                    action.setChecked(False)
                    action.blockSignals(False)
                    action.setEnabled(False)
            self._set_color_button_state(None, None)
            if fill_color_button is not None:
                fill_color_button.setEnabled(False)
                fill_color_button.setText("Fill…")
                fill_color_button.setIcon(QtGui.QIcon())
        finally:
            self._format_updating = False

    def _text_is_bold(self, text: Text) -> bool:
        weight = text.get_fontweight()
        if isinstance(weight, (int, float)):
            return float(weight) >= 600
        try:
            return str(weight).lower() in {"bold", "semibold", "demibold", "heavy"}
        except Exception:
            return False

    def _text_is_italic(self, text: Text) -> bool:
        style = text.get_fontstyle()
        try:
            return str(style).lower() in {"italic", "oblique"}
        except Exception:
            return False

    def _apply_text_family(self, family: str) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        token = str(family or "").strip() or "DejaVu Serif"
        texts = selection[1]
        for text in texts:
            try:
                text.set_fontfamily(token)
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _edit_text_payload_for_selection(self) -> _GraphTextDialogResult | None:
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return None
        texts = selection[1]
        first_text = texts[0] if isinstance(texts, tuple) and texts else None
        if not isinstance(first_text, Text):
            return None
        try:
            families = list(first_text.get_fontfamily())
        except Exception:
            families = []
        dialog = GraphTextDialog(
            self,
            title="Edit text",
            initial_text=str(getattr(first_text, "_mw_raw_text", first_text.get_text()) or ""),
            font_family=str(families[0]) if families else "DejaVu Serif",
            font_size=float(first_text.get_fontsize()),
            color=self._qcolor_from_mpl(first_text.get_color()),
            bold=self._text_is_bold(first_text),
            italic=self._text_is_italic(first_text),
            underline=bool(getattr(first_text, "get_underline", lambda: False)()),
        )
        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return None
        return dialog.result_payload()

    def _apply_text_dialog_payload(self, payload: _GraphTextDialogResult) -> None:
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        texts = selection[1]
        for text in texts:
            if not isinstance(text, Text):
                continue
            try:
                setattr(text, "_mw_raw_text", payload.text)
                setattr(text, "_mw_graph_object_label", payload.text)
                text.set_text(payload.text)
                text.set_fontfamily(payload.font_family)
                text.set_fontsize(payload.font_size)
                text.set_color(payload.color)
                text.set_fontweight("bold" if payload.bold else "normal")
                text.set_fontstyle("italic" if payload.italic else "normal")
                text.set_underline(bool(payload.underline))
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _edit_selected_text(self) -> None:
        payload = self._edit_text_payload_for_selection()
        if payload is None:
            return
        self._apply_text_dialog_payload(payload)

    def _line_style_key(self, line: Line2D) -> str:
        try:
            linestyle = line.get_linestyle()
        except Exception:
            linestyle = "-"
        try:
            marker = line.get_marker()
        except Exception:
            marker = None
        line_active = str(linestyle).strip().lower() not in {"", "none"}
        marker_active = str(marker).strip().lower() not in {"", "none"}
        if line_active and marker_active:
            return "line_symbol"
        if marker_active and not line_active:
            return "scatter"
        return "line"

    def _apply_text_size(self, value: int) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        texts = selection[1]
        for text in texts:
            try:
                text.set_fontsize(value)
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_bold(self, checked: bool) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        texts = selection[1]
        for text in texts:
            try:
                text.set_fontweight("bold" if checked else "normal")
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_italic(self, checked: bool) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        texts = selection[1]
        for text in texts:
            try:
                text.set_fontstyle("italic" if checked else "normal")
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_underline(self, checked: bool) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        texts = selection[1]
        for text in texts:
            try:
                text.set_underline(bool(checked))
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_subscript(self) -> None:
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        script, ok = QtWidgets.QInputDialog.getText(self, "Subscript", "Subscript text:")
        if not ok or not str(script).strip():
            return
        for text in selection[1]:
            base = self._strip_outer_math(str(getattr(text, "_mw_raw_text", text.get_text()) or "")) or "x"
            raw = f"${base}_{{{str(script).strip()}}}$"
            setattr(text, "_mw_raw_text", raw)
            setattr(text, "_mw_graph_object_label", raw)
            try:
                text.set_text(raw)
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_superscript(self) -> None:
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        script, ok = QtWidgets.QInputDialog.getText(self, "Superscript", "Superscript text:")
        if not ok or not str(script).strip():
            return
        for text in selection[1]:
            base = self._strip_outer_math(str(getattr(text, "_mw_raw_text", text.get_text()) or "")) or "x"
            raw = f"${base}^{{{str(script).strip()}}}$"
            setattr(text, "_mw_raw_text", raw)
            setattr(text, "_mw_graph_object_label", raw)
            try:
                text.set_text(raw)
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_subsuperscript(self) -> None:
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        subscript, ok = QtWidgets.QInputDialog.getText(self, "Subscript", "Subscript text:")
        if not ok or not str(subscript).strip():
            return
        superscript, ok = QtWidgets.QInputDialog.getText(self, "Superscript", "Superscript text:")
        if not ok or not str(superscript).strip():
            return
        for text in selection[1]:
            base = self._strip_outer_math(str(getattr(text, "_mw_raw_text", text.get_text()) or "")) or "x"
            raw = f"${base}_{{{str(subscript).strip()}}}^{{{str(superscript).strip()}}}$"
            setattr(text, "_mw_raw_text", raw)
            setattr(text, "_mw_graph_object_label", raw)
            try:
                text.set_text(raw)
            except Exception:
                continue
            self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _choose_format_color(self) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if selection is None:
            return
        role, target = selection
        initial = None
        if role == "text":
            texts = target if isinstance(target, tuple) else (target,)
            first_text = texts[0] if texts else None
            if isinstance(first_text, Text):
                initial = self._qcolor_from_mpl(first_text.get_color())
        elif role == "line" and isinstance(target, Line2D):
            initial = self._qcolor_from_mpl(target.get_color())
        elif role == "shape":
            try:
                initial = self._qcolor_from_mpl(
                    target.get_color() if isinstance(target, Line2D) else target.get_edgecolor()
                )
            except Exception:
                initial = None
        else:
            return
        color = QtWidgets.QColorDialog.getColor(initial, self, "Select colour")
        if not color.isValid():
            return
        mpl_color = self._mpl_color_from_qcolor(color)
        if role == "text":
            texts = target if isinstance(target, tuple) else (target,)
            for text in texts:
                if not isinstance(text, Text):
                    continue
                try:
                    text.set_color(mpl_color)
                except Exception:
                    continue
                self._redraw_artist(text)
        elif role == "line" and isinstance(target, Line2D):
            try:
                target.set_color(mpl_color)
            except Exception:
                return
            self._redraw_artist(target)
        elif role == "shape":
            try:
                if isinstance(target, Line2D):
                    target.set_color(mpl_color)
                else:
                    target.set_edgecolor(mpl_color)
            except Exception:
                return
            self._redraw_artist(target)
        self._update_format_toolbar_state()

    def _choose_fill_color(self) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "shape":
            return
        target = selection[1]
        if not isinstance(target, (Rectangle, Ellipse)):
            return
        initial = None
        try:
            fill_color = target.get_facecolor()
        except Exception:
            fill_color = None
        if isinstance(fill_color, (list, tuple)) and fill_color:
            fill_color = fill_color[0]
        if fill_color is not None:
            initial = self._qcolor_from_mpl(fill_color)
        color = QtWidgets.QColorDialog.getColor(initial, self, "Select fill colour")
        if not color.isValid():
            return
        try:
            target.set_facecolor(self._mpl_color_from_qcolor(color))
        except Exception:
            return
        self._redraw_artist(target)
        self._update_format_toolbar_state()

    def _apply_stroke_width(self, value: float) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection:
            return
        role, target = selection
        if role == "line" and isinstance(target, Line2D):
            try:
                target.set_linewidth(float(value))
            except Exception:
                return
            self._redraw_artist(target)
        elif role == "shape" and isinstance(target, (Line2D, Patch)):
            try:
                target.set_linewidth(float(value))
            except Exception:
                return
            self._redraw_artist(target)
        else:
            return
        self._update_format_toolbar_state()

    def _apply_line_style(self, style: str, checked: bool) -> None:
        if self._format_updating or not checked:
            return
        selection = self._format_selection
        if not selection or selection[0] != "line":
            return
        line = selection[1]
        try:
            if style == "line":
                line.set_linestyle("-")
                line.set_marker(None)
            elif style == "scatter":
                line.set_linestyle("None")
                line.set_marker("o")
                self._ensure_marker_size(line)
            elif style == "line_symbol":
                line.set_linestyle("-")
                marker = line.get_marker()
                if str(marker).strip().lower() in {"", "none"}:
                    line.set_marker("o")
                self._ensure_marker_size(line)
            else:
                return
        except Exception:
            return
        self._redraw_artist(line)
        self._update_format_toolbar_state()

    def _ensure_marker_size(self, line: Line2D) -> None:
        try:
            size = float(line.get_markersize())
        except Exception:
            size = 0.0
        if size <= 0.0:
            try:
                line.set_markersize(6.0)
            except Exception:
                pass

    def _set_color_button_state(self, role: str | None, color: QtGui.QColor | None) -> None:
        button = self._format_controls.color_button
        if button is None:
            return
        if role is None:
            button.setEnabled(False)
            button.setText("Stroke…")
            button.setToolTip("Select an object to adjust its colour")
            button.setIcon(QtGui.QIcon())
            return
        button.setEnabled(True)
        if role == "text":
            button.setText("Text…")
            button.setToolTip("Change the selected text colour")
        elif role == "line":
            button.setText("Stroke…")
            button.setToolTip("Change the selected line colour")
        elif role == "shape":
            button.setText("Stroke…")
            button.setToolTip("Change the selected shape stroke colour")
        else:
            button.setText("Stroke…")
            button.setToolTip("Change the selected object's colour")
        if color is None or not color.isValid():
            button.setIcon(QtGui.QIcon())
            return
        pixmap = QtGui.QPixmap(16, 16)
        pixmap.fill(color)
        painter = QtGui.QPainter(pixmap)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 80)))
        painter.drawRect(pixmap.rect().adjusted(0, 0, -1, -1))
        painter.end()
        button.setIcon(QtGui.QIcon(pixmap))

    def _qcolor_from_mpl(self, color: Any) -> QtGui.QColor:
        try:
            rgba = mcolors.to_rgba(color)
        except (ValueError, TypeError):
            return QtGui.QColor()
        return QtGui.QColor.fromRgbF(rgba[0], rgba[1], rgba[2], rgba[3])

    def _mpl_color_from_qcolor(self, color: QtGui.QColor) -> Any:
        if color.alpha() < 255:
            return (color.redF(), color.greenF(), color.blueF(), color.alphaF())
        return color.name()

    @staticmethod
    def _valid_font_size(value: Any) -> float | None:
        try:
            size = float(value)
        except Exception:
            return None
        if not np.isfinite(size) or size <= 0.0:
            return None
        return size

    def _tight_layout_font_candidates(self, figure: Any) -> list[tuple[str, float, int]]:
        if figure is None:
            return []
        candidates: list[tuple[str, float, int]] = []
        axes_list = []
        try:
            axes_list = [axis for axis in list(getattr(figure, "axes", [])) if axis is not None]
        except Exception:
            axes_list = []

        for index, axes in enumerate(axes_list, start=1):
            title = getattr(axes, "title", None)
            if title is not None:
                size = self._valid_font_size(getattr(title, "get_fontsize", lambda: None)())
                if size is not None:
                    candidates.append((f"Axes {index} title", size, 1))
            x_label = getattr(getattr(axes, "xaxis", None), "label", None)
            if x_label is not None:
                size = self._valid_font_size(getattr(x_label, "get_fontsize", lambda: None)())
                if size is not None:
                    candidates.append((f"Axes {index} X label", size, 1))
            y_label = getattr(getattr(axes, "yaxis", None), "label", None)
            if y_label is not None:
                size = self._valid_font_size(getattr(y_label, "get_fontsize", lambda: None)())
                if size is not None:
                    candidates.append((f"Axes {index} Y label", size, 1))

            for axis_name, getter in (
                ("X tick labels", getattr(axes, "get_xticklabels", None)),
                ("Y tick labels", getattr(axes, "get_yticklabels", None)),
            ):
                if not callable(getter):
                    continue
                try:
                    labels = list(getter())
                except Exception:
                    labels = []
                sizes = [
                    size
                    for size in (
                        self._valid_font_size(getattr(label, "get_fontsize", lambda: None)())
                        for label in labels
                    )
                    if size is not None
                ]
                if sizes:
                    candidates.append((f"Axes {index} {axis_name}", max(sizes), len(sizes)))

            legend = None
            try:
                legend = axes.get_legend()
            except Exception:
                legend = None
            if legend is not None:
                try:
                    legend_texts = list(legend.get_texts())
                except Exception:
                    legend_texts = []
                legend_sizes = [
                    size
                    for size in (
                        self._valid_font_size(getattr(text, "get_fontsize", lambda: None)())
                        for text in legend_texts
                    )
                    if size is not None
                ]
                if legend_sizes:
                    candidates.append((f"Axes {index} legend entries", max(legend_sizes), len(legend_sizes)))
                legend_title = getattr(legend, "get_title", lambda: None)()
                if legend_title is not None:
                    size = self._valid_font_size(getattr(legend_title, "get_fontsize", lambda: None)())
                    if size is not None:
                        candidates.append((f"Axes {index} legend title", size, 1))

        suptitle = getattr(figure, "_suptitle", None)
        if suptitle is not None:
            size = self._valid_font_size(getattr(suptitle, "get_fontsize", lambda: None)())
            if size is not None:
                candidates.append(("Figure title", size, 1))
        return candidates

    def _tight_layout_warning_details(self, figure: Any) -> tuple[str, str]:
        candidates = self._tight_layout_font_candidates(figure)
        if not candidates:
            return ("unknown text object", "No text font sizes could be measured.")
        ranked = sorted(candidates, key=lambda entry: (entry[1], entry[2]), reverse=True)
        top_name, top_size, top_count = ranked[0]
        top_label = f"{top_name} ({top_size:.1f} pt"
        if top_count > 1:
            top_label += f", {top_count} items"
        top_label += ")"
        summary_parts = []
        for name, size, count in ranked[:5]:
            if count > 1:
                summary_parts.append(f"{name}: {size:.1f} pt ({count} items)")
            else:
                summary_parts.append(f"{name}: {size:.1f} pt")
        return top_label, "; ".join(summary_parts)

    @staticmethod
    def _tight_layout_recommended_size(name: str, current_size: float, count: int) -> float:
        token = str(name or "").strip().lower()
        if "legend entries" in token:
            if count >= 48:
                cap = 6.0
            elif count >= 32:
                cap = 7.0
            elif count >= 16:
                cap = 8.0
            else:
                cap = 9.0
        elif "legend title" in token:
            cap = 9.0
        elif "x label" in token or "y label" in token:
            cap = 11.0
        elif "tick labels" in token:
            cap = 9.0
        elif "figure title" in token:
            cap = 14.0
        elif "title" in token:
            cap = 14.0
        else:
            cap = 10.0
        return min(float(current_size), cap)

    def _tight_layout_font_recommendations(self, figure: Any) -> dict[str, float]:
        recommendations: dict[str, float] = {}
        for name, size, count in self._tight_layout_font_candidates(figure):
            key: str | None
            token = str(name or "").strip().lower()
            if "legend entries" in token or "legend title" in token:
                key = "legend_font_size"
            elif "tick labels" in token:
                key = "tick_font"
            elif "x label" in token or "y label" in token:
                key = "label_font"
            elif "title" in token:
                key = "title_font"
            else:
                key = None
            if not key:
                continue
            suggested = self._tight_layout_recommended_size(name, size, count)
            existing = recommendations.get(key)
            if existing is None or suggested < existing:
                recommendations[key] = float(suggested)
        return recommendations

    @staticmethod
    def _tight_layout_recommendation_text(recommendations: dict[str, float]) -> str:
        if not recommendations:
            return "Title <= 14 pt; labels <= 11 pt; ticks <= 9 pt; legend <= 8 pt."
        labels = {
            "title_font": "Title",
            "label_font": "Axis labels",
            "tick_font": "Tick labels",
            "legend_font_size": "Legend text",
        }
        parts: list[str] = []
        for key in ("title_font", "label_font", "tick_font", "legend_font_size"):
            if key not in recommendations:
                continue
            parts.append(f"{labels.get(key, key)} <= {float(recommendations[key]):.1f} pt")
        return "; ".join(parts) if parts else "Reduce text sizes."

    def _apply_tight_layout_auto_fit(self, figure: Any, recommendations: dict[str, float]) -> int:
        if figure is None or not recommendations:
            return 0
        changed = 0
        axes_list: list[Any]
        try:
            axes_list = [axis for axis in list(getattr(figure, "axes", [])) if axis is not None]
        except Exception:
            axes_list = []
        for axes in axes_list:
            title_font = recommendations.get("title_font")
            if title_font is not None:
                title = getattr(axes, "title", None)
                if title is not None:
                    try:
                        current = self._valid_font_size(title.get_fontsize())
                        if current is not None and current > float(title_font) + 0.01:
                            title.set_fontsize(float(title_font))
                            changed += 1
                    except Exception:
                        pass
            label_font = recommendations.get("label_font")
            if label_font is not None:
                for label in (getattr(getattr(axes, "xaxis", None), "label", None), getattr(getattr(axes, "yaxis", None), "label", None)):
                    if label is None:
                        continue
                    try:
                        current = self._valid_font_size(label.get_fontsize())
                        if current is not None and current > float(label_font) + 0.01:
                            label.set_fontsize(float(label_font))
                            changed += 1
                    except Exception:
                        continue
            tick_font = recommendations.get("tick_font")
            if tick_font is not None:
                for getter in (getattr(axes, "get_xticklabels", None), getattr(axes, "get_yticklabels", None)):
                    if not callable(getter):
                        continue
                    try:
                        labels = list(getter())
                    except Exception:
                        labels = []
                    for label in labels:
                        try:
                            current = self._valid_font_size(label.get_fontsize())
                            if current is not None and current > float(tick_font) + 0.01:
                                label.set_fontsize(float(tick_font))
                                changed += 1
                        except Exception:
                            continue
            legend_font = recommendations.get("legend_font_size")
            if legend_font is not None:
                try:
                    legend = axes.get_legend()
                except Exception:
                    legend = None
                if legend is not None:
                    for text in list(getattr(legend, "get_texts", lambda: [])()):
                        try:
                            current = self._valid_font_size(text.get_fontsize())
                            if current is not None and current > float(legend_font) + 0.01:
                                text.set_fontsize(float(legend_font))
                                changed += 1
                        except Exception:
                            continue
                    title = getattr(legend, "get_title", lambda: None)()
                    if title is not None:
                        try:
                            current = self._valid_font_size(title.get_fontsize())
                            if current is not None and current > float(legend_font) + 0.01:
                                title.set_fontsize(float(legend_font))
                                changed += 1
                        except Exception:
                            pass

        if changed <= 0:
            return 0
        fit_to_content = getattr(self, "_fit_figure_to_content", None)
        if callable(fit_to_content):
            try:
                fit_to_content(figure)
            except Exception:
                pass
        else:
            tight_layout = getattr(figure, "tight_layout", None)
            if callable(tight_layout):
                try:
                    tight_layout(pad=1.0)
                except Exception:
                    pass
            canvas = getattr(figure, "canvas", None)
            if canvas is not None:
                try:
                    canvas.draw_idle()
                except Exception:
                    try:
                        canvas.draw()
                    except Exception:
                        pass
        return changed

    @staticmethod
    def _can_show_tight_layout_warning_dialog() -> bool:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return False
        try:
            platform_name = str(app.platformName() or "").strip().lower()
        except Exception:
            platform_name = ""
        return platform_name not in {"offscreen", "minimal", "headless"}

    def _tight_layout_warning_context_key(self, context_label: str) -> str:
        return str(context_label or "Graph layout").strip().lower()

    def _show_tight_layout_warning_dialog(
        self,
        *,
        message: str,
        can_apply_override: bool,
    ) -> tuple[str, bool]:
        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Graph layout warning")
        dialog.setText(message)
        keep_button = dialog.addButton(
            "Keep current sizes",
            QtWidgets.QMessageBox.ButtonRole.RejectRole,
        )
        auto_button = dialog.addButton(
            "Auto-fit current graph",
            QtWidgets.QMessageBox.ButtonRole.AcceptRole,
        )
        override_button = None
        if can_apply_override:
            override_button = dialog.addButton(
                "Apply plugin override",
                QtWidgets.QMessageBox.ButtonRole.ActionRole,
            )
        apply_all_checkbox = QtWidgets.QCheckBox(
            "Apply this choice to all affected graphs",
            dialog,
        )
        dialog.setCheckBox(apply_all_checkbox)
        dialog.setDefaultButton(keep_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is auto_button:
            choice = "auto"
        elif override_button is not None and clicked is override_button:
            choice = "override"
        else:
            choice = "keep"
        return choice, bool(apply_all_checkbox.isChecked())

    def _apply_tight_layout_warning_choice(
        self,
        *,
        choice: str,
        figure: Any,
        recommendations: dict[str, float],
        can_apply_override: bool,
        apply_override_handler: Any,
        axes_for_override: Any,
    ) -> None:
        token = str(choice or "").strip().lower()
        if token == "auto":
            changed = self._apply_tight_layout_auto_fit(figure, recommendations)
            append_log = getattr(self, "_append_log", None)
            if callable(append_log):
                if changed > 0:
                    append_log(
                        f"Auto-fit adjusted {changed} text element(s) to reduce graph layout clipping."
                    )
                else:
                    append_log(
                        "Auto-fit could not reduce text sizes further; adjust Graph formatting manually.",
                        level="warning",
                    )
            return
        if token == "override":
            if not can_apply_override or not callable(apply_override_handler):
                return
            applied = False
            try:
                applied = bool(apply_override_handler(axes_for_override, recommendations))
            except Exception:
                applied = False
            if not applied:
                QtWidgets.QMessageBox.information(
                    self,
                    "Graph layout warning",
                    "Plugin override could not be applied. You can still adjust sizes in Graph options.",
                )
            return

    def _handle_tight_layout_warning(
        self,
        figure: Any,
        messages: Sequence[str],
        *,
        context: str | None = None,
    ) -> None:
        target = None
        for raw in messages:
            text = str(raw or "").strip()
            if "tight layout not applied" in text.lower():
                target = text
                break
        if not target:
            return
        offender, summary = self._tight_layout_warning_details(figure)
        context_label = str(context or "Graph layout").strip()
        signature = (id(figure), f"{context_label}|{target}|{offender}")
        if signature in self._tight_layout_warning_signatures:
            return
        self._tight_layout_warning_signatures.add(signature)
        recommendations = self._tight_layout_font_recommendations(figure)
        recommendation_text = self._tight_layout_recommendation_text(recommendations)
        message = (
            f"{context_label}: Matplotlib could not fit all graph decorations.\n\n"
            f"Likely too large font: {offender}\n"
            f"Detected text sizes: {summary}\n\n"
            f"Suggested size targets: {recommendation_text}\n\n"
            "Choose: keep current sizes, auto-fit this graph, or apply a plugin override."
        )
        self._last_tight_layout_warning_message = message

        if not self._can_show_tight_layout_warning_dialog():
            append_log = getattr(self, "_append_log", None)
            if callable(append_log):
                try:
                    append_log(f"Graph layout warning: {message}", level="warning")
                except Exception:
                    pass
            return

        axes_for_override = None
        try:
            axes_list = [axis for axis in list(getattr(figure, "axes", [])) if axis is not None]
        except Exception:
            axes_list = []
        if axes_list:
            axes_for_override = axes_list[0]
        apply_override_handler = getattr(self, "_apply_plugin_graph_option_override_for_axes", None)
        can_apply_override = callable(apply_override_handler) and axes_for_override is not None
        has_saved_override = False
        if axes_for_override is not None:
            plugin_name_for_axes = None
            plugin_name_getter = getattr(self, "_plugin_name_for_axes", None)
            if callable(plugin_name_getter):
                try:
                    plugin_name_for_axes = plugin_name_getter(axes_for_override)
                except Exception:
                    plugin_name_for_axes = None
            override_checker = getattr(self, "_has_plugin_graph_option_override", None)
            if callable(override_checker):
                try:
                    has_saved_override = bool(override_checker(plugin_name_for_axes))
                except Exception:
                    has_saved_override = False
        if has_saved_override and can_apply_override:
            self._apply_tight_layout_warning_choice(
                choice="override",
                figure=figure,
                recommendations=recommendations,
                can_apply_override=can_apply_override,
                apply_override_handler=apply_override_handler,
                axes_for_override=axes_for_override,
            )
            return
        context_key = self._tight_layout_warning_context_key(context_label)
        expiry = float(self._tight_layout_bulk_choice_expiry_by_context.get(context_key, 0.0))
        if expiry > 0.0 and time.monotonic() > expiry:
            self._tight_layout_bulk_choice_by_context.pop(context_key, None)
            self._tight_layout_bulk_choice_expiry_by_context.pop(context_key, None)
        bulk_choice = self._tight_layout_bulk_choice_by_context.get(context_key)
        if isinstance(bulk_choice, str) and bulk_choice:
            self._apply_tight_layout_warning_choice(
                choice=bulk_choice,
                figure=figure,
                recommendations=recommendations,
                can_apply_override=can_apply_override,
                apply_override_handler=apply_override_handler,
                axes_for_override=axes_for_override,
            )
            return

        choice, apply_to_all = self._show_tight_layout_warning_dialog(
            message=message,
            can_apply_override=can_apply_override,
        )
        if apply_to_all:
            self._tight_layout_bulk_choice_by_context[context_key] = choice
            # Keep batch decision for the current run only.
            self._tight_layout_bulk_choice_expiry_by_context[context_key] = time.monotonic() + 30.0
        else:
            self._tight_layout_bulk_choice_by_context.pop(context_key, None)
            self._tight_layout_bulk_choice_expiry_by_context.pop(context_key, None)
        self._apply_tight_layout_warning_choice(
            choice=choice,
            figure=figure,
            recommendations=recommendations,
            can_apply_override=can_apply_override,
            apply_override_handler=apply_override_handler,
            axes_for_override=axes_for_override,
        )

    def _tight_layout_with_feedback(
        self,
        figure: Any,
        *,
        context: str | None = None,
        **kwargs: Any,
    ) -> bool:
        if figure is None:
            return False
        tight_layout = getattr(figure, "tight_layout", None)
        if not callable(tight_layout):
            return False
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", UserWarning)
                tight_layout(**kwargs)
        except Exception:
            return False
        messages = [str(getattr(warning, "message", "")).strip() for warning in caught]
        self._handle_tight_layout_warning(figure, messages, context=context)
        return True

    def _redraw_artist(self, artist: Any) -> None:
        figure = getattr(artist, "figure", None)
        if figure is None:
            axes = getattr(artist, "axes", None)
            if axes is not None:
                figure = getattr(axes, "figure", None)
        if figure is not None:
            applied = self._tight_layout_with_feedback(
                figure,
                pad=1.0,
                context="Graph redraw",
            )
            if not applied:
                tight_layout = getattr(figure, "tight_layout", None)
                if callable(tight_layout):
                    try:
                        tight_layout(pad=1.0)
                    except Exception:
                        pass
        canvas = getattr(figure, "canvas", None) if figure is not None else None
        if canvas is None:
            return
        try:
            canvas.draw_idle()
        except Exception:
            try:
                canvas.draw()
            except Exception:
                pass

    def _dispatch_object_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        handler = getattr(self, "_handle_object_item_changed", None)
        if callable(handler):
            try:
                handler(item, column)
            except TypeError:
                try:
                    handler(item)
                except TypeError:
                    handler()

    def _open_worksheet_tab(self, key: Hashable) -> None:
        """Open or focus the worksheet that originated from ``key``."""

        widget = self._worksheet_tabs_open.get(key)
        if widget is not None:
            self._show_tab(widget)
            return
        worksheet = self._worksheets.get(key)
        if worksheet is None:
            return
        model = self._worksheet_models.get(key)
        if model is None:
            model = WorksheetTableModel(worksheet, self)
            self._worksheet_models[key] = model
        widget = self._create_worksheet_tab(worksheet, model)
        if widget is None:
            return
        index = self.tab_widget.addTab(widget, worksheet.name)
        self.tab_widget.setCurrentIndex(index)
        self._worksheet_tabs_open[key] = widget
        self._tab_to_worksheet_key[widget] = key
        self._update_tab_buttons()
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        self._update_worksheet_actions()

    def _show_tab(self, tab: QtWidgets.QWidget) -> None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return
        self._set_tab_visibility(tab, True)
        self.tab_widget.setCurrentIndex(index)
        self._update_tab_buttons()

    def _tab_for_axes(self, axes: Any) -> QtWidgets.QWidget | None:
        if axes is None:
            return None
        target_figure = getattr(axes, "figure", None)
        for tab, candidate in self._axes_by_tab.items():
            if candidate is axes:
                return tab
            candidate_figure = getattr(candidate, "figure", None)
            if target_figure is not None and candidate_figure is target_figure:
                return tab
        return None

    @staticmethod
    def _label_units_to_brackets(text: str) -> str:
        if not isinstance(text, str):
            return text
        stripped = text.strip()
        if not stripped or "[" in stripped or "]" in stripped:
            return text
        match = UNIT_SUFFIX_PAREN_RE.match(stripped)
        if not match:
            return text
        unit = (match.group("unit") or "").strip()
        prefix = (match.group("prefix") or "").rstrip()
        if not unit:
            return text
        return f"{prefix} [{unit}]".strip() if prefix else f"[{unit}]"

    def _normalize_axes_unit_labels(
        self,
        axes: Any,
        *,
        descriptor: TabDescriptor | None = None,
    ) -> None:
        if axes is None:
            return
        try:
            x_label = str(axes.get_xlabel() or "")
        except Exception:
            x_label = ""
        try:
            y_label = str(axes.get_ylabel() or "")
        except Exception:
            y_label = ""

        new_x = self._label_units_to_brackets(x_label)
        new_y = self._label_units_to_brackets(y_label)
        if new_x != x_label:
            try:
                axes.set_xlabel(new_x)
            except Exception:
                pass
        if new_y != y_label:
            try:
                axes.set_ylabel(new_y)
            except Exception:
                pass
        if descriptor is not None:
            if new_x:
                descriptor.x_label = new_x
            if new_y:
                descriptor.y_label = new_y

    def _apply_axes_text_value(
        self,
        axes: Any,
        *,
        field: Literal["title", "x_label", "y_label"],
        value: str,
    ) -> bool:
        if axes is None:
            return False
        text_value = str(value)
        try:
            if field == "title":
                axes.set_title(text_value)
            elif field == "x_label":
                axes.set_xlabel(text_value)
            else:
                axes.set_ylabel(text_value)
        except Exception:
            return False

        tab = self._tab_for_axes(axes)
        if tab is not None:
            descriptor = self._tab_descriptors.get(tab)
            if descriptor is not None:
                if field == "title":
                    descriptor.title = str(
                        getattr(axes, "get_title", lambda: text_value)() or text_value
                    )
                    item = self._graph_tree_items.get(tab)
                    if isinstance(item, QtWidgets.QTreeWidgetItem):
                        self._set_tree_item_text(
                            item,
                            name=item.text(0),
                            details=descriptor.title or "",
                        )
                elif field == "x_label":
                    descriptor.x_label = str(
                        getattr(axes, "get_xlabel", lambda: text_value)() or text_value
                    )
                else:
                    descriptor.y_label = str(
                        getattr(axes, "get_ylabel", lambda: text_value)() or text_value
                    )

        self._redraw_artist(axes)
        current_tab = self.tab_widget.currentWidget()
        if tab is not None and tab is current_tab:
            self._rebuild_object_manager_for_tab(tab)
            sync = getattr(self, "_sync_graph_format_controls_from_current_axes", None)
            if callable(sync):
                try:
                    sync()
                except Exception:
                    pass
        return True

    def _apply_axis_scale_settings(
        self,
        axes: Any,
        axis: Literal["x", "y"],
        *,
        scale: str,
        auto_limits: bool,
        lower: float | None,
        upper: float | None,
        show_dialog_errors: bool = True,
    ) -> bool:
        if axes is None:
            return False
        target_scale = str(scale or "linear").strip().lower()
        if target_scale not in {"linear", "log"}:
            target_scale = "linear"

        if not auto_limits:
            if lower is None or upper is None:
                if show_dialog_errors:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Axis scale",
                        "Set both min and max limits, or enable Auto limits.",
                    )
                return False
            if lower >= upper:
                if show_dialog_errors:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Axis scale",
                        "Axis min must be less than axis max.",
                    )
                return False
            if target_scale == "log" and (lower <= 0.0 or upper <= 0.0):
                if show_dialog_errors:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Axis scale",
                        "Log scale requires positive limits.",
                    )
                return False

        try:
            if axis == "x":
                axes.set_xscale(target_scale)
            else:
                axes.set_yscale(target_scale)
        except Exception:
            return False

        try:
            if auto_limits:
                axes.relim()
                axes.autoscale(enable=True, axis=axis, tight=False)
            else:
                if axis == "x":
                    axes.set_xlim(cast(float, lower), cast(float, upper))
                else:
                    axes.set_ylim(cast(float, lower), cast(float, upper))
        except Exception:
            return False

        self._redraw_artist(axes)
        tab = self._tab_for_axes(axes)
        if tab is not None and tab is self.tab_widget.currentWidget():
            sync = getattr(self, "_sync_graph_format_controls_from_current_axes", None)
            if callable(sync):
                try:
                    sync()
                except Exception:
                    pass
        return True

    def _axis_from_event_hit(self, event: Any, axes: Any) -> Literal["x", "y"] | None:
        if axes is None:
            return None
        x = getattr(event, "x", None)
        y = getattr(event, "y", None)
        if x is None or y is None:
            return None
        try:
            bbox = axes.get_window_extent()
        except Exception:
            return None
        margin = 14.0
        try:
            x_val = float(x)
            y_val = float(y)
        except Exception:
            return None
        if bbox.x0 - margin <= x_val <= bbox.x1 + margin:
            if min(abs(y_val - bbox.y0), abs(y_val - bbox.y1)) <= margin:
                return "x"
        if bbox.y0 - margin <= y_val <= bbox.y1 + margin:
            if min(abs(x_val - bbox.x0), abs(x_val - bbox.x1)) <= margin:
                return "y"
        return None

    @staticmethod
    def _artist_hit(artist: Any, event: Any) -> bool:
        if artist is None:
            return False
        contains = getattr(artist, "contains", None)
        if not callable(contains):
            result = False
        else:
            try:
                result = contains(event)
            except Exception:
                result = False
            if isinstance(result, tuple):
                if result and bool(result[0]):
                    return True
            elif bool(result):
                return True

        # Text objects frequently miss contains() hits when clicked just outside
        # the glyph outline (or when the click lands outside axes bounds), so
        # use a small bbox tolerance fallback.
        x = getattr(event, "x", None)
        y = getattr(event, "y", None)
        if x is None or y is None:
            return False
        figure = getattr(artist, "figure", None)
        canvas = getattr(figure, "canvas", None) if figure is not None else None
        if canvas is None:
            return False
        renderer_getter = getattr(canvas, "get_renderer", None)
        renderer = None
        if callable(renderer_getter):
            try:
                renderer = renderer_getter()
            except Exception:
                renderer = None
        bbox_getter = getattr(artist, "get_window_extent", None)
        if not callable(bbox_getter):
            return False
        try:
            bbox = bbox_getter(renderer=renderer)
        except Exception:
            return False
        if bbox is None:
            return False
        try:
            padded = bbox.expanded(1.18, 1.4)
            return bool(padded.contains(float(x), float(y)))
        except Exception:
            return False

    def _edit_axes_text_from_double_click(
        self,
        axes: Any,
        field: Literal["title", "x_label", "y_label"],
    ) -> None:
        if field == "title":
            current = str(getattr(axes, "get_title", lambda: "")() or "")
            dialog_title = "Edit graph title"
            prompt = "Title:"
        elif field == "x_label":
            current = str(getattr(axes, "get_xlabel", lambda: "")() or "")
            dialog_title = "Edit X axis label"
            prompt = "X label:"
        else:
            current = str(getattr(axes, "get_ylabel", lambda: "")() or "")
            dialog_title = "Edit Y axis label"
            prompt = "Y label:"

        text, ok = QtWidgets.QInputDialog.getText(
            self,
            dialog_title,
            prompt,
            text=current,
        )
        if not ok:
            return
        self._apply_axes_text_value(axes, field=field, value=text)

    def _edit_axis_scale_from_double_click(
        self, axes: Any, axis: Literal["x", "y"]
    ) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Edit axis scale")
        dialog.resize(360, 220)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        axis_name = "X" if axis == "x" else "Y"
        info = QtWidgets.QLabel(
            f"Adjust {axis_name} axis scale and limits.", dialog
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        layout.addLayout(form)

        scale_combo = QtWidgets.QComboBox(dialog)
        scale_combo.addItem("Linear", "linear")
        scale_combo.addItem("Log", "log")
        try:
            current_scale = axes.get_xscale() if axis == "x" else axes.get_yscale()
        except Exception:
            current_scale = "linear"
        index = scale_combo.findData(str(current_scale).lower())
        if index >= 0:
            scale_combo.setCurrentIndex(index)
        form.addRow("Scale:", scale_combo)

        auto_checkbox = QtWidgets.QCheckBox("Auto limits", dialog)
        auto_checkbox.setChecked(True)
        form.addRow("", auto_checkbox)

        limit_row = QtWidgets.QWidget(dialog)
        limit_layout = QtWidgets.QHBoxLayout(limit_row)
        limit_layout.setContentsMargins(0, 0, 0, 0)
        limit_layout.setSpacing(6)
        min_spin = QtWidgets.QDoubleSpinBox(limit_row)
        max_spin = QtWidgets.QDoubleSpinBox(limit_row)
        for spin in (min_spin, max_spin):
            spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
            spin.setDecimals(6)
            spin.setSingleStep(0.1)
        try:
            lower, upper = axes.get_xlim() if axis == "x" else axes.get_ylim()
            min_spin.setValue(float(lower))
            max_spin.setValue(float(upper))
        except Exception:
            pass
        limit_layout.addWidget(QtWidgets.QLabel("Min:", limit_row))
        limit_layout.addWidget(min_spin, 1)
        limit_layout.addWidget(QtWidgets.QLabel("Max:", limit_row))
        limit_layout.addWidget(max_spin, 1)
        form.addRow("", limit_row)

        def _sync_limit_enabled(checked: bool) -> None:
            enabled = not checked
            min_spin.setEnabled(enabled)
            max_spin.setEnabled(enabled)

        auto_checkbox.toggled.connect(_sync_limit_enabled)
        _sync_limit_enabled(auto_checkbox.isChecked())

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return
        target_scale = str(scale_combo.currentData() or "linear")
        auto_limits = bool(auto_checkbox.isChecked())
        lower_value = float(min_spin.value()) if not auto_limits else None
        upper_value = float(max_spin.value()) if not auto_limits else None
        self._apply_axis_scale_settings(
            axes,
            axis,
            scale=target_scale,
            auto_limits=auto_limits,
            lower=lower_value,
            upper=upper_value,
            show_dialog_errors=True,
        )

    def _open_shared_graph_format_from_canvas_target(
        self,
        axes: Any,
        *,
        text_field: Literal["title", "x_label", "y_label"] | None = None,
        axis: Literal["x", "y"] | None = None,
        legend: bool = False,
        line: bool = False,
    ) -> bool:
        """Allow concrete hosts to route double-clicks into shared formatting UI."""

        opener = getattr(self, "_open_shared_graph_format_from_double_click", None)
        if not callable(opener):
            return False
        kwargs: dict[str, Any] = {
            "axes": axes,
            "text_field": text_field,
            "axis": axis,
            "legend": legend,
        }
        if line:
            kwargs["line"] = True
        try:
            result = opener(**kwargs)
        except Exception:
            return False
        return bool(result)

    def _event_candidate_axes(self, event: Any) -> list[Any]:
        event_axes = getattr(event, "inaxes", None)
        candidates: list[Any] = []
        if event_axes is not None:
            candidates.append(event_axes)
        else:
            canvas = getattr(event, "canvas", None)
            figure = getattr(canvas, "figure", None) if canvas is not None else None
            for axes in getattr(figure, "axes", []) if figure is not None else []:
                if axes is not None and axes not in candidates:
                    candidates.append(axes)
        return candidates

    def _mark_graph_artist_changed(self, artist: Any) -> None:
        self._redraw_artist(artist)
        tab = self._tab_for_axes(getattr(artist, "axes", None))
        if tab is not None:
            self._rebuild_object_manager_for_tab(tab)
        self._mark_project_dirty()

    def _create_text_annotation(
        self,
        axes: Any,
        *,
        x: float,
        y: float,
        payload: _GraphTextDialogResult,
    ) -> Text | None:
        if axes is None:
            return None
        try:
            artist = axes.text(
                x,
                y,
                payload.text,
                fontsize=payload.font_size,
                fontfamily=payload.font_family,
                color=payload.color,
                fontweight="bold" if payload.bold else "normal",
                fontstyle="italic" if payload.italic else "normal",
                va="center",
                ha="center",
                zorder=10,
            )
        except Exception:
            return None
        try:
            artist.set_underline(bool(payload.underline))
        except Exception:
            pass
        setattr(artist, "_mw_raw_text", payload.text)
        self._mark_graph_object(artist, "text", label=payload.text)
        return artist

    def _select_graph_object_from_canvas(self, event: Any, candidates: list[Any]) -> bool:
        for axes in candidates:
            for text_artist in self._iter_graph_object_texts(axes):
                if self._artist_hit(text_artist, event):
                    self._set_format_selection(("text", (text_artist,)))
                    self._select_object_in_manager(text_artist)
                    return True
            for shape_artist in reversed(self._iter_graph_object_shapes(axes)):
                if self._artist_hit(shape_artist, event):
                    self._set_format_selection(("shape", shape_artist))
                    self._select_object_in_manager(shape_artist)
                    return True
            try:
                lines = list(axes.get_lines())
            except Exception:
                lines = []
            for line in reversed(lines):
                if not isinstance(line, Line2D):
                    continue
                if self._graph_object_kind(line) == "shape_line":
                    continue
                if self._artist_hit(line, event):
                    self._set_format_selection(("line", line))
                    return True
        return False

    def _start_annotation_drag(self, event: Any, tool: str, axes: Any) -> None:
        x0 = getattr(event, "xdata", None)
        y0 = getattr(event, "ydata", None)
        if x0 is None or y0 is None:
            return
        preview = None
        if tool == "shape_line":
            preview = Line2D([x0, x0], [y0, y0], color="#111111", linewidth=1.2, zorder=10)
            try:
                axes.add_line(preview)
            except Exception:
                preview = None
        elif tool == "arrow":
            preview = FancyArrowPatch((x0, y0), (x0, y0), arrowstyle="->", mutation_scale=12, color="#111111", linewidth=1.2, zorder=10)
            try:
                axes.add_patch(preview)
            except Exception:
                preview = None
        elif tool == "rectangle":
            preview = Rectangle((x0, y0), 0.0, 0.0, fill=False, edgecolor="#111111", linewidth=1.2, zorder=10)
            try:
                axes.add_patch(preview)
            except Exception:
                preview = None
        elif tool == "ellipse":
            preview = Ellipse((x0, y0), 0.0, 0.0, fill=False, edgecolor="#111111", linewidth=1.2, zorder=10)
            try:
                axes.add_patch(preview)
            except Exception:
                preview = None
        if preview is None:
            return
        self._annotation_interaction = {
            "tool": tool,
            "axes": axes,
            "canvas": getattr(event, "canvas", None),
            "start": (float(x0), float(y0)),
            "preview": preview,
        }
        self._redraw_artist(preview)

    def _update_annotation_preview_geometry(self, interaction: Dict[str, Any], x1: float, y1: float) -> None:
        tool = str(interaction.get("tool") or "")
        x0, y0 = interaction.get("start", (0.0, 0.0))
        preview = interaction.get("preview")
        if tool == "shape_line" and isinstance(preview, Line2D):
            preview.set_data([x0, x1], [y0, y1])
        elif tool == "arrow" and isinstance(preview, FancyArrowPatch):
            preview.set_positions((x0, y0), (x1, y1))
        elif tool == "rectangle" and isinstance(preview, Rectangle):
            preview.set_x(min(x0, x1))
            preview.set_y(min(y0, y1))
            preview.set_width(abs(x1 - x0))
            preview.set_height(abs(y1 - y0))
        elif tool == "ellipse" and isinstance(preview, Ellipse):
            preview.center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
            preview.width = abs(x1 - x0)
            preview.height = abs(y1 - y0)

    def _handle_canvas_motion(self, event: Any) -> None:
        interaction = self._annotation_interaction
        if not isinstance(interaction, dict):
            return
        if interaction.get("canvas") is not getattr(event, "canvas", None):
            return
        x1 = getattr(event, "xdata", None)
        y1 = getattr(event, "ydata", None)
        if x1 is None or y1 is None:
            return
        self._update_annotation_preview_geometry(interaction, float(x1), float(y1))
        preview = interaction.get("preview")
        if preview is not None:
            self._redraw_artist(preview)

    def _handle_canvas_button_release(self, event: Any) -> None:
        interaction = self._annotation_interaction
        if not isinstance(interaction, dict):
            return
        if interaction.get("canvas") is not getattr(event, "canvas", None):
            return
        x1 = getattr(event, "xdata", None)
        y1 = getattr(event, "ydata", None)
        preview = interaction.get("preview")
        if preview is None:
            self._annotation_interaction = None
            return
        if x1 is None or y1 is None:
            try:
                preview.remove()
            except Exception:
                pass
            self._annotation_interaction = None
            return
        self._update_annotation_preview_geometry(interaction, float(x1), float(y1))
        tool = str(interaction.get("tool") or "")
        label_map = {
            "shape_line": "Line",
            "arrow": "Arrow",
            "rectangle": "Rectangle",
            "ellipse": "Ellipse",
        }
        self._mark_graph_object(preview, tool, label=label_map.get(tool))
        self._annotation_interaction = None
        self._mark_graph_artist_changed(preview)
        self._set_format_selection(("shape", preview))
        self._select_object_in_manager(preview)

    def _handle_canvas_button_press(self, event: Any) -> None:
        if self._is_canvas_context_click(event):
            self._show_canvas_context_menu(event)
            return
        if self._nav_mode in {"zoom", "pan"}:
            return
        candidates = self._event_candidate_axes(event)
        tool = str(self._annotation_tool or "select")
        if tool == "select" and not bool(getattr(event, "dblclick", False)):
            if self._select_graph_object_from_canvas(event, candidates):
                return
        if tool == "text":
            if getattr(event, "button", None) == 1 and candidates:
                axes = candidates[0]
                x = getattr(event, "xdata", None)
                y = getattr(event, "ydata", None)
                if x is not None and y is not None:
                    dialog = GraphTextDialog(
                        self,
                        title="Add text label",
                        initial_text="",
                        font_family="DejaVu Serif",
                        font_size=14.0,
                    )
                    if dialog.exec() == int(QtWidgets.QDialog.DialogCode.Accepted):
                        artist = self._create_text_annotation(
                            axes,
                            x=float(x),
                            y=float(y),
                            payload=dialog.result_payload(),
                        )
                        if artist is not None:
                            self._mark_graph_artist_changed(artist)
                            self._set_format_selection(("text", (artist,)))
                            self._select_object_in_manager(artist)
                    return
        elif tool in {"arrow", "shape_line", "rectangle", "ellipse"}:
            if getattr(event, "button", None) == 1 and candidates:
                axes = candidates[0]
                if getattr(event, "xdata", None) is not None and getattr(event, "ydata", None) is not None:
                    self._start_annotation_drag(event, tool, axes)
                    return
        if not bool(getattr(event, "dblclick", False)):
            return
        if not candidates:
            return

        for axes in candidates:
            text_targets: list[tuple[Literal["title", "x_label", "y_label"], Any]] = [
                ("title", getattr(axes, "title", None)),
                ("x_label", getattr(getattr(axes, "xaxis", None), "label", None)),
                ("y_label", getattr(getattr(axes, "yaxis", None), "label", None)),
            ]
            for field, artist in text_targets:
                if self._artist_hit(artist, event):
                    opened = self._open_shared_graph_format_from_canvas_target(
                        axes,
                        text_field=field,
                    )
                    if not opened:
                        self._edit_axes_text_from_double_click(axes, field)
                    return
            for text_artist in self._iter_graph_object_texts(axes):
                if self._artist_hit(text_artist, event):
                    self._set_format_selection(("text", (text_artist,)))
                    self._select_object_in_manager(text_artist)
                    self._edit_selected_text()
                    return
            for shape_artist in reversed(self._iter_graph_object_shapes(axes)):
                if self._artist_hit(shape_artist, event):
                    self._set_format_selection(("shape", shape_artist))
                    self._select_object_in_manager(shape_artist)
                    return

        for axes in candidates:
            try:
                lines = list(axes.get_lines())
            except Exception:
                lines = []
            for line in lines:
                if not isinstance(line, Line2D):
                    continue
                if not self._artist_hit(line, event):
                    continue
                try:
                    self._set_format_selection(("line", line))
                except Exception:
                    pass
                opened = self._open_shared_graph_format_from_canvas_target(
                    axes,
                    line=True,
                )
                if opened:
                    return

        for axes in candidates:
            legend = None
            try:
                legend = axes.get_legend()
            except Exception:
                legend = None
            if legend is None:
                continue
            try:
                if not bool(legend.get_visible()):
                    continue
            except Exception:
                pass
            legend_artists: list[Any] = [legend, getattr(legend, "get_frame", lambda: None)()]
            try:
                legend_artists.extend(list(legend.get_texts()))
            except Exception:
                pass
            try:
                legend_artists.extend(_legend_handles(legend))
            except Exception:
                pass
            if any(self._artist_hit(artist, event) for artist in legend_artists if artist is not None):
                self._open_shared_graph_format_from_canvas_target(
                    axes,
                    legend=True,
                )
                return

        for axes in candidates:
            axis = self._axis_from_event_hit(event, axes)
            if axis is not None:
                opened = self._open_shared_graph_format_from_canvas_target(
                    axes,
                    axis=axis,
                )
                if not opened:
                    self._edit_axis_scale_from_double_click(axes, axis)
                return

    def _is_tab_visible(self, tab: QtWidgets.QWidget) -> bool:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return False
        try:
            return self.tab_widget.isTabVisible(index)
        except Exception:
            return tab.isVisible()

    def _set_tab_visibility(self, tab: QtWidgets.QWidget, visible: bool) -> None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return
        try:
            self.tab_widget.setTabVisible(index, visible)
        except Exception:
            tab.setVisible(visible)
        if visible:
            maximized = bool(getattr(self.tab_widget, "_global_maximized", False))
            fitter = getattr(self.tab_widget, "_fit_subwindow", None)
            maybe_maximize = getattr(self.tab_widget, "_maybe_apply_maximize", None)
            self._hidden_tabs.discard(tab)
            sub = self.tab_widget._subwindow_for(tab)  # type: ignore[attr-defined]
            if sub is not None and sub in self._hidden_subwindows:
                self._hidden_subwindows.discard(sub)
                sub.show()
                if maximized:
                    if callable(maybe_maximize):
                        maybe_maximize(sub)
                elif callable(fitter):
                    needs_fit = True
                    try:
                        geom = sub.geometry()
                        needs_fit = (
                            (not geom.isValid())
                            or geom.width() < 220
                            or geom.height() < 160
                        )
                    except Exception:
                        needs_fit = True
                    if needs_fit:
                        fitter(
                            sub,
                            use_half_width=True,
                            preferred_width=None,
                            remember_manual=False,
                        )
        else:
            self._hidden_tabs.add(tab)

    def _record_history_action(
        self,
        description: str,
        *,
        undo: Callable[[], None],
        redo: Callable[[], None],
    ) -> None:
        if self._history.is_replaying:
            return
        self._history.record(description, undo, redo)
        self._update_history_actions()

    def undo(self) -> None:
        self._history.undo()
        self._update_tab_buttons()
        self._update_history_actions()

    def redo(self) -> None:
        self._history.redo()
        self._update_tab_buttons()
        self._update_history_actions()

    def _update_tab_buttons(self) -> None:
        tab_bar = getattr(self.tab_widget, "tabBar", lambda: None)()
        if not isinstance(tab_bar, QtWidgets.QTabBar):
            return
        for index in range(self.tab_widget.count()):
            button = tab_bar.tabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide)
            if button is not None and bool(button.property("mw_tab_controls")):
                tab_bar.setTabButton(index, QtWidgets.QTabBar.ButtonPosition.RightSide, None)

        current_index = self.tab_widget.currentIndex()
        if current_index < 0:
            return
        tab = self.tab_widget.widget(current_index)
        if tab is None:
            return

        container = QtWidgets.QWidget()
        container.setProperty("mw_tab_controls", True)
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        style = self.style()

        minimize_button = QtWidgets.QToolButton(container)
        minimize_icon = style.standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_TitleBarMinButton
        )
        if minimize_icon.isNull():
            minimize_button.setText("-")
        else:
            minimize_button.setIcon(minimize_icon)
            minimize_button.setText("")
        minimize_button.setAutoRaise(sys.platform == "darwin")
        minimize_button.setToolTip("Hide this tab")
        minimize_button.clicked.connect(lambda _, t=tab: self._minimize_tab(t))
        layout.addWidget(minimize_button)

        if tab in self._tab_descriptors:
            close_button = QtWidgets.QToolButton(container)
            close_icon = style.standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_TitleBarCloseButton
            )
            if close_icon.isNull():
                close_icon = style.standardIcon(
                    QtWidgets.QStyle.StandardPixmap.SP_DockWidgetCloseButton
                )
            if close_icon.isNull():
                close_button.setText("x")
            else:
                close_button.setIcon(close_icon)
                close_button.setText("")
            close_button.setAutoRaise(sys.platform == "darwin")
            close_button.setToolTip("Close this graph tab")
            close_button.clicked.connect(lambda _, t=tab: self._close_tab(t))
            layout.addWidget(close_button)

        tab_bar.setTabButton(
            current_index,
            QtWidgets.QTabBar.ButtonPosition.RightSide,
            container,
        )

    def _minimize_tab(self, tab: QtWidgets.QWidget) -> None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return
        previous_widget = self.tab_widget.currentWidget()
        alternate_index = None
        if previous_widget is tab:
            alternate_index = self._find_alternate_tab_index(index)
        alternate_widget = (
            self.tab_widget.widget(alternate_index) if alternate_index is not None else None
        )

        def _apply_hide() -> None:
            self._set_tab_visibility(tab, False)
            target = alternate_widget if alternate_widget is not None else previous_widget
            if target is not None and target is not tab:
                target_index = self.tab_widget.indexOf(target)
                if target_index >= 0 and self._is_tab_visible(target):
                    self.tab_widget.setCurrentIndex(target_index)
            self._update_tab_buttons()

        def _restore() -> None:
            self._set_tab_visibility(tab, True)
            restored_index = self.tab_widget.indexOf(tab)
            if restored_index >= 0:
                self.tab_widget.setCurrentIndex(restored_index)
            self._update_tab_buttons()

        _apply_hide()
        self._record_history_action(
            f"Hide tab {self.tab_widget.tabText(index)}",
            undo=_restore,
            redo=_apply_hide,
        )

    def _find_alternate_tab_index(self, current_index: int) -> int | None:
        count = self.tab_widget.count()
        for offset in range(1, count):
            forward = (current_index + offset) % count
            if self._is_tab_visible(self.tab_widget.widget(forward)):
                return forward
        return None

    def _clear_tab_list(self, tabs: Iterable[QtWidgets.QWidget]) -> None:
        """Remove tabs using the same internal cleanup path as manual close actions."""

        for tab in list(tabs):
            if not isinstance(tab, QtWidgets.QWidget):
                continue
            if self.tab_widget.indexOf(tab) < 0:
                self._remove_shared_plot_workbook_for_tab(tab)
                continue
            info = self._remove_tab_internal(tab)
            if info is None:
                try:
                    index = self.tab_widget.indexOf(tab)
                    if index >= 0:
                        self.tab_widget.removeTab(index)
                except Exception:
                    pass
                self._remove_shared_plot_workbook_for_tab(tab)
        self._update_tab_buttons()
        self._rebuild_object_manager_for_tab(self.tab_widget.currentWidget())

    def _close_tab(self, tab: QtWidgets.QWidget) -> None:
        info = self._remove_tab_internal(tab)
        if info is None:
            return

        def _redo() -> None:
            self._remove_tab_internal(tab)

        def _undo() -> None:
            self._restore_tab_from_info(info)

        self._record_history_action(f"Close tab {info.title}", undo=_undo, redo=_redo)
        self._update_tab_buttons()

    def _remove_tab_internal(self, tab: QtWidgets.QWidget) -> _RemovedTabInfo | None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return None
        title = self.tab_widget.tabText(index)
        icon = self.tab_widget.tabIcon(index)
        descriptor = self._tab_descriptors.pop(tab, None)
        canvas = self._canvas_by_tab.pop(tab, None)
        axes = self._axes_by_tab.pop(tab, None)
        if canvas is not None:
            helper = self._navigation_helpers.pop(canvas, None)
            if helper is not None:
                helper.setParent(None)
                helper.deleteLater()
            if self._nav_active_canvas is canvas:
                self._deactivate_navigation_mode()
            cid = self._cursor_connections.pop(canvas, None)
            if cid is not None:
                try:
                    canvas.mpl_disconnect(cid)
                except Exception:
                    pass
            edit_cid = self._edit_connections.pop(canvas, None)
            if edit_cid is not None:
                try:
                    canvas.mpl_disconnect(edit_cid)
                except Exception:
                    pass
            resize_cid = self._resize_connections.pop(canvas, None)
            if resize_cid is not None:
                try:
                    canvas.mpl_disconnect(resize_cid)
                except Exception:
                    pass
            motion_cid = self._annotation_motion_connections.pop(canvas, None)
            if motion_cid is not None:
                try:
                    canvas.mpl_disconnect(motion_cid)
                except Exception:
                    pass
            release_cid = self._annotation_release_connections.pop(canvas, None)
            if release_cid is not None:
                try:
                    canvas.mpl_disconnect(release_cid)
                except Exception:
                    pass
            copy_shortcut = self._canvas_copy_shortcuts.pop(canvas, None)
            if copy_shortcut is not None:
                try:
                    copy_shortcut.setParent(None)
                    copy_shortcut.deleteLater()
                except Exception:
                    pass
            self._canvas_base_size_inches.pop(canvas, None)
            self._canvas_base_dpi.pop(canvas, None)
            self._canvas_display_scale.pop(canvas, None)
            self._canvas_scaling_guard.discard(canvas)
        item = self._graph_tree_items.pop(tab, None)
        if item is not None:
            parent = item.parent()
            if parent is not None:
                parent.removeChild(item)
            else:
                top_index = self.project_tree.indexOfTopLevelItem(item)
                if top_index >= 0:
                    self.project_tree.takeTopLevelItem(top_index)
        worksheet_key = self._tab_to_worksheet_key.pop(tab, None)
        if worksheet_key is not None:
            self._worksheet_tabs_open.pop(worksheet_key, None)
            self._update_worksheet_item_state(worksheet_key)
        self._remove_shared_plot_workbook_for_tab(tab)
        was_hidden = tab in self._hidden_tabs
        self._hidden_tabs.discard(tab)
        was_current = self.tab_widget.currentWidget() is tab
        self.tab_widget.removeTab(index)
        info = _RemovedTabInfo(
            tab=tab,
            index=index,
            title=title,
            icon=icon,
            descriptor=descriptor,
            canvas=canvas,
            axes=axes,
            worksheet_key=worksheet_key,
            was_hidden=was_hidden,
            was_current=was_current,
        )
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        self._update_navigation_enabled()
        self._rebuild_object_manager_for_tab(self.tab_widget.currentWidget())
        self._after_tab_removed(info)
        return info

    def _normalize_docks_initial(self) -> None:
        """Ensure visible primary docks start at readable sizes."""

        if getattr(self, "_normalizing_docks", False):
            return
        now = time.monotonic()
        last_run = getattr(self, "_last_normalize_docks_ts", 0.0)
        if now - last_run < 0.1:
            return
        self._normalizing_docks = True
        self._last_normalize_docks_ts = now
        try:
            dock_specs: list[tuple[QtWidgets.QDockWidget, int]] = []
            for dock in (
                getattr(self, "project_dock", None),
                getattr(self, "object_dock", None),
            ):
                if not isinstance(dock, QtWidgets.QDockWidget):
                    continue
                if not self._primary_dock_should_show(dock):
                    continue
                width = self._primary_dock_target_width(dock, PRIMARY_DOCK_DEFAULT_WIDTH)
                dock_specs.append((dock, width))
                try:
                    dock.setFloating(False)
                    dock.show()
                except Exception:
                    pass
            for dock, width in dock_specs:
                try:
                    self._apply_dock_width(dock, width)
                except Exception:
                    pass
            if dock_specs:
                docks = [dock for dock, _ in dock_specs]
                widths = [width for _, width in dock_specs]
                try:
                    self.resizeDocks(docks, widths, QtCore.Qt.Orientation.Horizontal)
                except Exception:
                    pass
            panels = [
                panel
                for panel in getattr(self, "_dock_switcher_panels", [])
                if isinstance(panel, QtWidgets.QDockWidget)
            ]
            for panel in panels:
                panel_width = max(panel.minimumWidth(), panel.sizeHint().width(), 0)
                if panel_width <= 0:
                    continue
                try:
                    self._apply_dock_width(panel, panel_width)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._normalizing_docks = False

    def _normalize_single_visible_graph_subwindow(self) -> None:
        tab_widget = getattr(self, "tab_widget", None)
        visible_getter = getattr(tab_widget, "_ordered_visible_subwindows", None)
        visible_count: int | None = None
        visible = None
        if callable(visible_getter):
            try:
                visible = visible_getter()
            except Exception:
                visible = None
            if isinstance(visible, (list, tuple)):
                visible_count = len(visible)
        if visible_count is not None and visible_count > 1:
            return
        # Preserve user-managed subwindow geometry; only refresh figure layout.
        if isinstance(visible, (list, tuple)) and len(visible) == 1:
            sub = visible[0]
            refresher = getattr(tab_widget, "_refresh_subwindow_figure_layout", None)
            if callable(refresher):
                try:
                    QtCore.QTimer.singleShot(
                        0,
                        lambda s=sub, r=refresher: r(s, deferred=True),
                    )
                except Exception:
                    pass

    def changeEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            QtCore.QTimer.singleShot(0, self._refresh_primary_dock_layout)
            QtCore.QTimer.singleShot(20, self._normalize_docks_initial)
            QtCore.QTimer.singleShot(0, self._refresh_status_bar_layout)
            return
        if event.type() == QtCore.QEvent.Type.ActivationChange and self.isActiveWindow():
            self._normalize_single_visible_graph_subwindow()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_status_bar_layout()
        if self._primary_dock_layout_refreshing:
            return
        if self._dock_resize_refresh_pending:
            return
        self._dock_resize_refresh_pending = True

        def _refresh_after_resize() -> None:
            self._dock_resize_refresh_pending = False
            if self._primary_dock_layout_refreshing:
                return
            try:
                self._refresh_primary_dock_layout()
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, _refresh_after_resize)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_status_bar_layout()
        if not getattr(self, "_layout_fixed_once", False):
            self._layout_fixed_once = True
            QtCore.QTimer.singleShot(0, self._apply_initial_dock_sizes)
            QtCore.QTimer.singleShot(0, self._refresh_primary_dock_layout)
            QtCore.QTimer.singleShot(10, self._ensure_primary_docks_pinned)
            QtCore.QTimer.singleShot(25, self._queue_retabify_primary_docks)
            QtCore.QTimer.singleShot(50, self._normalize_docks_initial)
            if sys.platform != "darwin":
                QtCore.QTimer.singleShot(150, self._normalize_docks_initial)
        if sys.platform == "darwin":
            QtCore.QTimer.singleShot(150, self._normalize_docks_initial)
        else:
            QtCore.QTimer.singleShot(100, self._normalize_docks_initial)
            QtCore.QTimer.singleShot(300, self._normalize_docks_initial)

    def _restore_tab_from_info(self, info: _RemovedTabInfo) -> None:
        insert_index = min(info.index, self.tab_widget.count())
        self.tab_widget.insertTab(insert_index, info.tab, info.title)
        if info.icon is not None:
            self.tab_widget.setTabIcon(insert_index, info.icon)
        if info.descriptor is not None:
            self._register_plot_tab(info.tab, info.canvas, info.axes, info.descriptor)
        else:
            if info.canvas is not None:
                self._canvas_by_tab[info.tab] = info.canvas
            if info.axes is not None:
                self._axes_by_tab[info.tab] = info.axes
        if info.worksheet_key is not None:
            self._worksheet_tabs_open[info.worksheet_key] = info.tab
            self._tab_to_worksheet_key[info.tab] = info.worksheet_key
            self._update_worksheet_item_state(info.worksheet_key)
        self._set_tab_visibility(info.tab, not info.was_hidden)
        if info.was_current or not info.was_hidden:
            restored_index = self.tab_widget.indexOf(info.tab)
            if restored_index >= 0:
                self.tab_widget.setCurrentIndex(restored_index)
        self._update_tab_buttons()
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        if self._dark_mode_enabled and info.axes is not None:
            self._apply_dark_mode_to_axes(info.axes, True)
        self._update_navigation_enabled()
        self._rebuild_object_manager_for_tab(self.tab_widget.currentWidget())
        self._after_tab_restored(info)

    def _after_tab_removed(self, info: _RemovedTabInfo) -> None:
        _ = info

    def _after_tab_restored(self, info: _RemovedTabInfo) -> None:
        _ = info

    # ------------------------------------------------------------------ state helpers
    def _handle_current_tab_changed(self, index: int) -> None:
        tab = self.tab_widget.widget(index) if index >= 0 else None
        active_mode = self._nav_mode if self._nav_mode in {"zoom", "pan"} else None
        self._set_navigation_mode(active_mode)
        self._update_tab_buttons()
        self._focus_tree_on_tab(tab)
        if self._project_tree_focus_restore_pending:
            tree = getattr(self, "project_tree", None)
            if isinstance(tree, QtWidgets.QTreeWidget):
                self._restore_project_tree_focus(tree)
            self._project_tree_focus_restore_pending = False
        self._rebuild_object_manager_for_tab(tab)
        self._update_worksheet_actions()
        # Update cursor label immediately when switching tabs
        self._update_cursor_status(None)
        self._update_navigation_enabled()
        if tab is not None:
            axes = self._axes_by_tab.get(tab)
            figure = getattr(axes, "figure", None) if axes is not None else None
            fit_to_content = getattr(self, "_fit_figure_to_content", None)
            if figure is not None and callable(fit_to_content):
                try:
                    QtCore.QTimer.singleShot(
                        0,
                        lambda fig=figure, fitter=fit_to_content: fitter(fig),
                    )
                except Exception:
                    pass

    def _focus_tree_on_tab(self, tab: QtWidgets.QWidget | None) -> None:
        self._project_tree_selection_sync_blocked = True
        self.project_tree.blockSignals(True)
        try:
            self.project_tree.clearSelection()
            target_item: QtWidgets.QTreeWidgetItem | None = None
            if tab is not None:
                if tab in self._tab_descriptors:
                    target_item = self._graph_tree_items.get(tab)
                else:
                    key = self._tab_to_worksheet_key.get(tab)
                    if key is not None:
                        target_item = self._worksheet_tree_items.get(key)
            if target_item is not None:
                if not self._is_live_tree_item(target_item):
                    target_item = None
                if target_item is not None:
                    try:
                        self.project_tree.setCurrentItem(target_item)
                        self.project_tree.scrollToItem(target_item)
                    except RuntimeError:
                        pass
        finally:
            self.project_tree.blockSignals(False)
            self._project_tree_selection_sync_blocked = False

    # ------------------------------------------------------------------ window menu hooks
    def _mdi_area(self) -> QtWidgets.QMdiArea | None:
        """Return the QMdiArea backing the tab proxy when available."""

        mdi_area = getattr(self.tab_widget, "_mdi", None)
        return mdi_area if isinstance(mdi_area, QtWidgets.QMdiArea) else None

    def _window_menu_arrangement_targets(self) -> list[QtWidgets.QWidget]:
        """Expose currently managed MDI subwindows for the shared Window menu."""

        area = self._mdi_area()
        if area is None:
            return []
        subwindows: list[QtWidgets.QWidget] = []
        for sub in area.subWindowList():
            if isinstance(sub, QtWidgets.QMdiSubWindow) and sub.widget() is not None and not sub.isHidden():
                subwindows.append(sub)
        return subwindows

    def _window_menu_cascade(self, widgets: Sequence[QtWidgets.QWidget]) -> None:
        """Cascade visible graph/workbook subwindows inside the workspace."""

        _ = widgets
        proxy = getattr(self, "tab_widget", None)
        setter = getattr(proxy, "set_arrangement_mode", None)
        if callable(setter):
            try:
                setter("cascade")
                return
            except Exception:
                pass
        area = self._mdi_area()
        if area is None:
            return
        area.cascadeSubWindows()

    def _window_menu_tile(
        self,
        widgets: Sequence[QtWidgets.QWidget],
        *,
        orientation: Literal["vertical", "horizontal"],
    ) -> None:
        """Tile visible subwindows within the QMdiArea using the requested orientation."""

        _ = widgets
        proxy = getattr(self, "tab_widget", None)
        setter = getattr(proxy, "set_arrangement_mode", None)
        if callable(setter):
            token = (
                "tile_vertical"
                if orientation == "vertical"
                else "tile_horizontal"
            )
            try:
                setter(token)
                return
            except Exception:
                pass
        area = self._mdi_area()
        if area is None:
            return
        subwindows = [
            sub
            for sub in area.subWindowList()
            if isinstance(sub, QtWidgets.QMdiSubWindow) and sub.widget() is not None and not sub.isHidden()
        ]
        if not subwindows:
            return
        viewport = area.viewport().rect()
        if viewport.width() <= 0 or viewport.height() <= 0:
            area.tileSubWindows()
            return
        for sub in subwindows:
            try:
                sub.showNormal()
            except Exception:
                pass
        if orientation == "vertical":
            base_width = max(1, viewport.width())
            column_width = max(160, base_width // len(subwindows))
            for index, sub in enumerate(subwindows):
                x = viewport.left() + index * column_width
                remaining = viewport.right() - x + 1
                current_width = column_width if index < len(subwindows) - 1 else max(column_width, remaining)
                sub.setGeometry(
                    x,
                    viewport.top(),
                    current_width,
                    viewport.height(),
                )
        elif orientation == "horizontal":
            base_height = max(1, viewport.height())
            row_height = max(140, base_height // len(subwindows))
            for index, sub in enumerate(subwindows):
                y = viewport.top() + index * row_height
                remaining = viewport.bottom() - y + 1
                current_height = row_height if index < len(subwindows) - 1 else max(row_height, remaining)
                sub.setGeometry(
                    viewport.left(),
                    y,
                    viewport.width(),
                    current_height,
                )
        else:
            area.tileSubWindows()
            return
        area.viewport().update()


class _ManagedSubWindow(QtWidgets.QMdiSubWindow):
    """QMdiSubWindow that avoids deleting its widget on close."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._aspect_ratio: float | None = None
        self._owner: _MdiTabProxy | None = None
        self._resizing = False
        self._handling_change = False
        self.setWindowFlag(QtCore.Qt.WindowType.Tool, False)
        set_resizable = getattr(QtWidgets.QMdiSubWindow, "setWidgetResizable", None)
        if callable(set_resizable):
            try:
                set_resizable(self, True)
            except Exception:
                pass
        try:
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
        except Exception:
            pass

    def set_owner(self, owner: "_MdiTabProxy") -> None:
        self._owner = owner

    def set_aspect_ratio(self, ratio: float | None) -> None:
        if ratio is None or ratio <= 0:
            return
        self._aspect_ratio = float(ratio)

    def _sync_child_geometry(self) -> None:
        widget = self.widget()
        if widget is None:
            return
        try:
            rect = self.contentsRect()
        except Exception:
            rect = QtCore.QRect()
        if not rect.isValid() or rect.width() <= 0 or rect.height() <= 0:
            return
        try:
            widget.setMinimumSize(0, 0)
        except Exception:
            pass
        try:
            widget.setGeometry(rect)
        except Exception:
            try:
                widget.resize(rect.size())
            except Exception:
                pass

    def _sync_child_geometry_deferred(self) -> None:
        try:
            QtCore.QTimer.singleShot(0, self._sync_child_geometry)
        except Exception:
            self._sync_child_geometry()

    def changeEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if self._handling_change:
            return
        self._handling_change = True
        try:
            if (
                event.type() == QtCore.QEvent.Type.WindowStateChange
                and self._owner is not None
                and not self._owner._syncing_state  # noqa: SLF001
            ):
                old_state = event.oldState()
                was_maximized = bool(
                    old_state
                    & (
                        QtCore.Qt.WindowState.WindowMaximized
                        | QtCore.Qt.WindowState.WindowFullScreen
                    )
                )
                setattr(self, "_mw_last_old_maximized", was_maximized)
                self._owner._handle_subwindow_state_change(  # noqa: SLF001
                    self.isMaximized() or self.isFullScreen(),
                    source=self,
                )
                if event.oldState() & QtCore.Qt.WindowState.WindowMinimized:
                    normalizer = getattr(self._owner, "_normalize_docks_initial", None)  # noqa: SLF001
                    if callable(normalizer):
                        QtCore.QTimer.singleShot(30, normalizer)
        finally:
            self._handling_change = False

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        # hide instead of destroying so the graph can be reopened from Project Explorer
        event.ignore()
        self.hide()
        if self._owner is not None:
            self._owner._handle_subwindow_hidden(self)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        if self._resizing or self._owner is None:
            result = super().resizeEvent(event)
            self._sync_child_geometry()
            self._sync_child_geometry_deferred()
            return result
        if getattr(self._owner, "_global_maximized", False) or getattr(self._owner, "_fullscreen_lock", False):
            result = super().resizeEvent(event)
            self._sync_child_geometry()
            self._sync_child_geometry_deferred()
            return result
        if getattr(self._owner, "_arranging_subwindows", False):
            result = super().resizeEvent(event)
            self._sync_child_geometry()
            self._sync_child_geometry_deferred()
            return result
        if self._aspect_ratio is None:
            result = super().resizeEvent(event)
            self._sync_child_geometry()
            self._sync_child_geometry_deferred()
            return result
        try:
            buttons = QtWidgets.QApplication.mouseButtons()
        except Exception:
            buttons = QtCore.Qt.MouseButton.NoButton
        # Ignore synthetic/background resizes (for example task switching) and
        # only enforce aspect when the user is actively dragging the frame.
        if not bool(buttons & QtCore.Qt.MouseButton.LeftButton):
            result = super().resizeEvent(event)
            self._sync_child_geometry()
            self._sync_child_geometry_deferred()
            return result
        self._resizing = True
        try:
            self._owner._fit_subwindow(  # noqa: SLF001
                self,
                use_half_width=False,
                preferred_width=event.size().width(),
                remember_manual=True,
            )
        finally:
            self._resizing = False
        super().resizeEvent(event)
        self._sync_child_geometry()
        self._sync_child_geometry_deferred()


class _MdiTabProxy(QtWidgets.QWidget):
    """Proxy that mimics a subset of QTabWidget behaviour using a QMdiArea backend."""

    currentChanged = QtCore.pyqtSignal(int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._mdi = QtWidgets.QMdiArea(self)
        self._mdi.setViewMode(QtWidgets.QMdiArea.ViewMode.SubWindowView)
        self._native_subwindow_maximize = sys.platform != "darwin"
        self._mdi.setOption(
            QtWidgets.QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation,
            not self._native_subwindow_maximize,
        )
        self._mdi.viewport().installEventFilter(self)
        layout.addWidget(self._mdi)

        self._widgets: list[QtWidgets.QWidget] = []
        self._subwindows: dict[QtWidgets.QWidget, _ManagedSubWindow] = {}
        self._titles: dict[QtWidgets.QWidget, str] = {}
        self._icons: dict[QtWidgets.QWidget, QtGui.QIcon] = {}
        self._visible: dict[QtWidgets.QWidget, bool] = {}
        self._blocking = False
        self._global_maximized = False
        self._fullscreen_lock = False
        self._syncing_state = False
        self._hidden_subwindows: set[_ManagedSubWindow] = set()
        self._hidden_tabs: set[QtWidgets.QWidget] = set()
        self._maximized_hidden: set[_ManagedSubWindow] = set()
        self._max_visible_windows: int = 6
        self._visibility_queue: list[_ManagedSubWindow] = []
        self._arrange_generation = 0
        self._mdi.subWindowActivated.connect(self._handle_subwindow_activated)
        try:
            self._mdi.setContentsMargins(0, 0, 0, 0)
            self._mdi.setViewportMargins(0, 0, 0, 0)
        except Exception:
            pass
        self._layout_margin = 6
        self._arranging_subwindows = False
        self._arrangement_mode: Literal["cascade", "tile_vertical", "tile_horizontal"] = "cascade"

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _is_live_subwindow(sub: QtWidgets.QMdiSubWindow | None) -> bool:
        if not isinstance(sub, _ManagedSubWindow):
            return False
        try:
            sub.isHidden()
            sub.widget()
        except RuntimeError:
            return False
        except Exception:
            return False
        return True

    def _purge_stale_subwindow_refs(self) -> None:
        live_map: dict[QtWidgets.QWidget, _ManagedSubWindow] = {}
        for widget, sub in list(self._subwindows.items()):
            if not self._is_live_subwindow(sub):
                continue
            live_map[widget] = sub
        self._subwindows = live_map
        self._widgets = [widget for widget in self._widgets if widget in self._subwindows]
        self._hidden_subwindows = {
            sub for sub in self._hidden_subwindows if self._is_live_subwindow(sub)
        }
        self._maximized_hidden = {
            sub for sub in self._maximized_hidden if self._is_live_subwindow(sub)
        }
        self._visibility_queue = [
            sub for sub in self._visibility_queue if self._is_live_subwindow(sub)
        ]

    @staticmethod
    def _remember_subwindow_geometry(sub: _ManagedSubWindow | None) -> None:
        if not isinstance(sub, _ManagedSubWindow):
            return
        try:
            if sub.isHidden():
                existing = getattr(sub, "_mw_saved_geometry", None)
                if isinstance(existing, QtCore.QRect) and existing.isValid():
                    return
        except Exception:
            pass
        try:
            if sub.isMaximized() or sub.isFullScreen():
                return
        except Exception:
            pass
        try:
            geom = sub.geometry()
        except Exception:
            return
        if not geom.isValid() or geom.width() < 120 or geom.height() < 90:
            return
        try:
            setattr(sub, "_mw_saved_geometry", QtCore.QRect(geom))
        except Exception:
            pass

    @staticmethod
    def _restore_saved_subwindow_geometry(sub: _ManagedSubWindow | None) -> bool:
        if not isinstance(sub, _ManagedSubWindow):
            return False
        stored = getattr(sub, "_mw_saved_geometry", None)
        if not isinstance(stored, QtCore.QRect):
            return False
        if not stored.isValid() or stored.width() < 120 or stored.height() < 90:
            return False
        try:
            sub.setGeometry(QtCore.QRect(stored))
            return True
        except Exception:
            return False

    def _subwindow_aspect_ratio(self, sub: _ManagedSubWindow | None) -> float:
        if sub is None:
            return 1.6
        stored_ratio = getattr(sub, "_aspect_ratio", None)
        try:
            stored_value = float(stored_ratio)
        except Exception:
            stored_value = 0.0
        if stored_value > 0.0:
            return stored_value
        widget = sub.widget()
        if widget is None:
            return float(getattr(sub, "_aspect_ratio", 1.6) or 1.6)
        canvas: FigureCanvas | None = None
        if isinstance(widget, FigureCanvas):
            canvas = widget
        else:
            try:
                canvases = widget.findChildren(FigureCanvas)
            except Exception:
                canvases = []
            if canvases:
                canvas = canvases[0]
        if canvas is not None:
            try:
                size = canvas.figure.get_size_inches()
                if len(size) >= 2:
                    width = float(size[0])
                    height = float(size[1])
                    if width > 0 and height > 0:
                        ratio = width / height
                        sub.set_aspect_ratio(ratio)
                        return ratio
            except Exception:
                pass
        fallback = float(getattr(sub, "_aspect_ratio", 1.6) or 1.6)
        if fallback <= 0:
            fallback = 1.6
        return fallback

    @staticmethod
    def _canvas_for_subwindow(sub: _ManagedSubWindow | None) -> FigureCanvas | None:
        if sub is None:
            return None
        widget = sub.widget()
        if isinstance(widget, FigureCanvas):
            return widget
        if widget is None:
            return None
        try:
            canvases = widget.findChildren(FigureCanvas)
        except Exception:
            canvases = []
        return canvases[0] if canvases else None

    @staticmethod
    def _sync_subwindow_child_widget(sub: _ManagedSubWindow | None) -> None:
        if not isinstance(sub, _ManagedSubWindow):
            return
        sync = getattr(sub, "_sync_child_geometry", None)
        if callable(sync):
            try:
                sync()
            except Exception:
                pass

    def _refresh_subwindow_figure_layout(
        self,
        sub: _ManagedSubWindow | None,
        *,
        deferred: bool = False,
    ) -> None:
        canvas = self._canvas_for_subwindow(sub)
        if canvas is None:
            return
        figure = getattr(canvas, "figure", None)
        if figure is None:
            return

        def _apply_layout() -> None:
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
            else:
                tight_layout = getattr(figure, "tight_layout", None)
                if callable(tight_layout):
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", UserWarning)
                            tight_layout(pad=1.0)
                    except Exception:
                        pass
            try:
                canvas.draw_idle()
            except Exception:
                try:
                    canvas.draw()
                except Exception:
                    pass

        if deferred:
            try:
                QtCore.QTimer.singleShot(0, _apply_layout)
            except Exception:
                _apply_layout()
            return
        _apply_layout()

    @staticmethod
    def _fit_rect_to_aspect(rect: QtCore.QRect, aspect: float) -> QtCore.QRect:
        if not rect.isValid() or rect.width() <= 0 or rect.height() <= 0:
            return rect
        if aspect <= 0:
            return rect
        width = rect.width()
        height = rect.height()
        target_w = width
        target_h = int(round(target_w / aspect))
        if target_h > height:
            target_h = height
            target_w = int(round(target_h * aspect))
        target_w = max(1, target_w)
        target_h = max(1, target_h)
        x = rect.left() + (width - target_w) // 2
        y = rect.top() + (height - target_h) // 2
        return QtCore.QRect(x, y, target_w, target_h)

    def _apply_fullscreen_geometry(self, sub: _ManagedSubWindow) -> bool:
        viewport = self._mdi.viewport() if self._mdi is not None else None
        if viewport is None:
            return False
        margin = self._layout_margin
        rect = viewport.rect().adjusted(margin, margin, -margin, -margin)
        if not rect.isValid() or rect.width() < 40 or rect.height() < 40:
            return False
        # Fullscreen mode should use the entire viewport. Preserving the figure
        # aspect here can shrink "fullscreen" windows to thin strips.
        sub.setGeometry(rect)
        self._sync_subwindow_child_widget(sub)
        self._refresh_subwindow_figure_layout(sub, deferred=True)
        return True

    def _is_maximized_target(self, sub: _ManagedSubWindow | None) -> bool:
        if not self._is_live_subwindow(sub):
            return False
        if not (self._global_maximized or self._fullscreen_lock):
            return False
        active = self._mdi.activeSubWindow()
        return active is sub

    def _maybe_apply_maximize(self, sub: _ManagedSubWindow) -> None:
        self._apply_fullscreen_geometry(sub)
        sub.show()

    def _handle_subwindow_activated(self, sub: QtWidgets.QMdiSubWindow | None) -> None:
        if self._blocking:
            return
        self._purge_stale_subwindow_refs()
        if sub is not None and not self._is_live_subwindow(sub):
            return
        was_global_maximized = bool(self._global_maximized or self._fullscreen_lock)
        widget = sub.widget() if sub is not None else None
        index = self.indexOf(widget) if widget is not None else -1
        if sub is not None:
            is_max = sub.isMaximized() or sub.isFullScreen() or self._fullscreen_lock
            if is_max:
                self._fullscreen_lock = True
                self._global_maximized = True
                self._maximize_single(sub)
            else:
                self._fullscreen_lock = False
                self._global_maximized = False
                if was_global_maximized:
                    self._apply_global_window_state(active=sub)
            self._register_visible(sub)
        self.currentChanged.emit(index)

    def _subwindow_for(self, widget: QtWidgets.QWidget | None) -> _ManagedSubWindow | None:
        if widget is None:
            return None
        return self._subwindows.get(widget)

    def _activate_index(self, index: int) -> None:
        self._purge_stale_subwindow_refs()
        if not 0 <= index < len(self._widgets):
            self._mdi.setActiveSubWindow(None)
            self.currentChanged.emit(-1)
            return
        widget = self._widgets[index]
        sub = self._subwindow_for(widget)
        if not self._is_live_subwindow(sub):
            return
        was_global_maximized = bool(self._global_maximized or self._fullscreen_lock)
        restored_from_hidden = False
        self._blocking = True
        if sub in self._hidden_subwindows or sub in self._maximized_hidden:
            restored_from_hidden = True
            self._hidden_subwindows.discard(sub)
            self._maximized_hidden.discard(sub)
            sub.show()
            self._register_visible(sub)
        self._mdi.setActiveSubWindow(sub)
        if was_global_maximized:
            self._maximize_single(sub)
        else:
            sub.showNormal()
            if restored_from_hidden:
                restored_geom = self._restore_saved_subwindow_geometry(sub)
                if not restored_geom:
                    needs_fit = True
                    try:
                        geom = sub.geometry()
                        needs_fit = (
                            (not geom.isValid())
                            or geom.width() < 220
                            or geom.height() < 160
                        )
                    except Exception:
                        needs_fit = True
                    if needs_fit:
                        self._fit_subwindow(
                            sub,
                            use_half_width=False,
                            remember_manual=False,
                        )
                self._refresh_subwindow_figure_layout(sub, deferred=True)
        sub.raise_()
        self._blocking = False
        self.currentChanged.emit(index)

    def _handle_subwindow_state_change(
        self,
        maximized: bool,
        *,
        source: _ManagedSubWindow | None = None,
    ) -> None:
        if self._syncing_state:
            return
        self._purge_stale_subwindow_refs()
        if source is not None and not self._is_live_subwindow(source):
            source = None
        source_was_maximized = False
        if isinstance(source, _ManagedSubWindow):
            try:
                source_was_maximized = bool(getattr(source, "_mw_last_old_maximized", False))
            except Exception:
                source_was_maximized = False
            try:
                delattr(source, "_mw_last_old_maximized")
            except Exception:
                pass
        self._syncing_state = True
        try:
            active = self._mdi.activeSubWindow()
            if maximized:
                self._fullscreen_lock = True
                self._global_maximized = True
                if active is not None:
                    self._maximize_single(active)
                else:
                    self._apply_global_window_state()
            else:
                if (
                    isinstance(source, _ManagedSubWindow)
                    and self._is_live_subwindow(source)
                ):
                    try:
                        if not source.isMaximized() and not source.isFullScreen():
                            self._fullscreen_lock = False
                            self._global_maximized = False
                            self._apply_global_window_state(active=source if source is active else None)
                            return
                    except Exception:
                        pass
                if self._fullscreen_lock or self._global_maximized:
                    if not self._native_subwindow_maximize and not source_was_maximized:
                        QtCore.QTimer.singleShot(0, self._reconcile_maximized_state)
                        return
                    # Ignore demote events emitted by non-active subwindows while
                    # a different subwindow is intentionally fullscreen/maximized.
                    if source is not None and active is not None and source is not active:
                        return
                    if active is None:
                        if (
                            isinstance(source, _ManagedSubWindow)
                            and self._is_live_subwindow(source)
                            and source not in self._hidden_subwindows
                        ):
                            active = source
                        else:
                            QtCore.QTimer.singleShot(0, self._reconcile_maximized_state)
                            return
                    if isinstance(active, _ManagedSubWindow) and self._is_live_subwindow(active):
                        try:
                            if active.isMaximized() or active.isFullScreen():
                                self._maximize_single(active)
                                return
                        except Exception:
                            self._maximize_single(active)
                            return
                        if source is None:
                            QtCore.QTimer.singleShot(0, self._reconcile_maximized_state)
                            return
                self._fullscreen_lock = False
                self._global_maximized = False
                self._apply_global_window_state()
        finally:
            self._syncing_state = False

    def _handle_subwindow_hidden(self, sub: _ManagedSubWindow) -> None:
        if not self._is_live_subwindow(sub):
            return
        self._remember_subwindow_geometry(sub)
        self._hidden_subwindows.add(sub)
        # ensure tab visibility matches hidden state
        widget = sub.widget()
        if widget is not None:
            self._set_tab_visibility(widget, False)
        if self._mdi.activeSubWindow() is sub:
            self._mdi.setActiveSubWindow(None)
        try:
            self._visibility_queue.remove(sub)
        except ValueError:
            pass
        if self._global_maximized or self._fullscreen_lock:
            QtCore.QTimer.singleShot(0, self._reconcile_maximized_state)

    def _apply_global_window_state(self, *, active: _ManagedSubWindow | None = None) -> None:
        self._purge_stale_subwindow_refs()
        self._syncing_state = True
        try:
            active_sub = active or self._mdi.activeSubWindow()
            if self._global_maximized or self._fullscreen_lock:
                if active_sub is not None:
                    self._maximize_single(active_sub)
                else:
                    self._reconcile_maximized_state()
            else:
                for sub in self._subwindows.values():
                    if sub in self._hidden_subwindows:
                        continue
                    sub.show()
                    sub.showNormal()
                    self._maximized_hidden.discard(sub)
                self._arrange_subwindows()
        finally:
            self._syncing_state = False

    def _reconcile_maximized_state(self) -> None:
        if not (self._global_maximized or self._fullscreen_lock):
            return
        self._purge_stale_subwindow_refs()
        active = self._mdi.activeSubWindow()
        target = (
            active
            if isinstance(active, _ManagedSubWindow)
            and self._is_live_subwindow(active)
            and active not in self._hidden_subwindows
            else None
        )
        if target is None:
            for widget in self._widgets:
                sub = self._subwindow_for(widget)
                if not self._is_live_subwindow(sub):
                    continue
                if sub in self._hidden_subwindows:
                    continue
                try:
                    if sub.isHidden():
                        continue
                except Exception:
                    continue
                target = sub
                break
        if target is None:
            self._fullscreen_lock = False
            self._global_maximized = False
            return
        self._mdi.setActiveSubWindow(target)
        self._maximize_single(target)

    def _register_visible(self, sub: _ManagedSubWindow) -> None:
        self._purge_stale_subwindow_refs()
        if not self._is_live_subwindow(sub):
            return
        if sub in self._hidden_subwindows:
            return
        try:
            self._visibility_queue.remove(sub)
        except ValueError:
            pass
        self._visibility_queue.append(sub)
        self._enforce_max_visible(active=sub)

    def set_max_visible_windows(self, limit: int) -> None:
        try:
            limit = max(1, int(limit))
        except Exception:
            return
        self._max_visible_windows = limit
        self._enforce_max_visible()

    def _enforce_max_visible(self, *, active: _ManagedSubWindow | None = None) -> None:
        self._purge_stale_subwindow_refs()
        visible: list[_ManagedSubWindow] = []
        for sub in self._visibility_queue:
            if sub in self._hidden_subwindows:
                continue
            if not self._is_live_subwindow(sub):
                continue
            try:
                if sub.isHidden():
                    continue
            except RuntimeError:
                continue
            visible.append(sub)
        while len(visible) > max(1, self._max_visible_windows):
            candidate = visible.pop(0)
            if active is not None and candidate is active:
                visible.append(candidate)
                continue
            if not self._is_live_subwindow(candidate):
                continue
            self._remember_subwindow_geometry(candidate)
            try:
                candidate.hide()
            except RuntimeError:
                continue
            self._hidden_subwindows.add(candidate)
            try:
                self._visibility_queue.remove(candidate)
            except ValueError:
                pass
        self._visibility_queue = [
            sub
            for sub in self._visibility_queue
            if sub not in self._hidden_subwindows and self._is_live_subwindow(sub)
        ]

    def _maximize_single(self, target: _ManagedSubWindow) -> None:
        self._purge_stale_subwindow_refs()
        if not self._is_live_subwindow(target):
            return
        self._fullscreen_lock = True
        self._global_maximized = True
        for sub in self._subwindows.values():
            if not self._is_live_subwindow(sub):
                continue
            if sub in self._hidden_subwindows:
                continue
            if sub is target:
                sub.show()
                applied = self._apply_fullscreen_geometry(sub)
                if not (sub.isMaximized() or sub.isFullScreen()):
                    self._maybe_apply_maximize(sub)
                sub.raise_()
                self._maximized_hidden.discard(sub)
                if not applied:
                    QtCore.QTimer.singleShot(
                        0,
                        lambda s=sub: self._apply_fullscreen_geometry(s),
                    )
            else:
                self._maximized_hidden.discard(sub)

    def _fit_subwindow(
        self,
        sub: _ManagedSubWindow,
        *,
        use_half_width: bool = True,
        preferred_width: int | None = None,
        remember_manual: bool = True,
    ) -> None:
        self._arrange_generation += 1
        try:
            setattr(sub, "_mw_user_sized", bool(remember_manual))
        except Exception:
            pass
        if self._global_maximized and preferred_width is None:
            # Respect maximize state instead of resizing back to windowed dimensions.
            return
        area_size = self._mdi.viewport().size()
        if not area_size.isValid():
            return
        widget = sub.widget()
        hint = widget.sizeHint() if widget is not None else QtCore.QSize(900, 560)
        min_hint = widget.minimumSizeHint() if widget is not None else hint
        min_size = widget.minimumSize() if widget is not None else QtCore.QSize(0, 0)
        base_w = max(hint.width(), min_hint.width(), min_size.width(), 400)
        base_h = max(hint.height(), min_hint.height(), min_size.height(), 300)
        aspect = self._subwindow_aspect_ratio(sub)
        if aspect <= 0:
            aspect = base_w / base_h if base_h else 1.6
        sub.set_aspect_ratio(aspect)
        available_w = area_size.width()
        available_h = area_size.height()
        if available_w <= 0 or available_h <= 0:
            return

        slot_width = available_w if not use_half_width else max(1, available_w // 2)
        target_w = slot_width if preferred_width is None else min(preferred_width, slot_width)
        target_w = max(300, min(target_w, slot_width))
        target_h = int(target_w / aspect) if aspect else available_h
        # Large legends can inflate Qt size hints and force near-square windows.
        # Keep a conservative minimum so restored windows stay landscape.
        min_height_hint = max(min_hint.height(), min_size.height(), 220)
        min_height = min(available_h, max(220, min(min_height_hint, int(available_h * 0.5))))
        if available_h >= min_height:
            target_h = max(target_h, min_height)
        if target_h > available_h and target_h > 0:
            scale = available_h / float(target_h)
            target_w = max(240, int(target_w * scale))
            target_h = max(200, int(target_h * scale))
        sub.resize(target_w, target_h)
        self._sync_subwindow_child_widget(sub)
        self._refresh_subwindow_figure_layout(sub, deferred=True)

    def _queue_arrange_subwindows(self) -> None:
        generation = self._arrange_generation

        def _apply_if_current() -> None:
            if generation != self._arrange_generation:
                return
            self._arrange_subwindows()

        try:
            QtCore.QTimer.singleShot(0, _apply_if_current)
        except Exception:
            _apply_if_current()

    def arrangement_mode(self) -> str:
        return self._arrangement_mode

    def set_arrangement_mode(self, mode: str) -> None:
        token = str(mode or "").strip().lower()
        if token not in {"cascade", "tile_vertical", "tile_horizontal"}:
            token = "cascade"
        if token == self._arrangement_mode:
            self._arrange_subwindows()
            return
        self._arrangement_mode = cast(
            Literal["cascade", "tile_vertical", "tile_horizontal"],
            token,
        )
        self._arrange_subwindows()

    def _ordered_visible_subwindows(self) -> list[_ManagedSubWindow]:
        self._purge_stale_subwindow_refs()
        visible_subwindows = [
            self._subwindow_for(widget)
            for widget in self._widgets
            if self._visible.get(widget, True)
        ]
        return [
            sub
            for sub in visible_subwindows
            if self._is_live_subwindow(sub) and sub not in self._hidden_subwindows
        ]

    def _arrange_cascade_visible(self, subwindows: Sequence[_ManagedSubWindow]) -> None:
        if not subwindows:
            return
        viewport = self._mdi.viewport().rect()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        margin = self._layout_margin
        max_w = max(320, viewport.width() - margin * 2)
        max_h = max(220, viewport.height() - margin * 2)
        count = max(1, len(subwindows))
        # Keep cascaded windows consistently sized instead of inheriting stale
        # per-window widths from earlier manual resizes.
        cascade_fraction = 0.62 if count <= 2 else 0.56
        shared_preferred_width = int(round(viewport.width() * cascade_fraction))
        shared_preferred_width = max(320, min(max_w, shared_preferred_width))
        self._arranging_subwindows = True
        try:
            for sub in subwindows:
                sub.show()
                sub.showNormal()
            self._mdi.cascadeSubWindows()
            for sub in subwindows:
                self._fit_subwindow(
                    sub,
                    use_half_width=False,
                    preferred_width=shared_preferred_width,
                    remember_manual=False,
                )
                geometry = sub.geometry()
                clamped_w = min(max_w, geometry.width())
                clamped_h = min(max_h, geometry.height())
                x = max(
                    viewport.left() + margin,
                    min(
                        geometry.x(),
                        viewport.right() - clamped_w - margin + 1,
                    ),
                )
                y = max(
                    viewport.top() + margin,
                    min(
                        geometry.y(),
                        viewport.bottom() - clamped_h - margin + 1,
                    ),
                )
                sub.setGeometry(x, y, clamped_w, clamped_h)
            active = self._mdi.activeSubWindow()
            if isinstance(active, QtWidgets.QMdiSubWindow):
                active.raise_()
        finally:
            self._arranging_subwindows = False

    def _arrange_tile_visible(
        self,
        subwindows: Sequence[_ManagedSubWindow],
        *,
        orientation: Literal["vertical", "horizontal"],
    ) -> None:
        if not subwindows:
            return
        viewport = self._mdi.viewport().rect()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        margin = self._layout_margin
        inner = viewport.adjusted(margin, margin, -margin, -margin)
        if inner.width() <= 0 or inner.height() <= 0:
            return
        count = len(subwindows)
        self._arranging_subwindows = True
        try:
            for sub in subwindows:
                sub.show()
                sub.showNormal()
            if orientation == "vertical":
                width_span = max(1, inner.width())
                for idx, sub in enumerate(subwindows):
                    left = inner.left() + int(round(idx * width_span / count))
                    right = inner.left() + int(round((idx + 1) * width_span / count)) - 1
                    cell = QtCore.QRect(
                        left,
                        inner.top(),
                        max(1, right - left + 1),
                        inner.height(),
                    )
                    target = self._fit_rect_to_aspect(cell, self._subwindow_aspect_ratio(sub))
                    sub.setGeometry(target)
                    self._refresh_subwindow_figure_layout(sub, deferred=True)
            else:
                height_span = max(1, inner.height())
                for idx, sub in enumerate(subwindows):
                    top = inner.top() + int(round(idx * height_span / count))
                    bottom = inner.top() + int(round((idx + 1) * height_span / count)) - 1
                    cell = QtCore.QRect(
                        inner.left(),
                        top,
                        inner.width(),
                        max(1, bottom - top + 1),
                    )
                    target = self._fit_rect_to_aspect(cell, self._subwindow_aspect_ratio(sub))
                    sub.setGeometry(target)
                    self._refresh_subwindow_figure_layout(sub, deferred=True)
            active = self._mdi.activeSubWindow()
            if isinstance(active, QtWidgets.QMdiSubWindow):
                active.raise_()
        finally:
            self._arranging_subwindows = False

    def _arrange_subwindows(self) -> None:
        """Lay out visible subwindows using the selected arrangement mode."""

        if self._global_maximized:
            return
        viewport = self._mdi.viewport().rect()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        visible_subwindows = self._ordered_visible_subwindows()
        if not visible_subwindows:
            return
        margin = self._layout_margin
        count = len(visible_subwindows)
        if count == 1 and self._arrangement_mode == "cascade":
            self._arrange_cascade_visible(visible_subwindows)
            return
        if count == 1:
            sub = visible_subwindows[0]
            cell = QtCore.QRect(
                viewport.left() + margin,
                viewport.top() + margin,
                max(1, viewport.width() - margin * 2),
                max(1, viewport.height() - margin * 2),
            )
            aspect = self._subwindow_aspect_ratio(sub)
            target = self._fit_rect_to_aspect(cell, aspect)
            self._arranging_subwindows = True
            try:
                sub.showNormal()
                sub.setGeometry(target)
                self._refresh_subwindow_figure_layout(sub, deferred=True)
            finally:
                self._arranging_subwindows = False
            return
        if self._arrangement_mode == "tile_vertical":
            self._arrange_tile_visible(visible_subwindows, orientation="vertical")
            return
        if self._arrangement_mode == "tile_horizontal":
            self._arrange_tile_visible(visible_subwindows, orientation="horizontal")
            return
        self._arrange_cascade_visible(visible_subwindows)

    def eventFilter(self, source: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if isinstance(source, _ManagedSubWindow):
            if event.type() in {
                QtCore.QEvent.Type.Resize,
                QtCore.QEvent.Type.Show,
                QtCore.QEvent.Type.LayoutRequest,
            }:
                self._sync_subwindow_child_widget(source)
                try:
                    QtCore.QTimer.singleShot(
                        0,
                        lambda s=source: self._sync_subwindow_child_widget(s),
                    )
                except Exception:
                    pass
        if source is self._mdi.viewport() and event.type() == QtCore.QEvent.Type.Resize:
            if self._global_maximized or self._fullscreen_lock:
                active = self._mdi.activeSubWindow()
                if active is not None:
                    self._apply_fullscreen_geometry(active)
            else:
                visible_count = len(self._ordered_visible_subwindows())
                if self._arrangement_mode in {"tile_vertical", "tile_horizontal"}:
                    self._arrange_subwindows()
                elif visible_count == 1:
                    visible = self._ordered_visible_subwindows()
                    sub = visible[0] if visible else None
                    if isinstance(sub, _ManagedSubWindow):
                        user_sized = bool(getattr(sub, "_mw_user_sized", False))
                        needs_fit = False
                        try:
                            geom = sub.geometry()
                            needs_fit = (
                                (not geom.isValid())
                                or geom.width() < 220
                                or geom.height() < 160
                            )
                        except Exception:
                            needs_fit = True
                        if needs_fit:
                            self._fit_subwindow(
                                sub,
                                use_half_width=False,
                                remember_manual=False,
                            )
                        elif not user_sized:
                            self._arrange_subwindows()
                        else:
                            self._sync_subwindow_child_widget(sub)
                            self._refresh_subwindow_figure_layout(sub, deferred=True)
        return super().eventFilter(source, event)

    def _remove_widget(self, widget: QtWidgets.QWidget | None) -> None:
        if widget is None:
            return
        try:
            index = self._widgets.index(widget)
        except ValueError:
            return
        self._widgets.pop(index)
        self._titles.pop(widget, None)
        self._icons.pop(widget, None)
        self._visible.pop(widget, None)
        sub = self._subwindows.pop(widget, None)
        if sub is not None:
            self._hidden_subwindows.discard(sub)
            self._maximized_hidden.discard(sub)
            try:
                self._visibility_queue.remove(sub)
            except ValueError:
                pass
            try:
                self._mdi.removeSubWindow(sub)
            except Exception:
                pass
            try:
                sub.setWidget(None)
            except Exception:
                pass
            try:
                sub.deleteLater()
            except Exception:
                pass
            self._purge_stale_subwindow_refs()
        widget.setParent(None)
        if self._widgets:
            new_index = min(index, len(self._widgets) - 1)
            self._activate_index(new_index)
        else:
            self._mdi.setActiveSubWindow(None)
            self.currentChanged.emit(-1)

    # ------------------------------------------------------------------ tab-like API
    def addTab(self, widget: QtWidgets.QWidget, title: str) -> int:
        return self.insertTab(self.count(), widget, title)

    def insertTab(self, index: int, widget: QtWidgets.QWidget, title: str) -> int:
        index = max(0, min(index, len(self._widgets)))
        widget.setParent(None)
        sub = _ManagedSubWindow(self._mdi)
        sub.set_owner(self)
        sub.installEventFilter(self)
        sub.setWidget(widget)
        sub.setWindowTitle(title)
        icon = self._icons.get(widget, QtGui.QIcon())
        if not icon.isNull():
            sub.setWindowIcon(icon)
        self._mdi.addSubWindow(sub)
        self._fit_subwindow(sub, remember_manual=False)
        if self._global_maximized:
            self._maximize_single(sub)
        else:
            sub.show()

        if widget in self._widgets:
            self._remove_widget(widget)
        self._widgets.insert(index, widget)
        self._subwindows[widget] = sub
        self._titles[widget] = title
        self._icons[widget] = icon
        self._visible[widget] = True

        self._register_visible(sub)

        self._activate_index(index)
        if self._global_maximized or self._fullscreen_lock:
            self._maximize_single(sub)
        else:
            self._arrange_subwindows()
            self._queue_arrange_subwindows()
        return index

    def removeTab(self, index: int) -> None:
        widget = self.widget(index)
        self._remove_widget(widget)

    def indexOf(self, widget: QtWidgets.QWidget | None) -> int:
        if widget is None:
            return -1
        try:
            return self._widgets.index(widget)
        except ValueError:
            return -1

    def widget(self, index: int) -> QtWidgets.QWidget | None:
        if 0 <= index < len(self._widgets):
            return self._widgets[index]
        return None

    def setCurrentIndex(self, index: int) -> None:
        self._activate_index(index)

    def setCurrentWidget(self, widget: QtWidgets.QWidget | None) -> None:
        self._activate_index(self.indexOf(widget))

    def currentIndex(self) -> int:
        active = self._mdi.activeSubWindow()
        if active is None:
            return -1
        return self.indexOf(active.widget())

    def currentWidget(self) -> QtWidgets.QWidget | None:
        index = self.currentIndex()
        return self.widget(index) if index >= 0 else None

    def count(self) -> int:
        return len(self._widgets)

    def setTabVisible(self, index: int, visible: bool) -> None:
        self._purge_stale_subwindow_refs()
        widget = self.widget(index)
        if widget is None:
            return
        sub = self._subwindow_for(widget)
        if not self._is_live_subwindow(sub):
            return
        self._visible[widget] = bool(visible)
        if visible:
            if sub not in self._mdi.subWindowList():
                self._mdi.addSubWindow(sub)
            sub.show()
            self._hidden_subwindows.discard(sub)
            self._activate_index(index)
            self._register_visible(sub)
            if self._global_maximized:
                self._maximize_single(sub)
        else:
            was_active = self._mdi.activeSubWindow() is sub
            self._remember_subwindow_geometry(sub)
            sub.hide()
            self._hidden_subwindows.add(sub)
            try:
                self._visibility_queue.remove(sub)
            except ValueError:
                pass
            if was_active:
                for idx, candidate in enumerate(self._widgets):
                    if self._visible.get(candidate, False):
                        self._activate_index(idx)
                        break

    def isTabVisible(self, index: int) -> bool:
        widget = self.widget(index)
        if widget is None:
            return False
        return bool(self._visible.get(widget, False))

    def setTabIcon(self, index: int, icon: QtGui.QIcon) -> None:
        widget = self.widget(index)
        if widget is None:
            return
        if not isinstance(icon, QtGui.QIcon):
            icon = QtGui.QIcon()
        self._icons[widget] = icon
        sub = self._subwindow_for(widget)
        if sub is not None:
            sub.setWindowIcon(icon)

    def tabIcon(self, index: int) -> QtGui.QIcon:
        widget = self.widget(index)
        if widget is None:
            return QtGui.QIcon()
        return self._icons.get(widget, QtGui.QIcon())

    def setTabText(self, index: int, title: str) -> None:
        widget = self.widget(index)
        if widget is None:
            return
        self._titles[widget] = title
        sub = self._subwindow_for(widget)
        if sub is not None:
            sub.setWindowTitle(title)

    def tabText(self, index: int) -> str:
        widget = self.widget(index)
        if widget is None:
            return ""
        return self._titles.get(widget, "")

    def tabBar(self) -> None:
        return None

    def _set_tab_visibility(self, widget: QtWidgets.QWidget, visible: bool) -> None:
        self._purge_stale_subwindow_refs()
        if widget not in self._widgets:
            return
        sub = self._subwindow_for(widget)
        if not self._is_live_subwindow(sub):
            return
        was_hidden = sub in self._hidden_subwindows
        if visible:
            self._hidden_tabs.discard(widget)
            self._hidden_subwindows.discard(sub)
            self._maximized_hidden.discard(sub)
            sub.show()
            self._register_visible(sub)
            if self._global_maximized:
                self._maybe_apply_maximize(sub)
            else:
                sub.showNormal()
                if self._arrangement_mode in {"tile_vertical", "tile_horizontal"}:
                    self._arrange_subwindows()
                else:
                    restored_geom = self._restore_saved_subwindow_geometry(sub) if was_hidden else False
                    if not restored_geom:
                        needs_fit = was_hidden
                        try:
                            geom = sub.geometry()
                            if geom.isValid() and geom.width() >= 220 and geom.height() >= 160:
                                needs_fit = False
                        except Exception:
                            needs_fit = True
                        if needs_fit:
                            self._fit_subwindow(
                                sub,
                                use_half_width=False,
                                remember_manual=False,
                            )
                    self._refresh_subwindow_figure_layout(sub, deferred=True)
        else:
            self._hidden_tabs.add(widget)
            self._hidden_subwindows.add(sub)
            sub.hide()
            try:
                self._visibility_queue.remove(sub)
            except ValueError:
                pass

    def setTabToolTip(self, index: int, tooltip: str) -> None:
        _ = index, tooltip

    def tabToolTip(self, index: int) -> str:
        _ = index
        return ""

    def clear(self) -> None:
        for widget in list(self._widgets):
            self._remove_widget(widget)


__all__ = [
    "_DockSwitcherWidget",
    "PyPlotWindow",
    "GraphLineState",
    "GraphSelectionDialog",
    "PlotTabState",
    "TabDescriptor",
]
