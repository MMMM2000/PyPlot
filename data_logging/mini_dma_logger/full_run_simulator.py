"""Software-only Mini DMA full-run controller simulation.

This harness is intentionally deterministic and hardware-free. It models a
first-overheating style run with target acquisition, current rise, endpoint
recovery, optional reverse unwind, mechanical corrections, and slack take-up.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from .wire_simulator import (
    CurrentSweepConfig,
    MeasurementSample,
    RobustControllerConfig,
    VirtualWireConfig,
    _clamp,
    _median_absolute_deviation,
    decide_robust_center,
    load_g_from_stress_mpa,
    processed_control_signal,
    transformation_fraction,
)


FULL_RUN_SCENARIOS = (
    "baseline_first_overheating",
    "noisy_centered_first_overheating",
    "transformation_recovery",
    "reverse_unwind_recovery",
    "slack_after_unwind_takeup",
    "thin_wire_delayed_feedback",
)


@dataclass(frozen=True)
class FullRunConfig:
    name: str = "baseline_first_overheating"
    description: str = "Nominal software-only first-overheating Mini DMA run."
    wire: VirtualWireConfig = field(default_factory=VirtualWireConfig)
    controller: RobustControllerConfig = field(default_factory=RobustControllerConfig)
    sweep: CurrentSweepConfig = field(
        default_factory=lambda: CurrentSweepConfig(start_ma=1.0, end_ma=80.0, rate_ma_s=0.8, sample_hz=4.5)
    )
    reverse_current: bool = True
    target_ramp_timeout_s: float = 90.0
    endpoint_hold_timeout_s: float = 90.0
    max_ticks: int = 5000
    scale_latency_s: float = 0.2
    zero_compression_stress: bool = False
    seed: int = 0

    def validated(self) -> "FullRunConfig":
        self.wire.validated()
        self.controller.validated()
        self.sweep.validated()
        if self.target_ramp_timeout_s <= 0.0:
            raise ValueError("target_ramp_timeout_s must be positive")
        if self.endpoint_hold_timeout_s <= 0.0:
            raise ValueError("endpoint_hold_timeout_s must be positive")
        if self.max_ticks <= 0:
            raise ValueError("max_ticks must be positive")
        if self.scale_latency_s < 0.0:
            raise ValueError("scale_latency_s must be non-negative")
        return self


@dataclass(frozen=True)
class FullRunEvent:
    elapsed_s: float
    phase: str
    current_ma: float
    motor_mm: float
    target_stress_mpa: float
    processed_center_mpa: float
    processed_noise_mpa: float
    processed_slope_mpa_s: float
    raw_min_mpa: float
    raw_max_mpa: float
    error_mpa: float
    decision: str
    result: str
    correction_mm: float
    reason: str
    endpoint_recovered: bool
    fresh: bool
    feedback_age_s: float
    total_travel_mm: float
    cruise_allowed: bool = False

    def to_row(self) -> dict[str, Any]:
        return {
            "elapsed_s": f"{self.elapsed_s:.6f}",
            "automation_phase": self.phase,
            "automation_basis": "stress_mpa",
            "automation_target_value": f"{self.target_stress_mpa:.6f}",
            "decision": self.decision,
            "result": self.result,
            "current_value": f"{self.processed_center_mpa:.6f}",
            "error_value": f"{self.error_mpa:.6f}",
            "filtered_noise": f"{self.processed_noise_mpa:.6f}",
            "filtered_slope_per_s": f"{self.processed_slope_mpa_s:.6f}",
            "raw_min_mpa": f"{self.raw_min_mpa:.6f}",
            "raw_max_mpa": f"{self.raw_max_mpa:.6f}",
            "tolerance": "",
            "sensitivity_per_mm": "",
            "motor_step_mm": "",
            "correction_mm": f"{self.correction_mm:.9f}",
            "target_mm": f"{self.motor_mm:.9f}",
            "result_detail": self.reason,
            "endpoint_recovered": str(bool(self.endpoint_recovered)).lower(),
            "processed_fresh": str(bool(self.fresh)).lower(),
            "feedback_age_s": f"{self.feedback_age_s:.6f}",
            "total_travel_mm": f"{self.total_travel_mm:.9f}",
            "cruise_allowed": str(bool(self.cruise_allowed)).lower(),
        }


@dataclass(frozen=True)
class FullRunTrace:
    config: FullRunConfig
    samples: list[MeasurementSample]
    events: list[FullRunEvent]
    stop_reason: str
    invariants: dict[str, bool]
    warnings: list[str]

    def summary(self) -> dict[str, Any]:
        final = self.events[-1] if self.events else None
        return {
            "scenario": self.config.name,
            "description": self.config.description,
            "stop_reason": self.stop_reason,
            "sample_count": len(self.samples),
            "event_count": len(self.events),
            "final_phase": None if final is None else final.phase,
            "final_decision": None if final is None else final.decision,
            "final_result": None if final is None else final.result,
            "final_processed_center_mpa": None if final is None else final.processed_center_mpa,
            "final_error_mpa": None if final is None else final.error_mpa,
            "max_total_travel_mm": max((event.total_travel_mm for event in self.events), default=0.0),
            "max_abs_correction_mm": max((abs(event.correction_mm) for event in self.events), default=0.0),
            "current_hold_count": sum(1 for event in self.events if event.phase == "current_hold"),
            "endpoint_hold_count": sum(
                1 for event in self.events if event.result == "endpoint_waiting_for_recovery"
            ),
            "invariants": dict(self.invariants),
            "warnings": list(self.warnings),
        }


class _FullRunState:
    def __init__(self, config: FullRunConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)
        self.elapsed_s = 0.0
        self.sample_index = 0
        self.current_ma = config.sweep.start_ma
        self.motor_mm = config.wire.initial_motor_mm
        self.total_travel_mm = 0.0
        self.samples: list[MeasurementSample] = []
        self.events: list[FullRunEvent] = []
        self.previous_error_sign = 0

    @property
    def dt_s(self) -> float:
        return 1.0 / self.config.sweep.sample_hz

    def feedback_samples(self) -> list[MeasurementSample]:
        if self.config.scale_latency_s <= 0.0:
            return list(self.samples)
        latency_samples = max(1, int(math.ceil(self.config.scale_latency_s / self.dt_s - 1e-12)))
        available_count = len(self.samples) - latency_samples
        if available_count <= 0:
            return []
        return self.samples[:available_count]

    def feedback_age_s(self, feedback_samples: list[MeasurementSample] | None = None) -> float:
        feedback = self.feedback_samples() if feedback_samples is None else feedback_samples
        if not feedback:
            return math.inf
        return max(0.0, self.elapsed_s - feedback[-1].elapsed_s)

    def controller_for_decision(self) -> RobustControllerConfig:
        return replace(self.config.controller, previous_error_sign=self.previous_error_sign)

    def sample(self, phase: str, *, rising: bool) -> MeasurementSample:
        wire = self.config.wire
        fraction = transformation_fraction(self.current_ma, wire, rising=rising)
        free_shift_mm = wire.initial_free_length_shift_mm + fraction * wire.transformation_contraction_mm
        mechanical_mm = self.motor_mm + free_shift_mm
        base_stress = mechanical_mm * wire.elastic_stiffness_mpa_per_mm
        if self.config.zero_compression_stress and base_stress < 0.0:
            base_stress = 0.0
        fluctuation = 0.0
        if 0.0 < fraction < 1.0 and wire.fluctuation_mpa:
            fluctuation = wire.fluctuation_mpa * math.sin(2.0 * math.pi * wire.fluctuation_cycles * fraction)
        noise = self.rng.gauss(0.0, wire.noise_mpa) if wire.noise_mpa else 0.0
        drift = wire.drift_mpa_per_s * self.elapsed_s
        stress = base_stress + fluctuation + noise + drift
        raw_stress = stress
        status = "ok"
        safety_reason = ""
        strain_pct = mechanical_mm / wire.length_mm * 100.0
        if wire.break_stress_mpa is not None and raw_stress >= wire.break_stress_mpa:
            status = "wire_break"
            safety_reason = "break_stress"
        if wire.break_strain_pct is not None and abs(strain_pct) >= wire.break_strain_pct:
            status = "wire_break"
            safety_reason = safety_reason or "break_strain"
        sample = MeasurementSample(
            sample_index=self.sample_index,
            elapsed_s=self.elapsed_s,
            current_ma=self.current_ma,
            motor_mm=self.motor_mm,
            free_length_shift_mm=free_shift_mm,
            transformation_fraction=fraction,
            stress_mpa=stress,
            raw_stress_mpa=raw_stress,
            load_g=load_g_from_stress_mpa(stress, wire.diameter_mm),
            raw_load_g=load_g_from_stress_mpa(raw_stress, wire.diameter_mm),
            strain_pct=strain_pct,
            status=status,
            safety_reason=safety_reason,
        )
        self.samples.append(sample)
        self.sample_index += 1
        return sample

    def advance_time(self) -> None:
        self.elapsed_s += self.dt_s

    def record_event(self, phase: str, correction_mm: float, reason: str) -> FullRunEvent:
        feedback = self.feedback_samples()
        if not feedback:
            raise ValueError("cannot record a control event without delayed feedback")
        controller = self.controller_for_decision()
        decision = decide_robust_center(feedback, controller)
        signal = processed_control_signal(feedback, controller)
        feedback_age_s = self.feedback_age_s(feedback)
        event = FullRunEvent(
            elapsed_s=self.elapsed_s,
            phase=phase,
            current_ma=self.current_ma,
            motor_mm=self.motor_mm,
            target_stress_mpa=self.config.controller.target_stress_mpa,
            processed_center_mpa=signal.center_mpa,
            processed_noise_mpa=signal.noise_mpa,
            processed_slope_mpa_s=signal.slope_mpa_s,
            raw_min_mpa=signal.raw_min_mpa,
            raw_max_mpa=signal.raw_max_mpa,
            error_mpa=signal.center_mpa - self.config.controller.target_stress_mpa,
            decision=decision.decision,
            result=decision.result if reason != "endpoint_waiting_for_recovery" else reason,
            correction_mm=correction_mm,
            reason=reason,
            endpoint_recovered=decision.endpoint_recovered,
            fresh=signal.fresh and feedback_age_s <= self.config.controller.stale_feedback_s,
            feedback_age_s=feedback_age_s,
            total_travel_mm=self.total_travel_mm,
            cruise_allowed=False,
        )
        self.events.append(event)
        recovery_band = max(controller.tolerance_mpa, controller.min_recovery_mpa)
        if abs(event.error_mpa) > recovery_band and decision.decision != "safety_stop":
            self.previous_error_sign = 1 if event.error_mpa > 0.0 else -1
        return event

    def correct_toward_target(self) -> float:
        feedback = self.feedback_samples()
        if not feedback or self.feedback_age_s(feedback) > self.config.controller.stale_feedback_s:
            return 0.0
        decision = decide_robust_center(feedback, self.controller_for_decision())
        correction = decision.motor_step_mm
        if decision.decision in {"no_move", "wait_reversal", "safety_stop"}:
            correction = 0.0
        correction = _clamp(
            correction,
            -self.config.controller.max_correction_mm,
            self.config.controller.max_correction_mm,
        )
        if abs(correction) <= 0.0:
            return 0.0
        self.motor_mm += correction
        self.total_travel_mm += abs(correction)
        return correction


def _recovered(state: _FullRunState) -> bool:
    feedback = state.feedback_samples()
    if not feedback:
        return False
    decision = decide_robust_center(feedback, state.controller_for_decision())
    return decision.endpoint_recovered


def _stop_for_safety(state: _FullRunState) -> str | None:
    if state.samples and state.samples[-1].status != "ok":
        return state.samples[-1].status
    return None


def run_full_mini_dma_simulation(config: FullRunConfig) -> FullRunTrace:
    config = config.validated()
    state = _FullRunState(config)
    stop_reason = "completed"

    # Target acquisition before heating.
    target_deadline = config.target_ramp_timeout_s
    while state.elapsed_s <= target_deadline and len(state.events) < config.max_ticks:
        state.sample("target_ramp", rising=True)
        stop = _stop_for_safety(state)
        if stop is not None:
            stop_reason = stop
            break
        if not state.feedback_samples():
            state.advance_time()
            continue
        correction = 0.0 if _recovered(state) else state.correct_toward_target()
        state.record_event("target_ramp", correction, "target_acquisition")
        stop = _stop_for_safety(state)
        if stop is not None:
            stop_reason = stop
            break
        if _recovered(state):
            break
        state.advance_time()
    else:
        stop_reason = "target_ramp_timeout"

    def _run_sweep(*, start_ma: float, end_ma: float, phase_name: str, rising: bool) -> str | None:
        direction = 1.0 if end_ma >= start_ma else -1.0
        state.current_ma = start_ma
        current = start_ma
        endpoint_hold_s = 0.0
        while len(state.events) < config.max_ticks:
            state.current_ma = current
            state.sample(phase_name, rising=rising)
            at_endpoint = abs(current - end_ma) <= 1e-12
            stop = _stop_for_safety(state)
            if stop is not None:
                return stop
            if not state.feedback_samples():
                if not at_endpoint:
                    current += direction * abs(config.sweep.rate_ma_s) * state.dt_s
                    if direction >= 0.0:
                        current = min(end_ma, current)
                    else:
                        current = max(end_ma, current)
                state.advance_time()
                continue
            recovered = _recovered(state)
            phase = phase_name
            reason = "current_tracking"
            if not recovered:
                phase = "current_hold"
                reason = "endpoint_waiting_for_recovery" if at_endpoint else "processed_recovery"
            correction = 0.0 if recovered else state.correct_toward_target()
            state.record_event(phase, correction, reason)
            stop = _stop_for_safety(state)
            if stop is not None:
                return stop
            if at_endpoint:
                if recovered:
                    return None
                endpoint_hold_s += state.dt_s
                if endpoint_hold_s >= config.endpoint_hold_timeout_s:
                    return "endpoint_recovery_timeout"
            else:
                current += direction * abs(config.sweep.rate_ma_s) * state.dt_s
                if direction >= 0.0:
                    current = min(end_ma, current)
                else:
                    current = max(end_ma, current)
            state.advance_time()
        return "tick_limit"

    if stop_reason == "completed":
        state.advance_time()
        stop = _run_sweep(
            start_ma=config.sweep.start_ma,
            end_ma=config.sweep.end_ma,
            phase_name="current",
            rising=config.sweep.end_ma >= config.sweep.start_ma,
        )
        if stop is not None:
            stop_reason = stop
    if stop_reason == "completed" and config.reverse_current:
        state.advance_time()
        stop = _run_sweep(
            start_ma=config.sweep.end_ma,
            end_ma=config.sweep.start_ma,
            phase_name="current_limit_unwind",
            rising=False,
        )
        if stop is not None:
            stop_reason = stop

    invariants = check_full_run_invariants(FullRunTrace(config, state.samples, state.events, stop_reason, {}, []))
    warnings = [name for name, passed in invariants.items() if not passed]
    return FullRunTrace(config, state.samples, state.events, stop_reason, invariants, warnings)


def check_full_run_invariants(trace: FullRunTrace) -> dict[str, bool]:
    events = trace.events
    endpoint_waits = [event for event in events if event.result == "endpoint_waiting_for_recovery"]
    endpoint_completions = [
        event
        for event in events
        if event.phase in {"current", "current_limit_unwind"}
        and abs(event.current_ma - trace.config.sweep.end_ma) <= 1e-9
    ]
    return {
        "no_load_stress_cruise": all(not event.cruise_allowed for event in events),
        "corrections_bounded": all(
            abs(event.correction_mm) <= trace.config.controller.max_correction_mm + 1e-12
            for event in events
        ),
        "no_accumulated_correction_travel_stop": trace.stop_reason != "travel_limit",
        "endpoint_waits_when_unrecovered": all(not event.endpoint_recovered for event in endpoint_waits),
        "endpoint_completion_recovered": all(event.endpoint_recovered for event in endpoint_completions),
        "completed_run_recovered": trace.stop_reason != "completed"
        or not events
        or events[-1].endpoint_recovered,
        "does_not_stop_for_slack": trace.stop_reason != "slack_no_response",
        "scale_latency_applied": trace.config.scale_latency_s <= 0.0
        or all(event.feedback_age_s + 1e-12 >= trace.config.scale_latency_s for event in events),
    }


def full_run_scenario_by_name(name: str) -> FullRunConfig:
    base = FullRunConfig(
        name="baseline_first_overheating",
        description="Nominal full first-overheating run with endpoint recovery.",
        wire=VirtualWireConfig(
            length_mm=33.0,
            diameter_mm=0.0125,
            initial_motor_mm=0.030,
            elastic_stiffness_mpa_per_mm=650.0,
            transformation_onset_ma=20.0,
            transformation_end_ma=70.0,
            transformation_contraction_mm=0.006,
            noise_mpa=0.25,
        ),
        controller=RobustControllerConfig(target_stress_mpa=20.0, tolerance_mpa=0.5, min_recovery_mpa=1.0, max_correction_mm=0.005),
        sweep=CurrentSweepConfig(start_ma=1.0, end_ma=80.0, rate_ma_s=2.0, sample_hz=4.5),
        seed=101,
    )
    scenarios = {
        base.name: base,
        "noisy_centered_first_overheating": replace(
            base,
            name="noisy_centered_first_overheating",
            description="High raw noise centered near target should avoid unnecessary chasing.",
            wire=replace(base.wire, fluctuation_mpa=0.0, noise_mpa=2.0, transformation_contraction_mm=0.0),
            seed=103,
        ),
        "transformation_recovery": replace(
            base,
            name="transformation_recovery",
            description="Transformation contraction forces current-hold mechanical recovery.",
            wire=replace(base.wire, transformation_contraction_mm=0.075, fluctuation_mpa=5.0, noise_mpa=0.5),
            controller=replace(base.controller, safety_min_stress_mpa=None),
            sweep=replace(base.sweep, rate_ma_s=20.0),
            seed=107,
        ),
        "reverse_unwind_recovery": replace(
            base,
            name="reverse_unwind_recovery",
            description="Reverse unwind must recover processed center before completing.",
            wire=replace(base.wire, initial_motor_mm=0.018, transformation_contraction_mm=0.045, noise_mpa=0.45),
            seed=109,
        ),
        "slack_after_unwind_takeup": replace(
            base,
            name="slack_after_unwind_takeup",
            description="Near-zero-load slack case keeps taking up tension until the processed center recovers.",
            wire=replace(base.wire, initial_motor_mm=-0.018, elastic_stiffness_mpa_per_mm=650.0, transformation_contraction_mm=0.0, noise_mpa=0.05),
            controller=replace(base.controller, max_correction_mm=0.002, safety_min_stress_mpa=None),
            zero_compression_stress=True,
            seed=113,
        ),
        "thin_wire_delayed_feedback": replace(
            base,
            name="thin_wire_delayed_feedback",
            description="Very thin wire with low sample cadence remains bounded.",
            wire=replace(base.wire, diameter_mm=0.0083, noise_mpa=0.35, transformation_contraction_mm=0.03),
            sweep=replace(base.sweep, sample_hz=1.2),
            controller=replace(base.controller, max_correction_mm=0.02, stale_feedback_s=1.2),
            seed=127,
        ),
    }
    try:
        return scenarios[name]
    except KeyError as exc:
        known = ", ".join(sorted(scenarios))
        raise ValueError(f"unknown full-run Mini DMA simulator scenario {name!r}; known: {known}") from exc


def run_parameter_sweep() -> list[FullRunTrace]:
    traces: list[FullRunTrace] = []
    base = full_run_scenario_by_name("baseline_first_overheating")
    index = 0
    for diameter in (0.0083, 0.0125, 0.04):
        for stiffness in (250.0, 650.0, 1200.0):
            for noise in (0.2, 1.2):
                index += 1
                config = replace(
                    base,
                    name=f"sweep_d{diameter:g}_k{stiffness:g}_n{noise:g}",
                    description="Parameter sweep of diameter, stiffness, and noise.",
                    wire=replace(base.wire, diameter_mm=diameter, elastic_stiffness_mpa_per_mm=stiffness, noise_mpa=noise),
                    seed=1000 + index,
                )
                traces.append(run_full_mini_dma_simulation(config))
    return traces


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
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


def write_full_run_outputs(trace: FullRunTrace, output_dir: Path | str) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    measurement_path = out / "measurement.csv"
    control_path = out / "control_trace.csv"
    summary_path = out / "summary.json"
    config_path = out / "config.json"
    report_path = out / "report.md"
    plot_path = out / "full_run.png"
    _write_csv(measurement_path, (sample.to_row() for sample in trace.samples))
    _write_csv(control_path, (event.to_row() for event in trace.events))
    summary_path.write_text(json.dumps(trace.summary(), indent=2), encoding="utf-8")
    config_path.write_text(json.dumps(asdict(trace.config), indent=2), encoding="utf-8")
    _write_full_run_report(report_path, trace)
    paths = {
        "measurement": measurement_path,
        "control_trace": control_path,
        "summary": summary_path,
        "config": config_path,
        "report": report_path,
    }
    if _write_full_run_plot(plot_path, trace):
        paths["plot"] = plot_path
    return paths


def write_sweep_outputs(traces: list[FullRunTrace], output_dir: Path | str) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "full_run_sweep_summary.json"
    report_path = out / "full_run_sweep_report.md"
    summary = {"runs": [trace.summary() for trace in traces]}
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    lines = [
        "# Mini DMA full-run parameter sweep",
        "",
        "| Scenario | Stop | Max travel mm | Max correction mm | Holds | Invariants |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for trace in traces:
        item = trace.summary()
        invariant_text = "ok" if all(item["invariants"].values()) else ",".join(item["warnings"])
        lines.append(
            f"| {item['scenario']} | {item['stop_reason']} | {item['max_total_travel_mm']:.6f} | "
            f"{item['max_abs_correction_mm']:.6f} | {item['current_hold_count']} | {invariant_text} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"summary": summary_path, "report": report_path}


def _write_full_run_report(path: Path, trace: FullRunTrace) -> None:
    item = trace.summary()
    invariants = "\n".join(
        f"- {name}: {'pass' if passed else 'FAIL'}"
        for name, passed in trace.invariants.items()
    )
    text = f"""# Mini DMA full-run simulation

Scenario: `{trace.config.name}`

{trace.config.description}

- Stop reason: {trace.stop_reason}
- Samples: {item["sample_count"]}
- Events: {item["event_count"]}
- Current-hold events: {item["current_hold_count"]}
- Endpoint hold/recovery events: {item["endpoint_hold_count"]}
- Maximum total correction travel: {item["max_total_travel_mm"]:.6f} mm
- Maximum single correction: {item["max_abs_correction_mm"]:.6f} mm

## Invariants

{invariants}
"""
    path.write_text(text, encoding="utf-8")


def _write_full_run_plot(path: Path, trace: FullRunTrace) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    elapsed = [sample.elapsed_s for sample in trace.samples]
    raw = [sample.raw_stress_mpa for sample in trace.samples]
    current = [sample.current_ma for sample in trace.samples]
    motor = [sample.motor_mm for sample in trace.samples]
    event_t = [event.elapsed_s for event in trace.events]
    center = [event.processed_center_mpa for event in trace.events]
    corrections = [event.correction_mm for event in trace.events]
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    axes[0].plot(elapsed, raw, color="#94a3b8", lw=0.8, label="raw stress")
    axes[0].plot(event_t, center, color="#dc2626", lw=1.2, label="processed center")
    axes[0].axhline(trace.config.controller.target_stress_mpa, color="#111827", ls="--", lw=0.9, label="target")
    axes[0].set_ylabel("MPa")
    axes[0].legend(fontsize=8, loc="best")
    axes[1].plot(elapsed, current, color="#2563eb", lw=1.0, label="current")
    axes[1].plot(elapsed, motor, color="#059669", lw=1.0, label="motor")
    axes[1].set_ylabel("mA / mm")
    axes[1].legend(fontsize=8, loc="best")
    axes[2].bar(event_t, corrections, width=max(0.02, trace.config.sweep.sample_hz ** -1 * 0.6), color="#7c3aed")
    axes[2].set_ylabel("correction mm")
    axes[2].set_xlabel("Elapsed (s)")
    for ax in axes:
        ax.grid(True, alpha=0.25)
    fig.suptitle(f"{trace.config.name} | {trace.stop_reason}")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run software-only full Mini DMA first-overheating simulations.")
    parser.add_argument("--scenario", action="append", choices=FULL_RUN_SCENARIOS)
    parser.add_argument("--sweep", action="store_true", help="Run the built-in parameter sweep.")
    parser.add_argument("--out", type=Path, default=Path("artifacts/mini-dma-full-run-sim"))
    args = parser.parse_args(argv)

    traces: list[FullRunTrace]
    if args.sweep:
        traces = run_parameter_sweep()
        write_sweep_outputs(traces, args.out)
    else:
        names = args.scenario or list(FULL_RUN_SCENARIOS)
        traces = [run_full_mini_dma_simulation(full_run_scenario_by_name(name)) for name in names]
        for trace in traces:
            scenario_out = args.out / trace.config.name if len(traces) > 1 else args.out
            write_full_run_outputs(trace, scenario_out)
    print(json.dumps({"runs": [trace.summary() for trace in traces]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
