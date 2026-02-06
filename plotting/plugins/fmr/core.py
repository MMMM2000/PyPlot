from __future__ import annotations

from dataclasses import dataclass
import io
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

_HEADER_RE = re.compile(r"^\s*time\s*,", re.IGNORECASE)
_SAMPLE_RE = re.compile(r"^\s*sample\s+name\s*,\s*(.+)", re.IGNORECASE)
_FREQ_RE = re.compile(r"^\s*freq\s*,", re.IGNORECASE)


@dataclass
class FmrParseResult:
    frame: pd.DataFrame
    sample: str
    units: Dict[str, str]
    metadata: Dict[str, str]


def _match_column(columns: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    normalized = {str(col).strip().lower(): str(col) for col in columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in normalized:
            return normalized[key]
    for candidate in candidates:
        key = candidate.strip().lower()
        for original in columns:
            if key in str(original).strip().lower():
                return str(original)
    return None


def parse_fmr_csv(path: Path) -> FmrParseResult:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    sample = ""
    metadata: Dict[str, str] = {}
    header_idx = None
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        sample_match = _SAMPLE_RE.match(line)
        if sample_match:
            sample = sample_match.group(1).strip().strip('"')
            metadata["sample_name"] = sample
        if _FREQ_RE.match(line):
            metadata["frequency_line"] = line.strip()
        if _HEADER_RE.match(line):
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError("No FMR header row found.")

    header_parts = [part.strip() for part in lines[header_idx].split(",")]
    columns = [part for part in header_parts if part]
    units: Dict[str, str] = {}
    units_line = lines[header_idx + 1] if header_idx + 1 < len(lines) else ""
    units_parts = [part.strip() for part in units_line.split(",")]
    if len(units_parts) >= len(columns):
        for column, unit in zip(columns, units_parts):
            if unit:
                units[column] = unit

    data_start = header_idx + 2
    data_text = "\n".join(lines[data_start:])
    frame = pd.read_csv(
        io.StringIO(data_text),
        header=None,
        names=columns,
        skip_blank_lines=True,
    )
    if not frame.empty:
        frame = frame.apply(pd.to_numeric, errors="coerce")
        frame = frame.dropna(how="all")

    if not sample:
        sample = path.stem

    return FmrParseResult(frame=frame, sample=sample, units=units, metadata=metadata)


def select_fmr_axes(columns: Iterable[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    field_column = _match_column(columns, ("Field", "Applied Field"))
    x_column = _match_column(columns, ("X", "Signal X", "X (V)", "X [V]"))
    y_column = _match_column(columns, ("Y", "Signal Y", "Y (V)", "Y [V]"))
    return field_column, x_column, y_column


def rotate_lockin_phase(
    x_values: Iterable[float],
    y_values: Iterable[float],
    angle_deg: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Rotate lock-in X/Y channels by ``angle_deg``.

    The transform follows:
      X' = X*cos(theta) + Y*sin(theta)
      Y' = -X*sin(theta) + Y*cos(theta)
    """

    theta = np.deg2rad(float(angle_deg))
    cos_theta = float(np.cos(theta))
    sin_theta = float(np.sin(theta))
    x_arr = np.asarray(list(x_values), dtype=float)
    y_arr = np.asarray(list(y_values), dtype=float)
    x_rot = x_arr * cos_theta + y_arr * sin_theta
    y_rot = -x_arr * sin_theta + y_arr * cos_theta
    return x_rot, y_rot


def _phase_score(field: np.ndarray, x_vals: np.ndarray, y_vals: np.ndarray, angle_deg: float) -> float:
    _, y_rot = rotate_lockin_phase(x_vals, y_vals, angle_deg)
    if y_rot.size < 3:
        return float(np.inf)
    centered_field = field - float(np.nanmean(field))
    try:
        slope, intercept = np.polyfit(centered_field, y_rot, 1)
        baseline = slope * centered_field + intercept
        residual = y_rot - baseline
    except Exception:
        residual = y_rot - float(np.nanmean(y_rot))
    score = float(np.nanmean(np.square(residual)))
    if not np.isfinite(score):
        return float(np.inf)
    return score


def _search_best_phase(
    field: np.ndarray,
    x_vals: np.ndarray,
    y_vals: np.ndarray,
    start: float,
    stop: float,
    step: float,
) -> float:
    best_angle = 0.0
    best_score = float(np.inf)
    if step <= 0:
        return best_angle
    count = int(round((stop - start) / step)) + 1
    for idx in range(max(count, 1)):
        angle = start + idx * step
        score = _phase_score(field, x_vals, y_vals, angle)
        if score < best_score:
            best_score = score
            best_angle = angle
    return float(best_angle)


def estimate_phase_rotation_angle(
    field_values: Iterable[float],
    x_values: Iterable[float],
    y_values: Iterable[float],
) -> float:
    """Estimate a phase angle that makes Y as flat as possible.

    The objective minimizes the RMS of Y' after subtracting a linear baseline
    versus field, which is robust to small drift.
    """

    field_arr = np.asarray(list(field_values), dtype=float)
    x_arr = np.asarray(list(x_values), dtype=float)
    y_arr = np.asarray(list(y_values), dtype=float)
    valid = np.isfinite(field_arr) & np.isfinite(x_arr) & np.isfinite(y_arr)
    if int(np.count_nonzero(valid)) < 3:
        return 0.0

    field = field_arr[valid]
    x_vals = x_arr[valid]
    y_vals = y_arr[valid]
    if np.nanstd(x_vals) == 0 and np.nanstd(y_vals) == 0:
        return 0.0

    coarse = _search_best_phase(field, x_vals, y_vals, -90.0, 90.0, 1.0)
    fine_start = max(-90.0, coarse - 2.0)
    fine_stop = min(90.0, coarse + 2.0)
    fine = _search_best_phase(field, x_vals, y_vals, fine_start, fine_stop, 0.05)
    return float(fine)
