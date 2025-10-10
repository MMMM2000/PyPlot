"""Origin-style workbench for exploring VSM hysteresis loop data."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.vsm_hysteresis_loops import (
    VSMMeasurement,
    _derive_metadata_from_dataframe,
    _find_vsm_files,
    _parse_angle,
    _parse_temperature,
    _read_vsm_file,
)
from plotting.utils import ensure_app_theme, install_standard_menu


class AutoHideDockWidget(QtWidgets.QDockWidget):
    """Dock widget that can collapse when auto-hide mode is active."""

    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._auto_hide = False
        self._last_width = 260
        self._building_title = False
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.topLevelChanged.connect(self._sync_float_button)
        self._build_title_bar(title)

    def _build_title_bar(self, title: str) -> None:
        if self._building_title:
            return
        self._building_title = True
        bar = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(4)
        label = QtWidgets.QLabel(title)
        label.setObjectName("dockTitleLabel")
        layout.addWidget(label)
        layout.addStretch(1)

        self.pin_button = QtWidgets.QToolButton()
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(True)
        self.pin_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TitleBarNormalButton)
        )
        self.pin_button.setToolTip("Pin dock (disable auto-hide)")
        self.pin_button.toggled.connect(self._handle_pin_toggle)
        layout.addWidget(self.pin_button)

        self.float_button = QtWidgets.QToolButton()
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
        if checked:
            self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
            self.show()
        else:
            self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, False)
            self.show()

    def _sync_float_button(self, floating: bool) -> None:
        if not hasattr(self, "float_button"):
            return
        self.float_button.blockSignals(True)
        self.float_button.setChecked(floating)
        self.float_button.blockSignals(False)
        if floating:
            self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
        else:
            self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, False)

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
        self._last_width = max(self.width(), self._last_width)
        widget.setVisible(False)
        self.setMinimumWidth(28)
        self.setMaximumWidth(28)

    def _expand(self) -> None:
        widget = self.widget()
        if widget is None:
            return
        widget.setVisible(True)
        self.setMinimumWidth(160)
        self.setMaximumWidth(16777215)
        self.resize(self._last_width, self.height())


class WorksheetModel(QtCore.QAbstractTableModel):
    """Editable view of a measurement dataframe."""

    def __init__(self, measurement: VSMMeasurement, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._measurement = measurement
        self._columns = list(measurement.data.columns)

    @property
    def dataframe(self) -> pd.DataFrame:
        return self._measurement.data

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self.dataframe)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        column = self._columns[index.column()]
        value = self.dataframe.iloc[index.row(), self.dataframe.columns.get_loc(column)]
        if role in {QtCore.Qt.ItemDataRole.DisplayRole, QtCore.Qt.ItemDataRole.EditRole}:
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
        if role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == QtCore.Qt.Orientation.Horizontal:
            if 0 <= section < len(self._columns):
                return str(self._columns[section])
        else:
            return str(section + 1)
        return None

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
        column = self._columns[index.column()]
        df = self.dataframe
        column_index = df.columns.get_loc(column)
        series = df.iloc[:, column_index]
        text = str(value)
        if pd.api.types.is_numeric_dtype(series):
            try:
                parsed = float(text)
            except ValueError:
                return False
            df.iat[index.row(), column_index] = parsed
        else:
            df.iat[index.row(), column_index] = text
        self.dataChanged.emit(index, index, [role])
        return True

    def removeRows(
        self,
        row: int,
        count: int,
        parent: QtCore.QModelIndex = QtCore.QModelIndex(),
    ) -> bool:  # type: ignore[override]
        if row < 0 or count <= 0 or row + count > len(self.dataframe):
            return False
        self.beginRemoveRows(parent, row, row + count - 1)
        drop_index = self.dataframe.index[row : row + count]
        self.dataframe.drop(index=drop_index, inplace=True)
        self.dataframe.reset_index(drop=True, inplace=True)
        self.endRemoveRows()
        return True


class OriginLikeVSMWorkbench(QtWidgets.QMainWindow):
    """Experiment window that mimics Origin's project layout."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VSM Origin Workbench")
        self.resize(1600, 950)

        self.settings = QtCore.QSettings("MicrowireLab", "VSMOriginWorkbench")
        self.measurements: List[VSMMeasurement] = []
        self._worksheet_models: Dict[Path, WorksheetModel] = {}

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------ UI assembly
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        controls = QtWidgets.QWidget()
        controls_layout = QtWidgets.QGridLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setHorizontalSpacing(8)
        controls_layout.setVerticalSpacing(6)

        self.input_edit = QtWidgets.QLineEdit()
        self.input_edit.setPlaceholderText("Select VSM files or a folder…")
        self.browse_files_button = QtWidgets.QPushButton("Browse Files…")
        self.browse_files_button.clicked.connect(self._choose_files)
        self.browse_folder_button = QtWidgets.QPushButton("Browse Folder…")
        self.browse_folder_button.clicked.connect(self._choose_folder)

        controls_layout.addWidget(QtWidgets.QLabel("Data sources"), 0, 0)
        controls_layout.addWidget(self.input_edit, 0, 1, 1, 2)
        controls_layout.addWidget(self.browse_files_button, 0, 3)
        controls_layout.addWidget(self.browse_folder_button, 0, 4)

        self.x_axis_combo = QtWidgets.QComboBox()
        self.y_axis_combo = QtWidgets.QComboBox()
        self.x_axis_combo.currentTextChanged.connect(self._store_axis_preferences)
        self.y_axis_combo.currentTextChanged.connect(self._store_axis_preferences)

        controls_layout.addWidget(QtWidgets.QLabel("X axis"), 1, 0)
        controls_layout.addWidget(self.x_axis_combo, 1, 1, 1, 2)
        controls_layout.addWidget(QtWidgets.QLabel("Y axis"), 1, 3)
        controls_layout.addWidget(self.y_axis_combo, 1, 4)

        self.rescale_checkbox = QtWidgets.QCheckBox("Normalise loop endpoints")
        controls_layout.addWidget(self.rescale_checkbox, 2, 0, 1, 2)

        self.dark_theme_checkbox = QtWidgets.QCheckBox("Dark theme")
        self.dark_theme_checkbox.toggled.connect(self._apply_plot_theme)
        controls_layout.addWidget(self.dark_theme_checkbox, 2, 2)

        self.load_button = QtWidgets.QPushButton("Load data")
        self.load_button.clicked.connect(self._load_measurements)
        controls_layout.addWidget(self.load_button, 2, 3)

        self.plot_button = QtWidgets.QPushButton("Plot loops")
        self.plot_button.clicked.connect(self._plot_measurements)
        self.plot_button.setEnabled(False)
        controls_layout.addWidget(self.plot_button, 2, 4)

        layout.addWidget(controls)

        self.figure = Figure(figsize=(12.0, 8.0))
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        layout.addWidget(self.canvas, 1)

        self.setCentralWidget(central)

        # docks --------------------------------------------------------------
        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setHeaderLabels(["Project Explorer", "Details"])
        self.project_tree.header().setStretchLastSection(True)
        project_dock = AutoHideDockWidget("Project Explorer", self)
        project_dock.setWidget(self.project_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, project_dock)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_dock = AutoHideDockWidget("Message Log", self)
        log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, log_dock)
        self.tabifyDockWidget(project_dock, log_dock)
        project_dock.raise_()

        self.object_tree = QtWidgets.QTreeWidget()
        self.object_tree.setHeaderLabels(["Object Manager"])
        self.object_tree.itemChanged.connect(self._handle_object_toggled)
        object_dock = AutoHideDockWidget("Object Manager", self)
        object_dock.setWidget(self.object_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, object_dock)

        self.worksheet_tabs = QtWidgets.QTabWidget()
        self.worksheet_tabs.setTabsClosable(False)
        worksheet_dock = AutoHideDockWidget("Worksheets", self)
        worksheet_dock.setWidget(self.worksheet_tabs)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, worksheet_dock)

        install_standard_menu(
            self,
            help_topic="vsm_origin_workbench",
            open_file=self._choose_files,
            open_folder=self._choose_folder,
            close_window=self.close,
        )

    # ------------------------------------------------------------------ settings
    def _load_settings(self) -> None:
        value = self.settings.value("sources", "")
        if isinstance(value, str):
            self.input_edit.setText(value)
        x_axis = self.settings.value("x_axis", "Applied Field [Oe]")
        y_axis = self.settings.value("y_axis", "Signal X direction [emu]")
        if isinstance(x_axis, str):
            self.x_axis_combo.setCurrentText(x_axis)
        if isinstance(y_axis, str):
            self.y_axis_combo.setCurrentText(y_axis)
        rescale = self.settings.value("rescale", False)
        if isinstance(rescale, bool):
            self.rescale_checkbox.setChecked(rescale)
        dark = self.settings.value("dark_theme", False)
        if isinstance(dark, bool):
            self.dark_theme_checkbox.setChecked(dark)

    def _store_axis_preferences(self) -> None:
        self.settings.setValue("x_axis", self.x_axis_combo.currentText())
        self.settings.setValue("y_axis", self.y_axis_combo.currentText())

    def _save_settings(self) -> None:
        self.settings.setValue("sources", self.input_edit.text())
        self.settings.setValue("rescale", self.rescale_checkbox.isChecked())
        self.settings.setValue("dark_theme", self.dark_theme_checkbox.isChecked())
        self._store_axis_preferences()
        self.settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._save_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------------ helpers
    def _choose_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select VSM files",
            self.input_edit.text() or str(Path.home()),
            "VSM data (*.VSM-Hys-Data);;All files (*)",
        )
        if files:
            self.input_edit.setText(";".join(files))

    def _choose_folder(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select folder with VSM files",
            self.input_edit.text() or str(Path.home()),
        )
        if directory:
            paths = _find_vsm_files(Path(directory))
            self.input_edit.setText(";".join(str(path) for path in paths))

    def _selected_paths(self) -> List[Path]:
        raw = self.input_edit.text().strip()
        if not raw:
            return []
        return [Path(part) for part in raw.split(";") if part]

    def _load_measurements(self) -> None:
        self.measurements.clear()
        self._worksheet_models.clear()
        self.object_tree.clear()
        self.worksheet_tabs.clear()
        self.project_tree.clear()
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.canvas.draw_idle()

        paths = self._selected_paths()
        if not paths:
            QtWidgets.QMessageBox.warning(self, "VSM Origin Workbench", "Select VSM files first.")
            return

        for path in paths:
            if not path.exists():
                self._log(f"Skipping missing path: {path}")
                continue
            try:
                df = _read_vsm_file(path)
            except Exception as exc:
                self._log(f"Failed to parse {path.name}: {exc}")
                continue
            temperature = _parse_temperature(path)
            angle = _parse_angle(path)
            derived_angle, derived_temp = _derive_metadata_from_dataframe(df)
            if angle is None:
                angle = derived_angle
            if temperature is None:
                temperature = derived_temp
            measurement = VSMMeasurement(path=path, temperature=temperature, angle=angle, data=df)
            self.measurements.append(measurement)

        if not self.measurements:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Origin Workbench",
                "No valid VSM measurements were loaded.",
            )
            return

        self.measurements.sort(
            key=lambda m: (
                float("inf") if m.temperature is None else m.temperature,
                float("inf") if m.angle is None else m.angle,
            )
        )

        self._populate_axis_options()
        self._populate_project_tree()
        self._populate_worksheets()
        self.plot_button.setEnabled(True)
        self._log(f"Loaded {len(self.measurements)} VSM table(s).")

    def _populate_axis_options(self) -> None:
        if not self.measurements:
            return
        numeric_columns: Dict[str, int] | None = None
        for measurement in self.measurements:
            numeric = {
                column: idx
                for idx, column in enumerate(measurement.data.columns)
                if pd.api.types.is_numeric_dtype(measurement.data[column])
            }
            if numeric_columns is None:
                numeric_columns = numeric
            else:
                numeric_columns = {
                    name: numeric_columns[name]
                    for name in list(numeric_columns)
                    if name in numeric
                }
        labels = list(numeric_columns.keys()) if numeric_columns else list(self.measurements[0].data.columns)
        for combo in (self.x_axis_combo, self.y_axis_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(labels)
            combo.blockSignals(False)
        preferred_x = "Applied Field [Oe]"
        preferred_y = "Signal X direction [emu]"
        if preferred_x in labels:
            self.x_axis_combo.setCurrentText(preferred_x)
        elif labels:
            self.x_axis_combo.setCurrentIndex(0)
        if preferred_y in labels:
            self.y_axis_combo.setCurrentText(preferred_y)
        elif labels:
            self.y_axis_combo.setCurrentIndex(min(1, len(labels) - 1))

    def _populate_project_tree(self) -> None:
        self.project_tree.clear()
        groups: Dict[float | None, QtWidgets.QTreeWidgetItem] = {}
        for measurement in self.measurements:
            temp_key = measurement.temperature if measurement.temperature is not None else float("nan")
            if temp_key not in groups:
                label = "Unknown temperature" if measurement.temperature is None else f"{measurement.temperature:g} °C"
                item = QtWidgets.QTreeWidgetItem([label, ""])
                item.setExpanded(True)
                groups[temp_key] = item
                self.project_tree.addTopLevelItem(item)
            parent = groups[temp_key]
            angle_label = "Unknown angle" if measurement.angle is None else f"{measurement.angle:g}°"
            child = QtWidgets.QTreeWidgetItem([angle_label, measurement.path.name])
            parent.addChild(child)

    def _populate_worksheets(self) -> None:
        self.worksheet_tabs.clear()
        self._worksheet_models.clear()
        for measurement in self.measurements:
            model = WorksheetModel(measurement)
            self._worksheet_models[measurement.path] = model
            view = QtWidgets.QTableView()
            view.setModel(model)
            view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
            view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda pos, table=view: self._open_table_menu(table, pos)
            )
            tab_name = measurement.path.stem
            if measurement.temperature is not None and measurement.angle is not None:
                tab_name = f"{measurement.temperature:g}°C @ {measurement.angle:g}°"
            container = QtWidgets.QWidget()
            container_layout = QtWidgets.QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(view)
            self.worksheet_tabs.addTab(container, tab_name)

    def _open_table_menu(self, table: QtWidgets.QTableView, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(table)
        delete_action = menu.addAction("Delete selected rows")
        if delete_action is None:
            return
        delete_action.triggered.connect(lambda: self._delete_selected_rows(table))
        menu.exec(table.viewport().mapToGlobal(pos))

    def _delete_selected_rows(self, table: QtWidgets.QTableView) -> None:
        model = table.model()
        if not isinstance(model, WorksheetModel):
            return
        selection = table.selectionModel()
        if selection is None:
            return
        rows = sorted({index.row() for index in selection.selectedRows()}, reverse=True)
        if not rows:
            return
        for row in rows:
            model.removeRows(row, 1)
        self._log(f"Deleted {len(rows)} row(s) from {model._measurement.path.name}.")
        self._plot_measurements()

    def _handle_object_toggled(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        line = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if line is None:
            for idx in range(item.childCount()):
                child = item.child(idx)
                child.setCheckState(0, item.checkState(0))
            return
        visible = item.checkState(0) == QtCore.Qt.CheckState.Checked
        try:
            line.set_visible(visible)
        except Exception:
            pass
        self.canvas.draw_idle()

    def _apply_plot_theme(self) -> None:
        if not hasattr(self, "ax"):
            return
        if self.dark_theme_checkbox.isChecked():
            self.figure.patch.set_facecolor("#202124")
            self.ax.set_facecolor("#202124")
            for spine in self.ax.spines.values():
                spine.set_color("white")
            self.ax.tick_params(colors="white")
            self.ax.yaxis.label.set_color("white")
            self.ax.xaxis.label.set_color("white")
            self.ax.title.set_color("white")
        else:
            self.figure.patch.set_facecolor("white")
            self.ax.set_facecolor("white")
            for spine in self.ax.spines.values():
                spine.set_color("black")
            self.ax.tick_params(colors="black")
            self.ax.yaxis.label.set_color("black")
            self.ax.xaxis.label.set_color("black")
            self.ax.title.set_color("black")
        self.canvas.draw_idle()

    def _plot_measurements(self) -> None:
        if not self.measurements:
            QtWidgets.QMessageBox.information(self, "VSM Origin Workbench", "Load data first.")
            return

        x_axis = self.x_axis_combo.currentText()
        y_axis = self.y_axis_combo.currentText()
        if not x_axis or not y_axis:
            QtWidgets.QMessageBox.warning(
                self,
                "VSM Origin Workbench",
                "Select X and Y axes before plotting.",
            )
            return

        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        line_entries: List[tuple[VSMMeasurement, any]] = []
        colors = self.ax._get_lines.prop_cycler

        rescale = self.rescale_checkbox.isChecked()
        extremes: Dict[float, tuple[float, float]] = {}
        if rescale:
            for measurement in self.measurements:
                if x_axis in measurement.data.columns and y_axis in measurement.data.columns:
                    series = pd.to_numeric(measurement.data[y_axis], errors="coerce").dropna()
                    if series.empty:
                        continue
                    left = float(series.min())
                    right = float(series.max())
                    key = float(measurement.temperature or 0.0)
                    stored = extremes.get(key)
                    if stored is None:
                        extremes[key] = (left, right)
                    else:
                        extremes[key] = (min(stored[0], left), max(stored[1], right))

        for measurement in self.measurements:
            if x_axis not in measurement.data.columns or y_axis not in measurement.data.columns:
                continue
            subset = (
                measurement.data[[x_axis, y_axis]]
                .apply(pd.to_numeric, errors="coerce")
                .dropna()
            )
            if subset.empty:
                continue
            series_y = subset[y_axis]
            if rescale and measurement.temperature is not None:
                bounds = extremes.get(float(measurement.temperature))
                if bounds is not None and bounds[1] - bounds[0]:
                    scale = (bounds[1] - bounds[0]) or 1.0
                    series_y = (series_y - series_y.min()) / scale * (bounds[1] - bounds[0]) + bounds[0]
            label_parts: List[str] = []
            if measurement.temperature is not None:
                label_parts.append(f"{measurement.temperature:g} °C")
            if measurement.angle is not None:
                label_parts.append(f"{measurement.angle:g}°")
            label = " @ ".join(label_parts) if label_parts else measurement.path.name
            props = next(colors)
            line = self.ax.plot(subset[x_axis], series_y, label=label, **props)[0]
            line_entries.append((measurement, line))

        self.ax.set_xlabel(x_axis)
        self.ax.set_ylabel(y_axis)
        self.ax.set_title(f"{y_axis} vs {x_axis}")
        self.ax.legend(loc="best")
        self._apply_plot_theme()
        self._populate_object_manager(line_entries)
        self.canvas.draw_idle()
        self._log(f"Plotted {len(line_entries)} measurement(s).")

    def _populate_object_manager(self, entries: Iterable[tuple[VSMMeasurement, any]]) -> None:
        self.object_tree.blockSignals(True)
        self.object_tree.clear()
        temperature_groups: Dict[float | None, QtWidgets.QTreeWidgetItem] = {}
        for measurement, line in entries:
            key = measurement.temperature
            if key not in temperature_groups:
                label = "Unknown temperature" if key is None else f"{key:g} °C"
                temp_item = QtWidgets.QTreeWidgetItem([label])
                temp_item.setCheckState(0, QtCore.Qt.CheckState.Checked)
                temperature_groups[key] = temp_item
                self.object_tree.addTopLevelItem(temp_item)
            else:
                temp_item = temperature_groups[key]
            angle_label = "Unknown angle" if measurement.angle is None else f"{measurement.angle:g}°"
            item = QtWidgets.QTreeWidgetItem([angle_label])
            item.setCheckState(0, QtCore.Qt.CheckState.Checked)
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, line)
            temp_item.addChild(item)
        self.object_tree.expandAll()
        self.object_tree.blockSignals(False)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)


def main() -> QtWidgets.QWidget | None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        ensure_app_theme(app)
        window = OriginLikeVSMWorkbench()
        window.show()
        return window
    ensure_app_theme(app)
    window = OriginLikeVSMWorkbench()
    window.show()
    return window


__all__ = ["OriginLikeVSMWorkbench", "main"]
