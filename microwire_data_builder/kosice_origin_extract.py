from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd


NORMALIZED_COLUMNS = ("displacement_mm", "load_g", "strain_pct", "stress_mpa")

_SAMPLE_RE = re.compile(
    r"(?P<composition>[A-Z][a-z]?\d+(?:[A-Z][a-z]?\d+)+(?:[A-Z][a-z]?\d+)*)"
    r"[\s_\-]+(?P<draw>\d+)[_\-/](?P<piece>\d+)"
)


@dataclass(frozen=True)
class OriginWorksheetExport:
    sample_key: str | None
    workbook: str
    workbook_long_name: str
    sheet: str
    sheet_long_name: str
    source_columns: Mapping[str, str]
    units: Mapping[str, str]
    row_count: int
    output_csv_path: str
    output_txt_path: str

    def as_manifest_entry(self) -> dict[str, object]:
        return {
            "sample_key": self.sample_key,
            "workbook": self.workbook,
            "workbook_long_name": self.workbook_long_name,
            "sheet": self.sheet,
            "sheet_long_name": self.sheet_long_name,
            "source_columns": dict(self.source_columns),
            "units": dict(self.units),
            "row_count": self.row_count,
            "output_csv_path": self.output_csv_path,
            "output_txt_path": self.output_txt_path,
        }


@dataclass(frozen=True)
class NormalizedManualStressTrace:
    trace_label: str
    frame: pd.DataFrame
    source_columns: Mapping[str, str]
    units: Mapping[str, str]


def infer_sample_key(*parts: object) -> str | None:
    text = " ".join(str(part or "") for part in parts)
    match = _SAMPLE_RE.search(text.replace("\\", " ").replace("/", "_"))
    if not match:
        return None
    composition = match.group("composition")
    draw = int(match.group("draw"))
    piece = int(match.group("piece"))
    return f"{composition} {draw}/{piece}"


def normalized_manual_stress_traces(
    frame: pd.DataFrame,
    *,
    column_labels: Sequence[str] | None = None,
    unit_labels: Sequence[str] | None = None,
) -> list[NormalizedManualStressTrace]:
    """Return every manual stress/strain trace found in a worksheet.

    Košice Origin sheets often store repeated Displacement/Load pairs followed by
    repeated Strain/Stress pairs. The existing Builder parser wants one four-column
    file per graph, so this helper pairs the nth mechanical trace with the nth
    stress/strain trace.
    """

    labels = list(column_labels or [])
    units = list(unit_labels or [])
    load_pairs = _paired_columns(
        frame,
        labels,
        units,
        left_target="displacement_mm",
        right_target="load_g",
    )
    stress_pairs = _paired_columns(
        frame,
        labels,
        units,
        left_target="strain_pct",
        right_target="stress_mpa",
    )
    traces: list[NormalizedManualStressTrace] = []
    for index, (load_pair, stress_pair) in enumerate(zip(load_pairs, stress_pairs, strict=False), start=1):
        columns = {
            "displacement_mm": load_pair[0],
            "load_g": load_pair[1],
            "strain_pct": stress_pair[0],
            "stress_mpa": stress_pair[1],
        }
        normalized = pd.DataFrame(
            {
                target: pd.to_numeric(frame[source], errors="coerce")
                for target, source in columns.items()
            }
        )
        normalized = normalized.dropna(how="all", subset=list(NORMALIZED_COLUMNS))
        normalized = normalized.dropna(subset=["strain_pct", "stress_mpa"], how="all")
        if normalized.empty:
            continue
        unit_map = {
            target: _unit_for_source(frame, source, units)
            for target, source in columns.items()
            if _unit_for_source(frame, source, units)
        }
        traces.append(
            NormalizedManualStressTrace(
                trace_label=f"trace{index:02d}",
                frame=normalized.reset_index(drop=True),
                source_columns=columns,
                units=unit_map,
            )
        )
    if traces:
        return traces

    try:
        normalized, source_columns, unit_map = normalized_manual_stress_frame(
            frame,
            column_labels=labels,
            unit_labels=units,
        )
    except ValueError:
        return []
    return [
        NormalizedManualStressTrace(
            trace_label="trace01",
            frame=normalized,
            source_columns=source_columns,
            units=unit_map,
        )
    ]


def normalized_manual_stress_frame(
    frame: pd.DataFrame,
    *,
    column_labels: Sequence[str] | None = None,
    unit_labels: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, str], dict[str, str]]:
    labels = list(column_labels or [])
    units = list(unit_labels or [])
    candidates: dict[str, list[tuple[int, str]]] = {column: [] for column in NORMALIZED_COLUMNS}
    for index, column in enumerate(frame.columns):
        label_parts = [str(column)]
        if index < len(labels):
            label_parts.append(str(labels[index]))
        if index < len(units):
            label_parts.append(str(units[index]))
        text = " ".join(part.casefold() for part in label_parts if part)
        for target in _candidate_targets(text):
            candidates[target].append((index, str(column)))

    selected: dict[str, str] = {}
    used_indices: set[int] = set()
    for target in NORMALIZED_COLUMNS:
        for index, column in candidates[target]:
            if index not in used_indices:
                selected[target] = column
                used_indices.add(index)
                break
    missing = [target for target in NORMALIZED_COLUMNS if target not in selected]
    if missing:
        raise ValueError("Missing manual stress/strain columns: " + ", ".join(missing))

    normalized = pd.DataFrame()
    source_columns: dict[str, str] = {}
    unit_map: dict[str, str] = {}
    for target in NORMALIZED_COLUMNS:
        source = selected[target]
        normalized[target] = pd.to_numeric(frame[source], errors="coerce")
        source_columns[target] = source
        source_index = list(frame.columns).index(source)
        if source_index < len(units):
            unit = str(units[source_index]).strip()
            if unit:
                unit_map[target] = unit
    normalized = normalized.dropna(how="all", subset=list(NORMALIZED_COLUMNS))
    normalized = normalized.dropna(subset=["strain_pct", "stress_mpa"], how="all")
    if normalized.empty:
        raise ValueError("No usable manual stress/strain data rows found.")
    return normalized.reset_index(drop=True), source_columns, unit_map


def _paired_columns(
    frame: pd.DataFrame,
    labels: Sequence[str],
    units: Sequence[str],
    *,
    left_target: str,
    right_target: str,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    columns = list(frame.columns)
    for index in range(0, max(len(columns) - 1, 0)):
        left = columns[index]
        right = columns[index + 1]
        left_text = _column_search_text(left, index, labels, units)
        right_text = _column_search_text(right, index + 1, labels, units)
        if left_target in _candidate_targets(left_text) and right_target in _candidate_targets(right_text):
            pairs.append((str(left), str(right)))
    return pairs


def _column_search_text(
    column: object,
    index: int,
    labels: Sequence[str],
    units: Sequence[str],
) -> str:
    parts = [str(column)]
    if index < len(labels):
        parts.append(str(labels[index]))
    if index < len(units):
        parts.append(str(units[index]))
    return " ".join(part.casefold() for part in parts if part)


def _unit_for_source(frame: pd.DataFrame, source: str, units: Sequence[str]) -> str:
    try:
        index = list(frame.columns).index(source)
    except ValueError:
        return ""
    if index >= len(units):
        return ""
    return str(units[index]).strip()


def write_builder_ready_manual_stress_txt(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("Displacement [mm]\tLoad [g]\tStrain [%]\tStress [MPa]\n")
        handle.write("mm\tg\t%\tMPa\n")
        for row in frame.loc[:, NORMALIZED_COLUMNS].itertuples(index=False):
            handle.write("\t".join(_format_value(value) for value in row) + "\n")


def _candidate_targets(text: str) -> list[str]:
    targets: list[str] = []
    compact = text.replace(" ", "").replace("_", "")
    if any(token in compact for token in ("displacement", "extension", "elongation", "position")):
        targets.append("displacement_mm")
    if "load" in compact or "force" in compact:
        targets.append("load_g")
    if "strain" in compact or "eps" in compact:
        targets.append("strain_pct")
    if "stress" in compact or "sigma" in compact or "mpa" in compact:
        targets.append("stress_mpa")
    return targets


def _format_value(value: object) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return ""
    if pd.isna(parsed):
        return ""
    return f"{parsed:.12g}"


__all__ = [
    "NORMALIZED_COLUMNS",
    "NormalizedManualStressTrace",
    "OriginWorksheetExport",
    "infer_sample_key",
    "normalized_manual_stress_frame",
    "normalized_manual_stress_traces",
    "write_builder_ready_manual_stress_txt",
]
