"""Production Mini DMA backend hosted exclusively in the control child.

The existing Prague/Košice recipe and hardware implementation remains the
single policy implementation.  This adapter constructs its ``MainWindow`` host
only inside the spawned process, never shows it, and publishes immutable
readback snapshots to the visible UI process.
"""

from __future__ import annotations

from dataclasses import fields
import json
import os
from typing import Any, Mapping

from .control_process import ControlStartRequest, ReadbackValue


CONFIG_SCHEMA_VERSION = 1
STARTING_LENGTH_INPUT = "starting_length_mm"


def _json_scalar(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _capture_widget_state(candidate: object) -> dict[str, object] | None:
    from PyQt6 import QtWidgets

    if isinstance(candidate, QtWidgets.QComboBox):
        return {
            "kind": "combo",
            "index": int(candidate.currentIndex()),
            "data": _json_scalar(candidate.currentData()),
            "text": candidate.currentText(),
        }
    if isinstance(candidate, QtWidgets.QSpinBox):
        return {"kind": "integer_spin", "value": int(candidate.value())}
    if isinstance(candidate, QtWidgets.QDoubleSpinBox):
        return {"kind": "decimal_spin", "value": float(candidate.value())}
    if isinstance(candidate, QtWidgets.QLineEdit):
        return {"kind": "line", "text": candidate.text()}
    if isinstance(candidate, QtWidgets.QPlainTextEdit):
        return {"kind": "plain", "text": candidate.toPlainText()}
    if isinstance(candidate, QtWidgets.QAbstractButton):
        return {"kind": "button", "checked": bool(candidate.isChecked())}
    if isinstance(candidate, QtWidgets.QTabWidget):
        return {"kind": "tabs", "index": int(candidate.currentIndex())}
    return None


def capture_window_configuration(
    window: object,
    *,
    starting_length_mm: float | None,
    cadence_downgrade_accepted: bool,
) -> str:
    """Capture operator-visible configuration without retaining Qt objects."""

    widgets: dict[str, dict[str, object]] = {}
    for name, candidate in vars(window).items():
        state = _capture_widget_state(candidate)
        if state is not None:
            widgets[name] = state
    decision = getattr(window, "_first_overheating_preflight_decision", None)
    payload = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "widgets": widgets,
        "starting_length_mm": starting_length_mm,
        "first_overheating_preflight_decision": (
            decision if isinstance(decision, Mapping) else None
        ),
        "prior_run_preflight_complete": True,
        "cadence_downgrade_accepted": bool(cadence_downgrade_accepted),
        "parent_pid": os.getpid(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def capture_runtime_configuration(window: object) -> str:
    """Capture only controls that a running current sweep permits editing."""

    editable_ids = {
        id(widget) for widget in window._current_sweep_runtime_editable_widgets()
    }
    widgets = {
        name: state
        for name, candidate in vars(window).items()
        if id(candidate) in editable_ids
        if (state := _capture_widget_state(candidate)) is not None
    }
    payload = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "runtime_update": True,
        "widgets": widgets,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def capture_starting_length_response(starting_length_mm: float) -> str:
    """Serialize the sole operator response accepted during child startup."""

    payload = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "operator_response": STARTING_LENGTH_INPUT,
        "value": float(starting_length_mm),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _apply_window_configuration(window: object, payload: Mapping[str, object]) -> None:
    from PyQt6 import QtCore, QtWidgets

    widgets = payload.get("widgets")
    if not isinstance(widgets, Mapping):
        raise ValueError("production control payload has no widget state")
    for name, raw_state in widgets.items():
        if not isinstance(name, str) or not isinstance(raw_state, Mapping):
            continue
        candidate = getattr(window, name, None)
        kind = str(raw_state.get("kind", ""))
        blocker = (
            QtCore.QSignalBlocker(candidate)
            if isinstance(candidate, QtCore.QObject)
            else None
        )
        try:
            if kind == "combo" and isinstance(candidate, QtWidgets.QComboBox):
                wanted = raw_state.get("data")
                index = candidate.findData(wanted)
                if index < 0:
                    wanted_text = str(raw_state.get("text", ""))
                    index = candidate.findText(wanted_text)
                if index < 0:
                    index = int(raw_state.get("index", -1))
                if 0 <= index < candidate.count():
                    candidate.setCurrentIndex(index)
            elif kind == "integer_spin" and isinstance(
                candidate,
                QtWidgets.QSpinBox,
            ):
                candidate.setValue(int(raw_state.get("value", 0)))
            elif kind == "decimal_spin" and isinstance(
                candidate,
                QtWidgets.QDoubleSpinBox,
            ):
                candidate.setValue(float(raw_state.get("value", 0.0)))
            elif kind == "line" and isinstance(candidate, QtWidgets.QLineEdit):
                candidate.setText(str(raw_state.get("text", "")))
            elif kind == "plain" and isinstance(candidate, QtWidgets.QPlainTextEdit):
                candidate.setPlainText(str(raw_state.get("text", "")))
            elif kind == "button" and isinstance(
                candidate,
                QtWidgets.QAbstractButton,
            ):
                if candidate.isCheckable():
                    candidate.setChecked(bool(raw_state.get("checked", False)))
            elif kind == "tabs" and isinstance(candidate, QtWidgets.QTabWidget):
                index = int(raw_state.get("index", -1))
                if 0 <= index < candidate.count():
                    candidate.setCurrentIndex(index)
        finally:
            del blocker


def _hardware_preflight_readback(window: object) -> dict[str, ReadbackValue]:
    scale_port = str(
        getattr(getattr(window, "combo_scale_port", None), "currentData", lambda: "")()
        or ""
    )
    scale_baud = str(
        getattr(getattr(window, "combo_scale_baud", None), "currentText", lambda: "")()
        or ""
    )
    supply = getattr(window, "_supply_controller", None)
    supply_connected = False
    if supply is not None:
        try:
            supply_connected = bool(supply.is_connected())
        except Exception:
            supply_connected = False
    supply_endpoint = str(getattr(supply, "port_name", "") or "")
    scale_connected = getattr(window, "_scale_thread", None) is not None
    tic_connected = (
        getattr(window, "_tic_controller", None) is not None
        or getattr(window, "_tic_command_dispatcher", None) is not None
    )
    detail_parts = [
        (
            f"scale {scale_port or 'connected'}"
            + (f" at {scale_baud} baud" if scale_baud else "")
            if scale_connected
            else "scale not required"
        ),
        (
            f"PSU {supply_endpoint or 'connected'}"
            if supply_connected
            else "PSU not required"
        ),
        "Tic connected" if tic_connected else "Tic not required",
    ]
    return {
        "hardware_preflight_detail": "; ".join(detail_parts),
        "scale_connected": scale_connected,
        "scale_port": scale_port or None,
        "scale_baud": scale_baud or None,
        "supply_connected": supply_connected,
        "supply_endpoint": supply_endpoint or None,
        "tic_connected": tic_connected,
    }


class ProductionMiniDmaBackend:
    """Child-owned adapter around the existing production controller."""

    def __init__(self, *, window_factory: object | None = None) -> None:
        self._app: Any | None = None
        self._window: Any | None = None
        self._owner_pid = os.getpid()
        self._started = False
        self._stopped = False
        self._emergency_reason = ""
        self._last_error = ""
        self._window_factory = window_factory
        self._awaiting_starting_length = False
        self._hardware_preflight: dict[str, ReadbackValue] = {}

    def start(self, request: ControlStartRequest) -> None:
        payload = json.loads(request.config_json)
        if int(payload.get("schema_version", 0)) != CONFIG_SCHEMA_VERSION:
            raise ValueError("unsupported production control payload schema")

        from PyQt6 import QtWidgets
        from .mini_dma_logger import MainWindow

        self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        factory = self._window_factory or MainWindow
        self._window = factory(
            persist_settings=False,
            control_process_enabled=False,
            controller_process_mode=True,
        )
        _apply_window_configuration(self._window, payload)
        self._window._controller_process_cadence_downgrade_accepted = bool(
            payload.get("cadence_downgrade_accepted", False)
        )
        decision = payload.get("first_overheating_preflight_decision")
        self._window._first_overheating_preflight_decision = (
            dict(decision) if isinstance(decision, Mapping) else None
        )
        self._window._controller_process_prior_run_preflight_complete = bool(
            payload.get("prior_run_preflight_complete", False)
        )
        starting_length = payload.get("starting_length_mm")
        steps, _summary, _interval_ms = self._window._build_automation_recipe()
        if not self._window._preflight_recipe_hardware(steps, show_progress=False):
            detail = (
                str(getattr(self._window, "_controller_process_error", "")).strip()
                or "controller-process hardware preflight failed"
            )
            raise RuntimeError(detail)
        self._hardware_preflight = _hardware_preflight_readback(self._window)
        self._window._controller_process_hardware_preflight_complete = True
        requires_starting_length = any(
            step.action == "starting_length_prompt" for step in steps
        )
        if starting_length is None and requires_starting_length:
            self._awaiting_starting_length = True
            self._started = True
            self._stopped = False
            self._drain_events()
            return
        self._window.set_length_setup_automation_values(
            starting_length_mm=(
                None if starting_length is None else float(starting_length)
            ),
            preload_length_mm=None,
        )
        self._window._start_auto_ramp()
        self._drain_events()
        if not self._window._automation_active:
            detail = (
                str(getattr(self._window, "_controller_process_error", "")).strip()
                or "controller process did not start the recipe"
            )
            raise RuntimeError(detail)
        self._started = True
        self._stopped = False
        self._awaiting_starting_length = False

    def tick(self, now_s: float) -> None:
        del now_s
        self._drain_events()

    def pause(self) -> None:
        self._require_window()._pause_recipe()
        self._drain_events()

    def resume(self) -> None:
        self._require_window()._resume_paused_recipe()
        self._drain_events()

    def stop(self) -> None:
        window = self._require_window()
        if window._automation_active:
            window._stop_auto_ramp(
                log_completion=True,
                user_initiated=True,
                offer_recovery=False,
            )
        elif self._awaiting_starting_length:
            window._disable_supply_output()
            window._disable_motor_supply_output()
        self._awaiting_starting_length = False
        self._stopped = True
        self._drain_events()

    def emergency_stop(self, reason: str) -> None:
        self._emergency_reason = str(reason)
        self._awaiting_starting_length = False
        window = self._window
        if window is None:
            return
        try:
            if window._automation_active:
                window._stop_auto_ramp(
                    log_completion=False,
                    user_initiated=False,
                    offer_recovery=False,
                    stop_reason="control_process_emergency",
                    stop_detail=self._emergency_reason,
                )
        finally:
            try:
                window._disable_supply_output()
            except Exception as exc:
                self._last_error = str(exc)
            try:
                window._disable_motor_supply_output()
            except Exception as exc:
                self._last_error = str(exc)
        self._stopped = True
        self._drain_events()

    def update_config(self, config_json: str) -> tuple[bool, str]:
        window = self._require_window()
        payload = json.loads(config_json)
        if int(payload.get("schema_version", 0)) != CONFIG_SCHEMA_VERSION:
            return False, "unsupported runtime configuration schema"
        if payload.get("operator_response") == STARTING_LENGTH_INPUT:
            if not self._awaiting_starting_length:
                return False, "starting length is not currently requested"
            try:
                starting_length_mm = float(payload["value"])
            except (KeyError, TypeError, ValueError):
                return False, "starting length response is invalid"
            if not 0.001 <= starting_length_mm <= 100000.0:
                return False, "starting length is outside the allowed range"
            window.set_length_setup_automation_values(
                starting_length_mm=starting_length_mm,
                preload_length_mm=None,
            )
            window._start_auto_ramp()
            self._drain_events()
            if not window._automation_active:
                detail = (
                    str(getattr(window, "_controller_process_error", "")).strip()
                    or "controller process did not start the recipe"
                )
                return False, detail
            self._awaiting_starting_length = False
            return True, "mounted starting length accepted; production recipe started"
        if payload.get("runtime_update") is not True:
            return False, "configuration update is not marked as runtime-safe"
        _apply_window_configuration(window, payload)
        accepted = bool(
            window._apply_current_sweep_pending_overrides(show_message=False)
        )
        self._drain_events()
        return (
            accepted,
            (
                "current-sweep runtime settings applied"
                if accepted
                else "current-sweep runtime settings were rejected or unchanged"
            ),
        )

    def readback(self) -> tuple[tuple[str, ReadbackValue], ...]:
        window = self._window
        if window is None:
            return (
                ("backend_owner_pid", self._owner_pid),
                ("started", self._started),
                ("stopped", self._stopped),
                ("emergency_reason", self._emergency_reason),
                ("error", self._last_error),
            )
        try:
            effective_load = float(window._current_effective_load_g())
        except Exception:
            effective_load = None
        try:
            stress = window._current_distribution_value("stress_mpa")
        except Exception:
            stress = None
        snapshot = getattr(window, "_supply_snapshot", {})
        session_path = getattr(window, "_session_base_path", None)
        base_readback: tuple[tuple[str, ReadbackValue], ...] = (
            ("backend_owner_pid", self._owner_pid),
            ("started", self._started),
            ("stopped", self._stopped),
            (
                "operator_input_required",
                STARTING_LENGTH_INPUT if self._awaiting_starting_length else None,
            ),
            (
                "operator_input_default",
                (
                    float(window.spin_initial_length.value())
                    if self._awaiting_starting_length
                    else None
                ),
            ),
            ("hardware_preflight_complete", bool(self._started)),
            ("automation_active", bool(window._automation_active)),
            ("automation_paused", bool(window._automation_paused)),
            ("automation_phase", str(window._automation_phase)),
            ("automation_name", str(window._automation_name)),
            ("automation_index", int(window._automation_index)),
            (
                "automation_completed",
                int(getattr(window, "_automation_completed_ticks", window._automation_index)),
            ),
            (
                "automation_total",
                int(
                    getattr(
                        window,
                        "_automation_total_steps",
                        len(window._automation_steps),
                    )
                ),
            ),
            ("task", str(window._current_task_summary())),
            ("position_mm", float(window._current_position_mm)),
            ("load_g", effective_load),
            ("stress_mpa", None if stress is None else float(stress)),
            ("scale_age_s", window._scale_reading_age_s()),
            ("supply_output_enabled", bool(window._supply_output_enabled)),
            (
                "supply_setpoint_mA",
                None
                if window._supply_last_setpoint_mA is None
                else float(window._supply_last_setpoint_mA),
            ),
            ("supply_current_mA", snapshot.get("current_mA")),
            ("supply_voltage_V", snapshot.get("voltage_V")),
            (
                "supply_effective_hz",
                float(window._supply_effective_readback_hz),
            ),
            ("session_active", bool(window._session_active)),
            ("session_points", int(len(window._session_points))),
            ("session_path", None if session_path is None else str(session_path)),
            ("tic_vin_v", window._last_tic_vin_v),
            ("emergency_reason", self._emergency_reason),
            ("error", self._last_error),
        ) + tuple(self._hardware_preflight.items())
        capture_plot_point = getattr(window, "_capture_live_plot_point", None)
        plot_point = capture_plot_point() if callable(capture_plot_point) else None
        if plot_point is None:
            return base_readback
        plot_readback = tuple(
            (
                f"plot_{field.name}",
                _json_scalar(getattr(plot_point, field.name)),
            )
            for field in fields(plot_point)
        )
        return base_readback + plot_readback

    def completion_detail(self) -> str | None:
        if (
            self._started
            and not self._awaiting_starting_length
            and self._window is not None
            and not self._window._automation_active
        ):
            self._stopped = True
            return "production recipe completed"
        return None

    def close(self) -> None:
        window = self._window
        if window is not None:
            try:
                if window._automation_active:
                    self.emergency_stop("control process closing")
            finally:
                window.close()
                self._drain_events()
        self._window = None

    def _require_window(self) -> Any:
        if self._window is None:
            raise RuntimeError("production control window is not initialized")
        return self._window

    def _drain_events(self) -> None:
        app = self._app
        if app is not None:
            app.processEvents()


def create_production_backend() -> ProductionMiniDmaBackend:
    return ProductionMiniDmaBackend()


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "ProductionMiniDmaBackend",
    "capture_starting_length_response",
    "capture_runtime_configuration",
    "capture_window_configuration",
    "create_production_backend",
]
