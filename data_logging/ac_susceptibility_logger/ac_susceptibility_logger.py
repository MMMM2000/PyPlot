"""AC susceptibility logger built on the current annealing workflow."""

from __future__ import annotations

from datetime import datetime
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Sequence, cast

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
    LCR_FRONT_PANEL_VOLTAGE_PRESETS_V as _LCR_FRONT_PANEL_VOLTAGE_PRESETS_V,
    SUPPORTED_FUNCTIONS,
    SUPPORTED_MONITORS,
    available_serial_ports,
    build_settings_plan,
    commands_for_settings,
    parse_numeric_list,
)
from . import sweep


HEADER_LINE = (
    "# Current (mA)\tVoltage (V)\tResistance (Ohm)\t"
    "AC plan index\tLCR frequency (Hz)\tLCR level mode\tLCR level\t"
    "LCR function\tLCR primary\tLCR secondary\tLCR monitor1\t"
    "LCR monitor2\tLCR comparator\tLCR raw"
)

BASELINE_HEADER_LINE = (
    "# Timestamp UTC\tBaseline setting index\tBaseline repeat index\t"
    "LCR frequency (Hz)\tLCR level mode\tLCR level\tLCR function\t"
    "LCR primary\tLCR secondary\tLCR monitor1\tLCR monitor2\t"
    "LCR comparator\tLCR raw"
)

PRACTICAL_FREQUENCY_PRESETS_HZ = [
    10.0,
    20.0,
    50.0,
    100.0,
    200.0,
    500.0,
    1000.0,
    2000.0,
    5000.0,
    10000.0,
    20000.0,
    50000.0,
    100000.0,
    200000.0,
]

DEFAULT_FREQUENCY_PRESETS_HZ = [10.0, 20.0, 100.0, 1000.0, 2000.0, 10000.0, 100000.0, 200000.0]
LCR_FRONT_PANEL_VOLTAGE_PRESETS_V = list(_LCR_FRONT_PANEL_VOLTAGE_PRESETS_V)
OWON_DEFAULT_VOLTAGE_LIMIT_V = 60.0
HMP_DEFAULT_VOLTAGE_LIMIT_V = 30.0


class MainWindow(CurrentAnnealingWindow):
    """Current annealing logger extended with LCR-6200 measurements."""

    def __init__(self) -> None:
        self.ac_settings = QtCore.QSettings("microwire", "ac_susceptibility_logger")
        self.lcr_meter: Lcr6000Serial | None = None
        self._lcr_plan: list[Lcr6000Settings] = []
        self._lcr_plan_index = 0
        self._lcr_last_reading: Lcr6000Reading | None = None
        self._lcr_last_error = ""
        self._ac_sweep_running = False
        self._ac_sweep_stop_requested = False
        self._ac_lcr_scroll_area: QtWidgets.QScrollArea | None = None
        super().__init__()
        self.setWindowTitle("AC Susceptibility Logger")
        self._install_lcr_controls()
        self._simplify_inherited_ac_workflow()
        self._load_lcr_settings()
        self.populate_lcr_ports()
        self.populate_ac_psu_ports()
        self.auto_detect_power_supply()
        self._update_ac_sweep_estimate()
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

        serial_group = getattr(self.ui, "groupBox_serial_settings", None)
        if isinstance(serial_group, QtWidgets.QGroupBox):
            serial_group.setTitle("PSU connection")
            serial_group.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Preferred,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            serial_group.setMaximumHeight(72)

        group = QtWidgets.QGroupBox("Instrument setup", frame)
        outer = QtWidgets.QVBoxLayout(group)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        output_group = QtWidgets.QGroupBox("Output", group)
        output_grid = QtWidgets.QGridLayout(output_group)
        output_grid.setColumnStretch(1, 1)
        output_grid.addWidget(getattr(self.ui, "label_log_dir"), 0, 0)
        output_grid.addWidget(getattr(self.ui, "lineEdit_log_dir"), 0, 1)
        output_buttons = QtWidgets.QHBoxLayout()
        output_buttons.setContentsMargins(0, 0, 0, 0)
        output_buttons.addWidget(getattr(self.ui, "pushButton_open_dir"))
        output_buttons.addWidget(getattr(self.ui, "pushButton_browse_dir"))
        output_grid.addLayout(output_buttons, 0, 2)
        output_grid.addWidget(getattr(self.ui, "label_log_file"), 1, 0)
        file_row = QtWidgets.QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.addWidget(getattr(self.ui, "lineEdit_log_file"), 1)
        file_row.addWidget(getattr(self.ui, "label_extension"))
        output_grid.addLayout(file_row, 1, 1, 1, 2)
        outer.addWidget(output_group)

        row = QtWidgets.QHBoxLayout()
        self.comboBox_lcr_port = QtWidgets.QComboBox(group)
        self.pushButton_refresh_lcr_ports = QtWidgets.QPushButton("Refresh", group)
        self.pushButton_connect_lcr = QtWidgets.QPushButton("Connect LCR", group)
        self.pushButton_identify_lcr = QtWidgets.QPushButton("Identify", group)
        self.pushButton_auto_setup = QtWidgets.QPushButton("Auto setup", group)
        row.addWidget(QtWidgets.QLabel("Port:", group))
        row.addWidget(self.comboBox_lcr_port, stretch=1)
        row.addWidget(self.pushButton_refresh_lcr_ports)
        row.addWidget(self.pushButton_connect_lcr)
        row.addWidget(self.pushButton_identify_lcr)
        row.addWidget(self.pushButton_auto_setup)
        outer.addLayout(row)

        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.lineEdit_lcr_frequencies = QtWidgets.QLineEdit(group)
        self.lineEdit_lcr_levels = QtWidgets.QLineEdit(group)
        self.comboBox_lcr_level_mode = QtWidgets.QComboBox(group)
        self.comboBox_lcr_level_mode.addItem("Voltage", "voltage")
        self.comboBox_lcr_level_mode.hide()
        self.comboBox_lcr_function = QtWidgets.QComboBox(group)
        self.comboBox_lcr_function.addItems(list(SUPPORTED_FUNCTIONS))
        self.comboBox_lcr_function.setCurrentText("Ls-Rs")
        self.comboBox_lcr_function.hide()
        self.checkBox_lcr_model_lsrs = QtWidgets.QCheckBox("Ls-Rs", group)
        self.checkBox_lcr_model_lsrs.hide()
        self.checkBox_lcr_model_lprp = QtWidgets.QCheckBox("Lp-Rp", group)
        self.checkBox_lcr_model_lprp.setText("Also measure Lp-Rp")
        self.checkBox_lcr_model_lsrs.setChecked(True)
        self.comboBox_lcr_monitor1 = QtWidgets.QComboBox(group)
        self.comboBox_lcr_monitor1.addItems(list(SUPPORTED_MONITORS))
        self.comboBox_lcr_monitor2 = QtWidgets.QComboBox(group)
        self.comboBox_lcr_monitor2.addItems(list(SUPPORTED_MONITORS))
        self.comboBox_lcr_aperture = QtWidgets.QComboBox(group)
        self.comboBox_lcr_aperture.addItems(["FAST", "MED", "SLOW"])
        self.checkBox_ac_plan_loops = QtWidgets.QCheckBox("One current sweep per AC setting", group)
        self.checkBox_ac_plan_loops.setChecked(True)
        self.checkBox_ac_plan_loops.hide()
        self.pushButton_apply_lcr_setting = QtWidgets.QPushButton("Apply setting", group)
        self.pushButton_apply_lcr_setting.hide()
        self.pushButton_lcr_default_presets = QtWidgets.QPushButton("Default subset", group)
        self.pushButton_lcr_all_frequencies = QtWidgets.QPushButton("All practical frequencies", group)
        self.pushButton_lcr_all_levels = QtWidgets.QPushButton("All amplitudes", group)
        self.spinBox_lcr_baseline_repeats = QtWidgets.QSpinBox(group)
        self.spinBox_lcr_baseline_repeats.setRange(1, 100)
        self.spinBox_lcr_baseline_repeats.setValue(3)
        self.pushButton_measure_lcr_baseline = QtWidgets.QPushButton("Measure empty-coil baseline", group)
        self.label_ac_psu_status = QtWidgets.QLabel("", group)
        self.label_ac_psu_status.setWordWrap(True)
        self.spinBox_ac_voltage_limit = QtWidgets.QDoubleSpinBox(group)
        self.spinBox_ac_voltage_limit.setRange(0.1, 120.0)
        self.spinBox_ac_voltage_limit.setDecimals(2)
        self.spinBox_ac_voltage_limit.setSuffix(" V")
        self.spinBox_ac_voltage_limit.setValue(OWON_DEFAULT_VOLTAGE_LIMIT_V)
        self.spinBox_ac_current_start = QtWidgets.QDoubleSpinBox(group)
        self.spinBox_ac_current_start.setRange(0.0, 10000.0)
        self.spinBox_ac_current_start.setDecimals(3)
        self.spinBox_ac_current_start.setSuffix(" mA")
        self.spinBox_ac_current_start.setValue(20.0)
        self.spinBox_ac_current_stop = QtWidgets.QDoubleSpinBox(group)
        self.spinBox_ac_current_stop.setRange(0.0, 10000.0)
        self.spinBox_ac_current_stop.setDecimals(3)
        self.spinBox_ac_current_stop.setSuffix(" mA")
        self.spinBox_ac_current_stop.setValue(80.0)
        self.spinBox_ac_current_step = QtWidgets.QDoubleSpinBox(group)
        self.spinBox_ac_current_step.setRange(0.001, 10000.0)
        self.spinBox_ac_current_step.setDecimals(3)
        self.spinBox_ac_current_step.setSuffix(" mA")
        self.spinBox_ac_current_step.setValue(5.0)
        self.comboBox_ac_direction = QtWidgets.QComboBox(group)
        self.comboBox_ac_direction.addItem("Up and down", "up-down")
        self.comboBox_ac_direction.addItem("Up only", "up")
        self.comboBox_ac_direction.addItem("Down only", "down")
        self.spinBox_ac_dwell = QtWidgets.QDoubleSpinBox(group)
        self.spinBox_ac_dwell.setRange(0.0, 3600.0)
        self.spinBox_ac_dwell.setDecimals(2)
        self.spinBox_ac_dwell.setSuffix(" s")
        self.spinBox_ac_dwell.setValue(1.0)
        self.spinBox_ac_repeats = QtWidgets.QSpinBox(group)
        self.spinBox_ac_repeats.setRange(1, 1000)
        self.spinBox_ac_repeats.setValue(1)
        self.label_ac_sweep_estimate = QtWidgets.QLabel("", group)
        self.label_ac_sweep_estimate.setWordWrap(True)
        self.pushButton_run_ac_sweep = QtWidgets.QPushButton("Run microwire current sweep", group)
        self.pushButton_stop_ac_sweep = QtWidgets.QPushButton("Stop", group)
        self.pushButton_stop_ac_sweep.setEnabled(False)

        self.lineEdit_lcr_frequencies.setPlaceholderText("10, 20, 50, 100, 200, 500, 1k, 2k, 5k, 10k, 20k, 50k, 100k, 200k")
        self.lineEdit_lcr_levels.setPlaceholderText("0.01, 0.1, 0.3, 0.5, 1.0, 1.5, 2.0")

        grid.addWidget(QtWidgets.QLabel("Frequencies:", group), 0, 0)
        grid.addWidget(self.lineEdit_lcr_frequencies, 0, 1, 1, 3)
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(self.pushButton_lcr_default_presets)
        preset_row.addWidget(self.pushButton_lcr_all_frequencies)
        preset_row.addWidget(self.pushButton_lcr_all_levels)
        preset_row.addStretch(1)
        grid.addLayout(preset_row, 1, 1, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Amplitudes:", group), 2, 0)
        grid.addWidget(self.lineEdit_lcr_levels, 2, 1, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Model:", group), 3, 0)
        model_label = QtWidgets.QLabel("Ls-Rs", group)
        model_label.setToolTip("Recommended default for the overnight AC susceptibility workflow.")
        grid.addWidget(model_label, 3, 1)
        grid.addWidget(self.checkBox_lcr_model_lprp, 3, 2, 1, 2)
        grid.addWidget(QtWidgets.QLabel("LCR speed:", group), 4, 0)
        grid.addWidget(self.comboBox_lcr_aperture, 4, 1)
        grid.addWidget(QtWidgets.QLabel("Monitor 1:", group), 4, 2)
        grid.addWidget(self.comboBox_lcr_monitor1, 4, 3)
        self.comboBox_lcr_monitor2.hide()
        grid.addWidget(self.comboBox_lcr_level_mode, 5, 0)
        grid.addWidget(self.comboBox_lcr_function, 5, 1)
        grid.addWidget(self.checkBox_lcr_model_lsrs, 5, 2)
        grid.addWidget(self.checkBox_ac_plan_loops, 5, 3)
        grid.addWidget(self.pushButton_apply_lcr_setting, 6, 3)
        outer.addLayout(grid)

        plan_group = QtWidgets.QGroupBox("Experiment plan", group)
        plan_grid = QtWidgets.QGridLayout(plan_group)
        plan_grid.setColumnStretch(1, 1)
        plan_grid.setColumnStretch(3, 1)
        plan_grid.addWidget(QtWidgets.QLabel("PSU:", plan_group), 0, 0)
        plan_grid.addWidget(self.label_ac_psu_status, 0, 1, 1, 3)
        plan_grid.addWidget(QtWidgets.QLabel("Voltage limit:", plan_group), 1, 0)
        plan_grid.addWidget(self.spinBox_ac_voltage_limit, 1, 1)
        plan_grid.addWidget(QtWidgets.QLabel("Baseline repeats:", plan_group), 1, 2)
        plan_grid.addWidget(self.spinBox_lcr_baseline_repeats, 1, 3)
        plan_grid.addWidget(QtWidgets.QLabel("Current start:", plan_group), 2, 0)
        plan_grid.addWidget(self.spinBox_ac_current_start, 2, 1)
        plan_grid.addWidget(QtWidgets.QLabel("Current stop:", plan_group), 2, 2)
        plan_grid.addWidget(self.spinBox_ac_current_stop, 2, 3)
        plan_grid.addWidget(QtWidgets.QLabel("Current step:", plan_group), 3, 0)
        plan_grid.addWidget(self.spinBox_ac_current_step, 3, 1)
        plan_grid.addWidget(QtWidgets.QLabel("Direction:", plan_group), 3, 2)
        plan_grid.addWidget(self.comboBox_ac_direction, 3, 3)
        plan_grid.addWidget(QtWidgets.QLabel("Dwell:", plan_group), 4, 0)
        plan_grid.addWidget(self.spinBox_ac_dwell, 4, 1)
        plan_grid.addWidget(QtWidgets.QLabel("Repeats/current:", plan_group), 4, 2)
        plan_grid.addWidget(self.spinBox_ac_repeats, 4, 3)
        plan_grid.addWidget(self.label_ac_sweep_estimate, 5, 0, 1, 4)
        action_row = QtWidgets.QHBoxLayout()
        action_row.addWidget(self.pushButton_measure_lcr_baseline)
        action_row.addWidget(self.pushButton_run_ac_sweep)
        action_row.addWidget(self.pushButton_stop_ac_sweep)
        plan_grid.addLayout(action_row, 6, 0, 1, 4)
        outer.addWidget(plan_group)
        self.groupBox_ac_plan = plan_group

        self.label_lcr_status = QtWidgets.QLabel("LCR not connected", group)
        self.label_lcr_status.setWordWrap(True)
        outer.addWidget(self.label_lcr_status)

        cast(QtWidgets.QVBoxLayout, layout).addWidget(group)
        self.groupBox_lcr_settings = group

        self.pushButton_refresh_lcr_ports.clicked.connect(self.populate_lcr_ports)
        self.pushButton_connect_lcr.clicked.connect(self.handle_connect_lcr_clicked)
        self.pushButton_identify_lcr.clicked.connect(self.handle_identify_lcr_clicked)
        self.pushButton_auto_setup.clicked.connect(self.handle_auto_setup_clicked)
        self.pushButton_apply_lcr_setting.clicked.connect(self.handle_apply_lcr_setting_clicked)
        self.pushButton_measure_lcr_baseline.clicked.connect(self.handle_measure_lcr_baseline_clicked)
        self.pushButton_run_ac_sweep.clicked.connect(self.handle_run_ac_sweep_clicked)
        self.pushButton_stop_ac_sweep.clicked.connect(self.handle_stop_ac_sweep_clicked)
        self.pushButton_lcr_default_presets.clicked.connect(self.apply_default_lcr_presets)
        self.pushButton_lcr_all_frequencies.clicked.connect(self.apply_all_lcr_frequencies)
        self.pushButton_lcr_all_levels.clicked.connect(self.apply_all_lcr_levels)
        for shared in (
            getattr(self.ui, "comboBox_supply", None),
            getattr(self.ui, "comboBox_port", None),
            getattr(self.ui, "comboBox_baudrate", None),
        ):
            if isinstance(shared, QtWidgets.QComboBox):
                shared.currentIndexChanged.connect(lambda *_args: self._sync_ac_psu_from_shared_controls())
        for edit in (self.lineEdit_lcr_frequencies, self.lineEdit_lcr_levels):
            edit.editingFinished.connect(self._store_lcr_settings)
            edit.editingFinished.connect(self._update_ac_sweep_estimate)
        for combo in (
            self.comboBox_lcr_level_mode,
            self.comboBox_lcr_function,
            self.comboBox_lcr_monitor1,
            self.comboBox_lcr_monitor2,
            self.comboBox_lcr_aperture,
        ):
            combo.currentIndexChanged.connect(lambda *_args: self._store_lcr_settings())
            combo.currentIndexChanged.connect(lambda *_args: self._update_ac_sweep_estimate())
        self.checkBox_ac_plan_loops.toggled.connect(lambda *_args: self._store_lcr_settings())
        self.spinBox_lcr_baseline_repeats.valueChanged.connect(lambda *_args: self._store_lcr_settings())
        self.checkBox_lcr_model_lsrs.toggled.connect(lambda *_args: self._store_lcr_settings())
        self.checkBox_lcr_model_lprp.toggled.connect(lambda *_args: self._store_lcr_settings())
        self.checkBox_lcr_model_lsrs.toggled.connect(lambda *_args: self._update_ac_sweep_estimate())
        self.checkBox_lcr_model_lprp.toggled.connect(lambda *_args: self._update_ac_sweep_estimate())
        for widget in (
            self.spinBox_ac_voltage_limit,
            self.spinBox_ac_current_start,
            self.spinBox_ac_current_stop,
            self.spinBox_ac_current_step,
            self.comboBox_ac_direction,
            self.spinBox_ac_dwell,
            self.spinBox_ac_repeats,
        ):
            signal = getattr(widget, "currentIndexChanged", None) or getattr(widget, "valueChanged", None)
            if signal is not None:
                signal.connect(lambda *_args: self._store_lcr_settings())
                signal.connect(lambda *_args: self._update_ac_sweep_estimate())
        self._install_ac_wheel_guard(group)

    def _simplify_inherited_ac_workflow(self) -> None:
        process_frame = getattr(self.ui, "frame_process_settings", None)
        if isinstance(process_frame, QtWidgets.QWidget):
            process_frame.hide()
        command_frame = getattr(self.ui, "frame_command_and_response", None)
        if isinstance(command_frame, QtWidgets.QWidget):
            command_frame.hide()

    def _load_lcr_settings(self) -> None:
        self.lineEdit_lcr_frequencies.setText(
            self.ac_settings.value("frequencies", self._format_numeric_list(DEFAULT_FREQUENCY_PRESETS_HZ), type=str)
        )
        self.lineEdit_lcr_levels.setText(
            self.ac_settings.value("levels", self._format_numeric_list(LCR_FRONT_PANEL_VOLTAGE_PRESETS_V), type=str)
        )
        self._set_combo_data(self.comboBox_lcr_level_mode, self.ac_settings.value("level_mode", "voltage", type=str))
        self._set_combo_text(self.comboBox_lcr_function, "Ls-Rs")
        models = str(self.ac_settings.value("models", "Ls-Rs", type=str))
        model_tokens = {token.strip().lower() for token in re.split(r"[,;\s]+", models) if token.strip()}
        self.checkBox_lcr_model_lsrs.setChecked(True)
        self.checkBox_lcr_model_lprp.setChecked("lp-rp" in model_tokens)
        self._set_combo_text(self.comboBox_lcr_monitor1, self.ac_settings.value("monitor1", "Z", type=str))
        self._set_combo_text(self.comboBox_lcr_monitor2, self.ac_settings.value("monitor2", "IAC", type=str))
        self._set_combo_text(self.comboBox_lcr_aperture, self.ac_settings.value("aperture", "FAST", type=str))
        repeats = self.ac_settings.value("baseline_repeats", 3)
        try:
            self.spinBox_lcr_baseline_repeats.setValue(max(1, int(repeats)))
        except (TypeError, ValueError):
            self.spinBox_lcr_baseline_repeats.setValue(3)
        plan_loops = self.ac_settings.value("plan_loops", 1)
        self.checkBox_ac_plan_loops.setChecked(str(plan_loops).lower() not in {"0", "false", "no"})
        self._set_combo_data(self.comboBox_ac_direction, self.ac_settings.value("direction", "up-down", type=str))
        self.spinBox_ac_current_start.setValue(float(self.ac_settings.value("current_start_mA", 20.0)))
        self.spinBox_ac_current_stop.setValue(float(self.ac_settings.value("current_stop_mA", 80.0)))
        self.spinBox_ac_current_step.setValue(float(self.ac_settings.value("current_step_mA", 5.0)))
        self.spinBox_ac_dwell.setValue(float(self.ac_settings.value("dwell_s", 1.0)))
        self.spinBox_ac_repeats.setValue(max(1, int(self.ac_settings.value("sweep_repeats", 1))))
        voltage_limit = self.ac_settings.value("voltage_limit_v", None)
        if voltage_limit is None:
            self._sync_ac_psu_from_shared_controls()
        else:
            self.spinBox_ac_voltage_limit.setValue(float(voltage_limit))
            self._sync_ac_psu_from_shared_controls()

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
        self.ac_settings.setValue("function", "Ls-Rs")
        self.ac_settings.setValue("models", ",".join(self._selected_lcr_models()))
        self.ac_settings.setValue("monitor1", self.comboBox_lcr_monitor1.currentText())
        self.ac_settings.setValue("monitor2", self.comboBox_lcr_monitor2.currentText())
        self.ac_settings.setValue("aperture", self.comboBox_lcr_aperture.currentText())
        self.ac_settings.setValue("plan_loops", int(self.checkBox_ac_plan_loops.isChecked()))
        self.ac_settings.setValue("baseline_repeats", int(self.spinBox_lcr_baseline_repeats.value()))
        self.ac_settings.setValue("psu_backend", self._selected_ac_psu_backend())
        self.ac_settings.setValue("psu_port", self._selected_ac_psu_resource())
        self.ac_settings.setValue("psu_baud", str(self._selected_ac_psu_baudrate()))
        self.ac_settings.setValue("voltage_limit_v", float(self.spinBox_ac_voltage_limit.value()))
        self.ac_settings.setValue("current_start_mA", float(self.spinBox_ac_current_start.value()))
        self.ac_settings.setValue("current_stop_mA", float(self.spinBox_ac_current_stop.value()))
        self.ac_settings.setValue("current_step_mA", float(self.spinBox_ac_current_step.value()))
        self.ac_settings.setValue("direction", self.comboBox_ac_direction.currentData())
        self.ac_settings.setValue("dwell_s", float(self.spinBox_ac_dwell.value()))
        self.ac_settings.setValue("sweep_repeats", int(self.spinBox_ac_repeats.value()))

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

    def populate_ac_psu_ports(self) -> None:
        try:
            self.populate_ports()
        except Exception:
            pass
        self._sync_ac_psu_from_shared_controls()

    def auto_detect_power_supply(self) -> list[sweep.PowerSupplyCandidate]:
        candidates = sweep.detect_power_supply_candidates()
        if not candidates:
            self._sync_ac_psu_from_shared_controls()
            return []
        current_resource = self._selected_ac_psu_resource()
        port_combo = getattr(self.ui, "comboBox_port", None)
        if isinstance(port_combo, QtWidgets.QComboBox):
            port_combo.clear()
            for candidate in candidates:
                port_combo.addItem(candidate.label, candidate.resource)
        selected_index = 0
        for idx, candidate in enumerate(candidates):
            if candidate.resource == current_resource:
                selected_index = idx
                break
        candidate = candidates[selected_index]
        if isinstance(port_combo, QtWidgets.QComboBox):
            port_combo.setCurrentIndex(selected_index)
        supply_combo = getattr(self.ui, "comboBox_supply", None)
        if isinstance(supply_combo, QtWidgets.QComboBox):
            self._set_combo_data(supply_combo, candidate.backend_id)
        baud_combo = getattr(self.ui, "comboBox_baudrate", None)
        if isinstance(baud_combo, QtWidgets.QComboBox):
            self._set_combo_text(baud_combo, str(candidate.baudrate))
        self._sync_ac_psu_from_shared_controls()
        self.label_lcr_status.setText(f"Detected PSU: {candidate.idn} on {candidate.resource}")
        self._store_lcr_settings()
        return candidates

    def handle_auto_setup_clicked(self) -> None:
        self.populate_lcr_ports()
        candidates = self.auto_detect_power_supply()
        if not candidates:
            self.populate_ac_psu_ports()
            tried = ", ".join(device for _label, device in sweep.available_power_supply_ports()) or "no serial ports"
            self.label_lcr_status.setText(
                f"Auto setup did not find a supported HMP/OWON power supply. Tried {tried}; select the PSU manually above if needed."
            )
        else:
            self.label_lcr_status.setText(
                f"Auto setup selected {candidates[0].label} and LCR-6200-safe sweep defaults."
            )
        self.apply_default_lcr_presets(store=False)
        self.checkBox_lcr_model_lsrs.setChecked(True)
        self.checkBox_lcr_model_lprp.setChecked(False)
        self._store_lcr_settings()
        self._update_ac_sweep_estimate()

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

    def handle_run_ac_sweep_clicked(self) -> None:
        if self._ac_sweep_running:
            self.handle_stop_ac_sweep_clicked()
            return
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            QtWidgets.QMessageBox.information(self, "LCR not connected", "Connect the LCR port first.")
            return
        try:
            config = self._build_ac_sweep_config()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid AC sweep", str(exc))
            return
        psu = sweep.SerialScpiCurrentSource(
            backend_id=config.psu_backend,
            resource=config.psu_resource,
            baudrate=self._selected_ac_psu_baudrate(),
            voltage_limit_v=config.voltage_limit_v,
        )
        output_path = self._sweep_output_path()
        self._ac_sweep_running = True
        self._ac_sweep_stop_requested = False
        self.pushButton_run_ac_sweep.setEnabled(False)
        self.pushButton_stop_ac_sweep.setEnabled(True)
        self.label_lcr_status.setText(f"Running AC sweep: {output_path}")
        try:
            sweep.run_ac_sweep(
                config=config,
                lcr=meter,
                psu=psu,
                output_path=output_path,
                progress=self._handle_ac_sweep_progress,
                stop_requested=lambda: self._ac_sweep_stop_requested,
            )
        except Exception as exc:
            self._lcr_last_error = str(exc)
            self.label_lcr_status.setText(f"AC sweep failed: {exc}")
            QtWidgets.QMessageBox.warning(self, "AC sweep failed", str(exc))
            return
        finally:
            self._ac_sweep_running = False
            self._ac_sweep_stop_requested = False
            self.pushButton_run_ac_sweep.setEnabled(True)
            self.pushButton_stop_ac_sweep.setEnabled(False)
        self.label_lcr_status.setText(f"AC sweep saved: {output_path}")
        QtWidgets.QMessageBox.information(self, "AC sweep saved", f"Saved AC sweep to:\n{output_path}")

    def handle_stop_ac_sweep_clicked(self) -> None:
        if self._ac_sweep_running:
            self._ac_sweep_stop_requested = True
            self.label_lcr_status.setText("Stopping AC sweep after the current point...")

    def handle_measure_lcr_baseline_clicked(self) -> None:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            QtWidgets.QMessageBox.information(self, "LCR not connected", "Connect the LCR port first.")
            return
        try:
            plan = self._prepare_lcr_plan()
            repeats = max(1, int(self.spinBox_lcr_baseline_repeats.value()))
            path = self._baseline_output_path()
            self.pushButton_measure_lcr_baseline.setEnabled(False)
            self.label_lcr_status.setText(
                f"Measuring baseline: {len(plan)} settings x {repeats} repeats"
            )
            rows = self._collect_baseline_rows(plan, repeats=repeats)
            self._write_baseline_file(path, plan, rows)
        except Exception as exc:
            self._lcr_last_error = str(exc)
            self.label_lcr_status.setText(f"Baseline failed: {exc}")
            QtWidgets.QMessageBox.warning(self, "Baseline failed", str(exc))
            return
        finally:
            self.pushButton_measure_lcr_baseline.setEnabled(True)
        self.label_lcr_status.setText(f"Baseline saved: {path}")
        QtWidgets.QMessageBox.information(self, "Baseline saved", f"Saved LCR baseline to:\n{path}")

    def _prepare_lcr_plan(self) -> list[Lcr6000Settings]:
        self._store_lcr_settings()
        level_mode = str(self.comboBox_lcr_level_mode.currentData() or "voltage")
        quantity = "current" if level_mode == "current" else "generic"
        frequencies = parse_numeric_list(self.lineEdit_lcr_frequencies.text(), quantity="frequency")
        levels = parse_numeric_list(self.lineEdit_lcr_levels.text(), quantity=quantity)
        self._lcr_plan = sweep.build_ac_settings_plan(
            models=self._selected_lcr_models(),
            frequencies_hz=frequencies,
            levels=levels,
            level_mode=level_mode,
            monitor1=self.comboBox_lcr_monitor1.currentText(),
            monitor2=self.comboBox_lcr_monitor2.currentText(),
            aperture=self.comboBox_lcr_aperture.currentText(),
        )
        if not self._lcr_plan:
            raise ValueError("No LCR settings were generated.")
        return self._lcr_plan

    def _selected_lcr_models(self) -> list[str]:
        models: list[str] = []
        models.append("Ls-Rs")
        if getattr(self, "checkBox_lcr_model_lprp", None) is not None and self.checkBox_lcr_model_lprp.isChecked():
            models.append("Lp-Rp")
        return models

    def _build_ac_sweep_config(self) -> sweep.AcSweepConfig:
        plan = self._prepare_lcr_plan()
        current_points = sweep.build_current_loop_points(
            start_mA=float(self.spinBox_ac_current_start.value()),
            stop_mA=float(self.spinBox_ac_current_stop.value()),
            step_mA=float(self.spinBox_ac_current_step.value()),
            direction_mode=str(self.comboBox_ac_direction.currentData() or "up-down"),
        )
        self._sync_ac_psu_from_shared_controls()
        psu_resource = self._selected_ac_psu_resource()
        if not psu_resource:
            raise ValueError("Select the power-supply serial port first.")
        return sweep.AcSweepConfig(
            lcr_settings=plan,
            current_points=current_points,
            repeats=max(1, int(self.spinBox_ac_repeats.value())),
            dwell_s=max(0.0, float(self.spinBox_ac_dwell.value())),
            psu_backend=self._selected_ac_psu_backend(),
            psu_resource=psu_resource,
            voltage_limit_v=float(self.spinBox_ac_voltage_limit.value()),
        )

    def _update_ac_sweep_estimate(self) -> None:
        try:
            plan = self._prepare_lcr_plan()
            current_points = sweep.build_current_loop_points(
                start_mA=float(self.spinBox_ac_current_start.value()),
                stop_mA=float(self.spinBox_ac_current_stop.value()),
                step_mA=float(self.spinBox_ac_current_step.value()),
                direction_mode=str(self.comboBox_ac_direction.currentData() or "up-down"),
            )
            estimate = sweep.estimate_sweep(
                lcr_settings=plan,
                current_points=current_points,
                repeats=max(1, int(self.spinBox_ac_repeats.value())),
                dwell_s=max(0.0, float(self.spinBox_ac_dwell.value())),
            )
        except Exception as exc:
            self.label_ac_sweep_estimate.setText(f"Estimate unavailable: {exc}")
            return
        self.label_ac_sweep_estimate.setText(
            f"{estimate.total_measurements} LCR reads, about "
            f"{self._format_duration(estimate.estimated_seconds)} before communication overhead"
        )
        self._refresh_ac_psu_status()

    def _handle_ac_sweep_progress(self, row: sweep.AcSweepRow) -> None:
        self.label_lcr_status.setText(
            f"AC sweep {row.setting_index}/{row.total_settings}: "
            f"{row.setting.function}, {row.setting.frequency_hz:g} Hz, "
            f"{row.current_point.current_a * 1000:g} mA {row.current_point.direction}, "
            f"repeat {row.repeat_index}"
        )
        QtWidgets.QApplication.processEvents()

    def _sweep_output_path(self) -> Path:
        log_path = Path(self.build_log_path())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return log_path.with_name(f"{log_path.stem}_ac_sweep_{timestamp}.tsv")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds_int = max(0, int(round(seconds)))
        hours, remainder = divmod(seconds_int, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m {secs}s"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    @staticmethod
    def _format_numeric_list(values: Sequence[float]) -> str:
        return ", ".join(f"{value:g}" for value in values)

    def apply_default_lcr_presets(self, *, store: bool = True) -> None:
        self.lineEdit_lcr_frequencies.setText(self._format_numeric_list(DEFAULT_FREQUENCY_PRESETS_HZ))
        self.lineEdit_lcr_levels.setText(self._format_numeric_list(LCR_FRONT_PANEL_VOLTAGE_PRESETS_V))
        if store:
            self._store_lcr_settings()
            self._update_ac_sweep_estimate()

    def apply_all_lcr_frequencies(self) -> None:
        self.lineEdit_lcr_frequencies.setText(self._format_numeric_list(PRACTICAL_FREQUENCY_PRESETS_HZ))
        self._store_lcr_settings()
        self._update_ac_sweep_estimate()

    def apply_all_lcr_levels(self) -> None:
        self.lineEdit_lcr_levels.setText(self._format_numeric_list(LCR_FRONT_PANEL_VOLTAGE_PRESETS_V))
        self._store_lcr_settings()
        self._update_ac_sweep_estimate()

    def _selected_ac_psu_backend(self) -> str:
        combo = getattr(getattr(self, "ui", None), "comboBox_supply", None)
        if isinstance(combo, QtWidgets.QComboBox):
            data = combo.currentData(QtCore.Qt.ItemDataRole.UserRole)
            text = combo.currentText()
            if isinstance(data, str) and data in sweep.POWER_SUPPLY_PROFILES:
                return data
            classified = sweep.classify_power_supply_idn(text)
            if classified is not None:
                return classified
            upper = text.upper()
            if "OWON" in upper or "SPE" in upper:
                return "owon_spe6102"
            if "HMP" in upper or "HAMEG" in upper or "ROHDE" in upper:
                return "hmp4030"
        return "hmp4030"

    def _selected_ac_psu_resource(self) -> str:
        combo = getattr(getattr(self, "ui", None), "comboBox_port", None)
        if isinstance(combo, QtWidgets.QComboBox):
            return str(combo.currentData(QtCore.Qt.ItemDataRole.UserRole) or combo.currentText()).strip()
        return ""

    def _selected_ac_psu_baudrate(self) -> int:
        combo = getattr(getattr(self, "ui", None), "comboBox_baudrate", None)
        if isinstance(combo, QtWidgets.QComboBox):
            try:
                return int(combo.currentText())
            except ValueError:
                pass
        return 9600 if self._selected_ac_psu_backend() == "owon_spe6102" else 115200

    def _sync_ac_psu_from_shared_controls(self) -> None:
        backend = self._selected_ac_psu_backend()
        if backend == "owon_spe6102":
            default_limit = OWON_DEFAULT_VOLTAGE_LIMIT_V
        else:
            default_limit = HMP_DEFAULT_VOLTAGE_LIMIT_V
        current_limit = float(self.spinBox_ac_voltage_limit.value())
        if (
            not math.isfinite(current_limit)
            or current_limit <= 0
            or (backend == "owon_spe6102" and current_limit <= 5.0)
            or (backend != "owon_spe6102" and math.isclose(current_limit, OWON_DEFAULT_VOLTAGE_LIMIT_V))
        ):
            self.spinBox_ac_voltage_limit.setValue(default_limit)
        self._refresh_ac_psu_status()

    def _refresh_ac_psu_status(self) -> None:
        try:
            label = getattr(self, "label_ac_psu_status", None)
        except RuntimeError:
            return
        if not isinstance(label, QtWidgets.QLabel):
            return
        backend = self._selected_ac_psu_backend()
        profile = sweep.POWER_SUPPLY_PROFILES.get(backend, {})
        backend_label = str(profile.get("label", backend))
        resource = self._selected_ac_psu_resource() or "no port selected"
        baudrate = self._selected_ac_psu_baudrate()
        label.setText(f"{backend_label} from shared PSU controls, {resource}, {baudrate} baud")

    def _install_ac_wheel_guard(self, control_root: QtWidgets.QWidget) -> None:
        self._ac_lcr_scroll_area = self._find_parent_scroll_area(control_root)
        for widget in control_root.findChildren((QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
            widget.setProperty("_ac_wheel_guard", True)
            widget.installEventFilter(self)
            if isinstance(widget, QtWidgets.QAbstractSpinBox):
                editor = widget.lineEdit()
                editor.setProperty("_ac_wheel_guard", True)
                editor.installEventFilter(self)

    @staticmethod
    def _find_parent_scroll_area(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea | None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QtWidgets.QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if (
            event.type() == QtCore.QEvent.Type.Wheel
            and isinstance(watched, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QLineEdit))
            and watched.property("_ac_wheel_guard")
        ):
            if isinstance(watched, QtWidgets.QComboBox) and watched.view().isVisible():
                return super().eventFilter(watched, event)
            self._scroll_ac_panel_from_wheel(event)
            return True
        return super().eventFilter(watched, event)

    def _scroll_ac_panel_from_wheel(self, event: QtCore.QEvent) -> None:
        if not isinstance(event, QtGui.QWheelEvent):
            event.ignore()
            return
        scroll_area = self._ac_lcr_scroll_area
        if scroll_area is None:
            event.ignore()
            return
        scrollbar = scroll_area.verticalScrollBar()
        delta = event.pixelDelta().y()
        if delta == 0:
            delta = int(event.angleDelta().y() / 120 * scrollbar.singleStep() * 3)
        if delta != 0:
            scrollbar.setValue(scrollbar.value() - delta)
        event.accept()

    def _configure_lcr_for_current_index(self, *, show_errors: bool = False) -> bool:
        if not self._lcr_plan:
            try:
                self._prepare_lcr_plan()
            except Exception as exc:
                if show_errors:
                    QtWidgets.QMessageBox.warning(self, "Invalid AC settings", str(exc))
                self.label_lcr_status.setText(f"Invalid AC settings: {exc}")
                return False
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
                return False
        plan_count = len(self._lcr_plan)
        level_unit = "A" if setting.level_mode == "current" else "V"
        self.label_lcr_status.setText(
            f"AC setting {index + 1}/{plan_count}: {setting.function}, "
            f"{setting.frequency_hz:g} Hz, {setting.level_value:g} {level_unit}"
        )
        return True

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
            if not self._configure_lcr_for_current_index(show_errors=True):
                return
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

    def _baseline_output_path(self) -> Path:
        log_path = Path(self.build_log_path())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return log_path.with_name(f"{log_path.stem}_baseline_{timestamp}.tsv")

    def _collect_baseline_rows(
        self,
        plan: Sequence[Lcr6000Settings],
        *,
        repeats: int,
    ) -> list[list[str]]:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            raise RuntimeError("LCR meter is not connected")
        rows: list[list[str]] = []
        repeat_count = max(1, int(repeats))
        for setting_index, setting in enumerate(plan, start=1):
            meter.configure(setting)
            self._lcr_plan_index = setting_index - 1
            for repeat_index in range(1, repeat_count + 1):
                reading = self._fetch_baseline_reading()
                self._lcr_last_reading = reading
                rows.append(
                    self._format_baseline_row(
                        setting_index=setting_index,
                        repeat_index=repeat_index,
                        setting=setting,
                        reading=reading,
                    )
                )
                QtWidgets.QApplication.processEvents()
        return rows

    def _fetch_baseline_reading(self, *, attempts: int = 3) -> Lcr6000Reading:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            raise RuntimeError("LCR meter is not connected")
        for attempt in range(1, max(1, int(attempts)) + 1):
            reading = meter.fetch_impedance()
            if reading.raw.strip():
                return reading
            if attempt < attempts:
                self._lcr_last_error = "Empty LCR response"
                QtWidgets.QApplication.processEvents()
                time.sleep(0.25)
        raise RuntimeError("LCR returned an empty response during baseline measurement")

    @staticmethod
    def _format_baseline_row(
        *,
        setting_index: int,
        repeat_index: int,
        setting: Lcr6000Settings,
        reading: Lcr6000Reading,
    ) -> list[str]:
        return [
            reading.timestamp_utc,
            str(setting_index),
            str(repeat_index),
            CurrentAnnealingWindow._format_sample_value(setting.frequency_hz),
            setting.level_mode,
            CurrentAnnealingWindow._format_sample_value(setting.level_value),
            setting.function,
            MainWindow._format_optional_sample_value(reading.primary),
            MainWindow._format_optional_sample_value(reading.secondary),
            MainWindow._format_optional_sample_value(reading.monitor1),
            MainWindow._format_optional_sample_value(reading.monitor2),
            reading.comparator,
            reading.raw,
        ]

    @staticmethod
    def _format_optional_sample_value(value: float | None) -> str:
        if value is None or not math.isfinite(value):
            return ""
        return CurrentAnnealingWindow._format_sample_value(value)

    @staticmethod
    def _write_baseline_file(
        path: str | Path,
        plan: Sequence[Lcr6000Settings],
        rows: Sequence[Sequence[str]],
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write("# AC susceptibility baseline generated from LCR-6200 settings\n")
            for index, setting in enumerate(plan, start=1):
                commands = " ".join(command.strip() for command in commands_for_settings(setting))
                fh.write(f"# AC setting {index}: {commands}\n")
            fh.write(BASELINE_HEADER_LINE + "\n")
            for row in rows:
                fh.write("\t".join(row) + "\n")

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
        return self._format_optional_sample_value(value)

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
