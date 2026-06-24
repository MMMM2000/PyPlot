"""Offline baseline-subtraction helpers for AC susceptibility TSV files."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence


PRIMARY_COLUMN = "LCR primary"
SECONDARY_COLUMN = "LCR secondary"
SETTING_COLUMNS = (
    "LCR function",
    "LCR frequency (Hz)",
    "LCR level mode",
    "LCR level",
)
PRIMARY_BASELINE_COLUMN = "LCR primary empty-coil baseline"
SECONDARY_BASELINE_COLUMN = "LCR secondary empty-coil baseline"
PRIMARY_SUBTRACTED_COLUMN = "LCR primary baseline subtracted"
SECONDARY_SUBTRACTED_COLUMN = "LCR secondary baseline subtracted"
BASELINE_STATUS_COLUMN = "Empty-coil baseline status"


@dataclass(frozen=True)
class AcTsvData:
    path: Path
    comments: list[str]
    columns: list[str]
    rows: list[dict[str, str]]


@dataclass(frozen=True)
class BaselineValue:
    key: tuple[str, str, str, str]
    primary_mean: float | None
    secondary_mean: float | None
    count: int


@dataclass(frozen=True)
class BaselineSubtractionSummary:
    output_path: Path
    sweep_path: Path
    baseline_path: Path
    total_rows: int
    subtracted_rows: int
    unmatched_rows: int
    baseline_count: int


def read_ac_tsv(path: str | Path) -> AcTsvData:
    """Read an AC baseline or sweep TSV while preserving comment metadata."""

    input_path = Path(path)
    comments: list[str] = []
    columns: list[str] | None = None
    rows: list[dict[str, str]] = []
    with input_path.open("r", encoding="utf-8", newline="") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if line.startswith("#"):
                comments.append(line)
                candidate = line[1:].strip()
                if "\t" in candidate and PRIMARY_COLUMN in candidate:
                    columns = candidate.split("\t")
                continue
            if not line.strip():
                continue
            if columns is None:
                raise ValueError(f"{input_path} does not contain an AC TSV header before data rows")
            values = next(csv.reader([line], delimiter="\t"))
            rows.append({column: values[index] if index < len(values) else "" for index, column in enumerate(columns)})
    if columns is None:
        raise ValueError(f"{input_path} does not contain an AC TSV header")
    return AcTsvData(path=input_path, comments=comments, columns=columns, rows=rows)


def _parse_float(text: str) -> float | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    try:
        value = float(stripped)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def _format_float(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.12g}"


def _canonical_numeric_text(text: str) -> str:
    value = _parse_float(text)
    if value is None:
        return str(text or "").strip()
    return f"{value:.12g}"


def setting_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    missing = [column for column in SETTING_COLUMNS if column not in row]
    if missing:
        raise ValueError(f"AC TSV row is missing setting columns: {', '.join(missing)}")
    return (
        str(row["LCR function"]).strip(),
        _canonical_numeric_text(row["LCR frequency (Hz)"]),
        str(row["LCR level mode"]).strip().lower(),
        _canonical_numeric_text(row["LCR level"]),
    )


def _mean(values: Iterable[float]) -> float | None:
    valid = list(values)
    if not valid:
        return None
    return sum(valid) / len(valid)


def build_empty_coil_baseline(baseline: AcTsvData) -> dict[tuple[str, str, str, str], BaselineValue]:
    """Average empty-coil readings by LCR setting."""

    grouped: dict[tuple[str, str, str, str], dict[str, list[float]]] = {}
    for row in baseline.rows:
        key = setting_key(row)
        bucket = grouped.setdefault(key, {"primary": [], "secondary": []})
        primary = _parse_float(row.get(PRIMARY_COLUMN, ""))
        secondary = _parse_float(row.get(SECONDARY_COLUMN, ""))
        if primary is not None:
            bucket["primary"].append(primary)
        if secondary is not None:
            bucket["secondary"].append(secondary)
    result: dict[tuple[str, str, str, str], BaselineValue] = {}
    for key, values in grouped.items():
        result[key] = BaselineValue(
            key=key,
            primary_mean=_mean(values["primary"]),
            secondary_mean=_mean(values["secondary"]),
            count=max(len(values["primary"]), len(values["secondary"])),
        )
    return result


def subtract_empty_coil_baseline(
    sweep: AcTsvData,
    baseline_values: dict[tuple[str, str, str, str], BaselineValue],
) -> tuple[list[str], list[dict[str, str]], int, int]:
    """Append baseline-subtracted columns to sweep rows."""

    output_columns = list(sweep.columns)
    for column in (
        PRIMARY_BASELINE_COLUMN,
        SECONDARY_BASELINE_COLUMN,
        PRIMARY_SUBTRACTED_COLUMN,
        SECONDARY_SUBTRACTED_COLUMN,
        BASELINE_STATUS_COLUMN,
    ):
        if column not in output_columns:
            output_columns.append(column)
    output_rows: list[dict[str, str]] = []
    subtracted_rows = 0
    unmatched_rows = 0
    for row in sweep.rows:
        output = dict(row)
        baseline = baseline_values.get(setting_key(row))
        if baseline is None:
            output[BASELINE_STATUS_COLUMN] = "unmatched_setting"
            unmatched_rows += 1
        else:
            primary = _parse_float(row.get(PRIMARY_COLUMN, ""))
            secondary = _parse_float(row.get(SECONDARY_COLUMN, ""))
            primary_subtracted = None if primary is None or baseline.primary_mean is None else primary - baseline.primary_mean
            secondary_subtracted = (
                None if secondary is None or baseline.secondary_mean is None else secondary - baseline.secondary_mean
            )
            output[PRIMARY_BASELINE_COLUMN] = _format_float(baseline.primary_mean)
            output[SECONDARY_BASELINE_COLUMN] = _format_float(baseline.secondary_mean)
            output[PRIMARY_SUBTRACTED_COLUMN] = _format_float(primary_subtracted)
            output[SECONDARY_SUBTRACTED_COLUMN] = _format_float(secondary_subtracted)
            output[BASELINE_STATUS_COLUMN] = "subtracted"
            subtracted_rows += 1
        output_rows.append(output)
    return output_columns, output_rows, subtracted_rows, unmatched_rows


def write_baseline_subtracted_tsv(
    output_path: str | Path,
    *,
    sweep: AcTsvData,
    baseline: AcTsvData,
    columns: Sequence[str],
    rows: Sequence[dict[str, str]],
    baseline_values: dict[tuple[str, str, str, str], BaselineValue],
    subtracted_rows: int,
    unmatched_rows: int,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "kind": "empty_coil_baseline_subtraction",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sweep_path": str(sweep.path),
        "baseline_path": str(baseline.path),
        "model": "subtract_empty_coil_mean_by_lcr_setting",
        "total_rows": len(rows),
        "subtracted_rows": int(subtracted_rows),
        "unmatched_rows": int(unmatched_rows),
        "baseline_count": len(baseline_values),
        "note": "Derived analysis file; original LCR columns are preserved.",
    }
    with path.open("w", encoding="utf-8", newline="") as fh:
        for comment in sweep.comments:
            fh.write(comment + "\n")
        fh.write("# baseline_subtraction_json=" + json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
        writer = csv.DictWriter(fh, fieldnames=list(columns), delimiter="\t", lineterminator="\n")
        fh.write("# " + "\t".join(columns) + "\n")
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def subtract_file(
    *,
    sweep_path: str | Path,
    baseline_path: str | Path,
    output_path: str | Path | None = None,
) -> BaselineSubtractionSummary:
    sweep = read_ac_tsv(sweep_path)
    baseline = read_ac_tsv(baseline_path)
    baseline_values = build_empty_coil_baseline(baseline)
    columns, rows, subtracted_rows, unmatched_rows = subtract_empty_coil_baseline(sweep, baseline_values)
    output = Path(output_path) if output_path is not None else sweep.path.with_name(
        f"{sweep.path.stem}_empty_coil_subtracted.tsv"
    )
    write_baseline_subtracted_tsv(
        output,
        sweep=sweep,
        baseline=baseline,
        columns=columns,
        rows=rows,
        baseline_values=baseline_values,
        subtracted_rows=subtracted_rows,
        unmatched_rows=unmatched_rows,
    )
    return BaselineSubtractionSummary(
        output_path=output,
        sweep_path=sweep.path,
        baseline_path=baseline.path,
        total_rows=len(rows),
        subtracted_rows=subtracted_rows,
        unmatched_rows=unmatched_rows,
        baseline_count=len(baseline_values),
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a derived AC TSV with empty-coil baseline-subtracted LCR columns.",
    )
    parser.add_argument("sweep", help="Microwire AC sweep TSV.")
    parser.add_argument("--baseline", required=True, help="Empty-coil baseline TSV measured with matching LCR settings.")
    parser.add_argument("--output", help="Output TSV path. Defaults to <sweep>_empty_coil_subtracted.tsv.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = subtract_file(sweep_path=args.sweep, baseline_path=args.baseline, output_path=args.output)
    print(json.dumps({key: str(value) for key, value in summary.__dict__.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
