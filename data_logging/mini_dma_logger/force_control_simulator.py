"""Deterministic, hardware-free simulation of :mod:`force_control`.

The plant families are expressed relative to their own load scale.  Gram and
millimetre values exist only at the boundary required by ``ForceControlPolicy``;
all cross-family error and target comparisons are dimensionless.
"""

from __future__ import annotations

import math
import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .force_control import (
    ForceControlAction,
    ForceControlConfig,
    ForceControlInput,
    ForceControlIntent,
    ForceControlPolicy,
    ForceControlProfile,
)


_COMMAND_ACTIONS = {
    ForceControlAction.MOVE_RELATIVE,
    ForceControlAction.PROBE_RELATIVE,
}


@dataclass(frozen=True, slots=True)
class ForceControlPlantFamily:
    """Scaled first-order plant and feedback characteristics."""

    name: str
    load_scale_g: float
    load_per_mm_g: float
    initial_gain_ratio: float = 0.8
    target_normalized: float = 1.0
    initial_load_normalized: float = 0.25
    tolerance_normalized: float = 0.012
    noise_normalized: float = 0.002
    quantization_normalized: float = 0.002
    response_delay_steps: int = 1
    response_observation_steps: int = 4
    disturbance_normalized: float = 0.0
    disturbance_start_step: int = 45
    disturbance_ramp_steps: int = 6
    motor_resolution_fraction: float = 0.002
    max_command_normalized: float = 0.28
    sample_period_s: float = 0.1
    max_steps: int = 240
    settle_samples: int = 5

    def validated(self) -> "ForceControlPlantFamily":
        positive = (
            self.load_scale_g,
            self.load_per_mm_g,
            self.target_normalized,
            self.tolerance_normalized,
            self.motor_resolution_fraction,
            self.max_command_normalized,
            self.sample_period_s,
        )
        if any(not math.isfinite(value) or value <= 0.0 for value in positive):
            raise ValueError("plant scale, gain, target, and resolutions must be positive")
        non_negative = (
            self.initial_load_normalized,
            self.noise_normalized,
            self.quantization_normalized,
            self.disturbance_normalized,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in non_negative):
            raise ValueError("initial load, feedback limits, and disturbance must be non-negative")
        if self.initial_gain_ratio <= 0.0 or not math.isfinite(self.initial_gain_ratio):
            raise ValueError("initial_gain_ratio must be positive")
        if self.response_delay_steps < 1 or self.disturbance_ramp_steps < 1:
            raise ValueError("response delays must be positive")
        if self.response_observation_steps < 0:
            raise ValueError("response observation steps must be non-negative")
        if self.disturbance_start_step < 1 or self.max_steps < 1 or self.settle_samples < 1:
            raise ValueError("simulation step counts must be positive")
        return self

    @property
    def target_load_g(self) -> float:
        return self.target_normalized * self.load_scale_g

    @property
    def motor_resolution_mm(self) -> float:
        return self.motor_resolution_fraction * self.load_scale_g / self.load_per_mm_g

    @property
    def max_safe_correction_mm(self) -> float:
        return self.max_command_normalized * self.load_scale_g / self.load_per_mm_g


@dataclass(frozen=True, slots=True)
class ForceControlSimulationSample:
    step: int
    normalized_load: float
    normalized_error: float
    command_mm: float
    command_normalized: float
    command_in_flight: bool
    disturbance_normalized: float
    action: ForceControlAction


@dataclass(frozen=True, slots=True)
class ForceControlSimulationMetrics:
    family: str
    completed: bool
    completion_step: int | None
    p95_normalized_error: float
    max_command_mm: float
    max_command_normalized: float
    command_count: int
    overlap_count: int
    max_commands_in_flight: int
    recovered: bool
    recovery_steps: int | None


@dataclass(frozen=True, slots=True)
class ForceControlSimulationResult:
    family: ForceControlPlantFamily
    metrics: ForceControlSimulationMetrics
    samples: tuple[ForceControlSimulationSample, ...]


def scaled_plant_families() -> tuple[ForceControlPlantFamily, ...]:
    """Return representative families with no shared absolute target."""

    return (
        ForceControlPlantFamily(
            name="responsive_low_scale",
            load_scale_g=0.8,
            load_per_mm_g=3.2,
            response_delay_steps=1,
        ),
        ForceControlPlantFamily(
            name="quantized_medium_gain",
            load_scale_g=7.5,
            load_per_mm_g=9.0,
            noise_normalized=0.004,
            quantization_normalized=0.008,
            tolerance_normalized=0.016,
            response_delay_steps=2,
        ),
        ForceControlPlantFamily(
            name="delayed_high_scale",
            load_scale_g=42.0,
            load_per_mm_g=18.0,
            noise_normalized=0.003,
            quantization_normalized=0.004,
            response_delay_steps=5,
        ),
        ForceControlPlantFamily(
            name="transformation_recovery",
            load_scale_g=3.0,
            load_per_mm_g=1.8,
            noise_normalized=0.003,
            quantization_normalized=0.003,
            response_delay_steps=3,
            disturbance_normalized=0.22,
            disturbance_start_step=50,
            disturbance_ramp_steps=8,
            max_steps=300,
        ),
    )


def _deterministic_noise(step: int, amplitude: float) -> float:
    pattern = (0.0, 0.55, -0.35, 0.9, -0.7, 0.25, -0.15, 0.45, -0.5)
    return amplitude * pattern[step % len(pattern)]


def _quantize(value: float, quantum: float) -> float:
    if quantum <= 0.0:
        return value
    return round(value / quantum) * quantum


def _percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = math.ceil(0.95 * len(ordered)) - 1
    return ordered[max(0, index)]


def simulate_force_control_family(
    family: ForceControlPlantFamily,
) -> ForceControlSimulationResult:
    """Exercise the adaptive policy against one deterministic plant family."""

    family = family.validated()
    policy = ForceControlPolicy(
        ForceControlConfig(
            profile=ForceControlProfile.KOSICE_ADAPTIVE,
            initial_load_per_mm_g=family.load_per_mm_g * family.initial_gain_ratio,
            minimum_gain_windows=2,
            minimum_gain_confidence=0.35,
        )
    )
    position_mm = family.initial_load_normalized * family.load_scale_g / family.load_per_mm_g
    pending_command_mm = 0.0
    pending_steps = 0
    observation_steps_remaining = 0
    command_count = 0
    overlap_count = 0
    max_commands_in_flight = 0
    settled = 0
    completion_step: int | None = None
    recovery_steps: int | None = None
    samples: list[ForceControlSimulationSample] = []

    disturbance_end = family.disturbance_start_step + family.disturbance_ramp_steps
    requires_recovery = family.disturbance_normalized > 0.0

    for step in range(family.max_steps):
        if pending_steps > 0:
            pending_steps -= 1
            if pending_steps == 0:
                position_mm += pending_command_mm
                pending_command_mm = 0.0
                observation_steps_remaining = family.response_observation_steps
        elif observation_steps_remaining > 0:
            observation_steps_remaining -= 1

        if requires_recovery and step >= family.disturbance_start_step:
            fraction = min(
                1.0,
                (step - family.disturbance_start_step + 1) / family.disturbance_ramp_steps,
            )
            disturbance = family.disturbance_normalized * fraction
        else:
            disturbance = 0.0

        true_normalized_load = (
            family.load_per_mm_g * position_mm / family.load_scale_g - disturbance
        )
        measured_normalized = _quantize(
            true_normalized_load + _deterministic_noise(step, family.noise_normalized),
            family.quantization_normalized,
        )
        command_in_flight = pending_steps > 0
        intent = (
            ForceControlIntent.RECOVER_DISTURBANCE
            if requires_recovery and step >= family.disturbance_start_step
            else ForceControlIntent.ACQUIRE_TARGET
        )
        decision = policy.decide(
            ForceControlInput(
                intent=intent,
                target_load_g=family.target_load_g,
                current_load_g=measured_normalized * family.load_scale_g,
                filtered_load_g=measured_normalized * family.load_scale_g,
                tolerance_g=family.tolerance_normalized * family.load_scale_g,
                robust_noise_g=family.noise_normalized * family.load_scale_g,
                quantization_g=family.quantization_normalized * family.load_scale_g,
                readability_g=family.quantization_normalized * family.load_scale_g,
                position_mm=position_mm,
                motor_resolution_mm=family.motor_resolution_mm,
                max_safe_correction_mm=family.max_safe_correction_mm,
                speed_mm_s=family.max_safe_correction_mm / family.sample_period_s,
                target_ramp_g_s=0.0,
                ramp_active=False,
                current_mA=1.0 if intent is ForceControlIntent.RECOVER_DISTURBANCE else 0.0,
                current_changing=(
                    requires_recovery
                    and family.disturbance_start_step <= step < disturbance_end
                ),
                feedback_fresh=True,
                motor_complete=not command_in_flight,
                timestamp_s=(step + 1) * family.sample_period_s,
                context_key=family.name,
                response_observation_complete=observation_steps_remaining == 0,
            )
        )

        command_mm = decision.correction_mm if decision.action in _COMMAND_ACTIONS else 0.0
        if decision.action in _COMMAND_ACTIONS:
            command_count += 1
            if command_in_flight:
                overlap_count += 1
            else:
                pending_command_mm = command_mm
                pending_steps = family.response_delay_steps
        commands_in_flight = int(pending_steps > 0)
        max_commands_in_flight = max(max_commands_in_flight, commands_in_flight)

        normalized_error = family.target_normalized - measured_normalized
        command_normalized = command_mm * family.load_per_mm_g / family.load_scale_g
        samples.append(
            ForceControlSimulationSample(
                step=step,
                normalized_load=measured_normalized,
                normalized_error=normalized_error,
                command_mm=command_mm,
                command_normalized=command_normalized,
                command_in_flight=commands_in_flight > 0,
                disturbance_normalized=disturbance,
                action=decision.action,
            )
        )

        after_final_disturbance = not requires_recovery or step >= disturbance_end
        if (
            after_final_disturbance
            and abs(normalized_error)
            <= decision.effective_deadband_g / family.load_scale_g + 1e-12
        ):
            settled += 1
        else:
            settled = 0
        if (
            settled >= family.settle_samples
            and pending_steps == 0
            and observation_steps_remaining == 0
        ):
            completion_step = step
            if requires_recovery:
                recovery_steps = step - disturbance_end + 1
            break

    absolute_errors = [abs(sample.normalized_error) for sample in samples]
    max_command_mm = max((abs(sample.command_mm) for sample in samples), default=0.0)
    max_command_normalized = max(
        (abs(sample.command_normalized) for sample in samples),
        default=0.0,
    )
    completed = completion_step is not None
    metrics = ForceControlSimulationMetrics(
        family=family.name,
        completed=completed,
        completion_step=completion_step,
        p95_normalized_error=_percentile_95(absolute_errors),
        max_command_mm=max_command_mm,
        max_command_normalized=max_command_normalized,
        command_count=command_count,
        overlap_count=overlap_count,
        max_commands_in_flight=max_commands_in_flight,
        recovered=completed if requires_recovery else True,
        recovery_steps=recovery_steps,
    )
    return ForceControlSimulationResult(family=family, metrics=metrics, samples=tuple(samples))


def simulate_scaled_plant_families() -> tuple[ForceControlSimulationResult, ...]:
    """Run every standard family in a stable order."""

    return tuple(simulate_force_control_family(family) for family in scaled_plant_families())


def simulation_report() -> dict[str, object]:
    """Return a machine-readable summary of the standard normalized campaign."""

    results = simulate_scaled_plant_families()
    return {
        "schema_version": 1,
        "policy": ForceControlProfile.KOSICE_ADAPTIVE.value,
        "all_completed": all(result.metrics.completed for result in results),
        "all_commands_serialized": all(result.metrics.overlap_count == 0 for result in results),
        "families": [asdict(result.metrics) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run normalized Košice force-control simulations.")
    parser.add_argument("--out", type=Path, help="Optional JSON report path.")
    args = parser.parse_args()
    payload = simulation_report()
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload["all_completed"] and payload["all_commands_serialized"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
