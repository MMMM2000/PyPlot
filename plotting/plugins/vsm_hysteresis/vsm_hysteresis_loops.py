"""Visualise VSM hysteresis loops grouped by temperature and angle."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Sequence, Tuple, Hashable

import pandas as pd
import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

from plotting.pyplot.window import (
    PyPlotWindow,
    GraphLineState,
    GraphSelectionDialog,
    PlotTabState,
    TabDescriptor,
    WorksheetTableModel,
    WorksheetTableView,
    WorksheetColumnMeta,
    WorksheetData,
    OBJECT_TREE_STATE_ROLE,
)
from plotting.shared.utils import ensure_app_theme, format_annealing_title
from plotting.shared.origin import (
    origin_session,
    set_origin_axis_title,
    set_origin_graph_title,
)

HEADER_COLUMN_RE = re.compile(r"Column\s+\d+\s*:\s*(.+)")
WHITESPACE_RE = re.compile(r"[_\s]+")
ANGLE_RE = re.compile(r"(?<![A-Za-z0-9])a(-?(?:\d+(?:\.\d+)?°?)(?:-\d+)*)", re.IGNORECASE)
TEMP_RE = re.compile(r"T(-?(?:\d+(?:\.\d+)?°?)(?:-\d+)*)", re.IGNORECASE)
VSM_FILE_TOKEN_RE = re.compile(r"vsm-hys-data(?:$|[^0-9a-z])")
FIELD_ANGLE_RE = re.compile(
    r"Set Field Angle to\s+([-+]?\d+(?:\.\d+)?)(?:\s*°?\s*(?:\[?deg\]?|deg)?c?)?",
    re.IGNORECASE,
)
ANGLE_OFFSET_RE = re.compile(
    r"Sample Angle Offset\s*=\s*([-+]?\d+(?:\.\d+)?)(?:\s*°?\s*(?:\[?deg\]?|deg)?c?)?",
    re.IGNORECASE,
)
SET_TEMPERATURE_RE = re.compile(
    r"Set Sample Temperature to\s+([-+]?\d+(?:\.\d+)?)(?:\s*°?\s*(?:\[?deg\]?|deg)?c?)?",
    re.IGNORECASE,
)
FOLDER_SANITIZE_RE = re.compile(r"[^0-9A-Za-z._-]+")
SAMPLE_FRACTION_RE = re.compile(r"(?<=\b)(\d+)-(\d+)(?=\b)")

ORIGIN_PLOT_COLORS = [
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
]

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
    sample_name: str | None = None
    operator: str | None = None
    test_id: str | None = None
    header_metadata: Dict[str, Any] = field(default_factory=dict)


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
            "Plot loops to inspect raw metric crossings."
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


def _numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric Series even when duplicate column labels exist."""

    if column not in frame.columns:
        return pd.Series(dtype=float)
    values: pd.Series | pd.DataFrame = frame.loc[:, column]
    if isinstance(values, pd.DataFrame):
        values = values.iloc[:, 0]
    return pd.to_numeric(values, errors="coerce")


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
        series_y = _numeric_column(subset, y_axis)
        y_min = float(series_y.min()) if not series_y.empty else float(left)
        y_max = float(series_y.max()) if not series_y.empty else float(right)
        prepared.append((path, subset, float(left), float(right), y_min, y_max))

    if not prepared:
        return {}

    reference_entry = prepared[0]
    reference_span = abs(reference_entry[5] - reference_entry[4])
    max_span = max(abs(item[5] - item[4]) for item in prepared)
    if (
        reference_span < max_span * 0.1
        or _is_near_zero(reference_span, reference_entry[4], reference_entry[5])
    ):
        reference_entry = max(prepared, key=lambda item: abs(item[5] - item[4]))
        reference_span = abs(reference_entry[5] - reference_entry[4])
        if _is_near_zero(reference_span, reference_entry[4], reference_entry[5]):
            reference_entry = max(prepared, key=lambda item: abs(item[3] - item[2]))
    target_left = reference_entry[4]
    target_right = reference_entry[5]

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
            numeric_series = _numeric_column(subset, y_axis).dropna()
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
            if entry.sample_name:
                cleaned_sample = _clean_folder_name(entry.sample_name)
                if cleaned_sample:
                    return cleaned_sample
            stem = entry.path.name
        elif isinstance(entry, Path):
            stem = entry.name
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


_BASE_Y_LABEL_KEY = "base_y_label"
_BASE_TITLE_KEY = "base_title"
_PRE_NORMALIZE_Y_KEY = "pre_normalize_y"
_NORMALIZABLE_TAB_KINDS = {"temperature", "overlay"}
_LEGACY_ANY_SAMPLE = "*"
PlotGroupKey = tuple[str, float]


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


def _normalized_axis_label(label: str) -> str:
    """Return a normalised Y axis label without unit suffix."""

    base_label, _ = _split_column_label(label)
    cleaned = base_label.strip()
    if not cleaned:
        cleaned = "Signal"
    if cleaned.lower().startswith("normalized"):
        return cleaned
    return f"Normalized {cleaned}".strip()


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
    finite_candidates = [float(value) for value in candidates if math.isfinite(value)]
    if not finite_candidates:
        return None, None, None

    positives = sorted((value for value in finite_candidates if value > 0.0), key=abs)
    negatives = sorted((value for value in finite_candidates if value < 0.0), key=abs)

    if positives and negatives:
        pos_value = positives[0]
        neg_value = negatives[0]
        magnitude = (abs(pos_value) + abs(neg_value)) / 2.0
        sym_pair = (-float(magnitude), float(magnitude))
        raw_pair = (float(neg_value), float(pos_value))
        return float(magnitude), sym_pair, raw_pair

    ordered = sorted(finite_candidates, key=lambda value: (abs(value), value))
    if len(ordered) >= 2:
        first, second = ordered[:2]
        magnitude = (abs(first) + abs(second)) / 2.0
        sym_pair = (-float(magnitude), float(magnitude))
        raw_pair = (float(min(first, second)), float(max(first, second)))
        return float(magnitude), sym_pair, raw_pair

    only = ordered[0]
    magnitude = abs(only)
    sym_pair = (-float(magnitude), float(magnitude))
    if math.isclose(magnitude, 0.0, rel_tol=1e-12, abs_tol=1e-12):
        return 0.0, (0.0, 0.0), (0.0, 0.0)
    return float(magnitude), sym_pair, (float(only), None)


def _collect_crossings_x_at_y(
    x_values: np.ndarray, y_values: np.ndarray, target: float = 0.0
) -> List[float]:
    candidates: List[float] = []

    def _record(value: float) -> None:
        if not math.isfinite(value):
            return
        for existing in candidates:
            tolerance = max(1e-12, 1e-6 * max(abs(existing), abs(value)))
            if math.isclose(existing, value, abs_tol=tolerance):
                return
        candidates.append(value)

    for x0, y0, x1, y1 in zip(x_values[:-1], y_values[:-1], x_values[1:], y_values[1:]):
        if any(math.isnan(v) for v in (x0, x1, y0, y1)):
            continue
        delta0 = y0 - target
        delta1 = y1 - target
        scale = max(abs(y0), abs(y1), abs(target))
        zero_tol = max(1e-12, 1e-6 * scale)
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
            threshold = max(1e-12, 0.02 * scale)
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
                    segment_min = min(x0, x1) - 1e-12
                    segment_max = max(x0, x1) + 1e-12
                    if segment_min <= candidate <= segment_max:
                        _record(candidate)

    if len(candidates) == 1:
        finite_mask = np.isfinite(y_values)
        finite_y = y_values[finite_mask]
        finite_x = x_values[finite_mask]
        if finite_y.size:
            max_span = float(np.max(np.abs(finite_y))) if np.any(np.isfinite(finite_y)) else 0.0
            graze_threshold = max(1e-9, 0.05 * max_span)
            graze_index = int(np.argmin(np.abs(finite_y)))
            y_graze = float(finite_y[graze_index])
            if abs(y_graze - target) <= graze_threshold:
                def _project_zero(idx_from: int, idx_to: int) -> float | None:
                    y0 = float(finite_y[idx_from])
                    y1 = float(finite_y[idx_to])
                    x0 = float(finite_x[idx_from])
                    x1 = float(finite_x[idx_to])
                    if not math.isfinite(y0) or not math.isfinite(y1):
                        return None
                    if math.isclose(y0, y1, rel_tol=1e-12, abs_tol=1e-12):
                        return None
                    return x0 + (target - y0) * (x1 - x0) / (y1 - y0)

                graze_candidate = None
                if graze_index > 0:
                    sign_prev = math.copysign(1.0, finite_y[graze_index - 1])
                    sign_curr = math.copysign(1.0, y_graze)
                    if sign_prev == sign_curr:
                        graze_candidate = _project_zero(graze_index - 1, graze_index)
                if graze_candidate is None and graze_index + 1 < finite_y.size:
                    sign_next = math.copysign(1.0, finite_y[graze_index + 1])
                    sign_curr = math.copysign(1.0, y_graze)
                    if sign_next == sign_curr:
                        graze_candidate = _project_zero(graze_index, graze_index + 1)
                if graze_candidate is not None and math.isfinite(graze_candidate):
                    candidates = [graze_candidate]

    return candidates


def _collect_crossings_y_at_x(
    x_values: np.ndarray, y_values: np.ndarray, target: float = 0.0
) -> List[float]:
    candidates: List[float] = []

    def _record(value: float) -> None:
        if not math.isfinite(value):
            return
        for existing in candidates:
            tolerance = max(1e-12, 1e-6 * max(abs(existing), abs(value)))
            if math.isclose(existing, value, abs_tol=tolerance):
                return
        candidates.append(value)

    for x0, y0, x1, y1 in zip(x_values[:-1], y_values[:-1], x_values[1:], y_values[1:]):
        if any(math.isnan(v) for v in (x0, x1, y0, y1)):
            continue
        delta0 = x0 - target
        delta1 = x1 - target
        scale = max(abs(x0), abs(x1), abs(target))
        zero_tol = max(1e-12, 1e-6 * scale)
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
            threshold = max(1e-12, 0.02 * scale)
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
                    segment_min = min(x0, x1) - 1e-12
                    segment_max = max(x0, x1) + 1e-12
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
    x_series = numeric[x_axis]
    y_series = numeric[y_axis]
    if isinstance(x_series, pd.DataFrame):
        x_series = x_series.iloc[:, 0]
    if isinstance(y_series, pd.DataFrame):
        y_series = y_series.iloc[:, 0]
    ordered = pd.concat([x_series, y_series], axis=1).dropna()
    if ordered.empty:
        return MetricResult(None, None, None)
    x_values = ordered.iloc[:, 0].to_numpy(dtype=float)
    y_values = ordered.iloc[:, 1].to_numpy(dtype=float)
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

def _normalise_metadata_value(
    value: float | None,
    *,
    decimals: int = 3,
    snap_threshold: float = 0.45,
) -> float | None:
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
    if math.isclose(rounded, nearest_integer, abs_tol=snap_threshold):
        rounded = float(nearest_integer)
    else:
        rounded = round(rounded, decimals)

    if rounded == 0:
        return 0.0
    return rounded


def _normalise_angle_value(value: float | None) -> float | None:
    return _normalise_metadata_value(value, decimals=3, snap_threshold=0.45)


def _normalise_temperature_value(value: float | None) -> float | None:
    return _normalise_metadata_value(value, decimals=2, snap_threshold=0.2)

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

    def _split_vsm_tokens(raw_line: str) -> List[str]:
        line = raw_line.strip()
        if not line:
            return []
        if "," in line and " " not in line and "\t" not in line:
            parts = [part.strip() for part in line.split(",")]
        elif ";" in line and " " not in line and "\t" not in line:
            parts = [part.strip() for part in line.split(";")]
        elif "\t" in line and " " not in line:
            parts = [part.strip() for part in line.split("\t")]
        else:
            parts = line.split()
        tokens: List[str] = []
        for part in parts:
            if not part:
                continue
            cleaned = part.strip()
            if "," in cleaned and "." not in cleaned:
                cleaned = cleaned.replace(",", ".")
            tokens.append(cleaned)
        return tokens

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

            tokens = _split_vsm_tokens(stripped)
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

    data_rows: List[List[str]] = []
    for section in reversed(sections):
        if section:
            data_rows = section
            break
    if not data_rows:
        expected_fallback: Optional[int] = (
            len(columns) if columns else (len(inline_header) if inline_header else None)
        )
        length_buckets: Dict[int, List[List[str]]] = {}
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("@"):
                    continue
                tokens = _split_vsm_tokens(stripped)
                if not tokens:
                    continue
                if not all(_looks_numeric(token) for token in tokens):
                    continue
                length_buckets.setdefault(len(tokens), []).append(tokens)
        if expected_fallback is not None and expected_fallback in length_buckets:
            data_rows = length_buckets[expected_fallback]
        elif length_buckets:
            best_len = max(length_buckets.keys(), key=lambda key: len(length_buckets[key]))
            data_rows = length_buckets[best_len]
        if not data_rows:
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
    if "," in token and "." not in token:
        token = token.replace(",", ".")
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

    return _normalise_angle_value(angle), _normalise_temperature_value(temperature)

def _safe_float(token: str) -> float | None:
    token = token.strip()
    token = token.replace("°", "")
    token = re.sub(r"\s*\[.*?\]\s*$", "", token)
    token = re.sub(r"(degc?|degrees?|c)$", "", token, flags=re.IGNORECASE).strip()
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


def _prefer_filename_temperature(
    filename_value: float | None,
    header_value: float | None,
) -> float | None:
    if filename_value is None:
        return header_value
    if not math.isfinite(filename_value):
        return header_value
    return filename_value

@lru_cache(maxsize=256)
def _metadata_from_file(path: Path) -> tuple[float | None, float | None]:
    angle, temperature = _metadata_from_filename(path)
    filename_angle = angle
    filename_temperature = temperature
    explicit_angle: float | None = None
    explicit_temperature: float | None = None

    try:
        handle = path.open("r", encoding="utf-8", errors="ignore")
    except OSError:
        return _normalise_angle_value(angle), _normalise_temperature_value(temperature)

    with handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if explicit_angle is None:
                match = FIELD_ANGLE_RE.search(stripped)
                if match:
                    candidate = _safe_float(match.group(1))
                    if candidate is not None:
                        explicit_angle = candidate
                        angle = candidate
            if explicit_temperature is None:
                match = SET_TEMPERATURE_RE.search(stripped)
                if match:
                    candidate = _safe_float(match.group(1))
                    if candidate is not None:
                        explicit_temperature = candidate
                        temperature = candidate
            if angle is None:
                match = ANGLE_RE.search(stripped)
                if not match:
                    match = ANGLE_OFFSET_RE.search(stripped)
                if match:
                    candidate = _safe_float(match.group(1))
                    if candidate is not None:
                        angle = candidate
            if temperature is None:
                match = TEMP_RE.search(stripped)
                if match:
                    candidate = _safe_float(match.group(1))
                    if candidate is not None:
                        temperature = candidate
            if stripped.startswith("@@Data") or stripped.startswith("@@Final Manipulated Data"):
                break

    if explicit_temperature is not None:
        temperature = explicit_temperature
    else:
        temperature = _prefer_filename_temperature(filename_temperature, temperature)
    return _normalise_angle_value(angle), _normalise_temperature_value(temperature)


@lru_cache(maxsize=256)
def _header_metadata(path: Path) -> Dict[str, Any]:
    """Extract header metadata such as sample and operator information."""

    metadata: Dict[str, Any] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return metadata
    except Exception:
        return metadata

    iterator = iter(lines)
    for raw_line in iterator:
        stripped = raw_line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if stripped.startswith("@@COMMENTS"):
            comments: List[str] = []
            for comment_line in iterator:
                comment_stripped = comment_line.strip()
                if comment_stripped.upper().startswith("@@END COMMENTS"):
                    break
                if comment_stripped.startswith("@@"):
                    continue
                if comment_stripped.startswith("@"):
                    continue
                if comment_stripped:
                    comments.append(comment_stripped)
            if comments:
                metadata["comment_lines"] = comments
            continue
        if stripped.startswith("@@") and "DATA" in upper:
            break
        if not stripped.startswith("@"):
            continue
        token = stripped.lstrip("@")
        if ":" not in token:
            continue
        key_raw, value_raw = token.split(":", 1)
        key_clean = WHITESPACE_RE.sub(" ", key_raw).strip().lower()
        value = value_raw.strip()
        if not value:
            continue
        normalized = key_clean.replace(" ", "_")
        if normalized in {"samplename", "sample_name", "sample"}:
            metadata["sample_name"] = value
        elif normalized in {"testid", "test_id"}:
            metadata["test_id"] = value
        elif normalized == "operator":
            metadata["operator"] = value
        elif normalized in {"date", "time"}:
            metadata[normalized] = value
        else:
            extra = metadata.setdefault("header", {})
            extra[normalized] = value
    return metadata


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
        self.setWindowTitle("TXT Data Export")
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

        scope_label = QtWidgets.QLabel("Data to export")
        layout.addWidget(scope_label)

        self.scope_combo = QtWidgets.QComboBox()
        self.scope_combo.addItem("Visible plotted series", "plot_axes")
        plot_index = self.scope_combo.count() - 1
        self.scope_combo.addItem("All plotted series (including hidden)", "all")
        if not self._allow_plot_axes:
            model = self.scope_combo.model()
            if hasattr(model, "item"):
                item = model.item(plot_index)
                if item is not None:
                    item.setEnabled(False)
            self.scope_combo.setCurrentIndex(self.scope_combo.count() - 1)
        else:
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
                    "TXT Data Export",
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


class VSMPlotter(PyPlotWindow):
    """Render hysteresis loops for VSM-HYS-DATA files."""

    help_topic = "vsm_hysteresis_loops"
    PROJECT_VERSION = 1
    PROJECT_EXTENSION = ".pypj"
    PROJECT_CODE = "VSM_Hysteresis_Loops"
    PROJECT_SETTINGS_PREFIX = "vsm_project"
    _WINDOW_STATE_SIGNATURE_KEY = "window_state_signature"

    def __init__(self) -> None:
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
        self._last_prepared_groups: Dict[PlotGroupKey, List[tuple[VSMMeasurement, pd.DataFrame]]] = {}
        self._last_rescale_info: Dict[PlotGroupKey, Dict[Path, RescaleResult]] = {}
        self._last_axes: tuple[str, str] | None = None
        self._last_rescale_enabled = False
        self._line_visibility: Dict[PlotGroupKey, Dict[float, bool]] = {}
        self._worksheet_models: Dict[Hashable, WorksheetTableModel] = {}
        self._plotted_series_exports: Dict[tuple[str, float, float], PlotSeriesExport] = {}
        self._metrics_by_temperature: Dict[float, pd.DataFrame] = {}
        self._metrics_by_angle: Dict[float, pd.DataFrame] = {}
        self._metric_column_names: Dict[str, str] = {}
        self._metric_results: Dict[tuple[str, float, float], MetricResult] = {}
        self._metric_debug_tables: Dict[str, Dict[float, pd.DataFrame]] = {}
        self._metric_debug_columns: Dict[str, Dict[str, str]] = {}
        self._metric_debug_windows: Dict[str, MetricDebugWindow] = {}
        self._last_graph_dir: Path | None = None
        self._field_direction_enabled = False
        self._direction_legends: Dict[Any, Legend] = {}
        self._last_source_dir: Path | None = None

        self._base_title = "VSM Hysteresis Loops"
        super().__init__(title=self._base_title)
        self.setMinimumSize(960, 640)
        self.resize(1360, 900)
        self._update_project_title()
        self._load_settings()
        self._ensure_window_visibility()
        self._update_project_actions()

    def _create_dock_widget(self, title: str, object_name: str) -> QtWidgets.QDockWidget:
        return super()._create_dock_widget(title, object_name)

    def _after_base_ui_created(
        self,
        *,
        project_dock: QtWidgets.QDockWidget,
        log_dock: QtWidgets.QDockWidget,
        graph_dock: QtWidgets.QDockWidget | None,
        graph_panel: QtWidgets.QWidget | None,
    ) -> None:
        _ = graph_panel
        if graph_dock is None:
            return
        graph_dock_features = graph_dock.features()
        graph_dock.setFeatures(
            graph_dock_features | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        graph_dock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )
        graph_dock.setMinimumWidth(260)

        def _handle_location_change(area: QtCore.Qt.DockWidgetArea) -> None:
            if area == QtCore.Qt.DockWidgetArea.LeftDockWidgetArea:
                graph_dock.setFloating(False)
                self._retabify_primary_docks()

        graph_dock.dockLocationChanged.connect(_handle_location_change)
        self.message_log_dock = log_dock
        self.log_view.installEventFilter(self)
        log_dock.visibilityChanged.connect(self._handle_log_visibility)
        self._retabify_primary_docks()

    # ------------------------------------------------------------------ project helpers
    def _has_project_data_to_save(self) -> bool:
        return bool(self.measurements)

    def _reset_project_state(self) -> None:
        self._project_path = None
        self._update_project_title()
        self._reset_session_state()
        self._update_project_actions()

    def _after_project_saved(self, path: Path, payload: Dict[str, Any]) -> None:
        _ = payload
        self._append_log(f"Saved project to {path}")

    def _after_project_loaded(self, path: Path, payload: Dict[str, Any]) -> None:
        _ = payload
        self._append_log(f"Opened project {path}")

    def _extend_menus(self, menu_bar: QtWidgets.QMenuBar) -> None:
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

        self._update_project_actions()

    def _build_project_payload(self, *, base_path: Path | None = None) -> Dict[str, Any]:
        sources = [str(path) for path in self._selected_paths()]
        temp_value = self.temperature_combo.currentData() if hasattr(self, 'temperature_combo') else None
        if isinstance(temp_value, (int, float)):
            temperature_filter = float(temp_value)
        else:
            temperature_filter = None
        axes_payload = {
            'x': self.x_axis_combo.currentText() if hasattr(self, 'x_axis_combo') else None,
            'y': self.y_axis_combo.currentText() if hasattr(self, 'y_axis_combo') else None,
        }
        visibility_payload: List[Dict[str, Any]] = []
        for group_key, angles in self._line_visibility.items():
            sample_name, temperature = group_key
            if sample_name == _LEGACY_ANY_SAMPLE:
                continue
            angle_map = {str(angle): bool(flag) for angle, flag in angles.items()}
            visibility_payload.append(
                {
                    "sample": str(sample_name),
                    "temperature": float(temperature),
                    "angles": angle_map,
                }
            )
        measurements_payload: List[Dict[str, Any]] = []
        base_dir = base_path.resolve() if base_path is not None else None
        for measurement in self.measurements:
            table = measurement.data.astype(object).where(
                pd.notnull(measurement.data), None
            )
            records: List[Dict[str, Any]] = []
            for _, row in table.iterrows():
                record: Dict[str, Any] = {}
                for column in table.columns:
                    record[str(column)] = self._json_friendly(row[column])
                records.append(record)
            index_payload = [self._json_friendly(value) for value in table.index.tolist()]
            entry: Dict[str, Any] = {
                'path': str(measurement.path),
                'temperature': self._json_friendly(measurement.temperature),
                'angle': self._json_friendly(measurement.angle),
                'data': {
                    'columns': [str(column) for column in table.columns],
                    'records': records,
                    'index': index_payload,
                },
            }
            if base_dir is not None:
                try:
                    relative = measurement.path.resolve().relative_to(base_dir)
                except Exception:
                    relative = None
                if relative is not None:
                    entry['relative_path'] = str(relative)
            measurements_payload.append(entry)
        payload: Dict[str, Any] = {
            'version': self.PROJECT_VERSION,
            'sources': sources,
            'axes': axes_payload,
            'temperature_filter': temperature_filter,
            'field_direction': bool(self._field_direction_enabled),
            'style': self.style_combo.currentData() if hasattr(self, 'style_combo') else None,
            'dark_mode': bool(self._plot_dark_enabled()),
            'line_visibility': visibility_payload,
            'measurements': measurements_payload,
        }
        return payload

    def _apply_project_payload(self, payload: Dict[str, Any], *, project_dir: Path) -> bool:
        measurements_data = payload.get('measurements')
        if not isinstance(measurements_data, list) or not measurements_data:
            QtWidgets.QMessageBox.warning(
                self, 'VSM Hysteresis Loops', 'The project does not contain any measurements.'
            )
            return False
        sources = payload.get('sources')
        if isinstance(sources, list):
            source_strings = [str(item) for item in sources if isinstance(item, str)]
            self.path_edit.setText(';'.join(source_strings))
        else:
            self.path_edit.clear()
        style_value = payload.get('style')
        if hasattr(self, 'style_combo') and isinstance(style_value, str):
            index = self.style_combo.findData(style_value)
            if index >= 0:
                self.style_combo.setCurrentIndex(index)
        dark_value = payload.get('dark_mode')
        dark_action = getattr(self, "_dark_mode_action", None)
        if dark_action is not None and callable(getattr(dark_action, "setChecked", None)):
            try:
                dark_action.setChecked(bool(dark_value))
            except Exception:
                pass
        else:
            try:
                self._dark_mode_enabled = bool(dark_value)
            except Exception:
                pass
        axes_data = payload.get('axes', {}) if isinstance(payload, dict) else {}
        x_axis = axes_data.get('x') if isinstance(axes_data, dict) else None
        y_axis = axes_data.get('y') if isinstance(axes_data, dict) else None
        x_axis = x_axis if isinstance(x_axis, str) and x_axis else None
        y_axis = y_axis if isinstance(y_axis, str) and y_axis else None
        self._stored_axes = (x_axis, y_axis)
        self._line_visibility = self._deserialize_line_visibility(payload.get('line_visibility', {}))
        field_direction = bool(payload.get('field_direction'))
        measurements: List[VSMMeasurement] = []
        base_dir = project_dir
        for entry in measurements_data:
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get('relative_path')
            measurement_path: Path | None = None
            if isinstance(raw_path, str) and raw_path:
                measurement_path = (base_dir / raw_path).resolve()
            else:
                fallback = entry.get('path')
                if isinstance(fallback, str) and fallback:
                    candidate = Path(fallback)
                    if not candidate.is_absolute():
                        measurement_path = (base_dir / candidate).resolve()
                    else:
                        measurement_path = candidate
            if measurement_path is None:
                continue
            temperature_value = entry.get('temperature')
            angle_value = entry.get('angle')
            temperature = float(temperature_value) if isinstance(temperature_value, (int, float)) else None
            angle = float(angle_value) if isinstance(angle_value, (int, float)) else None
            data_payload = entry.get('data')
            if not isinstance(data_payload, dict):
                continue
            columns = data_payload.get('columns')
            records = data_payload.get('records', [])
            if not isinstance(columns, list):
                continue
            df = pd.DataFrame.from_records(records, columns=[str(col) for col in columns])
            index_values = data_payload.get('index')
            if isinstance(index_values, list) and len(index_values) == len(df):
                df.index = pd.Index(index_values)
            measurements.append(
                VSMMeasurement(path=measurement_path, temperature=temperature, angle=angle, data=df)
            )
        if not measurements:
            QtWidgets.QMessageBox.warning(
                self, 'VSM Hysteresis Loops', 'No valid measurements were found in the project file.'
            )
            return False
        self.measurements = measurements
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
            self.temperature_combo.addItem(f'{temp:g} °C', temp)
        plottable = sum(1 for m in self.measurements if m.temperature is not None and m.angle is not None)
        candidate_columns: List[str]
        common_columns: Dict[str, int] | None = None
        plottable_column_counts: Dict[str, int] = {}
        plottable_column_order: Dict[str, int] = {}
        for measurement in self.measurements:
            column_set = {col for col in measurement.data.columns if pd.api.types.is_numeric_dtype(measurement.data[col])}
            if common_columns is None:
                common_columns = {col: idx for idx, col in enumerate(measurement.data.columns) if col in column_set}
            else:
                common_columns = {col: idx for col, idx in common_columns.items() if col in column_set}
        if common_columns:
            candidate_columns = list(common_columns.keys())
        elif self.measurements:
            candidate_columns = list(self.measurements[0].data.columns)
        else:
            candidate_columns = []
        if candidate_columns:
            self._populate_axis_combos(candidate_columns)
            if x_axis and self.x_axis_combo.findText(x_axis) >= 0:
                self.x_axis_combo.setCurrentText(x_axis)
            if y_axis and self.y_axis_combo.findText(y_axis) >= 0:
                self.y_axis_combo.setCurrentText(y_axis)
            self._store_axis_selection()
        temp_filter = payload.get('temperature_filter')
        if isinstance(temp_filter, (int, float)):
            index = self.temperature_combo.findData(float(temp_filter))
            if index >= 0:
                self.temperature_combo.setCurrentIndex(index)
        self.plot_button.setEnabled(plottable > 0)
        self.export_button.setEnabled(True)
        self._generate_plots()
        self._set_field_direction_enabled(field_direction)
        self._save_settings()
        return True

    def _deserialize_line_visibility(self, payload: Any) -> Dict[PlotGroupKey, Dict[float, bool]]:
        result: Dict[PlotGroupKey, Dict[float, bool]] = {}
        if isinstance(payload, list):
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                sample = entry.get("sample")
                temperature = entry.get("temperature")
                if not isinstance(sample, str):
                    continue
                if not isinstance(temperature, (int, float)):
                    continue
                visibility: Dict[float, bool] = {}
                angles = entry.get("angles")
                if isinstance(angles, dict):
                    for angle_key, flag in angles.items():
                        try:
                            angle = float(angle_key)
                        except (TypeError, ValueError):
                            continue
                        visibility[angle] = bool(flag)
                result[(sample.strip() or "Unknown sample", float(temperature))] = visibility
            return result

        # Backward compatibility with older projects that keyed only by temperature.
        if not isinstance(payload, dict):
            return result
        for temp_key, angles in payload.items():
            try:
                temperature = float(temp_key)
            except (TypeError, ValueError):
                continue
            visibility: Dict[float, bool] = {}
            if isinstance(angles, dict):
                for angle_key, flag in angles.items():
                    try:
                        angle = float(angle_key)
                    except (TypeError, ValueError):
                        continue
                    visibility[angle] = bool(flag)
            result[(_LEGACY_ANY_SAMPLE, float(temperature))] = visibility
        return result

    def _plot_group_key(
        self,
        sample_name: str | None,
        temperature: float | None,
    ) -> PlotGroupKey | None:
        if temperature is None:
            return None
        try:
            temp_value = float(temperature)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(temp_value):
            return None
        sample = str(sample_name or "").strip() or "Unknown sample"
        return (sample, temp_value)

    def _line_visibility_for_group(self, key: PlotGroupKey) -> Dict[float, bool]:
        visibility = self._line_visibility.get(key)
        if isinstance(visibility, dict):
            return visibility
        legacy_key = (_LEGACY_ANY_SAMPLE, float(key[1]))
        legacy_visibility = self._line_visibility.get(legacy_key)
        if isinstance(legacy_visibility, dict):
            migrated = dict(legacy_visibility)
            self._line_visibility[key] = migrated
            return migrated
        created: Dict[float, bool] = {}
        self._line_visibility[key] = created
        return created

    def _descriptor_plot_group_key(self, descriptor: TabDescriptor | None) -> PlotGroupKey | None:
        if not isinstance(descriptor, TabDescriptor):
            return None
        metadata = descriptor.metadata if isinstance(descriptor.metadata, dict) else {}
        raw_key = metadata.get("plot_group_key")
        if isinstance(raw_key, (tuple, list)) and len(raw_key) == 2:
            sample = raw_key[0]
            temperature = raw_key[1]
            if isinstance(sample, str) and isinstance(temperature, (int, float)):
                return (sample.strip() or "Unknown sample", float(temperature))
        sample_name = metadata.get("sample_name")
        temperature = metadata.get("temperature")
        if isinstance(sample_name, str) and isinstance(temperature, (int, float)):
            return (sample_name.strip() or "Unknown sample", float(temperature))
        return None

    @staticmethod
    def _json_friendly(value: Any) -> Any:
        if isinstance(value, (int, str, bool)) or value is None:
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, np.generic):
            python_value = value.item()
            return VSMPlotter._json_friendly(python_value)
        return str(value)

    def _populate_graph_settings(self, layout: QtWidgets.QVBoxLayout) -> None:
        def _style_group(group: QtWidgets.QGroupBox, *, margin: int = 12) -> None:
            group.setFlat(True)
            group.setStyleSheet(
                "QGroupBox { border: none; margin-top: %dpx; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 0; padding: 0 0 6px 0; font-weight: 600; }"
                % max(0, margin)
            )

        axes_group = QtWidgets.QGroupBox("Axes and filters")
        _style_group(axes_group, margin=0)
        axes_form = QtWidgets.QFormLayout(axes_group)
        axes_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.temperature_combo = QtWidgets.QComboBox()
        self.temperature_combo.addItem("All temperatures", None)
        axes_form.addRow("Temperature", self.temperature_combo)

        self.x_axis_combo = QtWidgets.QComboBox()
        self.y_axis_combo = QtWidgets.QComboBox()
        self.x_axis_combo.currentTextChanged.connect(self._store_axis_selection)
        self.y_axis_combo.currentTextChanged.connect(self._store_axis_selection)
        axes_form.addRow("X axis", self.x_axis_combo)
        axes_form.addRow("Y axis", self.y_axis_combo)

        self.field_direction_button = QtWidgets.QPushButton(
            "Highlight field direction"
        )
        self.field_direction_button.setCheckable(True)
        self.field_direction_button.setToolTip(
            "Use solid lines for increasing magnetic field and dashed lines for decreasing segments."
        )
        self.field_direction_button.toggled.connect(self._handle_field_direction_toggle)
        axes_form.addRow("Field direction", self.field_direction_button)

        layout.addWidget(axes_group)

        overlay_group = QtWidgets.QGroupBox("Angle overlays")
        _style_group(overlay_group)
        overlay_layout = QtWidgets.QVBoxLayout(overlay_group)
        overlay_layout.setContentsMargins(8, 8, 8, 8)
        self.angle_overlay_list = QtWidgets.QListWidget()
        self.angle_overlay_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.angle_overlay_list.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
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
        layout.addWidget(overlay_group, 1)

        metrics_group = QtWidgets.QGroupBox("Derived metrics")
        _style_group(metrics_group)
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
        layout.addWidget(metrics_group)

        self.angle_overlay_list.itemSelectionChanged.connect(
            self._update_overlay_button_state
        )


    def _handle_field_direction_toggle(self, checked: bool) -> None:
        previous = self._field_direction_enabled
        if previous == checked:
            return
        self._set_field_direction_enabled(checked, update_button=False)
        description = (
            "Enable field-direction highlighting"
            if checked
            else "Disable field-direction highlighting"
        )
        self._record_history_action(
            description,
            undo=lambda prev=previous: self._set_field_direction_enabled(prev),
            redo=lambda current=checked: self._set_field_direction_enabled(current),
        )

    def _set_field_direction_enabled(
        self,
        enabled: bool,
        *,
        update_button: bool = True,
    ) -> None:
        self._field_direction_enabled = bool(enabled)
        if update_button and hasattr(self, "field_direction_button"):
            self.field_direction_button.blockSignals(True)
            self.field_direction_button.setChecked(self._field_direction_enabled)
            self.field_direction_button.blockSignals(False)
        for descriptor in list(self._tab_descriptors.values()):
            self._apply_direction_split_to_descriptor(descriptor)
            self._refresh_descriptor_legend(descriptor, force_layout=True)

    def _apply_direction_split_to_descriptor(self, descriptor: TabDescriptor) -> None:
        if not descriptor.lines:
            self._update_direction_legend(descriptor, False)
            return
        for state in descriptor.lines.values():
            self._apply_direction_split_to_state(descriptor, state)

    def _apply_direction_split_to_state(
        self,
        descriptor: TabDescriptor,
        state: GraphLineState,
    ) -> None:
        self._clear_state_extra_lines(state)
        x_data = np.asarray(state.x_data(), dtype=float)
        y_data = np.asarray(state.y_data(), dtype=float)
        if (
            x_data.size == 0
            or y_data.size == 0
            or x_data.shape != y_data.shape
            or not np.isfinite(x_data).any()
        ):
            if x_data.size and y_data.size:
                state.line.set_data(x_data, y_data)
            self._apply_default_line_style(state.line)
            return

        if not self._field_direction_enabled:
            state.line.set_data(x_data, y_data)
            self._apply_default_line_style(state.line)
            return

        half = x_data.size // 2
        if half == 0 or half >= x_data.size:
            state.line.set_data(x_data, y_data)
            self._apply_default_line_style(state.line)
            return

        x_first = x_data[:half]
        y_first = y_data[:half]
        x_second = x_data[half:]
        y_second = y_data[half:]

        direction_first = self._segment_direction(x_first)
        direction_second = self._segment_direction(x_second)

        get_attr = getattr
        color = get_attr(state.line, "get_color", lambda: None)()
        linewidth = get_attr(state.line, "get_linewidth", lambda: None)()
        marker = get_attr(state.line, "get_marker", lambda: "None")()
        markersize = get_attr(state.line, "get_markersize", lambda: None)()

        state.line.set_data(x_first, y_first)
        state.line.set_linestyle("-" if direction_first >= 0 else "--")
        if color is not None:
            state.line.set_color(color)
        if linewidth is not None:
            state.line.set_linewidth(linewidth)
        if marker is not None:
            state.line.set_marker(marker)
            if markersize is not None and marker != "None":
                state.line.set_markersize(markersize)

        extra_line = Line2D(x_second, y_second)
        if color is not None:
            extra_line.set_color(color)
        extra_line.set_linestyle("-" if direction_second >= 0 else "--")
        if linewidth is not None:
            extra_line.set_linewidth(linewidth)
        if marker is not None:
            extra_line.set_marker(marker)
            if markersize is not None and marker != "None":
                extra_line.set_markersize(markersize)
        extra_line.set_label("_direction_segment")
        extra_line.set_visible(state.line.get_visible())
        descriptor.axes.add_line(extra_line)
        state.extra_lines = [extra_line]

    @staticmethod
    def _segment_direction(values: np.ndarray) -> int:
        diffs = np.diff(values)
        finite = diffs[np.isfinite(diffs)]
        if finite.size == 0:
            return 0
        total = float(np.sum(finite))
        if total > 0:
            return 1
        if total < 0:
            return -1
        return 0

    def _clear_state_extra_lines(self, state: GraphLineState) -> None:
        for line in state.extra_lines:
            try:
                line.remove()
            except Exception:
                pass
        state.extra_lines.clear()

    def _apply_default_line_style(self, line: Any) -> None:
        style = self._line_style_kwargs()
        line.set_linestyle(style.get("linestyle", "-"))
        marker = style.get("marker")
        if marker:
            line.set_marker(marker)
            line.set_markersize(style.get("markersize", line.get_markersize()))
        else:
            line.set_marker("None")

    def _update_direction_legend(self, descriptor: TabDescriptor, enabled: bool) -> None:
        axes = descriptor.axes
        direction_legends = getattr(self, "_direction_legends", None)
        if direction_legends is None:
            self._direction_legends = {}
            direction_legends = self._direction_legends
        legend = direction_legends.pop(axes, None)
        if legend is not None:
            try:
                legend.remove()
            except Exception:
                pass
        if not enabled or not descriptor.lines:
            return

        color = self._direction_reference_color(descriptor)
        handles = [
            Line2D([], [], color=color, linestyle="-", label="Increasing H"),
            Line2D([], [], color=color, linestyle="--", label="Decreasing H"),
        ]
        legend = Legend(
            axes,
            handles,
            [handle.get_label() for handle in handles],
            loc="lower left",
            bbox_to_anchor=(0.0, 1.02),
            borderaxespad=0.0,
        )
        try:
            legend.set_in_layout(False)
        except Exception:
            pass
        axes.add_artist(legend)
        self._style_legend(legend)
        self._direction_legends[axes] = legend

    def _direction_reference_color(self, descriptor: TabDescriptor) -> str:
        for state in descriptor.lines.values():
            try:
                color = state.line.get_color()
            except Exception:
                color = None
            if color:
                return str(color)
        return "#404040"


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

        source_dir = self.settings.value("last_source_dir", "")
        if isinstance(source_dir, str) and source_dir:
            try:
                self._last_source_dir = Path(source_dir)
            except (TypeError, ValueError):
                self._last_source_dir = None

        style_value = self.settings.value("plot_style", "line")
        if isinstance(style_value, str) and hasattr(self, "style_combo"):
            index = self.style_combo.findData(style_value)
            if index >= 0:
                self.style_combo.setCurrentIndex(index)

        dark_value = self.settings.value("plot_dark_mode", False)
        dark_enabled = bool(dark_value) if dark_value is not None else False
        dark_action = getattr(self, "_dark_mode_action", None)
        if dark_action is not None and callable(getattr(dark_action, "isChecked", None)):
            try:
                self._dark_mode_enabled = bool(dark_action.isChecked())
            except Exception:
                self._dark_mode_enabled = bool(getattr(self, "_dark_mode_enabled", False))
        elif hasattr(self, "_dark_mode_enabled"):
            try:
                self._dark_mode_enabled = dark_enabled
            except Exception:
                pass

        suppress_window = bool(getattr(self, "_suppress_window_persistence", False))
        maximized_preference = bool(self.settings.value("window_maximized", False))

        if not suppress_window:
            geometry_restored = False
            if not maximized_preference:
                geometry = self.settings.value("geometry")
                if isinstance(geometry, QtCore.QByteArray):
                    try:
                        geometry_restored = bool(self.restoreGeometry(geometry))
                    except Exception:  # pragma: no cover - Qt versions differ
                        geometry_restored = False
            else:
                geometry_restored = True

            if not geometry_restored and not maximized_preference:
                self.resize(1480, 940)
            elif geometry_restored and maximized_preference:
                try:
                    self.setWindowState(self.windowState() | QtCore.Qt.WindowState.WindowMaximized)
                except Exception:
                    pass
            elif geometry_restored:
                rect = self.geometry()
                if rect.width() < 400 or rect.height() < 300:
                    screen = QtGui.QGuiApplication.primaryScreen()
                    if screen is not None:
                        fallback = QtCore.QSize(1024, 768)
                        self.resize(fallback)
                        available = screen.availableGeometry()
                        frame = self.frameGeometry()
                        frame.moveCenter(available.center())
                        self.move(frame.topLeft())

            window_state = self.settings.value("window_state")
            signature_key = getattr(self, "_WINDOW_STATE_SIGNATURE_KEY", "window_state_signature")
            signature = self.settings.value(signature_key, "")
            expected_signature = self._window_state_signature()
            if not isinstance(signature, str) or signature != expected_signature:
                window_state = None
                self.settings.remove("window_state")
                self.settings.remove(signature_key)
                self.settings.sync()
            if isinstance(window_state, QtCore.QByteArray):
                try:
                    self.restoreState(window_state)
                except Exception:  # pragma: no cover - Qt versions differ
                    pass

    def _save_settings(self) -> None:
        self.settings.setValue("sources", self.path_edit.text())
        self.settings.setValue("plot_style", self._plot_style_token())
        if not hasattr(self, "dark_mode_checkbox"):
            self.settings.setValue("plot_dark_mode", self._plot_dark_enabled())
        if self.last_export_path:
            self.settings.setValue("last_export_path", str(self.last_export_path))
        if self._last_source_dir:
            self.settings.setValue("last_source_dir", str(self._last_source_dir))
        if not bool(getattr(self, "_suppress_window_persistence", False)):
            maximized_now = self.isMaximized() or bool(self.windowState() & QtCore.Qt.WindowState.WindowMaximized)
            self.settings.setValue("window_maximized", maximized_now)
            if maximized_now:
                self.settings.remove("geometry")
            else:
                self.settings.setValue("geometry", self.saveGeometry())
            self.settings.setValue("window_state", self.saveState())
            signature_key = getattr(self, "_WINDOW_STATE_SIGNATURE_KEY", "window_state_signature")
            self.settings.setValue(signature_key, self._window_state_signature())
        self.settings.sync()

    def _window_state_signature(self) -> str:
        return f"qt={QtCore.QT_VERSION_STR};pyqt={QtCore.PYQT_VERSION_STR}"

    def _ensure_window_visibility(self) -> None:
        if bool(getattr(self, "_suppress_window_persistence", False)):
            return
        if self.isMaximized() or bool(self.windowState() & QtCore.Qt.WindowState.WindowMaximized):
            self.activateWindow()
            return

        frame = self.frameGeometry()
        screen = QtGui.QGuiApplication.screenAt(frame.center())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()

        min_size = QtCore.QSize(960, 640)
        clamped_width = max(min_size.width(), min(frame.width(), available.width()))
        clamped_height = max(min_size.height(), min(frame.height(), available.height()))
        if clamped_width != frame.width() or clamped_height != frame.height():
            original_top_left = frame.topLeft()
            self.resize(clamped_width, clamped_height)
            frame = self.frameGeometry()
            candidate = QtCore.QRect(original_top_left, frame.size())
            if available.contains(candidate):
                self.move(original_top_left)
                frame = self.frameGeometry()

        tolerance = 4
        expanded = frame.adjusted(-tolerance, -tolerance, tolerance, tolerance)
        if available.contains(expanded):
            self.activateWindow()
            return

        width = frame.width()
        height = frame.height()
        left_limit = available.left()
        right_limit = available.right() - width
        top_limit = available.top()
        bottom_limit = available.bottom() - height

        new_left = frame.left()
        new_top = frame.top()

        if frame.left() < left_limit:
            new_left = left_limit
        elif frame.right() > available.right():
            new_left = right_limit

        if frame.top() < top_limit:
            new_top = top_limit
        elif frame.bottom() > available.bottom():
            new_top = bottom_limit

        if new_left != frame.left() or new_top != frame.top():
            self.move(new_left, new_top)

        self.activateWindow()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._save_settings()
        try:
            super().closeEvent(event)
        except TypeError:
            # Method may be rebound onto PyPlotWorkbench (not a VSMPlotter subclass).
            PyPlotWindow.closeEvent(self, event)

    def _choose_files(self) -> None:
        start_dir = str(self._default_source_directory())
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select VSM files",
            start_dir,
            "VSM data (*.VSM-Hys-Data);;All files (*)",
        )
        if not files:
            return
        selected_text = ";".join(files)
        self.path_edit.setText(selected_text)
        apply_path_text = getattr(self, "_apply_path_text", None)
        if callable(apply_path_text):
            try:
                apply_path_text(selected_text)
            except Exception:
                pass
        update_actions = getattr(self, "_update_action_states", None)
        if callable(update_actions):
            try:
                update_actions()
            except Exception:
                pass
        first = Path(files[0])
        self._remember_source_directory(first.parent if first.is_file() else first)
        self._load_measurements(show_warning=False)
        self._save_settings()

    def _import_data_from_files(self) -> None:
        # Route shared "Import data…" action through the plugin loader so
        # measurements/worksheet tabs/plot enablement stay in sync.
        self._choose_files()

    def _choose_folder(self) -> None:
        start_dir = str(self._default_source_directory())
        directories = self._select_directories(
            self,
            title="Select folder(s) with VSM files",
            start_dir=Path(start_dir),
        )
        if not directories:
            return

        seen: set[Path] = set()
        paths: list[Path] = []
        self._begin_task_progress(
            "Scanning folders for VSM files…",
            maximum=max(1, len(directories)),
            value=0,
        )
        for index, raw_directory in enumerate(directories, start=1):
            directory = Path(raw_directory)
            self._update_task_progress(
                value=index - 1,
                title=f"Scanning {directory.name or str(directory)} ({index}/{len(directories)})",
            )
            for path in _find_vsm_files(directory):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                paths.append(resolved)
        self._update_task_progress(
            value=max(1, len(directories)),
            title="Folder scan complete.",
        )
        self._end_task_progress()

        if not paths:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No VSM files were found in the selected folder(s).",
            )
            selected_text = ";".join(directories)
            self.path_edit.setText(selected_text)
            apply_path_text = getattr(self, "_apply_path_text", None)
            if callable(apply_path_text):
                try:
                    apply_path_text(selected_text)
                except Exception:
                    pass
            update_actions = getattr(self, "_update_action_states", None)
            if callable(update_actions):
                try:
                    update_actions()
                except Exception:
                    pass
            self._remember_source_directory(Path(directories[0]))
            self._save_settings()
            return

        selected_text = ";".join(str(path) for path in paths)
        self.path_edit.setText(selected_text)
        apply_path_text = getattr(self, "_apply_path_text", None)
        if callable(apply_path_text):
            try:
                apply_path_text(selected_text)
            except Exception:
                pass
        update_actions = getattr(self, "_update_action_states", None)
        if callable(update_actions):
            try:
                update_actions()
            except Exception:
                pass
        self._remember_source_directory(Path(directories[0]))
        self._load_measurements(show_warning=False)
        self._save_settings()

    def _import_data_from_folder(self) -> None:
        # Route shared "Import data…" action through the plugin loader so
        # measurements/worksheet tabs/plot enablement stay in sync.
        self._choose_folder()

    def _handle_manual_path_entry(self) -> None:
        text = self.path_edit.text().strip()
        if not text:
            return
        apply_path_text = getattr(self, "_apply_path_text", None)
        if callable(apply_path_text):
            try:
                apply_path_text(text)
            except Exception:
                pass
        first = Path(text.split(";")[0])
        if first.exists():
            self._remember_source_directory(first)
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

    def _populate_project_tree(self) -> None:
        self.project_tree.blockSignals(True)
        self.project_tree.clear()
        self._graph_tree_items.clear()
        self._worksheet_tree_items.clear()

        graphs_root = QtWidgets.QTreeWidgetItem(["Graphs", ""])
        graphs_root.setFlags(graphs_root.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
        self._assign_project_payload(graphs_root, ("group", "graphs"))
        graphs_root.setExpanded(True)
        self.project_tree.addTopLevelItem(graphs_root)
        self._graph_tree_root = graphs_root

        worksheets_root = QtWidgets.QTreeWidgetItem(["Worksheets", ""])
        worksheets_root.setFlags(
            worksheets_root.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        self._assign_project_payload(
            worksheets_root, ("group", "worksheets")
        )
        worksheets_root.setExpanded(True)
        self.project_tree.addTopLevelItem(worksheets_root)
        self._worksheet_tree_root = worksheets_root

        sample_groups: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        temp_groups: Dict[tuple[str, float | None], QtWidgets.QTreeWidgetItem] = {}
        grouped: Dict[tuple[str, float | None], List[VSMMeasurement]] = {}
        for measurement in self.measurements:
            sample_label = measurement.sample_name or "Unknown sample"
            grouped.setdefault((sample_label, measurement.temperature), []).append(measurement)

        for (sample_label, temperature), group in sorted(
            grouped.items(),
            key=lambda item: (
                item[0][0].lower(),
                float("inf") if item[0][1] is None else float(item[0][1]),
            ),
        ):
            sample_parent = sample_groups.get(sample_label)
            if sample_parent is None:
                sample_parent = QtWidgets.QTreeWidgetItem([sample_label, ""])
                sample_parent.setFlags(
                    sample_parent.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable
                )
                sample_parent.setExpanded(True)
                self._assign_project_payload(
                    sample_parent,
                    ("worksheet_sample", sample_label),
                )
                sample_groups[sample_label] = sample_parent
                worksheets_root.addChild(sample_parent)

            temp_key = (sample_label, temperature)
            parent = temp_groups.get(temp_key)
            if parent is None:
                label = (
                    "Unknown temperature"
                    if temperature is None
                    else f"{temperature:g} °C"
                )
                parent = QtWidgets.QTreeWidgetItem([label, ""])
                parent.setFlags(parent.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
                parent.setExpanded(True)
                self._assign_project_payload(
                    parent,
                    ("worksheet_group", temp_key),
                )
                temp_groups[temp_key] = parent
                sample_parent.addChild(parent)

            worksheet_key = self._worksheet_group_key(sample_label, temperature)
            worksheet = self._worksheets.get(worksheet_key)
            if worksheet is None:
                continue
            angle_count = sum(1 for m in group if m.angle is not None)
            detail = f"{angle_count} angle(s)" if angle_count else ""
            child = QtWidgets.QTreeWidgetItem(["Worksheet", detail])
            self._assign_project_payload(
                child,
                ("worksheet", worksheet_key),
            )
            parent.addChild(child)
            self._worksheet_tree_items[worksheet_key] = child

        self.project_tree.expandAll()
        self.project_tree.blockSignals(False)

    def _update_sample_title(self) -> None:
        names = sorted({m.sample_name for m in self.measurements if m.sample_name})
        if len(names) == 1:
            self._base_title = f"VSM Hysteresis Loops — {names[0]}"
        else:
            self._base_title = "VSM Hysteresis Loops"
        self._update_project_title()


    def _populate_worksheets(self) -> None:
        self._worksheet_models.clear()
        self._worksheets.clear()
        grouped: Dict[tuple[str, float | None], List[VSMMeasurement]] = {}
        for measurement in self.measurements:
            sample_label = measurement.sample_name or "Unknown sample"
            key = self._worksheet_group_key(sample_label, measurement.temperature)
            grouped.setdefault(key, []).append(measurement)

        for key, group in sorted(
            grouped.items(),
            key=lambda item: (
                item[0][0].lower(),
                float("inf") if item[0][1] is None else float(item[0][1]),
            ),
        ):
            sample_label, temperature = key
            worksheet = self._build_group_worksheet(sample_label, temperature, group)
            self._worksheets[worksheet.key] = worksheet
            model = WorksheetTableModel(worksheet, self)
            self._worksheet_models[worksheet.key] = model
            widget = self._worksheet_tabs_open.get(worksheet.key)
            if widget is not None:
                view = getattr(widget, "_worksheet_view", None)
                if isinstance(view, QtWidgets.QTableView):
                    view.setModel(model)
                    self._configure_worksheet_view(view)

        for key in list(self._worksheet_tabs_open.keys()):
            if key not in self._worksheets:
                widget = self._worksheet_tabs_open.get(key)
                if widget is not None:
                    self._close_tab(widget)

        for key in list(self._worksheet_tree_items.keys()):
            self._update_worksheet_item_state(key)

    def _update_worksheet_item_state(self, key: Hashable) -> None:
        item = self._worksheet_tree_items.get(key)
        if item is None:
            return
        widget = self._worksheet_tabs_open.get(key)
        visible = False
        if widget is not None:
            index = self.tab_widget.indexOf(widget)
            if index >= 0:
                try:
                    visible = self.tab_widget.isTabVisible(index)
                except Exception:
                    visible = widget.isVisible()
        palette = self.project_tree.palette()
        base_color = palette.color(QtGui.QPalette.ColorRole.Text)
        if visible:
            brush = QtGui.QBrush(base_color)
        else:
            dim_color = QtGui.QColor(base_color)
            dim_color = dim_color.lighter(160)
            brush = QtGui.QBrush(dim_color)
        item.setForeground(0, brush)
        item.setForeground(1, brush)

    def _create_worksheet_tab(
        self,
        key: Hashable,
        model: WorksheetTableModel,
        worksheet: WorksheetData | None,
    ) -> QtWidgets.QWidget:
        view = WorksheetTableView()
        view.setModel(model)
        view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(
            lambda pos, table=view: self._open_table_menu(table, pos)
        )
        view.horizontalHeader().setStretchLastSection(True)
        self._configure_worksheet_view(view)

        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)
        setattr(container, "_worksheet_view", view)
        setattr(view, "_worksheet_key", key)

        tab_label = worksheet.name if worksheet is not None else "Worksheet"

        index = self.tab_widget.addTab(container, tab_label)
        self.tab_widget.setCurrentIndex(index)
        self._worksheet_tabs_open[key] = container
        self._tab_to_worksheet_key[container] = key
        self._set_tab_visibility(container, True)
        return container

    def _configure_worksheet_view(self, view: QtWidgets.QTableView) -> None:
        header = view.verticalHeader()
        header.setVisible(True)
        for row_index in range(len(WorksheetTableModel.METADATA_FIELDS)):
            header.setSectionResizeMode(
                row_index, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
            )
        header.setDefaultSectionSize(max(22, header.defaultSectionSize()))

    def _worksheet_group_key(
        self, sample_label: str, temperature: float | None
    ) -> tuple[str, float | None]:
        return (sample_label, temperature)

    def _worksheet_display_label(self, sample_label: str, temperature: float | None) -> str:
        temp_label = (
            "Unknown temperature"
            if temperature is None
            else f"{temperature:g} °C"
        )
        if sample_label:
            return f"{sample_label} — {temp_label}"
        return temp_label

    def _worksheet_axis_columns(self, frame: pd.DataFrame) -> tuple[str | None, str | None]:
        if frame is None or frame.empty:
            return None, None
        preferred_x = [
            "Applied Field For Plot",
            "Raw Applied Field For Plot",
            "Applied Field [Oe]",
            "Applied Field",
        ]
        preferred_y = [
            "Signal X direction",
            "Signal X direction [emu]",
            "Signal parallel with sample",
            "Signal Magnitude",
            "Moment [emu]",
        ]

        def _pick(preferences: Sequence[str]) -> str | None:
            for pref in preferences:
                match = _match_column(frame, pref)
                if match:
                    return match
            for column in frame.columns:
                if pd.api.types.is_numeric_dtype(frame[column]):
                    return str(column)
            return None

        return _pick(preferred_x), _pick(preferred_y)

    def _build_group_worksheet(
        self,
        sample_label: str,
        temperature: float | None,
        measurements: Sequence[VSMMeasurement],
    ) -> WorksheetData:
        data: Dict[str, pd.Series] = {}
        columns: Dict[str, WorksheetColumnMeta] = {}
        axis_roles: List[str] = []
        source: Path | None = None

        def _unique_name(base: str) -> str:
            if base not in data:
                return base
            counter = 2
            candidate = f"{base} ({counter})"
            while candidate in data:
                counter += 1
                candidate = f"{base} ({counter})"
            return candidate

        for measurement in sorted(
            measurements,
            key=lambda m: float("inf") if m.angle is None else float(m.angle),
        ):
            frame = measurement.data
            x_column, y_column = self._worksheet_axis_columns(frame)
            if not x_column or not y_column:
                self._append_log(
                    f"{measurement.path.name}: no usable axis columns for worksheet export."
                )
                continue
            if source is None:
                source = measurement.path

            angle_label = (
                f"{measurement.angle:g}°"
                if measurement.angle is not None
                else measurement.path.stem or "Angle"
            )
            x_long, x_unit = _split_column_label(x_column)
            y_long, y_unit = _split_column_label(y_column)
            x_name = _unique_name(f"{x_column} ({angle_label})")
            y_name = _unique_name(f"{y_column} ({angle_label})")

            x_series = pd.to_numeric(frame[x_column], errors="coerce").reset_index(drop=True)
            y_series = pd.to_numeric(frame[y_column], errors="coerce").reset_index(drop=True)
            data[x_name] = x_series
            data[y_name] = y_series
            axis_roles.extend(["X", "Y"])

            comment = self._measurement_comment(measurement)
            formula = self._measurement_formula(measurement)
            columns[x_name] = WorksheetColumnMeta(
                long_name=f"{x_long or x_column} ({angle_label})",
                units=x_unit,
                comments=comment,
                formula=formula,
            )
            columns[y_name] = WorksheetColumnMeta(
                long_name=f"{y_long or y_column} ({angle_label})",
                units=y_unit,
                comments=comment,
                formula=formula,
            )

        frame = pd.DataFrame(data)
        worksheet_key = self._worksheet_group_key(sample_label, temperature)
        return WorksheetData(
            key=worksheet_key,
            name=self._worksheet_display_label(sample_label, temperature),
            dataframe=frame,
            columns=columns,
            source=source,
            workbook_key=worksheet_key,
            axis_roles="".join(axis_roles),
        )

    def _measurement_comment(self, measurement: VSMMeasurement) -> str:
        if measurement.angle is not None and not math.isnan(measurement.angle):
            return f"{measurement.angle:g}°"
        return measurement.path.name

    def _measurement_formula(self, measurement: VSMMeasurement) -> str:
        _ = measurement
        return ""

    def _series_label(
        self,
        measurement: VSMMeasurement,
        column: str,
        fallback: str,
    ) -> str:
        worksheet = self._worksheets.get(measurement.path)
        if worksheet is not None:
            meta = worksheet.columns.get(column)
            if meta and meta.comments:
                return meta.comments
        return fallback

    def _format_sample_label(self, name: str | None) -> str | None:
        if not name:
            return None
        formatted = format_annealing_title(str(name).strip())
        tokens = formatted.split()
        for index, token in enumerate(tokens):
            if "-" not in token:
                continue
            replacement = SAMPLE_FRACTION_RE.sub(r"\1/\2", token)
            tokens[index] = replacement
        return " ".join(tokens)

    def _default_source_directory(self) -> Path:
        if self._last_source_dir is not None and self._last_source_dir.exists():
            return self._last_source_dir
        text = self.path_edit.text().strip()
        if text:
            first = text.split(";")[0]
            candidate = Path(first)
            if candidate.is_dir():
                return candidate
            if candidate.is_file():
                return candidate.parent
        return Path.home()

    def _remember_source_directory(self, path: Path) -> None:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved.is_file():
            resolved = resolved.parent
        if not resolved.exists():
            return
        self._last_source_dir = resolved
        self.settings.setValue("last_source_dir", str(resolved))

    def _open_worksheet_tab(self, key: Hashable) -> None:
        model = self._worksheet_models.get(key)
        worksheet = self._worksheets.get(key)
        if model is None:
            if worksheet is None:
                return
            model = WorksheetTableModel(worksheet, self)
            self._worksheet_models[key] = model
        widget = self._worksheet_tabs_open.get(key)
        if widget is None:
            widget = self._create_worksheet_tab(key, model, worksheet)
        index = self.tab_widget.indexOf(widget)
        if index >= 0:
            self._set_tab_visibility(widget, True)
            self.tab_widget.setCurrentIndex(index)
        self._update_worksheet_item_state(key)
        self._update_tab_buttons()

    def _show_tab(self, tab: QtWidgets.QWidget) -> None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return
        self._set_tab_visibility(tab, True)
        self.tab_widget.setCurrentIndex(index)
        self._update_tab_buttons()

    def _ensure_graph_tree_item(self, tab: QtWidgets.QWidget, descriptor: TabDescriptor) -> None:
        if self._graph_tree_root is None:
            return
        item = self._graph_tree_items.get(tab)
        label = descriptor.root_label or descriptor.title
        detail = descriptor.title
        if item is None:
            item = QtWidgets.QTreeWidgetItem([label, detail])
            self._assign_project_payload(
                item,
                ("graph", tab),
            )
            self._graph_tree_root.addChild(item)
            self._graph_tree_items[tab] = item
        else:
            item.setText(0, label)
            item.setText(1, detail)
            self._assign_project_payload(
                item,
                ("graph", tab),
            )
        self._style_graph_item(item, self._is_tab_visible(tab))

    def _update_graph_tree_for_tab(self, tab: QtWidgets.QWidget) -> None:
        descriptor = self._tab_descriptors.get(tab)
        item = self._graph_tree_items.get(tab)
        if descriptor is None or item is None:
            return
        item.setText(0, descriptor.root_label or descriptor.title)
        item.setText(1, descriptor.title)
        self._style_graph_item(item, self._is_tab_visible(tab))

    def _style_graph_item(self, item: QtWidgets.QTreeWidgetItem, visible: bool) -> None:
        font = item.font(0)
        font.setItalic(not visible)
        item.setFont(0, font)
        item.setFont(1, font)

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
        if tab in self._tab_descriptors:
            self._update_graph_tree_for_tab(tab)
        else:
            key = self._tab_to_worksheet_key.get(tab)
            if key is not None:
                self._update_worksheet_item_state(key)

    def _find_alternate_tab_index(self, current_index: int) -> int | None:
        count = self.tab_widget.count()
        for offset in range(1, count):
            forward = (current_index + offset) % count
            if self._is_tab_visible(self.tab_widget.widget(forward)):
                return forward
        return None

    def _minimize_tab(self, tab: QtWidgets.QWidget) -> None:
        index = self.tab_widget.indexOf(tab)
        if index < 0:
            return
        next_index = None
        if self.tab_widget.currentWidget() is tab:
            next_index = self._find_alternate_tab_index(index)
        self._set_tab_visibility(tab, False)
        if next_index is not None:
            self.tab_widget.setCurrentIndex(next_index)
        self._update_tab_buttons()
        if tab in self._tab_descriptors:
            self._rebuild_object_manager_for_tab(self.tab_widget.currentWidget())

        if not self._tab_descriptors:
            self.export_button.setEnabled(False)
            self.open_origin_button.setEnabled(False)
        self.export_button.setEnabled(False)
        key = self._tab_to_worksheet_key.get(tab)
        if key is not None:
            self._update_worksheet_item_state(key)

    def _focus_tree_on_tab(self, tab: QtWidgets.QWidget | None) -> None:
        self.project_tree.blockSignals(True)
        self.project_tree.clearSelection()
        target_item: QtWidgets.QTreeWidgetItem | None = None
        if tab is not None:
            descriptor = self._tab_descriptors.get(tab)
            if descriptor is not None:
                target_item = self._graph_tree_items.get(tab)
            else:
                key = self._tab_to_worksheet_key.get(tab)
                if key is not None:
                    target_item = self._worksheet_tree_items.get(key)
        if target_item is not None:
            self.project_tree.setCurrentItem(target_item)
            self.project_tree.scrollToItem(target_item)
        self.project_tree.blockSignals(False)

    def _update_tab_buttons(self) -> None:
        tab_bar = self.tab_widget.tabBar()
        if tab_bar is None:
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

    def _open_table_menu(
        self, table: QtWidgets.QTableView, pos: QtCore.QPoint
    ) -> None:
        menu = QtWidgets.QMenu(table)
        copy_action = menu.addAction("Copy")
        if copy_action is not None:
            copy_action.triggered.connect(lambda: self._copy_table_selection(table))
        delete_action = menu.addAction("Delete selected rows")
        if delete_action is not None:
            delete_action.triggered.connect(lambda: self._delete_selected_rows(table))
        menu.exec(table.viewport().mapToGlobal(pos))

    def _delete_selected_rows(self, table: QtWidgets.QTableView) -> None:
        model = table.model()
        if not isinstance(model, WorksheetTableModel):
            return
        selection = table.selectionModel()
        if selection is None:
            return
        rows = sorted(
            {
                index.row()
                for index in selection.selectedRows()
                if index.row() >= len(WorksheetTableModel.METADATA_FIELDS)
            },
            reverse=True,
        )
        if not rows:
            return
        for row in rows:
            model.removeRows(row, 1)
        label = None
        worksheet_key = getattr(table, "_worksheet_key", None)
        if worksheet_key is not None:
            worksheet = self._worksheets.get(worksheet_key)
            if worksheet is not None:
                label = worksheet.name
        if label is None:
            label = "worksheet"
        self._append_log(f"Deleted {len(rows)} row(s) from {label}.")
        self._generate_plots()

    def _copy_table_selection(self, table: QtWidgets.QTableView) -> None:
        if isinstance(table, WorksheetTableView):
            table.copy_selection()
            return
        model = table.model()
        selection = table.selectionModel()
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
            grid[row_map[index.row()]][column_map[index.column()]] = (
                "" if value is None else str(value)
            )
        text = "\n".join("\t".join(row) for row in grid)
        QtWidgets.QApplication.clipboard().setText(text)

    def _register_plot_tab(
        self,
        tab: QtWidgets.QWidget,
        canvas: FigureCanvas,
        axes: Any,
        descriptor: TabDescriptor | None = None,
    ) -> None:
        if descriptor is not None:
            descriptor.metadata.setdefault(_BASE_Y_LABEL_KEY, descriptor.y_label)
            descriptor.metadata.setdefault(_BASE_TITLE_KEY, descriptor.title)
        PyPlotWindow._register_plot_tab(self, tab, canvas, axes, descriptor)
        apply_graph_options = getattr(self, "_apply_graph_options_to_axes", None)
        if callable(apply_graph_options):
            plugin_name = None
            tab_plugin_name = getattr(self, "_tab_plugin_name", None)
            if callable(tab_plugin_name):
                try:
                    plugin_name = tab_plugin_name(descriptor)
                except Exception:
                    plugin_name = None
            try:
                apply_graph_options(axes, plugin_name=plugin_name)
            except TypeError:
                try:
                    apply_graph_options(axes)
                except Exception:
                    pass
            except Exception:
                pass
        if descriptor is not None:
            if self._field_direction_enabled:
                self._apply_direction_split_to_descriptor(descriptor)
            self._refresh_descriptor_legend(descriptor, force_layout=True)
        # VSM plots are the primary work product in this window; use the full
        # MDI viewport width instead of the default half-width slot.
        fitter = getattr(self.tab_widget, "_fit_subwindow", None)
        subwindow_for = getattr(self.tab_widget, "_subwindow_for", None)
        if callable(fitter) and callable(subwindow_for):
            try:
                subwindow = subwindow_for(tab)
            except Exception:
                subwindow = None
            if subwindow is not None:
                try:
                    fitter(subwindow, use_half_width=False, preferred_width=None)
                except Exception:
                    pass

    def _clear_tab_list(self, tabs: List[QtWidgets.QWidget]) -> None:
        PyPlotWindow._clear_tab_list(self, list(tabs))
        tabs.clear()
        sync_shared = getattr(self, "_sync_shared_action_states", None)
        if callable(sync_shared):
            try:
                sync_shared()
            except Exception:
                pass

    def _update_save_graph_enabled(self, *_: object) -> None:
        current = self.tab_widget.currentWidget()
        enabled = bool(current and current in self._canvas_by_tab)
        self.save_graph_button.setEnabled(enabled)

    def _update_normalize_enabled(self) -> None:
        tab = self.tab_widget.currentWidget()
        descriptor = self._tab_descriptors.get(tab) if tab is not None else None
        can_popout = bool(descriptor and descriptor.lines)
        can_normalize = bool(
            descriptor
            and descriptor.lines
            and descriptor.kind in _NORMALIZABLE_TAB_KINDS
        )
        if hasattr(self, "normalize_button"):
            try:
                self.normalize_button.setEnabled(can_normalize)
            except Exception:
                pass
        if hasattr(self, "popout_button"):
            try:
                self.popout_button.setEnabled(can_popout)
            except Exception:
                pass

    def _handle_current_tab_changed(self, index: int) -> None:
        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        tab = self.tab_widget.widget(index) if index >= 0 else None
        self._rebuild_object_manager_for_tab(tab)
        self._focus_tree_on_tab(tab)
        self._update_tab_buttons()
    # ------------------------------------------------------------------ data loading
    def _reset_session_state(self, *, clear_measurements: bool = True) -> None:
        self._base_title = "VSM Hysteresis Loops"
        if clear_measurements:
            self.measurements.clear()
        for legend in list(self._direction_legends.values()):
            try:
                legend.remove()
            except Exception:
                pass
        self._direction_legends.clear()
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
        self._worksheets.clear()
        self._workbooks.clear()
        self._data_workbook_items.clear()
        self._data_folder_items.clear()
        self._data_tree_root = None
        self._update_project_title()
        self._reset_object_manager()
        self._graph_tree_root = None
        self._worksheet_tree_root = None
        self._graph_tree_items.clear()
        self._worksheet_tree_items.clear()
        self._worksheet_tabs_open.clear()
        self._tab_to_worksheet_key.clear()
        self._hidden_tabs.clear()
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
        self.open_origin_button.setEnabled(False)
        if hasattr(self, "popout_button"):
            try:
                self.popout_button.setEnabled(False)
            except Exception:
                pass
        self.metrics_angle_button.setEnabled(False)
        self.metrics_temperature_button.setEnabled(False)
        self._update_metric_controls()
        self._update_normalize_enabled()
        self._update_tab_buttons()
        self._update_project_actions()

    def _load_measurements(self, *, show_warning: bool = True) -> None:
        self._reset_session_state()
        self._project_path = None
        self._update_project_title()

        paths = self._selected_paths()
        if not paths:
            if show_warning:
                QtWidgets.QMessageBox.warning(
                    self, "VSM Hysteresis Loops", "Select at least one VSM file to load."
                )
            return

        total_steps = max(1, len(paths)) + 3
        self._begin_task_progress(
            "Importing VSM measurements…",
            maximum=total_steps,
            value=0,
        )

        total_loaded = 0
        plottable = 0
        common_columns: Dict[str, int] | None = None
        plottable_column_counts: Dict[str, int] = {}
        plottable_column_order: Dict[str, int] = {}
        for position, path in enumerate(paths, start=1):
            self._update_task_progress(
                value=position - 1,
                title=f"Importing {path.name} ({position}/{len(paths)})",
            )
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
            header_info = _header_metadata(path)
            sample_name = header_info.get("sample_name")
            operator = header_info.get("operator")
            test_id = header_info.get("test_id")

            derived_angle, derived_temperature = _derive_metadata_from_dataframe(df)
            recovered: List[str] = []
            if angle is None and derived_angle is not None:
                angle = derived_angle
                recovered.append("angle")
            if temperature is None and derived_temperature is not None:
                temperature = derived_temperature
                recovered.append("temperature")

            measurement = VSMMeasurement(
                path=path,
                temperature=temperature,
                angle=angle,
                data=df,
                sample_name=str(sample_name) if sample_name else None,
                operator=str(operator) if operator else None,
                test_id=str(test_id) if test_id else None,
                header_metadata=header_info,
            )
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
                details = f"{angle:g}° @ {temperature:g} °C"
                if measurement.sample_name:
                    details = f"{measurement.sample_name} — {details}"
                if recovered:
                    self._append_log(f"{path.name}: using recovered metadata ({details}).")
                else:
                    self._append_log(f"{path.name}: {details}.")
            numeric_column_names = [
                col for col in df.columns if pd.api.types.is_numeric_dtype(df[col])
            ]
            column_set = set(numeric_column_names)
            if common_columns is None:
                common_columns = {col: idx for idx, col in enumerate(df.columns) if col in column_set}
            else:
                common_columns = {col: idx for col, idx in common_columns.items() if col in column_set}
            if temperature is not None and angle is not None:
                for index, label in enumerate(df.columns):
                    if label not in column_set:
                        continue
                    plottable_column_counts[label] = plottable_column_counts.get(label, 0) + 1
                    if label not in plottable_column_order:
                        plottable_column_order[label] = index

        self._update_task_progress(
            value=max(1, len(paths)),
            title="Creating workbooks…",
        )

        if total_loaded == 0:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No VSM measurements could be loaded.",
            )
            self._end_task_progress()
            return

        self.measurements.sort(
            key=lambda m: (
                float('inf') if m.temperature is None else m.temperature,
                float('inf') if m.angle is None else m.angle,
            )
        )

        self._update_task_progress(
            value=max(1, len(paths)) + 1,
            title="Updating project tree and worksheets…",
        )
        self._update_sample_title()
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
        if plottable_column_counts:
            candidate_columns = [
                label
                for label, _ in sorted(
                    plottable_column_counts.items(),
                    key=lambda item: (
                        -item[1],
                        plottable_column_order.get(item[0], 10_000),
                        str(item[0]).lower(),
                    ),
                )
            ]
        elif common_columns:
            candidate_columns = list(common_columns.keys())
        elif self.measurements:
            candidate_columns = list(self.measurements[0].data.columns)
        else:
            candidate_columns = []
        if candidate_columns:
            self._populate_axis_combos(candidate_columns)

        self.plot_button.setEnabled(plottable > 0)
        self.export_button.setEnabled(True)
        self._update_project_actions()
        self._save_settings()
        self._update_task_progress(
            value=total_steps,
            title="Import complete.",
        )
        self._end_task_progress()

    def _populate_axis_combos(self, labels: List[str]) -> None:
        numeric_labels = [label for label in labels if label]
        preferred_x = [
            "Applied Field For Plot",
            "Applied Field For Plot [Oe]",
            "Raw Applied Field For Plot",
            "Raw Applied Field For Plot [Oe]",
            "Applied Field [Oe]",
            "Applied Field",
        ]
        preferred_y = [
            "Signal X direction",
            "Signal X direction [emu]",
            "Signal parallel with sample",
            "Signal Magnitude",
            "Moment [emu]",
        ]

        stored_x, stored_y = self._stored_axes
        if isinstance(stored_x, str):
            stored_x_lower = stored_x.lower()
            # VSM loop plots should default to a field axis; ignore unrelated
            # stored axes from prior plugin/state usage.
            if "applied field" not in stored_x_lower:
                stored_x = None
        if isinstance(stored_y, str):
            stored_y_lower = stored_y.lower()
            expected_y_tokens = (
                "signal x direction",
                "signal parallel with sample",
                "signal magnitude",
                "moment",
            )
            if not any(token in stored_y_lower for token in expected_y_tokens):
                stored_y = None
        if stored_x and stored_y:
            stored_x_lower = stored_x.lower()
            stored_y_lower = stored_y.lower()
            if any(pref.lower() in stored_x_lower for pref in preferred_y) and any(
                pref.lower() in stored_y_lower for pref in preferred_x
            ):
                stored_x = None
                stored_y = None
            # Avoid restoring diagnostic time/temperature defaults for loop plots.
            if "time" in stored_x_lower and "temp" in stored_y_lower:
                stored_x = None
                stored_y = None

        def _column_has_variation(label: str) -> bool:
            for measurement in self.measurements:
                if measurement.temperature is None or measurement.angle is None:
                    continue
                if label not in measurement.data.columns:
                    continue
                series = pd.to_numeric(measurement.data[label], errors="coerce").dropna()
                if series.empty:
                    continue
                try:
                    spread = float(series.max() - series.min())
                except Exception:
                    continue
                if not math.isfinite(spread):
                    continue
                if spread > 1e-12:
                    return True
            return False

        def _choose(
            preferences: Iterable[str],
            combo: QtWidgets.QComboBox,
            stored: str | None,
            *,
            avoid_raw: bool = False,
            prefer_varying: bool = False,
        ) -> None:
            def _accept(label: str) -> bool:
                lowered = label.lower()
                if avoid_raw and "raw applied field" in lowered:
                    return False
                if prefer_varying and not _column_has_variation(label):
                    return False
                return True

            stored_text = stored if stored in numeric_labels else None
            if stored_text and not _accept(stored_text):
                stored_text = None
            if stored_text:
                combo.setCurrentText(stored_text)
                return
            for pref in preferences:
                matches = [
                    label
                    for label in numeric_labels
                    if pref.lower() in label.lower() and _accept(label)
                ]
                if matches:
                    combo.setCurrentText(matches[0])
                    return
            if prefer_varying:
                varying = [label for label in numeric_labels if _accept(label)]
                if varying:
                    combo.setCurrentText(varying[0])
                    return
            if combo.count():
                combo.setCurrentIndex(0)

        for combo in (self.x_axis_combo, self.y_axis_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(numeric_labels)
            combo.blockSignals(False)
        _choose(
            preferred_x,
            self.x_axis_combo,
            stored_x,
            avoid_raw=True,
            prefer_varying=True,
        )
        _choose(preferred_y, self.y_axis_combo, stored_y)
        # Guard against stale settings selecting the same column for both axes.
        x_selected = self.x_axis_combo.currentText().strip()
        y_selected = self.y_axis_combo.currentText().strip()
        if x_selected and y_selected and x_selected == y_selected:
            replacement: str | None = None
            for pref in preferred_y:
                matches = [
                    label
                    for label in numeric_labels
                    if pref.lower() in label.lower() and label != x_selected
                ]
                if matches:
                    replacement = matches[0]
                    break
            if replacement is None:
                for label in numeric_labels:
                    if label != x_selected:
                        replacement = label
                        break
            if replacement is not None:
                self.y_axis_combo.setCurrentText(replacement)
        self._store_axis_selection()

    # ------------------------------------------------------------------ plotting helpers
    def _axis_spread_for_measurements(
        self,
        measurements: Sequence[VSMMeasurement],
        axis_label: str,
    ) -> float | None:
        label = str(axis_label or "").strip()
        if not label:
            return None
        spreads: List[float] = []
        for measurement in measurements:
            if label not in measurement.data.columns:
                continue
            series = pd.to_numeric(measurement.data[label], errors="coerce").dropna()
            if series.empty:
                continue
            try:
                spread = float(series.max() - series.min())
            except Exception:
                continue
            if math.isfinite(spread):
                spreads.append(abs(spread))
        if not spreads:
            return None
        return max(spreads)

    def _resolve_axis_for_loop_plot(
        self,
        selected_axis: str,
        measurements: Sequence[VSMMeasurement],
        *,
        axis: Literal["x", "y"],
        exclude: str | None = None,
    ) -> tuple[str, bool]:
        selected = str(selected_axis or "").strip()
        excluded = str(exclude or "").strip()
        combo = self.x_axis_combo if axis == "x" else self.y_axis_combo
        options: List[str] = []
        for index in range(combo.count()):
            label = combo.itemText(index).strip()
            if not label:
                continue
            if label not in options:
                options.append(label)

        if not options:
            return selected, False

        preferred = (
            [
                "Applied Field For Plot",
                "Applied Field For Plot [Oe]",
                "Raw Applied Field For Plot",
                "Raw Applied Field For Plot [Oe]",
                "Applied Field [Oe]",
                "Applied Field",
            ]
            if axis == "x"
            else [
                "Signal X direction",
                "Signal X direction [emu]",
                "Signal parallel with sample",
                "Signal Magnitude",
                "Moment [emu]",
            ]
        )

        def _accept(label: str) -> bool:
            if excluded and label == excluded:
                return False
            lowered = label.lower()
            if axis == "x" and "raw applied field" in lowered:
                return False
            spread = self._axis_spread_for_measurements(measurements, label)
            return spread is not None and spread > 1e-12

        current_spread = self._axis_spread_for_measurements(measurements, selected)
        if (
            selected
            and (not excluded or selected != excluded)
            and current_spread is not None
            and current_spread > 1e-12
        ):
            return selected, False

        for token in preferred:
            for label in options:
                if token.lower() in label.lower() and _accept(label):
                    return label, label != selected

        for label in options:
            if _accept(label):
                return label, label != selected

        return selected, False

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
        if x_axis == y_axis:
            QtWidgets.QMessageBox.warning(
                self,
                "VSM Hysteresis Loops",
                "Select different X and Y axes for plotting.",
            )
            return

        total_steps = max(1, len(self.measurements)) + 4
        self._begin_task_progress(
            "Preparing hysteresis plots…",
            maximum=total_steps,
            value=0,
        )

        self.open_origin_button.setEnabled(False)
        self.export_button.setEnabled(False)
        target_temp = self.temperature_combo.currentData()
        eligible_measurements = [
            measurement
            for measurement in self.measurements
            if measurement.temperature is not None
            and measurement.angle is not None
            and (target_temp is None or measurement.temperature == target_temp)
        ]
        resolved_x, x_changed = self._resolve_axis_for_loop_plot(
            x_axis,
            eligible_measurements,
            axis="x",
        )
        resolved_y, y_changed = self._resolve_axis_for_loop_plot(
            y_axis,
            eligible_measurements,
            axis="y",
            exclude=resolved_x,
        )
        if x_changed:
            self.x_axis_combo.blockSignals(True)
            self.x_axis_combo.setCurrentText(resolved_x)
            self.x_axis_combo.blockSignals(False)
            self._append_log(
                f"Selected X axis '{x_axis}' had no usable variation; switched to '{resolved_x}'."
            )
        if y_changed:
            self.y_axis_combo.blockSignals(True)
            self.y_axis_combo.setCurrentText(resolved_y)
            self.y_axis_combo.blockSignals(False)
            self._append_log(
                f"Selected Y axis '{y_axis}' had no usable variation; switched to '{resolved_y}'."
            )
        x_axis = resolved_x
        y_axis = resolved_y

        groups: Dict[PlotGroupKey, List[VSMMeasurement]] = {}
        for position, measurement in enumerate(self.measurements, start=1):
            self._update_task_progress(
                value=position - 1,
                title=f"Preparing data ({position}/{len(self.measurements)})",
            )
            if measurement.angle is None:
                continue
            group_key = self._plot_group_key(measurement.sample_name, measurement.temperature)
            if group_key is None:
                continue
            if target_temp is not None and float(group_key[1]) != float(target_temp):
                continue
            if x_axis not in measurement.data.columns or y_axis not in measurement.data.columns:
                self._append_log(f"Skipping {measurement.path.name} because it lacks the selected axes.")
                continue
            groups.setdefault(group_key, []).append(measurement)

        if not groups:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No measurements match the selected filters and axes.",
            )
            self._end_task_progress()
            return

        prepared_groups: Dict[PlotGroupKey, List[tuple[VSMMeasurement, pd.DataFrame]]] = {}
        for group_key, measurement_list in sorted(
            groups.items(),
            key=lambda item: (item[0][0].lower(), float(item[0][1])),
        ):
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
                prepared_groups[group_key] = prepared

        if not prepared_groups:
            self._reset_object_manager()
            self._update_angle_overlay_options({})
            self._append_log(
                "No numeric data matched the selected axes; nothing to plot."
            )
            self._update_normalize_enabled()
            self._end_task_progress()
            return

        self._update_task_progress(
            value=max(1, len(self.measurements)) + 1,
            title="Rendering graphs…",
        )

        self._update_angle_overlay_options(prepared_groups)

        rescale_info: Dict[PlotGroupKey, Dict[Path, RescaleResult]] = {}
        rescale_enabled = False

        plot_exports: Dict[tuple[str, float, float], PlotSeriesExport] = {}
        metric_records: List[tuple[float, float, MetricResult]] = []
        coercivity_debug_entries: Dict[float, List[Dict[str, float]]] = {}
        remanence_debug_entries: Dict[float, List[Dict[str, float]]] = {}
        self._metric_results = {}
        for group_key, entries in prepared_groups.items():
            sample_key, temperature = group_key
            for measurement, subset in entries:
                if measurement.angle is None:
                    continue
                export_subset = subset.copy()
                if export_subset.empty:
                    continue
                key = (sample_key, float(temperature), float(measurement.angle))
                plot_exports[key] = PlotSeriesExport(
                    temperature=float(temperature),
                    angle=float(measurement.angle),
                    data=export_subset[[x_axis, y_axis]].copy(),
                    x_axis=x_axis,
                    y_axis=y_axis,
                    rescaled=False,
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

                raw_first = math.nan
                raw_second = math.nan
                raw_values: List[float] = []
                if metrics.coercivity_raw_pair:
                    first_value, second_value = metrics.coercivity_raw_pair
                    if first_value is not None and math.isfinite(first_value):
                        raw_first = float(first_value)
                        raw_values.append(float(first_value))
                    if second_value is not None and math.isfinite(second_value):
                        raw_second = float(second_value)
                        raw_values.append(float(second_value))

                sym_neg = math.nan
                sym_pos = math.nan
                corrected = math.nan
                if metrics.coercivity_pair:
                    neg_value, pos_value = metrics.coercivity_pair
                    if neg_value is not None and math.isfinite(neg_value):
                        sym_neg = float(neg_value)
                    if pos_value is not None and math.isfinite(pos_value):
                        sym_pos = float(pos_value)
                        corrected = float(pos_value)

                original = math.nan
                if raw_values:
                    positive_candidates = [value for value in raw_values if value >= 0]
                    if positive_candidates:
                        original = float(min(positive_candidates, key=abs))
                    else:
                        original = float(min(abs(value) for value in raw_values))

                coercivity_debug_entries.setdefault(float(temperature), []).append(
                    {
                        "angle": float(measurement.angle),
                        "x_column": x_axis,
                        "y_column": y_axis,
                        "raw_first": raw_first,
                        "raw_second": raw_second,
                        "sym_neg": sym_neg,
                        "sym_pos": sym_pos,
                        "original": original,
                        "corrected": corrected,
                    }
                )

                rem_raw_first = math.nan
                rem_raw_second = math.nan
                rem_raw_values: List[float] = []
                if metrics.remanence_raw_pair:
                    first_value, second_value = metrics.remanence_raw_pair
                    if first_value is not None and math.isfinite(first_value):
                        rem_raw_first = float(first_value)
                        rem_raw_values.append(float(first_value))
                    if second_value is not None and math.isfinite(second_value):
                        rem_raw_second = float(second_value)
                        rem_raw_values.append(float(second_value))

                rem_sym_neg = math.nan
                rem_sym_pos = math.nan
                rem_corrected = math.nan
                if metrics.remanence_pair:
                    neg_value, pos_value = metrics.remanence_pair
                    if neg_value is not None and math.isfinite(neg_value):
                        rem_sym_neg = float(neg_value)
                    if pos_value is not None and math.isfinite(pos_value):
                        rem_sym_pos = float(pos_value)
                        rem_corrected = float(pos_value)

                rem_original = math.nan
                if rem_raw_values:
                    positive_candidates = [value for value in rem_raw_values if value >= 0]
                    if positive_candidates:
                        rem_original = float(min(positive_candidates, key=abs))
                    else:
                        rem_original = float(min(abs(value) for value in rem_raw_values))

                remanence_debug_entries.setdefault(float(temperature), []).append(
                    {
                        "angle": float(measurement.angle),
                        "x_column": x_axis,
                        "y_column": y_axis,
                        "raw_first": rem_raw_first,
                        "raw_second": rem_raw_second,
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
            x_label: str,
            y_label: str,
        ) -> tuple[Dict[float, pd.DataFrame], Dict[str, str]]:
            if not entries:
                return {}, {}
            spec = METRIC_DEBUG_SPECS.get(metric_key)
            if spec is None:
                return {}, {}
            metric_label = spec["label"]
            angle_label = _format_column_with_unit("Angle", "deg")
            x_label = str(x_label)
            y_label = str(y_label)
            x_column_label = f"X column ({x_label})"
            y_column_label = f"Y column ({y_label})"
            raw_first_label = _format_column_with_unit("Crossing 1", unit)
            raw_second_label = _format_column_with_unit("Crossing 2", unit)
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
                        "x_column": x_column_label,
                        "y_column": y_column_label,
                        "raw_first": raw_first_label,
                        "raw_second": raw_second_label,
                        "sym_neg": sym_neg_label,
                        "sym_pos": sym_pos_label,
                        "original": original_label,
                        "corrected": corrected_label,
                    },
                    inplace=True,
                )
                desired_columns = [
                    angle_label,
                    raw_first_label,
                    raw_second_label,
                    sym_neg_label,
                    sym_pos_label,
                    original_label,
                    corrected_label,
                    x_column_label,
                    y_column_label,
                ]
                df = df[[column for column in desired_columns if column in df.columns]]
                tables[temperature] = df

            column_map = {
                "angle": angle_label,
                "x_column": x_column_label,
                "y_column": y_column_label,
                "raw_first": raw_first_label,
                "raw_second": raw_second_label,
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
            tables, columns = _build_debug_payload(entries, unit, metric_key, x_axis, y_axis)
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

        self.tab_widget.clear()
        self._render_matplotlib(prepared_groups, rescale_info, x_axis, y_axis, rescale_enabled)
        self._update_task_progress(
            value=max(1, len(self.measurements)) + 3,
            title="Finalizing graph objects…",
        )

        self.open_origin_button.setEnabled(bool(prepared_groups))
        self.export_button.setEnabled(True)

        self._update_save_graph_enabled()
        self._update_normalize_enabled()
        self._rebuild_object_manager_for_tab(self.tab_widget.currentWidget())
        sync_shared = getattr(self, "_sync_shared_action_states", None)
        if callable(sync_shared):
            try:
                sync_shared()
            except Exception:
                pass

        if not self._tab_descriptors:
            self.export_button.setEnabled(False)
            self.open_origin_button.setEnabled(False)
        self._update_task_progress(
            value=total_steps,
            title="Plot generation complete.",
        )
        self._end_task_progress()

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
        grouped: Dict[PlotGroupKey, List[VSMMeasurement]] = {}
        for measurement in self.measurements:
            group_key = self._plot_group_key(measurement.sample_name, measurement.temperature)
            if group_key is None:
                continue
            if x_axis not in measurement.data.columns or y_axis not in measurement.data.columns:
                continue
            grouped.setdefault(group_key, []).append(measurement)

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
        style = self._plot_style_token()
        if style == "line_markers":
            return {"linestyle": "-", "marker": "o", "markersize": 4}
        return {"linestyle": "-"}

    def _plot_style_token(self) -> str:
        try:
            combo = self.style_combo
        except Exception:
            combo = None
        if combo is not None:
            try:
                token = combo.currentData()
            except Exception:
                token = None
            if isinstance(token, str) and token:
                return token
        return "line"

    def _plot_dark_enabled(self) -> bool:
        try:
            action = getattr(self, "_dark_mode_action", None)
        except Exception:
            action = None
        if action is not None:
            is_checked = getattr(action, "isChecked", None)
            if callable(is_checked):
                try:
                    return bool(is_checked())
                except Exception:
                    pass
        try:
            toggle = self.dark_mode_checkbox
        except Exception:
            toggle = None
        if toggle is not None:
            try:
                return bool(toggle.isChecked())
            except Exception:
                pass
        try:
            return bool(self._dark_mode_enabled)
        except Exception:
            return False

    def _graph_options_for_axes(self, axes: Any) -> Dict[str, Any]:
        try:
            resolver = getattr(self, "_effective_graph_options", None)
        except Exception:
            return {}
        if not callable(resolver):
            return {}
        descriptor = None
        try:
            tab_for_axes = getattr(self, "_tab_for_axes", None)
        except Exception:
            tab_for_axes = None
        if callable(tab_for_axes):
            try:
                tab = tab_for_axes(axes)
            except Exception:
                tab = None
            if tab is not None:
                try:
                    descriptor = self._tab_descriptors.get(tab)
                except Exception:
                    descriptor = None
        plugin_name = None
        try:
            tab_plugin_name = getattr(self, "_tab_plugin_name", None)
        except Exception:
            tab_plugin_name = None
        if callable(tab_plugin_name):
            try:
                plugin_name = tab_plugin_name(descriptor)
            except Exception:
                plugin_name = None
        if not plugin_name:
            try:
                plugin_name = getattr(self, "_current_plotter_name", None)
            except Exception:
                plugin_name = None
        try:
            options = resolver(plugin_name)
        except TypeError:
            try:
                options = resolver(None)
            except Exception:
                options = {}
        except Exception:
            options = {}
        return dict(options) if isinstance(options, dict) else {}

    def _apply_plot_theme(self, axes: Any) -> None:
        """Apply the current light/dark theme to ``axes``."""

        dark = self._plot_dark_enabled()
        apply_dark_mode = getattr(self, "_apply_dark_mode_to_axes", None)
        if callable(apply_dark_mode):
            try:
                apply_dark_mode(axes, dark)
            except Exception:
                pass

    def _style_legend(self, legend: Any | None) -> None:
        """Restyle ``legend`` to match the current theme."""

        if legend is None:
            return
        axes = getattr(legend, "axes", None)
        if axes is not None:
            try:
                if axes.get_legend() is legend:
                    # Shared graph formatting manages the primary legend.
                    return
            except Exception:
                pass
        dark = self._plot_dark_enabled()
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

        for legend in list(self._direction_legends.values()):
            self._style_legend(legend)

    def _refresh_tab_legend(self, tab_state: PlotTabState, *, draw: bool = True) -> None:
        tab = None
        try:
            tab_for_axes = getattr(self, "_tab_for_axes", None)
        except Exception:
            tab_for_axes = None
        if callable(tab_for_axes):
            try:
                tab = tab_for_axes(tab_state.axes)
            except Exception:
                tab = None
        descriptor = self._tab_descriptors.get(tab) if tab is not None else None
        plugin_name = None
        try:
            tab_plugin_name = getattr(self, "_tab_plugin_name", None)
        except Exception:
            tab_plugin_name = None
        if callable(tab_plugin_name):
            try:
                plugin_name = tab_plugin_name(descriptor)
            except Exception:
                plugin_name = None
        try:
            legend = self._sync_axes_legend_with_visible_lines(
                tab_state.axes,
                plugin_name=plugin_name,
            )
        except Exception:
            legend = None
            old_legend = getattr(tab_state.axes, "legend_", None)
            if old_legend is not None:
                try:
                    old_legend.remove()
                except Exception:
                    pass
            visible_lines = [line for line in tab_state.lines.values() if line.get_visible()]
            labels = [line.get_label() for line in visible_lines]
            try:
                legend = tab_state.axes.legend(visible_lines, labels, loc="best")
            except Exception:
                legend = None
        self._apply_plot_theme(tab_state.axes)
        self._style_legend(legend)
        try:
            fit_to_content = getattr(self, "_fit_figure_to_content", None)
        except Exception:
            fit_to_content = None
        if callable(fit_to_content):
            try:
                fit_to_content(tab_state.axes.figure)
            except Exception:
                pass
        else:
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
        plugin_name = None
        try:
            tab_plugin_name = getattr(self, "_tab_plugin_name", None)
        except Exception:
            tab_plugin_name = None
        if callable(tab_plugin_name):
            try:
                plugin_name = tab_plugin_name(descriptor)
            except Exception:
                plugin_name = None
        try:
            legend = self._sync_axes_legend_with_visible_lines(
                descriptor.axes,
                plugin_name=plugin_name,
            )
        except Exception:
            legend = None
            old_legend = getattr(descriptor.axes, "legend_", None)
            if old_legend is not None:
                try:
                    old_legend.remove()
                except Exception:
                    pass
            visible_states = [
                state for state in descriptor.lines.values() if state.line.get_visible()
            ]
            if visible_states:
                handles = [self._legend_handle_for_state(state) for state in visible_states]
                try:
                    legend = descriptor.axes.legend(
                        handles,
                        [state.label for state in visible_states],
                        loc="best",
                    )
                except Exception:
                    legend = None
        self._apply_plot_theme(descriptor.axes)
        self._style_legend(legend)

        if force_layout or not descriptor.layout_initialized:
            try:
                fit_to_content = getattr(self, "_fit_figure_to_content", None)
            except Exception:
                fit_to_content = None
            if callable(fit_to_content):
                try:
                    fit_to_content(descriptor.axes.figure)
                except Exception:
                    pass
            else:
                try:
                    descriptor.axes.figure.tight_layout()
                except Exception:  # pragma: no cover - backend specific
                    pass
            descriptor.layout_initialized = True
        try:
            descriptor.canvas.draw_idle()
        except Exception:  # pragma: no cover - backend specific
            pass
        self._update_direction_legend(descriptor, self._field_direction_enabled)

    def _legend_handle_for_state(self, state: GraphLineState) -> Line2D:
        try:
            return state.line
        except Exception:
            handle = Line2D([], [])
        try:
            color = state.line.get_color()
        except Exception:
            color = None
        if color:
            handle.set_color(color)
        try:
            linewidth = state.line.get_linewidth()
        except Exception:
            linewidth = None
        if linewidth is not None:
            handle.set_linewidth(linewidth)
        marker = None
        try:
            marker = state.line.get_marker()
        except Exception:
            marker = None
        if marker:
            handle.set_marker(marker)
            if marker != "None":
                try:
                    markersize = state.line.get_markersize()
                except Exception:
                    markersize = None
                if markersize is not None:
                    handle.set_markersize(markersize)
        if self._field_direction_enabled:
            style = self._line_style_kwargs().get("linestyle", "-")
            handle.set_linestyle(style)
        else:
            try:
                handle.set_linestyle(state.line.get_linestyle())
            except Exception:
                handle.set_linestyle("-")
        return handle

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
            data = np.asarray(state.y_data(), dtype=float)
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
            child.setData(0, OBJECT_TREE_STATE_ROLE, line_state)
            root.addChild(child)
            self._object_items[(tab, key)] = child

        self.object_tree.expandAll()
        self.object_tree.blockSignals(False)

    def _object_manager_sort_key(self, item: tuple[tuple[str, float | str], GraphLineState]) -> tuple[int, float | str, str]:
        """Sort object manager entries numerically when possible."""

        key, state = item
        numeric: float | None = None

        if isinstance(key, tuple) and len(key) == 2:
            candidate = key[1]
            if isinstance(candidate, (int, float)):
                numeric = float(candidate)

        if numeric is None:
            match = re.search(r"-?\d+(?:\.\d+)°", state.label)
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
        prepared_groups: Dict[PlotGroupKey, List[tuple[VSMMeasurement, pd.DataFrame]]],
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
        self.metrics_temperature_button.setEnabled(has_metrics and has_angles)

    def _handle_object_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        raw_payload = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        descriptor: TabDescriptor | None = None
        normalized_key: Any | None = None
        if isinstance(raw_payload, (tuple, list)) and len(raw_payload) == 2:
            candidate, key_raw = raw_payload
            if isinstance(candidate, QtWidgets.QWidget):
                descriptor = self._tab_descriptors.get(candidate)
            elif isinstance(candidate, TabDescriptor):
                descriptor = candidate
            if isinstance(key_raw, (list, tuple)):
                normalized_key = tuple(key_raw)
            else:
                normalized_key = key_raw
        if descriptor is None:
            return
        if normalized_key is None:
            return
        line_state = item.data(0, OBJECT_TREE_STATE_ROLE)
        if not isinstance(line_state, GraphLineState):
            line_state = descriptor.lines.get(normalized_key)
            if isinstance(line_state, GraphLineState):
                item.setData(0, OBJECT_TREE_STATE_ROLE, line_state)
        if not isinstance(line_state, GraphLineState):
            return
        new_visible = item.checkState(0) == QtCore.Qt.CheckState.Checked
        old_visible = line_state.line.get_visible()
        if new_visible == old_visible:
            return

        def _apply(flag: bool) -> None:
            for segment in line_state.iter_lines():
                try:
                    segment.set_visible(flag)
                except Exception:
                    pass
            if descriptor.kind == "temperature":
                group_key = self._descriptor_plot_group_key(descriptor)
                if group_key is not None:
                    visibility = self._line_visibility_for_group(group_key)
                    angle_value = None
                    if isinstance(normalized_key, tuple) and len(normalized_key) == 2:
                        angle_value = normalized_key[1]
                    if isinstance(angle_value, (int, float)):
                        visibility[float(angle_value)] = flag
            self._refresh_descriptor_legend(descriptor)

        handled = False
        result_visible = new_visible
        action = "Show" if new_visible else "Hide"

        if (
            descriptor.kind == "temperature"
            and isinstance(normalized_key, tuple)
            and len(normalized_key) == 2
            and normalized_key[0] == "angle"
        ):
            group_key = self._descriptor_plot_group_key(descriptor)
            angle_value = normalized_key[1]
            if group_key is not None and isinstance(angle_value, (int, float)):
                angle_f = float(angle_value)
                self._toggle_line_visibility(group_key, angle_f, new_visible)
                result_visible = bool(line_state.line.get_visible())

                self._record_history_action(
                    f"{action} {line_state.label}",
                    undo=lambda gk=group_key, av=angle_f: self._toggle_line_visibility(gk, av, old_visible),
                    redo=lambda gk=group_key, av=angle_f: self._toggle_line_visibility(gk, av, new_visible),
                )
                handled = True

        if not handled:
            _apply(new_visible)
            result_visible = bool(line_state.line.get_visible())
            self._record_history_action(
                f"{action} {line_state.label}",
                undo=lambda: _apply(old_visible),
                redo=lambda: _apply(new_visible),
            )

        tree = getattr(self, "object_tree", None)
        try:
            if isinstance(tree, QtWidgets.QTreeWidget):
                tree.blockSignals(True)
            item.setCheckState(
                0,
                QtCore.Qt.CheckState.Checked if result_visible else QtCore.Qt.CheckState.Unchecked,
            )
        finally:
            if isinstance(tree, QtWidgets.QTreeWidget):
                tree.blockSignals(False)

    def _toggle_line_visibility(self, group: Any, angle: float, visible: bool) -> None:
        try:
            attrs = object.__getattribute__(self, "__dict__")
        except Exception:
            attrs = {}
        if "_direction_legends" not in attrs:
            attrs["_direction_legends"] = {}
            try:
                self._direction_legends = attrs["_direction_legends"]
            except Exception:
                pass
        if "_field_direction_enabled" not in attrs:
            attrs["_field_direction_enabled"] = False
            try:
                self._field_direction_enabled = False
            except Exception:
                pass
        plot_tabs = attrs.get("_plot_tabs", {})
        group_key: PlotGroupKey | None = None
        legacy_temperature: float | None = None
        tab_lookup_key: Any = None
        if isinstance(group, (tuple, list)) and len(group) == 2:
            sample = group[0]
            temperature = group[1]
            if isinstance(sample, str) and isinstance(temperature, (int, float)):
                group_key = (sample.strip() or "Unknown sample", float(temperature))
                tab_lookup_key = group_key
        elif isinstance(group, (int, float)):
            legacy_temperature = float(group)
            tab_lookup_key = legacy_temperature
            for candidate in plot_tabs.keys():
                if (
                    isinstance(candidate, tuple)
                    and len(candidate) == 2
                    and isinstance(candidate[1], (int, float))
                    and math.isclose(float(candidate[1]), legacy_temperature, rel_tol=0.0, abs_tol=1e-6)
                ):
                    group_key = (str(candidate[0]), float(candidate[1]))
                    tab_lookup_key = candidate
                    break
        if group_key is None and legacy_temperature is None:
            return
        tab_state = plot_tabs.get(tab_lookup_key)
        if tab_state is None and group_key is not None:
            tab_state = plot_tabs.get(group_key)
        if tab_state is None and legacy_temperature is not None:
            tab_state = plot_tabs.get(legacy_temperature)
            if tab_state is not None:
                tab_lookup_key = legacy_temperature
        if tab_state is None:
            return
        line = tab_state.lines.get(float(angle))
        if line is None:
            return
        try:
            line.set_visible(visible)
        except Exception:
            pass
        self._refresh_tab_legend(tab_state)

        line_visibility = attrs.setdefault("_line_visibility", {})
        if group_key is not None:
            line_visibility.setdefault(group_key, {})[float(angle)] = visible
        if legacy_temperature is not None:
            line_visibility.setdefault(float(legacy_temperature), {})[float(angle)] = visible
        try:
            self._line_visibility = line_visibility
        except Exception:
            pass

        tab_descriptors = attrs.get("_tab_descriptors", {})
        for _tab, descriptor in tab_descriptors.items():
            if descriptor.kind != "temperature":
                continue
            descriptor_group_key = self._descriptor_plot_group_key(descriptor)
            if descriptor_group_key is not None:
                if group_key is not None and descriptor_group_key != group_key:
                    continue
                if (
                    group_key is None
                    and legacy_temperature is not None
                    and not math.isclose(
                        float(descriptor_group_key[1]),
                        float(legacy_temperature),
                        rel_tol=0.0,
                        abs_tol=1e-6,
                    )
                ):
                    continue
            elif legacy_temperature is not None:
                descriptor_temp = descriptor.metadata.get("temperature")
                if not isinstance(descriptor_temp, (int, float)):
                    continue
                if not math.isclose(
                    float(descriptor_temp),
                    float(legacy_temperature),
                    rel_tol=0.0,
                    abs_tol=1e-6,
                ):
                    continue
            else:
                continue
            state = descriptor.lines.get(("angle", float(angle)))
            if state is None:
                continue
            for segment in state.iter_lines():
                try:
                    segment.set_visible(visible)
                except Exception:
                    pass
            self._refresh_descriptor_legend(descriptor)
            break

    def _after_tab_removed(self, info: Any) -> None:  # type: ignore[override]
        PyPlotWindow._after_tab_removed(self, info)

        tab = getattr(info, "tab", None)
        descriptor = getattr(info, "descriptor", None)
        extra = getattr(info, "extra", {})
        if not isinstance(extra, dict):
            extra = {}
            setattr(info, "extra", extra)

        extra.setdefault("collections", {})
        collections: Dict[str, int] = extra["collections"]

        for key, collection in (
            ("temperature", self._temperature_tab_widgets),
            ("metrics_angle", self._metrics_angle_tabs),
            ("metrics_temperature", self._metrics_temperature_tabs),
            ("overlay", self._overlay_tab_widgets),
        ):
            if tab in collection:
                index = collection.index(tab)
                collection.pop(index)
                collections[key] = index

        if isinstance(descriptor, TabDescriptor):
            if descriptor.kind == "temperature":
                group_key = self._descriptor_plot_group_key(descriptor)
                if group_key is not None:
                    tab_state = self._plot_tabs.pop(group_key, None)
                    if tab_state is not None:
                        extra["plot_tab_state"] = tab_state
                    visibility = self._line_visibility.pop(group_key, None)
                    if visibility is not None:
                        extra["line_visibility"] = visibility

        extra["export_enabled_before"] = self.export_button.isEnabled()
        extra["origin_enabled_before"] = self.open_origin_button.isEnabled()

        if not self._tab_descriptors:
            self.open_origin_button.setEnabled(False)
        self.export_button.setEnabled(False)
        sync_shared = getattr(self, "_sync_shared_action_states", None)
        if callable(sync_shared):
            try:
                sync_shared()
            except Exception:
                pass

        axes = getattr(info, "axes", None)
        legend = None
        if axes is not None:
            legend = self._direction_legends.pop(axes, None)
        if legend is not None:
            try:
                legend.remove()
            except Exception:
                pass

    def _after_tab_restored(self, info: Any) -> None:  # type: ignore[override]
        PyPlotWindow._after_tab_restored(self, info)
        descriptor = getattr(info, "descriptor", None)
        tab = getattr(info, "tab", None)
        extra = getattr(info, "extra", {})

        collections: Dict[str, int] = {}
        if isinstance(extra, dict):
            collections = dict(extra.get("collections", {}))

        for key, collection in (
            ("temperature", self._temperature_tab_widgets),
            ("metrics_angle", self._metrics_angle_tabs),
            ("metrics_temperature", self._metrics_temperature_tabs),
            ("overlay", self._overlay_tab_widgets),
        ):
            if tab is None:
                continue
            if key in collections:
                index = collections[key]
                if 0 <= index <= len(collection):
                    collection.insert(index, tab)
                elif tab not in collection:
                    collection.append(tab)

        if isinstance(descriptor, TabDescriptor):
            if descriptor.kind == "temperature":
                group_key = self._descriptor_plot_group_key(descriptor)
                if group_key is not None:
                    tab_state = extra.get("plot_tab_state") if isinstance(extra, dict) else None
                    if isinstance(tab_state, PlotTabState):
                        self._plot_tabs[group_key] = tab_state
                    visibility = extra.get("line_visibility") if isinstance(extra, dict) else None
                    if isinstance(visibility, dict):
                        self._line_visibility[group_key] = visibility
            self._apply_direction_split_to_descriptor(descriptor)
            self._refresh_descriptor_legend(descriptor, force_layout=True)

        if isinstance(extra, dict):
            if "export_enabled_before" in extra:
                self.export_button.setEnabled(bool(extra["export_enabled_before"]))
            if "origin_enabled_before" in extra:
                self.open_origin_button.setEnabled(bool(extra["origin_enabled_before"]))
        sync_shared = getattr(self, "_sync_shared_action_states", None)
        if callable(sync_shared):
            try:
                sync_shared()
            except Exception:
                pass

    def _plot_angle_overlays(self) -> None:
        if not self._last_prepared_groups or not self._last_axes:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Plot loops before creating angle overlays.",
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
            raw_sample_names: set[str] = set()
            for group_key, entries in sorted(
                self._last_prepared_groups.items(),
                key=lambda item: (float(item[0][1]), item[0][0].lower()),
            ):
                sample_key, temperature = group_key
                for measurement, subset in entries:
                    if measurement.angle is None:
                        continue
                    if not math.isclose(float(measurement.angle), angle, abs_tol=0.05):
                        continue
                    if measurement.sample_name:
                        raw_sample_names.add(str(measurement.sample_name))
                    series_y = _numeric_column(subset, y_axis)
                    if rescale_enabled:
                        result = rescale_info.get(group_key, {}).get(measurement.path)
                        if result is not None:
                            if result.replacement is not None:
                                replacement = result.replacement.reindex(subset.index)
                                series_y = replacement
                            else:
                                series_y = series_y * result.scale + result.offset
                    numeric_x = _numeric_column(subset, x_axis).to_numpy()
                    numeric_y = pd.to_numeric(series_y, errors="coerce").to_numpy()
                    if numeric_x.size == 0 or numeric_y.size == 0:
                        continue
                    sample_label = self._format_sample_label(sample_key) or sample_key
                    label = self._series_label(
                        measurement,
                        y_axis,
                        f"{sample_label} {temperature:g} °C",
                    )
                    line, = ax.plot(
                        numeric_x,
                        numeric_y,
                        label=label,
                        **line_kwargs,
                    )
                    line_key = ("temperature", f"{sample_key}|{float(temperature):g}")
                    lines[line_key] = GraphLineState(
                        key=line_key,
                        label=label,
                        line=line,
                        base_x=numeric_x,
                        base_y=numeric_y,
                        full_x=numeric_x,
                        full_y=numeric_y,
                    )
                    plotted = True
            formatted_samples = {
                sample
                for sample in (
                    self._format_sample_label(name) for name in raw_sample_names
                )
                if sample
            }

            if plotted:
                overlay_title = f"{angle:g}° across temperatures"
                if len(formatted_samples) == 1:
                    overlay_title = f"{next(iter(formatted_samples))} — {overlay_title}"
                ax.set_title(overlay_title)
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
            if len(formatted_samples) == 1:
                descriptor.metadata["sample_name"] = next(iter(formatted_samples))
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


    def _write_plotted_series(
        self,
        target_dir: Path,
        descriptors: Sequence[TabDescriptor],
        *,
        include_hidden: bool = False,
    ) -> int:
        exported = 0
        for descriptor in descriptors:
            temperature = descriptor.metadata.get("temperature")
            if isinstance(temperature, (int, float)):
                base_dir = target_dir / _temperature_subfolder_name(float(temperature))
            else:
                base_label = _clean_folder_name(descriptor.root_label or descriptor.title) or "Plot"
                base_dir = target_dir / base_label
            base_dir.mkdir(parents=True, exist_ok=True)

            for state in descriptor.lines.values():
                if not include_hidden and not state.line.get_visible():
                    continue
                x_data = np.asarray(state.x_data(), dtype=float)
                y_data = np.asarray(state.y_data(), dtype=float)
                if x_data.size == 0 or y_data.size == 0 or x_data.shape != y_data.shape:
                    continue

                data_frame = pd.DataFrame({
                    descriptor.x_label: x_data,
                    descriptor.y_label: y_data,
                })

                series_label = _clean_folder_name(state.label) or "Series"
                x_label_clean = _clean_folder_name(descriptor.x_label) or "X"
                y_label_clean = _clean_folder_name(descriptor.y_label) or "Y"
                filename = base_dir / f"{series_label}_{y_label_clean}_vs_{x_label_clean}.txt"
                counter = 2
                while filename.exists():
                    filename = base_dir / f"{series_label}_{y_label_clean}_vs_{x_label_clean}_{counter}.txt"
                    counter += 1

                metadata = {
                    "title": descriptor.title,
                    "series": state.label,
                    "normalized": state.normalized,
                    "x_axis": descriptor.x_label,
                    "y_axis": descriptor.y_label,
                }
                if isinstance(temperature, (int, float)):
                    metadata["temperature"] = float(temperature)

                angle_value = None
                key = state.key
                if isinstance(key, tuple) and len(key) == 2:
                    angle_value = key[1]
                    if isinstance(angle_value, (int, float)):
                        metadata["angle"] = float(angle_value)

                entry = None
                sample_name = descriptor.metadata.get("sample_name")
                if (
                    isinstance(sample_name, str)
                    and isinstance(temperature, (int, float))
                    and isinstance(angle_value, (int, float))
                ):
                    entry = self._plotted_series_exports.get(
                        (sample_name.strip() or "Unknown sample", float(temperature), float(angle_value))
                    )
                if entry is not None:
                    metadata["source"] = entry.source.name
                    metadata["rescaled"] = entry.rescaled

                axis_roles = {
                    descriptor.x_label: "X axis",
                    descriptor.y_label: "Y axis",
                }

                try:
                    _write_origin_ascii(filename, data_frame, metadata=metadata, axis_roles=axis_roles)
                except Exception as exc:
                    self._append_log(
                        f"Failed to export {state.label!r} from {descriptor.title}: {exc}"
                    )
                    continue

                exported += 1

        return exported

    def _plot_metrics_vs_angle(self) -> None:
        if not self._metrics_by_temperature:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Plot loops to compute derived metrics first.",
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
                    full_x=numeric_x,
                    full_y=numeric_y,
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
                "Plot loops to compute derived metrics first.",
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
                    full_x=numeric_x,
                    full_y=numeric_y,
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
                "Plot loops to compute derived metrics first.",
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
            ax.plot(state.x_data(), state.y_data(), **kwargs)
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

    def _descriptor_title_for_labels(self, descriptor: TabDescriptor, y_label: str) -> str:
        """Return a title string that reflects ``descriptor`` and ``y_label``."""

        x_label = descriptor.x_label
        if descriptor.kind == "temperature":
            temperature = descriptor.metadata.get("temperature")
            if isinstance(temperature, (int, float)):
                return f"{y_label} vs {x_label} at {float(temperature):g} °C"
        if descriptor.kind == "overlay":
            angle = descriptor.metadata.get("angle")
            if isinstance(angle, (int, float)):
                return f"{y_label} vs {x_label} at {float(angle):g}°"
        return f"{y_label} vs {x_label}"

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

        if descriptor.kind not in _NORMALIZABLE_TAB_KINDS:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Normalization is only available for hysteresis loops and angle overlays.",
            )
            return

        descriptor.metadata.setdefault(_BASE_Y_LABEL_KEY, descriptor.y_label)
        descriptor.metadata.setdefault(_BASE_TITLE_KEY, descriptor.title)

        if all(state.normalized for state in descriptor.lines.values()):
            for state in descriptor.lines.values():
                state.line.set_ydata(state.base_y)
                state.normalized = False
                try:
                    state.full_y = np.asarray(state.base_y, dtype=float)
                except Exception:
                    state.full_y = state.base_y

            original_label = descriptor.metadata.get(_BASE_Y_LABEL_KEY, descriptor.y_label)
            descriptor.y_label = str(original_label or descriptor.y_label)
            descriptor.axes.set_ylabel(descriptor.y_label)
            original_title = descriptor.metadata.get(_BASE_TITLE_KEY, descriptor.title)
            descriptor.title = str(original_title or descriptor.title)
            descriptor.axes.set_title(descriptor.title)
            self._update_graph_tree_for_tab(tab)

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

            self._apply_direction_split_to_descriptor(descriptor)
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
            try:
                state.full_y = np.asarray(normalized, dtype=float)
            except Exception:
                state.full_y = normalized
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
        base_label = descriptor.metadata.get(_BASE_Y_LABEL_KEY, descriptor.y_label)
        descriptor.y_label = _normalized_axis_label(base_label or descriptor.y_label)
        descriptor.axes.set_ylabel(descriptor.y_label)
        descriptor.title = self._descriptor_title_for_labels(descriptor, descriptor.y_label)
        descriptor.axes.set_title(descriptor.title)
        self._update_graph_tree_for_tab(tab)
        self._apply_direction_split_to_descriptor(descriptor)
        self._refresh_descriptor_legend(descriptor, force_layout=True)
        self._append_log(
            "Normalized the current graph and rescaled the Y axis to fit the data."
        )


    def _export_txt(self) -> None:
        if not self._tab_descriptors:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Plot loops before exporting TXT data.",
            )
            return

        entries: List[tuple[str, str, QtWidgets.QWidget, TabDescriptor]] = []
        for tab, descriptor in self._tab_descriptors.items():
            if not descriptor.lines:
                continue
            label = descriptor.root_label or descriptor.title
            entries.append((label, descriptor.title, tab, descriptor))

        if not entries:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No plotted data is available to export.",
            )
            return

        selection_dialog = GraphSelectionDialog(
            self,
            entries=[(label, detail, tab) for label, detail, tab, _ in entries],
            title="Select Data to Export",
            prompt="Choose which plotted data to export as TXT files.",
            current=self.tab_widget.currentWidget(),
        )
        if selection_dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        selected_tabs = selection_dialog.selected_tabs()
        selected_descriptors = [descriptor for label, detail, tab, descriptor in entries if tab in selected_tabs]
        if not selected_descriptors:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No plotted data was selected for export.",
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

        any_visible = any(
            any(state.line.get_visible() for state in descriptor.lines.values())
            for descriptor in selected_descriptors
        )

        options_dialog = ExportOptionsDialog(
            self,
            Path(directory),
            suggestion=_suggest_export_subfolder(self.measurements),
            allow_plot_axes=any_visible,
        )
        if options_dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        target_dir = options_dialog.selected_directory()
        target_dir.mkdir(parents=True, exist_ok=True)
        scope = options_dialog.selected_scope()
        include_hidden = scope == "all"

        exported_series = self._write_plotted_series(
            target_dir,
            selected_descriptors,
            include_hidden=include_hidden,
        )
        if exported_series:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                f"Exported {exported_series} data series to {target_dir}",
            )
            self._append_log(f"Exported {exported_series} data series to {target_dir}")
            self.last_export_path = target_dir
            self.settings.setValue("last_export_path", str(target_dir))
            self.settings.sync()
        else:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No plotted data matched the current visibility filters.",
            )


    def _open_origin_prompt(self) -> None:
        if not self._last_prepared_groups or not self._last_axes:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Plot loops before sending data to Origin.",
            )
            return

        entries: List[tuple[str, str, QtWidgets.QWidget, TabDescriptor]] = []
        for tab, descriptor in self._tab_descriptors.items():
            if descriptor.kind != "temperature" or not descriptor.lines:
                continue
            label = descriptor.root_label or descriptor.title
            entries.append((label, descriptor.title, tab, descriptor))

        if not entries:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No temperature plots are available to open in Origin.",
            )
            return

        dialog = GraphSelectionDialog(
            self,
            entries=[(label, detail, tab) for label, detail, tab, _ in entries],
            title="Open in Origin",
            prompt="Select which temperature plots to open in Origin.",
            current=self.tab_widget.currentWidget(),
        )
        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        selected_tabs = dialog.selected_tabs()
        selected_group_keys: List[PlotGroupKey] = []
        for label, detail, tab, descriptor in entries:
            if tab not in selected_tabs:
                continue
            key_meta = descriptor.metadata.get("plot_group_key")
            group_key: PlotGroupKey | None = None
            if isinstance(key_meta, (tuple, list)) and len(key_meta) == 2:
                sample = key_meta[0]
                temperature = key_meta[1]
                if isinstance(sample, str) and isinstance(temperature, (int, float)):
                    group_key = (sample.strip() or "Unknown sample", float(temperature))
            if group_key is None:
                temperature = descriptor.metadata.get("temperature")
                sample_name = descriptor.metadata.get("sample_name")
                if isinstance(sample_name, str) and isinstance(temperature, (int, float)):
                    group_key = (sample_name.strip() or "Unknown sample", float(temperature))
            if group_key is None:
                continue
            if group_key not in selected_group_keys:
                selected_group_keys.append(group_key)

        if not selected_group_keys:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "No plots were selected for Origin.",
            )
            return

        x_axis, y_axis = self._last_axes
        selected_prepared = {
            key: self._last_prepared_groups.get(key)
            for key in selected_group_keys
            if key in self._last_prepared_groups
        }
        selected_prepared = {k: v for k, v in selected_prepared.items() if v}
        if not selected_prepared:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Hysteresis Loops",
                "Selected plots are no longer available. Plot loops again and retry.",
            )
            return

        rescale_info = {
            key: self._last_rescale_info.get(key, {})
            for key in selected_prepared.keys()
        }

        self._export_origin(selected_prepared, rescale_info, x_axis, y_axis, self._last_rescale_enabled)

    def _render_matplotlib(
        self,
        prepared_groups: Dict[PlotGroupKey, List[tuple[VSMMeasurement, pd.DataFrame]]],
        rescale_info: Dict[PlotGroupKey, Dict[Path, RescaleResult]],
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
        for group_key in list(self._line_visibility.keys()):
            if group_key not in prepared_groups and group_key[0] != _LEGACY_ANY_SAMPLE:
                del self._line_visibility[group_key]
        line_kwargs = self._line_style_kwargs()
        for group_key, entries in sorted(
            prepared_groups.items(),
            key=lambda item: (item[0][0].lower(), float(item[0][1])),
        ):
            sample_key, temperature = group_key
            fig = Figure(figsize=(11.5, 7.8))
            ax = fig.add_subplot(111)
            visibility = self._line_visibility_for_group(group_key)
            lines: Dict[float, Any] = {}
            descriptor_lines: Dict[tuple[str, float | str], GraphLineState] = {}
            valid_angles: set[float] = set()
            sample_label = self._format_sample_label(sample_key) or sample_key
            for measurement, subset in entries:
                if measurement.angle is None:
                    continue
                angle = float(measurement.angle)
                series_y = _numeric_column(subset, y_axis)
                if rescale_enabled:
                    result = rescale_info.get(group_key, {}).get(measurement.path)
                    if result is not None:
                        if result.replacement is not None:
                            replacement = result.replacement.reindex(subset.index)
                            series_y = replacement
                        else:
                            series_y = series_y * result.scale + result.offset
                numeric_x = _numeric_column(subset, x_axis).to_numpy()
                numeric_y = pd.to_numeric(series_y, errors="coerce").to_numpy()
                label = self._series_label(
                    measurement,
                    y_axis,
                    f"{measurement.angle:g}°",
                )
                line, = ax.plot(
                    numeric_x,
                    numeric_y,
                    label=label,
                    **line_kwargs,
                )
                visible = visibility.get(angle, True)
                line.set_visible(visible)
                lines[angle] = line
                descriptor_lines[("angle", angle)] = GraphLineState(
                    key=("angle", angle),
                    label=label,
                    line=line,
                    base_x=numeric_x,
                    base_y=numeric_y,
                    full_x=numeric_x,
                    full_y=numeric_y,
                )
                valid_angles.add(angle)

            for angle in list(visibility.keys()):
                if angle not in valid_angles:
                    del visibility[angle]

            ax.set_xlabel(x_axis)
            ax.set_ylabel(y_axis)
            temperature_label = f"{temperature:g} °C"
            title = f"{sample_label} — {temperature_label}"
            ax.set_title(title)
            self._apply_plot_theme(ax)

            legend = None
            if lines:
                legend = ax.legend(loc="best")
                self._style_legend(legend)

            try:
                fig.tight_layout()
            except Exception:
                pass

            canvas = FigureCanvas(fig)
            tab_state = PlotTabState(axes=ax, canvas=canvas, lines=lines)
            self._plot_tabs[group_key] = tab_state

            tab = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(canvas)
            title = f"{sample_label} — {temperature_label}"
            descriptor = TabDescriptor(
                kind="temperature",
                title=ax.get_title(),
                root_label=title,
                x_label=x_axis,
                y_label=y_axis,
                canvas=canvas,
                axes=ax,
                lines=descriptor_lines,
                metadata={
                    "temperature": float(temperature),
                    "sample_name": sample_key,
                    "plot_group_key": group_key,
                },
            )
            self.tab_widget.addTab(tab, title)
            self._temperature_tab_widgets.append(tab)
            self._register_plot_tab(tab, canvas, ax, descriptor)
            if legend is None:
                self._refresh_tab_legend(tab_state)

        fitter = getattr(self.tab_widget, "_fit_subwindow", None)
        subwindow_for = getattr(self.tab_widget, "_subwindow_for", None)
        if callable(fitter) and callable(subwindow_for):
            for tab in list(self._temperature_tab_widgets):
                try:
                    subwindow = subwindow_for(tab)
                except Exception:
                    subwindow = None
                if subwindow is None:
                    continue
                try:
                    fitter(subwindow, use_half_width=False, preferred_width=None)
                except Exception:
                    pass
                try:
                    QtCore.QTimer.singleShot(
                        0,
                        lambda s=subwindow, f=fitter: f(
                            s, use_half_width=False, preferred_width=None
                        ),
                    )
                except Exception:
                    pass

        self._append_log("Finished generating Matplotlib hysteresis plots.")
        try:
            self._ensure_window_visibility()
        except Exception:
            pass

    def _export_origin(
        self,
        prepared_groups: Dict[PlotGroupKey, List[tuple[VSMMeasurement, pd.DataFrame]]],
        rescale_info: Dict[PlotGroupKey, Dict[Path, RescaleResult]],
        x_axis: str,
        y_axis: str,
        rescale_enabled: bool,
    ) -> None:
        total_groups = max(1, len(prepared_groups))
        self._begin_task_progress(
            "Exporting plots to Origin…",
            maximum=total_groups,
            value=0,
        )
        try:
            try:
                with origin_session(keep_open=True) as op:
                    exported = 0
                    ordered_groups = sorted(
                        prepared_groups.items(),
                        key=lambda item: (item[0][0].lower(), float(item[0][1])),
                    )
                    for index, (group_key, entries) in enumerate(ordered_groups, start=1):
                        sample_key, temperature = group_key
                        self._update_task_progress(
                            value=index - 1,
                            title=f"Exporting {sample_key} @ {temperature:g} °C ({index}/{len(ordered_groups)})",
                        )
                        valid = []
                        for measurement, subset in entries:
                            series_y = _numeric_column(subset, y_axis)
                            if rescale_enabled:
                                result = rescale_info.get(group_key, {}).get(measurement.path)
                                if result is not None:
                                    series_y = series_y * result.scale + result.offset
                            export_subset = pd.DataFrame(
                                {
                                    x_axis: _numeric_column(subset, x_axis),
                                    y_axis: pd.to_numeric(series_y, errors="coerce"),
                                }
                            ).dropna().astype(float)
                            if export_subset.empty:
                                continue
                            valid.append((measurement, export_subset))
                        if not valid:
                            continue
                        try:
                            self._build_origin_group(
                                op,
                                temperature,
                                valid,
                                x_axis,
                                y_axis,
                                sample_label=sample_key,
                            )
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
        finally:
            self._update_task_progress(
                value=total_groups,
                title="Origin export complete.",
            )
            self._end_task_progress()

    def _origin_book_name(self, temperature: float, sample_label: str | None = None) -> str:
        if sample_label:
            label = f"{sample_label} {temperature:g}C"
        else:
            label = f"VSM_{temperature:g}C"
        return "".join(ch if ch.isalnum() else "_" for ch in label)[:30]

    def _origin_graph_short_name(self, temperature: float, sample_label: str | None = None) -> str:
        if sample_label:
            label = f"{sample_label} {temperature:g}C"
        else:
            label = f"T{temperature:g}C"
        return "".join(ch if ch.isalnum() else "_" for ch in label)[:13]

    def _build_origin_group(
        self,
        origin_any: Any,
        temperature: float,
        entries: Sequence[Tuple[VSMMeasurement, pd.DataFrame]],
        x_axis: str,
        y_axis: str,
        sample_label: str | None = None,
    ) -> None:
        if not sample_label:
            for measurement, _subset in entries:
                candidate = measurement.sample_name
                if candidate:
                    sample_label = str(candidate)
                    break
        book = origin_any.new_book(
            'w', lname=self._origin_book_name(temperature, sample_label)
        )
        book.activate()

        graph = origin_any.new_graph(template='line')
        layer = graph[0] if graph else None
        if layer is None:
            return

        try:
            graph_title = (
                f"{sample_label} — {temperature:g} °C"
                if sample_label
                else f"{temperature:g} °C"
            )
            graph.lname = graph_title
        except Exception:
            pass
        try:
            graph.name = self._origin_graph_short_name(temperature, sample_label)
        except Exception:
            pass

        sheet = book[0] if len(book) else book.add_sheet()
        sheet.name = "Data"
        column_names: List[str] = []
        axis_roles: List[str] = []
        data: Dict[str, pd.Series] = {}
        plot_pairs: List[tuple[str, str, str, int]] = []
        column_labels: Dict[str, str] = {}
        column_units: Dict[str, str] = {}
        column_comments: Dict[str, str] = {}
        plot_idx = 1

        def _split_label_unit(label: str) -> tuple[str, str]:
            text = str(label)
            match = re.search(r"\[(.+?)\]\s*$", text)
            if match:
                return text[: match.start()].strip(), match.group(1).strip()
            match = re.search(r"\((.+?)\)\s*$", text)
            if match:
                return text[: match.start()].strip(), match.group(1).strip()
            return text.strip(), ""

        x_label, x_unit = _split_label_unit(x_axis)
        y_label, y_unit = _split_label_unit(y_axis)
        if not x_unit and "Oe" in x_axis:
            x_unit = "Oe"
        if not y_unit and "emu" in y_axis.lower():
            y_unit = "emu"

        def _unique_column_name(base: str) -> str:
            candidate = base
            suffix = 2
            while candidate in data:
                candidate = f"{base} #{suffix}"
                suffix += 1
            return candidate

        for measurement, subset in entries:
            angle_label = (
                f"{measurement.angle:g}°"
                if measurement.angle is not None
                else measurement.path.stem
            )
            x_name = _unique_column_name(f"{x_label or x_axis} ({angle_label})")
            y_name = _unique_column_name(f"{y_label or y_axis} ({angle_label})")
            data[x_name] = _numeric_column(subset, x_axis).reset_index(drop=True)
            data[y_name] = _numeric_column(subset, y_axis).reset_index(drop=True)
            column_names.extend([x_name, y_name])
            axis_roles.extend(["X", "Y"])
            column_labels[x_name] = x_name
            column_units[x_name] = x_unit
            column_labels[y_name] = y_name
            column_units[y_name] = y_unit
            column_comments[y_name] = f"Angle {angle_label}"
            plot_pairs.append((x_name, y_name, angle_label, plot_idx - 1))
            plot_idx += 1

        frame = pd.DataFrame(data)
        sheet.from_df(frame)
        try:
            sheet.header_rows("LUC")
        except Exception:
            pass
        try:
            sheet.cols_axis("".join(axis_roles))
        except Exception:
            pass

        col_index = {name: idx for idx, name in enumerate(column_names)}
        for name, index in col_index.items():
            label_text = column_labels.get(name, name)
            try:
                sheet.set_label(index, label_text, "L")
            except TypeError:
                try:
                    sheet.set_label(index, label_text)
                except Exception:
                    pass
            except Exception:
                pass
            unit = column_units.get(name)
            if unit:
                try:
                    sheet.set_label(index, unit, "U")
                except Exception:
                    pass
            comment = column_comments.get(name)
            if comment:
                try:
                    sheet.set_label(index, comment, "C")
                except TypeError:
                    try:
                        setter = getattr(sheet, "set_comment", None)
                        if callable(setter):
                            setter(index, comment)
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    safe_comment = self._escape_origin_text(comment)
                    origin_any.lt_exec(f'wks.col{index + 1}.comment$="{safe_comment}";')
                except Exception:
                    pass

        style = self._line_style_kwargs()
        show_markers = "marker" in style
        marker_size = float(style.get("markersize", 4)) if show_markers else 0.0
        for x_name, y_name, angle_label, color_index in plot_pairs:
            x_idx = col_index.get(x_name)
            y_idx = col_index.get(y_name)
            if x_idx is None or y_idx is None:
                continue
            plot_obj = layer.add_plot(sheet, coly=y_idx, colx=x_idx, type='y')
            if plot_obj is not None:
                color = ORIGIN_PLOT_COLORS[color_index % len(ORIGIN_PLOT_COLORS)]
                try:
                    plot_obj.color = color
                    plot_obj.line_width = 1.5
                    if show_markers:
                        plot_obj.symbol_shape = 2
                        plot_obj.symbol_size = marker_size
                        plot_obj.symbol_edge_color = color
                        plot_obj.symbol_fill_color = color
                    else:
                        plot_obj.symbol_shape = 0
                        plot_obj.symbol_size = 0
                    plot_obj.legend = f"{angle_label}"
                except Exception:
                    pass

        try:
            graph.activate()
        except Exception:
            pass

        if sample_label:
            title_text = f"{sample_label} — {temperature:g} °C"
        else:
            title_text = f"{temperature:g} °C"
        try:
            set_origin_axis_title(layer, "x", x_axis)
            set_origin_axis_title(layer, "y", y_axis)
        except Exception:
            pass
        try:
            set_origin_graph_title(origin_any, graph, layer, title_text)
            graph.name = self._origin_graph_short_name(temperature, sample_label)
        except Exception:
            pass
        try:
            origin_any.lt_exec("legend;")
        except Exception:
            pass
        try:
            legend_label = layer.label("Legend")
        except Exception:
            legend_label = None
        if legend_label is not None:
            set_float = getattr(legend_label, "set_float", None)
            set_int = getattr(legend_label, "set_int", None)
            get_float = getattr(layer, "get_float", None)
            legend_entries_count = len(plot_pairs)
            if callable(set_float):
                try:
                    set_float("fsize", 7.5 if legend_entries_count > 12 else 9.0)
                except Exception:
                    pass
            if callable(set_int):
                for key, value in (("show", 1), ("horzalign", 2), ("vertalign", 2)):
                    try:
                        set_int(key, int(value))
                    except Exception:
                        continue
            if callable(set_float) and callable(get_float):
                try:
                    x_from = float(get_float("x.from"))
                    x_to = float(get_float("x.to"))
                    y_from = float(get_float("y.from"))
                    y_to = float(get_float("y.to"))
                except Exception:
                    x_from = x_to = y_from = y_to = math.nan
                if all(math.isfinite(value) for value in (x_from, x_to, y_from, y_to)):
                    x_span = x_to - x_from
                    y_span = y_to - y_from
                    if x_span > 0 and y_span > 0:
                        if legend_entries_count > 12:
                            target_x = x_from + (x_span * 0.70)
                            target_y = y_from + (y_span * 0.62)
                        else:
                            target_x = x_from + (x_span * 0.72)
                            target_y = y_to - (y_span * 0.02)
                        try:
                            # Use a conservative in-frame anchor so long legends
                            # remain visible even if horizontal alignment is ignored.
                            set_float("x", target_x)
                        except Exception:
                            pass
                        try:
                            set_float("y", target_y)
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
        try:
            return super().eventFilter(obj, event)
        except TypeError:
            # Method may be rebound onto PyPlotWorkbench (not a VSMPlotter subclass).
            return bool(PyPlotWindow.eventFilter(self, obj, event))

    def _handle_log_visibility(self, visible: bool) -> None:
        if visible:
            self._clear_log_alert()

    def _clear_log_alert(self) -> None:
        if getattr(self, "message_log_dock", None) is None:
            return
        if getattr(self, "_log_has_unread_errors", False):
            self._log_has_unread_errors = False
        dock = getattr(self, "message_log_dock", None)
        if hasattr(dock, "set_alert"):
            dock.set_alert(False)

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
                if hasattr(dock, "set_alert"):
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
