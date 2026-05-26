from __future__ import annotations

import importlib
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)

setup_mod = importlib.import_module("data_logging.shared_power_supply.setup_ui")


class _FakeDetectSerial:
    is_open = True

    def __init__(self, *_args, **_kwargs) -> None:
        self._responses = []

    def write(self, data: bytes) -> int:
        command = data.decode("ascii").strip().upper()
        if command == "*IDN?":
            self._responses.append(b"Rohde&Schwarz,HMP4040,serial,fw\n")
        return len(data)

    def readline(self) -> bytes:
        if self._responses:
            return self._responses.pop(0)
        return b"\n"

    def close(self) -> None:
        self.is_open = False


def test_setup_dialog_shows_three_channels_for_hmp4030(qtbot) -> None:
    window = setup_mod.SharedPowerSupplySetupWindow()
    qtbot.addWidget(window)

    window.set_detected_profile("hmp4030", "COM3")

    assert window.table.rowCount() == 3
    assert window.table.item(2, 0).text() == "CH3"


def test_setup_dialog_shows_four_channels_for_hmp4040(qtbot) -> None:
    window = setup_mod.SharedPowerSupplySetupWindow()
    qtbot.addWidget(window)

    window.set_detected_profile("hmp4040", "COM4")

    assert window.table.rowCount() == 4
    assert window.table.item(3, 0).text() == "CH4"


def test_setup_dialog_can_detect_hmp4040_from_configured_port(qtbot) -> None:
    window = setup_mod.SharedPowerSupplySetupWindow(serial_factory=_FakeDetectSerial)
    qtbot.addWidget(window)
    window.edit_port_identity.setText("COM4")

    detected = window.detect_from_configured_port()

    assert detected is True
    assert window.combo_model.currentData() == "hmp4040"
    assert window.table.rowCount() == 4
    assert "Detected HMP4040" in window.status_label.text()


def test_loaded_profile_prefills_roles_but_requires_review(qtbot) -> None:
    window = setup_mod.SharedPowerSupplySetupWindow()
    qtbot.addWidget(window)

    window.apply_profile_payload(
        {
            "name": "Kosice HMP4040 bench",
            "model": "hmp4040",
            "port_identity": "COM4",
            "requires_confirmation": True,
            "channels": {
                "3": {
                    "role": "mini_dma_motor_supply",
                    "confirmed": True,
                    "voltage_limit_v": 12.0,
                    "current_limit_a": 0.5,
                },
                "4": {
                    "role": "mini_dma_current_sweep",
                    "confirmed": True,
                    "voltage_limit_v": 32.05,
                    "current_limit_a": 1.0,
                },
            },
        }
    )

    assert window._role_combos[3].currentData() == "mini_dma_motor_supply"
    assert window._role_combos[4].currentData() == "mini_dma_current_sweep"
    assert window._confirm_checks[3].isChecked() is False
    assert window._confirm_checks[4].isChecked() is False
    assert window.button_enable_output.isEnabled() is False


def test_output_enable_requires_selected_confirmed_non_unused_channel(qtbot) -> None:
    window = setup_mod.SharedPowerSupplySetupWindow()
    qtbot.addWidget(window)
    window.set_detected_profile("hmp4040", "COM4")
    role_index = window._role_combos[4].findData("current_annealing")
    window._role_combos[4].setCurrentIndex(role_index)
    window.table.selectRow(3)

    assert window.button_enable_output.isEnabled() is False

    window._confirm_checks[4].setChecked(True)

    assert window.button_enable_output.isEnabled() is True
