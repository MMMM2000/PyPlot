from __future__ import annotations

from dataclasses import dataclass
import io
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

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
