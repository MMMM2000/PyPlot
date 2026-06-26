"""Post-run TMA control-trace diagnostics."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

GRAVITY_MS2 = 9.80665
DEFAULT_AUTO_TOLERANCE_LOAD_G = 0.005
CURRENT_SWEEP_PHASES = {"target_ramp", "current", "current_hold", "current_limit_unwind"}
LOAD_STRESS_BASES = {"load_g", "stress_mpa"}
ACCEPT_RESULTS = {"reached", "filtered_noise_band", "reversal_hold", "backlash_limited"}


def stress_mpa_from_load_g(load_g: float, diameter_mm: float) -> float | None:
    if diameter_mm <= 0.0:
        return None
    area_mm2 = (math.pi * diameter_mm * diameter_mm) / 4.0
    if area_mm2 <= 0.0:
        return None
    force_n = load_g * GRAVITY_MS2 / 1000.0
    return force_n / area_mm2


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _truthy_text(value: bool) -> str:
    return "true" if value else "false"


def _requested_acceptance_tolerance(
    basis: str,
    *,
    diameter_mm: float,
    load_tolerance_g: float,
) -> float:
    if basis == "load_g":
        return abs(float(load_tolerance_g))
    if basis == "stress_mpa":
        stress = stress_mpa_from_load_g(abs(float(load_tolerance_g)), diameter_mm)
        return 0.0 if stress is None else abs(float(stress))
    return abs(float(load_tolerance_g))


@dataclass(frozen=True)
class TraceReplaySummary:
    run_dir: Path
    diameter_mm: float
    load_tolerance_g: float
    decision_count: int
    old_accept_count: int
    split_accept_count: int
    step_floor_only_accept_count: int
    max_step_floor_only_error: float
    max_old_effective_tolerance: float
    max_motor_step_floor: float
    stop_detail: str
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir),
            "diameter_mm": self.diameter_mm,
            "load_tolerance_g": self.load_tolerance_g,
            "decision_count": self.decision_count,
            "old_accept_count": self.old_accept_count,
            "split_accept_count": self.split_accept_count,
            "step_floor_only_accept_count": self.step_floor_only_accept_count,
            "max_step_floor_only_error": self.max_step_floor_only_error,
            "max_old_effective_tolerance": self.max_old_effective_tolerance,
            "max_motor_step_floor": self.max_motor_step_floor,
            "stop_detail": self.stop_detail,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TraceReplayResult:
    summary: TraceReplaySummary
    rows: list[dict[str, Any]]


def analyze_control_trace(
    run_dir: Path | str,
    *,
    load_tolerance_g: float = DEFAULT_AUTO_TOLERANCE_LOAD_G,
) -> TraceReplayResult:
    run_path = Path(run_dir)
    trace_path = run_path / "control_trace.csv"
    metadata_path = run_path / "metadata.json"
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    if metadata_path.exists():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"invalid:metadata.json:{exc.__class__.__name__}")
        else:
            metadata = payload if isinstance(payload, dict) else {}
            if not isinstance(payload, dict):
                warnings.append("invalid:metadata.json:not_object")
    else:
        warnings.append("missing:metadata.json")
    diameter_mm = _float_or_none(metadata.get("wire_diameter_mm")) or 0.0
    stop = metadata.get("stop") if isinstance(metadata.get("stop"), dict) else {}
    stop_detail = str(stop.get("detail") or "")

    replay_rows: list[dict[str, Any]] = []
    if not trace_path.exists():
        warnings.append("missing:control_trace.csv")
    else:
        with trace_path.open(newline="", encoding="utf-8-sig") as handle:
            trace_reader = csv.DictReader(handle)
            if not trace_reader.fieldnames:
                warnings.append("empty:control_trace.csv")
            for row in trace_reader:
                phase = str(row.get("automation_phase") or "")
                basis = str(row.get("automation_basis") or "")
                if phase not in CURRENT_SWEEP_PHASES or basis not in LOAD_STRESS_BASES:
                    continue
                error_value = _float_or_none(row.get("error_value"))
                old_tolerance = _float_or_none(row.get("tolerance"))
                sensitivity = _float_or_none(row.get("sensitivity_per_mm"))
                motor_step = _float_or_none(row.get("motor_step_mm"))
                requested_tolerance = _requested_acceptance_tolerance(
                    basis,
                    diameter_mm=diameter_mm,
                    load_tolerance_g=load_tolerance_g,
                )
                abs_error = None if error_value is None else abs(float(error_value))
                old_effective = 0.0 if old_tolerance is None else abs(float(old_tolerance))
                motor_step_floor = 0.0
                if sensitivity is not None and motor_step is not None:
                    motor_step_floor = abs(float(sensitivity)) * abs(float(motor_step))
                decision = str(row.get("decision") or "")
                result = str(row.get("result") or "")
                old_accept = decision == "accept" and result in ACCEPT_RESULTS
                split_accept = abs_error is not None and abs_error <= requested_tolerance
                step_floor_only_accept = (
                    old_accept
                    and abs_error is not None
                    and not split_accept
                    and motor_step_floor > requested_tolerance
                    and abs_error <= max(old_effective, motor_step_floor)
                )
                replay_row = dict(row)
                replay_row.update(
                    {
                        "abs_error": "" if abs_error is None else f"{abs_error:.12g}",
                        "old_effective_tolerance": f"{old_effective:.12g}",
                        "motor_step_floor": f"{motor_step_floor:.12g}",
                        "replayed_acceptance_tolerance": f"{requested_tolerance:.12g}",
                        "old_accept": _truthy_text(old_accept),
                        "would_accept_after_split": _truthy_text(bool(split_accept)),
                        "step_floor_only_accept": _truthy_text(bool(step_floor_only_accept)),
                    }
                )
                replay_rows.append(replay_row)

    old_accept_count = sum(row["old_accept"] == "true" for row in replay_rows)
    split_accept_count = sum(row["would_accept_after_split"] == "true" for row in replay_rows)
    step_floor_only_rows = [row for row in replay_rows if row["step_floor_only_accept"] == "true"]
    max_step_floor_only_error = max(
        (_float_or_none(row["abs_error"]) or 0.0 for row in step_floor_only_rows),
        default=0.0,
    )
    max_old_effective_tolerance = max(
        (_float_or_none(row["old_effective_tolerance"]) or 0.0 for row in replay_rows),
        default=0.0,
    )
    max_motor_step_floor = max(
        (_float_or_none(row["motor_step_floor"]) or 0.0 for row in replay_rows),
        default=0.0,
    )
    summary = TraceReplaySummary(
        run_dir=run_path,
        diameter_mm=diameter_mm,
        load_tolerance_g=abs(float(load_tolerance_g)),
        decision_count=len(replay_rows),
        old_accept_count=old_accept_count,
        split_accept_count=split_accept_count,
        step_floor_only_accept_count=len(step_floor_only_rows),
        max_step_floor_only_error=max_step_floor_only_error,
        max_old_effective_tolerance=max_old_effective_tolerance,
        max_motor_step_floor=max_motor_step_floor,
        stop_detail=stop_detail,
        warnings=warnings,
    )
    return TraceReplayResult(summary=summary, rows=replay_rows)


def _write_replay_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "None"
    display_rows = [
        [str(row.get(column, "")) for column in columns]
        for row in rows
    ]
    widths = [len(column) for column in columns]
    for row in display_rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]

    def _line(values: list[str]) -> str:
        return "| " + " | ".join(value.ljust(width) for value, width in zip(values, widths)) + " |"

    return "\n".join(
        [_line(columns), "| " + " | ".join("-" * width for width in widths) + " |"]
        + [_line(row) for row in display_rows]
    )


def write_markdown_report(path: Path, result: TraceReplayResult) -> None:
    summary = result.summary
    step_rows = [row for row in result.rows if row.get("step_floor_only_accept") == "true"][:8]
    columns = [
        "elapsed_s",
        "automation_phase",
        "automation_target_value",
        "current_value",
        "error_value",
        "old_effective_tolerance",
        "motor_step_floor",
        "replayed_acceptance_tolerance",
    ]
    text = f"""# TMA Control Trace Replay

Run: `{summary.run_dir}`

Stop detail: {summary.stop_detail or "not recorded"}

## Summary

- Diameter: {summary.diameter_mm:.6g} mm
- Automatic load tolerance: {summary.load_tolerance_g:.6g} g
- Current-sweep load/stress decisions analyzed: {summary.decision_count}
- Old accept decisions: {summary.old_accept_count}
- Decisions that would be accepted by the split target band: {summary.split_accept_count}
- Old accepts that were only inside the motor-step floor: {summary.step_floor_only_accept_count}
- Largest motor-step-only accepted error: {summary.max_step_floor_only_error:.6g}
- Maximum old effective tolerance: {summary.max_old_effective_tolerance:.6g}
- Maximum motor-step floor: {summary.max_motor_step_floor:.6g}
- Warnings: {", ".join(summary.warnings) if summary.warnings else "none"}

## First Motor-Step-Only Accepts

{_markdown_table(step_rows, columns)}
"""
    path.write_text(text, encoding="utf-8")


def write_replay_outputs(result: TraceReplayResult, output_dir: Path | str) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "control_trace_replay.csv"
    json_path = out / "control_trace_replay_summary.json"
    md_path = out / "control_trace_replay.md"
    _write_replay_csv(csv_path, result.rows)
    json_path.write_text(json.dumps(result.summary.to_dict(), indent=2), encoding="utf-8")
    write_markdown_report(md_path, result)
    return {"csv": csv_path, "json": json_path, "markdown": md_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay TMA current-sweep control_trace.csv acceptance decisions.")
    parser.add_argument("run_dir", help="TMA run folder containing control_trace.csv and metadata.json.")
    parser.add_argument("--load-tolerance-g", type=float, default=DEFAULT_AUTO_TOLERANCE_LOAD_G)
    parser.add_argument("--out", type=Path, default=None, help="Output directory for replay CSV/JSON/Markdown files.")
    args = parser.parse_args(argv)

    result = analyze_control_trace(args.run_dir, load_tolerance_g=args.load_tolerance_g)
    print(json.dumps(result.summary.to_dict(), indent=2))
    if args.out is not None:
        paths = write_replay_outputs(result, args.out)
        for label, path in paths.items():
            print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
