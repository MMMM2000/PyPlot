"""Read-only, finished-run diagnostics for TMA logger output folders."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SUMMARY_JSON = "diagnostic_summary.json"
SUMMARY_MARKDOWN = "diagnostic_summary.md"
BUNDLE_ZIP = "diagnostic_bundle.zip"
CORRECTION_JSON = "identity_correction.json"
SCHEMA_VERSION = 2

_LOGGING_PATHS = {
    "measurement": ("measurement_csv", "measurement.csv"),
    "control": ("control_trace_csv", "control_trace.csv"),
    "raw_scale": ("raw_scale_sidecar", "scale_raw.csv"),
    "ui_timing": ("ui_telemetry_csv", "ui_telemetry.csv"),
    "run_log": ("run_log_txt", "run_log.txt"),
    "ir_temperature": ("ir_temperature_sidecar", "ir_temperature.csv"),
    "setup": ("setup_csv", "setup.csv"),
}
_CSV_STREAMS = ("measurement", "control", "raw_scale", "ui_timing")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class DiagnosticError(ValueError):
    """Raised when a run cannot safely produce final diagnostics."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        data = path.read_bytes()
        payload = json.loads(data.decode("utf-8-sig"))
    except FileNotFoundError as exc:
        raise DiagnosticError(f"Required metadata file is missing: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiagnosticError(f"Cannot read metadata file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DiagnosticError(f"Metadata must contain a JSON object: {path}")
    return payload, data


def _contained_file(run_dir: Path, value: object, *, label: str) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = (run_dir / text).resolve()
    try:
        candidate.relative_to(run_dir)
    except ValueError as exc:
        raise DiagnosticError(f"Unsafe {label} path outside the run folder: {text!r}") from exc
    return candidate


def _load_final_metadata(run_dir: Path) -> tuple[Path, dict[str, Any], bytes]:
    legacy_path = run_dir / "metadata.json"
    metadata, metadata_bytes = _read_json(legacy_path)
    logging = metadata.get("logging")
    configured = logging.get("metadata_json") if isinstance(logging, Mapping) else None
    configured_path = _contained_file(run_dir, configured, label="metadata")
    if configured_path is not None and configured_path != legacy_path:
        metadata, metadata_bytes = _read_json(configured_path)
        return configured_path, metadata, metadata_bytes
    return legacy_path, metadata, metadata_bytes


def discover_run_files(run_dir: Path) -> tuple[dict[str, Path | None], dict[str, Any], bytes]:
    """Resolve authoritative run artifacts without opening hardware or importing Qt."""

    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise DiagnosticError(f"Run folder does not exist: {run_dir}")
    metadata_path, metadata, metadata_bytes = _load_final_metadata(run_dir)
    logging = metadata.get("logging")
    logging = logging if isinstance(logging, Mapping) else {}
    files: dict[str, Path | None] = {"metadata": metadata_path}
    for name, (metadata_key, legacy_name) in _LOGGING_PATHS.items():
        configured = logging.get(metadata_key)
        path = _contained_file(run_dir, configured, label=metadata_key)
        if path is None:
            legacy = run_dir / legacy_name
            path = legacy if legacy.exists() else None
        files[name] = path if path is not None and path.is_file() else None
    return files, metadata, metadata_bytes


def _pending_finalization(metadata: Mapping[str, Any]) -> list[str]:
    pending: list[str] = []
    source = metadata.get("source_control")
    if isinstance(source, Mapping) and str(source.get("capture_state") or "").lower() == "pending":
        pending.append("source-control capture")
    logging = metadata.get("logging")
    logging = logging if isinstance(logging, Mapping) else {}
    sidecars = logging.get("sensor_sidecars")
    if isinstance(sidecars, Mapping):
        for name, outcome in sidecars.items():
            if not isinstance(outcome, Mapping):
                continue
            status = str(outcome.get("status") or "").lower()
            try:
                pending_rows = int(outcome.get("pending_rows") or 0)
            except (TypeError, ValueError):
                pending_rows = 0
            reason = str(outcome.get("reason") or "").lower()
            if status == "active" or pending_rows > 0 or reason == "close_timeout":
                pending.append(f"{name} sidecar reconciliation")
    return pending


def validate_run_state(metadata: Mapping[str, Any], *, snapshot: bool = False) -> dict[str, Any]:
    state = str(metadata.get("session_state") or "").lower()
    finished_utc = str(metadata.get("finished_utc") or "").strip()
    lifecycle_finished = state == "finished" and bool(finished_utc)
    pending = _pending_finalization(metadata)
    if not snapshot and not lifecycle_finished:
        raise DiagnosticError(
            "Run is active or incomplete; finalized diagnostics require "
            "session_state='finished' and finished_utc. Use snapshot mode explicitly for a point-in-time copy."
        )
    if not snapshot and pending:
        raise DiagnosticError(
            "Run finalization is still pending ("
            + ", ".join(pending)
            + "). Use snapshot mode explicitly for a point-in-time copy."
        )
    return {
        "mode": "snapshot" if snapshot else "final",
        "session_state": state or None,
        "finished_utc": finished_utc or None,
        "lifecycle_finished": lifecycle_finished,
        "pending_finalization": pending,
    }


def _read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        raise DiagnosticError(f"Cannot read CSV sidecar {path}: {exc}") from exc


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _percentiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = (len(ordered) - 1) * fraction
        low = math.floor(index)
        high = math.ceil(index)
        if low == high:
            return ordered[low]
        return ordered[low] + (ordered[high] - ordered[low]) * (index - low)

    return {
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": ordered[-1],
    }


def _stream_summary(
    rows: Sequence[Mapping[str, str]],
    *,
    expected_interval_s: float | None,
    run_started: datetime | None,
) -> dict[str, Any]:
    elapsed = [value for row in rows if (value := _finite_float(row.get("elapsed_s"))) is not None]
    utc_values = [value for row in rows if (value := _parse_utc(row.get("timestamp_utc"))) is not None]
    elapsed_gaps = [later - earlier for earlier, later in zip(elapsed, elapsed[1:]) if later >= earlier]
    utc_gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(utc_values, utc_values[1:])
        if later >= earlier
    ]
    gaps = elapsed_gaps or utc_gaps
    baseline = expected_interval_s
    if baseline is None and gaps:
        baseline = statistics.median(gaps)
    threshold = None if baseline is None else max(0.001, baseline * 3.0)
    gap_count = 0 if threshold is None else sum(gap > threshold for gap in gaps)
    first_utc = utc_values[0] if utc_values else None
    last_utc = utc_values[-1] if utc_values else None
    elapsed_span = elapsed[-1] - elapsed[0] if len(elapsed) >= 2 else None
    utc_span = (last_utc - first_utc).total_seconds() if first_utc and last_utc else None
    return {
        "row_count": len(rows),
        "first_elapsed_s": elapsed[0] if elapsed else None,
        "last_elapsed_s": elapsed[-1] if elapsed else None,
        "first_utc": _iso_utc(first_utc),
        "last_utc": _iso_utc(last_utc),
        "first_utc_offset_from_run_start_s": (
            (first_utc - run_started).total_seconds() if first_utc and run_started else None
        ),
        "elapsed_span_s": elapsed_span,
        "utc_span_s": utc_span,
        "clock_span_delta_s": (
            utc_span - elapsed_span if utc_span is not None and elapsed_span is not None else None
        ),
        "expected_interval_s": expected_interval_s,
        "gap_threshold_s": threshold,
        "gap_count": gap_count,
        "interval_s": _percentiles(gaps),
    }


def _count_values(rows: Sequence[Mapping[str, str]], field: str) -> dict[str, int]:
    values = (str(row.get(field) or "").strip() for row in rows)
    return dict(sorted(Counter(value for value in values if value).items()))


def _interval_ms(metadata: Mapping[str, Any], *path: str) -> float | None:
    value: object = metadata
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    number = _finite_float(value)
    return None if number is None else number / 1000.0


def _source_manifest(
    run_dir: Path,
    files: Mapping[str, Path | None],
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    manifest: dict[str, dict[str, Any]] = {}
    content: dict[str, bytes] = {}
    for role in sorted(files):
        path = files[role]
        if path is None:
            manifest[role] = {"available": False, "path": None, "size_bytes": None, "sha256": None}
            continue
        data = path.read_bytes()
        relative = path.relative_to(run_dir).as_posix()
        manifest[role] = {
            "available": True,
            "path": relative,
            "size_bytes": len(data),
            "sha256": _sha256(data),
        }
        content[relative] = data
    return manifest, content


def build_diagnostic_summary(run_dir: Path, *, snapshot: bool = False) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    files, metadata, metadata_bytes = discover_run_files(run_dir)
    state = validate_run_state(metadata, snapshot=snapshot)
    rows = {name: _read_csv(files[name]) for name in _CSV_STREAMS}
    created_utc = _parse_utc(metadata.get("created_utc"))
    expected = {
        "measurement": _interval_ms(metadata, "logging", "log_interval_ms"),
        "control": _interval_ms(metadata, "control", "control_interval_ms"),
        "raw_scale": _interval_ms(metadata, "scale", "poll_interval_ms"),
        "ui_timing": _interval_ms(metadata, "control", "ui_heartbeat_interval_ms"),
    }
    streams = {
        name: _stream_summary(rows[name], expected_interval_s=expected[name], run_started=created_utc)
        for name in _CSV_STREAMS
    }
    raw_intervals_ms = [
        value
        for row in rows["raw_scale"]
        if (value := _finite_float(row.get("host_interval_ms"))) is not None
    ]
    ui_actual_ms = [
        value
        for row in rows["ui_timing"]
        if (value := _finite_float(row.get("actual_interval_ms"))) is not None
    ]
    ui_handler_ms = [
        value
        for row in rows["ui_timing"]
        if (value := _finite_float(row.get("handler_duration_ms"))) is not None
    ]
    command_rows = [
        row
        for row in rows["control"]
        if (_finite_float(row.get("correction_mm")) or 0.0) != 0.0
        or (_finite_float(row.get("motor_step_mm")) or 0.0) != 0.0
        or str(row.get("target_mm") or "").strip()
    ]
    logging = metadata.get("logging")
    logging = logging if isinstance(logging, Mapping) else {}
    source_files, _content = _source_manifest(run_dir, files)
    source_files["metadata"]["sha256"] = _sha256(metadata_bytes)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": str(metadata.get("finished_utc") or metadata.get("created_utc") or ""),
        "run_folder": run_dir.name,
        "diagnostic_state": state,
        "run_timeline": {
            "created_utc": metadata.get("created_utc"),
            "finished_utc": metadata.get("finished_utc"),
            "recorded_elapsed_s": metadata.get("elapsed_s"),
            "streams": streams,
        },
        "measurement": {
            "row_count": len(rows["measurement"]),
            "recipe_modes": _count_values(rows["measurement"], "recipe_mode"),
            "automation_phases": _count_values(rows["measurement"], "automation_phase"),
            "timeline": streams["measurement"],
        },
        "control": {
            "row_count": len(rows["control"]),
            "command_row_count": len(command_rows),
            "decisions": _count_values(rows["control"], "decision"),
            "results": _count_values(rows["control"], "result"),
            "reasons": _count_values(rows["control"], "reason"),
            "automation_phases": _count_values(rows["control"], "automation_phase"),
            "last_decision": dict(rows["control"][-1]) if rows["control"] else None,
            "timeline": streams["control"],
        },
        "raw_scale": {
            "row_count": len(rows["raw_scale"]),
            "host_interval_ms": _percentiles(raw_intervals_ms),
            "metadata_sample_count": logging.get("raw_scale_sample_count"),
            "metadata_rate_hz": logging.get("raw_scale_session_rate_hz"),
            "metadata_max_gap_s": logging.get("raw_scale_max_gap_s"),
            "timeline": streams["raw_scale"],
        },
        "ui_timing": {
            "row_count": len(rows["ui_timing"]),
            "actual_interval_ms": _percentiles(ui_actual_ms),
            "handler_duration_ms": _percentiles(ui_handler_ms),
            "metadata_sample_count": logging.get("ui_telemetry_sample_count"),
            "timeline": streams["ui_timing"],
        },
        "stop": metadata.get("stop"),
        "source_control": metadata.get("source_control"),
        "scale": {
            "profile": (metadata.get("scale") or {}).get("profile")
            if isinstance(metadata.get("scale"), Mapping)
            else None,
            "settings": metadata.get("scale"),
        },
        "control_settings": metadata.get("control"),
        "control_logic": metadata.get("control_logic"),
        "logging_finalization": {
            "run_log_complete": logging.get("run_log_complete"),
            "run_log_incomplete_lines": logging.get("run_log_incomplete_lines"),
            "run_log_incomplete_reason": logging.get("run_log_incomplete_reason"),
            "sensor_sidecars": logging.get("sensor_sidecars"),
        },
        "source_files": source_files,
    }


def render_markdown_summary(summary: Mapping[str, Any]) -> str:
    state = summary["diagnostic_state"]
    stop = summary.get("stop") or {}
    source = summary.get("source_control") or {}
    scale = summary.get("scale") or {}
    control_settings = summary.get("control_settings") or {}
    lines = [
        f"# TMA diagnostic summary: {summary['run_folder']}",
        "",
        f"- Mode: {state.get('mode')}",
        f"- Session state: {state.get('session_state')}",
        f"- Finished UTC: {state.get('finished_utc')}",
        f"- Stop reason: {stop.get('reason')}",
        f"- Stop category: {stop.get('category')}",
        f"- Code branch: {source.get('branch')}",
        f"- Code commit: {source.get('commit')}",
        f"- Scale profile: {scale.get('profile')}",
        f"- Force-control profile: {control_settings.get('force_control_profile')}",
        "",
        "## Streams",
        "",
        "| Stream | Rows | First UTC | Last UTC | Max gap (s) | Gaps |",
        "|---|---:|---|---|---:|---:|",
    ]
    streams = summary["run_timeline"]["streams"]
    for name in _CSV_STREAMS:
        stream = streams[name]
        max_gap = stream["interval_s"]["max"]
        lines.append(
            f"| {name} | {stream['row_count']} | {stream['first_utc']} | {stream['last_utc']} | "
            f"{max_gap} | {stream['gap_count']} |"
        )
    lines.extend(["", "## Control decisions", ""])
    decisions = summary["control"]["decisions"]
    if decisions:
        lines.extend(f"- {name}: {count}" for name, count in decisions.items())
    else:
        lines.append("- No control decisions recorded.")
    finalization = summary["logging_finalization"]
    lines.extend(
        [
            "",
            "## Logging finalization",
            "",
            f"- Run log complete: {finalization.get('run_log_complete')}",
            f"- Run log incomplete lines: {finalization.get('run_log_incomplete_lines')}",
            f"- Sensor sidecars: `{json.dumps(finalization.get('sensor_sidecars'), sort_keys=True)}`",
            "",
        ]
    )
    return "\n".join(lines)


def _output_directory(run_dir: Path, output_dir: Path | None) -> Path:
    run_dir = Path(run_dir).resolve()
    output = (
        run_dir.parent / f"{run_dir.name}_diagnostics"
        if output_dir is None
        else Path(output_dir).resolve()
    )
    if output == run_dir or run_dir in output.parents:
        raise DiagnosticError("Diagnostic outputs must be outside the source run folder.")
    output.mkdir(parents=True, exist_ok=True)
    return output


def write_diagnostic_summary(
    run_dir: Path,
    *,
    output_dir: Path | None = None,
    snapshot: bool = False,
) -> tuple[Path, Path]:
    summary = build_diagnostic_summary(run_dir, snapshot=snapshot)
    output = _output_directory(run_dir, output_dir)
    json_path = output / SUMMARY_JSON
    markdown_path = output / SUMMARY_MARKDOWN
    json_path.write_bytes(_json_bytes(summary))
    markdown_path.write_text(render_markdown_summary(summary), encoding="utf-8", newline="\n")
    return json_path, markdown_path


def _zip_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def write_diagnostic_bundle(
    run_dir: Path,
    *,
    output_dir: Path | None = None,
    snapshot: bool = False,
) -> Path:
    run_dir = Path(run_dir).resolve()
    files, _metadata, _metadata_bytes = discover_run_files(run_dir)
    summary = build_diagnostic_summary(run_dir, snapshot=snapshot)
    output = _output_directory(run_dir, output_dir)
    json_data = _json_bytes(summary)
    markdown_data = render_markdown_summary(summary).encode("utf-8")
    (output / SUMMARY_JSON).write_bytes(json_data)
    (output / SUMMARY_MARKDOWN).write_bytes(markdown_data)
    _manifest, content = _source_manifest(run_dir, files)
    entries = {
        SUMMARY_JSON: json_data,
        SUMMARY_MARKDOWN: markdown_data,
        **{f"run/{name}": data for name, data in content.items()},
    }
    correction_path = output / CORRECTION_JSON
    if correction_path.is_file():
        entries[CORRECTION_JSON] = correction_path.read_bytes()
    bundle_path = output / BUNDLE_ZIP
    with zipfile.ZipFile(bundle_path, "w") as archive:
        for name in sorted(entries):
            _zip_entry(archive, name, entries[name])
    return bundle_path


def write_identity_correction(
    run_dir: Path,
    *,
    corrected: Mapping[str, Any],
    reason: str,
    operator: str,
    output_dir: Path | None = None,
    snapshot: bool = False,
    timestamp_utc: str | None = None,
) -> Path:
    if not str(reason).strip() or not str(operator).strip():
        raise DiagnosticError("Corrections require a non-empty reason and operator.")
    run_dir = Path(run_dir).resolve()
    files, metadata, metadata_bytes = discover_run_files(run_dir)
    validate_run_state(metadata, snapshot=snapshot)
    output = _output_directory(run_dir, output_dir)
    path = output / CORRECTION_JSON
    manifest, _content = _source_manifest(run_dir, files)
    source_hashes = {
        role: details["sha256"]
        for role, details in manifest.items()
        if details["available"]
    }
    original = metadata.get("sample_identity") or {
        "sample_name": metadata.get("sample_name"),
        "composition": (metadata.get("name_fields") or {}).get("composition"),
        "microwire": (metadata.get("name_fields") or {}).get("microwire"),
        "specimen": (metadata.get("name_fields") or {}).get("specimen"),
        "diameter_mm": metadata.get("wire_diameter_mm"),
        "initial_length_mm": metadata.get("initial_length_mm"),
    }
    history: list[dict[str, Any]] = []
    effective = dict(original)
    if path.exists():
        existing, _existing_bytes = _read_json(path)
        if existing.get("final_metadata_sha256") != _sha256(metadata_bytes):
            raise DiagnosticError("Existing correction sidecar belongs to a different metadata revision.")
        history = list(existing.get("corrections") or [])
        effective.update(existing.get("effective_identity") or {})
    update = {str(key): value for key, value in corrected.items()}
    history.append(
        {
            "timestamp_utc": timestamp_utc or _utc_now(),
            "operator": str(operator).strip(),
            "reason": str(reason).strip(),
            "corrected": update,
        }
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_run_folder": run_dir.name,
        "final_metadata_path": files["metadata"].relative_to(run_dir).as_posix(),
        "final_metadata_sha256": _sha256(metadata_bytes),
        "source_sha256": dict(sorted(source_hashes.items())),
        "original_identity": original,
        "effective_identity": {**effective, **update},
        "corrections": history,
    }
    path.write_bytes(_json_bytes(payload))
    return path


def _parse_set_values(values: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise DiagnosticError(f"Correction must use key=value syntax: {item!r}")
        key, value = item.split("=", 1)
        if not key.strip():
            raise DiagnosticError(f"Correction key is empty: {item!r}")
        parsed[key.strip()] = value.strip()
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create read-only TMA run diagnostics.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("summarize", "Write JSON and Markdown summaries."),
        ("bundle", "Write summaries and a deterministic diagnostic ZIP."),
    ):
        child = subparsers.add_parser(command, help=help_text)
        child.add_argument("run_dir", type=Path)
        child.add_argument("--output-dir", type=Path)
        child.add_argument("--snapshot", action="store_true")
    correct = subparsers.add_parser("correct-identity", help="Write an audited correction sidecar.")
    correct.add_argument("run_dir", type=Path)
    correct.add_argument("--output-dir", type=Path)
    correct.add_argument("--snapshot", action="store_true")
    correct.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", required=True)
    correct.add_argument("--reason", required=True)
    correct.add_argument("--operator", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "summarize":
            outputs = write_diagnostic_summary(
                args.run_dir, output_dir=args.output_dir, snapshot=args.snapshot
            )
        elif args.command == "bundle":
            outputs = (
                write_diagnostic_bundle(
                    args.run_dir, output_dir=args.output_dir, snapshot=args.snapshot
                ),
            )
        else:
            outputs = (
                write_identity_correction(
                    args.run_dir,
                    corrected=_parse_set_values(args.set),
                    reason=args.reason,
                    operator=args.operator,
                    output_dir=args.output_dir,
                    snapshot=args.snapshot,
                ),
            )
    except DiagnosticError as exc:
        raise SystemExit(str(exc)) from exc
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
