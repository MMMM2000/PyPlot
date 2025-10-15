"""Visualise VSM hysteresis loops grouped by temperature and angle."""

from __future__ import annotations

import logging
import math
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Sequence, Tuple

import pandas as pd
import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.backends import wants_matplotlib, wants_origin
from plotting.utils import ensure_app_theme, install_standard_menu, origin_session, schedule_origin_release

HEADER_COLUMN_RE = re.compile(r"Column\s+\d+\s*:\s*(.+)")
WHITESPACE_RE = re.compile(r"[_\s]+")
ANGLE_RE = re.compile(r"a(-?(?:\d+(?:\.\d+)?)(?:-\d+)*)", re.IGNORECASE)
TEMP_RE = re.compile(r"T(-?(?:\d+(?:\.\d+)?)(?:-\d+)*)", re.IGNORECASE)
VSM_FILE_TOKEN_RE = re.compile(r"vsm-hys-data(?:$|[^0-9a-z])")
FIELD_ANGLE_RE = re.compile(r"Set Field Angle to\s+([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
ANGLE_OFFSET_RE = re.compile(r"Sample Angle Offset\s*=\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
SET_TEMPERATURE_RE = re.compile(r"Set Sample Temperature to\s+([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
FOLDER_SANITIZE_RE = re.compile(r"[^0-9A-Za-z._-]+")

TEMPERATURE_COLUMN_CANDIDATES = [
    "Sample Temperature [degC]",
    "Temperature [degC]",
    "Temperature 2 [degC]",
    "Raw Temperature [degC]",
]

ANGLE_COLUMN_CANDIDATES = [
    "Field Angle [deg]",
    "Signal Angle with field [deg]",
    "Signal Angle with sample [deg]",
]


def _clean_folder_name(name: str) -> str:
    """Sanitise folder names so they play nicely with the local filesystem."""

    candidate = name.strip().replace("/", "_").replace('\\', "_")
    candidate = FOLDER_SANITIZE_RE.sub("_", candidate)
    candidate = candidate.strip("._-")
    return candidate


@dataclass
class VSMMeasurement:
    path: Path
    temperature: float | None
    angle: float | None
    data: pd.DataFrame


class AutoHideDockWidget(QtWidgets.QDockWidget):
    """Dock widget that mimics Origin's hover-to-expand panels."""

    def __init__(
        self,
        title: str,
        parent: QtWidgets.QWidget | None = None,
        *,
        object_name: str | None = None,
    ) -> None:
        super().__init__(title, parent)
        self._auto_hide = False
        self._last_width = 260
        self._rebuilding = False
        if object_name:
            self.setObjectName(object_name)
        else:
            safe = re.sub(r"[^0-9A-Za-z]+", "", title.title())
            if safe:
                self.setObjectName(f"{safe}Dock")
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.topLevelChanged.connect(self._sync_float_button)
        self._build_title_bar(title)

    def _build_title_bar(self, title: str) -> None:
        if self._rebuilding:
            return
        self._rebuilding = True
        bar = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(4)
        self._title_label = QtWidgets.QLabel(title)
        self._title_label.setObjectName("dockTitleLabel")
        layout.addWidget(self._title_label)
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
        self._rebuilding = False
        self._alert_active = False

    def _handle_pin_toggle(self, checked: bool) -> None:
        self.set_auto_hide(not checked)

    def _handle_float_toggle(self, checked: bool) -> None:
        self.setFloating(checked)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()

    def _sync_float_button(self, floating: bool) -> None:
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

    def set_alert(self, enabled: bool) -> None:
        """Toggle a visual alert state for the dock title."""

        if not hasattr(self, "_title_label"):
            return
        if getattr(self, "_alert_active", False) == enabled:
            return
        self._alert_active = enabled
        if enabled:
            self._title_label.setStyleSheet("color: #b3261e; font-weight: 600;")
        else:
            self._title_label.setStyleSheet("")


class WorksheetModel(QtCore.QAbstractTableModel):
    """Editable model exposing a measurement dataframe."""

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

    def data(
        self,
        index: QtCore.QModelIndex,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> Any:  # type: ignore[override]
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


class _MetricDebugTab(QtWidgets.QWidget):
    """Container widget showing raw and symmetrised metric data for one temperature."""

    def __init__(
        self,
        metric_label: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._metric_label = metric_label
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        self.table = QtWidgets.QTableWidget()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        figure = Figure(figsize=(5, 3))
        self.canvas = FigureCanvas(figure)
        self.axes = figure.add_subplot(111)
        layout.addWidget(self.canvas, 1)

    @staticmethod
    def _format_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            if not math.isfinite(value):
                return ""
            return f"{value:.6g}"
        return str(value)

    def update_frame(
        self,
        temperature: float,
        dataframe: pd.DataFrame,
        columns: Dict[str, str],
    ) -> None:
        headers = list(dataframe.columns)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(dataframe))

        for row_index, (_, row) in enumerate(dataframe.iterrows()):
            for column_index, header in enumerate(headers):
                value = row.get(header)
                item = QtWidgets.QTableWidgetItem(self._format_value(value))
                item.setTextAlignment(
                    QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
                )
                self.table.setItem(row_index, column_index, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

        self.axes.clear()
        angle_label = columns.get("angle")
        original_label = columns.get("original")
        corrected_label = columns.get("corrected")
        if (
            angle_label
            and original_label
            and corrected_label
            and not dataframe.empty
            and angle_label in dataframe
        ):
            angles = dataframe[angle_label].to_numpy(dtype=float)
            original = dataframe[original_label].to_numpy(dtype=float)
            corrected = dataframe[corrected_label].to_numpy(dtype=float)

            if np.any(np.isfinite(original)):
                self.axes.plot(
                    angles,
                    original,
                    marker="o",
                    linestyle="-",
                    label="Original",
                )
            if np.any(np.isfinite(corrected)):
                self.axes.plot(
                    angles,
                    corrected,
                    marker="s",
                    linestyle="-",
                    label="Corrected",
                )

            if np.any(np.isfinite(original)) or np.any(np.isfinite(corrected)):
                self.axes.set_xlabel(angle_label)
                self.axes.set_ylabel(corrected_label)
                self.axes.legend()
                self.axes.grid(True, alpha=0.3)
                self.axes.set_title(f"{self._metric_label} vs angle @ {temperature:g} °C")
        try:
            self.canvas.figure.tight_layout()
        except Exception:
            pass
        self.canvas.draw_idle()


class MetricDebugWindow(QtWidgets.QDialog):
    """Floating inspector that exposes raw crossing pairs for a derived metric."""

    def __init__(
        self,
        metric_label: str,
        window_title: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._metric_label = metric_label
        self.setWindowTitle(window_title)
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._empty_label = QtWidgets.QLabel(
            "Generate plots to inspect raw metric crossings."
        )
        self._empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_label)

        self.tab_widget = QtWidgets.QTabWidget()
        layout.addWidget(self.tab_widget, 1)

        self._tabs: Dict[float, _MetricDebugTab] = {}

        self.resize(820, 620)

    def update_data(
        self,
        tables: Dict[float, pd.DataFrame],
        columns: Dict[str, str],
    ) -> None:
        if not tables:
            self.tab_widget.hide()
            self._empty_label.show()
            for temperature, tab in list(self._tabs.items()):
                index = self.tab_widget.indexOf(tab)
                if index >= 0:
                    self.tab_widget.removeTab(index)
                self._tabs.pop(temperature, None)
            return

        self._empty_label.hide()
        self.tab_widget.show()

        target_temps = sorted(tables.keys())
        for temperature, tab in list(self._tabs.items()):
            if temperature not in tables:
                index = self.tab_widget.indexOf(tab)
                if index >= 0:
                    self.tab_widget.removeTab(index)
                self._tabs.pop(temperature, None)

        for temperature in target_temps:
            tab = self._tabs.get(temperature)
            if tab is None:
                tab = _MetricDebugTab(self._metric_label, self)
                self._tabs[temperature] = tab
                self.tab_widget.addTab(tab, f"{temperature:g} °C")
            else:
                index = self.tab_widget.indexOf(tab)
                if index >= 0:
                    self.tab_widget.setTabText(index, f"{temperature:g} °C")
            tab.update_frame(temperature, tables[temperature], columns)

        if target_temps:
            first_temp = target_temps[0]
            tab = self._tabs.get(first_temp)
            if tab is not None:
                index = self.tab_widget.indexOf(tab)
                if index >= 0:
                    self.tab_widget.setCurrentIndex(index)


METRIC_DEBUG_SPECS: Dict[str, Dict[str, str]] = {
    "coercivity": {"title": "Coercivity Debugger", "label": "Coercivity"},
    "remanence": {"title": "Remanence Debugger", "label": "Remanence"},
}


@dataclass
class RescaleResult:
    """Describe the linear transform used to align hysteresis endpoints."""

    scale: float
    offset: float
    source_left: float
    source_right: float
    target_left: float
    target_right: float
    applied: bool = True
    replacement: pd.Series | None = None


EDGE_FRACTION = 0.05
ABS_TOLERANCE = 1e-30
RELATIVE_TOLERANCE = 1e-6
REFERENCE_FLOOR = 1e-30


def _is_near_zero(value: float, *references: float) -> bool:
    """Return ``True`` when ``value`` is negligible compared to ``references``."""

    magnitudes: List[float] = []
    for ref in references:
        if ref is None or isinstance(ref, (pd.Series, pd.DataFrame)):
            continue
        try:
            magnitude = abs(float(ref))
        except (TypeError, ValueError):
            continue
        if math.isnan(magnitude) or math.isinf(magnitude):
            continue
        magnitudes.append(magnitude)

    scale = max(magnitudes, default=0.0)
    scale = max(scale, REFERENCE_FLOOR)
    if math.isnan(value):
        return True
    tolerance = max(ABS_TOLERANCE, scale * RELATIVE_TOLERANCE)
    return abs(value) <= tolerance


def _estimate_edge_values(df: pd.DataFrame, x_axis: str, y_axis: str) -> tuple[float, float]:
    """Return representative Y values near the minimum and maximum X coordinates."""

    if df.empty:
        raise ValueError("Cannot estimate edge values from an empty dataframe")

    ordered = df[[x_axis, y_axis]].dropna().sort_values(by=x_axis)
    if ordered.empty:
        raise ValueError("Cannot estimate edge values from non-numeric dataframe")

    count = max(1, math.ceil(len(ordered) * EDGE_FRACTION))
    left_values = ordered[y_axis].iloc[:count]
    right_values = ordered[y_axis].iloc[-count:]

    numeric_left = pd.to_numeric(left_values, errors="coerce").dropna()
    if numeric_left.empty:
        numeric_left = pd.Series([left_values.iloc[0]], index=left_values.index[:1])
    numeric_right = pd.to_numeric(right_values, errors="coerce").dropna()
    if numeric_right.empty:
        numeric_right = pd.Series([right_values.iloc[-1]], index=right_values.index[-1:])

    left = float(numeric_left.min())
    right = float(numeric_right.max())
    return left, right


def _apply_rescaling(
    entries: Sequence[tuple[Path, pd.DataFrame]],
    x_axis: str,
    y_axis: str,
) -> Dict[Path, RescaleResult]:
    """Compute linear transforms that align loop endpoints across measurements."""

    prepared: List[tuple[Path, pd.DataFrame, float, float, float, float]] = []
    for path, subset in entries:
        if subset.empty:
            continue
        left, right = _estimate_edge_values(subset, x_axis, y_axis)
        series_y = pd.to_numeric(subset[y_axis], errors="coerce")
        y_min = float(series_y.min()) if not series_y.empty else float(left)
        y_max = float(series_y.max()) if not series_y.empty else float(right)
        prepared.append((path, subset, float(left), float(right), y_min, y_max))

    if not prepared:
        return {}

    target_left = min(item[4] for item in prepared)
    target_right = max(item[5] for item in prepared)

    if _is_near_zero(target_right - target_left, target_left, target_right):
        best_entry = max(
            prepared,
            key=lambda item: abs(item[5] - item[4]),
        )
        best_span = abs(best_entry[5] - best_entry[4])
        if not _is_near_zero(best_span, best_entry[4], best_entry[5]):
            target_left = best_entry[4]
            target_right = best_entry[5]
        else:
            edge_span = abs(best_entry[3] - best_entry[2])
            if not _is_near_zero(edge_span, best_entry[2], best_entry[3]):
                target_left = best_entry[2]
                target_right = best_entry[3]
            else:
                epsilon = max(
                    abs(best_entry[4]),
                    abs(best_entry[5]),
                    1.0,
                ) * RELATIVE_TOLERANCE
                target_left = best_entry[4] - epsilon
                target_right = best_entry[5] + epsilon

    if target_left < 0 < target_right:
        symmetric_span = max(abs(target_left), abs(target_right))
        if symmetric_span > 0:
            target_left = -symmetric_span
            target_right = symmetric_span

    results: Dict[Path, RescaleResult] = {}
    for path, subset, left_edge, right_edge, y_min, y_max in prepared:
        source_left = left_edge
        source_right = right_edge

        if _is_near_zero(source_right - source_left, source_left, source_right, y_min, y_max):
            source_left = y_min
            source_right = y_max

        delta = source_right - source_left
        if _is_near_zero(delta, source_left, source_right, y_min, y_max):
            numeric_series = pd.to_numeric(subset[y_axis], errors="coerce").dropna()
            if not numeric_series.empty:
                alt_min = float(numeric_series.min())
                alt_max = float(numeric_series.max())
            else:
                alt_min = y_min
                alt_max = y_max

            if not _is_near_zero(alt_max - alt_min, alt_min, alt_max):
                source_left = alt_min
                source_right = alt_max
                delta = source_right - source_left

        if _is_near_zero(delta, source_left, source_right):
            gradient = pd.Series(
                np.linspace(target_left, target_right, len(subset), dtype=float),
                index=subset.index,
            )
            results[path] = RescaleResult(
                scale=0.0,
                offset=target_left,
                source_left=source_left,
                source_right=source_right,
                target_left=target_left,
                target_right=target_right,
                applied=True,
                replacement=gradient,
            )
            continue

        scale = (target_right - target_left) / delta
        offset = target_left - scale * source_left

        results[path] = RescaleResult(
            scale=scale,
            offset=offset,
            source_left=source_left,
            source_right=source_right,
            target_left=target_left,
            target_right=target_right,
            applied=True,
        )

    return results


def _suggest_export_subfolder(measurements: Sequence[VSMMeasurement | Path | str]) -> str:
    """Suggest a folder name based on the first measurement path."""

    for entry in measurements:
        if isinstance(entry, VSMMeasurement):
            stem = entry.path.stem
        elif isinstance(entry, Path):
            stem = entry.stem
        else:
            stem = str(entry)
        cleaned = _clean_folder_name(stem)
        if cleaned:
            return cleaned
    return "VSM_Export"


def _temperature_subfolder_name(temperature: float) -> str:
    """Return a filesystem-friendly subfolder for a given temperature."""

    label = f"T{temperature:+g}C"
    cleaned = _clean_folder_name(label)
    return cleaned or "Temperature"


def _coerce_bool(value: object) -> bool:
    """Return ``True`` when ``value`` represents an enabled boolean."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in {"1", "true", "yes", "on", "enabled"}
    return False


def _looks_like_vsm_name(name: str) -> bool:
    """Return ``True`` when ``name`` resembles a VSM hysteresis export."""

    lowered = name.strip().lower()
    if VSM_FILE_TOKEN_RE.search(lowered):
        return True

    stem = Path(lowered).stem
    if VSM_FILE_TOKEN_RE.search(stem):
        return True

    return "-hys-" in stem and "-t" in stem and "-a" in stem


def _find_vsm_files(directory: Path) -> List[Path]:
    """Return all VSM data files within ``directory`` and its subdirectories."""

    if not directory.is_dir():
        return []

    matches: List[Path] = []
    for candidate in directory.rglob("*"):
        if not candidate.is_file():
            continue
        if _looks_like_vsm_name(candidate.name):
            matches.append(candidate)

    unique: Dict[Path, Path] = {}
    for path in matches:
        unique[path.resolve()] = path
    return sorted(unique.values())


@dataclass
class PlotTabState:
    """Track Matplotlib artefacts for a rendered temperature tab."""

    axes: Any
    canvas: FigureCanvas
    lines: Dict[float, Any]


@dataclass
class GraphLineState:
    """Describe a plotted line within the embedded Matplotlib canvas."""

    key: tuple[str, float | str]
    label: str
    line: Any
    base_x: np.ndarray
    base_y: np.ndarray
    normalized: bool = False


@dataclass
class TabDescriptor:
    """Capture metadata for a tabbed plot and its object manager bindings."""

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


_PRE_NORMALIZE_Y_KEY = "pre_normalize_y"


@dataclass
class PlotSeriesExport:
    """Describe a Matplotlib curve that can be exported as ASCII."""

    temperature: float
    angle: float
    data: pd.DataFrame
    x_axis: str
    y_axis: str
    rescaled: bool
    source: Path


@dataclass
class MetricResult:
    """Container for derived hysteresis properties."""

    coercivity: float | None
    remanence: float | None
    saturation: float | None
    coercivity_pair: tuple[float, float] | None = None
    remanence_pair: tuple[float, float] | None = None
    coercivity_raw_pair: tuple[float | None, float | None] | None = None
    remanence_raw_pair: tuple[float | None, float | None] | None = None


def _split_column_label(label: str) -> tuple[str, str]:
    """Return the long name and unit extracted from ``label``."""

    text = str(label or "").strip()
    if not text:
        return "Column", ""
    if "[" in text and "]" in text:
        name_part, unit_part = text.rsplit("[", 1)
        unit_part = unit_part.rstrip("] ")
        name_part = name_part.strip()
        if name_part:
            return name_part, unit_part.strip()
    return text, ""


def _origin_short_name(long_name: str, existing: set[str]) -> str:
    """Create an Origin-friendly short name that avoids duplicates."""

    candidate = re.sub(r"[^0-9A-Za-z]", "", long_name)
    if not candidate:
        candidate = "Col"
    if candidate[0].isdigit():
        candidate = f"C{candidate}"
    base = candidate[:13] or candidate
    choice = base
    index = 2
    while choice in existing:
        suffix = f"_{index}"
        trim = max(1, 13 - len(suffix))
        choice = f"{base[:trim]}{suffix}"
        index += 1
    existing.add(choice)
    return choice


def _format_column_with_unit(label: str, unit: str) -> str:
    unit = unit.strip()
    return f"{label} [{unit}]" if unit else label


def _format_metadata_comment(metadata: Dict[str, Any]) -> str:
    parts: List[str] = []
    temperature = metadata.get("temperature")
    if isinstance(temperature, (int, float)) and not math.isnan(temperature):
        parts.append(f"Temperature: {temperature:g} °C")
    angle = metadata.get("angle")
    if isinstance(angle, (int, float)) and not math.isnan(angle):
        parts.append(f"Angle: {angle:g} °")
    summary = metadata.get("summary")
    if summary:
        parts.append(str(summary))
    rescaled = metadata.get("rescaled")
    if rescaled:
        parts.append("Rescaled values")
    source = metadata.get("source")
    if source:
        parts.append(f"Source: {source}")
    return "; ".join(parts)


def _write_origin_ascii(
    path: Path,
    df: pd.DataFrame,
    *,
    metadata: Dict[str, Any],
    axis_roles: Dict[str, str] | None = None,
) -> None:
    """Write ``df`` to ``path`` with Origin-compatible header rows."""

    axis_roles = axis_roles or {}
    columns = [str(col) for col in df.columns]
    long_names: List[str] = []
    units: List[str] = []
    comments: List[str] = []
    short_names: List[str] = []
    seen: set[str] = set()
    base_comment = _format_metadata_comment(metadata)
    for column in columns:
        long_name, unit = _split_column_label(column)
        long_names.append(long_name)
        units.append(unit)
        short_names.append(_origin_short_name(long_name or "Column", seen))
        role = axis_roles.get(column)
        if role and base_comment:
            comments.append(f"{role}: {base_comment}")
        elif role:
            comments.append(role)
        elif base_comment:
            comments.append(base_comment)
        else:
            comments.append("")

    metadata_lines: List[str] = []
    x_axis = metadata.get("x_axis")
    if x_axis:
        metadata_lines.append(f"# X Axis: {x_axis}")
    y_axis = metadata.get("y_axis")
    if y_axis:
        metadata_lines.append(f"# Y Axis: {y_axis}")
    summary = metadata.get("summary")
    if summary:
        metadata_lines.append(f"# {summary}")

    with path.open("w", encoding="utf-8", newline="") as handle:
        for line in metadata_lines:
            handle.write(f"{line}\n")
        handle.write("\t".join(short_names) + "\n")
        handle.write("@L\t" + "\t".join(long_names) + "\n")
        handle.write("@U\t" + "\t".join(units) + "\n")
        handle.write("@C\t" + "\t".join(comments) + "\n")
        df.to_csv(handle, sep="\t", index=False, header=False, na_rep="")


def _symmetrise_crossings(
    candidates: Sequence[float],
) -> tuple[
    float | None,
    tuple[float, float] | None,
    tuple[float | None, float | None] | None,
]:
    positives = sorted((value for value in candidates if value > 0.0), key=abs)
    negatives = sorted((value for value in candidates if value < 0.0), key=abs)
    zeros = [value for value in candidates if math.isclose(value, 0.0, rel_tol=1e-12, abs_tol=1e-12)]

    raw_pair: tuple[float | None, float | None] | None = None

    if positives and negatives:
        pos_value = positives[0]
        neg_value = negatives[0]
        raw_pair = (float(neg_value), float(pos_value))
        pos = abs(pos_value)
        neg = abs(neg_value)
        magnitude = (pos + neg) / 2.0
        sym_pair = (-float(magnitude), float(magnitude))
        return float(magnitude), sym_pair, raw_pair

    if positives:
        magnitude = float(abs(positives[0]))
        raw_pair = (None, float(positives[0]))
        return magnitude, (-magnitude, magnitude), raw_pair
    if negatives:
        magnitude = float(abs(negatives[0]))
        raw_pair = (float(negatives[0]), None)
        return magnitude, (-magnitude, magnitude), raw_pair
    if zeros:
        raw_pair = (0.0, 0.0)
        return 0.0, (0.0, 0.0), raw_pair
    return None, None, None


def _collect_crossings_x_at_y(
    x_values: np.ndarray, y_values: np.ndarray, target: float = 0.0
) -> List[float]:
    candidates: List[float] = []

    def _record(value: float) -> None:
        if not math.isfinite(value):
            return
        for existing in candidates:
            tolerance = max(1e-9, 1e-6 * max(abs(existing), abs(value), 1.0))
            if math.isclose(existing, value, abs_tol=tolerance):
                return
        candidates.append(value)

    for x0, y0, x1, y1 in zip(x_values[:-1], y_values[:-1], x_values[1:], y_values[1:]):
        if any(math.isnan(v) for v in (x0, x1, y0, y1)):
            continue
        delta0 = y0 - target
        delta1 = y1 - target
        scale = max(abs(y0), abs(y1), abs(target), 1.0)
        zero_tol = max(1e-9, 1e-4 * scale)
        if abs(delta0) <= zero_tol:
            delta0 = 0.0
        if abs(delta1) <= zero_tol:
            delta1 = 0.0
        if delta0 == 0.0 and delta1 == 0.0:
            continue
        if delta0 == 0.0:
            _record(float(x0))
            continue
        if delta1 == 0.0:
            _record(float(x1))
            continue
        if delta0 * delta1 > 0:
            continue
        if math.isclose(y1, y0, rel_tol=1e-12, abs_tol=1e-12):
            continue
        fraction = (target - y0) / (y1 - y0)
        candidate = float(x0 + fraction * (x1 - x0))
        _record(candidate)

    if not candidates:
        finite_mask = np.isfinite(y_values)
        finite_y = y_values[finite_mask]
        finite_x = x_values[finite_mask]
        if finite_y.size:
            scale = float(np.max(np.abs(finite_y))) if np.any(np.isfinite(finite_y)) else 0.0
            threshold = max(1e-9, 0.02 * scale)
            distances = np.abs(finite_y - target)
            min_index = int(np.argmin(distances))
            if distances[min_index] <= threshold:
                x0 = float(finite_x[min_index])
                y0 = float(finite_y[min_index])
                neighbours: List[int] = []
                if min_index > 0:
                    neighbours.append(min_index - 1)
                if min_index + 1 < finite_x.size:
                    neighbours.append(min_index + 1)
                for neighbour in neighbours:
                    x1 = float(finite_x[neighbour])
                    y1 = float(finite_y[neighbour])
                    if not math.isfinite(y1) or math.isclose(y1, y0, rel_tol=1e-12, abs_tol=1e-12):
                        continue
                    fraction = (target - y0) / (y1 - y0)
                    candidate = float(x0 + fraction * (x1 - x0))
                    segment_min = min(x0, x1) - 1e-9
                    segment_max = max(x0, x1) + 1e-9
                    if segment_min <= candidate <= segment_max:
                        _record(candidate)

    return candidates


def _collect_crossings_y_at_x(
    x_values: np.ndarray, y_values: np.ndarray, target: float = 0.0
) -> List[float]:
    candidates: List[float] = []

    def _record(value: float) -> None:
        if not math.isfinite(value):
            return
        for existing in candidates:
            tolerance = max(1e-9, 1e-6 * max(abs(existing), abs(value), 1.0))
            if math.isclose(existing, value, abs_tol=tolerance):
                return
        candidates.append(value)

    for x0, y0, x1, y1 in zip(x_values[:-1], y_values[:-1], x_values[1:], y_values[1:]):
        if any(math.isnan(v) for v in (x0, x1, y0, y1)):
            continue
        delta0 = x0 - target
        delta1 = x1 - target
        scale = max(abs(x0), abs(x1), abs(target), 1.0)
        zero_tol = max(1e-9, 1e-4 * scale)
        if abs(delta0) <= zero_tol:
            delta0 = 0.0
        if abs(delta1) <= zero_tol:
            delta1 = 0.0
        if delta0 == 0.0 and delta1 == 0.0:
            continue
        if delta0 == 0.0:
            _record(float(y0))
            continue
        if delta1 == 0.0:
            _record(float(y1))
            continue
        if delta0 * delta1 > 0:
            continue
        if math.isclose(x1, x0, rel_tol=1e-12, abs_tol=1e-12):
            continue
        fraction = (target - x0) / (x1 - x0)
        candidate = float(y0 + fraction * (y1 - y0))
        _record(candidate)

    if not candidates:
        finite_mask = np.isfinite(x_values)
        finite_x = x_values[finite_mask]
        finite_y = y_values[finite_mask]
        if finite_x.size:
            scale = float(np.max(np.abs(finite_x))) if np.any(np.isfinite(finite_x)) else 0.0
            threshold = max(1e-9, 0.02 * scale)
            distances = np.abs(finite_x - target)
            min_index = int(np.argmin(distances))
            if distances[min_index] <= threshold:
                x0 = float(finite_x[min_index])
                y0 = float(finite_y[min_index])
                neighbours: List[int] = []
                if min_index > 0:
                    neighbours.append(min_index - 1)
                if min_index + 1 < finite_x.size:
                    neighbours.append(min_index + 1)
                for neighbour in neighbours:
                    x1 = float(finite_x[neighbour])
                    y1 = float(finite_y[neighbour])
                    if not math.isfinite(x1) or math.isclose(x1, x0, rel_tol=1e-12, abs_tol=1e-12):
                        continue
                    fraction = (target - x0) / (x1 - x0)
                    candidate = float(y0 + fraction * (y1 - y0))
                    segment_min = min(x0, x1) - 1e-9
                    segment_max = max(x0, x1) + 1e-9
                    if segment_min <= target <= segment_max:
                        _record(candidate)

    return candidates


def _interpolate_x_at_y(x_values: np.ndarray, y_values: np.ndarray, target: float = 0.0) -> float | None:
    """Return a symmetrised coercivity estimate for ``target`` crossings on Y."""

    candidates = _collect_crossings_x_at_y(x_values, y_values, target)
    value, _, _ = _symmetrise_crossings(candidates)
    return value


def _interpolate_y_at_x(x_values: np.ndarray, y_values: np.ndarray, target: float = 0.0) -> float | None:
    """Return a symmetrised remanence estimate for ``target`` crossings on X."""

    candidates = _collect_crossings_y_at_x(x_values, y_values, target)
    value, _, _ = _symmetrise_crossings(candidates)
    return value


def _calculate_metrics(subset: pd.DataFrame, x_axis: str, y_axis: str) -> MetricResult:
    """Compute coercivity, remanence, and saturation magnetisation."""

    if subset.empty:
        return MetricResult(None, None, None)
    numeric = subset[[x_axis, y_axis]].apply(pd.to_numeric, errors="coerce")
    ordered = numeric.dropna()
    if ordered.empty:
        return MetricResult(None, None, None)
    x_values = ordered[x_axis].to_numpy(dtype=float)
    y_values = ordered[y_axis].to_numpy(dtype=float)
    coercivity_candidates = _collect_crossings_x_at_y(x_values, y_values, target=0.0)
    coercivity, coercivity_pair, coercivity_raw = _symmetrise_crossings(coercivity_candidates)
    remanence_candidates = _collect_crossings_y_at_x(x_values, y_values, target=0.0)
    remanence, remanence_pair, remanence_raw = _symmetrise_crossings(remanence_candidates)
    if len(y_values) and np.any(np.isfinite(y_values)):
        saturation = float(np.nanmax(y_values))
    else:
        saturation = None
    return MetricResult(
        coercivity,
        remanence,
        saturation,
        coercivity_pair,
        remanence_pair,
        coercivity_raw,
        remanence_raw,
    )


def _aggregate_metrics(
    records: Sequence[tuple[float, float, MetricResult]],
    *,
    x_unit: str,
    y_unit: str,
    temperature_unit: str = "°C",
) -> tuple[Dict[float, pd.DataFrame], Dict[float, pd.DataFrame], Dict[str, str]]:
    """Return metric tables grouped by temperature and angle."""

    angle_label = _format_column_with_unit("Angle", "deg")
    temperature_label = _format_column_with_unit("Temperature", temperature_unit)
    coercivity_label = _format_column_with_unit("Coercivity", x_unit)
    remanence_label = _format_column_with_unit("Remanence", y_unit)
    saturation_label = _format_column_with_unit("Saturation Magnetization", y_unit)

    by_temperature: Dict[float, List[Dict[str, float]]] = {}
    by_angle: Dict[float, List[Dict[str, float]]] = {}

    for temperature, angle, metrics in records:
        row_temp = {
            angle_label: angle,
            coercivity_label: float(metrics.coercivity) if metrics.coercivity is not None else math.nan,
            remanence_label: float(metrics.remanence) if metrics.remanence is not None else math.nan,
            saturation_label: float(metrics.saturation) if metrics.saturation is not None else math.nan,
        }
        by_temperature.setdefault(temperature, []).append(row_temp)

        row_angle = {
            temperature_label: temperature,
            coercivity_label: float(metrics.coercivity) if metrics.coercivity is not None else math.nan,
            remanence_label: float(metrics.remanence) if metrics.remanence is not None else math.nan,
            saturation_label: float(metrics.saturation) if metrics.saturation is not None else math.nan,
        }
        by_angle.setdefault(angle, []).append(row_angle)

    temp_tables: Dict[float, pd.DataFrame] = {}
    for temperature, rows in by_temperature.items():
        df = pd.DataFrame(rows)
        df = df.sort_values(by=angle_label)
        temp_tables[temperature] = df

    angle_tables: Dict[float, pd.DataFrame] = {}
    for angle, rows in by_angle.items():
        df = pd.DataFrame(rows)
        df = df.sort_values(by=temperature_label)
        angle_tables[angle] = df

    column_map = {
        "angle": angle_label,
        "temperature": temperature_label,
        "coercivity": coercivity_label,
        "remanence": remanence_label,
        "saturation": saturation_label,
    }

    return temp_tables, angle_tables, column_map


def _normalise_column_name(raw: str, index: int) -> str:
    cleaned = re.sub(r"\\s+", " ", raw.strip())
    if not cleaned:
        return f"Column {index}"

    primary, *remainder = cleaned.split(",", 1)
    primary = WHITESPACE_RE.sub(" ", primary).strip()
    if not primary:
        primary = f"Column {index}"

    unit = ""
    if remainder:
        unit_match = re.search(r"\[(.+?)\]", remainder[0])
        if unit_match:
            unit = unit_match.group(0)

    if unit and unit not in primary:
        return f"{primary} {unit}".strip()
    return primary

def _normalise_header_token(raw: str, index: int) -> str:
    """Best effort conversion of inline header tokens to friendly labels."""

    cleaned = raw.strip().strip("_")
    cleaned = WHITESPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip()
    return cleaned or f"Column {index}"

def _normalise_metadata_value(value: float | None, *, decimals: int = 3) -> float | None:
    """Round metadata to a friendly value while guarding against NaNs."""

    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    rounded = round(numeric, decimals)
    nearest_integer = round(rounded)
    if math.isclose(rounded, nearest_integer, abs_tol=0.45):
        rounded = float(nearest_integer)
    else:
        rounded = round(rounded, decimals)

    if rounded == 0:
        return 0.0
    return rounded

def _read_vsm_file(path: Path) -> pd.DataFrame:
    columns: List[str] = []
    inline_header: List[str] | None = None
    sections: List[List[List[str]]] = []

    current_rows: List[List[str]] = []
    current_tokens: List[str] = []
    expected_columns: int | None = None
    in_data = False
    inline_header_ready = False

    def _start_section() -> None:
        nonlocal current_rows, current_tokens, expected_columns, in_data
        current_rows = []
        current_tokens = []
        expected_columns = len(columns) or (len(inline_header) if inline_header else None)
        in_data = True

    def _finish_section() -> None:
        nonlocal current_rows, current_tokens, expected_columns, in_data
        if expected_columns and current_tokens:
            if len(current_tokens) == expected_columns:
                current_rows.append(current_tokens[:])
            current_tokens = []
        if current_rows:
            sections.append([row[:] for row in current_rows])
        current_rows = []
        current_tokens = []
        expected_columns = None
        in_data = False

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            stripped = line.strip()
            if not in_data:
                if stripped.startswith("@@End of Header"):
                    inline_header_ready = True
                    inline_header = None
                    continue
                match = HEADER_COLUMN_RE.match(stripped)
                if match:
                    columns.append(_normalise_column_name(match.group(1), len(columns)))
                    continue
                if stripped.startswith("@@"):
                    if stripped.startswith("@@Data") or stripped.startswith("@@Final Manipulated Data"):
                        _start_section()
                        inline_header_ready = False
                    continue
                if stripped and not stripped.startswith("@"):
                    if inline_header_ready and inline_header is None:
                        parts = stripped.split()
                        if parts and any("_" in part for part in parts):
                            inline_header = parts
                            inline_header_ready = False
                            continue
                    if not columns and inline_header is None:
                        parts = stripped.split()
                        if parts and any("_" in part for part in parts):
                            inline_header = parts
                            continue
                continue

            if stripped.startswith("@@END Data"):
                _finish_section()
                continue
            if stripped.startswith("@@"):
                continue
            if not stripped or stripped.startswith("New Section"):
                continue
            if stripped.startswith("@"):
                continue

            tokens = stripped.split()
            if not tokens:
                continue
            current_tokens.extend(tokens)
            if expected_columns is None:
                if columns:
                    expected_columns = len(columns)
                elif inline_header:
                    expected_columns = len(inline_header)
                else:
                    expected_columns = len(tokens)
            if expected_columns:
                while len(current_tokens) >= expected_columns:
                    row = current_tokens[:expected_columns]
                    current_rows.append(row)
                    current_tokens = current_tokens[expected_columns:]

    if in_data:
        _finish_section()

    for section in reversed(sections):
        if section:
            data_rows = section
            break
    else:
        raise ValueError("No data rows detected in VSM file")

    numeric_rows: List[List[str]] = []
    for row in data_rows:
        if not row:
            continue
        if all(_looks_numeric(token) for token in row):
            numeric_rows.append(row)

    if not numeric_rows:
        raise ValueError("No numeric data rows detected in VSM file")

    df = pd.DataFrame(numeric_rows, dtype=float)

    width = df.shape[1]
    resolved: List[str] = []
    source_names: List[str]
    if columns:
        source_names = columns
    elif inline_header:
        source_names = [_normalise_header_token(token, idx) for idx, token in enumerate(inline_header)]
    else:
        source_names = []

    for idx in range(width):
        if idx < len(source_names):
            name = source_names[idx]
        else:
            name = f"Column {idx}"
        if name in resolved:
            suffix = 2
            while f"{name} ({suffix})" in resolved:
                suffix += 1
            name = f"{name} ({suffix})"
        resolved.append(name)
    df.columns = resolved
    return df

def _looks_numeric(token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    try:
        float(token)
    except ValueError:
        return False
    return True

def _coerce_constant_value(series: pd.Series) -> float | None:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    value = float(numeric.median())
    if pd.isna(value):
        return None
    return value

def _match_column(df: pd.DataFrame, candidate: str) -> str | None:
    needle = candidate.lower()
    for column in df.columns:
        if column.lower() == needle:
            return column
    for column in df.columns:
        if needle in column.lower():
            return column
    return None

def _derive_metadata_from_dataframe(df: pd.DataFrame) -> tuple[float | None, float | None]:
    angle = None
    temperature = None

    for label in ANGLE_COLUMN_CANDIDATES:
        column = _match_column(df, label)
        if column is None:
            continue
        angle = _coerce_constant_value(df[column])
        if angle is not None:
            break

    for label in TEMPERATURE_COLUMN_CANDIDATES:
        column = _match_column(df, label)
        if column is None:
            continue
        temperature = _coerce_constant_value(df[column])
        if temperature is not None:
            break

    return _normalise_metadata_value(angle), _normalise_metadata_value(temperature)

def _safe_float(token: str) -> float | None:
    token = token.strip()
    if not token:
        return None
    token = token.rstrip("-_")
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        pass

    sign = ""
    remainder = token
    if remainder.startswith("+"):
        remainder = remainder[1:]
    elif remainder.startswith("-"):
        sign = "-"
        remainder = remainder[1:]

    remainder = remainder.strip()
    remainder = remainder.rstrip("-")
    if not remainder:
        return None

    parts = [part for part in remainder.split("-") if part]
    if not parts:
        return None

    if len(parts) == 1 and parts[0].isdigit():
        try:
            return float(f"{sign}{parts[0]}")
        except ValueError:
            return None

    if len(parts) >= 2 and all(part.isdigit() for part in parts):
        major = parts[0]
        fractional = "".join(parts[1:])
        candidate = f"{sign}{major}.{fractional}" if fractional else f"{sign}{major}"
        try:
            return float(candidate)
        except ValueError:
            return None

    return None

def _metadata_from_filename(path: Path) -> tuple[float | None, float | None]:
    stem = path.stem
    angle_match = ANGLE_RE.search(stem)
    temp_match = TEMP_RE.search(stem)
    angle = _safe_float(angle_match.group(1)) if angle_match else None
    temperature = _safe_float(temp_match.group(1)) if temp_match else None
    return angle, temperature

@lru_cache(maxsize=256)
def _metadata_from_file(path: Path) -> tuple[float | None, float | None]:
    angle, temperature = _metadata_from_filename(path)
    if angle is not None and temperature is not None:
        return _normalise_metadata_value(angle), _normalise_metadata_value(temperature)

    try:
        handle = path.open("r", encoding="utf-8", errors="ignore")
    except OSError:
        return angle, temperature

    with handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if angle is None:
                match = ANGLE_RE.search(stripped)
                if not match:
                    match = FIELD_ANGLE_RE.search(stripped) or ANGLE_OFFSET_RE.search(stripped)
                if match:
                    candidate = _safe_float(match.group(1))
                    if candidate is not None:
                        angle = candidate
            if temperature is None:
                match = TEMP_RE.search(stripped)
                if not match:
                    match = SET_TEMPERATURE_RE.search(stripped)
                if match:
                    candidate = _safe_float(match.group(1))
                    if candidate is not None:
                        temperature = candidate
            if stripped.startswith("@@Data") or stripped.startswith("@@Final Manipulated Data"):
                if angle is not None and temperature is not None:
                    break
                continue
            if angle is not None and temperature is not None:
                break

    return _normalise_metadata_value(angle), _normalise_metadata_value(temperature)

def _parse_temperature(path: Path) -> float | None:
    _, temperature = _metadata_from_file(path)
    return temperature

def _parse_angle(path: Path) -> float | None:
    angle, _ = _metadata_from_file(path)
    return angle

class ExportOptionsDialog(QtWidgets.QDialog):
    """Prompt for optional subfolder creation when exporting TXT files."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        base_directory: Path,
        *,
        suggestion: str = "",
        allow_plot_axes: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("TXT Export Options")
        self.base_directory = Path(base_directory)
        self._selected_directory = self.base_directory
        self._suggestion = _clean_folder_name(suggestion) or "VSM_Export"
        self._allow_plot_axes = allow_plot_axes

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        label = QtWidgets.QLabel(f"Base folder:\n{self.base_directory}")
        label.setWordWrap(True)
        layout.addWidget(label)

        self.subfolder_checkbox = QtWidgets.QCheckBox("Create subfolder")
        layout.addWidget(self.subfolder_checkbox)

        self.subfolder_edit = QtWidgets.QLineEdit()
        self.subfolder_edit.setPlaceholderText(self._suggestion)
        self.subfolder_edit.setEnabled(False)
        layout.addWidget(self.subfolder_edit)

        self.subfolder_checkbox.toggled.connect(self._toggle_subfolder)

        scope_label = QtWidgets.QLabel("Columns to export")
        layout.addWidget(scope_label)

        self.scope_combo = QtWidgets.QComboBox()
        self.scope_combo.addItem("All columns", "all")
        plot_index = self.scope_combo.count()
        self.scope_combo.addItem("Plot axes only", "plot_axes")
        if not self._allow_plot_axes:
            model = self.scope_combo.model()
            if hasattr(model, "item"):
                item = model.item(plot_index)
                if item is not None:
                    item.setEnabled(False)
            self.scope_combo.setCurrentIndex(0)
        layout.addWidget(self.scope_combo)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _toggle_subfolder(self, checked: bool) -> None:
        self.subfolder_edit.setEnabled(checked)
        if checked and not self.subfolder_edit.text():
            self.subfolder_edit.setText(self._suggestion)
            self.subfolder_edit.selectAll()
            self.subfolder_edit.setFocus()

    def accept(self) -> None:  # type: ignore[override]
        if self.subfolder_checkbox.isChecked():
            text = self.subfolder_edit.text().strip()
            if not text:
                text = self._suggestion
            cleaned = _clean_folder_name(text)
            if not cleaned:
                QtWidgets.QMessageBox.warning(
                    self,
                    "TXT Export Options",
                    "Provide a folder name or disable subfolder creation.",
                )
                return
            self.subfolder_edit.setText(cleaned)
            self._selected_directory = self.base_directory / cleaned
        else:
            self._selected_directory = self.base_directory
        super().accept()

    def selected_directory(self) -> Path:
        return self._selected_directory

    def selected_scope(self) -> str:
        data = self.scope_combo.currentData()
        return str(data or "all")

class VSMPlotter(QtWidgets.QMainWindow):
    """Render hysteresis loops for VSM-HYS-DATA files."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VSM Hysteresis Loops")
        self.resize(1480, 940)

        self.logger = logging.getLogger("vsm_hysteresis_loops")
        self.logger.setLevel(logging.INFO)
        self.settings = QtCore.QSettings("MicrowireLab", "VSMHysteresisLoops")
        stored_x = self.settings.value("x_axis")
        stored_y = self.settings.value("y_axis")
        self._stored_axes: tuple[str | None, str | None] = (
            stored_x if isinstance(stored_x, str) and stored_x else None,
            stored_y if isinstance(stored_y, str) and stored_y else None,
        )
        self.last_export_path: Path | None = None

        self.measurements: List[VSMMeasurement] = []
        self._last_prepared_groups: Dict[float, List[tuple[VSMMeasurement, pd.DataFrame]]] = {}
        self._last_rescale_info: Dict[float, Dict[Path, RescaleResult]] = {}
        self._last_axes: tuple[str, str] | None = None
        self._last_rescale_enabled = False
        self._line_visibility: Dict[float, Dict[float, bool]] = {}
        self._plot_tabs: Dict[float, PlotTabState] = {}
        self._tab_descriptors: Dict[QtWidgets.QWidget, TabDescriptor] = {}
        self._object_items: Dict[
            tuple[QtWidgets.QWidget, tuple[str, float | str]],
            QtWidgets.QTreeWidgetItem,
        ] = {}
        self._worksheet_models: Dict[Path, WorksheetModel] = {}
        self._plotted_series_exports: Dict[tuple[float, float], PlotSeriesExport] = {}
        self._metrics_by_temperature: Dict[float, pd.DataFrame] = {}
        self._metrics_by_angle: Dict[float, pd.DataFrame] = {}
        self._metric_column_names: Dict[str, str] = {}
        self._metric_results: Dict[tuple[float, float], MetricResult] = {}
        self._metric_debug_tables: Dict[str, Dict[float, pd.DataFrame]] = {}
        self._metric_debug_columns: Dict[str, Dict[str, str]] = {}
        self._metric_debug_windows: Dict[str, MetricDebugWindow] = {}
        self._temperature_tab_widgets: List[QtWidgets.QWidget] = []
        self._metrics_angle_tabs: List[QtWidgets.QWidget] = []
        self._metrics_temperature_tabs: List[QtWidgets.QWidget] = []
        self._overlay_tab_widgets: List[QtWidgets.QWidget] = []
        self._canvas_by_tab: Dict[QtWidgets.QWidget, FigureCanvas] = {}
        self._axes_by_tab: Dict[QtWidgets.QWidget, Any] = {}
        self._last_graph_dir: Path | None = None

        self._build_ui()
        self._load_settings()


    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central)
        central_layout.setContentsMargins(12, 12, 12, 12)
        central_layout.setSpacing(10)

        controls = QtWidgets.QWidget()
        controls_layout = QtWidgets.QGridLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setHorizontalSpacing(8)
        controls_layout.setVerticalSpacing(6)

        controls_layout.addWidget(QtWidgets.QLabel("Data sources"), 0, 0)
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("Select VSM files or a folder…")
        self.path_edit.editingFinished.connect(self._handle_manual_path_entry)
        controls_layout.addWidget(self.path_edit, 0, 1, 1, 3)

        self.browse_files_button = QtWidgets.QPushButton("Browse files…")
        self.browse_files_button.clicked.connect(self._choose_files)
        controls_layout.addWidget(self.browse_files_button, 0, 4)

        self.browse_folder_button = QtWidgets.QPushButton("Browse folder…")
        self.browse_folder_button.clicked.connect(self._choose_folder)
        controls_layout.addWidget(self.browse_folder_button, 0, 5)

        controls_layout.setColumnStretch(1, 1)
        controls_layout.setColumnStretch(2, 1)

        central_layout.addWidget(controls)

        action_row = QtWidgets.QHBoxLayout()
        self.plot_button = QtWidgets.QPushButton("Generate plots")
        self.plot_button.clicked.connect(self._generate_plots)
        self.plot_button.setEnabled(False)
        action_row.addWidget(self.plot_button)

        self.popout_button = QtWidgets.QPushButton("Open in Matplotlib")
        self.popout_button.clicked.connect(self._open_matplotlib_window)
        self.popout_button.setEnabled(False)
        action_row.addWidget(self.popout_button)

        self.save_graph_button = QtWidgets.QPushButton("Save graph…")
        self.save_graph_button.setEnabled(False)
        self.save_graph_button.clicked.connect(self._save_current_graph)
        action_row.addWidget(self.save_graph_button)

        self.normalize_button = QtWidgets.QPushButton("Normalize Y")
        self.normalize_button.setEnabled(False)
        self.normalize_button.clicked.connect(self._normalize_current_graph)
        action_row.addWidget(self.normalize_button)

        self.export_button = QtWidgets.QPushButton("Export TXT…")
        self.export_button.clicked.connect(self._export_txt)
        self.export_button.setEnabled(False)
        action_row.addWidget(self.export_button)

        action_row.addStretch(1)
        central_layout.addLayout(action_row)

        self.tab_widget = QtWidgets.QTabWidget()
        self.tab_widget.currentChanged.connect(self._handle_current_tab_changed)
        central_layout.addWidget(self.tab_widget, 1)

        self.setCentralWidget(central)

        self._log_has_unread_errors = False
        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setHeaderLabels(["Project Explorer", "Details"])
        self.project_tree.header().setStretchLastSection(True)
        self.project_tree.itemDoubleClicked.connect(self._focus_measurement_tab)
        project_dock = AutoHideDockWidget("Project Explorer", self, object_name="projectExplorerDock")
        project_dock.setWidget(self.project_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, project_dock)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_dock = AutoHideDockWidget("Message Log", self, object_name="messageLogDock")
        log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, log_dock)
        self.tabifyDockWidget(project_dock, log_dock)
        project_dock.raise_()
        self.message_log_dock = log_dock
        self.log_view.installEventFilter(self)
        log_dock.visibilityChanged.connect(self._handle_log_visibility)

        self.object_tree = QtWidgets.QTreeWidget()
        self.object_tree.setHeaderLabels(["Object Manager"])
        self.object_tree.setColumnCount(1)
        self.object_tree.itemChanged.connect(self._handle_object_item_changed)
        object_dock = AutoHideDockWidget("Object Manager", self, object_name="objectManagerDock")
        object_dock.setWidget(self.object_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, object_dock)

        self.worksheet_tabs = QtWidgets.QTabWidget()
        worksheet_dock = AutoHideDockWidget("Worksheets", self, object_name="worksheetsDock")
        worksheet_dock.setWidget(self.worksheet_tabs)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, worksheet_dock)

        graph_settings_widget = QtWidgets.QWidget()
        graph_layout = QtWidgets.QVBoxLayout(graph_settings_widget)
        graph_layout.setContentsMargins(8, 8, 8, 8)
        graph_layout.setSpacing(12)

        axes_group = QtWidgets.QGroupBox("Axes and filters")
        axes_form = QtWidgets.QFormLayout(axes_group)
        axes_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        axes_form.addRow("Backend", self.backend_combo)

        self.temperature_combo = QtWidgets.QComboBox()
        self.temperature_combo.addItem("All temperatures", None)
        axes_form.addRow("Temperature", self.temperature_combo)

        self.x_axis_combo = QtWidgets.QComboBox()
        self.y_axis_combo = QtWidgets.QComboBox()
        self.x_axis_combo.currentTextChanged.connect(self._store_axis_selection)
        self.y_axis_combo.currentTextChanged.connect(self._store_axis_selection)
        axes_form.addRow("X axis", self.x_axis_combo)
        axes_form.addRow("Y axis", self.y_axis_combo)

        graph_layout.addWidget(axes_group)

        appearance_group = QtWidgets.QGroupBox("Appearance")
        appearance_layout = QtWidgets.QVBoxLayout(appearance_group)
        appearance_layout.setContentsMargins(8, 8, 8, 8)
        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.addItem("Line", "line")
        self.style_combo.addItem("Line + symbols", "line_markers")
        appearance_layout.addWidget(QtWidgets.QLabel("Matplotlib style"))
        appearance_layout.addWidget(self.style_combo)

        self.rescale_checkbox = QtWidgets.QCheckBox("Normalise Y axis endpoints")
        self.rescale_checkbox.setToolTip(
            "Scale each curve so the negative-field and positive-field endpoints share\n"
            "a common minimum/maximum across all angles for the same temperature."
        )
        appearance_layout.addWidget(self.rescale_checkbox)

        self.dark_mode_checkbox = QtWidgets.QCheckBox("Dark plot theme")
        self.dark_mode_checkbox.setToolTip("Render Matplotlib plots using a dark background theme.")
        self.dark_mode_checkbox.toggled.connect(self._restyle_plots)
        appearance_layout.addWidget(self.dark_mode_checkbox)
        graph_layout.addWidget(appearance_group)

        overlay_group = QtWidgets.QGroupBox("Angle overlays")
        overlay_layout = QtWidgets.QVBoxLayout(overlay_group)
        overlay_layout.setContentsMargins(8, 8, 8, 8)
        self.angle_overlay_list = QtWidgets.QListWidget()
        self.angle_overlay_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        overlay_layout.addWidget(self.angle_overlay_list, 1)
        overlay_hint = QtWidgets.QLabel(
            "Select rotations to compare across temperatures or when exporting overlays."
        )
        overlay_hint.setWordWrap(True)
        overlay_layout.addWidget(overlay_hint)
        self.angle_overlay_button = QtWidgets.QPushButton(
            "Plot selected angles across temperatures"
        )
        self.angle_overlay_button.setEnabled(False)
        self.angle_overlay_button.clicked.connect(self._plot_angle_overlays)
        overlay_layout.addWidget(self.angle_overlay_button)
        graph_layout.addWidget(overlay_group, 1)

        metrics_group = QtWidgets.QGroupBox("Derived metrics")
        metrics_layout = QtWidgets.QVBoxLayout(metrics_group)
        metrics_layout.setContentsMargins(8, 8, 8, 8)
        self.metrics_angle_button = QtWidgets.QPushButton("Plot metrics vs angle")
        self.metrics_angle_button.setEnabled(False)
        self.metrics_angle_button.clicked.connect(self._plot_metrics_vs_angle)
        metrics_layout.addWidget(self.metrics_angle_button)
        self.metrics_temperature_button = QtWidgets.QPushButton("Plot metrics vs temperature")
        self.metrics_temperature_button.setEnabled(False)
        self.metrics_temperature_button.clicked.connect(self._plot_metrics_vs_temperature)
        metrics_layout.addWidget(self.metrics_temperature_button)
        self.export_metrics_button = QtWidgets.QPushButton("Export metrics")
        self.export_metrics_button.setEnabled(False)
        self.export_metrics_button.clicked.connect(self._export_metrics)
        metrics_layout.addWidget(self.export_metrics_button)
        graph_layout.addWidget(metrics_group)

        export_group = QtWidgets.QGroupBox("TXT export mode")
        export_layout = QtWidgets.QVBoxLayout(export_group)
        export_layout.setContentsMargins(8, 8, 8, 8)
        self.export_mode_combo = QtWidgets.QComboBox()
        self.export_mode_combo.addItem("Original data", "original")
        self.export_mode_combo.addItem("Rescaled data", "rescaled")
        export_layout.addWidget(self.export_mode_combo)
        graph_layout.addWidget(export_group)
        graph_layout.addStretch(1)

        graph_dock = AutoHideDockWidget("Graph Settings", self, object_name="graphSettingsDock")
        graph_dock.setWidget(graph_settings_widget)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, graph_dock)
        self.tabifyDockWidget(project_dock, graph_dock)

        menu_bar = install_standard_menu(
            self,
            help_topic="vsm_hysteresis_loops",
            console=self.log_view,
            open_file=self._open_files_from_menu,
            open_folder=self._open_folder_from_menu,
            close_window=self.close,
        )

        export_menu = menu_bar.addMenu("Export")
        export_data_action = export_menu.addAction("TXT data…")
        export_data_action.triggered.connect(self._export_txt)
        export_metrics_action = export_menu.addAction("Derived metrics…")
        export_metrics_action.triggered.connect(self._export_metrics)

        developer_menu: QtWidgets.QMenu | None = None
        for action in menu_bar.actions():
            menu = action.menu()
            if menu is not None and menu.objectName() == "mw_shared_developer":
                developer_menu = menu
                break
        if developer_menu is not None:
            developer_menu.addSeparator()
            coercivity_action = developer_menu.addAction("Coercivity debug…")
            if coercivity_action is not None:
                coercivity_action.setObjectName("mw_vsm_coercivity_debug")
                coercivity_action.triggered.connect(
                    partial(self._show_metric_debug, "coercivity")
                )
            remanence_action = developer_menu.addAction("Remanence debug…")
            if remanence_action is not None:
                remanence_action.setObjectName("mw_vsm_remanence_debug")
                remanence_action.triggered.connect(
                    partial(self._show_metric_debug, "remanence")
                )

        self.angle_overlay_list.itemSelectionChanged.connect(
            self._update_overlay_button_state
        )

    def _load_settings(self) -> None:
        sources = self.settings.value("sources", "")
        if isinstance(sources, str):
            self.path_edit.setText(sources)

        export_path = self.settings.value("last_export_path", "")
        if isinstance(export_path, str) and export_path:
            try:
                self.last_export_path = Path(export_path)
            except (TypeError, ValueError):
                self.last_export_path = None

        graph_dir = self.settings.value("last_graph_dir", "")
        if isinstance(graph_dir, str) and graph_dir:
            try:
                self._last_graph_dir = Path(graph_dir)
            except (TypeError, ValueError):
                self._last_graph_dir = None

        backend = self.settings.value("backend", "Matplotlib")
        if isinstance(backend, str):
            index = self.backend_combo.findText(
                backend, QtCore.Qt.MatchFlag.MatchFixedString
            )
            if index >= 0:
                self.backend_combo.setCurrentIndex(index)

        export_mode = self.settings.value("export_mode", "original")
        if isinstance(export_mode, str):
            index = self.export_mode_combo.findData(export_mode)
            if index >= 0:
                self.export_mode_combo.setCurrentIndex(index)

        style_value = self.settings.value("plot_style", "line")
        if isinstance(style_value, str):
            index = self.style_combo.findData(style_value)
            if index >= 0:
                self.style_combo.setCurrentIndex(index)

        rescale_value = self.settings.value("rescale_y", False)
        if isinstance(rescale_value, bool):
            self.rescale_checkbox.setChecked(rescale_value)
        elif rescale_value is not None:
            self.rescale_checkbox.setChecked(bool(rescale_value))

        dark_value = self.settings.value("plot_dark_mode", False)
        if isinstance(dark_value, bool):
            self.dark_mode_checkbox.setChecked(dark_value)
        elif dark_value is not None:
            self.dark_mode_checkbox.setChecked(bool(dark_value))

        geometry = self.settings.value("geometry")
        if isinstance(geometry, QtCore.QByteArray):
            try:
                self.restoreGeometry(geometry)
            except Exception:  # pragma: no cover - Qt versions differ
                pass

        window_state = self.settings.value("window_state")
        if isinstance(window_state, QtCore.QByteArray):
            try:
                self.restoreState(window_state)
            except Exception:  # pragma: no cover - Qt versions differ
                pass

    def _save_settings(self) -> None:
        self.settings.setValue("sources", self.path_edit.text())
        self.settings.setValue("backend", self.backend_combo.currentText())
        self.settings.setValue("export_mode", self.export_mode_combo.currentData())
        self.settings.setValue("plot_style", self.style_combo.currentData())
        self.settings.setValue("rescale_y", self.rescale_checkbox.isChecked())
        self.settings.setValue("plot_dark_mode", self.dark_mode_checkbox.isChecked())
        if self.last_export_path:
            self.settings.setValue("last_export_path", str(self.last_export_path))
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("window_state", self.saveState())
        self.settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._save_settings()
        super().closeEvent(event)

    def _choose_files(self) -> None:
        start_dir = self.path_edit.text() or str(Path.home())
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select VSM files",
            start_dir,
            "VSM data (*.VSM-Hys-Data);;All files (*)",
        )
        if not files:
            return
        self.path_edit.setText(";".join(files))
        self._load_measurements(show_warning=False)
        self._save_settings()

    def _choose_folder(self) -> None:
        start_dir = self.path_edit.text() or str(Path.home())
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select folder with VSM files",
            start_dir,
        )
        if not directory:
            return
        paths = _find_vsm_files(Path(directory))
        if paths:
            self.path_edit.setText(";".join(str(path) for path in paths))
        else:
            self.path_edit.setText(directory)
        self._load_measurements(show_warning=False)
        self._save_settings()

    def _handle_manual_path_entry(self) -> None:
        text = self.path_edit.text().strip()
        if not text:
            return
        self._load_measurements(show_warning=False)
        self._save_settings()

    def _open_files_from_menu(self) -> None:
        self._choose_files()

    def _open_folder_from_menu(self) -> None:
        self._choose_folder()

    def _selected_paths(self) -> List[Path]:
        text = self.path_edit.text().strip()
        if not text:
            return []
        return [Path(part) for part in text.split(";") if part]

    def _focus_measurement_tab(
        self, item: QtWidgets.QTreeWidgetItem, column: int
    ) -> None:
        if column != 0:
            return
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not data:
            return
        temperature, _path = data
        if temperature is None:
            return
        target_label = f"{temperature:g}"
        for index in range(self.tab_widget.count()):
            label = self.tab_widget.tabText(index)
            if label.startswith(target_label):
                self.tab_widget.setCurrentIndex(index)
                break

    def _populate_project_tree(self) -> None:
        self.project_tree.blockSignals(True)
        self.project_tree.clear()
        groups: Dict[float | None, QtWidgets.QTreeWidgetItem] = {}
        for measurement in sorted(
            self.measurements,
            key=lambda m: (
                float("inf") if m.temperature is None else float(m.temperature),
                float("inf") if m.angle is None else float(m.angle),
                m.path.name.lower(),
            ),
        ):
            temp_key = measurement.temperature
            parent = groups.get(temp_key)
            if parent is None:
                label = (
                    "Unknown temperature"
                    if measurement.temperature is None
                    else f"{measurement.temperature:g} °C"
                )
                parent = QtWidgets.QTreeWidgetItem([label, ""])
                parent.setData(
                    0,
                    QtCore.Qt.ItemDataRole.UserRole,
                    (measurement.temperature, None),
                )
                parent.setExpanded(True)
                groups[temp_key] = parent
                self.project_tree.addTopLevelItem(parent)

            angle_label = (
                "Unknown angle"
                if measurement.angle is None
                else f"{measurement.angle:g}°"
            )
            details = measurement.path.name
            child = QtWidgets.QTreeWidgetItem([angle_label, details])
            child.setData(
                0,
                QtCore.Qt.ItemDataRole.UserRole,
                (measurement.temperature, measurement.path),
            )
            parent.addChild(child)

        self.project_tree.expandAll()
        self.project_tree.blockSignals(False)

    def _populate_worksheets(self) -> None:
        self.worksheet_tabs.clear()
        self._worksheet_models.clear()
        for measurement in self.measurements:
            model = WorksheetModel(measurement)
            self._worksheet_models[measurement.path] = model
            view = QtWidgets.QTableView()
            view.setModel(model)
            view.setSelectionBehavior(
                QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
            )
            view.setSelectionMode(
                QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
            )
            view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda pos, table=view: self._open_table_menu(table, pos)
            )
            view.horizontalHeader().setStretchLastSection(True)
            view.verticalHeader().setVisible(False)

            container = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(view)

            tab_name = measurement.path.stem
            if measurement.temperature is not None and measurement.angle is not None:
                tab_name = f"{measurement.temperature:g}°C @ {measurement.angle:g}°"
            self.worksheet_tabs.addTab(container, tab_name)

    def _open_table_menu(
        self, table: QtWidgets.QTableView, pos: QtCore.QPoint
    ) -> None:
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
        self._append_log(
            f"Deleted {len(rows)} row(s) from {model._measurement.path.name}."
        )
        self._generate_plots()

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
            self._refresh_descriptor_legend(descriptor, force_layout=True)
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        if self.tab_widget.currentWidget() is tab:
            self._rebuild_object_manager_for_tab(tab)

    def _clear_tab_list(self, tabs: List[QtWidgets.QWidget]) -> None:
        for tab in tabs:
            index = self.tab_widget.indexOf(tab)
            if index >= 0:
                self.tab_widget.removeTab(index)
            self._canvas_by_tab.pop(tab, None)
            self._axes_by_tab.pop(tab, None)
            self._tab_descriptors.pop(tab, None)
            for key in [key for key in self._object_items.keys() if key[0] is tab]:
                self._object_items.pop(key, None)
        tabs.clear()
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        self._rebuild_object_manager_for_tab(self.tab_widget.currentWidget())

    def _update_save_graph_enabled(self, *_: object) -> None:
        current = self.tab_widget.currentWidget()
        enabled = bool(current and current in self._canvas_by_tab)
        self.save_graph_button.setEnabled(enabled)

    def _update_normalize_enabled(self) -> None:
        tab = self.tab_widget.currentWidget()
        descriptor = self._tab_descriptors.get(tab) if tab is not None else None
        enabled = bool(descriptor and descriptor.lines)
        self.normalize_button.setEnabled(enabled)
        self.popout_button.setEnabled(enabled)

    def _handle_current_tab_changed(self, index: int) -> None:
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        tab = self.tab_widget.widget(index) if index >= 0 else None
        self._rebuild_object_manager_for_tab(tab)
    # ------------------------------------------------------------------ data loading
    def _load_measurements(self, *, show_warning: bool = True) -> None:
        self.measurements.clear()
        self.project_tree.clear()
        self._clear_tab_list(self._temperature_tab_widgets)
        self._clear_tab_list(self._metrics_angle_tabs)
        self._clear_tab_list(self._metrics_temperature_tabs)
        self._clear_tab_list(self._overlay_tab_widgets)
        self.tab_widget.clear()
        self._canvas_by_tab.clear()
        self._axes_by_tab.clear()
        self._tab_descriptors.clear()
        self._update_save_graph_enabled()
        self.log_view.clear()
        self._last_prepared_groups = {}
        self._last_rescale_info = {}
        self._last_axes = None
        self._last_rescale_enabled = False
        self._line_visibility = {}
        self._plot_tabs = {}
        self._object_items = {}
        self._worksheet_models.clear()
        self._reset_object_manager()
        self.worksheet_tabs.clear()
        self._plotted_series_exports = {}
        self._metrics_by_temperature = {}
        self._metrics_by_angle = {}
        self._metric_column_names = {}
        self.temperature_combo.blockSignals(True)
        self.temperature_combo.clear()
        self.temperature_combo.addItem("All temperatures", None)
        self.temperature_combo.blockSignals(False)
        self.plot_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.popout_button.setEnabled(False)
        self.metrics_angle_button.setEnabled(False)
        self.metrics_temperature_button.setEnabled(False)
        self.export_metrics_button.setEnabled(False)
        self._update_metric_controls()
        self._update_normalize_enabled()

        paths = self._selected_paths()
        if not paths:
            if show_warning:
                QtWidgets.QMessageBox.warning(
                    self, "VSM Hysteresis Loops", "Select at least one VSM file to load."
                )
            return

        total_loaded = 0
        plottable = 0
        common_columns: Dict[str, int] | None = None
        for path in paths:
            if not path.exists():
                self._append_log(f"Skipping missing file: {path}")
                continue
            try:
                df = _read_vsm_file(path)
            except Exception as exc:
                self._append_log(f"Failed to parse {path.name}: {exc}")
                continue
            temperature = _parse_temperature(path)
            angle = _parse_angle(path)

            derived_angle, derived_temperature = _derive_metadata_from_dataframe(df)
            recovered: List[str] = []
            if angle is None and derived_angle is not None:
                angle = derived_angle
                recovered.append("angle")
            if temperature is None and derived_temperature is not None:
                temperature = derived_temperature
                recovered.append("temperature")

            measurement = VSMMeasurement(path=path, temperature=temperature, angle=angle, data=df)
            self.measurements.append(measurement)
            total_loaded += 1

            if temperature is None or angle is None:
                if recovered:
                    self._append_log(
                        f"{path.name}: recovered {', '.join(recovered)} from data columns but metadata remains incomplete; TXT export only."
                    )
                else:
                    self._append_log(
                        f"Could not parse complete metadata from {path.name}; available for TXT export."
                    )
            else:
                plottable += 1
                if recovered:
                    self._append_log(
                        f"{path.name}: using recovered metadata ({angle:g}° @ {temperature:g} °C)."
                    )
                else:
                    self._append_log(
                        f"{path.name}: {angle:g}° @ {temperature:g} °C."
                    )
            column_set = {col for col in df.columns if pd.api.types.is_numeric_dtype(df[col])}
            if common_columns is None:
                common_columns = {col: idx for idx, col in enumerate(df.columns) if col in column_set}
            else:
                common_columns = {col: idx for col, idx in common_columns.items() if col in column_set}

        if total_loaded == 0:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No VSM measurements could be loaded.",
            )
            return

        self.measurements.sort(
            key=lambda m: (
                float('inf') if m.temperature is None else m.temperature,
                float('inf') if m.angle is None else m.angle,
            )
        )

        self._populate_project_tree()
        self._populate_worksheets()

        unique_temperatures = sorted({m.temperature for m in self.measurements if m.temperature is not None})
        for temp in unique_temperatures:
            self.temperature_combo.addItem(f"{temp:g} °C", temp)

        if plottable:
            self._append_log(
                f"Loaded {total_loaded} VSM measurement(s); {plottable} have full metadata."
            )
        else:
            self._append_log(
                "Loaded VSM tables without angle/temperature metadata; plotting is disabled but TXT export is available."
            )

        candidate_columns: List[str]
        if common_columns:
            candidate_columns = list(common_columns.keys())
        elif self.measurements:
            candidate_columns = list(self.measurements[0].data.columns)
        else:
            candidate_columns = []
        if candidate_columns:
            self._populate_axis_combos(candidate_columns)

        self.plot_button.setEnabled(plottable > 0)
        self.export_button.setEnabled(True)
        self._save_settings()

    def _populate_axis_combos(self, labels: List[str]) -> None:
        numeric_labels = [label for label in labels if label]
        preferred_x = [
            "Applied Field [Oe]",
            "Applied Field",
            "Applied Field For Plot",
        ]
        preferred_y = [
            "Signal X direction [emu]",
            "Signal parallel with sample",
            "Signal Magnitude",
            "Moment [emu]",
        ]

        stored_x, stored_y = self._stored_axes

        def _choose(
            preferences: Iterable[str],
            combo: QtWidgets.QComboBox,
            stored: str | None,
        ) -> None:
            if stored and stored in numeric_labels:
                combo.setCurrentText(stored)
                return
            for pref in preferences:
                matches = [label for label in numeric_labels if pref.lower() in label.lower()]
                if matches:
                    combo.setCurrentText(matches[0])
                    return
            if combo.count():
                combo.setCurrentIndex(0)

        for combo in (self.x_axis_combo, self.y_axis_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(numeric_labels)
            combo.blockSignals(False)
        _choose(preferred_x, self.x_axis_combo, stored_x)
        _choose(preferred_y, self.y_axis_combo, stored_y)
        self._store_axis_selection()

    # ------------------------------------------------------------------ plotting helpers
    def _store_axis_selection(self) -> None:
        if not hasattr(self, "x_axis_combo") or not hasattr(self, "y_axis_combo"):
            return
        x_axis = self.x_axis_combo.currentText().strip()
        y_axis = self.y_axis_combo.currentText().strip()
        self._stored_axes = (
            x_axis or None,
            y_axis or None,
        )
        if x_axis:
            self.settings.setValue("x_axis", x_axis)
        if y_axis:
            self.settings.setValue("y_axis", y_axis)

    def _generate_plots(self) -> None:
        if not self.measurements:
            QtWidgets.QMessageBox.warning(self, "VSM Hysteresis Loops", "Load VSM measurements first.")
            return
        x_axis = self.x_axis_combo.currentText()
        y_axis = self.y_axis_combo.currentText()
        if not x_axis or not y_axis:
            QtWidgets.QMessageBox.warning(self, "VSM Hysteresis Loops", "Select X and Y axes for plotting.")
            return

        target_temp = self.temperature_combo.currentData()
        groups: Dict[float, List[VSMMeasurement]] = {}
        for measurement in self.measurements:
            if measurement.temperature is None or measurement.angle is None:
                continue
            if target_temp is not None and measurement.temperature != target_temp:
                continue
            if x_axis not in measurement.data.columns or y_axis not in measurement.data.columns:
                self._append_log(f"Skipping {measurement.path.name} because it lacks the selected axes.")
                continue
            groups.setdefault(measurement.temperature, []).append(measurement)

        if not groups:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No measurements match the selected filters and axes.",
            )
            return

        prepared_groups: Dict[float, List[tuple[VSMMeasurement, pd.DataFrame]]] = {}
        for temperature, measurement_list in sorted(groups.items()):
            prepared: List[tuple[VSMMeasurement, pd.DataFrame]] = []
            for measurement in sorted(measurement_list, key=lambda m: m.angle):
                subset = (
                    measurement.data[[x_axis, y_axis]]
                    .apply(pd.to_numeric, errors="coerce")
                    .dropna()
                )
                if subset.empty:
                    self._append_log(
                        f"{measurement.path.name}: no numeric data for the selected axes; skipped."
                    )
                    continue
                prepared.append((measurement, subset))
            if prepared:
                prepared_groups[temperature] = prepared

        if not prepared_groups:
            self._reset_object_manager()
            self._update_angle_overlay_options({})
            self._append_log(
                "No numeric data matched the selected axes; nothing to plot."
            )
            self._update_normalize_enabled()
            return

        self._update_angle_overlay_options(prepared_groups)

        rescale_enabled = self.rescale_checkbox.isChecked()
        rescale_info: Dict[float, Dict[Path, RescaleResult]] = {}
        if rescale_enabled:
            for temperature, entries in prepared_groups.items():
                rescale_map = _apply_rescaling(
                    [(measurement.path, subset) for measurement, subset in entries],
                    x_axis,
                    y_axis,
                )
                if not rescale_map:
                    self._append_log(
                        f"{temperature:g} °C: unable to compute rescaling for {y_axis}; keeping original values."
                    )
                    continue
                rescale_info[temperature] = rescale_map
                for measurement, _ in entries:
                    result = rescale_map.get(measurement.path)
                    if result is None:
                        continue
                    if result.replacement is not None:
                        self._append_log(
                            f"{measurement.path.name}: generated gradient for {y_axis} spanning {result.target_left:.3g} to {result.target_right:.3g}."
                        )
                        continue
                    if not result.applied:
                        self._append_log(
                            f"{measurement.path.name}: insufficient variation to rescale {y_axis}; original values kept at {result.source_left:.3g}."
                        )
                        continue
                    inversion_note = " (inverted)" if result.scale < 0 else ""
                    self._append_log(
                        f"{measurement.path.name}: rescaled {y_axis} with scale {result.scale:.3g}{inversion_note} "
                        f"and offset {result.offset:.3g}; targets {result.target_left:.3g} to {result.target_right:.3g}."
                    )

        plot_exports: Dict[tuple[float, float], PlotSeriesExport] = {}
        metric_records: List[tuple[float, float, MetricResult]] = []
        coercivity_debug_entries: Dict[float, List[Dict[str, float]]] = {}
        remanence_debug_entries: Dict[float, List[Dict[str, float]]] = {}
        self._metric_results = {}
        for temperature, entries in prepared_groups.items():
            for measurement, subset in entries:
                if measurement.angle is None:
                    continue
                export_subset = subset.copy()
                rescale_applied = False
                result = rescale_info.get(temperature, {}).get(measurement.path) if rescale_enabled else None
                if rescale_enabled and result is not None:
                    if result.replacement is not None:
                        replacement = result.replacement.reindex(export_subset.index)
                        export_subset[y_axis] = replacement.to_numpy()
                        rescale_applied = True
                    elif result.applied:
                        export_subset[y_axis] = export_subset[y_axis] * result.scale + result.offset
                        rescale_applied = True
                if export_subset.empty:
                    continue
                key = (float(temperature), float(measurement.angle))
                plot_exports[key] = PlotSeriesExport(
                    temperature=float(temperature),
                    angle=float(measurement.angle),
                    data=export_subset[[x_axis, y_axis]].copy(),
                    x_axis=x_axis,
                    y_axis=y_axis,
                    rescaled=rescale_applied,
                    source=measurement.path,
                )
                metrics = _calculate_metrics(
                    export_subset[[x_axis, y_axis]], x_axis, y_axis
                )
                metric_records.append(
                    (
                        float(temperature),
                        float(measurement.angle),
                        metrics,
                    )
                )
                self._metric_results[key] = metrics

                if metrics.coercivity is None or metrics.coercivity_pair is None:
                    self._append_log(
                        (
                            "Unable to determine coercivity for "
                            f"{measurement.path.name} at {temperature:g} °C and "
                            f"{float(measurement.angle):g}°; no zero crossings found."
                        ),
                        level="error",
                    )
                if metrics.remanence is None or metrics.remanence_pair is None:
                    self._append_log(
                        (
                            "Unable to determine remanence for "
                            f"{measurement.path.name} at {temperature:g} °C and "
                            f"{float(measurement.angle):g}°; no zero crossings found."
                        ),
                        level="error",
                    )

                raw_neg = math.nan
                raw_pos = math.nan
                if metrics.coercivity_raw_pair:
                    neg_value, pos_value = metrics.coercivity_raw_pair
                    if neg_value is not None and math.isfinite(neg_value):
                        raw_neg = float(neg_value)
                    if pos_value is not None and math.isfinite(pos_value):
                        raw_pos = float(pos_value)

                sym_neg = math.nan
                sym_pos = math.nan
                if metrics.coercivity_pair:
                    neg_value, pos_value = metrics.coercivity_pair
                    if neg_value is not None and math.isfinite(neg_value):
                        sym_neg = float(neg_value)
                    if pos_value is not None and math.isfinite(pos_value):
                        sym_pos = float(pos_value)

                original = math.nan
                if metrics.coercivity_raw_pair:
                    neg_value, pos_value = metrics.coercivity_raw_pair
                    candidates = [
                        value
                        for value in (pos_value, neg_value)
                        if value is not None and math.isfinite(value)
                    ]
                    if candidates:
                        positive_candidates = [value for value in candidates if value >= 0]
                        if positive_candidates:
                            original = float(positive_candidates[0])
                        else:
                            original = float(abs(candidates[0]))

                corrected = math.nan
                if metrics.coercivity_pair:
                    _, pos_value = metrics.coercivity_pair
                    if pos_value is not None and math.isfinite(pos_value):
                        corrected = float(pos_value)

                coercivity_debug_entries.setdefault(float(temperature), []).append(
                    {
                        "angle": float(measurement.angle),
                        "raw_neg": raw_neg,
                        "raw_pos": raw_pos,
                        "sym_neg": sym_neg,
                        "sym_pos": sym_pos,
                        "original": original,
                        "corrected": corrected,
                    }
                )

                rem_raw_neg = math.nan
                rem_raw_pos = math.nan
                if metrics.remanence_raw_pair:
                    neg_value, pos_value = metrics.remanence_raw_pair
                    if neg_value is not None and math.isfinite(neg_value):
                        rem_raw_neg = float(neg_value)
                    if pos_value is not None and math.isfinite(pos_value):
                        rem_raw_pos = float(pos_value)

                rem_sym_neg = math.nan
                rem_sym_pos = math.nan
                if metrics.remanence_pair:
                    neg_value, pos_value = metrics.remanence_pair
                    if neg_value is not None and math.isfinite(neg_value):
                        rem_sym_neg = float(neg_value)
                    if pos_value is not None and math.isfinite(pos_value):
                        rem_sym_pos = float(pos_value)

                rem_original = math.nan
                if metrics.remanence_raw_pair:
                    neg_value, pos_value = metrics.remanence_raw_pair
                    candidates = [
                        value
                        for value in (pos_value, neg_value)
                        if value is not None and math.isfinite(value)
                    ]
                    if candidates:
                        positive_candidates = [value for value in candidates if value >= 0]
                        if positive_candidates:
                            rem_original = float(positive_candidates[0])
                        else:
                            rem_original = float(abs(candidates[0]))

                rem_corrected = math.nan
                if metrics.remanence_pair:
                    _, pos_value = metrics.remanence_pair
                    if pos_value is not None and math.isfinite(pos_value):
                        rem_corrected = float(pos_value)

                remanence_debug_entries.setdefault(float(temperature), []).append(
                    {
                        "angle": float(measurement.angle),
                        "raw_neg": rem_raw_neg,
                        "raw_pos": rem_raw_pos,
                        "sym_neg": rem_sym_neg,
                        "sym_pos": rem_sym_pos,
                        "original": rem_original,
                        "corrected": rem_corrected,
                    }
                )

        self._plotted_series_exports = plot_exports

        _, x_unit = _split_column_label(x_axis)
        _, y_unit = _split_column_label(y_axis)
        if metric_records:
            (
                self._metrics_by_temperature,
                self._metrics_by_angle,
                self._metric_column_names,
            ) = _aggregate_metrics(metric_records, x_unit=x_unit, y_unit=y_unit)
        else:
            self._metrics_by_temperature = {}
            self._metrics_by_angle = {}
            self._metric_column_names = {}

        def _build_debug_payload(
            entries: Dict[float, List[Dict[str, float]]],
            unit: str,
            metric_key: str,
        ) -> tuple[Dict[float, pd.DataFrame], Dict[str, str]]:
            if not entries:
                return {}, {}
            spec = METRIC_DEBUG_SPECS.get(metric_key)
            if spec is None:
                return {}, {}
            metric_label = spec["label"]
            angle_label = _format_column_with_unit("Angle", "deg")
            raw_neg_label = _format_column_with_unit("Raw crossing (-)", unit)
            raw_pos_label = _format_column_with_unit("Raw crossing (+)", unit)
            sym_neg_label = _format_column_with_unit("Symmetrised (-)", unit)
            sym_pos_label = _format_column_with_unit("Symmetrised (+)", unit)
            metric_lower = metric_label.lower()
            original_label = _format_column_with_unit(f"Original {metric_lower}", unit)
            corrected_label = _format_column_with_unit(f"Corrected {metric_lower}", unit)

            tables: Dict[float, pd.DataFrame] = {}
            for temperature, rows in entries.items():
                df = pd.DataFrame(rows)
                df = df.sort_values(by="angle")
                df.rename(
                    columns={
                        "angle": angle_label,
                        "raw_neg": raw_neg_label,
                        "raw_pos": raw_pos_label,
                        "sym_neg": sym_neg_label,
                        "sym_pos": sym_pos_label,
                        "original": original_label,
                        "corrected": corrected_label,
                    },
                    inplace=True,
                )
                tables[temperature] = df

            column_map = {
                "angle": angle_label,
                "raw_neg": raw_neg_label,
                "raw_pos": raw_pos_label,
                "sym_neg": sym_neg_label,
                "sym_pos": sym_pos_label,
                "original": original_label,
                "corrected": corrected_label,
            }
            return tables, column_map

        self._metric_debug_tables = {}
        self._metric_debug_columns = {}
        for metric_key, entries, unit in (
            ("coercivity", coercivity_debug_entries, x_unit),
            ("remanence", remanence_debug_entries, y_unit),
        ):
            tables, columns = _build_debug_payload(entries, unit, metric_key)
            self._metric_debug_tables[metric_key] = tables
            self._metric_debug_columns[metric_key] = columns

        for metric_key, window in list(self._metric_debug_windows.items()):
            ensure_app_theme(window)
            window.update_data(
                self._metric_debug_tables.get(metric_key, {}),
                self._metric_debug_columns.get(metric_key, {}),
            )

        self._update_metric_controls()

        self._last_prepared_groups = prepared_groups
        self._last_rescale_info = rescale_info
        self._last_axes = (x_axis, y_axis)
        self._last_rescale_enabled = rescale_enabled

        backend_choice = self.backend_combo.currentText()
        render_matplotlib = wants_matplotlib(backend_choice)
        export_origin = wants_origin(backend_choice)

        self.tab_widget.clear()

        if render_matplotlib:
            self._render_matplotlib(prepared_groups, rescale_info, x_axis, y_axis, rescale_enabled)
        else:
            self.tab_widget.setVisible(False)

        if export_origin:
            self._export_origin(prepared_groups, rescale_info, x_axis, y_axis, rescale_enabled)

        if not render_matplotlib and not export_origin:
            self._append_log("No backend selected; nothing generated.")

        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        self._rebuild_object_manager_for_tab(self.tab_widget.currentWidget())

    def _handle_metric_debug_closed(self, metric: str, *_: Any) -> None:
        self._metric_debug_windows.pop(metric, None)

    def _show_metric_debug(self, metric: str) -> None:
        spec = METRIC_DEBUG_SPECS.get(metric)
        if spec is None:
            return
        window = self._metric_debug_windows.get(metric)
        if window is None:
            window = MetricDebugWindow(spec["label"], spec["title"], self)
            window.destroyed.connect(partial(self._handle_metric_debug_closed, metric))
            self._metric_debug_windows[metric] = window

        ensure_app_theme(window)
        window.update_data(
            self._metric_debug_tables.get(metric, {}),
            self._metric_debug_columns.get(metric, {}),
        )
        window.show()
        window.raise_()
        window.activateWindow()

    def _compute_rescale_lookup(self, x_axis: str, y_axis: str) -> Dict[Path, RescaleResult]:
        grouped: Dict[float, List[VSMMeasurement]] = {}
        for measurement in self.measurements:
            if measurement.temperature is None:
                continue
            if x_axis not in measurement.data.columns or y_axis not in measurement.data.columns:
                continue
            grouped.setdefault(measurement.temperature, []).append(measurement)

        lookup: Dict[Path, RescaleResult] = {}
        for measurement_list in grouped.values():
            entries: List[tuple[Path, pd.DataFrame]] = []
            for measurement in measurement_list:
                subset = (
                    measurement.data[[x_axis, y_axis]]
                    .apply(pd.to_numeric, errors="coerce")
                    .dropna()
                )
                if subset.empty:
                    continue
                entries.append((measurement.path, subset))
            rescale_map = _apply_rescaling(entries, x_axis, y_axis)
            lookup.update(rescale_map)
        return lookup

    def _line_style_kwargs(self) -> Dict[str, Any]:
        style = self.style_combo.currentData()
        if style == "line_markers":
            return {"linestyle": "-", "marker": "o", "markersize": 4}
        return {"linestyle": "-"}

    def _apply_plot_theme(self, axes: Any) -> None:
        """Apply the current light/dark theme to ``axes``."""

        dark = self.dark_mode_checkbox.isChecked()
        if dark:
            bg = "#121212"
            fg = "#f0f0f0"
            grid = "#404040"
        else:
            bg = "#ffffff"
            fg = "#202020"
            grid = "#d0d0d0"

        try:
            axes.set_facecolor(bg)
            axes.figure.set_facecolor(bg)
        except Exception:  # pragma: no cover - backend differences
            pass

        try:
            for spine in getattr(axes, "spines", {}).values():
                spine.set_color(fg)
        except Exception:  # pragma: no cover - backend differences
            pass

        try:
            axes.tick_params(colors=fg)
            axes.xaxis.label.set_color(fg)
            axes.yaxis.label.set_color(fg)
            axes.title.set_color(fg)
        except Exception:  # pragma: no cover - backend differences
            pass

        try:
            axes.grid(True, color=grid)
        except Exception:  # pragma: no cover - backend differences
            pass

    def _style_legend(self, legend: Any | None) -> None:
        """Restyle ``legend`` to match the current theme."""

        if legend is None:
            return
        dark = self.dark_mode_checkbox.isChecked()
        fg = "#f0f0f0" if dark else "#202020"
        bg = "#1e1e1e" if dark else "#ffffff"

        try:
            for text in legend.get_texts():
                text.set_color(fg)
        except Exception:  # pragma: no cover - backend differences
            pass

        try:
            frame = legend.get_frame()
            frame.set_facecolor(bg)
            frame.set_edgecolor(fg if dark else "#4c4c4c")
        except Exception:  # pragma: no cover - backend differences
            pass

    def _restyle_plots(self) -> None:
        """Reapply the chosen theme to existing Matplotlib tabs."""

        for tab_state in self._plot_tabs.values():
            self._apply_plot_theme(tab_state.axes)
            legend = getattr(tab_state.axes, "legend_", None)
            self._style_legend(legend)
            try:
                tab_state.canvas.draw_idle()
            except Exception:  # pragma: no cover - backend differences
                pass

        handled_canvases = {state.canvas for state in self._plot_tabs.values()}
        for descriptor in self._tab_descriptors.values():
            canvas = descriptor.canvas
            if canvas in handled_canvases:
                continue
            self._apply_plot_theme(descriptor.axes)
            legend = getattr(descriptor.axes, "legend_", None)
            self._style_legend(legend)
            try:
                descriptor.axes.figure.tight_layout()
            except Exception:  # pragma: no cover - backend differences
                pass
            try:
                canvas.draw_idle()
            except Exception:  # pragma: no cover - backend differences
                pass
            handled_canvases.add(canvas)

        for tab, axes in list(self._axes_by_tab.items()):
            canvas = self._canvas_by_tab.get(tab)
            if canvas is None or canvas in handled_canvases:
                continue
            self._apply_plot_theme(axes)
            legend = getattr(axes, "legend_", None)
            self._style_legend(legend)
            try:
                axes.figure.tight_layout()
            except Exception:  # pragma: no cover - backend differences
                pass
            try:
                canvas.draw_idle()
            except Exception:  # pragma: no cover - backend differences
                pass

    def _refresh_tab_legend(self, tab_state: PlotTabState, *, draw: bool = True) -> None:
        legend = getattr(tab_state.axes, "legend_", None)
        if legend is not None:
            try:
                legend.remove()
            except Exception:  # pragma: no cover - matplotlib backend specific
                pass
        visible_lines = [line for line in tab_state.lines.values() if line.get_visible()]
        labels = [line.get_label() for line in visible_lines]
        legend = tab_state.axes.legend(visible_lines, labels, loc="best")
        self._apply_plot_theme(tab_state.axes)
        self._style_legend(legend)
        try:
            tab_state.axes.figure.tight_layout()
        except Exception:  # pragma: no cover - backend specific
            pass
        if draw:
            try:
                tab_state.canvas.draw_idle()
            except Exception:  # pragma: no cover - backend specific
                pass

    def _refresh_descriptor_legend(
        self,
        descriptor: TabDescriptor,
        *,
        force_layout: bool = False,
    ) -> None:
        legend = getattr(descriptor.axes, "legend_", None)
        if legend is not None:
            try:
                legend.remove()
            except Exception:  # pragma: no cover - matplotlib backend specific
                pass

        visible_states = [
            state for state in descriptor.lines.values() if state.line.get_visible()
        ]
        if visible_states:
            legend = descriptor.axes.legend(
                [state.line for state in visible_states],
                [state.label for state in visible_states],
                loc="best",
            )
            self._apply_plot_theme(descriptor.axes)
            self._style_legend(legend)
        else:
            legend = None
            self._apply_plot_theme(descriptor.axes)

        if force_layout or not descriptor.layout_initialized:
            try:
                descriptor.axes.figure.tight_layout()
            except Exception:  # pragma: no cover - backend specific
                pass
            descriptor.layout_initialized = True
        try:
            descriptor.canvas.draw_idle()
        except Exception:  # pragma: no cover - backend specific
            pass

    def _rescale_y_limits(
        self,
        descriptor: TabDescriptor,
        *,
        symmetric: bool = False,
        include_zero: bool = False,
    ) -> None:
        arrays: List[np.ndarray] = []
        for state in descriptor.lines.values():
            if not state.line.get_visible():
                continue
            data = np.asarray(state.line.get_ydata(), dtype=float)
            if data.size == 0:
                continue
            finite = data[np.isfinite(data)]
            if finite.size == 0:
                continue
            arrays.append(finite)

        if not arrays:
            return

        combined = np.concatenate(arrays)
        finite = combined[np.isfinite(combined)]
        if finite.size == 0:
            return

        min_y = float(np.min(finite))
        max_y = float(np.max(finite))

        if symmetric:
            bound = max(abs(min_y), abs(max_y))
            if not math.isfinite(bound):
                return
            if bound == 0:
                bound = 1.0
            padding = max(bound * 0.05, 0.05)
            lower = -bound - padding
            upper = bound + padding
        else:
            if not math.isfinite(min_y) or not math.isfinite(max_y):
                return
            span = max_y - min_y
            if span == 0:
                reference = max(abs(max_y), abs(min_y), 1.0)
                padding = reference * 0.05
                lower = min_y - padding
                upper = max_y + padding
            else:
                padding = max(span * 0.05, 0.05)
                lower = min_y - padding
                upper = max_y + padding

        if include_zero:
            lower = min(lower, 0.0)
            upper = max(upper, 0.0)

        try:
            descriptor.axes.set_ylim(lower, upper)
        except Exception:  # pragma: no cover - backend specific
            pass

    def _reset_object_manager(self) -> None:
        self.object_tree.blockSignals(True)
        self.object_tree.clear()
        self.object_tree.blockSignals(False)
        self._object_items.clear()
        self._reset_overlay_controls()

    def _reset_overlay_controls(self) -> None:
        self.angle_overlay_list.blockSignals(True)
        self.angle_overlay_list.clear()
        self.angle_overlay_list.blockSignals(False)
        self.angle_overlay_button.setEnabled(False)
        self._update_metric_controls()

    def _rebuild_object_manager_for_tab(
        self,
        tab: QtWidgets.QWidget | None,
    ) -> None:
        self.object_tree.blockSignals(True)
        self.object_tree.clear()
        self._object_items.clear()

        descriptor = self._tab_descriptors.get(tab) if tab is not None else None
        if descriptor is None:
            self.object_tree.blockSignals(False)
            return

        root = QtWidgets.QTreeWidgetItem([descriptor.root_label])
        root.setFlags(root.flags() & ~QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        self.object_tree.addTopLevelItem(root)

        for key, line_state in sorted(
            descriptor.lines.items(),
            key=self._object_manager_sort_key,
        ):
            child = QtWidgets.QTreeWidgetItem([line_state.label])
            child.setFlags(
                child.flags()
                | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            state = (
                QtCore.Qt.CheckState.Checked
                if line_state.line.get_visible()
                else QtCore.Qt.CheckState.Unchecked
            )
            child.setCheckState(0, state)
            child.setData(
                0,
                QtCore.Qt.ItemDataRole.UserRole,
                (tab, key),
            )
            root.addChild(child)
            self._object_items[(tab, key)] = child

        self.object_tree.expandAll()
        self.object_tree.blockSignals(False)

    @staticmethod
    def _object_manager_sort_key(item: tuple[tuple[str, float | str], GraphLineState]) -> tuple[int, float | str, str]:
        """Sort object manager entries numerically when possible."""

        key, state = item
        numeric: float | None = None

        if isinstance(key, tuple) and len(key) == 2:
            candidate = key[1]
            if isinstance(candidate, (int, float)):
                numeric = float(candidate)

        if numeric is None:
            match = re.search(r"-?\d+(?:\.\d+)?", state.label)
            if match is not None:
                try:
                    numeric = float(match.group())
                except ValueError:
                    numeric = None

        if numeric is not None:
            return (0, numeric, state.label.lower())
        return (1, state.label.lower(), state.label)

    def _update_angle_overlay_options(
        self,
        prepared_groups: Dict[float, List[tuple[VSMMeasurement, pd.DataFrame]]],
    ) -> None:
        selected_angles = set(self._selected_overlay_angles())
        self.angle_overlay_list.blockSignals(True)
        self.angle_overlay_list.clear()
        available_angles = sorted(
            {
                float(measurement.angle)
                for entries in prepared_groups.values()
                for measurement, _ in entries
                if measurement.angle is not None
            }
        )
        for angle in available_angles:
            item = QtWidgets.QListWidgetItem(f"{angle:g}°")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, angle)
            item.setSelected(angle in selected_angles)
            self.angle_overlay_list.addItem(item)
        self.angle_overlay_list.blockSignals(False)
        self._update_overlay_button_state()

    def _selected_overlay_angles(self) -> List[float]:
        angles: List[float] = []
        for item in self.angle_overlay_list.selectedItems():
            value = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if value is not None:
                angles.append(float(value))
        return angles

    def _update_overlay_button_state(self) -> None:
        has_angles = bool(self._selected_overlay_angles())
        self.angle_overlay_button.setEnabled(has_angles)
        self._update_metric_controls()

    def _update_metric_controls(self) -> None:
        has_metrics = bool(getattr(self, "_metrics_by_temperature", {}))
        has_angles = has_metrics and bool(self._selected_overlay_angles())
        self.metrics_angle_button.setEnabled(has_metrics)
        self.export_metrics_button.setEnabled(has_metrics)
        self.metrics_temperature_button.setEnabled(has_metrics and has_angles)

    def _handle_object_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        payload = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not payload:
            return
        tab, key = payload
        descriptor = self._tab_descriptors.get(tab)
        if descriptor is None:
            return
        line_state = descriptor.lines.get(tuple(key))
        if line_state is None:
            return
        visible = item.checkState(0) == QtCore.Qt.CheckState.Checked
        line_state.line.set_visible(visible)
        if descriptor.kind == "temperature":
            temperature = descriptor.metadata.get("temperature")
            if isinstance(temperature, (int, float)):
                visibility = self._line_visibility.setdefault(float(temperature), {})
                try:
                    _, angle_value = key
                except (TypeError, ValueError):
                    angle_value = None
                if isinstance(angle_value, (int, float)):
                    visibility[float(angle_value)] = visible
        self._refresh_descriptor_legend(descriptor)

    def _toggle_line_visibility(self, temperature: float, angle: float, visible: bool) -> None:
        tab_state = self._plot_tabs.get(float(temperature))
        if tab_state is None:
            return
        line = tab_state.lines.get(float(angle))
        if line is None:
            return
        line.set_visible(visible)
        self._refresh_tab_legend(tab_state)

        self._line_visibility.setdefault(float(temperature), {})[float(angle)] = visible

        for tab, descriptor in self._tab_descriptors.items():
            if descriptor.kind != "temperature":
                continue
            meta_temp = descriptor.metadata.get("temperature")
            if not isinstance(meta_temp, (int, float)):
                continue
            if not math.isclose(float(meta_temp), float(temperature), rel_tol=0.0, abs_tol=1e-6):
                continue
            state = descriptor.lines.get(("angle", float(angle)))
            if state is None:
                continue
            state.line.set_visible(visible)
            self._refresh_descriptor_legend(descriptor)
            break

    def _plot_angle_overlays(self) -> None:
        if not self._last_prepared_groups or not self._last_axes:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Generate plots before creating angle overlays.",
            )
            return

        angles = self._selected_overlay_angles()
        if not angles:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Select at least one angle to plot across temperatures.",
            )
            return

        x_axis, y_axis = self._last_axes
        rescale_enabled = self._last_rescale_enabled
        rescale_info = self._last_rescale_info if rescale_enabled else {}
        line_kwargs = self._line_style_kwargs()

        self._clear_tab_list(self._overlay_tab_widgets)
        any_tab = False

        for angle in angles:
            fig = Figure(figsize=(11.5, 7.8))
            ax = fig.add_subplot(111)
            lines: Dict[tuple[str, float | str], GraphLineState] = {}
            plotted = False
            for temperature, entries in sorted(self._last_prepared_groups.items()):
                for measurement, subset in entries:
                    if measurement.angle is None:
                        continue
                    if not math.isclose(float(measurement.angle), angle, abs_tol=0.05):
                        continue
                    series_y = subset[y_axis]
                    if rescale_enabled:
                        result = rescale_info.get(temperature, {}).get(measurement.path)
                        if result is not None:
                            if result.replacement is not None:
                                replacement = result.replacement.reindex(subset.index)
                                series_y = replacement
                            else:
                                series_y = series_y * result.scale + result.offset
                    numeric_x = pd.to_numeric(subset[x_axis], errors="coerce").to_numpy()
                    numeric_y = pd.to_numeric(series_y, errors="coerce").to_numpy()
                    if numeric_x.size == 0 or numeric_y.size == 0:
                        continue
                    line, = ax.plot(
                        numeric_x,
                        numeric_y,
                        label=f"{temperature:g} °C",
                        **line_kwargs,
                    )
                    lines[("temperature", float(temperature))] = GraphLineState(
                        key=("temperature", float(temperature)),
                        label=f"{temperature:g} °C",
                        line=line,
                        base_x=numeric_x,
                        base_y=numeric_y,
                    )
                    plotted = True
            if plotted:
                ax.set_title(f"{angle:g}° across temperatures")
                ax.set_xlabel(x_axis)
                ax.set_ylabel(y_axis)
                self._apply_plot_theme(ax)
                legend = ax.legend(loc="best")
                if legend is not None:
                    self._style_legend(legend)
            else:
                ax.text(
                    0.5,
                    0.5,
                    "No data for this angle.",
                    transform=ax.transAxes,
                    ha="center",
                    va="center",
                )
                self._apply_plot_theme(ax)

            try:
                fig.tight_layout()
            except Exception:  # pragma: no cover - backend dependent
                pass

            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(canvas)
            title = f"Angle {angle:g}° overlays"
            descriptor = TabDescriptor(
                kind="overlay",
                title=f"{y_axis} vs {x_axis} at {angle:g}°",
                root_label=title,
                x_label=x_axis,
                y_label=y_axis,
                canvas=canvas,
                axes=ax,
                lines=lines,
                metadata={"angle": float(angle)},
            )
            self.tab_widget.addTab(tab, title)
            self._overlay_tab_widgets.append(tab)
            self._register_plot_tab(tab, canvas, ax, descriptor)
            any_tab = True

        if any_tab:
            self._append_log("Added angle overlay tabs to the viewer.")
        else:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No overlays could be generated for the selected angles.",
            )

    def _write_plotted_series(self, target_dir: Path) -> int:
        exported = 0
        for (temperature, angle), entry in sorted(self._plotted_series_exports.items()):
            visible = self._line_visibility.get(temperature, {}).get(angle, True)
            if not visible or entry.data.empty:
                continue
            temp_label = _temperature_subfolder_name(temperature)
            angle_label = _clean_folder_name(f"Angle{angle:+g}deg")
            x_label = _clean_folder_name(entry.x_axis) or "X"
            y_label = _clean_folder_name(entry.y_axis) or "Y"
            filename = target_dir / f"{temp_label}_{angle_label}_{y_label}_vs_{x_label}.txt"
            counter = 2
            while filename.exists():
                filename = target_dir / f"{temp_label}_{angle_label}_{y_label}_vs_{x_label}_{counter}.txt"
                counter += 1
            metadata = {
                "temperature": temperature,
                "angle": angle,
                "rescaled": entry.rescaled,
                "source": entry.source.name,
                "x_axis": entry.x_axis,
                "y_axis": entry.y_axis,
                "summary": "Hysteresis curve prepared for plotting",
            }
            axis_roles = {
                entry.x_axis: "X axis",
                entry.y_axis: "Y axis",
            }
            try:
                _write_origin_ascii(filename, entry.data, metadata=metadata, axis_roles=axis_roles)
            except Exception as exc:
                self._append_log(f"Failed to export {entry.source.name}: {exc}")
                continue
            exported += 1

        return exported

    def _plot_metrics_vs_angle(self) -> None:
        if not self._metrics_by_temperature:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Generate plots to compute derived metrics first.",
            )
            return

        column_map = self._metric_column_names
        angle_column = column_map.get("angle")
        if not angle_column:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No angle metadata is available for metric plots.",
            )
            return

        metrics = ["coercivity", "remanence", "saturation"]
        self._clear_tab_list(self._metrics_angle_tabs)
        style = self._line_style_kwargs()
        any_tab = False

        long_angle, angle_unit = _split_column_label(angle_column)
        x_label = _format_column_with_unit(long_angle, angle_unit)

        for metric in metrics:
            column = column_map.get(metric)
            if not column:
                continue
            fig = Figure(figsize=(11.5, 7.8))
            ax = fig.add_subplot(111)
            plotted = False
            lines: Dict[tuple[str, float | str], GraphLineState] = {}
            for temperature, table in sorted(self._metrics_by_temperature.items()):
                if column not in table.columns:
                    continue
                subset = table[[angle_column, column]].dropna()
                if subset.empty:
                    continue
                numeric_x = subset[angle_column].to_numpy()
                numeric_y = subset[column].to_numpy()
                line, = ax.plot(
                    numeric_x,
                    numeric_y,
                    label=f"{temperature:g} °C",
                    **style,
                )
                lines[("temperature", float(temperature))] = GraphLineState(
                    key=("temperature", float(temperature)),
                    label=f"{temperature:g} °C",
                    line=line,
                    base_x=numeric_x,
                    base_y=numeric_y,
                )
                plotted = True
            long_name, unit = _split_column_label(column)
            ax.set_xlabel(x_label)
            ax.set_ylabel(_format_column_with_unit(long_name, unit))
            ax.set_title(f"{long_name} vs angle")
            self._apply_plot_theme(ax)
            if plotted:
                legend = ax.legend(loc="best")
                if legend is not None:
                    self._style_legend(legend)
            else:
                ax.text(
                    0.5,
                    0.5,
                    "No data available for this metric.",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )
            try:
                fig.tight_layout()
            except Exception:  # pragma: no cover - backend dependent
                pass

            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            tab_title = f"{long_name} vs angle"
            descriptor = TabDescriptor(
                kind="metrics_angle",
                title=tab_title,
                root_label=tab_title,
                x_label=x_label,
                y_label=_format_column_with_unit(long_name, unit),
                canvas=canvas,
                axes=ax,
                lines=lines,
                metadata={"metric": long_name},
            )
            self.tab_widget.addTab(tab, tab_title)
            self._metrics_angle_tabs.append(tab)
            self._register_plot_tab(tab, canvas, ax, descriptor)
            any_tab = True

        if any_tab:
            self._append_log("Added metric tabs versus angle to the viewer.")
        else:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No metric plots could be generated for the selected data.",
            )

    def _plot_metrics_vs_temperature(self) -> None:
        if not self._metrics_by_angle:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Generate plots to compute derived metrics first.",
            )
            return

        angles = self._selected_overlay_angles()
        if not angles:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Select at least one angle to plot metrics versus temperature.",
            )
            return

        column_map = self._metric_column_names
        temperature_column = column_map.get("temperature")
        if not temperature_column:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No temperature metadata is available for metric plots.",
            )
            return

        metrics = ["coercivity", "remanence", "saturation"]
        self._clear_tab_list(self._metrics_temperature_tabs)
        style = self._line_style_kwargs()
        any_tab = False

        long_temp, temp_unit = _split_column_label(temperature_column)
        x_label = _format_column_with_unit(long_temp, temp_unit)

        for metric in metrics:
            column = column_map.get(metric)
            if not column:
                continue
            fig = Figure(figsize=(11.5, 7.8))
            ax = fig.add_subplot(111)
            plotted = False
            lines: Dict[tuple[str, float | str], GraphLineState] = {}
            for angle in angles:
                table = self._metrics_by_angle.get(float(angle))
                if table is None or column not in table.columns:
                    continue
                subset = table[[temperature_column, column]].dropna()
                if subset.empty:
                    continue
                numeric_x = subset[temperature_column].to_numpy()
                numeric_y = subset[column].to_numpy()
                line, = ax.plot(
                    numeric_x,
                    numeric_y,
                    label=f"{angle:g}°",
                    **style,
                )
                lines[("angle", float(angle))] = GraphLineState(
                    key=("angle", float(angle)),
                    label=f"{angle:g}°",
                    line=line,
                    base_x=numeric_x,
                    base_y=numeric_y,
                )
                plotted = True
            long_name, unit = _split_column_label(column)
            ax.set_xlabel(x_label)
            ax.set_ylabel(_format_column_with_unit(long_name, unit))
            ax.set_title(f"{long_name} vs temperature")
            self._apply_plot_theme(ax)
            if plotted:
                legend = ax.legend(loc="best")
                if legend is not None:
                    self._style_legend(legend)
            else:
                ax.text(
                    0.5,
                    0.5,
                    "No data available for this metric.",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )
            try:
                fig.tight_layout()
            except Exception:  # pragma: no cover - backend dependent
                pass

            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            tab_title = f"{long_name} vs temperature"
            descriptor = TabDescriptor(
                kind="metrics_temperature",
                title=tab_title,
                root_label=tab_title,
                x_label=x_label,
                y_label=_format_column_with_unit(long_name, unit),
                canvas=canvas,
                axes=ax,
                lines=lines,
                metadata={"metric": long_name},
            )
            self.tab_widget.addTab(tab, tab_title)
            self._metrics_temperature_tabs.append(tab)
            self._register_plot_tab(tab, canvas, ax, descriptor)
            any_tab = True

        if any_tab:
            self._append_log("Added metric tabs versus temperature to the viewer.")
        else:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No metric plots could be generated for the selected angles.",
            )

    def _export_metrics(self) -> None:
        if not self._metrics_by_temperature and not self._metrics_by_angle:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Generate plots to compute derived metrics first.",
            )
            return

        start_directory = (
            str(self.last_export_path)
            if self.last_export_path is not None
            else self.path_edit.text()
        )
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select export folder",
            start_directory or str(Path.home()),
        )
        if not directory:
            return

        dialog = ExportOptionsDialog(self, Path(directory), suggestion="VSM_Metrics")
        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        target_dir = dialog.selected_directory()
        target_dir.mkdir(parents=True, exist_ok=True)

        exported = 0
        angle_column = self._metric_column_names.get("angle")
        temperature_column = self._metric_column_names.get("temperature")
        metrics = [
            self._metric_column_names.get("coercivity"),
            self._metric_column_names.get("remanence"),
            self._metric_column_names.get("saturation"),
        ]

        for temperature, table in sorted(self._metrics_by_temperature.items()):
            if angle_column not in table.columns:
                continue
            data = table.dropna(how="all")
            if data.empty:
                continue
            filename = target_dir / f"metrics_{_temperature_subfolder_name(temperature)}_vs_angle.txt"
            counter = 2
            while filename.exists():
                filename = target_dir / f"metrics_{_temperature_subfolder_name(temperature)}_vs_angle_{counter}.txt"
                counter += 1
            metadata = {
                "temperature": temperature,
                "summary": "Derived hysteresis metrics vs angle",
                "source": "VSM metrics aggregation",
                "x_axis": angle_column,
                "y_axis": ", ".join(filter(None, metrics)),
            }
            axis_roles = {angle_column: "Angle"}
            try:
                _write_origin_ascii(filename, data, metadata=metadata, axis_roles=axis_roles)
            except Exception as exc:
                self._append_log(f"Failed to export metrics for {temperature:g} °C: {exc}")
                continue
            exported += 1

        selected_angles = self._selected_overlay_angles()
        angle_targets = selected_angles or sorted(self._metrics_by_angle.keys())
        for angle in angle_targets:
            table = self._metrics_by_angle.get(float(angle))
            if table is None or temperature_column not in table.columns:
                continue
            data = table.dropna(how="all")
            if data.empty:
                continue
            angle_label = _clean_folder_name(f"Angle{float(angle):+g}deg")
            filename = target_dir / f"metrics_{angle_label}_vs_temperature.txt"
            counter = 2
            while filename.exists():
                filename = target_dir / f"metrics_{angle_label}_vs_temperature_{counter}.txt"
                counter += 1
            metadata = {
                "angle": float(angle),
                "summary": "Derived hysteresis metrics vs temperature",
                "source": "VSM metrics aggregation",
                "x_axis": temperature_column,
                "y_axis": ", ".join(filter(None, metrics)),
            }
            axis_roles = {temperature_column: "Temperature"}
            try:
                _write_origin_ascii(filename, data, metadata=metadata, axis_roles=axis_roles)
            except Exception as exc:
                self._append_log(f"Failed to export metrics for {angle:g}°: {exc}")
                continue
            exported += 1

        if exported:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                f"Exported {exported} metric table(s) to {target_dir}",
            )
            self._append_log(f"Exported {exported} metric table(s) to {target_dir}")
            self.last_export_path = target_dir
            self.settings.setValue("last_export_path", str(target_dir))
            self.settings.sync()
        else:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No derived metric tables were exported.",
            )

    def _open_matplotlib_window(self) -> None:
        tab = self.tab_widget.currentWidget()
        if tab is None:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Select a plot tab before opening a Matplotlib window.",
            )
            return

        descriptor = self._tab_descriptors.get(tab)
        if descriptor is None or not descriptor.lines:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No Matplotlib plots are available for the selected tab.",
            )
            return

        try:
            import matplotlib.pyplot as plt
        except Exception as exc:  # pragma: no cover - GUI/runtime dependent
            QtWidgets.QMessageBox.warning(
                self,
                "VSM Hysteresis Loops",
                f"Matplotlib's interactive backend is unavailable: {exc}",
            )
            return

        fig, ax = plt.subplots(constrained_layout=True)
        plotted = False
        for state in descriptor.lines.values():
            if not state.line.get_visible():
                continue
            try:
                color = state.line.get_color()
            except Exception:
                color = None
            try:
                linestyle = state.line.get_linestyle()
            except Exception:
                linestyle = "-"
            try:
                marker = state.line.get_marker()
            except Exception:
                marker = "None"
            try:
                markersize = state.line.get_markersize()
            except Exception:
                markersize = None
            kwargs: Dict[str, Any] = {"label": state.label}
            if color is not None:
                kwargs["color"] = color
            if linestyle is not None:
                kwargs["linestyle"] = linestyle
            if marker is not None and marker != "None":
                kwargs["marker"] = marker
            if markersize is not None:
                kwargs["markersize"] = markersize
            ax.plot(state.line.get_xdata(), state.line.get_ydata(), **kwargs)
            plotted = True

        ax.set_xlabel(descriptor.x_label)
        ax.set_ylabel(descriptor.y_label)
        ax.set_title(descriptor.title)
        self._apply_plot_theme(ax)
        if plotted:
            legend = ax.legend(loc="best")
            self._style_legend(legend)
        try:  # pragma: no cover - backend dependent
            fig.canvas.manager.set_window_title(descriptor.title)
        except Exception:
            pass
        try:
            fig.canvas.draw_idle()
        except Exception:  # pragma: no cover - backend dependent
            pass

        if plotted:
            plt.show()
            self._append_log(f"Opened Matplotlib window for '{descriptor.title}'.")
        else:
            plt.close(fig)
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No Matplotlib plots are available for the selected tab.",
            )

    def _save_current_graph(self) -> None:
        tab = self.tab_widget.currentWidget()
        if tab is None:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Select a plot tab before saving a graph.",
            )
            return

        canvas = self._canvas_by_tab.get(tab)
        if canvas is None:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "The selected tab does not contain a Matplotlib graph to save.",
            )
            return

        index = self.tab_widget.currentIndex()
        tab_label = self.tab_widget.tabText(index) if index >= 0 else ""
        base_name = _clean_folder_name(tab_label or "VSM_Graph") or "VSM_Graph"

        start_dir: Path | None = self._last_graph_dir or self.last_export_path
        if start_dir is None:
            path_text = self.path_edit.text().strip()
            if path_text:
                try:
                    candidate = Path(path_text)
                    start_dir = candidate if candidate.is_dir() else candidate.parent
                except (OSError, ValueError):
                    start_dir = None
        if start_dir is None:
            start_dir = Path.home()
        elif not start_dir.exists() and not start_dir.parent.exists():
            start_dir = Path.home()

        if start_dir.is_file():
            start_dir = start_dir.parent

        default_path = start_dir / f"{base_name}.png"

        filter_map = {
            "PNG Image (*.png)": ".png",
            "PDF Document (*.pdf)": ".pdf",
            "SVG Image (*.svg)": ".svg",
        }
        filters = ";;".join(filter_map.keys()) + ";;All Files (*)"

        filename, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save graph",
            str(default_path),
            filters,
        )
        if not filename:
            return

        path = Path(filename)
        if not path.suffix:
            extension = filter_map.get(selected_filter, "") or ".png"
            path = path.with_suffix(extension)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            canvas.figure.savefig(path)
        except Exception as exc:  # pragma: no cover - backend specific
            QtWidgets.QMessageBox.warning(
                self,
                "VSM Hysteresis Loops",
                f"Failed to save graph: {exc}",
            )
            return

        self._last_graph_dir = path.parent
        self.settings.setValue("last_graph_dir", str(path.parent))
        self.settings.sync()
        self._append_log(f"Saved graph to {path}")

    def _normalize_current_graph(self) -> None:
        tab = self.tab_widget.currentWidget()
        if tab is None:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Select a plot tab before normalizing.",
            )
            return

        descriptor = self._tab_descriptors.get(tab)
        if descriptor is None or not descriptor.lines:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "The selected tab does not contain a Matplotlib graph to normalize.",
            )
            return

        if all(state.normalized for state in descriptor.lines.values()):
            for state in descriptor.lines.values():
                state.line.set_ydata(state.base_y)
                state.normalized = False

            stored_limits = descriptor.stored_limits.pop(_PRE_NORMALIZE_Y_KEY, None)
            if (
                stored_limits is not None
                and len(stored_limits) == 2
                and all(math.isfinite(value) for value in stored_limits)
            ):
                try:
                    descriptor.axes.set_ylim(*stored_limits)
                except Exception:  # pragma: no cover - backend specific
                    pass
            else:
                self._rescale_y_limits(descriptor)

            self._refresh_descriptor_legend(descriptor, force_layout=True)
            self._append_log("Restored the original scaling for the current graph.")
            return

        updated = False
        overall_min: float | None = None
        overall_max: float | None = None
        restore_limits: tuple[float, float] | None = None
        if _PRE_NORMALIZE_Y_KEY not in descriptor.stored_limits:
            try:
                current_limits = descriptor.axes.get_ylim()
            except Exception:  # pragma: no cover - backend specific
                current_limits = None
            if (
                isinstance(current_limits, tuple)
                and len(current_limits) == 2
                and all(isinstance(value, (int, float)) for value in current_limits)
            ):
                lower, upper = float(current_limits[0]), float(current_limits[1])
                if math.isfinite(lower) and math.isfinite(upper):
                    restore_limits = (lower, upper)

        for state in descriptor.lines.values():
            data = state.base_y
            if data.size == 0:
                continue
            finite = data[np.isfinite(data)]
            if finite.size == 0:
                continue
            max_value = np.max(np.abs(finite))
            if max_value == 0:
                continue
            normalized = np.divide(
                data,
                max_value,
                where=np.isfinite(data),
                out=np.full_like(data, np.nan),
            )
            state.line.set_ydata(normalized)
            state.normalized = True
            updated = True

            finite_normalized = normalized[np.isfinite(normalized)]
            if finite_normalized.size:
                min_value = float(np.min(finite_normalized))
                max_value = float(np.max(finite_normalized))
                if overall_min is None or min_value < overall_min:
                    overall_min = min_value
                if overall_max is None or max_value > overall_max:
                    overall_max = max_value

        if not updated:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No numeric data was available to normalize for the selected tab.",
            )
            return

        if restore_limits is not None:
            descriptor.stored_limits[_PRE_NORMALIZE_Y_KEY] = restore_limits

        symmetric = (
            overall_min is not None
            and overall_max is not None
            and overall_min < 0.0
            and overall_max > 0.0
        )
        self._rescale_y_limits(descriptor, symmetric=symmetric)
        self._refresh_descriptor_legend(descriptor, force_layout=True)
        self._append_log(
            "Normalized the current graph and rescaled the Y axis to fit the data."
        )

    def _export_txt(self) -> None:
        if not self.measurements:
            QtWidgets.QMessageBox.warning(self, "VSM Hysteresis Loops", "Load VSM measurements first.")
            return

        start_directory = (
            str(self.last_export_path)
            if self.last_export_path is not None
            else self.path_edit.text()
        )
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select export folder",
            start_directory or str(Path.home()),
        )
        if not directory:
            return

        base_dir = Path(directory)
        dialog = ExportOptionsDialog(
            self,
            base_dir,
            suggestion=_suggest_export_subfolder(self.measurements),
            allow_plot_axes=bool(self._plotted_series_exports),
        )
        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        target_dir = dialog.selected_directory()
        target_dir.mkdir(parents=True, exist_ok=True)

        scope = dialog.selected_scope()

        if scope == "plot_axes":
            if not self._plotted_series_exports:
                QtWidgets.QMessageBox.information(
                    self,
                    "VSM Hysteresis Loops",
                    "Generate plots before exporting plotted axes.",
                )
                return
            exported = self._write_plotted_series(target_dir)
            if exported:
                QtWidgets.QMessageBox.information(
                    self,
                    "VSM Hysteresis Loops",
                    f"Exported {exported} plotted series to {target_dir}",
                )
                self._append_log(f"Exported {exported} plotted series to {target_dir}")
                self.last_export_path = target_dir
                self.settings.setValue("last_export_path", str(target_dir))
                self.settings.sync()
            else:
                QtWidgets.QMessageBox.information(
                    self,
                    "VSM Hysteresis Loops",
                    "No plotted series matched the current visibility filters.",
                )
            return

        unique_temperatures = {
            measurement.temperature
            for measurement in self.measurements
            if measurement.temperature is not None
        }
        separate_by_temp = len(unique_temperatures) > 1

        export_mode = self.export_mode_combo.currentData()
        rescale_requested = export_mode == "rescaled"
        x_axis = self.x_axis_combo.currentText()
        y_axis = self.y_axis_combo.currentText()
        if rescale_requested and (not x_axis or not y_axis):
            QtWidgets.QMessageBox.warning(
                self,
                "VSM Hysteresis Loops",
                "Select X and Y axes before exporting rescaled data.",
            )
            rescale_requested = False

        rescale_lookup: Dict[Path, RescaleResult] = {}
        if rescale_requested:
            rescale_lookup = self._compute_rescale_lookup(x_axis, y_axis)
            if not rescale_lookup:
                self._append_log(
                    "Rescaled export requested but no transforms could be calculated; exporting original data."
                )
                rescale_requested = False

        exported = 0
        for measurement in self.measurements:
            base_name = measurement.path.stem or f"measurement_{exported + 1}"
            destination_dir = target_dir
            if separate_by_temp and measurement.temperature is not None:
                subfolder = _temperature_subfolder_name(measurement.temperature)
                destination_dir = target_dir / subfolder
                destination_dir.mkdir(parents=True, exist_ok=True)
            candidate = destination_dir / f"{base_name}.txt"
            counter = 2
            while candidate.exists():
                candidate = destination_dir / f"{base_name}_{counter}.txt"
                counter += 1
            try:
                export_rescaled = False
                df_to_write = measurement.data.copy()
                if rescale_requested:
                    result = rescale_lookup.get(measurement.path)
                    if result is None:
                        self._append_log(
                            f"{measurement.path.name}: no rescale transform available; exported original values."
                        )
                    elif result.replacement is not None:
                        if y_axis in df_to_write.columns:
                            replacement = result.replacement.reindex(df_to_write.index)
                            numeric = pd.to_numeric(df_to_write[y_axis], errors="coerce")
                            if not replacement.dropna().empty:
                                numeric.loc[replacement.dropna().index] = replacement.dropna().to_numpy()
                            df_to_write[y_axis] = numeric
                            export_rescaled = True
                        else:
                            self._append_log(
                                f"{measurement.path.name}: Y axis '{y_axis}' not present; exported original values."
                            )
                    elif not result.applied:
                        self._append_log(
                            f"{measurement.path.name}: insufficient variation to rescale {y_axis}; exported original values."
                        )
                    else:
                        if y_axis in df_to_write.columns:
                            numeric = pd.to_numeric(df_to_write[y_axis], errors="coerce")
                            df_to_write[y_axis] = numeric * result.scale + result.offset
                            export_rescaled = True
                        else:
                            self._append_log(
                                f"{measurement.path.name}: Y axis '{y_axis}' not present; exported original values."
                            )

                metadata = {
                    "temperature": measurement.temperature,
                    "angle": measurement.angle,
                    "rescaled": export_rescaled,
                    "source": measurement.path.name,
                    "x_axis": x_axis,
                    "y_axis": y_axis,
                    "summary": "Full hysteresis measurement export",
                }
                axis_roles: Dict[str, str] = {}
                if x_axis in df_to_write.columns:
                    axis_roles[x_axis] = "X axis"
                if y_axis in df_to_write.columns:
                    axis_roles[y_axis] = "Y axis"

                _write_origin_ascii(candidate, df_to_write, metadata=metadata, axis_roles=axis_roles)
                exported += 1
            except Exception as exc:
                self._append_log(f"Failed to export {measurement.path.name}: {exc}")

        if exported:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                f"Exported {exported} measurement(s) to {target_dir}",
            )
            self._append_log(f"Exported {exported} measurement(s) to {target_dir}")
            self.last_export_path = target_dir
            self.settings.setValue("last_export_path", str(target_dir))
            self.settings.sync()
        else:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No files were exported."
            )

    def _render_matplotlib(
        self,
        prepared_groups: Dict[float, List[tuple[VSMMeasurement, pd.DataFrame]]],
        rescale_info: Dict[float, Dict[Path, RescaleResult]],
        x_axis: str,
        y_axis: str,
        rescale_enabled: bool,
    ) -> None:
        self._clear_tab_list(self._temperature_tab_widgets)
        self._clear_tab_list(self._metrics_angle_tabs)
        self._clear_tab_list(self._metrics_temperature_tabs)
        self._clear_tab_list(self._overlay_tab_widgets)
        self.tab_widget.setVisible(True)
        self._plot_tabs = {}
        for temperature in list(self._line_visibility.keys()):
            if temperature not in prepared_groups:
                del self._line_visibility[temperature]
        line_kwargs = self._line_style_kwargs()
        for temperature, entries in sorted(prepared_groups.items()):
            fig = Figure(figsize=(11.5, 7.8))
            ax = fig.add_subplot(111)
            visibility = self._line_visibility.setdefault(temperature, {})
            lines: Dict[float, Any] = {}
            descriptor_lines: Dict[tuple[str, float | str], GraphLineState] = {}
            valid_angles: set[float] = set()
            for measurement, subset in entries:
                angle = float(measurement.angle)
                series_y = subset[y_axis]
                if rescale_enabled:
                    result = rescale_info.get(temperature, {}).get(measurement.path)
                    if result is not None:
                        if result.replacement is not None:
                            replacement = result.replacement.reindex(subset.index)
                            series_y = replacement
                        else:
                            series_y = series_y * result.scale + result.offset
                numeric_x = pd.to_numeric(subset[x_axis], errors="coerce").to_numpy()
                numeric_y = pd.to_numeric(series_y, errors="coerce").to_numpy()
                line, = ax.plot(
                    numeric_x,
                    numeric_y,
                    label=f"{measurement.angle:g}°",
                    **line_kwargs,
                )
                visible = visibility.get(angle, True)
                line.set_visible(visible)
                lines[angle] = line
                descriptor_lines[("angle", angle)] = GraphLineState(
                    key=("angle", angle),
                    label=f"{measurement.angle:g}°",
                    line=line,
                    base_x=numeric_x,
                    base_y=numeric_y,
                )
                valid_angles.add(angle)

            for angle in list(visibility.keys()):
                if angle not in valid_angles:
                    del visibility[angle]

            ax.set_xlabel(x_axis)
            ax.set_ylabel(y_axis)
            ax.set_title(f"{y_axis} vs {x_axis} at {temperature:g} °C")
            self._apply_plot_theme(ax)

            legend = None
            if lines:
                legend = ax.legend(loc="best")
                self._style_legend(legend)

            canvas = FigureCanvas(fig)
            tab_state = PlotTabState(axes=ax, canvas=canvas, lines=lines)
            self._plot_tabs[temperature] = tab_state

            tab = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(canvas)
            title = f"{temperature:g} °C"
            descriptor = TabDescriptor(
                kind="temperature",
                title=f"{y_axis} vs {x_axis} at {temperature:g} °C",
                root_label=title,
                x_label=x_axis,
                y_label=y_axis,
                canvas=canvas,
                axes=ax,
                lines=descriptor_lines,
                metadata={"temperature": float(temperature)},
            )
            self.tab_widget.addTab(tab, title)
            self._temperature_tab_widgets.append(tab)
            self._register_plot_tab(tab, canvas, ax, descriptor)
            if legend is None:
                self._refresh_tab_legend(tab_state)

        self._append_log("Finished generating Matplotlib hysteresis plots.")

    def _export_origin(
        self,
        prepared_groups: Dict[float, List[tuple[VSMMeasurement, pd.DataFrame]]],
        rescale_info: Dict[float, Dict[Path, RescaleResult]],
        x_axis: str,
        y_axis: str,
        rescale_enabled: bool,
    ) -> None:
        try:
            with origin_session() as op:
                schedule_origin_release()
                exported = 0
                for temperature, entries in sorted(prepared_groups.items()):
                    valid = []
                    for measurement, subset in entries:
                        series_y = subset[y_axis]
                        if rescale_enabled:
                            result = rescale_info.get(temperature, {}).get(measurement.path)
                            if result is not None:
                                series_y = series_y * result.scale + result.offset
                        export_subset = pd.DataFrame({
                            x_axis: subset[x_axis],
                            y_axis: series_y,
                        }).astype(float)
                        if export_subset.empty:
                            continue
                        valid.append((measurement, export_subset))
                    if not valid:
                        continue
                    try:
                        self._build_origin_group(op, temperature, valid, x_axis, y_axis)
                        exported += 1
                    except Exception as exc:
                        self._append_log(
                            f"Origin export failed for {temperature:g} °C: {exc}"
                        )
                if exported:
                    self._append_log(f"Sent {exported} temperature groups to Origin.")
                else:
                    self._append_log("No Origin plots were exported because all groups were empty.")
        except (ModuleNotFoundError, ImportError):
            self._append_log("OriginPro is not installed. Install originpro to enable Origin output.")
        except Exception as exc:
            self._append_log(f"Unexpected Origin error: {exc}")

    def _origin_book_name(self, temperature: float) -> str:
        label = f"VSM_{temperature:g}C"
        return "".join(ch if ch.isalnum() else "_" for ch in label)[:30]

    def _origin_graph_short_name(self, temperature: float) -> str:
        label = f"T{temperature:g}C"
        return "".join(ch if ch.isalnum() else "_" for ch in label)[:13]

    def _build_origin_group(
        self,
        origin_any: Any,
        temperature: float,
        entries: Sequence[Tuple[VSMMeasurement, pd.DataFrame]],
        x_axis: str,
        y_axis: str,
    ) -> None:
        book = origin_any.new_book('w', lname=self._origin_book_name(temperature))
        book.activate()

        graph = origin_any.new_graph(template='line')
        layer = graph[0] if graph else None
        if layer is None:
            return

        try:
            graph.lname = f"{temperature:g} °C"
        except Exception:
            pass
        try:
            graph.name = self._origin_graph_short_name(temperature)
        except Exception:
            pass

        for index, (measurement, subset) in enumerate(entries):
            if index < len(book):
                sheet = book[index]
            else:
                sheet = book.add_sheet()
            sheet.name = f"a{measurement.angle:g}"
            sheet.from_df(subset)
            try:
                sheet.cols_axis('XY')
            except Exception:
                pass
            for col, label in enumerate((x_axis, y_axis)):
                try:
                    sheet.set_label(col, label)
                except Exception:
                    pass
            comment = f"Angle {measurement.angle:g}°"
            safe_comment = self._escape_origin_text(comment)
            try:
                sheet.activate()
            except Exception:
                pass
            try:
                sheet.set_comment(1, comment)  # type: ignore[attr-defined]
            except Exception:
                pass
            for command in (
                f'wks.comment$="{safe_comment}";',
                f'wks.col2.comment$="{safe_comment}";',
            ):
                try:
                    origin_any.lt_exec(command)
                except Exception:
                    pass
            try:
                setattr(sheet, "comment", comment)
            except Exception:
                pass
            plot_obj = layer.add_plot(sheet, coly=1, colx=0, type='y')
            if plot_obj is not None:
                try:
                    plot_obj.legend = f"Angle {measurement.angle:g}°"
                except Exception:
                    pass

        try:
            graph.activate()
        except Exception:
            pass

        safe_x = self._escape_origin_text(x_axis)
        safe_y = self._escape_origin_text(y_axis)
        safe_title = self._escape_origin_text(
            f"{y_axis} vs {x_axis} at {temperature:g} °C"
        )

        for command in (
            'page.antialias=1;',
            'layer -aa 1;',
            f'lab -xb "{safe_x}";',
            f'lab -yl "{safe_y}";',
            f'title -s "{safe_title}";',
            'legend;'
        ):
            try:
                origin_any.lt_exec(command)
            except Exception:
                pass

        try:
            layer.rescale()
        except Exception:
            pass

    def _escape_origin_text(self, text: str) -> str:
        return text.replace("\"", "''")

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self.log_view and event.type() in {
            QtCore.QEvent.Type.Show,
            QtCore.QEvent.Type.FocusIn,
        }:
            self._clear_log_alert()
        return super().eventFilter(obj, event)

    def _handle_log_visibility(self, visible: bool) -> None:
        if visible:
            self._clear_log_alert()

    def _clear_log_alert(self) -> None:
        if getattr(self, "message_log_dock", None) is None:
            return
        if getattr(self, "_log_has_unread_errors", False):
            self._log_has_unread_errors = False
        self.message_log_dock.set_alert(False)

    def _append_log(self, message: str, *, level: Literal["info", "error"] = "info") -> None:
        derived_level = level
        if derived_level == "info":
            lowered = message.lower()
            if lowered.startswith("failed") or lowered.startswith("error") or lowered.startswith("unable"):
                derived_level = "error"

        self.log_view.appendPlainText(message)
        dock = getattr(self, "message_log_dock", None)
        if derived_level == "error":
            self.logger.error(message)
            visible = bool(dock and self.log_view.isVisible() and dock.isVisible())
            if visible:
                self._clear_log_alert()
            else:
                self._log_has_unread_errors = True
                if dock is not None:
                    dock.set_alert(True)
        else:
            self.logger.info(message)

def main() -> QtWidgets.QWidget | None:  # pragma: no cover - launcher helper
    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    ensure_app_theme(app)
    widget = VSMPlotter()
    widget.show()
    if created_app:
        app.exec()
        return None
    return widget

if __name__ == "__main__":  # pragma: no cover - manual execution
    main()

