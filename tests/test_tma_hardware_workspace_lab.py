from __future__ import annotations

import os
import sys

from PyQt6 import QtWidgets

from experiments import EXPERIMENTS
from experiments import tma_hardware_workspace_lab as lab


def _ensure_app() -> QtWidgets.QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    lab.install_palette(app)
    return app


def test_hardware_study_is_registered() -> None:
    assert "TMA UI Design Lab - Hardware Workspace" in EXPERIMENTS


def test_all_bench_states_update_device_and_ownership_surfaces() -> None:
    app = _ensure_app()
    window = lab.HardwareWorkspaceWindow("disconnected")
    try:
        window.show()
        app.processEvents()
        assert window.headline.text() == "Bench disconnected"
        assert window.device_rows["motor"].status_label.text() == "Disconnected"

        window.set_state("partial")
        assert window.primary_button.text() == "Retry failed"
        assert window.device_rows["motor"].status_label.text() == "Needs attention"
        assert window.device_rows["supply"].summary_label.text() == "CH3 + CH4 reserved"

        window.set_state("ready")
        assert window.headline.text() == "Bench ready"
        assert window.motion_panel.up_button.isEnabled()
        assert window.motion_panel.down_button.isEnabled()

        window.set_state("active")
        assert not window.settings_button.isEnabled()
        assert not window.edit_safety_button.isEnabled()
        assert not window.motion_panel.up_button.isEnabled()
        assert "TMA recipe" in window.owner_label.text()
        assert all(not row.action_button.isVisible() for row in window.device_rows.values())
        assert not window.primary_button.isVisible()
        assert "locked" in window.motion_panel.connection_hint.text().lower()
    finally:
        window.close()
        app.processEvents()


def test_connect_and_retry_actions_reach_ready_state() -> None:
    app = _ensure_app()
    window = lab.HardwareWorkspaceWindow("disconnected")
    try:
        window.show()
        app.processEvents()
        window.primary_button.click()
        assert window.current_state.key == "ready"
        window.set_state("partial")
        window.primary_button.click()
        assert window.current_state.key == "ready"
    finally:
        window.close()
        app.processEvents()


def test_manual_motion_has_only_routine_controls_and_contextual_stop() -> None:
    app = _ensure_app()
    window = lab.HardwareWorkspaceWindow("ready")
    try:
        window.show()
        app.processEvents()
        assert window.motion_panel.up_button.text() == "Increase tension"
        assert window.motion_panel.down_button.text() == "Relax"
        assert not window.motion_panel.stop_button.isVisible()
        window.motion_panel.set_motion("increasing tension")
        app.processEvents()
        assert window.motion_panel.stop_button.isVisible()
        window.motion_panel.stop_button.click()
        assert window.motion_panel._motion == "idle"  # noqa: SLF001 - prototype state assertion
        visible_text = " ".join(
            button.text() for button in window.motion_panel.findChildren(QtWidgets.QPushButton)
        )
        for removed in (
            "Move displacement to 0",
            "Move load to 0",
            "Capture zero-load",
            "Record point now",
            "Refresh Tic status",
        ):
            assert removed not in visible_text
    finally:
        window.close()
        app.processEvents()


def test_hardware_and_safety_settings_are_separate_and_locked_during_run() -> None:
    app = _ensure_app()
    window = lab.HardwareWorkspaceWindow("ready")
    try:
        window.show()
        app.processEvents()
        hardware = window.open_hardware_settings()
        assert hardware is not None
        assert [hardware.tabs.tabText(i) for i in range(hardware.tabs.count())] == [
            "Connections",
            "Device profiles",
            "Optional IR",
            "Service and diagnostics",
        ]
        service_text = " ".join(
            button.text() for button in hardware.findChildren(QtWidgets.QPushButton)
        )
        assert "Physically tare scale" in service_text
        assert "Manual power-supply output" in service_text
        hardware.close()

        safety = window.open_safety()
        assert isinstance(safety, lab.SafetyDialog)
        safety.close()

        window.set_state("active")
        assert window.open_hardware_settings() is None
        assert window.open_safety() is None
    finally:
        window.close()
        app.processEvents()


def test_device_details_expand_without_changing_bench_state() -> None:
    app = _ensure_app()
    window = lab.HardwareWorkspaceWindow("ready")
    try:
        window.show()
        app.processEvents()
        row = window.device_rows["scale"]
        assert not row.details.isVisible()
        row.expand_button.click()
        app.processEvents()
        assert row.details.isVisible()
        assert window.current_state.key == "ready"
    finally:
        window.close()
        app.processEvents()
