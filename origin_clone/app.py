"""High-level Origin clone prototype built with PyQt6."""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast

import pandas as pd
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from matplotlib.figure import Figure

from plotting.utils import ensure_app_theme, install_standard_menu


@dataclass
class GraphLayer:
    """Descriptor for a plotted dataset layer."""

    label: str
    x: Sequence[float]
    y: Sequence[float]
    color: str | None = None


class AutoHideDockWidget(QtWidgets.QDockWidget):
    """Dock widget that collapses to a thin strip until hovered."""

    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._auto_hide = False
        self._last_size = 240
        self._building_title = False
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.topLevelChanged.connect(self._sync_float_state)
        self._build_title_bar(title)

    def _build_title_bar(self, title: str) -> None:
        if self._building_title:
            return
        self._building_title = True
        bar = QtWidgets.QWidget(self)
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(4)
        label = QtWidgets.QLabel(title)
        label.setObjectName("dockTitleLabel")
        layout.addWidget(label)
        layout.addStretch(1)

        self.pin_button = QtWidgets.QToolButton(bar)
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(True)
        self.pin_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TitleBarNormalButton)
        )
        self.pin_button.setToolTip("Pin dock (disable auto-hide)")
        self.pin_button.toggled.connect(self._handle_pin_toggle)
        layout.addWidget(self.pin_button)

        self.float_button = QtWidgets.QToolButton(bar)
        self.float_button.setCheckable(True)
        self.float_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TitleBarMaxButton)
        )
        self.float_button.setToolTip("Toggle floating window (always on top)")
        self.float_button.toggled.connect(self._handle_float_toggle)
        layout.addWidget(self.float_button)

        self.setTitleBarWidget(bar)
        self._building_title = False

    def _handle_pin_toggle(self, checked: bool) -> None:
        self.set_auto_hide(not checked)

    def _handle_float_toggle(self, checked: bool) -> None:
        self.setFloating(checked)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()

    def _sync_float_state(self, floating: bool) -> None:
        if not hasattr(self, "float_button"):
            return
        self.float_button.blockSignals(True)
        self.float_button.setChecked(floating)
        self.float_button.blockSignals(False)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, floating)

    def set_auto_hide(self, enabled: bool) -> None:
        self._auto_hide = bool(enabled)
        if self._auto_hide:
            self._collapse()
        else:
            self._expand()

    def enterEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().enterEvent(event)
        if self._auto_hide and not self.isFloating():
            self._expand()

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        if self._auto_hide and not self.isFloating():
            self._collapse()

    def _collapse(self) -> None:
        widget = self.widget()
        if widget is None:
            return
        self._last_size = max(self.width(), self._last_size)
        widget.setVisible(False)
        self.setMinimumWidth(32)
        self.setMaximumWidth(32)

    def _expand(self) -> None:
        widget = self.widget()
        if widget is None:
            return
        widget.setVisible(True)
        self.setMinimumWidth(180)
        self.setMaximumWidth(16777215)
        self.resize(self._last_size, self.height())


class WorksheetModel(QtCore.QAbstractTableModel):
    """Editable model backed by a :class:`pandas.DataFrame`."""

    def __init__(self, dataframe: pd.DataFrame | None = None, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        if dataframe is None:
            dataframe = pd.DataFrame()
        self._df = dataframe.reset_index(drop=True)

    # Convenience ---------------------------------------------------------
    @property
    def dataframe(self) -> pd.DataFrame:
        return self._df

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._df.index)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._df.columns)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole) -> Any:  # type: ignore[override]
        if not index.isValid():
            return None
        if role in (QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.EditRole):
            value = self._df.iloc[index.row(), index.column()]
            if pd.isna(value):
                return ""
            return str(value)
        return None

    def setData(
        self,
        index: QtCore.QModelIndex,
        value: Any,
        role: int = QtCore.Qt.ItemDataRole.EditRole,
    ) -> bool:  # type: ignore[override]
        if not index.isValid() or role != QtCore.Qt.ItemDataRole.EditRole:
            return False
        text = str(value)
        try:
            if text == "":
                coerced: Any = pd.NA
            else:
                coerced = float(text)
                if math.isfinite(coerced) and text.strip().isdigit():
                    coerced = int(coerced)
        except Exception:
            coerced = text
        self._df.iloc[index.row(), index.column()] = coerced
        self.dataChanged.emit(index, index, [role])
        return True

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> Any:  # type: ignore[override]
        if role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == QtCore.Qt.Orientation.Horizontal:
            try:
                return str(self._df.columns[section])
            except Exception:
                return str(section + 1)
        return str(section + 1)

    def flags(self, index: QtCore.QModelIndex) -> QtCore.Qt.ItemFlag:  # type: ignore[override]
        base = super().flags(index)
        if index.isValid():
            base |= QtCore.Qt.ItemFlag.ItemIsEditable
        return base

    # Structure changes ---------------------------------------------------
    def insertRows(
        self,
        row: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if parent.isValid() or count <= 0:
            return False
        row = max(0, min(row, len(self._df)))
        self.beginInsertRows(QtCore.QModelIndex(), row, row + count - 1)
        placeholder = pd.DataFrame(
            [[pd.NA] * len(self._df.columns) for _ in range(count)],
            columns=self._df.columns,
        )
        top = self._df.iloc[:row]
        bottom = self._df.iloc[row:]
        self._df = pd.concat([top, placeholder, bottom], ignore_index=True)
        self.endInsertRows()
        return True

    def removeRows(
        self,
        row: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if parent.isValid() or count <= 0:
            return False
        if row < 0 or row >= len(self._df):
            return False
        last = min(row + count - 1, len(self._df) - 1)
        self.beginRemoveRows(QtCore.QModelIndex(), row, last)
        self._df.drop(self._df.index[row : last + 1], inplace=True)
        self._df.reset_index(drop=True, inplace=True)
        self.endRemoveRows()
        return True

    def insertColumns(
        self,
        column: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if parent.isValid() or count <= 0:
            return False
        column = max(0, min(column, len(self._df.columns)))
        self.beginInsertColumns(QtCore.QModelIndex(), column, column + count - 1)
        for offset in range(count):
            name = self._generate_column_name(column + offset)
            self._df.insert(column + offset, name, pd.NA)
        self.endInsertColumns()
        return True

    def removeColumns(
        self,
        column: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if parent.isValid() or count <= 0:
            return False
        if column < 0 or column >= len(self._df.columns):
            return False
        last = min(column + count, len(self._df.columns))
        self.beginRemoveColumns(QtCore.QModelIndex(), column, last - 1)
        drop_cols = list(self._df.columns[column:last])
        self._df.drop(columns=drop_cols, inplace=True)
        self.endRemoveColumns()
        return True

    def rename_column(self, index: int, name: str) -> None:
        if 0 <= index < len(self._df.columns):
            columns = list(self._df.columns)
            columns[index] = name
            self._df.columns = columns
            top = self.createIndex(0, index)
            bottom = self.createIndex(max(0, self.rowCount() - 1), index)
            self.headerDataChanged.emit(QtCore.Qt.Orientation.Horizontal, index, index)
            self.dataChanged.emit(top, bottom, [QtCore.Qt.ItemDataRole.DisplayRole])

    def _generate_column_name(self, position: int) -> str:
        base = position + 1
        name = f"Col{base:02d}"
        existing = set(str(c) for c in self._df.columns)
        while name in existing:
            base += 1
            name = f"Col{base:02d}"
        return name


class WorkbookWidget(QtWidgets.QWidget):
    """Widget wrapping a worksheet model in a :class:`QTableView`."""

    selection_changed = QtCore.pyqtSignal()
    data_changed = QtCore.pyqtSignal()

    def __init__(self, name: str, dataframe: pd.DataFrame | None = None, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self.model = WorksheetModel(dataframe, self)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.toolbar = QtWidgets.QToolBar()
        layout.addWidget(self.toolbar)

        add_row_action = self.toolbar.addAction("+ Row")
        del_row_action = self.toolbar.addAction("− Row")
        add_col_action = self.toolbar.addAction("+ Col")
        del_col_action = self.toolbar.addAction("− Col")

        add_row_action.triggered.connect(self._append_row)
        del_row_action.triggered.connect(self._remove_selected_rows)
        add_col_action.triggered.connect(self._append_column)
        del_col_action.triggered.connect(self._remove_selected_columns)

        self.view = QtWidgets.QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectItems)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.setAlternatingRowColors(True)
        layout.addWidget(self.view, 1)

        self.model.dataChanged.connect(self.data_changed)
        self.model.rowsInserted.connect(lambda *_: self.data_changed.emit())
        self.model.rowsRemoved.connect(lambda *_: self.data_changed.emit())
        self.model.columnsInserted.connect(lambda *_: self.data_changed.emit())
        self.model.columnsRemoved.connect(lambda *_: self.data_changed.emit())
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit())

    def name(self) -> str:
        return self._name

    def set_name(self, new_name: str) -> None:
        self._name = new_name

    def dataframe(self) -> pd.DataFrame:
        return self.model.dataframe

    # Helpers -------------------------------------------------------------
    def selected_columns(self) -> list[str]:
        selection = self.view.selectionModel()
        if selection is None:
            return []
        columns = sorted(set(index.column() for index in selection.selectedColumns()))
        return [str(self.model.dataframe.columns[c]) for c in columns if 0 <= c < self.model.columnCount()]

    def selected_rows(self) -> list[int]:
        selection = self.view.selectionModel()
        if selection is None:
            return []
        rows = sorted(set(index.row() for index in selection.selectedRows()))
        return [r for r in rows if 0 <= r < self.model.rowCount()]

    def _append_row(self) -> None:
        self.model.insertRows(self.model.rowCount(), 1)

    def _remove_selected_rows(self) -> None:
        rows = self.selected_rows()
        for offset, row in enumerate(rows):
            self.model.removeRows(row - offset, 1)

    def _append_column(self) -> None:
        self.model.insertColumns(self.model.columnCount(), 1)

    def _remove_selected_columns(self) -> None:
        columns = self.view.selectionModel().selectedColumns() if self.view.selectionModel() else []
        indices = sorted({index.column() for index in columns})
        for offset, column in enumerate(indices):
            self.model.removeColumns(column - offset, 1)


class GraphWidget(QtWidgets.QWidget):
    """Matplotlib-backed graph window with a toolbar."""

    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(6, 4), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)

        self.layers: list[GraphLayer] = []
        self.title = title

    def plot_layers(self, layers: Sequence[GraphLayer], x_label: str | None = None, y_label: str | None = None) -> None:
        self.layers = list(layers)
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        for layer in self.layers:
            ax.plot(layer.x, layer.y, label=layer.label, color=layer.color)
        ax.set_title(self.title)
        if x_label:
            ax.set_xlabel(x_label)
        if y_label:
            ax.set_ylabel(y_label)
        if self.layers:
            ax.legend()
        self.canvas.draw_idle()


class PythonConsoleWidget(QtWidgets.QWidget):
    """Tiny Python REPL dock for experimentation."""

    executed = QtCore.pyqtSignal(str, object)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.output = QtWidgets.QPlainTextEdit(readOnly=True)
        self.output.setPlaceholderText("Console output")
        layout.addWidget(self.output, 1)

        self.input = QtWidgets.QLineEdit()
        self.input.setPlaceholderText("Enter Python and press Return")
        layout.addWidget(self.input)

        self._locals: dict[str, Any] = {}
        self._history: list[str] = []
        self._history_index = -1

        self.input.returnPressed.connect(self._evaluate_current)
        self.input.installEventFilter(self)

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self.input and event.type() == QtCore.QEvent.Type.KeyPress:
            key_event = cast(QtGui.QKeyEvent, event)
            if key_event.key() in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down):
                self._navigate_history(key_event.key() == QtCore.Qt.Key.Key_Up)
                return True
        return super().eventFilter(obj, event)

    def set_environment(self, mapping: dict[str, Any]) -> None:
        self._locals.update(mapping)

    def _navigate_history(self, up: bool) -> None:
        if not self._history:
            return
        if up:
            self._history_index = max(0, self._history_index - 1)
        else:
            self._history_index = min(len(self._history) - 1, self._history_index + 1)
        self.input.setText(self._history[self._history_index])
        self.input.end(False)

    def _evaluate_current(self) -> None:
        code = self.input.text().strip()
        if not code:
            return
        self._history.append(code)
        self._history_index = len(self._history)
        self.input.clear()
        try:
            compiled = compile(code, "<console>", "eval")
            result = eval(compiled, {}, self._locals)
            if result is not None:
                self.output.appendPlainText(repr(result))
                self.executed.emit(code, result)
        except SyntaxError:
            try:
                compiled = compile(code, "<console>", "exec")
                exec(compiled, {}, self._locals)
                self.executed.emit(code, None)
            except Exception as exc:  # noqa: BLE001 - display the error
                self.output.appendPlainText(f"Error: {exc}")
        except Exception as exc:  # noqa: BLE001 - display the error
            self.output.appendPlainText(f"Error: {exc}")


class OriginCloneWindow(QtWidgets.QMainWindow):
    """Main window orchestrating the Origin-like workspace."""

    def __init__(self) -> None:
        super().__init__()
        ensure_app_theme(self)
        self.setWindowTitle("Origin Clone (Prototype)")
        self.resize(1400, 900)

        self.workspace = QtWidgets.QMdiArea()
        self.workspace.setViewMode(QtWidgets.QMdiArea.ViewMode.TabbedView)
        self.workspace.setTabsClosable(True)
        self.workspace.setTabsMovable(True)
        self.setCentralWidget(self.workspace)

        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setHeaderHidden(True)
        self.project_tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.project_tree.itemDoubleClicked.connect(self._open_project_item)
        self.project_tree.itemSelectionChanged.connect(self._sync_object_manager)

        self.message_log = QtWidgets.QPlainTextEdit(readOnly=True)
        self.message_log.setPlaceholderText("System and analysis messages appear here.")

        self.object_manager = QtWidgets.QTreeWidget()
        self.object_manager.setColumnCount(3)
        self.object_manager.setHeaderLabels(["Object", "Type", "Details"])

        self.console = PythonConsoleWidget()
        self.console.set_environment({"app": self, "pd": pd})
        self.console.executed.connect(lambda code, result: self.log_message(f"Console ▶ {code}"))

        self.project_dock = AutoHideDockWidget("Project Explorer", self)
        self.project_dock.setWidget(self.project_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)

        self.message_dock = AutoHideDockWidget("Message Log", self)
        self.message_dock.setWidget(self.message_log)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, self.message_dock)
        self.tabifyDockWidget(self.project_dock, self.message_dock)
        self.project_dock.raise_()

        self.object_dock = AutoHideDockWidget("Object Manager", self)
        self.object_dock.setWidget(self.object_manager)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self.object_dock)

        self.console_dock = AutoHideDockWidget("Python Console", self)
        self.console_dock.setWidget(self.console)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.console_dock)

        menu_bar = install_standard_menu(self, help_topic="origin_clone", close_window=self._close_window)
        self._build_menus(menu_bar)

        self.workspace.subWindowActivated.connect(lambda _: self._sync_object_manager())

        self._roots = {
            "workbooks": QtWidgets.QTreeWidgetItem(["Workbooks"]),
            "graphs": QtWidgets.QTreeWidgetItem(["Graphs"]),
            "layouts": QtWidgets.QTreeWidgetItem(["Layouts"]),
        }
        for root in self._roots.values():
            self.project_tree.addTopLevelItem(root)
            self.project_tree.expandItem(root)

        self._workbook_counter = 1
        self._graph_counter = 1
        self._workbooks: dict[str, tuple[WorkbookWidget, QtWidgets.QMdiSubWindow, QtWidgets.QTreeWidgetItem]] = {}
        self._graphs: dict[str, tuple[GraphWidget, QtWidgets.QMdiSubWindow, QtWidgets.QTreeWidgetItem]] = {}
        self._mdi_lookup: dict[QtWidgets.QMdiSubWindow, tuple[str, str]] = {}

        self.log_message("Origin Clone ready. Use File → New Workbook or Import Data to begin.")

    # Menu construction ---------------------------------------------------
    def _build_menus(self, menu_bar: QtWidgets.QMenuBar) -> None:
        file_menu = menu_bar.addMenu("&File")
        new_workbook_action = file_menu.addAction("New Workbook")
        new_workbook_action.triggered.connect(self.create_empty_workbook)
        import_action = file_menu.addAction("Import Data…")
        import_action.triggered.connect(self.import_data_dialog)
        export_action = file_menu.addAction("Export Active Worksheet…")
        export_action.triggered.connect(self.export_active_workbook)
        file_menu.addSeparator()
        file_menu.addAction("Close", self._close_window)

        worksheet_menu = menu_bar.addMenu("&Worksheet")
        rename_action = worksheet_menu.addAction("Rename Worksheet…")
        rename_action.triggered.connect(self.rename_active_workbook)
        worksheet_menu.addAction("Add Column", lambda: self._mutate_active_workbook("add_column"))
        worksheet_menu.addAction("Remove Selected Columns", lambda: self._mutate_active_workbook("remove_columns"))
        worksheet_menu.addAction("Append Row", lambda: self._mutate_active_workbook("add_row"))
        worksheet_menu.addAction("Remove Selected Rows", lambda: self._mutate_active_workbook("remove_rows"))

        plot_menu = menu_bar.addMenu("&Plot")
        plot_menu.addAction("Quick Line Plot", self.plot_selected_columns)
        plot_menu.addAction("Plot All vs First Column", self.plot_all_vs_first)

        analysis_menu = menu_bar.addMenu("&Analysis")
        analysis_menu.addAction("Column Statistics", self.compute_active_statistics)

        window_menu = menu_bar.addMenu("&Window")
        window_menu.addAction("Cascade", self.workspace.cascadeSubWindows)
        window_menu.addAction("Tile", self.workspace.tileSubWindows)

        help_menu = menu_bar.addMenu("&Help")
        help_menu.addAction("About Origin Clone", self.show_about_dialog)

    # Menu actions --------------------------------------------------------
    def _close_window(self) -> None:
        self.close()

    def create_empty_workbook(self) -> None:
        name = f"Book{self._workbook_counter:02d}"
        self._workbook_counter += 1
        self._register_workbook(name, pd.DataFrame())

    def import_data_dialog(self) -> None:
        dialog = QtWidgets.QFileDialog(self, "Import data")
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFiles)
        dialog.setNameFilters([
            "Data files (*.csv *.tsv *.txt *.xlsx *.xlsm *.xls)",
            "CSV files (*.csv)",
            "Text files (*.txt *.tsv)",
            "Excel files (*.xlsx *.xlsm *.xls)",
            "All files (*)",
        ])
        if dialog.exec():
            for path in dialog.selectedFiles():
                self.import_data(Path(path))

    def import_data(self, path: Path) -> None:
        try:
            df = self._load_dataframe(path)
        except Exception as exc:  # noqa: BLE001 - display errors in the message log
            self.log_message(f"Failed to load {path}: {exc}")
            return
        name = path.stem
        self._register_workbook(name, df)
        self.log_message(f"Imported {path} into worksheet '{name}'.")

    def export_active_workbook(self) -> None:
        current = self._current_workbook()
        if current is None:
            self.log_message("No active worksheet to export.")
            return
        widget, *_ = current
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export worksheet", f"{widget.name()}.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            widget.dataframe().to_csv(path, index=False)
        except Exception as exc:  # noqa: BLE001
            self.log_message(f"Failed to export worksheet: {exc}")
            return
        self.log_message(f"Exported worksheet to {path}.")

    def rename_active_workbook(self) -> None:
        current = self._current_workbook()
        if current is None:
            self.log_message("Select a worksheet first.")
            return
        widget, sub, item = current
        new_name, ok = QtWidgets.QInputDialog.getText(self, "Rename worksheet", "New name", text=widget.name())
        if not ok or not new_name.strip():
            return
        widget.set_name(new_name.strip())
        sub.setWindowTitle(new_name.strip())
        item.setText(0, new_name.strip())
        self.log_message(f"Worksheet renamed to {new_name.strip()}.")

    def _mutate_active_workbook(self, action: str) -> None:
        current = self._current_workbook()
        if current is None:
            self.log_message("No worksheet selected.")
            return
        widget, _, _ = current
        if action == "add_column":
            widget._append_column()
        elif action == "remove_columns":
            widget._remove_selected_columns()
        elif action == "add_row":
            widget._append_row()
        elif action == "remove_rows":
            widget._remove_selected_rows()

    def plot_selected_columns(self) -> None:
        current = self._current_workbook()
        if current is None:
            self.log_message("Select a worksheet to plot from.")
            return
        widget, _, _ = current
        selected = widget.selected_columns()
        if len(selected) < 2:
            self.log_message("Select at least two columns (X then Y).")
            return
        x_col, *y_cols = selected
        self._create_graph(widget, x_col, y_cols)

    def plot_all_vs_first(self) -> None:
        current = self._current_workbook()
        if current is None:
            self.log_message("Select a worksheet to plot from.")
            return
        widget, _, _ = current
        df = widget.dataframe()
        if df.shape[1] < 2:
            self.log_message("Need at least two columns to plot.")
            return
        x_col = df.columns[0]
        y_cols = list(df.columns[1:])
        self._create_graph(widget, x_col, y_cols)

    def compute_active_statistics(self) -> None:
        current = self._current_workbook()
        if current is None:
            self.log_message("Select a worksheet to analyse.")
            return
        widget, _, _ = current
        df = widget.dataframe()
        if df.empty:
            self.log_message("Worksheet is empty; add data before analysis.")
            return
        summary = df.describe(include="all").transpose()
        message_lines = ["Column statistics:"]
        for column, row in summary.iterrows():
            stats = []
            for field in ["count", "mean", "std", "min", "max"]:
                if field in row and pd.notna(row[field]):
                    stats.append(f"{field}={row[field]:g}" if isinstance(row[field], (int, float)) else f"{field}={row[field]}")
            message_lines.append(f"- {column}: {', '.join(stats)}")
        self.log_message("\n".join(message_lines))

    def show_about_dialog(self) -> None:
        QtWidgets.QMessageBox.information(
            self,
            "About Origin Clone",
            (
                "This experimental workspace prototypes an Origin-like UI built "
                "entirely in Python using PyQt6."
            ),
        )

    # Registration -------------------------------------------------------
    def _register_workbook(self, name: str, dataframe: pd.DataFrame) -> None:
        widget = WorkbookWidget(name, dataframe, self)
        sub = QtWidgets.QMdiSubWindow()
        sub.setWidget(widget)
        sub.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        sub.setWindowTitle(name)
        self.workspace.addSubWindow(sub)
        sub.show()

        item = QtWidgets.QTreeWidgetItem([name])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, ("workbook", name))
        self._roots["workbooks"].addChild(item)
        self.project_tree.expandItem(self._roots["workbooks"])

        widget.data_changed.connect(self._sync_object_manager)
        widget.selection_changed.connect(self._sync_object_manager)
        sub.destroyed.connect(lambda *_: self._unregister_workbook(name))

        self._workbooks[name] = (widget, sub, item)
        self._mdi_lookup[sub] = ("workbook", name)
        self.workspace.setActiveSubWindow(sub)

    def _unregister_workbook(self, name: str) -> None:
        entry = self._workbooks.pop(name, None)
        if not entry:
            return
        _, sub, item = entry
        if item.parent():
            item.parent().removeChild(item)
        self._mdi_lookup.pop(sub, None)
        self._sync_object_manager()

    def _register_graph(self, name: str, widget: GraphWidget) -> None:
        sub = QtWidgets.QMdiSubWindow()
        sub.setWidget(widget)
        sub.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        sub.setWindowTitle(name)
        self.workspace.addSubWindow(sub)
        sub.show()

        item = QtWidgets.QTreeWidgetItem([name])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, ("graph", name))
        self._roots["graphs"].addChild(item)
        self.project_tree.expandItem(self._roots["graphs"])

        sub.destroyed.connect(lambda *_: self._unregister_graph(name))

        self._graphs[name] = (widget, sub, item)
        self._mdi_lookup[sub] = ("graph", name)
        self.workspace.setActiveSubWindow(sub)
        self._sync_object_manager()

    def _unregister_graph(self, name: str) -> None:
        entry = self._graphs.pop(name, None)
        if not entry:
            return
        _, sub, item = entry
        if item.parent():
            item.parent().removeChild(item)
        self._mdi_lookup.pop(sub, None)
        self._sync_object_manager()

    # Helpers -------------------------------------------------------------
    def _load_dataframe(self, path: Path) -> pd.DataFrame:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        if suffix in {".txt", ".tsv"}:
            return pd.read_csv(path, sep=None, engine="python")
        if suffix in {".xls", ".xlsx", ".xlsm"}:
            return pd.read_excel(path)
        # Fallback to pandas auto-detection
        return pd.read_table(path)

    def _current_workbook(self) -> tuple[WorkbookWidget, QtWidgets.QMdiSubWindow, QtWidgets.QTreeWidgetItem] | None:
        sub = self.workspace.activeSubWindow()
        if sub is None:
            return None
        meta = self._mdi_lookup.get(sub)
        if meta is None or meta[0] != "workbook":
            return None
        return self._workbooks.get(meta[1])

    def _current_graph(self) -> tuple[GraphWidget, QtWidgets.QMdiSubWindow, QtWidgets.QTreeWidgetItem] | None:
        sub = self.workspace.activeSubWindow()
        if sub is None:
            return None
        meta = self._mdi_lookup.get(sub)
        if meta is None or meta[0] != "graph":
            return None
        return self._graphs.get(meta[1])

    def _create_graph(self, workbook: WorkbookWidget, x_col: str, y_cols: Sequence[str]) -> None:
        df = workbook.dataframe()
        layers: list[GraphLayer] = []
        x_series = pd.to_numeric(df[x_col], errors="coerce").dropna().astype(float)
        for column in y_cols:
            y_series = pd.to_numeric(df[column], errors="coerce").dropna().astype(float)
            common = pd.concat([x_series, y_series], axis=1).dropna()
            if common.empty:
                continue
            layers.append(GraphLayer(label=column, x=common.iloc[:, 0].values, y=common.iloc[:, 1].values))
        if not layers:
            self.log_message("No numeric data to plot after cleaning.")
            return
        name = f"Graph{self._graph_counter:02d}"
        self._graph_counter += 1
        graph = GraphWidget(name, self)
        graph.plot_layers(layers, x_label=str(x_col), y_label=", ".join(str(c) for c in y_cols))
        self._register_graph(name, graph)
        self.log_message(f"Created {name} from columns {y_cols} vs {x_col}.")

    def _open_project_item(self, item: QtWidgets.QTreeWidgetItem) -> None:
        meta = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not meta:
            return
        kind, name = meta
        if kind == "workbook":
            entry = self._workbooks.get(name)
            if entry is None:
                return
            _, sub, _ = entry
            if sub is not None:
                self.workspace.setActiveSubWindow(sub)
        elif kind == "graph":
            entry = self._graphs.get(name)
            if entry is None:
                return
            _, sub, _ = entry
            if sub is not None:
                self.workspace.setActiveSubWindow(sub)

    def _sync_object_manager(self) -> None:
        self.object_manager.clear()
        current = self._current_workbook()
        if current is not None:
            widget, sub, _ = current
            df = widget.dataframe()
            for column in df.columns:
                dtype = df[column].dtype
                item = QtWidgets.QTreeWidgetItem([str(column), "Column", str(dtype)])
                self.object_manager.addTopLevelItem(item)
                item.setExpanded(True)
            return
        current_graph = self._current_graph()
        if current_graph is not None:
            widget, _, _ = current_graph
            for layer in widget.layers:
                item = QtWidgets.QTreeWidgetItem([layer.label, "Layer", f"{len(layer.x)} points"])
                self.object_manager.addTopLevelItem(item)

    def log_message(self, text: str) -> None:
        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        self.message_log.appendPlainText(f"[{stamp}] {text}")

    # Public API ----------------------------------------------------------
    def list_workbooks(self) -> list[str]:
        return list(self._workbooks.keys())

    def list_graphs(self) -> list[str]:
        return list(self._graphs.keys())


def main() -> QtWidgets.QWidget:
    """Launch the Origin clone window, returning the created widget."""

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    window = OriginCloneWindow()
    window.show()
    return window


__all__ = [
    "AutoHideDockWidget",
    "GraphLayer",
    "GraphWidget",
    "OriginCloneWindow",
    "PythonConsoleWidget",
    "WorkbookWidget",
    "WorksheetModel",
    "main",
]
