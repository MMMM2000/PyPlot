"""AC susceptibility logger built on the current annealing workflow."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any, cast

from PyQt6 import QtCore, QtGui, QtWidgets

from data_logging.current_annealing_logger.current_annealing_logger import (
    DEFAULT_LOG_DIR,
    MainWindow as CurrentAnnealingWindow,
    _apply_app_font_to_matplotlib,
)
from plotting.shared.utils import ensure_app_theme

from .lcr6000 import (
    DEFAULT_BAUDRATE,
    Lcr6000Reading,
    Lcr6000Serial,
    Lcr6000Settings,
    SUPPORTED_FUNCTIONS,
    SUPPORTED_MONITORS,
    available_serial_ports,
    build_settings_plan,
    commands_for_settings,
    parse_numeric_list,
)


HEADER_LINE = (
    "# Current (mA)\tVoltage (V)\tResistance (Ohm)\t"
    "AC plan index\tLCR frequency (Hz)\tLCR level mode\tLCR level\t"
    "LCR function\tLCR primary\tLCR secondary\tLCR monitor1\t"
    "LCR monitor2\tLCR comparator\tLCR raw"
)


class MainWindow(CurrentAnnealingWindow):
    """Current annealing logger extended with LCR-6200 measurements."""

    def __init__(self) -> None:
        self.ac_settings = QtCore.QSettings("microwire", "ac_susceptibility_logger")
        self.lcr_meter: Lcr6000Serial | None = None
        self._lcr_plan: list[Lcr6000Settings] = []
        self._lcr_plan_index = 0
        self._lcr_last_reading: Lcr6000Reading | None = None
        self._lcr_last_error = ""
        super().__init__()
        self.setWindowTitle("AC Susceptibility Logger")
        self._install_lcr_controls()
        self._load_lcr_settings()
        self.populate_lcr_ports()
        self._set_default_log_name()

    def _set_default_log_name(self) -> None:
        line_edit = getattr(self.ui, "lineEdit_log_file", None)
        if isinstance(line_edit, QtWidgets.QLineEdit):
            current = line_edit.text().strip()
            if not current or current == "anneal_log":
                line_edit.setText("ac_susceptibility_log")
                self.sync_full_log_path()

    def _install_lcr_controls(self) -> None:
        frame = getattr(self.ui, "frame_serial_settings", None)
        layout = frame.layout() if isinstance(frame, QtWidgets.QWidget) else None
        if layout is None:
            return

        group = QtWidgets.QGroupBox("LCR-6200 AC susceptibility", frame)
        outer = QtWidgets.QVBoxLayout(group)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        row = QtWidgets.QHBoxLayout()
        self.comboBox_lcr_port = QtWidgets.QComboBox(group)
        self.pushButton_refresh_lcr_ports = QtWidgets.QPushButton("Refresh", group)
        self.pushButton_connect_lcr = QtWidgets.QPushButton("Connect LCR", group)
        self.pushButton_identify_lcr = QtWidgets.QPushButton("Identify", group)
        row.addWidget(QtWidgets.QLabel("Port:", group))
        row.addWidget(self.comboBox_lcr_port, stretch=1)
        row.addWidget(self.pushButton_refresh_lcr_ports)
        row.addWidget(self.pushButton_connect_lcr)
        row.addWidget(self.pushButton_identify_lcr)
        outer.addLayout(row)

        grid = QtWidgets.QGridLayout()
        self.lineEdit_lcr_frequencies = QtWidgets.QLineEdit(group)
        self.lineEdit_lcr_levels = QtWidgets.QLineEdit(group)
        self.comboBox_lcr_level_mode = QtWidgets.QComboBox(group)
        self.comboBox_lcr_level_mode.addItem("Voltage", "voltage")
        self.comboBox_lcr_level_mode.addItem("Current", "current")
        self.comboBox_lcr_function = QtWidgets.QComboBox(group)
        self.comboBox_lcr_function.addItems(list(SUPPORTED_FUNCTIONS))
        self.comboBox_lcr_monitor1 = QtWidgets.QComboBox(group)
        self.comboBox_lcr_monitor1.addItems(list(SUPPORTED_MONITORS))
        self.comboBox_lcr_monitor2 = QtWidgets.QComboBox(group)
        self.comboBox_lcr_monitor2.addItems(list(SUPPORTED_MONITORS))
        self.comboBox_lcr_aperture = QtWidgets.QComboBox(group)
        self.comboBox_lcr_aperture.addItems(["FAST", "MED", "SLOW"])
        self.checkBox_ac_plan_loops = QtWidgets.QCheckBox("One current sweep per AC setting", group)
        self.checkBox_ac_plan_loops.setChecked(True)
        self.pushButton_apply_lcr_setting = QtWidgets.QPushButton("Apply setting", group)

        self.lineEdit_lcr_frequencies.setPlaceholderText("100, 1k, 10k, 100k")
        self.lineEdit_lcr_levels.setPlaceholderText("0.1, 0.3, 1.0")

        grid.addWidget(QtWidgets.QLabel("Frequencies:", group), 0, 0)
        grid.addWidget(self.lineEdit_lcr_frequencies, 0, 1, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Levels:", group), 1, 0)
        grid.addWidget(self.lineEdit_lcr_levels, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Mode:", group), 1, 2)
        grid.addWidget(self.comboBox_lcr_level_mode, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Function:", group), 2, 0)
        grid.addWidget(self.comboBox_lcr_function, 2, 1)
        grid.addWidget(QtWidgets.QLabel("Monitor 1:", group), 2, 2)
        grid.addWidget(self.comboBox_lcr_monitor1, 2, 3)
        grid.addWidget(QtWidgets.QLabel("Monitor 2:", group), 3, 0)
        grid.addWidget(self.comboBox_lcr_monitor2, 3, 1)
        grid.addWidget(QtWidgets.QLabel("Speed:", group), 3, 2)
        grid.addWidget(self.comboBox_lcr_aperture, 3, 3)
        grid.addWidget(self.checkBox_ac_plan_loops, 4, 0, 1, 3)
        grid.addWidget(self.pushButton_apply_lcr_setting, 4, 3)
        outer.addLayout(grid)

        self.label_lcr_status = QtWidgets.QLabel("LCR not connected", group)
        self.label_lcr_status.setWordWrap(True)
        outer.addWidget(self.label_lcr_status)

        cast(QtWidgets.QVBoxLayout, layout).addWidget(group)
        self.groupBox_lcr_settings = group

        self.pushButton_refresh_lcr_ports.clicked.connect(self.populate_lcr_ports)
        self.pushButton_connect_lcr.clicked.connect(self.handle_connect_lcr_clicked)
        self.pushButton_identify_lcr.clicked.connect(self.handle_identify_lcr_clicked)
        self.pushButton_apply_lcr_setting.clicked.connect(self.handle_apply_lcr_setting_clicked)
        for edit in (self.lineEdit_lcr_frequencies, self.lineEdit_lcr_levels):
            edit.editingFinished.connect(self._store_lcr_settings)
        for combo in (
            self.comboBox_lcr_level_mode,
            self.comboBox_lcr_function,
            self.comboBox_lcr_monitor1,
            self.comboBox_lcr_monitor2,
            self.comboBox_lcr_aperture,
        ):
            combo.currentIndexChanged.connect(lambda *_args: self._store_lcr_settings())
        self.checkBox_ac_plan_loops.toggled.connect(lambda *_args: self._store_lcr_settings())

    def _load_lcr_settings(self) -> None:
        self.lineEdit_lcr_frequencies.setText(
            self.ac_settings.value("frequencies", "100, 1k, 10k, 100k", type=str)
        )
        self.lineEdit_lcr_levels.setText(
            self.ac_settings.value("levels", "0.1, 0.3, 1.0", type=str)
        )
        self._set_combo_data(self.comboBox_lcr_level_mode, self.ac_settings.value("level_mode", "voltage", type=str))
        self._set_combo_text(self.comboBox_lcr_function, self.ac_settings.value("function", "Ls-Q", type=str))
        self._set_combo_text(self.comboBox_lcr_monitor1, self.ac_settings.value("monitor1", "Z", type=str))
        self._set_combo_text(self.comboBox_lcr_monitor2, self.ac_settings.value("monitor2", "IAC", type=str))
        self._set_combo_text(self.comboBox_lcr_aperture, self.ac_settings.value("aperture", "FAST", type=str))
        plan_loops = self.ac_settings.value("plan_loops", 1)
        self.checkBox_ac_plan_loops.setChecked(str(plan_loops).lower() not in {"0", "false", "no"})

    @staticmethod
    def _set_combo_text(combo: QtWidgets.QComboBox, text: str) -> None:
        idx = combo.findText(text, QtCore.Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    @staticmethod
    def _set_combo_data(combo: QtWidgets.QComboBox, data: str) -> None:
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _store_lcr_settings(self) -> None:
        self.ac_settings.setValue("frequencies", self.lineEdit_lcr_frequencies.text())
        self.ac_settings.setValue("levels", self.lineEdit_lcr_levels.text())
        self.ac_settings.setValue("level_mode", self.comboBox_lcr_level_mode.currentData())
        self.ac_settings.setValue("function", self.comboBox_lcr_function.currentText())
        self.ac_settings.setValue("monitor1", self.comboBox_lcr_monitor1.currentText())
        self.ac_settings.setValue("monitor2", self.comboBox_lcr_monitor2.currentText())
        self.ac_settings.setValue("aperture", self.comboBox_lcr_aperture.currentText())
        self.ac_settings.setValue("plan_loops", int(self.checkBox_ac_plan_loops.isChecked()))

    def populate_lcr_ports(self) -> None:
        self.comboBox_lcr_port.clear()
        ports = available_serial_ports()
        for port in ports:
            self.comboBox_lcr_port.addItem(port.label, port.device)
        if not ports:
            self.comboBox_lcr_port.addItem("No serial ports found", "")
        for idx, port in enumerate(ports):
            if port.is_lcr6000:
                self.comboBox_lcr_port.setCurrentIndex(idx)
                break

    def handle_connect_lcr_clicked(self) -> None:
        if self.lcr_meter is not None and self.lcr_meter.is_open:
            self.lcr_meter.close()
            self.lcr_meter = None
            self.pushButton_connect_lcr.setText("Connect LCR")
            self.label_lcr_status.setText("LCR disconnected")
            return
        port = str(self.comboBox_lcr_port.currentData() or "").strip()
        if not port:
            QtWidgets.QMessageBox.warning(self, "No LCR port", "Select the LCR-6200 serial port first.")
            return
        try:
            self.lcr_meter = Lcr6000Serial(port, baudrate=DEFAULT_BAUDRATE)
            idn = self.lcr_meter.identify()
        except Exception as exc:
            self.lcr_meter = None
            QtWidgets.QMessageBox.warning(self, "LCR connection failed", str(exc))
            self.label_lcr_status.setText(f"LCR connection failed: {exc}")
            return
        self.pushButton_connect_lcr.setText("Disconnect LCR")
        self.label_lcr_status.setText(f"Connected: {idn or port}")
        self._configure_lcr_for_current_index()

    def handle_identify_lcr_clicked(self) -> None:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            QtWidgets.QMessageBox.information(self, "LCR not connected", "Connect the LCR port first.")
            return
        try:
            idn = meter.identify()
        except Exception as exc:
            self.label_lcr_status.setText(f"Identify failed: {exc}")
            return
        self.label_lcr_status.setText(f"Connected: {idn}")

    def handle_apply_lcr_setting_clicked(self) -> None:
        self._prepare_lcr_plan()
        self._lcr_plan_index = 0
        self._configure_lcr_for_current_index(show_errors=True)

    def _prepare_lcr_plan(self) -> list[Lcr6000Settings]:
        self._store_lcr_settings()
        level_mode = str(self.comboBox_lcr_level_mode.currentData() or "voltage")
        quantity = "current" if level_mode == "current" else "generic"
        frequencies = parse_numeric_list(self.lineEdit_lcr_frequencies.text(), quantity="frequency")
        levels = parse_numeric_list(self.lineEdit_lcr_levels.text(), quantity=quantity)
        self._lcr_plan = build_settings_plan(
            frequencies,
            levels,
            level_mode=level_mode,
            function=self.comboBox_lcr_function.currentText(),
            monitor1=self.comboBox_lcr_monitor1.currentText(),
            monitor2=self.comboBox_lcr_monitor2.currentText(),
            aperture=self.comboBox_lcr_aperture.currentText(),
        )
        if not self._lcr_plan:
            raise ValueError("No LCR settings were generated.")
        return self._lcr_plan

    def _configure_lcr_for_current_index(self, *, show_errors: bool = False) -> None:
        if not self._lcr_plan:
            try:
                self._prepare_lcr_plan()
            except Exception as exc:
                if show_errors:
                    QtWidgets.QMessageBox.warning(self, "Invalid AC settings", str(exc))
                self.label_lcr_status.setText(f"Invalid AC settings: {exc}")
                return
        index = min(max(0, self._lcr_plan_index), len(self._lcr_plan) - 1)
        setting = self._lcr_plan[index]
        meter = self.lcr_meter
        if meter is not None and meter.is_open:
            try:
                meter.configure(setting)
            except Exception as exc:
                self._lcr_last_error = str(exc)
                if show_errors:
                    QtWidgets.QMessageBox.warning(self, "LCR configure failed", str(exc))
                self.label_lcr_status.setText(f"LCR configure failed: {exc}")
                return
        plan_count = len(self._lcr_plan)
        level_unit = "A" if setting.level_mode == "current" else "V"
        self.label_lcr_status.setText(
            f"AC setting {index + 1}/{plan_count}: {setting.function}, "
            f"{setting.frequency_hz:g} Hz, {setting.level_value:g} {level_unit}"
        )

    def handle_toggle_process_clicked(self):  # type: ignore[override]
        starting = not bool(getattr(self, "process_running", False))
        if starting:
            try:
                plan = self._prepare_lcr_plan()
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "Invalid AC settings", str(exc))
                return
            self._lcr_plan_index = 0
            if self.checkBox_ac_plan_loops.isChecked() and len(plan) > 1:
                loops = getattr(self.ui, "spinBox_loops", None)
                infinite = getattr(self.ui, "checkBox_infinite_loops", None)
                reverse = getattr(self.ui, "checkBox_reverse", None)
                if isinstance(infinite, QtWidgets.QCheckBox):
                    infinite.setChecked(False)
                if isinstance(reverse, QtWidgets.QCheckBox):
                    reverse.setChecked(True)
                if isinstance(loops, QtWidgets.QSpinBox):
                    loops.setValue(len(plan))
            self._configure_lcr_for_current_index(show_errors=False)
        super().handle_toggle_process_clicked()

    def _finalize_loop_cycle(self) -> None:
        super()._finalize_loop_cycle()
        if not self.checkBox_ac_plan_loops.isChecked() or not self._lcr_plan:
            return
        try:
            next_index = int(getattr(self, "loop_idx", 0))
        except Exception:
            next_index = self._lcr_plan_index + 1
        if 0 <= next_index < len(self._lcr_plan):
            self._lcr_plan_index = next_index
            self._configure_lcr_for_current_index(show_errors=False)

    def _current_lcr_setting(self) -> Lcr6000Settings | None:
        if not self._lcr_plan:
            try:
                self._prepare_lcr_plan()
            except Exception:
                return None
        if not self._lcr_plan:
            return None
        index = min(max(0, self._lcr_plan_index), len(self._lcr_plan) - 1)
        return self._lcr_plan[index]

    def _fetch_lcr_reading(self) -> Lcr6000Reading | None:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            return None
        try:
            reading = meter.fetch_impedance()
        except Exception as exc:
            self._lcr_last_error = str(exc)
            self.label_lcr_status.setText(f"LCR read failed: {exc}")
            return None
        self._lcr_last_reading = reading
        return reading

    def _ensure_log_header(self, path: str) -> None:  # type: ignore[override]
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return
        lines = text.splitlines()
        if not lines:
            lines = [HEADER_LINE]
        else:
            header_index: int | None = None
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("#") and "Current" in stripped and "Resistance" in stripped:
                    header_index = idx
                    break
            if header_index is None:
                lines.insert(0, HEADER_LINE)
            else:
                lines[header_index] = HEADER_LINE
        try:
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def prepare_output_file(self) -> bool:  # type: ignore[override]
        path = self.build_log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass

        mode = "w"
        if os.path.exists(path):
            msg = QtWidgets.QMessageBox(self)
            msg.setWindowTitle("File exists")
            msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
            base = os.path.basename(path)
            msg.setText(f"'{base}' already exists.")
            msg.setInformativeText("Choose an action:")
            replace_btn = msg.addButton("Replace", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
            continue_btn = msg.addButton("Continue", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is cancel_btn:
                return False
            if clicked is continue_btn:
                mode = "a"
            elif clicked is replace_btn:
                mode = "w"
            else:
                return False
        try:
            if mode == "a":
                self._ensure_log_header(path)
            with open(path, mode, encoding="utf-8") as fh:
                if mode != "a":
                    fh.write(HEADER_LINE + "\n")
                    self._write_lcr_metadata(fh)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to open {path}: {exc}")
            return False

        self.f_name = path
        return True

    def _write_lcr_metadata(self, fh: Any) -> None:
        if not self._lcr_plan:
            return
        fh.write("# AC susceptibility plan generated from LCR-6200 settings\n")
        for index, setting in enumerate(self._lcr_plan, start=1):
            commands = " ".join(command.strip() for command in commands_for_settings(setting))
            fh.write(f"# AC setting {index}: {commands}\n")

    def _write_sample_to_file(self, *, initial_sample: bool) -> None:  # type: ignore[override]
        if initial_sample or not self.f_name:
            return
        current_mA = float(self.current_current_read) * 1000.0
        voltage = float(self.current_voltage)
        resistance = float(self.current_resistance)
        if not math.isfinite(current_mA) or not math.isfinite(resistance):
            return
        setting = self._current_lcr_setting()
        reading = self._fetch_lcr_reading()
        if not self.f_out:
            try:
                Path(self.f_name).parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            try:
                self.f_out = open(self.f_name, "a", encoding="utf-8")
            except OSError:
                self.f_out = None
        if self.f_out:
            extras = self._format_lcr_columns(setting, reading)
            line = "\t".join(
                [
                    self._format_sample_value(current_mA),
                    self._format_sample_value(voltage),
                    self._format_sample_value(resistance),
                    *extras,
                ]
            ) + "\n"
            self.f_out.write(line)
            self.f_out.close()
            self.f_out = None

    def _format_lcr_columns(
        self,
        setting: Lcr6000Settings | None,
        reading: Lcr6000Reading | None,
    ) -> list[str]:
        if setting is None:
            base = ["", "", "", "", ""]
        else:
            base = [
                str(self._lcr_plan_index + 1),
                self._format_sample_value(setting.frequency_hz),
                setting.level_mode,
                self._format_sample_value(setting.level_value),
                setting.function,
            ]
        if reading is None:
            return base + ["", "", "", "", "", ""]
        return base + [
            self._format_optional_float(reading.primary),
            self._format_optional_float(reading.secondary),
            self._format_optional_float(reading.monitor1),
            self._format_optional_float(reading.monitor2),
            reading.comparator,
            reading.raw,
        ]

    def _format_optional_float(self, value: float | None) -> str:
        if value is None or not math.isfinite(value):
            return ""
        return self._format_sample_value(value)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        meter = self.lcr_meter
        if meter is not None:
            try:
                meter.close()
            except Exception:
                pass
            self.lcr_meter = None
        super().closeEvent(event)


WINDOWS: list[QtWidgets.QWidget] = []


def main() -> QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        qt_app = QtWidgets.QApplication(sys.argv)
        owns_app = True
    else:
        qt_app = cast(QtWidgets.QApplication, app)

    ensure_app_theme(qt_app)
    _apply_app_font_to_matplotlib(qt_app)
    win = MainWindow()
    WINDOWS.append(win)
    win.show()
    if owns_app:
        qt_app.exec()
    return win


if __name__ == "__main__":
    main()
