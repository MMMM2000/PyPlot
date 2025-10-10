"""Visualise VSM hysteresis loops grouped by temperature and angle."""

from __future__ import annotations

import logging
import math
import re
import sys
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

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

    df = pd.DataFrame(data_rows, dtype=float)

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

    def __init__(self, parent: QtWidgets.QWidget | None, base_directory: Path, *, suggestion: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("TXT Export Options")
        self.base_directory = Path(base_directory)
        self._selected_directory = self.base_directory
        self._suggestion = _clean_folder_name(suggestion) or "VSM_Export"

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

class VSMPlotter(QtWidgets.QWidget):
    """Render hysteresis loops for VSM-HYS-DATA files."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VSM Plot Explorer")
        self.resize(1480, 940)

        self.logger = logging.getLogger("vsm_plotter")
        self.logger.setLevel(logging.INFO)
        self.settings = QtCore.QSettings("MicrowireLab", "VSMPlotter")
        self.last_export_path: Path | None = None

        self.measurements: List[VSMMeasurement] = []
        self._last_prepared_groups: Dict[float, List[tuple[VSMMeasurement, pd.DataFrame]]] = {}
        self._last_rescale_info: Dict[float, Dict[Path, RescaleResult]] = {}
        self._last_axes: tuple[str, str] | None = None
        self._last_rescale_enabled = False
        self._line_visibility: Dict[float, Dict[float, bool]] = {}
        self._plot_tabs: Dict[float, PlotTabState] = {}
        self._angle_checkboxes: Dict[float, Dict[float, QtWidgets.QCheckBox]] = {}
        self._angle_group_widgets: List[QtWidgets.QWidget] = []

        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        controls = QtWidgets.QFrame()
        controls.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        controls.setMinimumWidth(340)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setSpacing(10)

        mode_group = QtWidgets.QButtonGroup(self)
        self.file_radio = QtWidgets.QRadioButton("Select files")
        self.folder_radio = QtWidgets.QRadioButton("Select folder")
        mode_group.addButton(self.file_radio)
        mode_group.addButton(self.folder_radio)
        self.file_radio.setChecked(True)

        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.addWidget(self.file_radio)
        mode_layout.addWidget(self.folder_radio)
        controls_layout.addLayout(mode_layout)

        self.path_edit = QtWidgets.QLineEdit()
        browse_button = QtWidgets.QPushButton("Browse…")
        browse_button.clicked.connect(self._choose_input)
        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse_button)
        controls_layout.addLayout(path_row)

        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        controls_layout.addWidget(QtWidgets.QLabel("Output backend"))
        controls_layout.addWidget(self.backend_combo)

        self.temperature_combo = QtWidgets.QComboBox()
        self.temperature_combo.addItem("All temperatures", None)
        controls_layout.addWidget(QtWidgets.QLabel("Temperature filter"))
        controls_layout.addWidget(self.temperature_combo)

        self.x_axis_combo = QtWidgets.QComboBox()
        self.y_axis_combo = QtWidgets.QComboBox()
        controls_layout.addWidget(QtWidgets.QLabel("X axis"))
        controls_layout.addWidget(self.x_axis_combo)
        controls_layout.addWidget(QtWidgets.QLabel("Y axis"))
        controls_layout.addWidget(self.y_axis_combo)

        controls_layout.addWidget(QtWidgets.QLabel("Matplotlib style"))
        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.addItem("Line", "line")
        self.style_combo.addItem("Line + symbols", "line_markers")
        controls_layout.addWidget(self.style_combo)

        self.rescale_checkbox = QtWidgets.QCheckBox("Normalise Y axis endpoints")
        self.rescale_checkbox.setToolTip(
            "Scale each curve so the negative-field and positive-field endpoints share\n"
            "a common minimum/maximum across all angles for the same temperature."
        )
        controls_layout.addWidget(self.rescale_checkbox)

        self.dark_mode_checkbox = QtWidgets.QCheckBox("Dark plot theme")
        self.dark_mode_checkbox.setToolTip("Render Matplotlib plots using a dark background theme.")
        self.dark_mode_checkbox.toggled.connect(self._restyle_plots)
        controls_layout.addWidget(self.dark_mode_checkbox)

        angle_label = QtWidgets.QLabel("Show angles")
        controls_layout.addWidget(angle_label)
        self.angle_scroll = QtWidgets.QScrollArea()
        self.angle_scroll.setWidgetResizable(True)
        self.angle_scroll.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.angle_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.angle_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.angle_scroll.setMaximumHeight(220)
        self.angle_container = QtWidgets.QWidget()
        self.angle_layout = QtWidgets.QVBoxLayout(self.angle_container)
        self.angle_layout.setContentsMargins(8, 4, 8, 4)
        self.angle_layout.setSpacing(4)
        self.angle_placeholder = QtWidgets.QLabel("Load data to configure visibility.")
        self.angle_placeholder.setWordWrap(True)
        self.angle_placeholder.setEnabled(False)
        self.angle_layout.addWidget(self.angle_placeholder)
        self.angle_layout.addStretch(1)
        self.angle_scroll.setWidget(self.angle_container)
        self.angle_scroll.setEnabled(False)
        controls_layout.addWidget(self.angle_scroll)

        controls_layout.addWidget(QtWidgets.QLabel("TXT export mode"))
        self.export_mode_combo = QtWidgets.QComboBox()
        self.export_mode_combo.addItem("Original data", "original")
        self.export_mode_combo.addItem("Rescaled data", "rescaled")
        controls_layout.addWidget(self.export_mode_combo)

        button_row = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("Load data")
        self.load_button.clicked.connect(self._load_measurements)
        button_row.addWidget(self.load_button)
        self.plot_button = QtWidgets.QPushButton("Generate plots")
        self.plot_button.clicked.connect(self._generate_plots)
        self.plot_button.setEnabled(False)
        button_row.addWidget(self.plot_button)
        self.popout_button = QtWidgets.QPushButton("Open in Matplotlib")
        self.popout_button.clicked.connect(self._open_matplotlib_window)
        self.popout_button.setEnabled(False)
        button_row.addWidget(self.popout_button)
        self.export_txt_button = QtWidgets.QPushButton("Export TXT")
        self.export_txt_button.clicked.connect(self._export_txt)
        self.export_txt_button.setEnabled(False)
        button_row.addWidget(self.export_txt_button)
        controls_layout.addLayout(button_row)

        controls_layout.addStretch(1)

        layout.addWidget(controls, 0)

        self.output_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(self.output_splitter, 1)

        self.tab_widget = QtWidgets.QTabWidget()
        self.output_splitter.addWidget(self.tab_widget)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Load VSM measurements to display hysteresis loops…")
        self.output_splitter.addWidget(self.log_view)
        self.output_splitter.setStretchFactor(0, 4)
        self.output_splitter.setStretchFactor(1, 1)
        self.output_splitter.setChildrenCollapsible(False)

        install_standard_menu(
            self,
            help_topic="vsm_plotter",
            console=self.log_view,
            open_file=self._open_files_from_menu,
            open_folder=self._open_folder_from_menu,
        )

    def _load_settings(self) -> None:
        value = self.settings.value("last_path", "")
        if isinstance(value, str):
            self.path_edit.setText(value)
        export_path = self.settings.value("last_export_path", "")
        if isinstance(export_path, str) and export_path:
            try:
                self.last_export_path = Path(export_path)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                self.last_export_path = None
        backend = self.settings.value("backend", "Matplotlib")
        if isinstance(backend, str):
            index = self.backend_combo.findText(backend, QtCore.Qt.MatchFlag.MatchFixedString)
            if index >= 0:
                self.backend_combo.setCurrentIndex(index)
        export_mode = self.settings.value("export_mode", "original")
        if isinstance(export_mode, str):
            index = self.export_mode_combo.findData(export_mode)
            if index >= 0:
                self.export_mode_combo.setCurrentIndex(index)
        style = self.settings.value("plot_style", "line")
        if isinstance(style, str):
            index = self.style_combo.findData(style)
            if index >= 0:
                self.style_combo.setCurrentIndex(index)
        rescale_value = self.settings.value("rescale_y", False)
        if rescale_value is not None:
            self.rescale_checkbox.setChecked(_coerce_bool(rescale_value))
        dark_value = self.settings.value("plot_dark_mode", False)
        if dark_value is not None:
            self.dark_mode_checkbox.setChecked(_coerce_bool(dark_value))
        mode = self.settings.value("mode", "files")
        if mode == "folder":
            self.folder_radio.setChecked(True)
        else:
            self.file_radio.setChecked(True)
        geometry = self.settings.value("geometry")
        if isinstance(geometry, QtCore.QByteArray):
            self.restoreGeometry(geometry)
        splitter_state = self.settings.value("splitter_state")
        if isinstance(splitter_state, QtCore.QByteArray):
            self.output_splitter.restoreState(splitter_state)

    def _save_settings(self) -> None:
        self.settings.setValue("last_path", self.path_edit.text())
        self.settings.setValue("backend", self.backend_combo.currentText())
        self.settings.setValue("mode", "folder" if self.folder_radio.isChecked() else "files")
        self.settings.setValue("export_mode", self.export_mode_combo.currentData())
        self.settings.setValue("plot_style", self.style_combo.currentData())
        self.settings.setValue("rescale_y", self.rescale_checkbox.isChecked())
        self.settings.setValue("plot_dark_mode", self.dark_mode_checkbox.isChecked())
        if self.last_export_path:
            self.settings.setValue("last_export_path", str(self.last_export_path))
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter_state", self.output_splitter.saveState())
        self.settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._save_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------------ file selection
    def _choose_input(self) -> None:
        if self.folder_radio.isChecked():
            directory = QtWidgets.QFileDialog.getExistingDirectory(
                self,
                "Select folder with VSM files",
                self.path_edit.text() or str(Path.home()),
            )
            if directory:
                self.path_edit.setText(directory)
        else:
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self,
                "Select VSM files",
                self.path_edit.text() or str(Path.home()),
                "VSM data (*.VSM-Hys-Data);;All files (*)",
            )
            if files:
                self.path_edit.setText(";".join(files))
        self._save_settings()

    def _open_files_from_menu(self) -> None:
        self.file_radio.setChecked(True)
        self._choose_input()

    def _open_folder_from_menu(self) -> None:
        self.folder_radio.setChecked(True)
        self._choose_input()

    def _selected_paths(self) -> List[Path]:
        text = self.path_edit.text().strip()
        if not text:
            return []
        if self.folder_radio.isChecked():
            directory = Path(text)
            return _find_vsm_files(directory)
        return [Path(part) for part in text.split(";") if part]

    # ------------------------------------------------------------------ data loading
    def _load_measurements(self) -> None:
        self.measurements.clear()
        self.tab_widget.clear()
        self.log_view.clear()
        self._last_prepared_groups = {}
        self._last_rescale_info = {}
        self._last_axes = None
        self._last_rescale_enabled = False
        self._line_visibility = {}
        self._plot_tabs = {}
        self._angle_checkboxes = {}
        self._reset_angle_controls()
        self.temperature_combo.blockSignals(True)
        self.temperature_combo.clear()
        self.temperature_combo.addItem("All temperatures", None)
        self.temperature_combo.blockSignals(False)
        self.plot_button.setEnabled(False)
        self.export_txt_button.setEnabled(False)
        self.popout_button.setEnabled(False)

        paths = self._selected_paths()
        if not paths:
            QtWidgets.QMessageBox.warning(self, "VSM Plot Explorer", "Select at least one VSM file to load.")
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
                "VSM Plot Explorer",
                "No VSM measurements could be loaded.",
            )
            return

        self.measurements.sort(key=lambda m: (
            float('inf') if m.temperature is None else m.temperature,
            float('inf') if m.angle is None else m.angle,
        ))

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
        self.export_txt_button.setEnabled(True)
        self._save_settings()

    def _populate_axis_combos(self, labels: List[str]) -> None:
        numeric_labels = [label for label in labels if label]
        preferred_x = [
            "Applied Field",
            "Applied Field [Oe]",
            "Applied Field For Plot",
        ]
        preferred_y = [
            "Signal parallel with sample",
            "Signal Magnitude",
            "Moment [emu]",
        ]
        def _choose(preferences: Iterable[str], combo: QtWidgets.QComboBox) -> None:
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
        _choose(preferred_x, self.x_axis_combo)
        _choose(preferred_y, self.y_axis_combo)

    # ------------------------------------------------------------------ plotting helpers
    def _generate_plots(self) -> None:
        if not self.measurements:
            QtWidgets.QMessageBox.warning(self, "VSM Plot Explorer", "Load VSM measurements first.")
            return
        x_axis = self.x_axis_combo.currentText()
        y_axis = self.y_axis_combo.currentText()
        if not x_axis or not y_axis:
            QtWidgets.QMessageBox.warning(self, "VSM Plot Explorer", "Select X and Y axes for plotting.")
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
                "VSM Plot Explorer",
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
            self._update_angle_controls({})
            self._append_log(
                "No numeric data matched the selected axes; nothing to plot."
            )
            self.popout_button.setEnabled(False)
            return

        self._update_angle_controls(prepared_groups)

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

        self._last_prepared_groups = prepared_groups
        self._last_rescale_info = rescale_info
        self._last_axes = (x_axis, y_axis)
        self._last_rescale_enabled = rescale_enabled
        self.popout_button.setEnabled(True)

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

    def _clear_angle_group_widgets(self) -> None:
        if not hasattr(self, "angle_layout"):
            return
        for widget in self._angle_group_widgets:
            try:
                self.angle_layout.removeWidget(widget)
            except Exception:
                pass
            widget.setParent(None)
            widget.deleteLater()
        self._angle_group_widgets.clear()

    def _reset_angle_controls(self) -> None:
        if not hasattr(self, "angle_layout"):
            return
        self._clear_angle_group_widgets()
        if hasattr(self, "angle_placeholder"):
            self.angle_placeholder.setVisible(True)
        if hasattr(self, "angle_scroll"):
            self.angle_scroll.setEnabled(False)

    def _update_angle_controls(
        self,
        prepared_groups: Dict[float, List[tuple[VSMMeasurement, pd.DataFrame]]],
    ) -> None:
        if not hasattr(self, "angle_layout"):
            return
        self._clear_angle_group_widgets()
        self._angle_checkboxes = {}
        if not prepared_groups:
            if hasattr(self, "angle_placeholder"):
                self.angle_placeholder.setVisible(True)
            if hasattr(self, "angle_scroll"):
                self.angle_scroll.setEnabled(False)
            return

        if hasattr(self, "angle_placeholder"):
            self.angle_placeholder.setVisible(False)
        self.angle_scroll.setEnabled(True)

        for temperature, entries in sorted(prepared_groups.items()):
            group_box = QtWidgets.QGroupBox(f"{temperature:g} °C")
            group_box.setFlat(True)
            group_layout = QtWidgets.QVBoxLayout(group_box)
            group_layout.setContentsMargins(6, 4, 6, 4)
            group_layout.setSpacing(2)

            visibility = self._line_visibility.setdefault(temperature, {})
            checkboxes: Dict[float, QtWidgets.QCheckBox] = {}
            seen_angles: set[float] = set()

            for measurement, _ in entries:
                if measurement.angle is None:
                    continue
                angle = float(measurement.angle)
                if angle in seen_angles:
                    continue
                seen_angles.add(angle)
                checkbox = QtWidgets.QCheckBox(f"{angle:g}°")
                checkbox.setChecked(visibility.get(angle, True))
                checkbox.toggled.connect(
                    partial(self._on_angle_checkbox_toggled, temperature, angle)
                )
                group_layout.addWidget(checkbox)
                checkboxes[angle] = checkbox

            for missing in [key for key in visibility.keys() if key not in seen_angles]:
                del visibility[missing]

            if not checkboxes:
                placeholder = QtWidgets.QLabel("No angles detected for this temperature.")
                placeholder.setEnabled(False)
                group_layout.addWidget(placeholder)

            group_layout.addStretch(1)
            self.angle_layout.insertWidget(self.angle_layout.count() - 1, group_box)
            self._angle_group_widgets.append(group_box)
            self._angle_checkboxes[temperature] = checkboxes

        for stale_temp in [key for key in list(self._line_visibility.keys()) if key not in prepared_groups]:
            del self._line_visibility[stale_temp]

    def _on_angle_checkbox_toggled(self, temperature: float, angle: float, checked: bool) -> None:
        self._toggle_line_visibility(temperature, angle, checked)

    def _toggle_line_visibility(self, temperature: float, angle: float, visible: bool) -> None:
        visibility = self._line_visibility.setdefault(temperature, {})
        visibility[angle] = visible
        checkbox = self._angle_checkboxes.get(temperature, {}).get(angle)
        if checkbox is not None and checkbox.isChecked() != visible:
            checkbox.blockSignals(True)
            checkbox.setChecked(visible)
            checkbox.blockSignals(False)
        tab_state = self._plot_tabs.get(temperature)
        if tab_state is None:
            return
        line = tab_state.lines.get(angle)
        if line is None:
            return
        line.set_visible(visible)
        self._refresh_tab_legend(tab_state)

    def _open_matplotlib_window(self) -> None:
        if not self._last_prepared_groups or not self._last_axes:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Plot Explorer",
                "Generate plots before opening a Matplotlib window.",
            )
            return

        try:
            import matplotlib.pyplot as plt
        except Exception as exc:  # pragma: no cover - GUI/runtime dependent
            QtWidgets.QMessageBox.warning(
                self,
                "VSM Plot Explorer",
                f"Matplotlib's interactive backend is unavailable: {exc}",
            )
            return

        x_axis, y_axis = self._last_axes
        rescale_enabled = self._last_rescale_enabled
        rescale_info = self._last_rescale_info if rescale_enabled else {}
        visibility = self._line_visibility
        line_kwargs = self._line_style_kwargs()

        created = False
        for temperature, entries in sorted(self._last_prepared_groups.items()):
            fig, ax = plt.subplots()
            plotted = False
            for measurement, subset in entries:
                angle_visibility = visibility.get(temperature, {}).get(measurement.angle, True)
                if not angle_visibility:
                    continue
                series_y = subset[y_axis]
                if rescale_enabled:
                    result = rescale_info.get(temperature, {}).get(measurement.path)
                    if result is not None:
                        if result.replacement is not None:
                            series_y = result.replacement.reindex(subset.index)
                        else:
                            series_y = series_y * result.scale + result.offset
                ax.plot(
                    subset[x_axis].to_numpy(),
                    pd.to_numeric(series_y, errors="coerce").to_numpy(),
                    label=f"{measurement.angle:g}°",
                    **line_kwargs,
                )
                plotted = True
            ax.set_xlabel(x_axis)
            ax.set_ylabel(y_axis)
            ax.set_title(f"{y_axis} vs {x_axis} at {temperature:g} °C")
            self._apply_plot_theme(ax)
            if plotted:
                legend = ax.legend(loc="best")
                self._style_legend(legend)
            try:  # pragma: no cover - backend dependent
                fig.canvas.manager.set_window_title(f"{temperature:g} °C")
            except Exception:
                pass
            fig.tight_layout()
            created = created or plotted

            if not plotted:
                plt.close(fig)
                continue

        if created:
            plt.show()
        else:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Plot Explorer",
                "No Matplotlib plots are available to display.",
            )

    def _export_txt(self) -> None:
        if not self.measurements:
            QtWidgets.QMessageBox.warning(self, "VSM Plot Explorer", "Load VSM measurements first.")
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
        )
        if dialog.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            return

        target_dir = dialog.selected_directory()
        target_dir.mkdir(parents=True, exist_ok=True)

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
                "VSM Plot Explorer",
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
                if rescale_requested:
                    if measurement.path in rescale_lookup:
                        result = rescale_lookup[measurement.path]
                        if result.replacement is not None:
                            if y_axis in measurement.data.columns:
                                df_to_write = measurement.data.copy()
                                numeric = pd.to_numeric(df_to_write[y_axis], errors="coerce")
                                updated = numeric.copy()
                                updated.loc[result.replacement.index] = result.replacement.to_numpy()
                                df_to_write[y_axis] = updated
                            else:
                                self._append_log(
                                    f"{measurement.path.name}: Y axis '{y_axis}' not present; exported original values."
                                )
                                df_to_write = measurement.data
                        elif not result.applied:
                            self._append_log(
                                f"{measurement.path.name}: insufficient variation to rescale {y_axis}; exported original values."
                            )
                            df_to_write = measurement.data
                        else:
                            df_to_write = measurement.data.copy()
                            if y_axis in df_to_write.columns:
                                numeric = pd.to_numeric(df_to_write[y_axis], errors="coerce")
                                df_to_write[y_axis] = numeric * result.scale + result.offset
                            else:
                                self._append_log(
                                    f"{measurement.path.name}: Y axis '{y_axis}' not present; exported original values."
                                )
                                df_to_write = measurement.data
                    else:
                        self._append_log(
                            f"{measurement.path.name}: no rescale transform available; exported original values."
                        )
                        df_to_write = measurement.data
                else:
                    df_to_write = measurement.data
                df_to_write.to_csv(candidate, sep="\t", index=False)
                exported += 1
            except Exception as exc:
                self._append_log(f"Failed to export {measurement.path.name}: {exc}")

        if exported:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Plot Explorer",
                f"Exported {exported} measurement(s) to {target_dir}",
            )
            self._append_log(f"Exported {exported} measurement(s) to {target_dir}")
            self.last_export_path = target_dir
            self.settings.setValue("last_export_path", str(target_dir))
            self.settings.sync()
        else:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Plot Explorer",
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
                line, = ax.plot(
                    subset[x_axis].to_numpy(),
                    pd.to_numeric(series_y, errors="coerce").to_numpy(),
                    label=f"{measurement.angle:g}°",
                    **line_kwargs,
                )
                visible = visibility.get(angle, True)
                line.set_visible(visible)
                lines[angle] = line
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
            self.tab_widget.addTab(tab, f"{temperature:g} °C")
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

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
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

