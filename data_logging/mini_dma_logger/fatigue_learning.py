"""Offline Mini DMA fatigue/current-sweep learning summaries.

This module deliberately does not feed values back into live control. It turns
saved run folders into reviewable priors for future repeated iso-stress sweeps.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from data_logging.mini_dma_logger.run_quality import RunQuality, analyze_run_quality


ANALYZER_VERSION = "2026-06-17.1"
CURRENT_PHASES = {"current", "current_hold", "current_limit_unwind"}
STRESS_SWEEP_MODES = {"current_sweep_stress", "current_sweep_fatigue", "current_sweep"}
STRESS_BASIS_VALUES = {"stress_mpa", "stress"}
RUNS_CSV_COLUMNS = [
    "run_name",
    "run_path",
    "group_key",
    "included",
    "exclusion_reasons",
    "sample_name",
    "composition",
    "microwire",
    "initial_length_mm",
    "wire_diameter_mm",
    "recipe_mode",
    "target_stress_mpa",
    "current_start_mA",
    "current_end_mA",
    "current_ramp_rate_mA_s",
    "loop_count_estimate",
    "total_elapsed_s",
    "stress_error_p95_abs_mpa",
    "stress_error_rms_mpa",
    "current_hold_recovery_time_median_s",
    "strain_at_max_current_pct",
    "strain_span_pct",
    "residual_strain_pct",
    "transformation_current_mA",
    "transformation_metric",
    "stop_reason",
    "stop_classification",
    "control_logic_fingerprint",
]


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(clean) if clean else None


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.mean(clean) if clean else None


def _percentile(values: Iterable[float | None], fraction: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = max(0.0, min(1.0, fraction)) * (len(clean) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return clean[lower]
    weight = position - lower
    return clean[lower] * (1.0 - weight) + clean[upper] * weight


def _iqr(values: Iterable[float | None]) -> float | None:
    q25 = _percentile(values, 0.25)
    q75 = _percentile(values, 0.75)
    if q25 is None or q75 is None:
        return None
    return q75 - q25


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _join(values: Iterable[str]) -> str:
    return ";".join(value for value in values if value)


def _current_value(row: Mapping[str, str]) -> float | None:
    measured = _float_or_none(row.get("current_measured_mA"))
    if measured is not None:
        return measured
    return _float_or_none(row.get("current_set_mA"))


def _current_phase_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if str(row.get("automation_phase") or "") in CURRENT_PHASES]


def _phase_target_stress(rows: list[dict[str, str]]) -> float | None:
    return _median(
        _float_or_none(row.get("automation_target_value"))
        for row in _current_phase_rows(rows)
        if str(row.get("automation_basis") or "stress_mpa").lower() in {"", *STRESS_BASIS_VALUES}
    )


def _estimate_current_ramp_rate(rows: list[dict[str, str]]) -> float | None:
    rates: list[float] = []
    for previous, current in zip(rows[:-1], rows[1:]):
        t0 = _float_or_none(previous.get("elapsed_s"))
        t1 = _float_or_none(current.get("elapsed_s"))
        i0 = _current_value(previous)
        i1 = _current_value(current)
        if t0 is None or t1 is None or i0 is None or i1 is None:
            continue
        dt = t1 - t0
        di = i1 - i0
        if dt <= 0.0 or abs(di) < 0.05:
            continue
        rates.append(abs(di) / dt)
    return _median(rates)


def _positive_segments(rows: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    segments: list[list[dict[str, str]]] = []
    active: list[dict[str, str]] = []
    previous_current: float | None = None
    for row in rows:
        current = _current_value(row)
        if current is None:
            continue
        if previous_current is not None and current < previous_current - 0.05:
            if len(active) >= 3:
                segments.append(active)
            active = []
        active.append(row)
        previous_current = current
    if len(active) >= 3:
        segments.append(active)
    return segments


def _estimate_transformation_current(rows: list[dict[str, str]]) -> tuple[float | None, str]:
    phase_rows = _current_phase_rows(rows)
    candidates: list[tuple[float, float, str]] = []
    for segment in _positive_segments(phase_rows):
        if len(segment) < 6:
            continue
        for previous, current in zip(segment[:-1], segment[1:]):
            i0 = _current_value(previous)
            i1 = _current_value(current)
            if i0 is None or i1 is None:
                continue
            di = i1 - i0
            if di < 0.10:
                continue
            midpoint = (i0 + i1) / 2.0
            r0 = _float_or_none(previous.get("resistance_ohm"))
            r1 = _float_or_none(current.get("resistance_ohm"))
            if r0 is not None and r1 is not None:
                candidates.append((abs((r1 - r0) / di), midpoint, "resistance_slope"))
                continue
            s0 = _float_or_none(previous.get("strain_pct"))
            s1 = _float_or_none(current.get("strain_pct"))
            if s0 is not None and s1 is not None:
                candidates.append((abs((s1 - s0) / di), midpoint, "strain_slope"))
    if not candidates:
        return None, ""
    candidates.sort(reverse=True, key=lambda item: item[0])
    top = candidates[: min(3, len(candidates))]
    return _median(current_mA for _score, current_mA, _metric in top), top[0][2]


def _strain_at_max_current(rows: list[dict[str, str]]) -> tuple[float | None, float | None, float | None]:
    values: list[tuple[float, float]] = []
    for row in _current_phase_rows(rows):
        current = _current_value(row)
        strain = _float_or_none(row.get("strain_pct"))
        if current is None or strain is None:
            continue
        values.append((current, strain))
    if not values:
        return None, None, None
    max_current = max(current for current, _strain in values)
    near_max = [strain for current, strain in values if abs(current - max_current) <= 0.5]
    strains = [strain for _current, strain in values]
    return _median(near_max), max(strains) - min(strains), strains[-1] - strains[0]


def _metadata_current_sweep(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(metadata.get("controlled_current_sweep"))


def _metadata_current_values(
    metadata: Mapping[str, Any],
    rows: list[dict[str, str]],
) -> tuple[float | None, float | None, float | None]:
    sweep = _metadata_current_sweep(metadata)
    phase_rows = _current_phase_rows(rows)
    current_values = [_current_value(row) for row in phase_rows]
    start = _float_or_none(sweep.get("current_start_mA"))
    end = _float_or_none(sweep.get("current_end_mA"))
    rate = _float_or_none(sweep.get("current_ramp_rate_mA_s"))
    if start is None:
        start = min((value for value in current_values if value is not None), default=None)
    if end is None:
        end = max((value for value in current_values if value is not None), default=None)
    if rate is None:
        rate = _estimate_current_ramp_rate(phase_rows)
    return start, end, rate


def _metadata_target_stress(metadata: Mapping[str, Any], rows: list[dict[str, str]]) -> float | None:
    sweep = _metadata_current_sweep(metadata)
    mode = str(metadata.get("recipe_mode") or sweep.get("mode") or "")
    target_start = _float_or_none(sweep.get("target_start"))
    target_end = _float_or_none(sweep.get("target_end"))
    if mode == "current_sweep_fatigue" and target_start is not None:
        return target_start
    if target_start is not None and (target_end is None or math.isclose(target_start, target_end, abs_tol=1e-9)):
        return target_start
    return _phase_target_stress(rows)


def _stress_sweep_exclusions(metadata: Mapping[str, Any]) -> list[str]:
    mode = str(metadata.get("recipe_mode") or "")
    sweep = _metadata_current_sweep(metadata)
    basis = str(sweep.get("basis") or "").lower()
    exclusions: list[str] = []
    if mode not in STRESS_SWEEP_MODES:
        exclusions.append(f"recipe_mode:{mode or 'missing'}")
    if basis and basis not in STRESS_BASIS_VALUES:
        exclusions.append(f"basis:{basis}")
    return exclusions


def _group_value(value: float | None, digits: int) -> str:
    if value is None:
        return "unknown"
    return str(round(value, digits))


@dataclass(frozen=True)
class FatigueRunFeatures:
    schema_version: int
    analyzer_version: str
    run_dir: str
    run_name: str
    group_key: str
    included: bool
    exclusion_reasons: list[str]
    sample_name: str
    composition: str
    microwire: str
    initial_length_mm: float | None
    wire_diameter_mm: float | None
    recipe_mode: str
    target_stress_mpa: float | None
    current_start_mA: float | None
    current_end_mA: float | None
    current_ramp_rate_mA_s: float | None
    loop_count_estimate: int
    total_elapsed_s: float | None
    stress_error_p95_abs_mpa: float | None
    stress_error_rms_mpa: float | None
    current_hold_recovery_time_median_s: float | None
    strain_at_max_current_pct: float | None
    strain_span_pct: float | None
    residual_strain_pct: float | None
    transformation_current_mA: float | None
    transformation_metric: str
    stop_reason: str
    stop_classification: str
    control_logic_version: str
    control_logic_fingerprint: str
    biggest_problems: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_run_features(
    run_dir: Path | str,
    *,
    min_measurement_rows: int = 100,
    min_current_loops: int = 1,
) -> FatigueRunFeatures:
    run_path = Path(run_dir)
    metadata = _read_json(run_path / "metadata.json")
    rows = _read_csv_rows(run_path / "measurement.csv")
    quality: RunQuality = analyze_run_quality(
        run_path,
        min_measurement_rows=min_measurement_rows,
        min_current_loops=min_current_loops,
    )
    name_fields = _mapping(metadata.get("name_fields"))
    current_start, current_end, current_ramp_rate = _metadata_current_values(metadata, rows)
    target_stress = _metadata_target_stress(metadata, rows)
    transformation_current, transformation_metric = _estimate_transformation_current(rows)
    strain_at_max, strain_span, residual_strain = _strain_at_max_current(rows)

    exclusion_reasons = list(quality.exclusion_reasons)
    exclusion_reasons.extend(_stress_sweep_exclusions(metadata))
    if target_stress is None:
        exclusion_reasons.append("missing_target_stress")
    if current_end is None:
        exclusion_reasons.append("missing_current_end")
    included = quality.include_in_optimization_summary and not exclusion_reasons
    group_key = (
        f"{str(name_fields.get('composition') or '').strip() or 'unknown'}|"
        f"{str(name_fields.get('microwire') or '').strip() or 'unknown'}|"
        f"d={_group_value(_float_or_none(metadata.get('wire_diameter_mm')), 5)}|"
        f"target={_group_value(target_stress, 2)}MPa|"
        f"current={_group_value(current_start, 2)}-{_group_value(current_end, 2)}mA"
    )
    return FatigueRunFeatures(
        schema_version=1,
        analyzer_version=ANALYZER_VERSION,
        run_dir=str(run_path),
        run_name=run_path.name,
        group_key=group_key,
        included=included,
        exclusion_reasons=exclusion_reasons,
        sample_name=str(metadata.get("sample_name") or ""),
        composition=str(name_fields.get("composition") or ""),
        microwire=str(name_fields.get("microwire") or ""),
        initial_length_mm=_float_or_none(metadata.get("initial_length_mm")),
        wire_diameter_mm=_float_or_none(metadata.get("wire_diameter_mm")),
        recipe_mode=str(metadata.get("recipe_mode") or ""),
        target_stress_mpa=_round(target_stress),
        current_start_mA=_round(current_start),
        current_end_mA=_round(current_end),
        current_ramp_rate_mA_s=_round(current_ramp_rate),
        loop_count_estimate=quality.current_loop_count_estimate,
        total_elapsed_s=_round(quality.total_elapsed_s, 3),
        stress_error_p95_abs_mpa=_round(quality.stress_error_p95_abs_mpa),
        stress_error_rms_mpa=_round(quality.stress_error_rms_mpa),
        current_hold_recovery_time_median_s=_round(quality.current_hold_recovery_time_median_s, 3),
        strain_at_max_current_pct=_round(strain_at_max),
        strain_span_pct=_round(strain_span),
        residual_strain_pct=_round(residual_strain),
        transformation_current_mA=_round(transformation_current),
        transformation_metric=transformation_metric,
        stop_reason=quality.stop_reason,
        stop_classification=quality.stop_classification,
        control_logic_version=quality.control_logic_version,
        control_logic_fingerprint=quality.control_logic_fingerprint,
        biggest_problems=list(quality.biggest_problems),
    )


@dataclass(frozen=True)
class FatigueGroupSummary:
    group_key: str
    run_count: int
    included_count: int
    excluded_count: int
    sample_names: list[str]
    composition: str
    microwire: str
    wire_diameter_mm_median: float | None
    target_stress_mpa_median: float | None
    current_start_mA_median: float | None
    current_end_mA_median: float | None
    current_ramp_rate_mA_s_median: float | None
    stress_error_p95_abs_mpa_median: float | None
    stress_error_rms_mpa_median: float | None
    current_hold_recovery_time_median_s: float | None
    strain_at_max_current_pct_median: float | None
    residual_strain_pct_median: float | None
    transformation_current_mA_median: float | None
    transformation_current_iqr_mA: float | None
    transformation_current_drift_mA: float | None
    confidence: str
    suggested_priors: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _confidence(included: list[FatigueRunFeatures], transform_count: int) -> str:
    if len(included) >= 10 and transform_count >= 5:
        return "moderate"
    if len(included) >= 3 and transform_count >= 2:
        return "low"
    if len(included) >= 1:
        return "single_run"
    return "none"


def summarize_group(features: Sequence[FatigueRunFeatures]) -> FatigueGroupSummary:
    included = [feature for feature in features if feature.included]
    basis = included or list(features)
    transform_values = [feature.transformation_current_mA for feature in included if feature.transformation_current_mA is not None]
    transform_iqr = _iqr(transform_values)
    transformation_window = None
    if transform_values:
        transformation_window = max(2.0, (transform_iqr or 0.0) * 1.5)
    warnings: list[str] = []
    if len(included) < len(features):
        warnings.append(f"{len(features) - len(included)} excluded run(s)")
    stop_reasons = sorted({feature.stop_reason for feature in included if feature.stop_reason not in {"recipe_completed", "completed"}})
    if stop_reasons:
        warnings.append("non-completed useful runs: " + ", ".join(stop_reasons))
    if not transform_values:
        warnings.append("no transformation-current estimate")
    drift = None
    if len(transform_values) >= 2:
        drift = transform_values[-1] - transform_values[0]
    confidence = _confidence(included, len(transform_values))
    target = _median(feature.target_stress_mpa for feature in basis)
    current_start = _median(feature.current_start_mA for feature in basis)
    current_end = _median(feature.current_end_mA for feature in basis)
    current_ramp = _median(feature.current_ramp_rate_mA_s for feature in basis)
    transform_median = _median(transform_values)
    suggested_priors = {
        "review_only": True,
        "confidence": confidence,
        "target_stress_mpa": _round(target),
        "current_start_mA": _round(current_start),
        "current_end_mA": _round(current_end),
        "current_ramp_rate_mA_s": _round(current_ramp),
        "expected_transformation_current_mA": _round(transform_median),
        "expected_transformation_window_mA": _round(transformation_window),
        "baseline_stress_error_p95_abs_mpa": _round(_median(feature.stress_error_p95_abs_mpa for feature in included)),
        "baseline_current_hold_recovery_s": _round(
            _median(feature.current_hold_recovery_time_median_s for feature in included), 3
        ),
        "expected_strain_at_max_current_pct": _round(
            _median(feature.strain_at_max_current_pct for feature in included)
        ),
        "apply_to_live_control": False,
    }
    return FatigueGroupSummary(
        group_key=features[0].group_key if features else "",
        run_count=len(features),
        included_count=len(included),
        excluded_count=len(features) - len(included),
        sample_names=sorted({feature.sample_name for feature in basis if feature.sample_name}),
        composition=str(basis[0].composition if basis else ""),
        microwire=str(basis[0].microwire if basis else ""),
        wire_diameter_mm_median=_round(_median(feature.wire_diameter_mm for feature in basis), 6),
        target_stress_mpa_median=_round(target),
        current_start_mA_median=_round(current_start),
        current_end_mA_median=_round(current_end),
        current_ramp_rate_mA_s_median=_round(current_ramp),
        stress_error_p95_abs_mpa_median=_round(_median(feature.stress_error_p95_abs_mpa for feature in included)),
        stress_error_rms_mpa_median=_round(_median(feature.stress_error_rms_mpa for feature in included)),
        current_hold_recovery_time_median_s=_round(
            _median(feature.current_hold_recovery_time_median_s for feature in included), 3
        ),
        strain_at_max_current_pct_median=_round(_median(feature.strain_at_max_current_pct for feature in included)),
        residual_strain_pct_median=_round(_median(feature.residual_strain_pct for feature in included)),
        transformation_current_mA_median=_round(transform_median),
        transformation_current_iqr_mA=_round(transform_iqr),
        transformation_current_drift_mA=_round(drift),
        confidence=confidence,
        suggested_priors=suggested_priors,
        warnings=warnings,
    )


def summarize_features(features: Sequence[FatigueRunFeatures]) -> list[FatigueGroupSummary]:
    grouped: dict[str, list[FatigueRunFeatures]] = {}
    for feature in features:
        grouped.setdefault(feature.group_key, []).append(feature)
    return [summarize_group(group) for _key, group in sorted(grouped.items())]


def discover_run_dirs(paths: Iterable[Path | str], *, recursive: bool = False) -> list[Path]:
    candidates: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_file() and path.name in {"metadata.json", "measurement.csv"}:
            candidates.append(path.parent)
        elif path.is_dir() and ((path / "metadata.json").exists() or (path / "measurement.csv").exists()):
            candidates.append(path)
        elif path.is_dir() and recursive:
            candidates.extend(metadata.parent for metadata in sorted(path.rglob("metadata.json")))
        elif path.is_dir():
            candidates.extend(
                child
                for child in sorted(path.iterdir(), key=lambda item: item.name.lower())
                if child.is_dir() and ((child / "metadata.json").exists() or (child / "measurement.csv").exists())
            )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).lower()
        except OSError:
            key = str(candidate.absolute()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def build_report(
    run_features: Sequence[FatigueRunFeatures],
    groups: Sequence[FatigueGroupSummary],
    *,
    title: str = "Mini DMA Fatigue Learning Report",
) -> str:
    analyzed_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# {title}",
        "",
        f"- Analyzer: `{ANALYZER_VERSION}`",
        f"- Analyzed UTC: `{analyzed_utc}`",
        f"- Run folders scanned: `{len(run_features)}`",
        f"- Included runs: `{sum(1 for feature in run_features if feature.included)}`",
        f"- Groups: `{len(groups)}`",
        "",
        "The suggested priors are review-only. They are not applied to live Mini DMA control.",
        "",
    ]
    for group in groups:
        lines.extend(
            [
                f"## {group.group_key}",
                "",
                f"- Included/excluded: `{group.included_count}` / `{group.excluded_count}`",
                f"- Confidence: `{group.confidence}`",
                f"- Target/current: `{group.target_stress_mpa_median} MPa`, "
                f"`{group.current_start_mA_median}-{group.current_end_mA_median} mA`, "
                f"`{group.current_ramp_rate_mA_s_median} mA/s`",
                f"- Stress error p95/RMS: `{group.stress_error_p95_abs_mpa_median}` / "
                f"`{group.stress_error_rms_mpa_median}` MPa",
                f"- Transformation current median/IQR/drift: `{group.transformation_current_mA_median}` / "
                f"`{group.transformation_current_iqr_mA}` / `{group.transformation_current_drift_mA}` mA",
                f"- Suggested priors: `{json.dumps(group.suggested_priors, sort_keys=True)}`",
            ]
        )
        if group.warnings:
            lines.append(f"- Warnings: `{_join(group.warnings)}`")
        lines.append("")
    excluded = [feature for feature in run_features if not feature.included]
    if excluded:
        lines.extend(["## Excluded Runs", ""])
        for feature in excluded:
            lines.append(f"- `{feature.run_name}`: `{_join(feature.exclusion_reasons)}`")
        lines.append("")
    return "\n".join(lines)


def analyze_fatigue_runs(
    paths: Iterable[Path | str],
    *,
    recursive: bool = False,
    min_measurement_rows: int = 100,
    min_current_loops: int = 1,
) -> dict[str, Any]:
    run_dirs = discover_run_dirs(paths, recursive=recursive)
    features = [
        extract_run_features(
            run_dir,
            min_measurement_rows=min_measurement_rows,
            min_current_loops=min_current_loops,
        )
        for run_dir in run_dirs
    ]
    groups = summarize_features(features)
    return {
        "schema_version": 1,
        "analyzer_version": ANALYZER_VERSION,
        "analyzed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_count": len(features),
        "included_run_count": sum(1 for feature in features if feature.included),
        "groups": [group.to_dict() for group in groups],
        "runs": [feature.to_dict() for feature in features],
    }


def write_outputs(payload: Mapping[str, Any], output_dir: Path | str) -> dict[str, str]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "fatigue_learning_summary.json"
    runs_path = out_dir / "fatigue_learning_runs.csv"
    report_path = out_dir / "fatigue_learning_report.md"
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    runs = list(payload.get("runs") if isinstance(payload.get("runs"), list) else [])
    with runs_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUNS_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for run in runs:
            row = dict(run) if isinstance(run, Mapping) else {}
            row["exclusion_reasons"] = _join(str(item) for item in row.get("exclusion_reasons", []))
            writer.writerow(row)
    groups = [
        FatigueGroupSummary(**group)
        for group in payload.get("groups", [])
        if isinstance(group, Mapping)
    ]
    features = [
        FatigueRunFeatures(**run)
        for run in payload.get("runs", [])
        if isinstance(run, Mapping)
    ]
    report_path.write_text(build_report(features, groups), encoding="utf-8")
    return {
        "summary_json": str(summary_path),
        "runs_csv": str(runs_path),
        "report_md": str(report_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Learn review-only Mini DMA fatigue/current-sweep priors from saved run folders."
    )
    parser.add_argument("paths", nargs="+", help="Run folders, metadata/measurement files, or parent folders.")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan parent folders for metadata.json files. Default scans direct child runs only.",
    )
    parser.add_argument(
        "--min-measurement-rows",
        type=int,
        default=100,
        help="Minimum measurement rows required before a run can be included.",
    )
    parser.add_argument(
        "--min-current-loops",
        type=int,
        default=1,
        help="Minimum current-loop reversals required before a run can be included.",
    )
    parser.add_argument("--out-dir", help="Directory receiving JSON, CSV, and Markdown outputs.")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = analyze_fatigue_runs(
        args.paths,
        recursive=args.recursive,
        min_measurement_rows=args.min_measurement_rows,
        min_current_loops=args.min_current_loops,
    )
    output_paths: dict[str, str] = {}
    if args.out_dir:
        output_paths = write_outputs(payload, args.out_dir)
    if args.json or not args.out_dir:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(
            f"Analyzed {payload['run_count']} Mini DMA run(s), "
            f"included {payload['included_run_count']}, wrote {output_paths['report_md']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
