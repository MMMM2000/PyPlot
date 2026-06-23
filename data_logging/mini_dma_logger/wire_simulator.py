"""Deterministic Mini DMA virtual wire simulator and replay harness."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .trace_replay import GRAVITY_MS2

DEFAULT_SCENARIOS = (
    "high_bias_cloud",
    "wide_high_cloud",
    "target_spanning_cloud",
    "transformation_bias",
    "sign_crossing_reversal",
    "wire_break",
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"non-finite simulator value: {value!r}")
    return float(value)


def load_g_from_stress_mpa(stress_mpa: float, diameter_mm: float) -> float:
    if diameter_mm <= 0.0:
        return 0.0
    area_mm2 = math.pi * diameter_mm * diameter_mm / 4.0
    force_n = stress_mpa * area_mm2
    return force_n * 1000.0 / GRAVITY_MS2


def _median_absolute_deviation(values: list[float], center: float) -> float:
    if not values:
        return 0.0
    return statistics.median(abs(value - center) for value in values)


@dataclass(frozen=True)
class VirtualWireConfig:
    """Physical-ish parameters for a virtual Mini DMA wire."""

    length_mm: float = 30.0
    diameter_mm: float = 0.0125
    elastic_stiffness_mpa_per_mm: float = 600.0
    initial_motor_mm: float = 0.0
    initial_free_length_shift_mm: float = 0.0
    transformation_onset_ma: float = 20.0
    transformation_end_ma: float = 60.0
    transformation_contraction_mm: float = 0.08
    transformation_hysteresis_ma: float = 4.0
    fluctuation_mpa: float = 0.0
    fluctuation_cycles: float = 3.0
    noise_mpa: float = 0.0
    drift_mpa_per_s: float = 0.0
    spike_mpa: float = 0.0
    spike_every_samples: int = 0
    raw_extreme_mpa: float | None = None
    raw_extreme_at_s: float | None = None
    break_stress_mpa: float | None = None
    break_strain_pct: float | None = None
    break_travel_mm: float | None = None

    def validated(self) -> "VirtualWireConfig":
        if self.length_mm <= 0.0:
            raise ValueError("length_mm must be positive")
        if self.diameter_mm <= 0.0:
            raise ValueError("diameter_mm must be positive")
        if self.elastic_stiffness_mpa_per_mm <= 0.0:
            raise ValueError("elastic_stiffness_mpa_per_mm must be positive")
        if self.transformation_end_ma <= self.transformation_onset_ma:
            raise ValueError("transformation_end_ma must be greater than onset")
        if self.spike_every_samples < 0:
            raise ValueError("spike_every_samples must be non-negative")
        for value in (
            self.length_mm,
            self.diameter_mm,
            self.elastic_stiffness_mpa_per_mm,
            self.initial_motor_mm,
            self.initial_free_length_shift_mm,
            self.transformation_onset_ma,
            self.transformation_end_ma,
            self.transformation_contraction_mm,
            self.transformation_hysteresis_ma,
            self.fluctuation_mpa,
            self.fluctuation_cycles,
            self.noise_mpa,
            self.drift_mpa_per_s,
            self.spike_mpa,
        ):
            _finite(float(value))
        return self


@dataclass(frozen=True)
class CurrentSweepConfig:
    start_ma: float = 0.0
    end_ma: float = 80.0
    rate_ma_s: float = 0.8
    sample_hz: float = 4.5
    hold_s: float = 0.0

    def validated(self) -> "CurrentSweepConfig":
        if self.sample_hz <= 0.0:
            raise ValueError("sample_hz must be positive")
        if self.rate_ma_s == 0.0 and self.start_ma != self.end_ma:
            raise ValueError("rate_ma_s cannot be zero for a changing current")
        for value in (self.start_ma, self.end_ma, self.rate_ma_s, self.sample_hz, self.hold_s):
            _finite(float(value))
        return self


@dataclass(frozen=True)
class RobustControllerConfig:
    target_stress_mpa: float = 20.0
    tolerance_mpa: float = 2.0
    window_s: float = 1.8
    noise_sigma: float = 2.5
    min_recovery_mpa: float = 4.0
    motor_step_mm: float = 0.001
    max_correction_mm: float = 0.08
    safety_max_stress_mpa: float | None = 320.0
    safety_min_stress_mpa: float | None = -20.0
    stale_feedback_s: float = 1.0
    absurd_jump_mpa: float | None = 180.0
    reversal_confirm_s: float = 0.6
    previous_error_sign: int = 0

    def validated(self) -> "RobustControllerConfig":
        if self.window_s <= 0.0:
            raise ValueError("window_s must be positive")
        if self.motor_step_mm <= 0.0:
            raise ValueError("motor_step_mm must be positive")
        if self.max_correction_mm < self.motor_step_mm:
            raise ValueError("max_correction_mm must be at least motor_step_mm")
        if self.previous_error_sign not in (-1, 0, 1):
            raise ValueError("previous_error_sign must be -1, 0, or 1")
        for value in (
            self.target_stress_mpa,
            self.tolerance_mpa,
            self.window_s,
            self.noise_sigma,
            self.min_recovery_mpa,
            self.motor_step_mm,
            self.max_correction_mm,
            self.stale_feedback_s,
            self.reversal_confirm_s,
        ):
            _finite(float(value))
        return self


@dataclass(frozen=True)
class VirtualWireScenario:
    name: str
    description: str
    wire: VirtualWireConfig = field(default_factory=VirtualWireConfig)
    sweep: CurrentSweepConfig = field(default_factory=CurrentSweepConfig)
    controller: RobustControllerConfig = field(default_factory=RobustControllerConfig)
    seed: int = 0
    expected_decision: str | None = None

    def validated(self) -> "VirtualWireScenario":
        if not self.name:
            raise ValueError("scenario name is required")
        self.wire.validated()
        self.sweep.validated()
        self.controller.validated()
        return self


@dataclass(frozen=True)
class MeasurementSample:
    sample_index: int
    elapsed_s: float
    current_ma: float
    motor_mm: float
    free_length_shift_mm: float
    transformation_fraction: float
    stress_mpa: float
    raw_stress_mpa: float
    load_g: float
    raw_load_g: float
    strain_pct: float
    status: str = "ok"
    safety_reason: str = ""

    def to_row(self) -> dict[str, Any]:
        return {
            "sample_index": self.sample_index,
            "elapsed_s": f"{self.elapsed_s:.6f}",
            "current_ma": f"{self.current_ma:.6f}",
            "motor_mm": f"{self.motor_mm:.9f}",
            "free_length_shift_mm": f"{self.free_length_shift_mm:.9f}",
            "transformation_fraction": f"{self.transformation_fraction:.6f}",
            "stress_mpa": f"{self.stress_mpa:.6f}",
            "raw_stress_mpa": f"{self.raw_stress_mpa:.6f}",
            "load_g": f"{self.load_g:.9f}",
            "raw_load_g": f"{self.raw_load_g:.9f}",
            "strain_pct": f"{self.strain_pct:.6f}",
            "status": self.status,
            "safety_reason": self.safety_reason,
        }


@dataclass(frozen=True)
class ControlDecision:
    elapsed_s: float
    decision: str
    result: str
    target_stress_mpa: float
    tolerance_mpa: float
    robust_center_mpa: float
    robust_noise_mpa: float
    error_mpa: float
    raw_min_mpa: float
    raw_max_mpa: float
    motor_step_mm: float
    reason: str
    window_samples: int

    def to_row(self) -> dict[str, Any]:
        return {
            "elapsed_s": f"{self.elapsed_s:.6f}",
            "automation_phase": "current_hold",
            "automation_basis": "stress_mpa",
            "automation_target_value": f"{self.target_stress_mpa:.6f}",
            "decision": self.decision,
            "result": self.result,
            "current_value": f"{self.robust_center_mpa:.6f}",
            "error_value": f"{self.error_mpa:.6f}",
            "filtered_noise": f"{self.robust_noise_mpa:.6f}",
            "raw_min_mpa": f"{self.raw_min_mpa:.6f}",
            "raw_max_mpa": f"{self.raw_max_mpa:.6f}",
            "tolerance": f"{self.tolerance_mpa:.6f}",
            "sensitivity_per_mm": "",
            "motor_step_mm": f"{self.motor_step_mm:.9f}",
            "result_detail": self.reason,
            "window_samples": self.window_samples,
        }


@dataclass(frozen=True)
class SimulationTrace:
    scenario: VirtualWireScenario
    samples: list[MeasurementSample]
    decisions: list[ControlDecision]
    stop_reason: str

    def summary(self) -> dict[str, Any]:
        final = self.decisions[-1] if self.decisions else None
        raw_max = max((sample.raw_stress_mpa for sample in self.samples), default=0.0)
        raw_min = min((sample.raw_stress_mpa for sample in self.samples), default=0.0)
        return {
            "scenario": self.scenario.name,
            "description": self.scenario.description,
            "seed": self.scenario.seed,
            "stop_reason": self.stop_reason,
            "sample_count": len(self.samples),
            "decision_count": len(self.decisions),
            "final_decision": None if final is None else final.decision,
            "final_result": None if final is None else final.result,
            "final_robust_center_mpa": None if final is None else final.robust_center_mpa,
            "final_error_mpa": None if final is None else final.error_mpa,
            "final_motor_step_mm": None if final is None else final.motor_step_mm,
            "raw_min_mpa": raw_min,
            "raw_max_mpa": raw_max,
            "expected_decision": self.scenario.expected_decision,
        }


def transformation_fraction(current_ma: float, wire: VirtualWireConfig, *, rising: bool = True) -> float:
    onset = wire.transformation_onset_ma
    end = wire.transformation_end_ma
    if not rising:
        onset -= wire.transformation_hysteresis_ma
        end -= wire.transformation_hysteresis_ma
    fraction = _clamp((current_ma - onset) / (end - onset), 0.0, 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * fraction)


def _current_at_elapsed(elapsed_s: float, sweep: CurrentSweepConfig) -> float:
    if sweep.start_ma == sweep.end_ma:
        return sweep.start_ma
    direction = 1.0 if sweep.end_ma > sweep.start_ma else -1.0
    current = sweep.start_ma + direction * abs(sweep.rate_ma_s) * elapsed_s
    low, high = sorted((sweep.start_ma, sweep.end_ma))
    return _clamp(current, low, high)


def _simulate_open_loop_samples(scenario: VirtualWireScenario) -> list[MeasurementSample]:
    scenario.validated()
    rng = random.Random(scenario.seed)
    wire = scenario.wire
    sweep = scenario.sweep
    sample_period_s = 1.0 / sweep.sample_hz
    ramp_duration_s = abs(sweep.end_ma - sweep.start_ma) / abs(sweep.rate_ma_s) if sweep.rate_ma_s else 0.0
    duration_s = ramp_duration_s + max(0.0, sweep.hold_s)
    sample_count = max(2, int(math.floor(duration_s / sample_period_s)) + 1)
    samples: list[MeasurementSample] = []
    previous_raw: float | None = None
    for sample_index in range(sample_count):
        elapsed_s = sample_index * sample_period_s
        current_ma = _current_at_elapsed(elapsed_s, sweep)
        fraction = transformation_fraction(current_ma, wire, rising=sweep.end_ma >= sweep.start_ma)
        free_shift_mm = wire.initial_free_length_shift_mm + fraction * wire.transformation_contraction_mm
        mechanical_mm = wire.initial_motor_mm + free_shift_mm
        base_stress = mechanical_mm * wire.elastic_stiffness_mpa_per_mm
        in_transition = 0.0 < fraction < 1.0
        fluctuation = 0.0
        if in_transition and wire.fluctuation_mpa:
            fluctuation = wire.fluctuation_mpa * math.sin(
                2.0 * math.pi * wire.fluctuation_cycles * fraction
            )
        drift = wire.drift_mpa_per_s * elapsed_s
        noise = rng.gauss(0.0, wire.noise_mpa) if wire.noise_mpa else 0.0
        spike = 0.0
        if wire.spike_every_samples and sample_index and sample_index % wire.spike_every_samples == 0:
            spike = wire.spike_mpa if (sample_index // wire.spike_every_samples) % 2 else -wire.spike_mpa
        stress = base_stress + fluctuation + drift + noise + spike
        raw_stress = stress
        if (
            wire.raw_extreme_mpa is not None
            and wire.raw_extreme_at_s is not None
            and abs(elapsed_s - wire.raw_extreme_at_s) <= sample_period_s / 2.0
        ):
            raw_stress = wire.raw_extreme_mpa
        strain_pct = mechanical_mm / wire.length_mm * 100.0
        status = "ok"
        safety_reason = ""
        if wire.break_stress_mpa is not None and raw_stress >= wire.break_stress_mpa:
            status = "wire_break"
            safety_reason = "break_stress"
        if wire.break_strain_pct is not None and abs(strain_pct) >= wire.break_strain_pct:
            status = "wire_break"
            safety_reason = safety_reason or "break_strain"
        if wire.break_travel_mm is not None and abs(wire.initial_motor_mm) >= wire.break_travel_mm:
            status = "wire_break"
            safety_reason = safety_reason or "break_travel"
        if previous_raw is not None and scenario.controller.absurd_jump_mpa is not None:
            if abs(raw_stress - previous_raw) >= scenario.controller.absurd_jump_mpa:
                status = "contact_loss"
                safety_reason = safety_reason or "absurd_jump"
        previous_raw = raw_stress
        samples.append(
            MeasurementSample(
                sample_index=sample_index,
                elapsed_s=elapsed_s,
                current_ma=current_ma,
                motor_mm=wire.initial_motor_mm,
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
        )
        if status in {"wire_break", "contact_loss"}:
            break
    return samples


def _safety_stop_for_window(samples: list[MeasurementSample], controller: RobustControllerConfig) -> str | None:
    if any(sample.status == "wire_break" for sample in samples):
        return "wire_break"
    if any(sample.status == "contact_loss" for sample in samples):
        return "contact_loss"
    raw_values = [sample.raw_stress_mpa for sample in samples]
    if controller.safety_max_stress_mpa is not None and max(raw_values, default=0.0) >= controller.safety_max_stress_mpa:
        return "max_stress"
    if controller.safety_min_stress_mpa is not None and min(raw_values, default=0.0) <= controller.safety_min_stress_mpa:
        return "min_stress"
    if controller.absurd_jump_mpa is not None:
        for previous, current in zip(raw_values, raw_values[1:]):
            if abs(current - previous) >= controller.absurd_jump_mpa:
                return "contact_loss"
    return None


def decide_robust_center(
    samples: list[MeasurementSample],
    controller: RobustControllerConfig,
) -> ControlDecision:
    controller.validated()
    if not samples:
        raise ValueError("at least one sample is required")
    latest = samples[-1]
    window_start = latest.elapsed_s - controller.window_s
    window = [sample for sample in samples if sample.elapsed_s >= window_start]
    if len(window) < 1:
        window = [latest]
    raw_values = [sample.raw_stress_mpa for sample in window]
    safety_reason = _safety_stop_for_window(window, controller)
    center = statistics.median(raw_values)
    noise = 1.4826 * _median_absolute_deviation(raw_values, center)
    error = center - controller.target_stress_mpa
    raw_min = min(raw_values)
    raw_max = max(raw_values)
    if safety_reason is not None:
        return ControlDecision(
            elapsed_s=latest.elapsed_s,
            decision="safety_stop",
            result=safety_reason,
            target_stress_mpa=controller.target_stress_mpa,
            tolerance_mpa=controller.tolerance_mpa,
            robust_center_mpa=center,
            robust_noise_mpa=noise,
            error_mpa=error,
            raw_min_mpa=raw_min,
            raw_max_mpa=raw_max,
            motor_step_mm=0.0,
            reason=f"raw safety rail: {safety_reason}",
            window_samples=len(window),
        )
    if latest.elapsed_s - window[0].elapsed_s > controller.stale_feedback_s and len(window) < 2:
        return ControlDecision(
            elapsed_s=latest.elapsed_s,
            decision="safety_stop",
            result="stale_feedback",
            target_stress_mpa=controller.target_stress_mpa,
            tolerance_mpa=controller.tolerance_mpa,
            robust_center_mpa=center,
            robust_noise_mpa=noise,
            error_mpa=error,
            raw_min_mpa=raw_min,
            raw_max_mpa=raw_max,
            motor_step_mm=0.0,
            reason="stale feedback window",
            window_samples=len(window),
        )
    noise_band = max(controller.tolerance_mpa, noise * controller.noise_sigma)
    if abs(error) <= noise_band and raw_min <= controller.target_stress_mpa <= raw_max:
        return ControlDecision(
            elapsed_s=latest.elapsed_s,
            decision="no_move",
            result="target_spanning_noise_cloud",
            target_stress_mpa=controller.target_stress_mpa,
            tolerance_mpa=controller.tolerance_mpa,
            robust_center_mpa=center,
            robust_noise_mpa=noise,
            error_mpa=error,
            raw_min_mpa=raw_min,
            raw_max_mpa=raw_max,
            motor_step_mm=0.0,
            reason="robust center is near target and cloud spans target",
            window_samples=len(window),
        )
    if abs(error) <= max(controller.tolerance_mpa, controller.min_recovery_mpa):
        return ControlDecision(
            elapsed_s=latest.elapsed_s,
            decision="no_move",
            result="inside_robust_band",
            target_stress_mpa=controller.target_stress_mpa,
            tolerance_mpa=controller.tolerance_mpa,
            robust_center_mpa=center,
            robust_noise_mpa=noise,
            error_mpa=error,
            raw_min_mpa=raw_min,
            raw_max_mpa=raw_max,
            motor_step_mm=0.0,
            reason="robust center is inside configured recovery band",
            window_samples=len(window),
        )
    sign = 1 if error > 0.0 else -1
    if controller.previous_error_sign and sign != controller.previous_error_sign:
        return ControlDecision(
            elapsed_s=latest.elapsed_s,
            decision="wait_reversal",
            result="sign_crossing_confirmation",
            target_stress_mpa=controller.target_stress_mpa,
            tolerance_mpa=controller.tolerance_mpa,
            robust_center_mpa=center,
            robust_noise_mpa=noise,
            error_mpa=error,
            raw_min_mpa=raw_min,
            raw_max_mpa=raw_max,
            motor_step_mm=controller.motor_step_mm,
            reason="robust error changed sign; wait or shrink step before reversing",
            window_samples=len(window),
        )
    stress_per_mm = max(1e-9, abs(controller.target_stress_mpa) / max(0.01, controller.max_correction_mm))
    correction_mm = _clamp(abs(error) / stress_per_mm, controller.motor_step_mm, controller.max_correction_mm)
    if abs(error) >= max(controller.min_recovery_mpa, controller.tolerance_mpa * 2.0):
        decision = "bias_recovery"
        result = "robust_center_far_from_target"
        reason = "robust center is biased away from target"
    else:
        decision = "bounded_correction"
        result = "small_robust_error"
        reason = "robust center is outside tolerance"
    return ControlDecision(
        elapsed_s=latest.elapsed_s,
        decision=decision,
        result=result,
        target_stress_mpa=controller.target_stress_mpa,
        tolerance_mpa=controller.tolerance_mpa,
        robust_center_mpa=center,
        robust_noise_mpa=noise,
        error_mpa=error,
        raw_min_mpa=raw_min,
        raw_max_mpa=raw_max,
        motor_step_mm=-sign * correction_mm,
        reason=reason,
        window_samples=len(window),
    )


def run_virtual_wire_scenario(scenario: VirtualWireScenario) -> SimulationTrace:
    samples = _simulate_open_loop_samples(scenario)
    decisions: list[ControlDecision] = []
    for index in range(len(samples)):
        decision = decide_robust_center(samples[: index + 1], scenario.controller)
        decisions.append(decision)
        if decision.decision == "safety_stop":
            return SimulationTrace(
                scenario=scenario,
                samples=samples[: index + 1],
                decisions=decisions,
                stop_reason=decision.result,
            )
    return SimulationTrace(
        scenario=scenario,
        samples=samples,
        decisions=decisions,
        stop_reason="completed",
    )


def scenario_by_name(name: str) -> VirtualWireScenario:
    scenarios = _scenario_map()
    try:
        return scenarios[name]
    except KeyError as exc:
        known = ", ".join(sorted(scenarios))
        raise ValueError(f"unknown Mini DMA wire simulator scenario {name!r}; known: {known}") from exc


def _scenario_map() -> dict[str, VirtualWireScenario]:
    base_sweep = CurrentSweepConfig(start_ma=35.0, end_ma=45.0, rate_ma_s=20.0, sample_hz=4.5, hold_s=2.0)
    return {
        "high_bias_cloud": VirtualWireScenario(
            name="high_bias_cloud",
            description="Noisy 60-70 MPa stress cloud against a 20 MPa target.",
            wire=VirtualWireConfig(
                initial_motor_mm=0.104,
                elastic_stiffness_mpa_per_mm=600.0,
                fluctuation_mpa=4.0,
                noise_mpa=0.8,
                transformation_contraction_mm=0.0,
            ),
            sweep=base_sweep,
            controller=RobustControllerConfig(target_stress_mpa=20.0, safety_max_stress_mpa=320.0),
            seed=11,
            expected_decision="bias_recovery",
        ),
        "wide_high_cloud": VirtualWireScenario(
            name="wide_high_cloud",
            description="Transformation-sized 10-300 MPa fluctuations whose robust center remains high.",
            wire=VirtualWireConfig(
                initial_motor_mm=0.258,
                elastic_stiffness_mpa_per_mm=600.0,
                fluctuation_mpa=145.0,
                fluctuation_cycles=1.0,
                noise_mpa=1.0,
                transformation_onset_ma=35.0,
                transformation_end_ma=45.0,
                transformation_contraction_mm=0.0,
            ),
            sweep=CurrentSweepConfig(start_ma=35.0, end_ma=45.0, rate_ma_s=5.0, sample_hz=4.5, hold_s=1.0),
            controller=RobustControllerConfig(target_stress_mpa=20.0, safety_max_stress_mpa=260.0),
            seed=17,
            expected_decision="safety_stop",
        ),
        "target_spanning_cloud": VirtualWireScenario(
            name="target_spanning_cloud",
            description="Noisy target-spanning cloud centered near 20 MPa.",
            wire=VirtualWireConfig(
                initial_motor_mm=0.0335,
                elastic_stiffness_mpa_per_mm=600.0,
                fluctuation_mpa=8.0,
                fluctuation_cycles=2.0,
                noise_mpa=0.4,
                transformation_onset_ma=35.0,
                transformation_end_ma=45.0,
                transformation_contraction_mm=0.0,
            ),
            sweep=base_sweep,
            controller=RobustControllerConfig(target_stress_mpa=20.0, safety_max_stress_mpa=320.0),
            seed=23,
            expected_decision="no_move",
        ),
        "transformation_bias": VirtualWireScenario(
            name="transformation_bias",
            description="Same-sign biased stress cloud from transformation contraction.",
            wire=VirtualWireConfig(
                initial_motor_mm=0.033,
                elastic_stiffness_mpa_per_mm=620.0,
                transformation_onset_ma=35.0,
                transformation_end_ma=45.0,
                transformation_contraction_mm=0.075,
                fluctuation_mpa=6.0,
                fluctuation_cycles=2.0,
                noise_mpa=0.7,
            ),
            sweep=base_sweep,
            controller=RobustControllerConfig(target_stress_mpa=20.0, safety_max_stress_mpa=320.0),
            seed=29,
            expected_decision="bias_recovery",
        ),
        "sign_crossing_reversal": VirtualWireScenario(
            name="sign_crossing_reversal",
            description="Robust center crosses target after a previous positive error.",
            wire=VirtualWireConfig(
                initial_motor_mm=0.025,
                elastic_stiffness_mpa_per_mm=600.0,
                fluctuation_mpa=1.5,
                noise_mpa=0.2,
                transformation_contraction_mm=0.0,
            ),
            sweep=base_sweep,
            controller=RobustControllerConfig(
                target_stress_mpa=20.0,
                safety_max_stress_mpa=320.0,
                previous_error_sign=1,
            ),
            seed=31,
            expected_decision="wait_reversal",
        ),
        "wire_break": VirtualWireScenario(
            name="wire_break",
            description="Raw stress exceeds the configured break/contact-loss rail.",
            wire=VirtualWireConfig(
                initial_motor_mm=0.034,
                elastic_stiffness_mpa_per_mm=600.0,
                transformation_onset_ma=35.0,
                transformation_end_ma=45.0,
                transformation_contraction_mm=0.12,
                break_stress_mpa=75.0,
                noise_mpa=0.1,
            ),
            sweep=base_sweep,
            controller=RobustControllerConfig(target_stress_mpa=20.0, safety_max_stress_mpa=320.0),
            seed=37,
            expected_decision="safety_stop",
        ),
    }


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


def write_simulation_outputs(trace: SimulationTrace, output_dir: Path | str) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    measurement_path = out / "measurement.csv"
    control_path = out / "control_trace.csv"
    summary_path = out / "summary.json"
    scenario_path = out / "scenario.json"
    _write_csv(measurement_path, (sample.to_row() for sample in trace.samples))
    _write_csv(control_path, (decision.to_row() for decision in trace.decisions))
    summary_path.write_text(json.dumps(trace.summary(), indent=2), encoding="utf-8")
    scenario_path.write_text(json.dumps(asdict(trace.scenario), indent=2), encoding="utf-8")
    return {
        "measurement": measurement_path,
        "control_trace": control_path,
        "summary": summary_path,
        "scenario": scenario_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Mini DMA virtual wire scenarios.")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=DEFAULT_SCENARIOS,
        help="Scenario to run. May be passed more than once. Defaults to all built-in scenarios.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Directory for CSV/JSON outputs.")
    args = parser.parse_args(argv)

    names = args.scenario or list(DEFAULT_SCENARIOS)
    summaries: list[dict[str, Any]] = []
    for name in names:
        trace = run_virtual_wire_scenario(scenario_by_name(name))
        summary = trace.summary()
        summaries.append(summary)
        if args.out is not None:
            scenario_out = args.out / name if len(names) > 1 else args.out
            write_simulation_outputs(trace, scenario_out)
    print(json.dumps({"scenarios": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
