from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


IDENTITY_SCHEMA_VERSION = 1
IDENTITY_CORRECTION_FILENAME = "identity_correction.json"
DIAGNOSTIC_SUMMARY_FILENAME = "diagnostic_summary.json"
DIAGNOSTIC_BUNDLE_FILENAME = "diagnostic_bundle.zip"


def utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalized(value: object) -> str:
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


@dataclass(frozen=True)
class SampleIdentitySnapshot:
    schema_version: int
    frozen_utc: str
    composition: str
    microwire: str
    specimen: str
    condition: str
    sample_name: str
    sample_id: str
    diameter_mm: float
    diameter_provenance: Mapping[str, Any]
    initial_length_mm: float
    length_provenance: Mapping[str, Any]
    base_filename: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_base_filename(self, base_filename: str) -> SampleIdentitySnapshot:
        return replace(self, base_filename=str(base_filename))


def build_sample_identity_snapshot(
    *,
    frozen_utc: str,
    composition: str,
    microwire: str,
    specimen: str,
    condition: str,
    sample_name: str,
    diameter_mm: float,
    diameter_provenance: Mapping[str, Any],
    initial_length_mm: float,
    length_provenance: Mapping[str, Any],
    base_filename: str,
) -> SampleIdentitySnapshot:
    composition = str(composition).strip()
    microwire = str(microwire).strip()
    specimen = str(specimen).strip()
    condition = " ".join(str(condition).split())
    sample_name = " ".join(str(sample_name).split())
    sample_id = " ".join(part for part in (composition, microwire, specimen) if part)
    snapshot = SampleIdentitySnapshot(
        schema_version=IDENTITY_SCHEMA_VERSION,
        frozen_utc=str(frozen_utc),
        composition=composition,
        microwire=microwire,
        specimen=specimen,
        condition=condition,
        sample_name=sample_name,
        sample_id=sample_id,
        diameter_mm=float(diameter_mm),
        diameter_provenance=dict(diameter_provenance),
        initial_length_mm=float(initial_length_mm),
        length_provenance=dict(length_provenance),
        base_filename=str(base_filename).strip(),
    )
    mismatches = identity_mismatches(snapshot)
    if mismatches:
        raise ValueError("Sample identity preflight failed: " + "; ".join(mismatches))
    return snapshot


def identity_mismatches(snapshot: SampleIdentitySnapshot) -> list[str]:
    mismatches: list[str] = []
    if not snapshot.sample_name and any((snapshot.composition, snapshot.microwire, snapshot.specimen)):
        mismatches.append("sample name is empty")
    if not snapshot.base_filename:
        mismatches.append("output base filename is empty")
    if not math.isfinite(snapshot.diameter_mm) or snapshot.diameter_mm <= 0.0:
        mismatches.append("diameter must be a positive finite value")
    if not math.isfinite(snapshot.initial_length_mm) or snapshot.initial_length_mm <= 0.0:
        mismatches.append("initial length must be a positive finite value")
    sample_token = _normalized(snapshot.sample_name)
    filename_token = _normalized(snapshot.base_filename)
    for label, value in (("composition", snapshot.composition), ("microwire", snapshot.microwire)):
        token = _normalized(value)
        if not token:
            continue
        if token not in sample_token:
            mismatches.append(f"sample name does not contain structured {label} {value!r}")
        if token not in filename_token:
            mismatches.append(f"output base filename does not contain structured {label} {value!r}")
    return mismatches


def write_identity_correction(
    run_dir: Path,
    *,
    corrected: Mapping[str, Any],
    reason: str,
    operator: str,
    timestamp_utc: str | None = None,
) -> Path:
    run_dir = Path(run_dir)
    metadata_path = run_dir / "metadata.json"
    metadata_bytes = metadata_path.read_bytes()
    metadata = json.loads(metadata_bytes.decode("utf-8"))
    if not str(reason).strip() or not str(operator).strip():
        raise ValueError("Identity corrections require a non-empty reason and operator.")
    original = metadata.get("sample_identity") or {
        "sample_name": metadata.get("sample_name"),
        "composition": (metadata.get("name_fields") or {}).get("composition"),
        "microwire": (metadata.get("name_fields") or {}).get("microwire"),
        "specimen": (metadata.get("name_fields") or {}).get("specimen"),
        "diameter_mm": metadata.get("wire_diameter_mm"),
        "initial_length_mm": metadata.get("initial_length_mm"),
    }
    correction_path = run_dir / IDENTITY_CORRECTION_FILENAME
    history: list[dict[str, Any]] = []
    if correction_path.exists():
        existing = json.loads(correction_path.read_text(encoding="utf-8"))
        history = list(existing.get("corrections") or [])
    effective = dict(original)
    for record in history:
        effective.update(record.get("corrected") or {})
    update = {str(key): value for key, value in corrected.items()}
    history.append(
        {
            "timestamp_utc": timestamp_utc or utc_timestamp(),
            "operator": str(operator).strip(),
            "reason": str(reason).strip(),
            "original": effective,
            "corrected": update,
        }
    )
    payload = {
        "schema_version": 1,
        "raw_metadata_path": metadata_path.name,
        "raw_metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "original_identity": original,
        "effective_identity": {**effective, **update},
        "corrections": history,
    }
    correction_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return correction_path


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _numbers(rows: Sequence[Mapping[str, str]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        try:
            value = float(row.get(field) or "")
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _percentiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(float(value) for value in values)

    def percentile(fraction: float) -> float:
        index = (len(ordered) - 1) * fraction
        lower = math.floor(index)
        upper = math.ceil(index)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)

    return {
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": ordered[-1],
    }


def build_diagnostic_summary(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    trace_rows = _read_csv(run_dir / "control_trace.csv")
    telemetry_rows = _read_csv(run_dir / "ui_telemetry.csv")
    raw_rows = _read_csv(run_dir / "scale_raw.csv")
    measurement_rows = _read_csv(run_dir / "measurement.csv")
    intervals = _numbers(raw_rows, "host_interval_ms")
    ui_intervals = _numbers(telemetry_rows, "actual_interval_ms")
    ui_handlers = _numbers(telemetry_rows, "handler_duration_ms")
    decisions: dict[str, int] = {}
    gate_reasons: dict[str, int] = {}
    results: dict[str, int] = {}
    command_ids: set[str] = set()
    for row in trace_rows:
        for target, key in ((decisions, "decision"), (gate_reasons, "gate_reason"), (results, "result")):
            value = str(row.get(key) or "").strip()
            if value:
                target[value] = target.get(value, 0) + 1
        command_id = str(row.get("motor_command_id") or "").strip()
        if command_id:
            command_ids.add(command_id)
    target_intervals = _numbers(telemetry_rows, "target_interval_ms")
    ui_stalls = sum(
        1
        for index, interval in enumerate(ui_intervals)
        if interval > max(1000.0, 3.0 * (target_intervals[index] if index < len(target_intervals) else 0.0))
    )
    correction_path = run_dir / IDENTITY_CORRECTION_FILENAME
    correction = json.loads(correction_path.read_text(encoding="utf-8")) if correction_path.exists() else None
    return {
        "schema_version": 1,
        "generated_utc": utc_timestamp(),
        "run_folder": run_dir.name,
        "sample_identity": metadata.get("sample_identity"),
        "identity_correction": correction,
        "scale": {
            "profile": (metadata.get("scale") or {}).get("profile"),
            "accepted_sample_count": len(raw_rows),
            "rejected_sample_count": (metadata.get("scale") or {}).get("rejected_sample_count"),
            "interval_ms": _percentiles(intervals),
            "interval_mean_ms": statistics.fmean(intervals) if intervals else None,
        },
        "control": {
            "trace_rows": len(trace_rows),
            "decisions": decisions,
            "results": results,
            "gate_reasons": gate_reasons,
            "motor_command_count": len(command_ids),
            "last_state": trace_rows[-1].get("controller_state") if trace_rows else None,
        },
        "ui_logging": {
            "telemetry_rows": len(telemetry_rows),
            "measurement_rows": len(measurement_rows),
            "stall_count": ui_stalls,
            "dropped_row_count": (metadata.get("logging") or {}).get("dropped_row_count"),
            "actual_interval_ms": _percentiles(ui_intervals),
            "handler_duration_ms": _percentiles(ui_handlers),
        },
        "stop": metadata.get("stop"),
        "source_control": metadata.get("source_control"),
        "control_logic": metadata.get("control_logic"),
    }


def write_diagnostic_summary(run_dir: Path) -> Path:
    path = Path(run_dir) / DIAGNOSTIC_SUMMARY_FILENAME
    path.write_text(json.dumps(build_diagnostic_summary(run_dir), indent=2), encoding="utf-8")
    return path


def write_diagnostic_bundle(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    summary_path = write_diagnostic_summary(run_dir)
    bundle_path = run_dir / DIAGNOSTIC_BUNDLE_FILENAME
    include_names = (
        "metadata.json",
        "control_trace.csv",
        "ui_telemetry.csv",
        "scale_raw.csv",
        "run_log.txt",
        IDENTITY_CORRECTION_FILENAME,
        summary_path.name,
    )
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in include_names:
            source = run_dir / name
            if source.exists():
                archive.write(source, arcname=name)
    return bundle_path


def _parse_set_values(values: Sequence[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"Correction must use key=value syntax: {item!r}")
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create non-mutating TMA run diagnostics and identity corrections.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    summarize = subparsers.add_parser("summarize", help="Write a machine-readable run diagnostic summary.")
    summarize.add_argument("run_dir", type=Path)
    bundle = subparsers.add_parser("bundle", help="Write a compact remote-diagnostic ZIP bundle.")
    bundle.add_argument("run_dir", type=Path)
    correct = subparsers.add_parser("correct-identity", help="Append an identity correction sidecar.")
    correct.add_argument("run_dir", type=Path)
    correct.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", required=True)
    correct.add_argument("--reason", required=True)
    correct.add_argument("--operator", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "summarize":
        output = write_diagnostic_summary(args.run_dir)
    elif args.command == "bundle":
        output = write_diagnostic_bundle(args.run_dir)
    else:
        output = write_identity_correction(
            args.run_dir,
            corrected=_parse_set_values(args.set),
            reason=args.reason,
            operator=args.operator,
        )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
