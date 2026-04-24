from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip(
    "PyQt6.QtWidgets",
    reason="Qt widgets backend is unavailable",
    exc_type=ImportError,
)

from PyQt6 import QtCore, QtGui, QtWidgets

mini_dma_mod = importlib.import_module(
    "data_logging.mini_dma_logger.mini_dma_logger"
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _snapshot_settings() -> dict[str, object]:
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    return {key: settings.value(key) for key in settings.allKeys()}


def _restore_settings(snapshot: dict[str, object]) -> None:
    settings = QtCore.QSettings("microwire", "mini_dma_logger")
    settings.clear()
    for key, value in snapshot.items():
        settings.setValue(key, value)
    settings.sync()


def _build_window(tmp_path: Path, qtbot) -> mini_dma_mod.MainWindow:
    _ensure_app()
    snapshot = _snapshot_settings()
    window = mini_dma_mod.MainWindow(log_dir=str(tmp_path))
    window._test_settings_snapshot = snapshot  # type: ignore[attr-defined]
    qtbot.addWidget(window)
    window.check_zero_position_on_start.setChecked(False)
    window.check_tare_on_start.setChecked(False)
    return window


def _close_test_window(window: mini_dma_mod.MainWindow) -> None:
    snapshot = getattr(window, "_test_settings_snapshot", None)
    window.close()
    _ensure_app().processEvents()
    if isinstance(snapshot, dict):
        _restore_settings(snapshot)


def _wheel_event(delta_y: int = -120) -> QtGui.QWheelEvent:
    return QtGui.QWheelEvent(
        QtCore.QPointF(10.0, 10.0),
        QtCore.QPointF(10.0, 10.0),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, delta_y),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_move_command_keeps_confirmed_position_until_status_refresh(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeController:
        def __init__(self) -> None:
            self.target_steps: int | None = None

        def set_target_position(self, position_steps: int) -> None:
            self.target_steps = position_steps

    controller = _FakeController()
    window._build_tic_controller = lambda: controller  # type: ignore[method-assign]
    window._current_position_mm = 1.25
    window._current_position_steps = 125
    window.spin_steps_per_mm.setValue(100.0)

    try:
        moved = window._move_to_position_mm(2.0)

        assert moved is True
        assert controller.target_steps == 200
        assert window._current_position_mm == pytest.approx(1.25)
        assert window._current_position_steps == 125
        assert window._last_move_target_mm == pytest.approx(2.0)
    finally:
        _close_test_window(window)


def test_settings_wheel_guard_scrolls_panel_without_changing_values(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        scrollbar = window._control_scroll_area.verticalScrollBar()
        scrollbar.setRange(0, 100)
        scrollbar.setValue(50)

        window.spin_ramp_distance.setValue(1.0)
        assert window.eventFilter(window.spin_ramp_distance, _wheel_event()) is True
        assert window.spin_ramp_distance.value() == pytest.approx(1.0)
        assert scrollbar.value() > 50

        scrollbar.setValue(50)
        assert window.eventFilter(window.spin_ramp_distance.lineEdit(), _wheel_event()) is True
        assert window.spin_ramp_distance.value() == pytest.approx(1.0)
        assert scrollbar.value() > 50

        combo = window.combo_recipe_mode
        combo.setCurrentIndex(0)
        assert window.eventFilter(combo, _wheel_event()) is True
        assert combo.currentIndex() == 0
    finally:
        _close_test_window(window)


def test_settings_panel_avoids_horizontal_scrolling(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        assert window._control_scroll_area.horizontalScrollBarPolicy() == (
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.edit_run_notes.horizontalScrollBarPolicy() == (
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.log_output.horizontalScrollBarPolicy() == (
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.edit_run_notes.lineWrapMode() == QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth
        assert window.log_output.lineWrapMode() == QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth
    finally:
        _close_test_window(window)


def test_distribution_seek_rejects_stale_scale_readings(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)
    window._latest_scale_value_g = 12.0
    window._latest_scale_timestamp = time.time() - (
        mini_dma_mod.STALE_SCALE_AFTER_S + 5.0
    )

    called = False

    def _fail_if_called(_target_mm: float) -> bool:
        nonlocal called
        called = True
        return True

    window._move_to_position_mm = _fail_if_called  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError, match="stale"):
            window._seek_distribution_target(
                mini_dma_mod.HSW_BASIS_LOAD_G,
                target_value=15.0,
                tolerance=0.5,
            )
        assert called is False
    finally:
        _close_test_window(window)


def test_session_metadata_keeps_original_created_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qtbot,
) -> None:
    window = _build_window(tmp_path, qtbot)
    window.edit_log_name.setText("metadata_smoke")
    window._record_current_point = lambda: None  # type: ignore[method-assign]

    values = iter(
        [
            "2026-04-23 10:00:00",
            "2026-04-23 10:05:00",
            "2026-04-23 10:10:00",
            "2026-04-23 10:15:00",
        ]
    )

    def _fake_timestamp() -> str:
        try:
            return next(values)
        except StopIteration:
            return "2026-04-23 10:15:00"

    monkeypatch.setattr(mini_dma_mod, "_utc_timestamp", _fake_timestamp)

    try:
        window._start_session()
        assert window._session_json_path is not None
        first_payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))

        window._write_session_metadata()
        second_payload = json.loads(window._session_json_path.read_text(encoding="utf-8"))

        assert first_payload["created_utc"] == second_payload["created_utc"]
        window._stop_session()
    finally:
        _close_test_window(window)


def test_find_ticcmd_detects_localappdata_pololu_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_app_data = tmp_path / "LocalAppData"
    ticcmd_path = local_app_data / "Programs" / "Pololu" / "Tic" / "bin" / "ticcmd.exe"
    ticcmd_path.parent.mkdir(parents=True)
    ticcmd_path.write_bytes(b"")

    monkeypatch.setattr(mini_dma_mod.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert mini_dma_mod._find_ticcmd() == str(ticcmd_path)


def test_auto_detect_scale_port_applies_detected_settings(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        monkeypatch.setattr(
            mini_dma_mod.list_ports,
            "comports",
            lambda: [
                SimpleNamespace(device="COM6", description="USB Serial A"),
                SimpleNamespace(device="COM3", description="USB Serial B"),
            ],
        )

        def _probe_scale(port_name: str):
            if port_name == "COM6":
                return {
                    "port": "COM6",
                    "baudrate": 9600,
                    "request_command": "\\x1bp",
                    "terminator": "",
                    "raw_text": "0.000 g",
                }
            return None

        monkeypatch.setattr(window, "_probe_scale_candidate", _probe_scale)

        window._refresh_scale_ports()
        detected = window._auto_detect_scale_port()

        assert detected is True
        assert window.combo_scale_port.currentData() == "COM6"
        assert window.combo_scale_baud.currentText() == "9600"
        assert window.edit_scale_request.text() == "\\x1bp"
        assert window.edit_scale_terminator.text() == ""
    finally:
        _close_test_window(window)


def test_auto_detect_supply_port_applies_detected_settings(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        monkeypatch.setattr(
            mini_dma_mod.list_ports,
            "comports",
            lambda: [
                SimpleNamespace(device="COM6", description="USB Serial A"),
                SimpleNamespace(device="COM3", description="USB Serial B"),
            ],
        )

        def _probe_supply(port_name: str):
            if port_name == "COM3":
                return {
                    "port": "COM3",
                    "baudrate": 115200,
                    "profile_id": "hmp4030",
                    "idn_text": "HAMEG,HMP4030,022982747,HW50020001/SW2.50",
                }
            return None

        monkeypatch.setattr(window, "_probe_supply_candidate", _probe_supply)

        window._refresh_supply_ports()
        detected = window._auto_detect_supply_port()

        assert detected is True
        assert window.combo_supply_port.currentData() == "COM3"
        assert window.combo_supply_baud.currentText() == "115200"
        assert window.combo_supply_profile.currentData() == "hmp4030"
    finally:
        _close_test_window(window)


def test_auto_detect_tic_sets_path_and_serial(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)

    class _FakeTicController:
        def __init__(self, command_path: str = "ticcmd", device_serial: str = "") -> None:
            self.command_path = command_path
            self.device_serial = device_serial

        def run(self, *args: str, timeout_s: float = 5.0) -> str:
            assert args == ("--list",)
            return "00501366,         Tic T500 Stepper Motor Controller            \n"

    try:
        monkeypatch.setattr(mini_dma_mod, "_find_ticcmd", lambda: r"C:\\tools\\ticcmd.exe")
        monkeypatch.setattr(mini_dma_mod, "TicController", _FakeTicController)
        window.edit_ticcmd_path.setText("")

        detected = window._auto_detect_tic()

        assert detected is True
        assert window.edit_ticcmd_path.text() == r"C:\\tools\\ticcmd.exe"
        assert window.edit_tic_serial.text() == "00501366"
    finally:
        _close_test_window(window)


def test_apply_name_fields_uses_display_wire_and_file_safe_wire_token(tmp_path: Path, qtbot) -> None:
    window = _build_window(tmp_path, qtbot)

    try:
        window.edit_name_composition.setText("Ni51Fe26Ga21")
        window.edit_name_wire.setText("156_2")
        window.edit_name_specimen.setText("s1")
        window.edit_name_condition.setText("preload test")
        recipe_index = window.combo_recipe_mode.findData("ramp")
        if recipe_index >= 0:
            window.combo_recipe_mode.setCurrentIndex(recipe_index)

        window._apply_name_fields()

        assert window.edit_sample_name.text() == "Ni51Fe26Ga21 156/2 s1 preload test"
        assert window.edit_log_name.text() == "Ni51Fe26Ga21 156_2 s1 preload test"
    finally:
        _close_test_window(window)


def test_hardware_tare_sends_escape_t_and_clears_software_offset(tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = _build_window(tmp_path, qtbot)
    window._load_offset_g = 1.234
    window.combo_scale_port.clear()
    window.combo_scale_port.addItem("COM6", "COM6")
    window.combo_scale_port.setCurrentIndex(0)
    if window.combo_scale_baud.findText("9600") >= 0:
        window.combo_scale_baud.setCurrentText("9600")

    written: list[bytes] = []

    class _FakeSerial:
        def __init__(self, *args, **kwargs) -> None:
            self.rts = False
            self.dtr = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def reset_input_buffer(self) -> None:
            return None

        def reset_output_buffer(self) -> None:
            return None

        def write(self, payload: bytes) -> None:
            written.append(payload)

        def flush(self) -> None:
            return None

        def readline(self) -> bytes:
            return b"0.000 g \\r\\n"

    try:
        monkeypatch.setattr(mini_dma_mod.serial, "Serial", _FakeSerial)

        assert window._tare_scale_hardware() is True
        assert written and written[0] == b"\x1bt"
        assert window._load_offset_g == 0.0
    finally:
        _close_test_window(window)
