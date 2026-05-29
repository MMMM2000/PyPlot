from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


INDEX_COLUMNS = [
    "indexed_utc",
    "source_name",
    "run_name",
    "data_file",
    "data_path",
    "metadata_path",
    "metadata_exists",
    "last_write_time",
    "created_utc",
    "composition",
    "microwire",
    "sample",
    "load",
    "start_current_mA",
    "max_current_mA",
    "current_ramp_rate_mA_s",
    "reverse_enabled",
    "loops",
    "loops_infinite",
    "max_voltage_action",
    "supply_profile",
    "supply_label",
    "supply_channel",
    "supply_voltage_limit_v",
    "supply_shared_broker",
    "supply_broker_source",
    "git_branch",
    "git_commit",
    "data_rows",
]


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


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _file_last_write_utc(path: Path) -> str:
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return ""
    return stamp.isoformat(timespec="seconds")


def _data_row_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(
                1
                for line in handle
                if line.strip() and not line.lstrip().startswith("#")
            )
    except OSError:
        return None


def _metadata_paths(source_root: Path) -> list[Path]:
    metadata_root = source_root / "metadata"
    if not metadata_root.exists():
        return []
    return sorted(
        (
            path / "metadata.json"
            for path in metadata_root.iterdir()
            if path.is_dir() and (path / "metadata.json").exists()
        ),
        key=lambda path: path.parent.name.lower(),
    )


def _data_path_for_metadata(source_root: Path, run_name: str, metadata: Mapping[str, Any]) -> Path:
    data_file = str(metadata.get("data_file") or "").strip()
    if data_file:
        return source_root / data_file
    output_file = str(metadata.get("output_file") or "").strip()
    if output_file:
        return Path(output_file)
    return source_root / f"{run_name}.txt"


def _run_row(source: SourceRoot, metadata_path: Path, indexed_utc: str) -> dict[str, Any]:
    metadata = _read_json(metadata_path) or {}
    run_name = metadata_path.parent.name
    recipe = _mapping(metadata.get("recipe"))
    supply = _mapping(metadata.get("supply") or metadata.get("hardware"))
    source_control = _mapping(metadata.get("source_control"))
    data_path = _data_path_for_metadata(source.path, run_name, metadata)
    return {
        "indexed_utc": indexed_utc,
        "source_name": source.name,
        "run_name": run_name,
        "data_file": metadata.get("data_file") or data_path.name,
        "data_path": str(data_path),
        "metadata_path": str(metadata_path),
        "metadata_exists": str(metadata_path.exists()).lower(),
        "last_write_time": _file_last_write_utc(metadata_path),
        "created_utc": metadata.get("created_utc", ""),
        "composition": metadata.get("composition", ""),
        "microwire": metadata.get("microwire", ""),
        "sample": metadata.get("sample", ""),
        "load": metadata.get("load", ""),
        "start_current_mA": recipe.get("start_current_mA", metadata.get("start_current_mA", "")),
        "max_current_mA": recipe.get("max_current_mA", metadata.get("max_current_mA", "")),
        "current_ramp_rate_mA_s": recipe.get("current_ramp_rate_mA_s", metadata.get("step_mA", "")),
        "reverse_enabled": recipe.get("reverse_enabled", metadata.get("reverse_enabled", "")),
        "loops": recipe.get("loops", metadata.get("loops", "")),
        "loops_infinite": recipe.get("loops_infinite", metadata.get("loops_infinite", "")),
        "max_voltage_action": recipe.get("max_voltage_action", ""),
        "supply_profile": supply.get("profile_id", metadata.get("supply_profile", "")),
        "supply_label": supply.get("label", metadata.get("supply_display", "")),
        "supply_channel": supply.get("channel", ""),
        "supply_voltage_limit_v": supply.get("voltage_limit_v", ""),
        "supply_shared_broker": supply.get("shared_broker", ""),
        "supply_broker_source": supply.get("broker_source", ""),
        "git_branch": source_control.get("branch", ""),
        "git_commit": source_control.get("commit", ""),
        "data_rows": _data_row_count(data_path),
    }


def discover_runs(sources: Sequence[SourceRoot]) -> list[dict[str, Any]]:
    indexed_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for source in sources:
        if not source.path.exists():
            continue
        for metadata_path in _metadata_paths(source.path):
            rows.append(_run_row(source, metadata_path, indexed_utc))
    rows.sort(key=lambda row: (str(row["source_name"]).lower(), str(row["run_name"]).lower()))
    return rows


def write_index(rows: Sequence[Mapping[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "current_annealing_index.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    jsonl_path = output_dir / "current_annealing_index.jsonl"
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
    parser = argparse.ArgumentParser(description="Build a Current Annealing run metadata index.")
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="Source root to scan, either NAME=PATH or PATH. Repeat for multiple roots.",
    )
    parser.add_argument("--output-dir", required=True, help="Directory that receives index CSV/JSONL files.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sources = [_parse_source(value) for value in args.source]
    rows = discover_runs(sources)
    output_dir = Path(args.output_dir).expanduser()
    write_index(rows, output_dir)
    print(f"Indexed {len(rows)} Current Annealing runs into {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
