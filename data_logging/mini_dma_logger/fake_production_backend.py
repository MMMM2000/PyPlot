"""Deterministic fake-hardware host for spawned TMA production tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import csv
from typing import Any, Mapping

from PyQt6 import QtWidgets

from .production_control_backend import ProductionTmaBackend


class _Profile(str, Enum):
    PRAGUE = "prague_legacy"
    KOSICE = "kosice_adaptive"


@dataclass
class _Point:
    elapsed_s: float
    load_g: float
    stress_mpa: float
    position_mm: float
    current_measured_mA: float
    voltage_V: float


class FakeProductionTmaWindow:
    """Small non-visual stand-in with fake scale, Tic, PSU, IR, and CSV."""

    def __init__(self, **_kwargs: object) -> None:
        self._policy = _Profile.PRAGUE
        self._automation_active = False
        self._automation_paused = False
        self._automation_phase = "idle"
        self._automation_name = "fake TMA"
        self._automation_steps: list[object] = []
        self._automation_index = 0
        self._automation_total_steps = 4
        self._automation_completed_ticks = 0
        self._supply_output_enabled = False
        self._supply_last_setpoint_mA = 1.0
        self._supply_last_measured_mA = 1.0
        self._supply_last_voltage_V = 0.2
        self._supply_effective_readback_hz = 2.0
        self._supply_snapshot = {
            "current_mA": self._supply_last_measured_mA,
            "voltage_V": self._supply_last_voltage_V,
        }
        self._supply_controller: Any = None
        self._scale_thread: Any = None
        self._ir_thread: Any = None
        self._tic_controller: Any = None
        self._tic_command_dispatcher: Any = None
        self._session_active = False
        self._session_logging_enabled = True
        self._session_points: list[object] = []
        self._session_base_path: Path | None = None
        self._last_tic_vin_v = 12.0
        self._latest_scale_value_g = 0.5
        self._current_position_mm = 0.0
        self._preserve_motor_supply_on_close = False
        self._controller_process_error = ""
        self._control_process_log_sink = None
        self._ticks_remaining = 20
        self._elapsed_s = 0.0
        self._csv_path: Path | None = None
        self.spin_initial_length = QtWidgets.QDoubleSpinBox()
        self.spin_initial_length.setValue(50.0)

    def _apply_controller_process_payload(
        self,
        payload: Mapping[str, object],
    ) -> None:
        policy = str(payload.get("fake_policy") or "prague").casefold()
        self._policy = (
            _Profile.KOSICE if policy == "kosice" else _Profile.PRAGUE
        )
        output_dir = str(payload.get("fake_output_dir") or "").strip()
        if output_dir:
            path = Path(output_dir)
            path.mkdir(parents=True, exist_ok=True)
            self._csv_path = path / "fake_measurement.csv"
            with self._csv_path.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(
                    ("elapsed_s", "policy", "load_g", "position_mm")
                )

    def _force_control_profile(self) -> _Profile:
        return self._policy

    def _build_automation_recipe(self) -> tuple[list[object], str, int]:
        self._automation_steps = [type("Step", (), {"action": "wait"})()]
        return (self._automation_steps, self._automation_name, 20)

    def _preflight_recipe_hardware(
        self,
        _steps: list[object],
        *,
        show_progress: bool,
    ) -> bool:
        assert show_progress is False
        self._scale_thread = object()
        self._tic_controller = object()
        self._supply_controller = _ConnectedSupply()
        return True

    def _manual_auto_connect_should_connect_ir(self) -> bool:
        return True

    def _connect_ir_thermometer(self, *, show_errors: bool) -> bool:
        assert show_errors is False
        self._ir_thread = object()
        return True

    def set_length_setup_automation_values(
        self,
        *,
        starting_length_mm: float | None,
        preload_length_mm: float | None,
    ) -> None:
        del starting_length_mm, preload_length_mm

    def _start_auto_ramp(self) -> None:
        self._automation_active = True
        self._session_active = True
        self._supply_output_enabled = True
        self._automation_phase = "running"

    def _controller_process_tick_hook(self, _now_s: float) -> None:
        if not self._automation_active or self._automation_paused:
            return
        self._elapsed_s += 0.02
        self._automation_completed_ticks += 1
        self._automation_index = min(
            self._automation_total_steps,
            self._automation_completed_ticks,
        )
        self._current_position_mm += 0.01
        self._ticks_remaining -= 1
        if self._csv_path is not None:
            with self._csv_path.open("a", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(
                    (
                        f"{self._elapsed_s:.3f}",
                        self._policy.value,
                        "0.500",
                        f"{self._current_position_mm:.3f}",
                    )
                )
        if self._ticks_remaining <= 0:
            self._automation_active = False
            self._session_active = False
            self._supply_output_enabled = False
            self._automation_phase = "complete"

    def _pause_recipe(self) -> None:
        self._automation_paused = True
        self._supply_output_enabled = False

    def _resume_paused_recipe(self) -> None:
        self._automation_paused = False
        self._supply_output_enabled = True

    def _stop_auto_ramp(self, **_kwargs: object) -> None:
        self._automation_active = False
        self._session_active = False
        self._supply_output_enabled = False
        self._automation_phase = "stopped"

    def _disable_supply_output(self) -> None:
        self._supply_output_enabled = False

    def _disable_motor_supply_output(self) -> None:
        self._preserve_motor_supply_on_close = False

    def _apply_current_sweep_pending_overrides(
        self,
        *,
        show_message: bool,
    ) -> bool:
        return not show_message

    def _current_effective_load_g(self) -> float:
        return self._latest_scale_value_g

    def _current_distribution_value(self, _basis: str) -> float:
        return 25.0

    def _scale_reading_age_s(self) -> float:
        return 0.01

    def _current_task_summary(self) -> str:
        return self._automation_phase

    def _capture_live_plot_point(self) -> _Point:
        return _Point(
            elapsed_s=self._elapsed_s,
            load_g=self._latest_scale_value_g,
            stress_mpa=25.0,
            position_mm=self._current_position_mm,
            current_measured_mA=self._supply_last_measured_mA,
            voltage_V=self._supply_last_voltage_V,
        )

    def close(self) -> None:
        return None


class _ConnectedSupply:
    port_name = "fake-process-broker"

    def is_connected(self) -> bool:
        return True


def create_fake_production_backend() -> ProductionTmaBackend:
    return ProductionTmaBackend(window_factory=FakeProductionTmaWindow)


__all__ = [
    "FakeProductionTmaWindow",
    "create_fake_production_backend",
]
