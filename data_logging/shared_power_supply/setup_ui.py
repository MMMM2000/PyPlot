from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6 import QtCore, QtWidgets

from .broker import (
    ROLE_AC_SUSCEPTIBILITY,
    ROLE_CURRENT_ANNEALING,
    ROLE_MINI_DMA_CURRENT,
    ROLE_MINI_DMA_MOTOR,
    ROLE_OTHER_MANUAL,
    ROLE_UNUSED,
    BenchProfile,
)
from .driver import HmpSerialDriver, SerialFactory
from .profiles import HMP_PROFILES, SupplyProfile


ROLE_LABELS = {
    ROLE_UNUSED: "Unused",
    ROLE_MINI_DMA_MOTOR: "TMA motor supply",
    ROLE_MINI_DMA_CURRENT: "TMA current sweep",
    ROLE_CURRENT_ANNEALING: "Current annealing",
    ROLE_AC_SUSCEPTIBILITY: "AC susceptibility",
    ROLE_OTHER_MANUAL: "Other/manual",
}


class SharedPowerSupplySetupWindow(QtWidgets.QMainWindow):
    """Operator setup utility for shared HMP bench profiles."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shared HMP PSU Setup")
        self._profile: SupplyProfile = HMP_PROFILES["hmp4030"]
        self._serial_factory = serial_factory
        self._role_combos: dict[int, QtWidgets.QComboBox] = {}
        self._confirm_checks: dict[int, QtWidgets.QCheckBox] = {}
        self._voltage_spins: dict[int, QtWidgets.QDoubleSpinBox] = {}
        self._current_spins: dict[int, QtWidgets.QDoubleSpinBox] = {}

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        top_form = QtWidgets.QFormLayout()
        self.combo_model = QtWidgets.QComboBox(central)
        for profile in HMP_PROFILES.values():
            self.combo_model.addItem(profile.label, profile.profile_id)
        self.combo_model.currentIndexChanged.connect(self._handle_model_changed)
        top_form.addRow("Detected model", self.combo_model)

        self.edit_port_identity = QtWidgets.QLineEdit(central)
        self.edit_port_identity.setPlaceholderText("COM port or instrument identity")
        top_form.addRow("Port identity", self.edit_port_identity)

        self.edit_profile_name = QtWidgets.QLineEdit("Shared HMP bench", central)
        top_form.addRow("Bench profile", self.edit_profile_name)
        layout.addLayout(top_form)

        detect_row = QtWidgets.QHBoxLayout()
        self.button_detect = QtWidgets.QPushButton("Detect HMP", central)
        self.button_detect.clicked.connect(self.detect_from_configured_port)
        detect_row.addWidget(self.button_detect)
        detect_row.addStretch(1)
        layout.addLayout(detect_row)

        self.table = QtWidgets.QTableWidget(central)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Channel", "Role", "Voltage limit", "Current limit", "Confirmed"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        button_row = QtWidgets.QHBoxLayout()
        self.button_save_profile = QtWidgets.QPushButton("Save confirmed profile", central)
        self.button_save_profile.clicked.connect(self.save_profile_to_dialog)
        button_row.addWidget(self.button_save_profile)
        self.button_load_profile = QtWidgets.QPushButton("Load profile", central)
        self.button_load_profile.clicked.connect(self.load_profile_from_dialog)
        button_row.addWidget(self.button_load_profile)
        self.button_enable_output = QtWidgets.QPushButton("Enable selected output", central)
        self.button_enable_output.setEnabled(False)
        button_row.addWidget(self.button_enable_output)
        layout.addLayout(button_row)

        self.status_label = QtWidgets.QLabel("Confirm channel wiring before enabling outputs.", central)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table.itemSelectionChanged.connect(self._refresh_output_button)
        self._rebuild_channel_rows()

    def set_detected_profile(self, profile_id: str, port_identity: str = "") -> None:
        index = self.combo_model.findData(profile_id)
        if index < 0:
            raise ValueError(f"Unsupported HMP profile: {profile_id}")
        self.combo_model.setCurrentIndex(index)
        self.edit_port_identity.setText(port_identity)
        self._mark_requires_review()

    def detect_from_configured_port(self) -> bool:
        port_identity = self.edit_port_identity.text().strip()
        if not port_identity:
            self.status_label.setText("Enter the HMP COM port before detection.")
            return False
        driver = HmpSerialDriver(
            port_name=port_identity,
            baudrate=self._profile.baudrate,
            serial_factory=self._serial_factory,
            timeout_s=0.5,
        )
        try:
            driver.connect()
            idn_text = driver.identify()
        except Exception as exc:
            self.status_label.setText(f"HMP detection failed: {exc}")
            return False
        finally:
            driver.close()
        if driver.profile is None:
            self.status_label.setText(f"Unsupported supply response: {idn_text}")
            return False
        self.set_detected_profile(driver.profile.profile_id, port_identity)
        self.status_label.setText(f"Detected {driver.profile.label}: {idn_text}")
        return True

    def _handle_model_changed(self) -> None:
        profile_id = str(self.combo_model.currentData() or "hmp4030")
        self._profile = HMP_PROFILES[profile_id]
        self._rebuild_channel_rows()
        self._mark_requires_review()

    def _rebuild_channel_rows(self) -> None:
        self._role_combos.clear()
        self._confirm_checks.clear()
        self._voltage_spins.clear()
        self._current_spins.clear()
        self.table.setRowCount(self._profile.channel_count)
        for row, channel in enumerate(range(1, self._profile.channel_count + 1)):
            item = QtWidgets.QTableWidgetItem(f"CH{channel}")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)

            role_combo = QtWidgets.QComboBox(self.table)
            for role, label in ROLE_LABELS.items():
                role_combo.addItem(label, role)
            role_combo.currentIndexChanged.connect(self._mark_requires_review)
            self.table.setCellWidget(row, 1, role_combo)
            self._role_combos[channel] = role_combo

            voltage_spin = QtWidgets.QDoubleSpinBox(self.table)
            voltage_spin.setRange(0.0, self._profile.max_voltage_v)
            voltage_spin.setDecimals(2)
            voltage_spin.setSuffix(" V")
            voltage_spin.valueChanged.connect(self._mark_requires_review)
            self.table.setCellWidget(row, 2, voltage_spin)
            self._voltage_spins[channel] = voltage_spin

            current_spin = QtWidgets.QDoubleSpinBox(self.table)
            current_spin.setRange(0.0, 10.0)
            current_spin.setDecimals(3)
            current_spin.setSuffix(" A")
            current_spin.valueChanged.connect(self._mark_requires_review)
            self.table.setCellWidget(row, 3, current_spin)
            self._current_spins[channel] = current_spin

            confirm = QtWidgets.QCheckBox(self.table)
            confirm.toggled.connect(self._refresh_output_button)
            self.table.setCellWidget(row, 4, confirm)
            self._confirm_checks[channel] = confirm
        self._refresh_output_button()

    def _mark_requires_review(self) -> None:
        for checkbox in self._confirm_checks.values():
            checkbox.setChecked(False)
        self.status_label.setText("Review and confirm channel wiring before enabling outputs.")
        self._refresh_output_button()

    def _selected_channel(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return row + 1

    def _refresh_output_button(self) -> None:
        channel = self._selected_channel()
        enabled = False
        if channel is not None:
            role = str(self._role_combos[channel].currentData() or ROLE_UNUSED)
            enabled = role != ROLE_UNUSED and self._confirm_checks[channel].isChecked()
        self.button_enable_output.setEnabled(enabled)

    def profile_payload(self) -> dict[str, Any]:
        channels: dict[str, dict[str, object]] = {}
        for channel in range(1, self._profile.channel_count + 1):
            channels[str(channel)] = {
                "role": str(self._role_combos[channel].currentData() or ROLE_UNUSED),
                "confirmed": self._confirm_checks[channel].isChecked(),
                "voltage_limit_v": float(self._voltage_spins[channel].value()),
                "current_limit_a": float(self._current_spins[channel].value()),
            }
        return {
            "name": self.edit_profile_name.text().strip() or "Shared HMP bench",
            "model": self._profile.profile_id,
            "port_identity": self.edit_port_identity.text().strip(),
            "requires_confirmation": not all(
                value["role"] == ROLE_UNUSED or bool(value["confirmed"]) for value in channels.values()
            ),
            "channels": channels,
        }

    def apply_profile_payload(self, payload: dict[str, Any]) -> None:
        profile = BenchProfile.from_dict(payload)
        self.set_detected_profile(profile.model, profile.port_identity)
        self.edit_profile_name.setText(profile.name)
        for channel, config in profile.channels.items():
            if channel not in self._role_combos:
                continue
            role_index = self._role_combos[channel].findData(config.role)
            if role_index >= 0:
                self._role_combos[channel].setCurrentIndex(role_index)
            self._confirm_checks[channel].setChecked(bool(config.confirmed and not profile.requires_confirmation))
            self._voltage_spins[channel].setValue(float(config.voltage_limit_v or 0.0))
            self._current_spins[channel].setValue(float(config.current_limit_a or 0.0))
        if profile.requires_confirmation:
            self.status_label.setText("Loaded profile requires review before output enable.")
        else:
            self.status_label.setText("Loaded confirmed bench profile.")
        self._refresh_output_button()

    def save_profile(self, path: Path) -> None:
        path.write_text(json.dumps(self.profile_payload(), indent=2), encoding="utf-8")

    def load_profile(self, path: Path) -> None:
        self.apply_profile_payload(json.loads(path.read_text(encoding="utf-8")))

    def save_profile_to_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save shared HMP bench profile",
            str(Path.cwd() / "shared_hmp_bench.json"),
            "JSON files (*.json)",
        )
        if path:
            self.save_profile(Path(path))

    def load_profile_from_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load shared HMP bench profile",
            str(Path.cwd()),
            "JSON files (*.json)",
        )
        if path:
            self.load_profile(Path(path))


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = SharedPowerSupplySetupWindow()
    window.resize(860, 420)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
