from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


DEFAULT_SAMPLE_KEY_RE = re.compile(
    r"(?P<composition>[A-Z][A-Za-z0-9]*(?:Fe|Ga|Co|Cu|Ni)[A-Za-z0-9]*)"
    r"(?:[\s_-]+(?P<draw>\d+)[-/](?P<piece>\d+[A-Za-z]*))?"
)

MANUAL_STRESS_OUTPUT_COLUMNS = {
    "displacement_mm": "displacement",
    "load_g": "load",
    "strain_pct": "strain",
    "stress_mpa": "stress",
}

_COLUMN_TOKEN_RE = re.compile(r"[^0-9A-Za-z]+")
_SAFE_STEM_RE = re.compile(r"[^0-9A-Za-z._-]+")


@dataclass(frozen=True)
class OriginColumn:
    index: int
    short_name: str
    long_name: str
    units: str = ""
    comments: str = ""

    @property
    def display_name(self) -> str:
        return self.long_name or self.short_name or f"col{self.index + 1}"

    def to_manifest(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "short_name": self.short_name,
            "long_name": self.long_name,
            "units": self.units,
            "comments": self.comments,
            "display_name": self.display_name,
            "normalized_name": normalize_column_name(self.display_name, units=self.units),
        }


@dataclass(frozen=True)
class OriginWorksheetExtract:
    sample_key: str
    workbook: str
    workbook_long_name: str
    sheet: str
    sheet_long_name: str
    columns: tuple[OriginColumn, ...]
    row_count: int
    csv_path: Path
    source_project: Path
    source_copy: Path
    sheet_rows: int = 0
    sheet_cols: int = 0
    manual_column_map: Mapping[str, str] = field(default_factory=dict)

    @property
    def candidate_manual_stress_strain(self) -> bool:
        return {"strain_pct", "stress_mpa"}.issubset(self.manual_column_map)

    def to_manifest(self, *, output_root: Path | None = None) -> dict[str, Any]:
        csv_path = self.csv_path
        if output_root is not None:
            try:
                csv_path_value = csv_path.relative_to(output_root).as_posix()
            except ValueError:
                csv_path_value = str(csv_path)
        else:
            csv_path_value = str(csv_path)
        return {
            "sample_key": self.sample_key,
            "workbook": self.workbook,
            "workbook_long_name": self.workbook_long_name,
            "sheet": self.sheet,
            "sheet_long_name": self.sheet_long_name,
            "row_count": self.row_count,
            "sheet_rows": self.sheet_rows,
            "sheet_cols": self.sheet_cols,
            "csv_path": csv_path_value,
            "columns": [column.to_manifest() for column in self.columns],
            "manual_column_map": dict(self.manual_column_map),
            "candidate_manual_stress_strain": self.candidate_manual_stress_strain,
            "source_project": str(self.source_project),
            "source_copy": str(self.source_copy),
        }


def normalize_column_name(name: object, *, units: object = "") -> str:
    text = f"{name or ''} {units or ''}".casefold()
    text = text.replace("%", " pct ")
    tokens = [token for token in _COLUMN_TOKEN_RE.split(text) if token]
    joined = "_".join(tokens)
    if "disp" in joined or "elong" in joined or joined in {"d", "dl"}:
        if "mm" in joined:
            return "displacement_mm"
        return "displacement"
    if "load" in joined or joined in {"force", "f", "g"}:
        if "g" in joined or "gram" in joined:
            return "load_g"
        return "load"
    if "strain" in joined or "epsilon" in joined or joined in {"eps", "e"}:
        if "pct" in joined or "percent" in joined:
            return "strain_pct"
        return "strain"
    if "stress" in joined or "sigma" in joined:
        if "mpa" in joined:
            return "stress_mpa"
        return "stress"
    return joined or "column"


def infer_manual_column_map(columns: Sequence[OriginColumn]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for column in columns:
        normalized = normalize_column_name(column.display_name, units=column.units)
        target = MANUAL_STRESS_OUTPUT_COLUMNS.get(normalized)
        if target and normalized not in mapping:
            mapping[normalized] = column.display_name
    return mapping


def infer_sample_key(*parts: object, default: str = "") -> str:
    for part in parts:
        text = str(part or "")
        match = DEFAULT_SAMPLE_KEY_RE.search(text)
        if not match:
            continue
        composition = match.group("composition")
        draw = match.group("draw")
        piece = match.group("piece")
        if draw and piece:
            return f"{composition} {draw}/{piece}"
        if composition:
            return composition
    return default


def safe_csv_stem(*parts: object, fallback: str = "worksheet") -> str:
    stem = "_".join(str(part or "").strip() for part in parts if str(part or "").strip())
    stem = _SAFE_STEM_RE.sub("_", stem).strip("._-")
    return stem[:140] or fallback


def build_manifest(
    *,
    source_project: Path,
    source_copy: Path,
    output_root: Path,
    worksheets: Iterable[OriginWorksheetExtract],
    origin_available: bool,
    status: str = "ok",
    error: str | None = None,
) -> dict[str, Any]:
    worksheet_entries = [worksheet.to_manifest(output_root=output_root) for worksheet in worksheets]
    return {
        "schema_version": 1,
        "status": status,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origin_available": origin_available,
        "source_project": str(source_project),
        "source_copy": str(source_copy),
        "output_root": str(output_root),
        "worksheet_count": len(worksheet_entries),
        "candidate_count": sum(
            1 for worksheet in worksheet_entries if worksheet.get("candidate_manual_stress_strain")
        ),
        "worksheets": worksheet_entries,
        "error": error,
    }


def load_origin_extract_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return normalize_origin_extract_manifest(payload, manifest_path=path)


def normalize_origin_extract_manifest(
    payload: Mapping[str, Any],
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    worksheets: list[dict[str, Any]] = []
    base_dir = manifest_path.parent if manifest_path is not None else None
    for raw in payload.get("worksheets") or []:
        if not isinstance(raw, Mapping):
            continue
        columns = raw.get("columns") or []
        normalized_columns: list[dict[str, Any]] = []
        origin_columns: list[OriginColumn] = []
        for index, raw_column in enumerate(columns):
            if not isinstance(raw_column, Mapping):
                continue
            column = OriginColumn(
                index=int(raw_column.get("index", index)),
                short_name=str(raw_column.get("short_name") or ""),
                long_name=str(raw_column.get("long_name") or raw_column.get("display_name") or ""),
                units=str(raw_column.get("units") or ""),
                comments=str(raw_column.get("comments") or ""),
            )
            origin_columns.append(column)
            normalized_columns.append(column.to_manifest())
        manual_map = raw.get("manual_column_map")
        if not isinstance(manual_map, Mapping):
            manual_map = infer_manual_column_map(origin_columns)
        else:
            manual_map = {str(key): str(value) for key, value in manual_map.items() if value}
        csv_path = str(raw.get("csv_path") or "")
        if base_dir is not None and csv_path and not Path(csv_path).is_absolute():
            csv_path = str((base_dir / csv_path).resolve())
        entry = dict(raw)
        entry["columns"] = normalized_columns
        entry["manual_column_map"] = dict(manual_map)
        entry["candidate_manual_stress_strain"] = {"strain_pct", "stress_mpa"}.issubset(
            manual_map
        )
        entry["csv_path"] = csv_path
        entry["row_count"] = int(raw.get("row_count") or 0)
        entry["sheet_rows"] = int(raw.get("sheet_rows") or raw.get("row_count") or 0)
        entry["sheet_cols"] = int(raw.get("sheet_cols") or len(normalized_columns))
        worksheets.append(entry)

    normalized = dict(payload)
    normalized["worksheets"] = worksheets
    normalized["worksheet_count"] = len(worksheets)
    normalized["candidate_count"] = sum(
        1 for worksheet in worksheets if worksheet.get("candidate_manual_stress_strain")
    )
    return normalized


def copy_origin_project(source_project: Path, output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    copy_path = output_root / source_project.name
    if source_project.resolve() == copy_path.resolve():
        return copy_path
    shutil.copy2(source_project, copy_path)
    return copy_path


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
