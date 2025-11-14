from __future__ import annotations

import datetime
import logging
import os
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
    List,
    Optional,
    Literal,
    Sequence,
    Tuple,
    cast,
)

import json
import uuid
from functools import partial

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt import NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib import colors as mcolors
import pandas as pd
from pandas.api.types import is_numeric_dtype

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
)
from plotting.shared.readability import (
    apply_readability,
    apply_readability_fonts,
    create_readability_group,
    sync_readability,
)


OBJECT_TREE_STATE_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1
PRIMARY_DOCK_DEFAULT_WIDTH = 320
PRIMARY_DOCK_MIN_WIDTH = 200

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
    size_spin: QtWidgets.QSpinBox | None = None
    bold_action: QtGui.QAction | None = None
    italic_action: QtGui.QAction | None = None
    underline_action: QtGui.QAction | None = None
    color_button: QtWidgets.QToolButton | None = None
    line_group: QtGui.QActionGroup | None = None
    line_action: QtGui.QAction | None = None
    scatter_action: QtGui.QAction | None = None
    line_symbol_action: QtGui.QAction | None = None


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
            self._dock_widths[dock] = max(base_width, stored_width, 220)
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
        prefer_overlay = bool(dock.property("mwOverlayPreferred"))
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
            width = self._dock_widths.get(dock, 0)
            if width > 0:
                self._apply_dock_width(dock, width)
        self._expanded_index = index
        self._collapse_timer.stop()
        self._syncing = False

    def _handle_visibility_change(self, index: int, visible: bool) -> None:
        if self._syncing or not self._docks:
            return
        if visible:
            if not self._docks[index].isFloating():
                current = self._docks[index]
                self._dock_widths[current] = max(current.width(), self._dock_widths.get(current, 220))
                main = self._main_window()
                if hasattr(main, "_primary_dock_widths"):
                    try:
                        main._primary_dock_widths[current] = max(  # type: ignore[attr-defined]
                            current.width(),
                            main._primary_dock_widths.get(current, 0),  # type: ignore[attr-defined]
                        )
                    except Exception:
                        pass
            self._expanded_index = index
            self._collapse_timer.stop()
            if self._tab_bar.currentIndex() != index:
                self._syncing = True
                self._tab_bar.setCurrentIndex(index)
                self._syncing = False
        else:
            if self._pinned_index == index and not self._docks[index].isFloating():
                dock = self._docks[index]
                self._syncing = True
                dock.show()
                try:
                    dock.raise_()
                except Exception:
                    pass
                self._syncing = False
                self._collapse_timer.stop()
                return
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
                width = self._dock_widths.get(dock, 0)
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
        if self._collapse_timer.isActive() or self._floating_indices:
            return
        self._collapse_timer.start(self._HOVER_CLOSE_DELAY_MS)

    def _collapse_if_outside(self) -> None:
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
            width = self._dock_widths.get(dock, 0)
            if width > 0:
                self._apply_dock_width(dock, width)

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


@dataclass
class WorkbookData:
    """Group one or more worksheets originating from the same file."""

    key: Hashable
    name: str
    worksheets: List[Hashable] = field(default_factory=list)
    source: Path | None = None
    folder: Path | None = None


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


class LegendSettingsDialog(QtWidgets.QDialog):
    """Configure appearance and layout options for a Matplotlib legend."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        legend: Legend,
    ) -> None:
        super().__init__(parent)
        self._legend = legend
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
        self.symbol_checkbox = QtWidgets.QCheckBox("Show legend symbols", self)
        self.symbol_checkbox.setChecked(bool(stored_symbol_setting))
        form.addRow("", self.symbol_checkbox)

        stored_follow_setting = getattr(legend, "_mw_text_follows_handles", False)
        self.text_color_follow_checkbox = QtWidgets.QCheckBox("Text colour follows plots", self)
        self.text_color_follow_checkbox.setChecked(bool(stored_follow_setting))
        form.addRow("", self.text_color_follow_checkbox)

        self.orientation_combo = QtWidgets.QComboBox(self)
        self.orientation_combo.addItem("Auto", "auto")
        self.orientation_combo.addItem("Horizontal", "horizontal")
        self.orientation_combo.addItem("Vertical", "vertical")
        stored_orientation = getattr(legend, "_mw_orientation", "auto")
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
            stored_placement = "outside" if getattr(legend, "_bbox_to_anchor", None) else "inside"
        placement_index = self.placement_combo.findData(stored_placement)
        if placement_index < 0:
            placement_index = 0
        self.placement_combo.setCurrentIndex(placement_index)
        form.addRow("Placement", self.placement_combo)

        self.draggable_checkbox = QtWidgets.QCheckBox("Allow drag", self)
        stored_draggable = getattr(legend, "_mw_draggable", None)
        if stored_draggable is None:
            stored_draggable = True
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

        handles: list[Any] = []
        for attr in ("legendHandles", "legend_handles"):
            value = getattr(legend, attr, None)
            if value:
                try:
                    handles = list(value)
                except Exception:
                    handles = []
                if handles:
                    break
        if not handles:
            try:
                handles = list(legend.legendHandles)
            except Exception:
                handles = []
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

        show_symbols = self.symbol_checkbox.isChecked()
        legend._mw_show_symbols = show_symbols
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

        follow_colors = self.text_color_follow_checkbox.isChecked()
        legend._mw_text_follows_handles = follow_colors
        originals = getattr(legend, "_mw_text_original_colors", [])
        if follow_colors and handles and texts:
            for text, handle in zip(texts, handles):
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
        else:
            for text, color in zip(texts, originals):
                try:
                    text.set_color(color)
                except Exception:
                    pass

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
                canvas.draw_idle()
            except Exception:
                try:
                    canvas.draw()
                except Exception:
                    pass

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
        ".xlsx",
        ".xls",
        ".xlsm",
        ".json",
        ".vsm-hys-data",
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
        self._data_folder_items: Dict[Path, QtWidgets.QTreeWidgetItem] = {}
        self._data_workbook_items: Dict[Hashable, QtWidgets.QTreeWidgetItem] = {}
        self._workbooks: Dict[Hashable, WorkbookData] = {}
        self._worksheets: Dict[Hashable, WorksheetData] = {}
        self._worksheet_models: Dict[Hashable, WorksheetTableModel] = {}
        self._primary_dock_widths: Dict[QtWidgets.QDockWidget, int] = {}
        self._log_alert_enabled: bool = False
        self._left_dock_switcher_widget: _DockSwitcherWidget | None = None
        self._log_view_watcher: _LogViewWatcher | None = None
        self._last_import_sources: List[str] = []
        self._restoring_imports = False
        self._data_menu: QtWidgets.QMenu | None = None
        self._import_files_action: QtGui.QAction | None = None
        self._import_folder_action: QtGui.QAction | None = None
        self._refresh_import_action: QtGui.QAction | None = None
        self._new_workbook_action: QtGui.QAction | None = None
        self._add_column_before_action: QtGui.QAction | None = None
        self._add_column_after_action: QtGui.QAction | None = None
        self._delete_column_action: QtGui.QAction | None = None
        self._reorder_columns_action: QtGui.QAction | None = None

        self._log_has_unread_errors = False
        self._history = _HistoryManager()
        self._retabify_pending = False
        self._import_storage_key = self._project_settings_key("import_sources")
        self._format_controls = FormatToolbarControls()
        self._format_selection: tuple[str, Any] | None = None
        self._format_updating = False
        self._object_tree_updating = False

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

        self._build_base_ui()
        self._update_project_title()
        self._update_project_actions()
        developer_options().keep_files_changed.connect(self._handle_keep_files_changed)
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
        tab_widget = getattr(self, "tab_widget", None)
        if not isinstance(tab_widget, QtWidgets.QTabWidget):
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

    def _save_current_graph(self) -> None:
        self._save_graph_for_current_tab()

    def _save_graph_for_current_tab(
        self,
        *,
        parent: QtWidgets.QWidget | None = None,
    ) -> bool:
        """Persist the currently visible graph to an image file."""

        target = parent or self
        tab_widget = getattr(self, "tab_widget", None)
        if not isinstance(tab_widget, QtWidgets.QTabWidget):
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
        suggested_filename = _clean_stem(default_name) + ".png"

        start_dir = getattr(self, "_last_graph_dir", None)
        if not isinstance(start_dir, Path) or not start_dir.exists():
            project_path = getattr(self, "_project_path", None)
            if isinstance(project_path, Path) and project_path.exists():
                start_dir = project_path.parent
        if not isinstance(start_dir, Path) or not start_dir.exists():
            start_dir = Path.home()
        suggested_path = start_dir / suggested_filename

        filters = "PNG Image (*.png);;PDF Document (*.pdf);;SVG Image (*.svg);;All files (*)"
        path_str, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            target,
            "Save Graph",
            str(suggested_path),
            filters,
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
        if isinstance(settings, QtCore.QSettings):
            if getattr(self, "_last_graph_dir", None):
                settings.setValue("last_graph_dir", str(self._last_graph_dir))
            else:
                settings.remove("last_graph_dir")
            settings.sync()
        return True

    def _normalize_current_graph(self) -> None:
        raise NotImplementedError

    def _export_txt(self) -> None:
        tab_widget = getattr(self, "tab_widget", None)
        if not isinstance(tab_widget, QtWidgets.QTabWidget):
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
        if descriptor is None or not descriptor.lines:
            QtWidgets.QMessageBox.information(
                self,
                "Export TXT",
                "The selected tab does not expose exportable data.",
            )
            return

        series: list[tuple[str, Any, Any]] = []
        for index, state in enumerate(descriptor.lines.values(), start=1):
            label = state.label or f"Series {index}"
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
            series.append((label, x_data, y_data))
        if not series:
            for index, state in enumerate(descriptor.lines.values(), start=1):
                x_data = state.x_data()
                y_data = state.y_data()
                if x_data is None or y_data is None:
                    continue
                label = state.label or f"Series {index}"
                series.append((label, x_data, y_data))
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
        suggested = _clean_stem(default_name) + ".txt"

        start_dir = getattr(self, "_last_export_dir", None)
        if not isinstance(start_dir, Path) or not start_dir.exists():
            start_dir = getattr(self, "_last_graph_dir", None)
        if not isinstance(start_dir, Path) or not start_dir.exists():
            project_path = getattr(self, "_project_path", None)
            if isinstance(project_path, Path) and project_path.exists():
                start_dir = project_path.parent
        if not isinstance(start_dir, Path) or not start_dir.exists():
            start_dir = Path.home()

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
            exported, errors = self._push_workbooks_to_origin(workbooks)
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
        self, workbooks: Iterable[WorkbookData]
    ) -> tuple[int, list[str]]:
        errors: list[str] = []
        exported = 0

        with origin_session() as origin_any:
            schedule_origin_release()
            workbook_names: set[str] = set()
            for workbook in workbooks:
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
                    book_obj = origin_any.new_book('w', lname=book_name)
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
                                    sheet = add_sheet('w', lname=sheet_name)
                                except TypeError:
                                    sheet = add_sheet()
                            if sheet is None:
                                sheet = origin_any.new_sheet('w', lname=sheet_name)
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

                    roles = self._origin_axis_roles(frame)
                    if roles:
                        try:
                            sheet.cols_axis(roles)
                        except Exception:
                            pass

                    self._apply_origin_metadata(origin_any, sheet, worksheet)
                    exported += 1

        return exported, errors

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

        for index, column in enumerate(columns):
            meta = worksheet.columns.get(
                column, WorksheetColumnMeta(long_name=str(column))
            )
            label = meta.long_name or str(column)
            if label:
                try:
                    sheet.set_label(index, label)
                except Exception:
                    pass
            if meta.comments:
                try:
                    sheet.set_comment(index, meta.comments)
                except Exception:
                    pass

            for value, field in ((meta.units, "unit"), (meta.formula, "formula")):
                if not value:
                    continue
                safe_value = self._escape_origin_text(str(value))
                command = f"wks.col{index + 1}.{field}$=\"{safe_value}\";"
                try:
                    origin_any.lt_exec(command)
                except Exception:
                    continue

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
            if not x_assigned and numeric:
                roles.append("X")
                x_assigned = True
            else:
                roles.append("Y")
        if not roles:
            return ""
        if not x_assigned:
            roles[0] = "X"
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
        return str(text).replace("\"", "''")

    def _sync_shared_action_states(self) -> None:
        has_worksheets = any(
            self._worksheets.get(key) is not None
            for workbook in self._workbooks.values()
            for key in workbook.worksheets
        )
        export_button = getattr(self, "export_origin_button", None)
        if isinstance(export_button, QtGui.QAction):
            export_button.setEnabled(has_worksheets)
        outlier_button = getattr(self, "check_outliers_button", None)
        if isinstance(outlier_button, QtGui.QAction):
            outlier_button.setEnabled(has_worksheets)

    def _show_check_outliers_placeholder(self) -> None:
        QtWidgets.QMessageBox.information(
            self,
            "Check outliers",
            "Outlier detection will be available in a future update.",
        )

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

    # ------------------------------------------------------------------ base UI
    def _build_base_ui(self) -> None:
        central = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central)
        central_layout.setContentsMargins(6, 6, 6, 4)
        central_layout.setSpacing(6)

        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("Select files or folders…")
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

        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setHeaderLabels(["Project Explorer", "Details"])
        self.project_tree.header().setStretchLastSection(True)
        self.project_tree.itemDoubleClicked.connect(self._handle_project_item_double_click)
        self.project_tree.itemActivated.connect(self._handle_project_item_double_click)
        self._ensure_data_root()
        project_dock = self._create_dock_widget("Project Explorer", "projectExplorerDock")
        project_dock.setWidget(self.project_tree)
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
        self.object_tree.itemChanged.connect(self._dispatch_object_item_changed)
        selection_model = self.object_tree.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._handle_object_selection_changed)
        self.object_tree.itemDoubleClicked.connect(self._handle_object_item_double_click)
        self.object_tree.itemActivated.connect(self._handle_object_item_double_click)
        object_dock = self._create_dock_widget("Object Manager", "objectManagerDock")
        object_dock.setWidget(self.object_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, object_dock)
        object_dock.setMinimumWidth(PRIMARY_DOCK_MIN_WIDTH)
        self.object_dock = object_dock

        graph_dock: QtWidgets.QDockWidget | None = None
        graph_panel: QtWidgets.QWidget | None = None
        self.graph_dock = None
        self.graph_panel = None

        self._setup_script_toolbar()
        self._setup_action_toolbar()
        self._setup_navigation_toolbar()
        self._setup_format_toolbar()

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
        graph_action = new_menu.addAction("Graph")
        create_graph = getattr(self, "_create_blank_graph", None)
        if callable(create_graph):
            graph_action.triggered.connect(create_graph)  # type: ignore[arg-type]
        else:
            graph_action.setEnabled(False)
        if insert_before is not None:
            file_menu.insertMenu(insert_before, new_menu)
        else:
            file_menu.addMenu(new_menu)

        open_project_action = QtGui.QAction("Open…", self)
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

        save_as_action = QtGui.QAction("Save Project As…", self)
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

        import_files_action = data_menu.addAction("Import Files…")
        import_files_action.triggered.connect(self._import_data_from_files)
        self._import_files_action = import_files_action

        import_folder_action = data_menu.addAction("Import Folder…")
        import_folder_action.triggered.connect(self._import_data_from_folder)
        self._import_folder_action = import_folder_action

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

        reorder_action = data_menu.addAction("Reorder Columns…")
        reorder_action.triggered.connect(self._reorder_columns)
        self._reorder_columns_action = reorder_action

        self._update_worksheet_actions()

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
            if anchor is None:
                section_menu = menu
            else:
                section_menu = self._build_graph_section_menu(title, anchor)
            button = self._create_graph_section_button(title, anchor, section_menu)
            layout.addWidget(button)
            buttons.append(button)
        layout.addStretch(1)
        self._graph_section_buttons = buttons

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

        return sections

    def _create_graph_section_button(
        self,
        title: str,
        anchor: QtWidgets.QWidget | None,
        menu: QtWidgets.QMenu,
    ) -> QtWidgets.QToolButton:
        button = _GraphSectionButton(anchor, self._set_graph_settings_anchor, self)
        button.setObjectName(f"mw_graph_section_{title.lower().replace(' ', '_')}")
        button.setText(title)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        if isinstance(menu, QtWidgets.QMenu):
            button.setMenu(menu)
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
            self._refresh_graph_section_buttons(None)
            return

        if widget.parent() is not container:
            widget.setParent(container)
        layout.insertWidget(0, widget)
        widget.show()
        self._active_settings_widget = widget
        if isinstance(placeholder, QtWidgets.QWidget):
            placeholder.setVisible(False)
        self._refresh_graph_section_buttons(widget)

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

        import_action = toolbar.addAction("Import data…")
        import_action.triggered.connect(self._prompt_import_data)
        self.import_data_button = import_action
        self._style_toolbar_button(toolbar, import_action)

        save_action = toolbar.addAction("Save graph…")
        save_action.setEnabled(False)
        save_action.triggered.connect(self._save_current_graph)
        self.save_graph_button = save_action
        self._style_toolbar_button(toolbar, save_action)

        normalize_action = toolbar.addAction("Normalize Y")
        normalize_action.setEnabled(False)
        normalize_action.triggered.connect(self._normalize_current_graph)
        self.normalize_button = normalize_action
        self._style_toolbar_button(toolbar, normalize_action)

        export_action = toolbar.addAction("Export TXT…")
        export_action.setEnabled(False)
        export_action.triggered.connect(self._export_txt)
        self.export_button = export_action
        self._style_toolbar_button(toolbar, export_action)

        origin_action = toolbar.addAction("Open in Origin…")
        origin_action.setEnabled(False)
        origin_action.triggered.connect(self._open_origin_prompt)
        self.open_origin_button = origin_action
        self._style_toolbar_button(toolbar, origin_action)

        export_workbooks_action = toolbar.addAction("Export workbooks to Origin…")
        export_workbooks_action.setEnabled(False)
        export_workbooks_action.triggered.connect(self._export_workbooks_to_origin)
        self.export_origin_button = export_workbooks_action
        self._style_toolbar_button(toolbar, export_workbooks_action)

        check_outliers_action = toolbar.addAction("Check outliers…")
        check_outliers_action.setEnabled(False)
        check_outliers_action.triggered.connect(self._show_check_outliers_placeholder)
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

        rescale_all_action = toolbar.addAction("Rescale all…")
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

    def _current_canvas(self) -> FigureCanvas | None:
        tab_widget = getattr(self, "tab_widget", None)
        if not isinstance(tab_widget, QtWidgets.QTabWidget):
            return None
        tab = tab_widget.currentWidget()
        if tab is None:
            return None
        return self._canvas_by_tab.get(tab)

    def _current_axes(self) -> Any | None:
        tab_widget = getattr(self, "tab_widget", None)
        if not isinstance(tab_widget, QtWidgets.QTabWidget):
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
            axes.relim()
            scalex = axis in {"both", "x"}
            scaley = axis in {"both", "y"}
            axes.autoscale_view(scalex=scalex, scaley=scaley)
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
        for axes in set(filter(None, self._axes_by_tab.values())):
            canvas = self._apply_dark_mode_to_axes(axes, self._dark_mode_enabled)
            if isinstance(canvas, FigureCanvas):
                canvases.add(canvas)
        for canvas in canvases:
            try:
                canvas.draw_idle()
            except Exception:
                canvas.draw()

    def _apply_dark_mode_to_axes(self, axes: Any, enabled: bool) -> FigureCanvas | None:
        if axes is None:
            return None
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
                state["spine_colors"] = {name: spine.get_edgecolor() for name, spine in axes.spines.items()}
            if "grid_color" not in state:
                try:
                    grid_lines = axes.get_xgridlines()
                    if grid_lines:
                        state["grid_color"] = grid_lines[0].get_color()
                        state["grid_alpha"] = grid_lines[0].get_alpha()
                except Exception:
                    state.setdefault("grid_color", None)
                    state.setdefault("grid_alpha", None)
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
            grid_color = "#4a4d52"

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
                axes.grid(True, color=grid_color, alpha=0.3)
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
                title_artist = legend.get_title()
                if title_artist is not None and _should_force_light_text(title_artist.get_color()):
                    title_artist.set_color(light_text)
        else:
            if figure is not None and "figure_face" in state:
                figure.patch.set_facecolor(state["figure_face"])
            if "axes_face" in state:
                axes.set_facecolor(state["axes_face"])
            for name, spine in axes.spines.items():
                colors = state.get("spine_colors", {})
                if name in colors:
                    try:
                        spine.set_color(colors[name])
                    except Exception:
                        pass
            axes.tick_params(colors=state.get("x_label_color", "#202020"))
            axes.xaxis.label.set_color(state.get("x_label_color", "#202020"))
            axes.yaxis.label.set_color(state.get("y_label_color", "#202020"))
            title = axes.title
            if title is not None:
                title.set_color(state.get("title_color", "#202020"))
            default_tick_color = state.get("x_label_color", "#202020")
            for tick in axes.get_xticklabels() + axes.get_yticklabels():
                tick.set_color(default_tick_color)
            legend = None
            try:
                legend = axes.get_legend()
            except Exception:
                legend = None
            if legend is not None and "legend" in state:
                meta = state["legend"]
                try:
                    legend.get_frame().set_facecolor(meta.get("frame_face"))
                    legend.get_frame().set_edgecolor(meta.get("frame_edge"))
                except Exception:
                    pass
                text_colors = meta.get("text_colors", [])
                for text, color in zip(legend.get_texts(), text_colors):
                    text.set_color(color)
                title_artist = legend.get_title()
                if title_artist is not None and meta.get("title_color") is not None:
                    title_artist.set_color(meta.get("title_color"))
            original_grid_color = state.get("grid_color")
            original_grid_alpha = state.get("grid_alpha")
            if original_grid_color is not None:
                try:
                    axes.grid(True, color=original_grid_color)
                    if original_grid_alpha is not None:
                        for line in axes.get_xgridlines() + axes.get_ygridlines():
                            line.set_alpha(original_grid_alpha)
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
        return canvas
    def _prompt_import_data(self) -> None:
        files_action = getattr(self, "_import_files_action", None)
        folder_action = getattr(self, "_import_folder_action", None)

        menu = QtWidgets.QMenu(self)
        actions_added = False

        if isinstance(files_action, QtGui.QAction):
            label = files_action.text() or "Import files…"
            proxy = menu.addAction(label)
            proxy.triggered.connect(files_action.trigger)
            actions_added = True

        if isinstance(folder_action, QtGui.QAction):
            label = folder_action.text() or "Import folder…"
            proxy = menu.addAction(label)
            proxy.triggered.connect(folder_action.trigger)
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

        size_spin = QtWidgets.QSpinBox(toolbar)
        size_spin.setRange(6, 96)
        size_spin.setValue(18)
        size_spin.setEnabled(False)
        size_spin.valueChanged.connect(self._apply_text_size)
        controls.size_spin = size_spin
        toolbar.addWidget(size_spin)

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

        color_button = QtWidgets.QToolButton(toolbar)
        color_button.setText("Color…")
        color_button.setEnabled(False)
        color_button.clicked.connect(self._choose_format_color)
        color_button.setToolTip("Select an object to adjust its colour")
        controls.color_button = color_button
        toolbar.addWidget(color_button)
        self._style_toolbar_button(toolbar, color_button)

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
        self.tab_widget.addTab(tab, "Graph")
        self.tab_widget.setCurrentWidget(tab)
        self._register_plot_tab(tab, canvas, ax, descriptor)

    def _register_plot_tab(
        self,
        tab: QtWidgets.QWidget,
        canvas: FigureCanvas,
        axes: Any,
        descriptor: TabDescriptor | None = None,
    ) -> None:
        self._canvas_by_tab[tab] = canvas
        self._axes_by_tab[tab] = axes
        if descriptor is not None:
            self._tab_descriptors[tab] = descriptor
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
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        if self._dark_mode_enabled:
            self._apply_dark_mode_to_axes(axes, True)
        self._update_navigation_enabled()
        if self.tab_widget.currentWidget() is tab:
            self._rebuild_object_manager_for_tab(tab)
        self._update_tab_buttons()
        self._mark_project_dirty()

    def _ensure_graph_tree_item(
        self, tab: QtWidgets.QWidget, descriptor: TabDescriptor
    ) -> QtWidgets.QTreeWidgetItem | None:
        tree = getattr(self, "project_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            return None
        root = self._ensure_graph_tree_root()
        if root is None:
            return None
        label = descriptor.root_label or descriptor.title or "Plot"
        item = QtWidgets.QTreeWidgetItem([label, descriptor.title or ""])
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
            root = QtWidgets.QTreeWidgetItem(["Plots"])
            root.setFirstColumnSpanned(True)
            root.setExpanded(True)
            tree.insertTopLevelItem(0, root)
            self._graph_tree_root = root
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

    def _update_project_title(self) -> None:
        title = self._base_title
        if self._project_path is not None:
            title = f"{self._base_title} — {self._project_path.name}"
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
            target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Save Project",
                f"Failed to write project file:\n{exc}",
            )
            return
        self._project_path = target
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
        self._update_project_title()
        self._remember_recent_project(path)
        self._after_project_loaded(path, payload)
        self._update_project_actions()
        self._clear_project_dirty()

    # ------------------------------------------------------------------ data import helpers
    def _import_data_from_files(self) -> None:
        start_dir = self._project_dialog_start_directory()
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
        start_dir = self._project_dialog_start_directory()
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Import Data Folder",
            str(start_dir),
        )
        if not directory:
            return
        self._import_paths([Path(directory)])

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
        total_files = len(files)
        progress: QtWidgets.QProgressDialog | None = None
        if total_files > 1:
            progress = QtWidgets.QProgressDialog(
                "Importing data…",
                "Cancel",
                0,
                total_files,
                self,
            )
            progress.setWindowTitle("Import Data")
            progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
        cancelled = False
        position = 0
        try:
            for position, file_path in enumerate(files, start=1):
                if progress is not None:
                    progress.setLabelText(
                        f"Importing {file_path.name} ({position}/{total_files})"
                    )
                    progress.setValue(position - 1)
                    try:
                        QtWidgets.QApplication.processEvents()
                    except Exception:
                        pass
                    if progress.wasCanceled():
                        cancelled = True
                        break
                result = self._load_workbook_from_file(file_path)
                if result is None:
                    continue
                workbook, worksheets = result
                try:
                    self._register_imported_workbook(workbook, worksheets)
                except Exception as exc:  # pragma: no cover - defensive, UI fallback
                    errors.append(f"{file_path}: {exc}")
                    continue
                imported += len(worksheets)
        finally:
            if progress is not None:
                try:
                    final_value = total_files if not cancelled else max(position - 1, 0)
                    progress.setValue(final_value)
                    progress.close()
                except Exception:
                    pass
        if cancelled:
            QtWidgets.QMessageBox.information(
                self,
                "Import Data",
                "Import cancelled before all files were processed.",
            )
            return
        if errors:
            QtWidgets.QMessageBox.warning(
                self,
                "Import Data",
                "Some files could not be imported:\n" + "\n".join(errors[:10]),
            )
        if imported:
            self._refresh_imported_data_summary()
            self._update_project_actions()
            if provided_paths:
                self._remember_import_directory(provided_paths[0])
                self._last_import_sources = [str(path) for path in provided_paths]
                self._persist_import_sources(provided_paths)
            self._session_has_imports = True
            self._mark_project_dirty()
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
            if suffix in {".csv", ".tsv", ".txt"}:
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

        workbook_item = QtWidgets.QTreeWidgetItem([workbook.name, str(workbook.source or "")])
        self._assign_project_payload(workbook_item, ("worksheet_group", workbook.key))
        parent_item.addChild(workbook_item)
        workbook_item.setExpanded(True)
        self._data_workbook_items[workbook.key] = workbook_item

        for worksheet in worksheets:
            worksheet.workbook_key = workbook.key
            self._worksheets[worksheet.key] = worksheet
            sheet_item = QtWidgets.QTreeWidgetItem([worksheet.name, ""])
            self._assign_project_payload(sheet_item, ("worksheet", worksheet.key))
            workbook_item.addChild(sheet_item)
            self._worksheet_tree_items[worksheet.key] = sheet_item

        self._sync_shared_action_states()
        if worksheets:
            self._mark_project_dirty()
            self._session_has_imports = True

    def _append_log(self, message: str, *, level: Literal["info", "error"] = "info") -> None:
        view = getattr(self, "log_view", None)
        if isinstance(view, QtWidgets.QPlainTextEdit):
            view.appendPlainText(message)
            try:
                view.ensureCursorVisible()
            except Exception:
                pass

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

    def _ensure_data_root(self) -> QtWidgets.QTreeWidgetItem:
        if self._data_tree_root is None:
            root = QtWidgets.QTreeWidgetItem(["Imported Data", ""])
            root.setExpanded(True)
            self.project_tree.addTopLevelItem(root)
            self._data_tree_root = root
        return self._data_tree_root

    def _ensure_workbook_root(self) -> QtWidgets.QTreeWidgetItem:
        if self._workbook_tree_root is None:
            root = QtWidgets.QTreeWidgetItem(["Workbooks", ""])
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
        if root is None:
            return
        if root.childCount() > 0:
            return
        tree = self.project_tree
        if isinstance(tree, QtWidgets.QTreeWidget):
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
        if item is None:
            label = resolved.name or str(resolved)
            item = QtWidgets.QTreeWidgetItem([label, str(resolved)])
            self._assign_project_payload(item, ("worksheet_group", resolved))
            root.addChild(item)
            item.setExpanded(True)
            self._data_folder_items[resolved] = item
        return item

    def _remove_worksheet(self, key: Hashable) -> None:
        tab = self._worksheet_tabs_open.pop(key, None)
        if tab is not None:
            self._tab_to_worksheet_key.pop(tab, None)
            index = self.tab_widget.indexOf(tab)
            if index >= 0:
                self.tab_widget.removeTab(index)
        item = self._worksheet_tree_items.pop(key, None)
        if item is not None:
            parent = item.parent()
            if parent is not None:
                index = parent.indexOfChild(item)
                if index >= 0:
                    parent.takeChild(index)
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
            item.setText(1, f"{rows} × {columns}")
        for key, item in self._data_workbook_items.items():
            workbook = self._workbooks.get(key)
            if workbook is None:
                continue
            count = len(workbook.worksheets)
            if workbook.source is not None:
                item.setText(1, f"{workbook.source} ({count} sheet{'s' if count != 1 else ''})")
            else:
                item.setText(1, f"{count} sheet{'s' if count != 1 else ''}")

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
        self._reorder_shared_menus(menu_bar)

    def _reorder_shared_menus(self, menu_bar: QtWidgets.QMenuBar) -> None:
        desired = [
            "mw_shared_file",
            "mw_shared_edit",
            "mw_shared_view",
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

        code = getattr(self, "PROJECT_CODE", None)
        if isinstance(code, str) and code.strip():
            prefix = code.strip()
        else:
            base_title = getattr(self, "_base_title", "")
            if isinstance(base_title, str) and base_title.strip():
                candidate = base_title.strip()
            else:
                candidate = self.windowTitle().strip()
            sanitized = candidate.replace("—", " ").replace("�?", " ").replace("-", " ")
            parts = [segment for segment in sanitized.replace("/", " ").split() if segment]
            prefix = "_".join(parts) if parts else self.__class__.__name__
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
            "Choose “Save project” to keep your current imports, or close without saving to discard them."
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
        env_override = os.environ.get("MW_DISABLE_DOCK_SWITCHER", "")
        if env_override.strip().lower() in {"1", "true", "yes", "on"}:
            return False
        return True

    def _create_dock_switcher(
        self,
        docks: Sequence[QtWidgets.QDockWidget],
        *,
        side: Literal["left", "right"],
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
                width = max(log_dock.sizeHint().width(), 220)
            self._primary_dock_widths[log_dock] = width

    def _apply_initial_primary_dock_size(
        self, dock: QtWidgets.QDockWidget | None, default_width: int
    ) -> None:
        if not isinstance(dock, QtWidgets.QDockWidget):
            return
        width = self._load_primary_dock_width(dock)
        min_width = max(PRIMARY_DOCK_MIN_WIDTH, dock.minimumWidth())
        if width is None or width <= 0:
            width = max(dock.sizeHint().width(), default_width)
        width = max(width, min_width)
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
        self._primary_dock_widths[dock] = width
        return width

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
        name = dock.objectName() if isinstance(dock, QtWidgets.QDockWidget) else ""
        if name in {"projectExplorerDock", "objectManagerDock"}:
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
        try:
            self._pin_primary_dock_switchers()
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
        self._queue_retabify_primary_docks()
        self._remember_primary_dock_state(dock, visible=dock.isVisible())

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
                    current_width = candidate.width()
                    stored_width = self._primary_dock_widths.get(candidate, 0)
                    width_reference = max(current_width, stored_width)
                    if width_reference <= 0:
                        continue
                    if stored_width and abs(width_reference - stored_width) <= 2:
                        continue
                    tracked_widths[candidate] = width_reference

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

            if isinstance(project_dock, QtWidgets.QDockWidget) and not project_dock.isFloating():
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
                self._primary_dock_widths[dock] = width
                self._apply_dock_width(dock, width)
        finally:
            self._retabbing_docks = False

    def _apply_dock_width(self, dock: QtWidgets.QDockWidget, width: int) -> None:
        if not isinstance(dock, QtWidgets.QDockWidget) or dock.isFloating() or width <= 0:
            return
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
                self._show_tab(tab)
        elif role == "worksheet":
            path = data[1]
            if isinstance(path, Path):
                self._open_worksheet_tab(path)
        elif role == "worksheet_group":
            target = data[1]
            if isinstance(target, Hashable) and target in self._workbooks:
                workbook = self._workbooks.get(target)
                if workbook is not None and workbook.worksheets:
                    self._open_worksheet_tab(workbook.worksheets[0])
                    return
            item.setExpanded(not item.isExpanded())

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
        if kind not in {"legend", "line"}:
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
            elif kind == "legend" and hasattr(target, "set_visible"):
                target.set_visible(visible)
                changed = True
        except Exception:
            changed = False
        if changed:
            item.setData(0, OBJECT_TREE_STATE_ROLE, new_state)
            canvas = self._canvas_by_tab.get(self.tab_widget.currentWidget())
            if canvas is not None:
                try:
                    canvas.draw_idle()
                except Exception:
                    pass
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
        if data.get("kind") != "legend":
            return
        legend = data.get("object")
        if not isinstance(legend, Legend):
            return
        dialog = LegendSettingsDialog(self, legend)
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

    def _rebuild_object_manager_for_tab(self, tab: QtWidgets.QWidget | None, *_: Any) -> None:
        """Rebuild the object manager tree for ``tab`` with all axes, legends, and lines."""

        tree = getattr(self, "object_tree", None)
        if not isinstance(tree, QtWidgets.QTreeWidget):
            return
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

        def _make_item(label: str, kind: str | None = None, obj: Any | None = None) -> QtWidgets.QTreeWidgetItem:
            item = QtWidgets.QTreeWidgetItem([label])
            item.setFlags(
                item.flags()
                | QtCore.Qt.ItemFlag.ItemIsEnabled
                | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            if kind and obj is not None:
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, {"kind": kind, "object": obj})
                if kind in {"legend", "line"}:
                    visible = True
                    getter = getattr(obj, "get_visible", None)
                    if callable(getter):
                        try:
                            visible = bool(getter())
                        except Exception:
                            visible = True
                    self._apply_object_item_visibility(item, visible)
            return item

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
            legend = None
            try:
                legend = axis.get_legend()
            except Exception:
                legend = None
            if legend is not None:
                try:
                    if not hasattr(legend, "_mw_show_symbols"):
                        legend._mw_show_symbols = True
                    if not hasattr(legend, "_mw_text_follows_handles"):
                        legend._mw_text_follows_handles = False
                    if not hasattr(legend, "_mw_orientation"):
                        legend._mw_orientation = "auto"
                    if not hasattr(legend, "_mw_placement"):
                        legend._mw_placement = "inside"
                        legend.set_bbox_to_anchor(None)
                    if not hasattr(legend, "_mw_draggable"):
                        legend._mw_draggable = True
                    if not getattr(legend, "get_draggable", lambda: True)():
                        legend.set_draggable(True)
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
                axis_item.addChild(legend_item)
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
                axis_item.addChild(line_item)
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
        if text_targets:
            self._set_format_selection(("text", tuple(text_targets)))
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
        self._format_selection = selection
        self._update_format_toolbar_state()

    def _update_format_toolbar_state(self) -> None:
        controls = self._format_controls
        if controls.toolbar is None:
            return
        size_spin = controls.size_spin
        bold_action = controls.bold_action
        italic_action = controls.italic_action
        underline_action = controls.underline_action
        line_actions = (
            (controls.line_action, "line"),
            (controls.scatter_action, "scatter"),
            (controls.line_symbol_action, "line_symbol"),
        )
        self._format_updating = True
        try:
            selection = self._format_selection
            if selection is None:
                if size_spin is not None:
                    size_spin.blockSignals(True)
                    size_spin.setEnabled(False)
                    size_spin.blockSignals(False)
                for action in (bold_action, italic_action, underline_action):
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
                return
            kind = selection[0]
            if kind == "text":
                texts = selection[1]
                text = texts[0] if isinstance(texts, tuple) and texts else None
                if text is not None:
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
                    for action, _ in line_actions:
                        if action is not None:
                            action.blockSignals(True)
                            action.setChecked(False)
                            action.blockSignals(False)
                            action.setEnabled(False)
                    self._set_color_button_state("text", self._qcolor_from_mpl(text.get_color()))
                    return
            elif kind == "line":
                target = selection[1]
                if isinstance(target, Line2D):
                    if size_spin is not None:
                        size_spin.blockSignals(True)
                        size_spin.setEnabled(False)
                        size_spin.blockSignals(False)
                    for action in (bold_action, italic_action, underline_action):
                        if action is not None:
                            action.blockSignals(True)
                            action.setChecked(False)
                            action.blockSignals(False)
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
                    return
            if size_spin is not None:
                size_spin.blockSignals(True)
                size_spin.setEnabled(False)
                size_spin.blockSignals(False)
            for action in (bold_action, italic_action, underline_action):
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
            button.setText("Color…")
            button.setToolTip("Select an object to adjust its colour")
            button.setIcon(QtGui.QIcon())
            return
        button.setEnabled(True)
        if role == "text":
            button.setText("Text color…")
            button.setToolTip("Change the selected text colour")
        elif role == "line":
            button.setText("Line color…")
            button.setToolTip("Change the selected line colour")
        else:
            button.setText("Color…")
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

    def _redraw_artist(self, artist: Any) -> None:
        figure = getattr(artist, "figure", None)
        if figure is None:
            axes = getattr(artist, "axes", None)
            if axes is not None:
                figure = getattr(axes, "figure", None)
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
            self._hidden_tabs.discard(tab)
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

        minimize_button = QtWidgets.QToolButton(container)
        minimize_button.setText("-")
        minimize_button.setAutoRaise(True)
        minimize_button.setToolTip("Hide this tab")
        minimize_button.clicked.connect(lambda _, t=tab: self._minimize_tab(t))
        layout.addWidget(minimize_button)

        if tab in self._tab_descriptors:
            close_button = QtWidgets.QToolButton(container)
            close_button.setText("x")
            close_button.setAutoRaise(True)
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
        self._set_navigation_mode(None)
        self._update_tab_buttons()
        self._focus_tree_on_tab(tab)
        self._rebuild_object_manager_for_tab(tab)
        self._update_worksheet_actions()
        self._update_navigation_enabled()

    def _focus_tree_on_tab(self, tab: QtWidgets.QWidget | None) -> None:
        self.project_tree.blockSignals(True)
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
            self.project_tree.setCurrentItem(target_item)
            self.project_tree.scrollToItem(target_item)
        self.project_tree.blockSignals(False)

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


class _MdiTabProxy(QtWidgets.QWidget):
    """Proxy that mimics a subset of QTabWidget behaviour using a QMdiArea backend."""

    currentChanged = QtCore.pyqtSignal(int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._mdi = QtWidgets.QMdiArea(self)
        self._mdi.setViewMode(QtWidgets.QMdiArea.ViewMode.TabbedView)
        self._mdi.setOption(QtWidgets.QMdiArea.AreaOption.DontMaximizeSubWindowOnActivation, True)
        try:
            self._mdi.setTabsClosable(False)
            self._mdi.setTabsMovable(True)
        except Exception:
            pass
        layout.addWidget(self._mdi)

        self._widgets: list[QtWidgets.QWidget] = []
        self._subwindows: dict[QtWidgets.QWidget, _ManagedSubWindow] = {}
        self._titles: dict[QtWidgets.QWidget, str] = {}
        self._icons: dict[QtWidgets.QWidget, QtGui.QIcon] = {}
        self._visible: dict[QtWidgets.QWidget, bool] = {}
        self._blocking = False
        self._mdi.subWindowActivated.connect(self._handle_subwindow_activated)

    # ------------------------------------------------------------------ helpers
    def _handle_subwindow_activated(self, sub: QtWidgets.QMdiSubWindow | None) -> None:
        if self._blocking:
            return
        widget = sub.widget() if sub is not None else None
        index = self.indexOf(widget) if widget is not None else -1
        self.currentChanged.emit(index)

    def _subwindow_for(self, widget: QtWidgets.QWidget | None) -> _ManagedSubWindow | None:
        if widget is None:
            return None
        return self._subwindows.get(widget)

    def _activate_index(self, index: int) -> None:
        if not 0 <= index < len(self._widgets):
            self._mdi.setActiveSubWindow(None)
            self.currentChanged.emit(-1)
            return
        widget = self._widgets[index]
        sub = self._subwindow_for(widget)
        if sub is None:
            return
        self._blocking = True
        self._mdi.setActiveSubWindow(sub)
        sub.showNormal()
        sub.raise_()
        self._blocking = False
        self.currentChanged.emit(index)

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
            self._mdi.removeSubWindow(sub)
            sub.setWidget(None)
            sub.deleteLater()
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
        sub.setWidget(widget)
        sub.setWindowTitle(title)
        icon = self._icons.get(widget, QtGui.QIcon())
        if not icon.isNull():
            sub.setWindowIcon(icon)
        self._mdi.addSubWindow(sub)
        sub.show()

        if widget in self._widgets:
            self._remove_widget(widget)
        self._widgets.insert(index, widget)
        self._subwindows[widget] = sub
        self._titles[widget] = title
        self._icons[widget] = icon
        self._visible[widget] = True

        self._activate_index(index)
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
        widget = self.widget(index)
        if widget is None:
            return
        sub = self._subwindow_for(widget)
        if sub is None:
            return
        self._visible[widget] = bool(visible)
        if visible:
            if sub not in self._mdi.subWindowList():
                self._mdi.addSubWindow(sub)
            sub.show()
            self._activate_index(index)
        else:
            was_active = self._mdi.activeSubWindow() is sub
            sub.hide()
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
