from __future__ import annotations

import datetime
import os
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
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib import colors as mcolors
import pandas as pd

from plotting.python_console import PythonConsoleWidget
from plotting.utils import install_standard_menu, developer_options


OBJECT_TREE_STATE_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1


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


class _DockSwitcherWidget(QtWidgets.QWidget):
    """Vertical tab bar that mirrors dock visibility with hover-to-open behaviour."""

    _HOVER_CLOSE_DELAY_MS = 200

    def __init__(
        self,
        docks: Sequence[QtWidgets.QDockWidget],
        *,
        side: Literal["left", "right"],
        parent: QtWidgets.QWidget | None = None,
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

        for idx, dock in enumerate(self._docks):
            tab_index = self._tab_bar.addTab(dock.windowTitle())
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
            self._dock_widths[dock] = max(dock.sizeHint().width(), 220)
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
                            self._pinned_index = None
                            self._collapse_all()
                        else:
                            self._pinned_index = index
                            self._activate_index(index)
                            self._tab_bar.setCurrentIndex(index)
                        return True
            if event.type() in (
                QtCore.QEvent.Type.MouseMove,
                QtCore.QEvent.Type.HoverMove,
                QtCore.QEvent.Type.HoverEnter,
            ):
                index = self._tab_index_from_event(event)
                if index >= 0:
                    self._activate_index(index)
            elif event.type() in (QtCore.QEvent.Type.HoverLeave, QtCore.QEvent.Type.Leave):
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
            dock.raise_()
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
            if self._pinned_index == index:
                self._pinned_index = None
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
        self._pinned_index = valid[0]
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

        def _resize() -> None:
            if dock.isFloating():
                return
            try:
                dock.resize(width, dock.height() or dock.sizeHint().height())
            except Exception:
                pass
            main_window = self._main_window()
            if not isinstance(main_window, QtWidgets.QMainWindow):
                return
            try:
                main_window.resizeDocks([dock], [width], QtCore.Qt.Orientation.Horizontal)
            except Exception:
                pass
            try:
                frame = main_window.frameGeometry()
                screen = QtGui.QGuiApplication.screenAt(frame.center())
                if screen is None:
                    screen = QtGui.QGuiApplication.primaryScreen()
                if screen is not None:
                    available_rect = screen.availableGeometry()
                    new_left = max(available_rect.left(), min(frame.left(), available_rect.right() - frame.width()))
                    new_top = max(available_rect.top(), min(frame.top(), available_rect.bottom() - frame.height()))
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


class PyPlotWindow(QtWidgets.QMainWindow):
    """Shared UI frame used by plotting tools."""

    help_topic: str = "plotter"
    PROJECT_EXTENSION: str = ".pypj"
    PROJECT_VERSION: int = 1
    PROJECT_CODE: str | None = None
    PROJECT_SETTINGS_PREFIX: str = "project"
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

        # Tab/graph bookkeeping shared by subclasses.
        self._tab_descriptors: Dict[QtWidgets.QWidget, TabDescriptor] = {}
        self._canvas_by_tab: Dict[QtWidgets.QWidget, FigureCanvas] = {}
        self._axes_by_tab: Dict[QtWidgets.QWidget, Any] = {}
        self._plot_tabs: Dict[float, PlotTabState] = {}
        self._object_items: Dict[
            tuple[QtWidgets.QWidget, tuple[str, float | str]],
            QtWidgets.QTreeWidgetItem,
        ] = {}
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
        self._script_panel_container: QtWidgets.QWidget | None = None
        self._script_panel_layout: QtWidgets.QVBoxLayout | None = None
        self._data_sources_widget: QtWidgets.QWidget | None = None
        self._data_tree_root: QtWidgets.QTreeWidgetItem | None = None
        self._data_folder_items: Dict[Path, QtWidgets.QTreeWidgetItem] = {}
        self._data_workbook_items: Dict[Hashable, QtWidgets.QTreeWidgetItem] = {}
        self._workbooks: Dict[Hashable, WorkbookData] = {}
        self._worksheets: Dict[Hashable, WorksheetData] = {}
        self._worksheet_models: Dict[Hashable, WorksheetTableModel] = {}
        self._primary_dock_widths: Dict[QtWidgets.QDockWidget, int] = {}
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

        self._build_base_ui()
        self._update_project_title()
        self._update_project_actions()
        developer_options().keep_files_changed.connect(self._handle_keep_files_changed)
        QtCore.QTimer.singleShot(0, self._restore_persisted_imports)

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
        raise NotImplementedError

    def _save_current_graph(self) -> None:
        raise NotImplementedError

    def _normalize_current_graph(self) -> None:
        raise NotImplementedError

    def _export_txt(self) -> None:
        raise NotImplementedError

    def _open_origin_prompt(self) -> None:
        raise NotImplementedError

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

    def _set_data_sources_visible(self, visible: bool) -> None:
        if self._data_sources_widget is None:
            return
        self._data_sources_widget.setVisible(visible)

    # ------------------------------------------------------------------ base UI
    def _build_base_ui(self) -> None:
        central = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central)
        central_layout.setContentsMargins(12, 12, 12, 12)
        central_layout.setSpacing(10)

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
        project_dock = self._create_dock_widget("Project Explorer", "projectExplorerDock")
        project_dock.setWidget(self.project_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, project_dock)
        project_dock.setMinimumWidth(240)
        self.project_dock = project_dock

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_dock = self._create_dock_widget("Message Log", "messageLogDock")
        log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, log_dock)
        log_dock.hide()
        self.log_dock = log_dock

        self.object_tree = QtWidgets.QTreeWidget()
        self.object_tree.setHeaderLabels(["Object Manager"])
        self.object_tree.setColumnCount(1)
        self.object_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.object_tree.itemChanged.connect(self._dispatch_object_item_changed)
        self.object_tree.currentItemChanged.connect(
            self._handle_object_selection_changed
        )
        object_dock = self._create_dock_widget("Object Manager", "objectManagerDock")
        object_dock.setWidget(self.object_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, object_dock)
        object_dock.setMinimumWidth(240)
        self.object_dock = object_dock

        graph_settings_widget = QtWidgets.QWidget()
        graph_layout = QtWidgets.QVBoxLayout(graph_settings_widget)
        graph_layout.setContentsMargins(8, 8, 8, 8)
        graph_layout.setSpacing(12)
        self._populate_graph_settings(graph_layout)
        graph_layout.addStretch(1)

        graph_dock = self._create_dock_widget("Graph Settings", "graphSettingsDock")
        graph_dock.setWidget(graph_settings_widget)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, graph_dock)
        graph_dock.hide()
        self.graph_dock = graph_dock

        self.console_widget = PythonConsoleWidget(self)
        self.console_widget.set_environment({"window": self, "pd": pd})
        self.console_widget.executed.connect(self._handle_console_execution)
        console_dock = self._create_dock_widget("Python Console", "pythonConsoleDock")
        console_dock.setWidget(self.console_widget)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, console_dock)
        console_dock.hide()
        self.console_dock = console_dock

        self._setup_action_toolbar()
        self._setup_format_toolbar()

        self._dock_switcher_panels: list[QtWidgets.QDockWidget | None] = []
        if self._dock_switcher_supported():
            self._dock_switcher_panels.append(
                self._create_dock_switcher(
                    (project_dock, log_dock, graph_dock),
                    side="left",
                    initial_visible=(0,),
                )
            )
            self._dock_switcher_panels.append(
                self._create_dock_switcher((object_dock,), side="right", initial_visible=(0,))
            )
        else:
            self._dock_switcher_panels.extend([None, None])

        QtCore.QTimer.singleShot(0, self._apply_initial_dock_sizes)

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
        self._after_base_ui_created(project_dock=project_dock, log_dock=log_dock, graph_dock=graph_dock)
        self._retabify_primary_docks()
        view_menu = menu_bar.findChild(QtWidgets.QMenu, "mw_shared_view")
        if view_menu is not None and hasattr(self, "console_dock"):
            view_menu.addSeparator()
            console_action = view_menu.addAction("Python Console")
            if console_action is not None:
                console_action.setCheckable(True)
                console_action.setChecked(self.console_dock.isVisible())
                console_action.toggled.connect(self.console_dock.setVisible)
                self.console_dock.visibilityChanged.connect(
                    lambda visible, action=console_action: self._sync_console_action(action, visible)
                )

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

    def _setup_action_toolbar(self) -> None:
        toolbar = QtWidgets.QToolBar("Plot actions", self)
        toolbar.setObjectName("mw_action_toolbar")
        toolbar.setMovable(True)
        toolbar.setFloatable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
        self._action_toolbar = toolbar

        load_action = toolbar.addAction("Load data")
        load_action.setEnabled(False)
        load_action.triggered.connect(self._load_data)
        self.load_data_button = load_action

        generate_action = toolbar.addAction("Generate plots")
        generate_action.setEnabled(False)
        generate_action.triggered.connect(self._generate_plots)
        self.plot_button = generate_action

        popout_action = toolbar.addAction("Open in Matplotlib")
        popout_action.setEnabled(False)
        popout_action.triggered.connect(self._open_matplotlib_window)
        self.popout_button = popout_action

        toolbar.addSeparator()

        save_action = toolbar.addAction("Save graph…")
        save_action.setEnabled(False)
        save_action.triggered.connect(self._save_current_graph)
        self.save_graph_button = save_action

        normalize_action = toolbar.addAction("Normalize Y")
        normalize_action.setEnabled(False)
        normalize_action.triggered.connect(self._normalize_current_graph)
        self.normalize_button = normalize_action

        export_action = toolbar.addAction("Export TXT…")
        export_action.setEnabled(False)
        export_action.triggered.connect(self._export_txt)
        self.export_button = export_action

        origin_action = toolbar.addAction("Open in Origin…")
        origin_action.setEnabled(False)
        origin_action.triggered.connect(self._open_origin_prompt)
        self.open_origin_button = origin_action

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
        toolbar.setMovable(True)
        toolbar.setFloatable(False)
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

        italic_action = toolbar.addAction("I")
        italic_action.setCheckable(True)
        italic_action.setEnabled(False)
        italic_action.triggered.connect(self._apply_text_italic)
        italic_action.setToolTip("Toggle italic text")
        controls.italic_action = italic_action

        underline_action = toolbar.addAction("U")
        underline_action.setCheckable(True)
        underline_action.setEnabled(False)
        underline_action.triggered.connect(self._apply_text_underline)
        underline_action.setToolTip("Toggle underlined text")
        controls.underline_action = underline_action

        color_button = QtWidgets.QToolButton(toolbar)
        color_button.setText("Color…")
        color_button.setEnabled(False)
        color_button.clicked.connect(self._choose_format_color)
        color_button.setToolTip("Select an object to adjust its colour")
        controls.color_button = color_button
        toolbar.addWidget(color_button)

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

        scatter_action = toolbar.addAction("Scatter")
        scatter_action.setCheckable(True)
        scatter_action.setEnabled(False)
        scatter_action.triggered.connect(lambda checked: self._apply_line_style("scatter", checked))
        scatter_action.setToolTip("Show only markers for the selection")
        line_group.addAction(scatter_action)
        controls.scatter_action = scatter_action

        line_symbol_action = toolbar.addAction("Line + symbol")
        line_symbol_action.setCheckable(True)
        line_symbol_action.setEnabled(False)
        line_symbol_action.triggered.connect(
            lambda checked: self._apply_line_style("line_symbol", checked)
        )
        line_symbol_action.setToolTip("Show the selection with lines and markers")
        line_group.addAction(line_symbol_action)
        controls.line_symbol_action = line_symbol_action

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
        if self.tab_widget.currentWidget() is tab:
            self._rebuild_object_manager_for_tab(tab)
        self._update_tab_buttons()

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

        folder_item = self._ensure_folder_item(workbook.folder)
        workbook_item = QtWidgets.QTreeWidgetItem([workbook.name, str(workbook.source or "")])
        self._assign_project_payload(workbook_item, ("worksheet_group", workbook.key))
        folder_item.addChild(workbook_item)
        workbook_item.setExpanded(True)
        self._data_workbook_items[workbook.key] = workbook_item

        for worksheet in worksheets:
            worksheet.workbook_key = workbook.key
            self._worksheets[worksheet.key] = worksheet
            sheet_item = QtWidgets.QTreeWidgetItem([worksheet.name, ""])
            self._assign_project_payload(sheet_item, ("worksheet", worksheet.key))
            workbook_item.addChild(sheet_item)
            self._worksheet_tree_items[worksheet.key] = sheet_item

    def _ensure_data_root(self) -> QtWidgets.QTreeWidgetItem:
        if self._data_tree_root is None:
            root = QtWidgets.QTreeWidgetItem(["Imported Data", ""])
            root.setExpanded(True)
            self.project_tree.addTopLevelItem(root)
            self._data_tree_root = root
        return self._data_tree_root

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
            header.setSectionResizeMode(row_index, QtWidgets.QHeaderView.ResizeToContents)
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
        _ = menu_bar  # appease linters until subclasses override

    def _after_base_ui_created(
        self,
        *,
        project_dock: QtWidgets.QDockWidget,
        log_dock: QtWidgets.QDockWidget,
        graph_dock: QtWidgets.QDockWidget,
    ) -> None:
        """Hook invoked once base dock widgets have been created."""
        _ = (project_dock, log_dock, graph_dock)

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

        switcher = _DockSwitcherWidget(docks, side=side, parent=panel)
        panel.setWidget(switcher)
        panel.setMinimumWidth(switcher.sizeHint().width())
        panel.setMaximumWidth(switcher.sizeHint().width())

        if initial_visible is not None:
            try:
                switcher.set_initial_visible(initial_visible)
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
        return panel

    def _apply_initial_dock_sizes(self) -> None:
        project = getattr(self, "project_dock", None)
        if isinstance(project, QtWidgets.QDockWidget):
            width = max(project.sizeHint().width(), 320)
            try:
                self.resizeDocks([project], [width], QtCore.Qt.Orientation.Horizontal)
            except Exception:
                try:
                    project.resize(width, project.height())
                except Exception:
                    pass
        obj_dock = getattr(self, "object_dock", None)
        if isinstance(obj_dock, QtWidgets.QDockWidget):
            width = max(obj_dock.sizeHint().width(), 320)
            try:
                self.resizeDocks([obj_dock], [width], QtCore.Qt.Orientation.Horizontal)
            except Exception:
                try:
                    obj_dock.resize(width, obj_dock.height())
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

        def _resize() -> None:
            if dock.isFloating():
                return
            try:
                dock.resize(width, dock.height() or dock.sizeHint().height())
            except Exception:
                pass
            try:
                self.resizeDocks([dock], [width], QtCore.Qt.Orientation.Horizontal)
            except Exception:
                pass
            try:
                frame = self.frameGeometry()
                screen = QtGui.QGuiApplication.screenAt(frame.center())
                if screen is None:
                    screen = QtGui.QGuiApplication.primaryScreen()
                if screen is not None:
                    available_rect = screen.availableGeometry()
                    new_left = max(available_rect.left(), min(frame.left(), available_rect.right() - frame.width()))
                    new_top = max(available_rect.top(), min(frame.top(), available_rect.bottom() - frame.height()))
                    self.move(new_left, new_top)
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, _resize)

    def _handle_console_execution(self, code: str, result: object) -> None:
        if not hasattr(self, "log_view") or self.log_view is None:
            return
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] >>> {code}")
        if result is not None:
            self.log_view.appendPlainText(repr(result))

    def _sync_console_action(self, action: QtGui.QAction, visible: bool) -> None:
        if action.isChecked() == visible:
            return
        action.blockSignals(True)
        action.setChecked(visible)
        action.blockSignals(False)

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
        if allow_toggle:
            if not item.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable:
                item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
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

    def _handle_object_selection_changed(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        _ = previous
        selection: tuple[str, Any] | None = None
        if isinstance(current, QtWidgets.QTreeWidgetItem):
            data = current.data(0, QtCore.Qt.ItemDataRole.UserRole)
            kind: str | None = None
            target: Any | None = None
            if isinstance(data, dict):
                kind = data.get("kind")
                target = data.get("object")
            elif isinstance(data, tuple) and len(data) >= 2:
                kind = cast(str, data[0])
                target = data[1]
            if kind == "text" and isinstance(target, Text):
                selection = ("text", target)
            elif kind == "line" and isinstance(target, Line2D):
                selection = ("line", target)
        self._set_format_selection(selection)

    def _set_format_selection(self, selection: tuple[str, Any] | None) -> None:
        if selection is not None:
            kind, target = selection
            if kind == "text" and not isinstance(target, Text):
                selection = None
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
            kind, target = selection
            if kind == "text" and isinstance(target, Text):
                if size_spin is not None:
                    size_spin.blockSignals(True)
                    try:
                        size_spin.setValue(int(round(float(target.get_fontsize()))))
                    except Exception:
                        pass
                    size_spin.blockSignals(False)
                    size_spin.setEnabled(True)
                if bold_action is not None:
                    bold_action.blockSignals(True)
                    bold_action.setChecked(self._text_is_bold(target))
                    bold_action.blockSignals(False)
                    bold_action.setEnabled(True)
                if italic_action is not None:
                    italic_action.blockSignals(True)
                    italic_action.setChecked(self._text_is_italic(target))
                    italic_action.blockSignals(False)
                    italic_action.setEnabled(True)
                if underline_action is not None:
                    underline_action.blockSignals(True)
                    underline = False
                    try:
                        underline = bool(target.get_underline())
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
                self._set_color_button_state("text", self._qcolor_from_mpl(target.get_color()))
                return
            if kind == "line" and isinstance(target, Line2D):
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
        text = selection[1]
        try:
            text.set_fontsize(value)
        except Exception:
            return
        self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_bold(self, checked: bool) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        text = selection[1]
        try:
            text.set_fontweight("bold" if checked else "normal")
        except Exception:
            return
        self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_italic(self, checked: bool) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        text = selection[1]
        try:
            text.set_fontstyle("italic" if checked else "normal")
        except Exception:
            return
        self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _apply_text_underline(self, checked: bool) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if not selection or selection[0] != "text":
            return
        text = selection[1]
        try:
            text.set_underline(bool(checked))
        except Exception:
            return
        self._redraw_artist(text)
        self._update_format_toolbar_state()

    def _choose_format_color(self) -> None:
        if self._format_updating:
            return
        selection = self._format_selection
        if selection is None:
            return
        role, target = selection
        if role == "text" and isinstance(target, Text):
            initial = self._qcolor_from_mpl(target.get_color())
        elif role == "line" and isinstance(target, Line2D):
            initial = self._qcolor_from_mpl(target.get_color())
        else:
            return
        color = QtWidgets.QColorDialog.getColor(initial, self, "Select colour")
        if not color.isValid():
            return
        mpl_color = self._mpl_color_from_qcolor(color)
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

    def undo(self) -> None:
        self._history.undo()
        self._update_tab_buttons()

    def redo(self) -> None:
        self._history.redo()
        self._update_tab_buttons()

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
        self._rebuild_object_manager_for_tab(self.tab_widget.currentWidget())
        self._after_tab_restored(info)

    def _after_tab_removed(self, info: _RemovedTabInfo) -> None:
        _ = info

    def _after_tab_restored(self, info: _RemovedTabInfo) -> None:
        _ = info

    # ------------------------------------------------------------------ state helpers
    def _handle_current_tab_changed(self, index: int) -> None:
        tab = self.tab_widget.widget(index) if index >= 0 else None
        self._update_tab_buttons()
        self._focus_tree_on_tab(tab)
        self._rebuild_object_manager_for_tab(tab)
        self._update_worksheet_actions()

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
    "PyPlotWindow",
    "GraphLineState",
    "GraphSelectionDialog",
    "PlotTabState",
    "TabDescriptor",
]
