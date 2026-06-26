from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from data_logging.mini_dma_logger.run_quality import analyze_run_quality


INDEX_COLUMNS = [
    "indexed_utc",
    "source_name",
    "run_name",
    "run_path",
    "last_write_time",
    "metadata_exists",
    "created_utc",
    "sample_name",
    "composition",
    "microwire",
    "initial_length_mm",
    "wire_diameter_mm",
    "recipe_mode",
    "recipe_summary",
    "stop_reason",
    "stop_label",
    "stop_detail",
    "control_logic_version",
    "control_logic_fingerprint",
    "git_branch",
    "git_commit",
    "raw_scale_sample_count",
    "measurement_rows",
    "setup_rows",
    "control_trace_rows",
    "ui_telemetry_rows",
    "quality_analyzer_version",
    "run_type",
    "include_in_optimization_summary",
    "exclusion_reasons",
    "current_loop_count_estimate",
    "stress_error_rms_mpa",
    "stress_error_p95_abs_mpa",
    "stress_error_max_abs_mpa",
    "stress_error_median_abs_mpa",
    "current_hold_elapsed_s",
    "total_elapsed_s",
    "current_compliance_ratio",
    "biggest_problems",
]

DEFAULT_EXCLUDED_DIR_NAMES = {
    "archive",
    "automated",
    "automated_control_tests",
    "automation_history",
    "campaigns",
    "legacy_imports",
    "plans",
    "reports",
}


@dataclass(frozen=True)
class SourceRoot:
    name: str
    path: Path


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _csv_data_row_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return max(0, sum(1 for _line in handle) - 1)
    except OSError:
        return None


def _quality_row(run_dir: Path) -> dict[str, Any]:
    quality_path = run_dir / "run_quality.json"
    cached = _read_json(quality_path)
    if cached is None:
        try:
            cached = analyze_run_quality(run_dir).to_dict()
        except Exception:
            cached = {}
    return cached


def _join_list(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    return str(value or "")


def _file_last_write_utc(path: Path) -> str:
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return ""
    return stamp.isoformat(timespec="seconds")


def _run_row(source: SourceRoot, run_dir: Path, indexed_utc: str) -> dict[str, Any]:
    metadata_path = run_dir / "metadata.json"
    metadata = _read_json(metadata_path) or {}
    name_fields = metadata.get("name_fields")
    if not isinstance(name_fields, Mapping):
        name_fields = {}
    stop = metadata.get("stop")
    if not isinstance(stop, Mapping):
        stop = {}
    source_control = metadata.get("source_control")
    if not isinstance(source_control, Mapping):
        source_control = {}
    control_logic = metadata.get("control_logic")
    if not isinstance(control_logic, Mapping):
        control_logic = {}
    logging = metadata.get("logging")
    if not isinstance(logging, Mapping):
        logging = {}
    quality = _quality_row(run_dir)

    row = {
        "indexed_utc": indexed_utc,
        "source_name": source.name,
        "run_name": run_dir.name,
        "run_path": str(run_dir),
        "last_write_time": _file_last_write_utc(run_dir),
        "metadata_exists": str(metadata_path.exists()).lower(),
        "created_utc": metadata.get("created_utc", ""),
        "sample_name": metadata.get("sample_name", ""),
        "composition": name_fields.get("composition", ""),
        "microwire": name_fields.get("microwire", ""),
        "initial_length_mm": metadata.get("initial_length_mm", ""),
        "wire_diameter_mm": metadata.get("wire_diameter_mm", ""),
        "recipe_mode": metadata.get("recipe_mode", ""),
        "recipe_summary": metadata.get("recipe_summary", ""),
        "stop_reason": stop.get("reason", ""),
        "stop_label": stop.get("label", ""),
        "stop_detail": stop.get("detail", ""),
        "control_logic_version": control_logic.get("version", ""),
        "control_logic_fingerprint": control_logic.get("fingerprint", ""),
        "git_branch": source_control.get("branch", ""),
        "git_commit": source_control.get("commit", ""),
        "raw_scale_sample_count": _nested(metadata, "logging", "raw_scale_sample_count") or "",
        "measurement_rows": _csv_data_row_count(run_dir / "measurement.csv"),
        "setup_rows": _csv_data_row_count(run_dir / "setup.csv"),
        "control_trace_rows": _csv_data_row_count(run_dir / "control_trace.csv"),
        "ui_telemetry_rows": _csv_data_row_count(run_dir / "ui_telemetry.csv"),
        "quality_analyzer_version": quality.get("analyzer_version", ""),
        "run_type": quality.get("run_type", ""),
        "include_in_optimization_summary": quality.get("include_in_optimization_summary", ""),
        "exclusion_reasons": _join_list(quality.get("exclusion_reasons")),
        "current_loop_count_estimate": quality.get("current_loop_count_estimate", ""),
        "stress_error_rms_mpa": quality.get("stress_error_rms_mpa", ""),
        "stress_error_p95_abs_mpa": quality.get("stress_error_p95_abs_mpa", ""),
        "stress_error_max_abs_mpa": quality.get("stress_error_max_abs_mpa", ""),
        "stress_error_median_abs_mpa": quality.get("stress_error_median_abs_mpa", ""),
        "current_hold_elapsed_s": quality.get("current_hold_elapsed_s", ""),
        "total_elapsed_s": quality.get("total_elapsed_s", ""),
        "current_compliance_ratio": quality.get("current_compliance_ratio", ""),
        "biggest_problems": _join_list(quality.get("biggest_problems")),
    }
    if not row["raw_scale_sample_count"]:
        row["raw_scale_sample_count"] = logging.get("raw_scale_sample_count", "")
    return row


def discover_runs(
    sources: Sequence[SourceRoot], exclude_names: Iterable[str] = ()
) -> list[dict[str, Any]]:
    indexed_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    excluded = {name.lower() for name in exclude_names}
    rows: list[dict[str, Any]] = []
    for source in sources:
        if not source.path.exists():
            continue
        for run_dir in sorted((path for path in source.path.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
            if run_dir.name.lower() in DEFAULT_EXCLUDED_DIR_NAMES:
                continue
            if run_dir.name.lower() in excluded:
                continue
            rows.append(_run_row(source, run_dir, indexed_utc))
    rows.sort(key=lambda row: (str(row["source_name"]).lower(), str(row["run_name"]).lower()))
    return rows


def write_index(rows: Sequence[Mapping[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "runs_index.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    jsonl_path = output_dir / "runs_index.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _parse_source(value: str) -> SourceRoot:
    if "=" in value:
        name, path_text = value.split("=", 1)
        return SourceRoot(name=name.strip() or Path(path_text).name, path=Path(path_text).expanduser())
    path = Path(value).expanduser()
    return SourceRoot(name=path.name, path=path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a TMA automation run index.")
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="Source root to scan, either NAME=PATH or PATH. Repeat for multiple roots.",
    )
    parser.add_argument(
        "--exclude-name",
        action="append",
        default=[],
        help="Exact run-folder name to skip. Repeat for multiple active or out-of-scope runs.",
    )
    parser.add_argument("--output-dir", required=True, help="Directory that receives runs_index.csv/jsonl.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sources = [_parse_source(value) for value in args.source]
    rows = discover_runs(sources, exclude_names=args.exclude_name)
    write_index(rows, Path(args.output_dir).expanduser())
    print(f"Indexed {len(rows)} TMA automation runs into {Path(args.output_dir).expanduser()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
