"""Production TMA backend hosted exclusively in the control child.

The existing Prague/Košice recipe and hardware implementation remains the
single policy implementation.  This adapter constructs its ``MainWindow`` host
only inside the spawned process, never shows it, and publishes immutable
readback snapshots to the visible UI process.
"""

from __future__ import annotations

from collections import deque
from dataclasses import fields
import json
import math
import os
import time
from typing import Any, Mapping

from .control_process import (
    ControlPolicy,
    ControlStartRejected,
    ControlStartRequest,
    ReadbackValue,
)


CONFIG_SCHEMA_VERSION = 1


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
        "output_collision_action": str(
            getattr(window, "_controller_process_output_collision_action", "cancel")
        ),
        "cadence_downgrade_accepted": bool(cadence_downgrade_accepted),
        "elastocaloric_initial_baseline_s": getattr(
            window,
            "_elastocaloric_initial_baseline_s",
            None,
        ),
        "supply_lease_owner": str(
            getattr(window, "_supply_lease_owner", "")
        ),
        "parent_pid": os.getpid(),
        "force_control_profile": str(
            getattr(
                getattr(window, "_force_control_profile", lambda: "")(),
                "value",
                "",
            )
        ),
        "connected_sensors": {
            # Preserve optional live instrumentation across the ownership
            # handoff even when the selected recipe does not require that
            # sensor for control.  Stationary thermal diagnostics, for
            # example, deliberately do not require force feedback but should
            # continue recording it when the operator had the scale connected.
            "scale": getattr(window, "_scale_thread", None) is not None,
            "ir": getattr(window, "_ir_thread", None) is not None,
        },
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
                # QComboBox.currentData() is None for ordinary text-only
                # entries.  Searching for None therefore always selected the
                # first entry instead of the captured text (notably 600 baud
                # instead of the Košice KERN setting).
                index = candidate.findData(wanted) if wanted is not None else -1
                if index < 0:
                    wanted_text = str(raw_state.get("text", ""))
                    index = candidate.findText(wanted_text)
                if (
                    index < 0
                    and name in {"combo_scale_port", "combo_supply_port"}
                    and wanted not in {None, ""}
                ):
                    wanted_text = str(raw_state.get("text", "") or wanted)
                    candidate.addItem(wanted_text, wanted)
                    index = candidate.count() - 1
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
    ir_connected = getattr(window, "_ir_thread", None) is not None
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
        "IR connected" if ir_connected else "IR not required",
    ]
    return {
        "hardware_preflight_detail": "; ".join(detail_parts),
        "scale_connected": scale_connected,
        "scale_port": scale_port or None,
        "scale_baud": scale_baud or None,
        "supply_connected": supply_connected,
        "supply_endpoint": supply_endpoint or None,
        "tic_connected": tic_connected,
        "ir_connected": ir_connected,
    }


class ProductionTmaBackend:
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
        self._hardware_preflight: dict[str, ReadbackValue] = {}
        self._ui_log_sequence = 0
        self._ui_log_lines: deque[tuple[int, str]] = deque(maxlen=256)
        self._ir_preview_key: object | None = None
        self._ir_preview_json = ""

    def _capture_ui_log_line(self, line: str) -> None:
        self._ui_log_sequence += 1
        self._ui_log_lines.append(
            (self._ui_log_sequence, str(line)[:2000])
        )

    def start(self, request: ControlStartRequest) -> None:
        payload = json.loads(request.config_json)
        if int(payload.get("schema_version", 0)) != CONFIG_SCHEMA_VERSION:
            raise ValueError("unsupported production control payload schema")

        from PyQt6 import QtWidgets
        from .mini_dma_logger import MainWindow

        if bool(payload.get("continue_prepared_elastocaloric", False)):
            try:
                self._start_prepared_elastocaloric(request, payload)
            except ControlStartRejected:
                raise
            except Exception as exc:
                raise ControlStartRejected(str(exc)) from exc
            return

        self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        factory = self._window_factory or MainWindow
        self._window = factory(
            persist_settings=False,
            control_process_enabled=False,
            controller_process_mode=True,
        )
        self._window._control_process_log_sink = self._capture_ui_log_line
        _apply_window_configuration(self._window, payload)
        payload_hook = getattr(
            self._window,
            "_apply_controller_process_payload",
            None,
        )
        if callable(payload_hook):
            payload_hook(payload)
        initial_baseline_s = payload.get("elastocaloric_initial_baseline_s")
        self._window._elastocaloric_initial_baseline_s = (
            None
            if initial_baseline_s is None
            else max(0.0, float(initial_baseline_s))
        )
        stationary_preparation = payload.get("stationary_thermal_preparation")
        self._window._stationary_thermal_preparation_config = (
            dict(stationary_preparation)
            if isinstance(stationary_preparation, Mapping)
            else None
        )
        self._window._supply_lease_owner = str(
            payload.get("supply_lease_owner") or ""
        )
        if not self._window._supply_lease_owner:
            # Compatibility for tests and serialized requests created before
            # the owner token was added. New UI requests always provide it.
            self._window._supply_lease_owner = (
                f"tma-session-{request.identity.session_id}"
            )
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
        self._window._controller_process_output_collision_action = str(
            payload.get("output_collision_action", "cancel")
        )
        captured_profile = str(payload.get("force_control_profile") or "").casefold()
        if captured_profile:
            captured_policy_matches = (
                request.policy is ControlPolicy.PRAGUE
                and "prague" in captured_profile
            ) or (
                request.policy is ControlPolicy.KOSICE
                and "kosice" in captured_profile
            )
            if not captured_policy_matches:
                raise ValueError(
                    "TMA IPC policy does not match the captured UI hardware "
                    f"profile ({request.policy.value} vs {captured_profile})."
                )
        starting_length = payload.get("starting_length_mm")
        if isinstance(starting_length, (int, float)) and math.isfinite(float(starting_length)):
            starting_length_value = float(starting_length)
            length_widget = getattr(self._window, "spin_initial_length", None)
            if starting_length_value > 0.0 and length_widget is not None:
                length_widget.setValue(starting_length_value)
        steps, _summary, _interval_ms = self._window._build_automation_recipe()
        if not self._window._preflight_recipe_hardware(steps, show_progress=False):
            log_output = getattr(self._window, "log_output", None)
            log_text = (
                str(log_output.toPlainText()).strip()
                if log_output is not None and hasattr(log_output, "toPlainText")
                else ""
            )
            recent_log = " | ".join(log_text.splitlines()[-12:])
            detail = (
                str(getattr(self._window, "_controller_process_error", "")).strip()
                or "controller-process hardware preflight failed"
            )
            if recent_log:
                detail = f"{detail} Child log: {recent_log}"
            raise RuntimeError(detail)
        connected_sensors = payload.get("connected_sensors")
        parent_scale_connected = bool(
            isinstance(connected_sensors, Mapping)
            and connected_sensors.get("scale", False)
        )
        if (
            parent_scale_connected
            and getattr(self._window, "_scale_thread", None) is None
        ):
            ensure_scale = getattr(
                self._window,
                "_ensure_scale_ready_for_recipe",
                None,
            )
            if not callable(ensure_scale) or not bool(ensure_scale()):
                raise RuntimeError(
                    "controller-process scale handoff could not restore the "
                    "parent's connected scale"
                )
        # Hardware auto-detection can update the scale protocol and therefore
        # the Prague/Košice force-control profile. Validate the IPC policy only
        # after the child has reconstructed and probed its actual hardware;
        # checking the constructor defaults here incorrectly classified a
        # Košice KERN setup as Prague before the probe ran.
        profile_method = getattr(self._window, "_force_control_profile", None)
        if callable(profile_method):
            selected_profile = profile_method()
            profile_value = str(
                getattr(selected_profile, "value", selected_profile)
            ).casefold()
            policy_matches = (
                request.policy is ControlPolicy.PRAGUE
                and "prague" in profile_value
            ) or (
                request.policy is ControlPolicy.KOSICE
                and "kosice" in profile_value
            )
            if not policy_matches:
                raise ValueError(
                    "TMA IPC policy does not match the reconstructed hardware "
                    f"profile ({request.policy.value} vs {profile_value})."
                )
        self._window._controller_process_hardware_preflight_complete = True
        should_connect_ir = getattr(
            self._window,
            "_manual_auto_connect_should_connect_ir",
            lambda: False,
        )
        if (
            bool(should_connect_ir())
            and getattr(self._window, "_ir_thread", None) is None
            and not self._window._connect_ir_thermometer(show_errors=False)
        ):
            raise RuntimeError(
                "controller-process IR acquisition could not be started"
            )
        self._hardware_preflight = _hardware_preflight_readback(self._window)
        requires_starting_length = any(
            step.action == "starting_length_prompt" for step in steps
        )
        if starting_length is None and requires_starting_length:
            raise ValueError(
                "mounted starting length must be collected by the visible UI "
                "before hardware ownership is transferred"
            )
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
        if self._window._stationary_thermal_preparation_config is not None:
            target_current_mA = float(
                self._window._stationary_thermal_preparation_config.get(
                    "target_current_mA",
                    self._window.spin_elastocaloric_hold_mA.value(),
                )
            )
            self._window._elastocaloric_prepared_baseline_mm = float(
                self._window._current_position_mm
            )
            self._window._elastocaloric_prepared_current_mA = target_current_mA
            self._window._elastocaloric_release_confirmed = True
        self._started = True
        self._stopped = False

    def _start_prepared_elastocaloric(
        self,
        request: ControlStartRequest,
        payload: Mapping[str, object],
    ) -> None:
        window = self._require_window()
        thermal_response = payload.get("thermal_response_diagnostic")
        is_stationary_thermal_response = isinstance(thermal_response, Mapping)
        if not self._stopped or bool(window._automation_active):
            raise RuntimeError("prepared elastocaloric controller is not idle")
        if not bool(getattr(window, "_elastocaloric_prepared_ready", False)):
            raise RuntimeError("the previous run did not leave a reusable prepared baseline")
        prepared_position = getattr(window, "_elastocaloric_prepared_baseline_mm", None)
        prepared_current = getattr(window, "_elastocaloric_prepared_current_mA", None)
        if prepared_position is None or prepared_current is None:
            raise RuntimeError("prepared baseline readback is incomplete")
        _apply_window_configuration(window, payload)
        requested_current = float(window.spin_elastocaloric_hold_mA.value())
        if abs(requested_current - float(prepared_current)) > 0.05:
            raise RuntimeError(
                "prepared continuation cannot change the hold current "
                f"({prepared_current:.2f} mA prepared, {requested_current:.2f} mA requested)"
            )
        window._refresh_tic_status()
        position_tolerance_mm = 1.0 / max(1.0, float(window.spin_steps_per_mm.value()))
        if abs(float(window._current_position_mm) - float(prepared_position)) > position_tolerance_mm:
            raise RuntimeError(
                "motor is no longer at the confirmed prepared baseline position"
            )
        supply_snapshot = window._refresh_supply_snapshot(force=True)
        supply_current = supply_snapshot.get("current_mA")
        if supply_current is None or abs(float(supply_current) - float(prepared_current)) > 0.5:
            raise RuntimeError(
                "CH4 current is not confirmed at the prepared hold setpoint"
            )
        if not is_stationary_thermal_response and not window._has_fresh_scale_reading():
            raise RuntimeError("scale feedback is not fresh for the next jump")
        ir_snapshot = window._latest_ir_snapshot()
        ir_age_s = ir_snapshot.get("sample_age_s")
        if ir_age_s is None or float(ir_age_s) > 1.0:
            raise RuntimeError("thermal-camera feedback is not fresh for the next jump")
        window._thermal_response_diagnostic_config = (
            dict(thermal_response) if isinstance(thermal_response, Mapping) else None
        )
        window._thermal_response_roi_sums = None
        window._thermal_response_roi_count = 0
        window._thermal_response_roi_indices = ()
        # Preparation is a one-shot recipe.  Leaving this flag set makes every
        # retained run enter the preparation ramp again instead of the requested
        # jump or stationary diagnostic.
        window._stationary_thermal_preparation_config = None
        window._elastocaloric_continue_prepared_requested = True
        window._controller_process_prior_run_preflight_complete = True
        window._controller_process_hardware_preflight_complete = True
        window._start_auto_ramp()
        self._drain_events()
        if not window._automation_active:
            raise RuntimeError("prepared elastocaloric jump did not start")
        if window._thermal_response_diagnostic_config is not None:
            # This recipe never moves the motor away from the prepared baseline.
            # Marking the return contract here lets normal completion perform the
            # same fresh CH4 verification and retained-controller handoff as a
            # completed pull/release cycle.
            window._elastocaloric_release_confirmed = True
        self._started = True
        self._stopped = False

    def tick(self, now_s: float) -> None:
        tick_hook = getattr(
            self._require_window(),
            "_controller_process_tick_hook",
            None,
        )
        if callable(tick_hook):
            tick_hook(now_s)
        self._drain_events()

    def pause(self) -> None:
        self._require_window()._pause_recipe()
        self._drain_events()

    def resume(self) -> None:
        self._require_window()._resume_paused_recipe()
        self._drain_events()

    def stop(self) -> None:
        window = self._require_window()
        # A confirmed normal recipe stop preserves the separately configured
        # motor-supply channel. Emergency/crash/close paths retain the default
        # fail-safe behavior and turn it off.
        window._preserve_motor_supply_on_close = True
        if window._automation_active:
            window._stop_auto_ramp(
                log_completion=True,
                user_initiated=True,
                offer_recovery=False,
            )
        self._stopped = True
        self._drain_events()

    def emergency_stop(self, reason: str) -> None:
        self._emergency_reason = str(reason)
        window = self._window
        if window is None:
            return
        window._preserve_motor_supply_on_close = False
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

    def set_current_hold_bypass(self, enabled: bool) -> tuple[bool, str]:
        window = self._window
        if window is None:
            return (not enabled), "controller is not running"
        setter = getattr(window, "_set_current_hold_bypass_active", None)
        if not callable(setter):
            return False, "controller does not support current-hold bypass"
        return setter(bool(enabled))

    def start_stress_recovery(
        self,
        target_stress_mpa: float,
        reason: str,
    ) -> tuple[bool, str]:
        """Start a child-owned, current-off recovery after a bench guard trip."""

        window = self._require_window()
        try:
            window._disable_supply_output()
        except Exception as exc:
            self._last_error = str(exc)
            return False, f"failed to disable current before stress recovery: {exc}"
        starter = getattr(window, "start_bench_stress_recovery", None)
        if not callable(starter):
            return False, "controller does not support bench stress recovery"
        accepted = bool(
            starter(
                float(target_stress_mpa),
                reason=str(reason),
            )
        )
        self._drain_events()
        return (
            accepted,
            (
                f"stress recovery toward {float(target_stress_mpa):.3f} MPa started"
                if accepted
                else "stress recovery preflight was rejected"
            ),
        )

    def _latest_ir_preview_json(self, window: Any) -> str:
        """Return one immutable, cached camera frame for latest-value UI IPC."""

        frame = getattr(window, "_latest_ir_frame", None)
        if frame is None:
            self._ir_preview_key = None
            self._ir_preview_json = ""
            return ""
        key = (
            getattr(frame, "sequence", None),
            getattr(frame, "elapsed_ms", None),
            id(frame),
        )
        if key == self._ir_preview_key:
            return self._ir_preview_json
        try:
            values = tuple(float(value) for value in getattr(frame, "values", ()))
            width = int(getattr(frame, "width", 0) or 0)
            height = int(getattr(frame, "height", 0) or 0)
            if not values or width <= 0 or height <= 0 or len(values) != width * height:
                return ""
            payload = {
                "elapsed_ms": getattr(frame, "elapsed_ms", None),
                "ambient_c": getattr(frame, "ambient_c", None),
                "values": [round(value, 4) if math.isfinite(value) else None for value in values],
                "unit": str(getattr(frame, "unit", "C") or "C"),
                "raw_read_us": getattr(frame, "raw_read_us", None),
                "sequence": getattr(frame, "sequence", None),
                "flags": int(getattr(frame, "flags", 0) or 0),
                "width": width,
                "height": height,
                "roi_start_col": int(getattr(frame, "roi_start_col", 0) or 0),
            }
            preview_json = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, OverflowError):
            return ""
        self._ir_preview_key = key
        self._ir_preview_json = preview_json
        return preview_json

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
        try:
            speed_mm_s = window._live_speed_values().get("speed_mm_s")
        except Exception:
            speed_mm_s = None
        terminal_readback = getattr(window, "_recipe_terminal_readback", None)
        if not bool(window._automation_active) and isinstance(
            terminal_readback,
            Mapping,
        ):
            if terminal_readback.get("load_g") is not None:
                effective_load = float(terminal_readback["load_g"])
            if terminal_readback.get("stress_mpa") is not None:
                stress = float(terminal_readback["stress_mpa"])
        snapshot = getattr(window, "_supply_snapshot", {})
        try:
            scale_summary = window._scale_signal_buffer.recent_summary(
                now_s=time.time(),
                window_s=1.0,
            )
        except Exception:
            scale_summary = None
        try:
            ir_snapshot = dict(window._latest_ir_snapshot())
        except Exception:
            ir_snapshot = {}
        session_path = getattr(window, "_session_base_path", None)
        stop_metadata_reader = getattr(window, "_session_stop_metadata", None)
        stop_metadata = (
            dict(stop_metadata_reader())
            if callable(stop_metadata_reader)
            else {}
        )
        base_readback: tuple[tuple[str, ReadbackValue], ...] = (
            ("backend_owner_pid", self._owner_pid),
            ("started", self._started),
            ("stopped", self._stopped),
            ("hardware_preflight_complete", bool(self._started)),
            ("automation_active", bool(window._automation_active)),
            ("automation_paused", bool(window._automation_paused)),
            (
                "current_hold_bypass_active",
                bool(getattr(window, "_current_hold_bypass_active", False)),
            ),
            (
                "elastocaloric_release_confirmed",
                bool(getattr(window, "_elastocaloric_release_confirmed", False)),
            ),
            (
                "preserve_current_supply_on_close",
                bool(getattr(window, "_preserve_current_supply_on_close", False)),
            ),
            (
                "elastocaloric_prepared_ready",
                bool(getattr(window, "_elastocaloric_prepared_ready", False)),
            ),
            (
                "elastocaloric_prepared_output_confirmed",
                bool(
                    getattr(
                        window,
                        "_elastocaloric_prepared_output_confirmed",
                        False,
                    )
                ),
            ),
            (
                "elastocaloric_prepared_baseline_mm",
                getattr(window, "_elastocaloric_prepared_baseline_mm", None),
            ),
            (
                "current_hold_fluctuation_classification",
                str(
                    getattr(
                        window,
                        "_current_hold_fluctuation_classification",
                        "inactive",
                    )
                ),
            ),
            ("automation_phase", str(window._automation_phase)),
            ("automation_name", str(window._automation_name)),
            ("automation_index", int(window._automation_index)),
            (
                "session_logging_enabled",
                bool(getattr(window, "_session_logging_enabled", False)),
            ),
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
            (
                "fatigue_cycle_index",
                int(getattr(window, "_fatigue_cycle_index", 0)),
            ),
            (
                "fatigue_cycles_completed",
                int(getattr(window, "_fatigue_cycles_completed", 0)),
            ),
            (
                "fatigue_cycle_limit",
                getattr(window, "_fatigue_cycle_limit", None),
            ),
            (
                "fatigue_cycle_leg",
                str(getattr(window, "_automation_fatigue_leg", "") or ""),
            ),
            ("task", str(window._current_task_summary())),
            ("position_mm", float(window._current_position_mm)),
            ("load_g", effective_load),
            ("stress_mpa", None if stress is None else float(stress)),
            (
                "speed_mm_s",
                None if speed_mm_s is None else float(speed_mm_s),
            ),
            ("scale_age_s", window._scale_reading_age_s()),
            ("scale_raw_g", getattr(window, "_latest_scale_value_g", None)),
            (
                "scale_rate_hz",
                None if scale_summary is None else scale_summary.sample_rate_hz,
            ),
            (
                "scale_std_g",
                None if scale_summary is None else scale_summary.load_std_g,
            ),
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
            ("session_stop_reason", stop_metadata.get("reason")),
            ("session_stop_category", stop_metadata.get("category")),
            ("session_stop_label", stop_metadata.get("label")),
            ("session_stop_detail", stop_metadata.get("detail")),
            ("tic_vin_v", window._last_tic_vin_v),
            ("tic_position_steps", getattr(window, "_current_position_steps", None)),
            ("tic_status_text", str(getattr(window, "_tic_status_text", "") or "")),
            ("position_reference_mm", getattr(window, "_position_reference_mm", None)),
            ("last_move_target_mm", getattr(window, "_last_move_target_mm", None)),
            ("ir_preview_json", self._latest_ir_preview_json(window)),
            ("ir_sample_age_s", ir_snapshot.get("sample_age_s")),
            ("ir_sample_rate_hz", ir_snapshot.get("sample_rate_hz")),
            ("ir_frame_min_c", ir_snapshot.get("frame_min_c")),
            ("ir_frame_mean_c", ir_snapshot.get("frame_mean_c")),
            ("ir_frame_max_c", ir_snapshot.get("frame_max_c")),
            ("ir_ambient_c", ir_snapshot.get("ambient_c")),
            ("emergency_reason", self._emergency_reason),
            ("error", self._last_error),
            (
                "ui_log_tail_json",
                json.dumps(
                    list(self._ui_log_lines)[-32:],
                    separators=(",", ":"),
                ),
            ),
            ("ui_log_sequence", self._ui_log_sequence),
        ) + tuple(self._hardware_preflight.items())
        if not bool(window._automation_active) and isinstance(
            terminal_readback,
            Mapping,
        ):
            terminal_plot_readback = tuple(
                (str(key), value)
                for key, value in terminal_readback.items()
                if str(key).startswith("plot_")
            )
            if terminal_plot_readback:
                return base_readback + terminal_plot_readback
        capture_plot_point = getattr(window, "_capture_live_plot_point", None)
        plot_point = capture_plot_point() if callable(capture_plot_point) else None
        if plot_point is None:
            # A stationary thermal diagnostic is allowed to proceed without a
            # live scale timestamp. Its authoritative scheduled measurement
            # points still contain current, temperature, phase, and the last
            # known mechanical values, so publish the newest one instead of
            # leaving the visible dashboard blank.
            session_points = getattr(window, "_session_points", ())
            if session_points:
                plot_point = session_points[-1]
        if plot_point is None:
            return base_readback
        try:
            plot_fields = fields(plot_point)
        except TypeError:
            return base_readback
        plot_readback = tuple(
            (
                f"plot_{field.name}",
                _json_scalar(getattr(plot_point, field.name)),
            )
            for field in plot_fields
        )
        return base_readback + plot_readback

    def completion_detail(self) -> str | None:
        if (
            self._started
            and self._window is not None
            and not self._window._automation_active
        ):
            self.set_current_hold_bypass(False)
            # Normal recipe completion has the same motor-supply policy as an
            # operator Stop: preserve the separately configured motor channel.
            self._window._preserve_motor_supply_on_close = True
            is_elastocaloric_mode = getattr(
                self._window,
                "_is_elastocaloric_mode",
                None,
            )
            if (
                callable(is_elastocaloric_mode)
                and is_elastocaloric_mode(
                    getattr(self._window, "_automation_name", None)
                )
                and bool(
                    getattr(
                        self._window,
                        "_elastocaloric_release_confirmed",
                        False,
                    )
                )
            ):
                channel = self._window._current_sweep_supply_channel()
                output_on = (
                    None
                    if channel is None
                    else self._window._supply_channel_output_state(channel)
                )
                supply_snapshot = self._window._refresh_supply_snapshot(force=True)
                measured_current = supply_snapshot.get("current_mA")
                prepared_current = getattr(
                    self._window, "_elastocaloric_prepared_current_mA", None
                )
                if prepared_current is None:
                    prepared_current = getattr(
                        self._window, "_supply_last_setpoint_mA", None
                    )
                output_confirmed = bool(
                    output_on is True
                    and measured_current is not None
                    and prepared_current is not None
                    and abs(float(measured_current) - float(prepared_current)) <= 0.5
                )
                self._window._elastocaloric_prepared_output_confirmed = output_confirmed
                self._window._elastocaloric_prepared_ready = output_confirmed
                self._window._preserve_current_supply_on_close = output_confirmed
                if not output_confirmed:
                    self._capture_ui_log_line(
                        "Elastocaloric release returned to baseline, but CH4 output/current "
                        "was not freshly confirmed; prepared continuation was rejected."
                    )
            self._stopped = True
            return "production recipe completed"
        return None

    def close(self) -> None:
        window = self._window
        if window is not None:
            try:
                self.set_current_hold_bypass(False)
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


def create_production_backend() -> ProductionTmaBackend:
    return ProductionTmaBackend()


# Compatibility alias for older imports. New code uses ProductionTmaBackend.
ProductionMiniDmaBackend = ProductionTmaBackend


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "ProductionMiniDmaBackend",
    "ProductionTmaBackend",
    "capture_runtime_configuration",
    "capture_window_configuration",
    "create_production_backend",
]
