from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PyQt6 import QtWidgets

from plotting.shared.utils import ensure_app_theme

from .trace_replay import analyze_control_trace, write_replay_outputs


MINI_DMA_BENCH_CONFIRMATION = "MINI_DMA_BENCH_ARMED"
PLAN_KIND = "mini_dma_bench_sequence"
PLAN_SCHEMA_VERSION = 1
DEFAULT_MAX_RUN_DURATION_S = 3600.0


class MiniDmaBenchAutomationError(RuntimeError):
    """Raised when a Mini DMA bench automation plan is invalid or cannot run."""


@dataclass(frozen=True)
class MiniDmaBenchRun:
    name: str
    recipe_path: Path
    repeat_index: int = 1
    max_run_duration_s: float = DEFAULT_MAX_RUN_DURATION_S
    starting_length_mm: float | None = None
    preload_length_mm: float | None = None


@dataclass(frozen=True)
class MiniDmaBenchGuardrails:
    max_stress_mpa: float | None = None
    recovery_stress_mpa: float | None = None
    wire_break_stops_plan: bool = True
    allow_mechanical_slack_takeup: bool = False
    mechanical_slack_max_seek_mm: float | None = None


@dataclass(frozen=True)
class MiniDmaBenchPlan:
    path: Path
    execute: bool
    log_dir: Path | None
    summary_path: Path | None
    max_total_duration_s: float | None
    guardrails: MiniDmaBenchGuardrails
    runs: tuple[MiniDmaBenchRun, ...]


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MiniDmaBenchAutomationError(f"Could not read Mini DMA bench plan {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MiniDmaBenchAutomationError(f"Mini DMA bench plan is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MiniDmaBenchAutomationError("Mini DMA bench plan must contain a JSON object.")
    return payload


def _as_float(value: object, *, field: str, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MiniDmaBenchAutomationError(f"Mini DMA bench plan field '{field}' must be numeric.") from exc
    if minimum is not None and result < minimum:
        raise MiniDmaBenchAutomationError(f"Mini DMA bench plan field '{field}' must be at least {minimum}.")
    return result


def _optional_float(mapping: Mapping[str, Any], key: str) -> float | None:
    if key not in mapping or mapping[key] is None:
        return None
    return _as_float(mapping[key], field=key)


def _resolve_plan_path(base: Path, value: object, *, field: str, must_exist: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise MiniDmaBenchAutomationError(f"Mini DMA bench plan field '{field}' must be a non-empty path string.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    if must_exist and not path.exists():
        raise MiniDmaBenchAutomationError(f"Mini DMA bench plan path does not exist for '{field}': {path}")
    return path


def _validate_arming(payload: Mapping[str, Any]) -> None:
    if not bool(payload.get("armed", False)):
        raise MiniDmaBenchAutomationError("Mini DMA bench execution requires 'armed': true.")
    if payload.get("operator_confirmation") != MINI_DMA_BENCH_CONFIRMATION:
        raise MiniDmaBenchAutomationError(
            "Mini DMA bench execution requires operator_confirmation "
            f"to equal {MINI_DMA_BENCH_CONFIRMATION!r}."
        )


def load_mini_dma_bench_plan(path: str | Path) -> MiniDmaBenchPlan:
    plan_path = Path(path).expanduser().resolve()
    payload = _load_json_object(plan_path)
    if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise MiniDmaBenchAutomationError("Mini DMA bench plan schema_version must be 1.")
    if payload.get("kind") != PLAN_KIND:
        raise MiniDmaBenchAutomationError(f"Mini DMA bench plan kind must be {PLAN_KIND!r}.")

    execute = bool(payload.get("execute", False))
    if execute:
        _validate_arming(payload)

    base = plan_path.parent
    log_dir = None
    if payload.get("log_dir") is not None:
        log_dir = _resolve_plan_path(base, payload["log_dir"], field="log_dir", must_exist=False)
    summary_path = None
    if payload.get("summary_path") is not None:
        summary_path = _resolve_plan_path(base, payload["summary_path"], field="summary_path", must_exist=False)
    max_total_duration_s = _optional_float(payload, "max_total_duration_s")
    raw_guardrails = payload.get("guardrails", {})
    if raw_guardrails is None:
        raw_guardrails = {}
    if not isinstance(raw_guardrails, Mapping):
        raise MiniDmaBenchAutomationError("Mini DMA bench plan field 'guardrails' must be an object.")
    max_stress_mpa = _optional_float(raw_guardrails, "max_stress_mpa")
    recovery_stress_mpa = _optional_float(raw_guardrails, "recovery_stress_mpa")
    if recovery_stress_mpa is not None and recovery_stress_mpa <= 0.0:
        raise MiniDmaBenchAutomationError("Mini DMA bench plan guardrail recovery_stress_mpa must be positive.")
    mechanical_slack_max_seek_mm = _optional_float(raw_guardrails, "mechanical_slack_max_seek_mm")
    if mechanical_slack_max_seek_mm is not None and mechanical_slack_max_seek_mm <= 0.0:
        raise MiniDmaBenchAutomationError("Mini DMA bench plan guardrail mechanical_slack_max_seek_mm must be positive.")
    guardrails = MiniDmaBenchGuardrails(
        max_stress_mpa=max_stress_mpa,
        recovery_stress_mpa=recovery_stress_mpa,
        wire_break_stops_plan=bool(raw_guardrails.get("wire_break_stops_plan", True)),
        allow_mechanical_slack_takeup=bool(raw_guardrails.get("allow_mechanical_slack_takeup", False)),
        mechanical_slack_max_seek_mm=mechanical_slack_max_seek_mm,
    )
    default_max_run_duration_s = _as_float(
        payload.get("default_max_run_duration_s", DEFAULT_MAX_RUN_DURATION_S),
        field="default_max_run_duration_s",
        minimum=0.1,
    )

    default_lengths = payload.get("length_setup", {})
    if default_lengths is None:
        default_lengths = {}
    if not isinstance(default_lengths, Mapping):
        raise MiniDmaBenchAutomationError("Mini DMA bench plan field 'length_setup' must be an object.")
    default_starting_length_mm = _optional_float(default_lengths, "starting_length_mm")
    default_preload_length_mm = _optional_float(default_lengths, "preload_length_mm")

    allow_interactive_setup_prompts = bool(payload.get("allow_interactive_setup_prompts", False))
    raw_runs = payload.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs:
        raise MiniDmaBenchAutomationError("Mini DMA bench plan requires a non-empty 'runs' array.")

    runs: list[MiniDmaBenchRun] = []
    for index, raw_run in enumerate(raw_runs, start=1):
        if not isinstance(raw_run, Mapping):
            raise MiniDmaBenchAutomationError(f"Mini DMA bench run #{index} must be an object.")
        recipe_path = _resolve_plan_path(base, raw_run.get("recipe_path"), field=f"runs[{index}].recipe_path", must_exist=True)
        repeat = int(raw_run.get("repeat", 1))
        if repeat < 1:
            raise MiniDmaBenchAutomationError(f"Mini DMA bench run #{index} repeat must be at least 1.")
        max_run_duration_s = _as_float(
            raw_run.get("max_run_duration_s", default_max_run_duration_s),
            field=f"runs[{index}].max_run_duration_s",
            minimum=0.1,
        )
        run_lengths = raw_run.get("length_setup", {})
        if run_lengths is None:
            run_lengths = {}
        if not isinstance(run_lengths, Mapping):
            raise MiniDmaBenchAutomationError(f"Mini DMA bench run #{index} length_setup must be an object.")
        starting_length_mm = _optional_float(run_lengths, "starting_length_mm")
        preload_length_mm = _optional_float(run_lengths, "preload_length_mm")
        if starting_length_mm is None:
            starting_length_mm = default_starting_length_mm
        if preload_length_mm is None:
            preload_length_mm = default_preload_length_mm
        base_name = str(raw_run.get("name") or recipe_path.stem or f"run_{index}")
        for repeat_index in range(1, repeat + 1):
            name = base_name if repeat == 1 else f"{base_name}_{repeat_index:02d}"
            runs.append(
                MiniDmaBenchRun(
                    name=name,
                    recipe_path=recipe_path,
                    repeat_index=repeat_index,
                    max_run_duration_s=max_run_duration_s,
                    starting_length_mm=starting_length_mm,
                    preload_length_mm=preload_length_mm,
                )
            )
    if execute and not allow_interactive_setup_prompts:
        missing_lengths = [
            run.name
            for run in runs
            if run.starting_length_mm is None or run.preload_length_mm is None
        ]
        if missing_lengths:
            raise MiniDmaBenchAutomationError(
                "Mini DMA bench execution requires automated length_setup starting_length_mm "
                "and preload_length_mm for every run, unless allow_interactive_setup_prompts is true. "
                f"Missing: {', '.join(missing_lengths)}"
            )

    return MiniDmaBenchPlan(
        path=plan_path,
        execute=execute,
        log_dir=log_dir,
        summary_path=summary_path,
        max_total_duration_s=max_total_duration_s,
        guardrails=guardrails,
        runs=tuple(runs),
    )


def _write_summary(path: Path | None, summary: Mapping[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _ensure_qapplication(qt_args: Sequence[str] | None) -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if not isinstance(app, QtWidgets.QApplication):
        app = QtWidgets.QApplication(["mini-dma-bench", *(qt_args or [])])
    ensure_app_theme(app)
    return app


def _suppress_modal_warnings() -> Callable[[], None]:
    original_warning = QtWidgets.QMessageBox.warning

    def _warning(*_args: Any, **_kwargs: Any) -> QtWidgets.QMessageBox.StandardButton:
        return QtWidgets.QMessageBox.StandardButton.Ok

    QtWidgets.QMessageBox.warning = _warning  # type: ignore[method-assign]

    def _restore() -> None:
        QtWidgets.QMessageBox.warning = original_warning  # type: ignore[method-assign]

    return _restore


def _apply_length_setup_automation(window: Any, run: MiniDmaBenchRun) -> None:
    method = getattr(window, "set_length_setup_automation_values", None)
    if callable(method):
        method(
            starting_length_mm=run.starting_length_mm,
            preload_length_mm=run.preload_length_mm,
        )


def _apply_bench_guardrails(window: Any, guardrails: MiniDmaBenchGuardrails) -> None:
    method = getattr(window, "set_bench_mechanical_slack_takeup", None)
    if callable(method):
        method(
            allow=guardrails.allow_mechanical_slack_takeup,
            max_seek_mm=guardrails.mechanical_slack_max_seek_mm,
        )
        return
    setattr(window, "_bench_allow_mechanical_slack_takeup", guardrails.allow_mechanical_slack_takeup)
    setattr(window, "_bench_mechanical_slack_max_seek_mm", guardrails.mechanical_slack_max_seek_mm)


def _prefer_next_output_run(window: Any) -> None:
    def _next_run(_paths: Sequence[Path]) -> str:
        return "next"

    if hasattr(window, "_ask_existing_output_action"):
        window._ask_existing_output_action = _next_run  # type: ignore[method-assign]


def _window_active(window: Any) -> bool:
    return bool(getattr(window, "_automation_active", False) or getattr(window, "_session_active", False))


def _metadata_path(window: Any) -> str | None:
    path = getattr(window, "_session_json_path", None)
    return None if path is None else str(path)


def _window_log_tail(window: Any, *, max_chars: int = 4000) -> str:
    log_widget = getattr(window, "log_output", None)
    text_method = getattr(log_widget, "toPlainText", None)
    if not callable(text_method):
        return ""
    try:
        text = str(text_method())
    except Exception:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _run_dir_from_metadata_path(metadata_path: str | None) -> Path | None:
    if metadata_path is None:
        return None
    return Path(metadata_path).expanduser().resolve().parent


def _attach_control_trace_replay(run_summary: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir_from_metadata_path(run_summary.get("metadata_path"))
    if run_dir is None:
        run_summary["control_trace_replay"] = {"status": "not_available", "reason": "missing_metadata_path"}
        return run_summary
    try:
        replay = analyze_control_trace(run_dir)
        output_paths = write_replay_outputs(replay, run_dir / "diagnostics" / "control_trace_replay")
    except Exception as exc:
        run_summary["control_trace_replay"] = {
            "status": "not_available",
            "reason": str(exc),
            "run_dir": str(run_dir),
        }
        return run_summary
    run_summary["control_trace_replay"] = {
        "status": "written",
        "summary": replay.summary.to_dict(),
        "outputs": {key: str(value) for key, value in output_paths.items()},
    }
    return run_summary


def _latest_stress_mpa(window: Any) -> float | None:
    method = getattr(window, "_bench_latest_stress_mpa", None)
    if callable(method):
        value = method()
        return None if value is None else float(value)
    for attr in ("_live_plot_points", "_session_points"):
        points = getattr(window, attr, None)
        if points:
            for point in reversed(points):
                value = getattr(point, "stress_mpa", None)
                if value is not None:
                    return float(value)
    return None


def _call_window_method(window: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    method = getattr(window, name, None)
    if callable(method):
        return method(*args, **kwargs)
    return None


def _check_guardrails(
    window: Any,
    guardrails: MiniDmaBenchGuardrails,
) -> dict[str, Any] | None:
    wire_break = getattr(window, "_wire_break_detected", None)
    if callable(wire_break) and bool(wire_break()):
        _call_window_method(window, "_disable_supply_output")
        _call_window_method(
            window,
            "_stop_auto_ramp",
            log_completion=False,
            user_initiated=False,
            offer_recovery=False,
            stop_reason="wire_break_or_contact_loss",
            stop_detail="Bench automation detected wire break or contact loss.",
        )
        _call_window_method(
            window,
            "_stop_session",
            reason="wire_break_or_contact_loss",
            detail="Bench automation detected wire break or contact loss.",
        )
        return {"type": "wire_break"}

    if guardrails.max_stress_mpa is None:
        return None
    stress_mpa = _latest_stress_mpa(window)
    if stress_mpa is None or stress_mpa <= guardrails.max_stress_mpa:
        return None
    _call_window_method(window, "_disable_supply_output")
    recovered = False
    if guardrails.recovery_stress_mpa is not None:
        recovered = bool(
            _call_window_method(
                window,
                "start_bench_stress_recovery",
                guardrails.recovery_stress_mpa,
                reason="bench high-stress guard",
            )
        )
    return {
        "type": "high_stress",
        "stress_mpa": stress_mpa,
        "max_stress_mpa": guardrails.max_stress_mpa,
        "recovery_stress_mpa": guardrails.recovery_stress_mpa,
        "recovery_started": recovered,
    }


def _execute_run(
    run: MiniDmaBenchRun,
    *,
    app: Any,
    window: Any,
    guardrails: MiniDmaBenchGuardrails,
    sleep_fn: Callable[[float], None],
    total_deadline_s: float | None,
) -> dict[str, Any]:
    start_s = time.monotonic()
    deadline_s = start_s + run.max_run_duration_s
    if total_deadline_s is not None:
        deadline_s = min(deadline_s, total_deadline_s)

    window._load_recipe_from_path(run.recipe_path)
    _apply_length_setup_automation(window, run)
    _apply_bench_guardrails(window, guardrails)
    _prefer_next_output_run(window)
    window._start_auto_ramp()
    app.processEvents()
    if not _window_active(window):
        return {
            "name": run.name,
            "recipe_path": str(run.recipe_path),
            "repeat_index": run.repeat_index,
            "status": "completed" if _metadata_path(window) is not None else "not_started",
            "elapsed_s": max(0.0, time.monotonic() - start_s),
            "metadata_path": _metadata_path(window),
            "startup_log_tail": _window_log_tail(window),
        }

    status = "completed"
    guard_events: list[dict[str, Any]] = []
    while _window_active(window):
        app.processEvents()
        guard_event = _check_guardrails(window, guardrails)
        if guard_event is not None:
            guard_events.append(guard_event)
            if guard_event["type"] == "wire_break":
                status = "wire_break"
                break
            if guard_event["type"] == "high_stress":
                status = "guard_recovered" if guard_event.get("recovery_started") else "guard_tripped"
                break
        if time.monotonic() >= deadline_s:
            status = "timeout"
            stop = getattr(window, "_stop_auto_ramp", None)
            if callable(stop):
                stop(
                    log_completion=False,
                    user_initiated=False,
                    offer_recovery=False,
                    stop_reason="automation_timeout",
                    stop_detail=f"Bench automation run exceeded {run.max_run_duration_s:.1f} s.",
                )
            stop_session = getattr(window, "_stop_session", None)
            if callable(stop_session):
                stop_session(
                    reason="automation_timeout",
                    detail=f"Bench automation run exceeded {run.max_run_duration_s:.1f} s.",
                )
            break
        sleep_fn(0.05)
    app.processEvents()
    return {
        "name": run.name,
        "recipe_path": str(run.recipe_path),
        "repeat_index": run.repeat_index,
        "status": status,
        "elapsed_s": max(0.0, time.monotonic() - start_s),
        "metadata_path": _metadata_path(window),
        "guard_events": guard_events,
    }


def run_mini_dma_bench_plan(
    path: str | Path,
    *,
    qt_args: Sequence[str] | None = None,
    app_factory: Callable[[Sequence[str] | None], Any] | None = None,
    window_factory: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    plan = load_mini_dma_bench_plan(path)
    if not plan.execute:
        summary: dict[str, Any] = {
            "kind": PLAN_KIND,
            "schema_version": PLAN_SCHEMA_VERSION,
            "mode": "dry_run",
            "plan_path": str(plan.path),
            "run_count": len(plan.runs),
            "runs": [
                {
                    "name": run.name,
                    "recipe_path": str(run.recipe_path),
                    "repeat_index": run.repeat_index,
                    "status": "validated",
                    "guardrails": {
                        "max_stress_mpa": plan.guardrails.max_stress_mpa,
                        "recovery_stress_mpa": plan.guardrails.recovery_stress_mpa,
                        "wire_break_stops_plan": plan.guardrails.wire_break_stops_plan,
                        "allow_mechanical_slack_takeup": plan.guardrails.allow_mechanical_slack_takeup,
                        "mechanical_slack_max_seek_mm": plan.guardrails.mechanical_slack_max_seek_mm,
                    },
                }
                for run in plan.runs
            ],
        }
        _write_summary(plan.summary_path, summary)
        return summary

    from .mini_dma_logger import MainWindow

    app = app_factory(qt_args) if app_factory is not None else _ensure_qapplication(qt_args)
    factory = window_factory or MainWindow
    restore_warning = _suppress_modal_warnings()
    run_summaries: list[dict[str, Any]] = []
    total_start_s = time.monotonic()
    total_deadline_s = None
    if plan.max_total_duration_s is not None:
        total_deadline_s = total_start_s + plan.max_total_duration_s
    try:
        stop_after_wire_break = False
        for run in plan.runs:
            if stop_after_wire_break:
                run_summaries.append(
                    {
                        "name": run.name,
                        "recipe_path": str(run.recipe_path),
                        "repeat_index": run.repeat_index,
                        "status": "skipped_after_wire_break",
                    }
                )
                continue
            if total_deadline_s is not None and time.monotonic() >= total_deadline_s:
                run_summaries.append(
                    {
                        "name": run.name,
                        "recipe_path": str(run.recipe_path),
                        "repeat_index": run.repeat_index,
                        "status": "skipped_total_timeout",
                    }
                )
                continue
            window = factory(log_dir=None if plan.log_dir is None else str(plan.log_dir), persist_settings=True)
            try:
                run_summary = _execute_run(
                    run,
                    app=app,
                    window=window,
                    guardrails=plan.guardrails,
                    sleep_fn=sleep_fn,
                    total_deadline_s=total_deadline_s,
                )
                run_summaries.append(_attach_control_trace_replay(run_summary))
                if (
                    run_summaries[-1].get("status") == "wire_break"
                    and plan.guardrails.wire_break_stops_plan
                ):
                    stop_after_wire_break = True
            finally:
                close = getattr(window, "close", None)
                if callable(close):
                    close()
                app.processEvents()
    finally:
        restore_warning()

    summary = {
        "kind": PLAN_KIND,
        "schema_version": PLAN_SCHEMA_VERSION,
        "mode": "execute",
        "plan_path": str(plan.path),
        "log_dir": None if plan.log_dir is None else str(plan.log_dir),
        "run_count": len(run_summaries),
        "elapsed_s": max(0.0, time.monotonic() - total_start_s),
        "runs": run_summaries,
    }
    _write_summary(plan.summary_path, summary)
    return summary
