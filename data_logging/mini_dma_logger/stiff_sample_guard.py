"""Offline TMA stiff-sample recovery guard."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from PyQt6 import QtWidgets

from . import mini_dma_logger as mini_dma


PLAN_KIND = "mini_dma_offline_stiff_sample_guard"
RESULT_KIND = "mini_dma_stiff_sample_guard_result"


def _safe_float(value: object, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number.")
    return result


def _safe_positive_float(value: object, *, field: str) -> float:
    result = _safe_float(value, field=field)
    if result <= 0.0:
        raise ValueError(f"{field} must be positive.")
    return result


def _safe_int(value: object, *, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer.") from exc
    return result


def _git_text(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _control_source(repo_root: Path | None = None) -> dict[str, Any]:
    cwd = repo_root or Path.cwd()
    return {
        "branch": _git_text(["branch", "--show-current"], cwd),
        "commit": _git_text(["rev-parse", "HEAD"], cwd),
        "control_logic_version": mini_dma.CONTROL_LOGIC_VERSION,
        "control_logic_profile": mini_dma.CONTROL_LOGIC_PROFILE,
    }


def _load_plan(path: Path | str) -> dict[str, Any]:
    plan_path = Path(path)
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read guard plan {plan_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Guard plan is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Guard plan must be a JSON object.")
    if payload.get("kind") != PLAN_KIND:
        raise ValueError(f"Guard plan kind must be {PLAN_KIND!r}.")
    return payload


def _check_results(
    *,
    results: list[dict[str, Any]],
    motor_step_mm: float,
    acceptance: Mapping[str, Any],
) -> list[dict[str, Any]]:
    by_id = {str(row.get("id")): row for row in results}
    soft_step = by_id.get("soft_reference", {}).get("dynamic_step_mm")
    stiff_10x_step = by_id.get("stiff_10x", {}).get("dynamic_step_mm")
    stiff_50x_step = by_id.get("stiff_50x", {}).get("dynamic_step_mm")
    stiff_10x_max = _safe_positive_float(
        acceptance.get("stiff_10x_max_step_mm", 0.005),
        field="acceptance.stiff_10x_max_step_mm",
    )
    stiff_10x_fraction = _safe_positive_float(
        acceptance.get("stiff_10x_max_fraction_of_soft_step", 0.2),
        field="acceptance.stiff_10x_max_fraction_of_soft_step",
    )
    stiff_50x_max = _safe_positive_float(
        acceptance.get("stiff_50x_max_step_mm", 0.002),
        field="acceptance.stiff_50x_max_step_mm",
    )
    checks: list[dict[str, Any]] = [
        {
            "id": "soft_reference_escapes_one_tic",
            "passed": soft_step is not None and float(soft_step) > motor_step_mm * 10.0,
            "observed": soft_step,
            "threshold": motor_step_mm * 10.0,
        },
        {
            "id": "stiff_10x_scales_below_configured_mm",
            "passed": stiff_10x_step is not None and float(stiff_10x_step) < stiff_10x_max,
            "observed": stiff_10x_step,
            "threshold": stiff_10x_max,
        },
        {
            "id": "stiff_10x_fraction_of_soft_step",
            "passed": (
                stiff_10x_step is not None
                and soft_step is not None
                and float(soft_step) > 0.0
                and float(stiff_10x_step) / float(soft_step) < stiff_10x_fraction
            ),
            "observed": None
            if stiff_10x_step is None or soft_step is None or float(soft_step) <= 0.0
            else float(stiff_10x_step) / float(soft_step),
            "threshold": stiff_10x_fraction,
        },
        {
            "id": "stiff_50x_tiny_or_declined",
            "passed": stiff_50x_step is None or float(stiff_50x_step) < stiff_50x_max,
            "observed": stiff_50x_step,
            "threshold": stiff_50x_max,
        },
    ]
    return checks


def _check_historical_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for row in results:
        case_id = str(row.get("id") or "")
        expected = str(row.get("expected") or "decline_dynamic_escape")
        dynamic_step_mm = row.get("dynamic_step_mm")
        max_step = row.get("max_dynamic_step_mm")
        if expected == "decline_dynamic_escape":
            passed = dynamic_step_mm is None
            threshold: object = None
        elif max_step is not None:
            threshold = _safe_positive_float(
                max_step,
                field=f"historical_oscillation_cases.{case_id}.max_dynamic_step_mm",
            )
            passed = dynamic_step_mm is None or float(dynamic_step_mm) <= float(threshold)
        else:
            threshold = None
            passed = False
        checks.append(
            {
                "id": f"historical_{case_id}",
                "passed": passed,
                "observed": dynamic_step_mm,
                "threshold": threshold,
                "expected": expected,
                "source_run": row.get("source_run"),
            }
        )
    return checks


def evaluate_guard_with_window(
    plan: Mapping[str, Any],
    window: mini_dma.MainWindow,
    *,
    plan_path: Path | str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    condition = plan.get("drift_condition")
    if not isinstance(condition, Mapping):
        raise ValueError("Guard plan needs drift_condition.")
    acceptance = plan.get("acceptance")
    if not isinstance(acceptance, Mapping):
        acceptance = {}
    sensitivity_cases = plan.get("sensitivity_cases")
    if not isinstance(sensitivity_cases, list) or not sensitivity_cases:
        raise ValueError("Guard plan needs at least one sensitivity case.")

    basis = str(condition.get("basis") or mini_dma.HSW_BASIS_STRESS_MPA)
    if basis not in {mini_dma.HSW_BASIS_STRESS_MPA, mini_dma.HSW_BASIS_LOAD_G}:
        raise ValueError("drift_condition.basis must be stress_mpa or load_g.")
    target_value = _safe_float(
        condition.get("target_mpa", condition.get("target_value", 50.0)),
        field="drift_condition.target",
    )
    previous_error = _safe_float(
        condition.get("previous_error_mpa", condition.get("previous_error", -10.0)),
        field="drift_condition.previous_error",
    )
    current_error = _safe_float(
        condition.get("current_error_mpa", condition.get("current_error", -17.0)),
        field="drift_condition.current_error",
    )
    tolerance = _safe_positive_float(
        condition.get("tolerance_mpa", condition.get("tolerance", 0.171)),
        field="drift_condition.tolerance",
    )
    noise = _safe_float(
        condition.get("filtered_noise_mpa", condition.get("filtered_noise", 0.1)),
        field="drift_condition.filtered_noise",
    )
    slope = _safe_float(
        condition.get("filtered_slope_mpa_per_s", condition.get("filtered_slope_per_s", 8.0)),
        field="drift_condition.filtered_slope",
    )
    sample_count = _safe_int(
        condition.get("filtered_sample_count", 7),
        field="drift_condition.filtered_sample_count",
    )

    window.spin_steps_per_mm.setValue(
        _safe_positive_float(plan.get("steps_per_mm", 800.0), field="steps_per_mm")
    )
    window._automation_active = True
    window._automation_name = mini_dma.CURRENT_SWEEP_STRESS
    window._set_automation_context(
        phase="current_hold",
        basis=basis,
        target_value=target_value,
        plateau_index=1,
    )
    seek_key = window._seek_error_key(basis, target_value)
    signal = mini_dma.ScaleControlSignal(
        value=target_value - current_error,
        latest_value=target_value - current_error,
        noise=noise,
        slope_per_s=slope,
        sample_count=sample_count,
        timestamp_s=time.time(),
    )
    motor_step_mm = window._motor_step_mm()
    results: list[dict[str, Any]] = []
    for index, case in enumerate(sensitivity_cases):
        if not isinstance(case, Mapping):
            raise ValueError(f"sensitivity_cases[{index}] must be an object.")
        case_id = str(case.get("id") or f"case_{index + 1}")
        sensitivity = _safe_positive_float(
            case.get("sensitivity_mpa_per_mm", case.get("sensitivity_per_mm")),
            field=f"sensitivity_cases[{index}].sensitivity_mpa_per_mm",
        )
        window._basis_sensitivity_per_mm = (  # type: ignore[method-assign]
            lambda *_args, _s=sensitivity, **_kwargs: _s
        )
        step = window._current_sweep_hold_drift_recovery_step_mm(
            basis,
            previous_error,
            current_error,
            tolerance,
            motor_step_mm,
            signal,
            seek_key=seek_key,
        )
        results.append(
            {
                "id": case_id,
                "sensitivity_mpa_per_mm": sensitivity,
                "dynamic_step_mm": None if step is None else float(step),
                "dynamic_step_motor_steps": None if step is None else float(step) / motor_step_mm,
                "stress_equivalent_mpa": None if step is None else float(step) * sensitivity,
            }
        )

    checks = _check_results(results=results, motor_step_mm=motor_step_mm, acceptance=acceptance)
    historical_results: list[dict[str, Any]] = []
    historical_cases = plan.get("historical_oscillation_cases")
    if isinstance(historical_cases, list):
        for index, case in enumerate(historical_cases):
            if not isinstance(case, Mapping):
                raise ValueError(f"historical_oscillation_cases[{index}] must be an object.")
            case_id = str(case.get("id") or f"historical_{index + 1}")
            historical_basis = str(case.get("basis") or basis)
            if historical_basis not in {mini_dma.HSW_BASIS_STRESS_MPA, mini_dma.HSW_BASIS_LOAD_G}:
                raise ValueError(
                    f"historical_oscillation_cases[{index}].basis must be stress_mpa or load_g."
                )
            historical_target = _safe_float(
                case.get("target_mpa", case.get("target_value", target_value)),
                field=f"historical_oscillation_cases[{index}].target",
            )
            historical_previous_error = _safe_float(
                case.get("previous_error_mpa", case.get("previous_error")),
                field=f"historical_oscillation_cases[{index}].previous_error",
            )
            historical_current_error = _safe_float(
                case.get("current_error_mpa", case.get("current_error")),
                field=f"historical_oscillation_cases[{index}].current_error",
            )
            historical_tolerance = _safe_positive_float(
                case.get("tolerance_mpa", case.get("tolerance", tolerance)),
                field=f"historical_oscillation_cases[{index}].tolerance",
            )
            historical_noise = _safe_float(
                case.get("filtered_noise_mpa", case.get("filtered_noise", noise)),
                field=f"historical_oscillation_cases[{index}].filtered_noise",
            )
            historical_slope = _safe_float(
                case.get("filtered_slope_mpa_per_s", case.get("filtered_slope_per_s", slope)),
                field=f"historical_oscillation_cases[{index}].filtered_slope",
            )
            historical_sample_count = _safe_int(
                case.get("filtered_sample_count", sample_count),
                field=f"historical_oscillation_cases[{index}].filtered_sample_count",
            )
            historical_sensitivity = _safe_positive_float(
                case.get("sensitivity_mpa_per_mm", case.get("sensitivity_per_mm")),
                field=f"historical_oscillation_cases[{index}].sensitivity_mpa_per_mm",
            )
            window._set_automation_context(
                phase="current_hold",
                basis=historical_basis,
                target_value=historical_target,
                plateau_index=1,
            )
            historical_seek_key = window._seek_error_key(historical_basis, historical_target)
            historical_signal = mini_dma.ScaleControlSignal(
                value=historical_target - historical_current_error,
                latest_value=historical_target - historical_current_error,
                noise=historical_noise,
                slope_per_s=historical_slope,
                sample_count=historical_sample_count,
                timestamp_s=time.time(),
            )
            window._basis_sensitivity_per_mm = (  # type: ignore[method-assign]
                lambda *_args, _s=historical_sensitivity, **_kwargs: _s
            )
            historical_step = window._current_sweep_hold_drift_recovery_step_mm(
                historical_basis,
                historical_previous_error,
                historical_current_error,
                historical_tolerance,
                motor_step_mm,
                historical_signal,
                seek_key=historical_seek_key,
            )
            historical_results.append(
                {
                    "id": case_id,
                    "source_run": case.get("source_run"),
                    "sample": case.get("sample"),
                    "expected": case.get("expected", "decline_dynamic_escape"),
                    "max_dynamic_step_mm": case.get("max_dynamic_step_mm"),
                    "basis": historical_basis,
                    "target_value": historical_target,
                    "previous_error": historical_previous_error,
                    "current_error": historical_current_error,
                    "sensitivity_mpa_per_mm": historical_sensitivity,
                    "dynamic_step_mm": None if historical_step is None else float(historical_step),
                    "dynamic_step_motor_steps": None
                    if historical_step is None
                    else float(historical_step) / motor_step_mm,
                    "stress_equivalent_mpa": None
                    if historical_step is None
                    else float(historical_step) * historical_sensitivity,
                }
            )
    historical_checks = _check_historical_results(historical_results)
    all_checks = [*checks, *historical_checks]
    return {
        "schema_version": 1,
        "kind": RESULT_KIND,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plan_path": None if plan_path is None else str(plan_path),
        "control_source": _control_source(repo_root),
        "motor_step_mm": motor_step_mm,
        "drift_condition": dict(condition),
        "results": results,
        "historical_oscillation_results": historical_results,
        "checks": checks,
        "historical_checks": historical_checks,
        "passed": all(bool(check["passed"]) for check in all_checks),
        "notes": [
            "This is an offline controller guard, not a live stiff-wire validation.",
            "It exercises the same current-hold drift recovery helper used by the GUI controller on the current branch.",
        ],
    }


def write_markdown_report(path: Path | str, result: Mapping[str, Any]) -> None:
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    historical_rows = (
        result.get("historical_oscillation_results")
        if isinstance(result.get("historical_oscillation_results"), list)
        else []
    )
    checks = result.get("checks") if isinstance(result.get("checks"), list) else []
    historical_checks = (
        result.get("historical_checks") if isinstance(result.get("historical_checks"), list) else []
    )
    control = result.get("control_source") if isinstance(result.get("control_source"), Mapping) else {}
    lines = [
        "# TMA Stiff-Sample Guard",
        "",
        (
            f"Control: `{control.get('branch') or ''}` "
            f"`{str(control.get('commit') or '')[:12]}` / "
            f"logic `{control.get('control_logic_version') or ''}`"
        ),
        f"Passed: `{bool(result.get('passed'))}`",
        "",
        "| Case | Sensitivity (MPa/mm) | Step (mm) | Motor steps | Stress eq. (MPa) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        step = row.get("dynamic_step_mm")
        motor_steps = row.get("dynamic_step_motor_steps")
        stress = row.get("stress_equivalent_mpa")
        lines.append(
            f"| {row.get('id', '')} | {float(row.get('sensitivity_mpa_per_mm') or 0.0):.0f} | "
            f"{'' if step is None else f'{float(step):.6g}'} | "
            f"{'' if motor_steps is None else f'{float(motor_steps):.3g}'} | "
            f"{'' if stress is None else f'{float(stress):.6g}'} |"
        )
    if historical_rows:
        lines.extend(
            [
                "",
                "## Historical Oscillation Cases",
                "",
                "| Case | Source | Previous error | Current error | Dynamic step (mm) |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for row in historical_rows:
            if not isinstance(row, Mapping):
                continue
            step = row.get("dynamic_step_mm")
            lines.append(
                f"| {row.get('id', '')} | {row.get('sample') or row.get('source_run') or ''} | "
                f"{float(row.get('previous_error') or 0.0):.6g} | "
                f"{float(row.get('current_error') or 0.0):.6g} | "
                f"{'' if step is None else f'{float(step):.6g}'} |"
            )
    lines.extend(["", "## Checks", ""])
    for check in [*checks, *historical_checks]:
        if not isinstance(check, Mapping):
            continue
        lines.append(
            f"- {check.get('id')}: {'PASS' if check.get('passed') else 'FAIL'} "
            f"(observed={check.get('observed')}, threshold={check.get('threshold')})"
        )
    lines.extend(["", "This is offline evidence only; it does not replace a real stiff-wire hardware run.", ""])
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def run_guard(
    plan_path: Path | str,
    *,
    out_json: Path | str | None = None,
    out_markdown: Path | str | None = None,
    repo_root: Path | None = None,
    app_factory: Callable[[], QtWidgets.QApplication] | None = None,
    window_factory: Callable[..., mini_dma.MainWindow] | None = None,
) -> dict[str, Any]:
    path = Path(plan_path)
    plan = _load_plan(path)
    app = app_factory() if app_factory is not None else (QtWidgets.QApplication.instance() or QtWidgets.QApplication([]))
    factory = window_factory or mini_dma.MainWindow
    window = factory(log_dir=str(path.parent), persist_settings=False)
    try:
        result = evaluate_guard_with_window(plan, window, plan_path=path, repo_root=repo_root)
    finally:
        close = getattr(window, "close", None)
        if callable(close):
            close()
        delete_later = getattr(window, "deleteLater", None)
        if callable(delete_later):
            delete_later()
        QtWidgets.QApplication.sendPostedEvents(None, 0)
        app.processEvents()
    if out_json is not None:
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    if out_markdown is not None:
        Path(out_markdown).parent.mkdir(parents=True, exist_ok=True)
        write_markdown_report(out_markdown, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an offline TMA stiff-sample current-hold guard.")
    parser.add_argument("plan", help="Path to a mini_dma_offline_stiff_sample_guard JSON plan.")
    parser.add_argument("--out-json", type=Path, default=None, help="Write machine-readable guard result.")
    parser.add_argument("--out-md", type=Path, default=None, help="Write a Markdown guard report.")
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Repository root for source-control metadata.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_guard(
        args.plan,
        out_json=args.out_json,
        out_markdown=args.out_md,
        repo_root=args.repo_root,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
