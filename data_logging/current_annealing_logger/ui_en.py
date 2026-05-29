"""English, layout-based UI for the Current Annealing Logger.

This UI mirrors the controls expected by ``current_annealing_logger.py``
but presents them with modern layouts and English labels. Object names
match the English identifiers used throughout the logger logic.
"""

from __future__ import annotations

import re
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets
try:
    # Reuse InfoLineEdit from data logger for inline info and validation
    from data_logging.data_logger.file_name_builder import InfoLineEdit
except Exception:
    InfoLineEdit = QtWidgets.QLineEdit  # type: ignore[assignment]


class SampleSpinBox(QtWidgets.QSpinBox):
    """Spin box that preserves the alphanumeric sample prefix and suffix."""

    _pattern = re.compile(r"^(.*?)(\d+)(.*)$")

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._prefix = "s"
        self._suffix = ""
        self._allow_empty = False
        self.setObjectName("lineEdit_sample")
        self.setRange(0, 999_999)
        self.setAccelerated(True)
        self.setKeyboardTracking(False)
        self.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        line_edit = self.lineEdit()
        if isinstance(line_edit, QtWidgets.QLineEdit):
            line_edit.setPlaceholderText("Optional sample, e.g., s1 or s2-1")
            line_edit.setToolTip("Optional sample identifier, e.g., s1 or s2-1")

    # Qt uses ``textFromValue``/``valueFromText`` to keep the display updated.
    def textFromValue(self, v: int) -> str:  # noqa: D401
        if self._allow_empty and v == self.minimum():
            return ""
        return f"{self._prefix}{v}{self._suffix}"

    def valueFromText(self, text: Optional[str]) -> int:  # noqa: D401
        if text is None:
            return max(0, min(self.maximum(), self.value()))
        normalized = text.strip()
        if not normalized:
            self._allow_empty = True
            return self.minimum()
        parsed = self._parse(normalized)
        if parsed is None:
            return max(0, min(self.maximum(), self.value()))
        prefix, value, suffix = parsed
        self._prefix = prefix or "s"
        self._suffix = suffix or ""
        self._allow_empty = False
        return max(self.minimum(), min(self.maximum(), value))

    def validate(
        self, text: str | None, pos: int
    ) -> tuple[QtGui.QValidator.State, str, int]:  # noqa: D401
        normalized = (text or "").strip()
        if not normalized:
            return (QtGui.QValidator.State.Acceptable, text or "", pos)
        if self._pattern.match(normalized):
            return (QtGui.QValidator.State.Acceptable, text or "", pos)
        if re.match(r"^.*?\d*$", normalized):
            return (QtGui.QValidator.State.Intermediate, text or "", pos)
        return (QtGui.QValidator.State.Invalid, text or "", pos)

    # Public helpers mirroring QLineEdit for backwards compatibility
    def text(self) -> str:  # noqa: D401
        if self._allow_empty and self.value() == self.minimum():
            return ""
        return super().text()

    def setText(self, text: str) -> None:
        normalized = (text or "").strip()
        if not normalized:
            self._allow_empty = True
            self.blockSignals(True)
            self.setValue(self.minimum())
            self.blockSignals(False)
            return
        parsed = self._parse(normalized)
        if parsed is None:
            self._prefix, value, self._suffix = "s", 1, ""
        else:
            prefix, value, suffix = parsed
            self._prefix = prefix or "s"
            self._suffix = suffix or ""
        self._allow_empty = False
        self.blockSignals(True)
        self.setValue(value)
        self.blockSignals(False)

    def _parse(self, text: str) -> Optional[tuple[str, int, str]]:
        match = self._pattern.match(text.strip())
        if match is None:
            return None
        prefix, digits, suffix = match.groups()
        try:
            value = int(digits)
        except ValueError:
            return None
        return prefix or "s", value, suffix or ""


class Ui_MainWindow(object):
    def __init__(self) -> None:
        self.left_scroll: Optional[QtWidgets.QScrollArea] = None
        self.plot_container: Optional[QtWidgets.QFrame] = None
        self.label_live_voltage: Optional[QtWidgets.QLabel] = None
        self.label_set_current: Optional[QtWidgets.QLabel] = None
        self.lcd_current_mA: Optional[QtWidgets.QLCDNumber] = None
        self.lcd_resistance: Optional[QtWidgets.QLCDNumber] = None

    def setupUi(self, MainWindow: QtWidgets.QMainWindow) -> None:
        MainWindow.setObjectName("CurrentAnnealingMainWindow")
        MainWindow.resize(880, 720)

        font = QtGui.QFont()
        font.setPointSize(10)
        MainWindow.setFont(font)

        self.centralWidget = QtWidgets.QWidget(MainWindow)
        MainWindow.setCentralWidget(self.centralWidget)

        # Root layout: settings on the left, plots on the right
        root = QtWidgets.QHBoxLayout(self.centralWidget)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        left_container = QtWidgets.QWidget(self.centralWidget)
        left_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        left_container_layout = QtWidgets.QVBoxLayout(left_container)
        left_container_layout.setContentsMargins(0, 0, 0, 0)
        left_container_layout.setSpacing(8)

        left_panel = QtWidgets.QWidget(left_container)
        # Allow the left column to shrink to viewport width without forcing
        # a horizontal scrollbar in the scroll area.
        left_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        main_layout = QtWidgets.QVBoxLayout(left_panel)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(12)
        left_scroll = QtWidgets.QScrollArea(left_container)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        # Avoid horizontal scrollbar; let content wrap/stack vertically
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents
        )
        left_scroll.setWidget(left_panel)
        # Expose scroll for overlays from logic
        self.left_scroll = left_scroll
        left_container_layout.addWidget(left_scroll, stretch=1)

        sticky_buttons_frame = QtWidgets.QFrame(left_container)
        sticky_buttons_frame.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        sticky_buttons_frame.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        sticky_buttons_layout = QtWidgets.QHBoxLayout(sticky_buttons_frame)
        sticky_buttons_layout.setContentsMargins(8, 0, 8, 0)
        sticky_buttons_layout.setSpacing(8)
        left_container_layout.addWidget(sticky_buttons_frame)

        root.addWidget(left_container, stretch=0)

        # Right plot container
        self.plot_container = QtWidgets.QFrame(self.centralWidget)
        # Remove frame to avoid bright border lines in dark themes
        self.plot_container.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.plot_container.setMinimumWidth(480)
        self.plot_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        root.addWidget(self.plot_container, stretch=1)

        # ------------------------------------------------------------------
        # Serial basics (frame_serial_settings)
        # ------------------------------------------------------------------
        self.frame_serial_settings = QtWidgets.QFrame(self.centralWidget)
        self.frame_serial_settings.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.frame_serial_settings.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        main_layout.addWidget(self.frame_serial_settings)

        gb_serial = QtWidgets.QGroupBox("Serial settings", self.frame_serial_settings)
        gb_layout = QtWidgets.QHBoxLayout(gb_serial)
        self.groupBox_serial_settings = gb_serial

        # Port selection (modernized): list available ports with names
        self.label_port = QtWidgets.QLabel("Port:")
        gb_layout.addWidget(self.label_port)
        self.comboBox_port = QtWidgets.QComboBox()
        gb_layout.addWidget(self.comboBox_port)
        self.pushButton_refresh_ports = QtWidgets.QPushButton("Refresh")
        gb_layout.addWidget(self.pushButton_refresh_ports)

        self.label_supply = QtWidgets.QLabel("Supply:")
        gb_layout.addWidget(self.label_supply)
        self.comboBox_supply = QtWidgets.QComboBox()
        gb_layout.addWidget(self.comboBox_supply)

        self.label_broker_host = QtWidgets.QLabel("Broker:")
        gb_layout.addWidget(self.label_broker_host)
        self.lineEdit_broker_host = QtWidgets.QLineEdit("127.0.0.1")
        self.lineEdit_broker_host.setMaximumWidth(110)
        self.lineEdit_broker_host.setToolTip("Shared HMP broker host.")
        gb_layout.addWidget(self.lineEdit_broker_host)
        self.spinBox_broker_port = QtWidgets.QSpinBox()
        self.spinBox_broker_port.setRange(1, 65535)
        self.spinBox_broker_port.setValue(8765)
        self.spinBox_broker_port.setMaximumWidth(84)
        self.spinBox_broker_port.setToolTip("Shared HMP broker port.")
        gb_layout.addWidget(self.spinBox_broker_port)

        # Legacy numeric COM spin kept for compatibility, but hidden
        self.label_port_number = QtWidgets.QLabel("COM:")
        self.label_port_number.hide()
        self.spinBox_port_number = QtWidgets.QSpinBox()
        self.spinBox_port_number.setRange(1, 127)
        self.spinBox_port_number.setValue(3)
        self.spinBox_port_number.hide()

        # Baudrate combo
        self.label_baudrate = QtWidgets.QLabel("Baud:")
        gb_layout.addWidget(self.label_baudrate)
        self.comboBox_baudrate = QtWidgets.QComboBox()
        self.comboBox_baudrate.addItems([
            "921600",
            "460800",
            "115200",
            "57600",
            "19200",
            "9600",
        ])
        self.comboBox_baudrate.setCurrentText("115200")
        gb_layout.addWidget(self.comboBox_baudrate)

        gb_layout.addStretch(1)

        self.pushButton_connect_port = QtWidgets.QPushButton("Connect to port")
        gb_layout.addWidget(self.pushButton_connect_port)

        # Fit the group box into the frame
        frame_layout_serial = QtWidgets.QVBoxLayout(self.frame_serial_settings)
        frame_layout_serial.setContentsMargins(0, 0, 0, 0)
        frame_layout_serial.addWidget(gb_serial)

        # ------------------------------------------------------------------
        # Process settings (frame_process_settings)
        # ------------------------------------------------------------------
        self.frame_process_settings = QtWidgets.QFrame(self.centralWidget)
        self.frame_process_settings.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.frame_process_settings.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        main_layout.addWidget(self.frame_process_settings)

        gb_proc = QtWidgets.QGroupBox("Process settings", self.frame_process_settings)
        self.groupBox_process_settings = gb_proc
        grid = QtWidgets.QGridLayout(gb_proc)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        # Keep labels compact while inputs stretch to fill the column
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)

        # Log file location (directory + quick actions)
        self.label_log_dir = QtWidgets.QLabel("Directory:")
        self.lineEdit_log_dir = QtWidgets.QLineEdit()
        self.lineEdit_log_dir.setMinimumWidth(320)
        self.lineEdit_log_dir.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.pushButton_open_dir = QtWidgets.QPushButton("Open")
        self.pushButton_browse_dir = QtWidgets.QPushButton("Browse")
        dir_row = QtWidgets.QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.setSpacing(6)
        dir_row.addWidget(self.lineEdit_log_dir, 1)
        dir_row.addWidget(self.pushButton_open_dir)
        dir_row.addWidget(self.pushButton_browse_dir)
        grid.addWidget(self.label_log_dir, 0, 0)
        grid.addLayout(dir_row, 0, 1)

        # File name preview
        self.label_log_file = QtWidgets.QLabel("File name:")
        self.lineEdit_log_file = QtWidgets.QLineEdit()
        self.lineEdit_log_file.setMinimumWidth(260)
        self.lineEdit_log_file.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.label_extension = QtWidgets.QLabel(".txt")
        file_row = QtWidgets.QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(6)
        file_row.addWidget(self.lineEdit_log_file, 1)
        file_row.addWidget(self.label_extension)
        grid.addWidget(self.label_log_file, 1, 0)
        grid.addLayout(file_row, 1, 1)

        # Legacy single-path widgets kept (hidden) for compatibility with code
        self.label_log_file_legacy = QtWidgets.QLabel("Log file:")
        self.label_log_file_legacy.hide()
        self.lineEdit_log_file_full = QtWidgets.QLineEdit()
        self.lineEdit_log_file_full.setPlaceholderText("data/sample.txt")
        self.lineEdit_log_file_full.hide()
        self.pushButton_select_filename = QtWidgets.QPushButton("...")
        self.pushButton_select_filename.hide()

        # Ramp configuration compacted into two rows
        ramp = QtWidgets.QGridLayout()
        ramp.setContentsMargins(0, 0, 0, 0)
        ramp.setHorizontalSpacing(12)
        ramp.setVerticalSpacing(6)
        self.label_max_current = QtWidgets.QLabel("Max current [mA]:")
        self.spinBox_max_current = QtWidgets.QSpinBox()
        self.spinBox_max_current.setRange(1, 10_000)
        self.spinBox_max_current.setValue(10)
        self.spinBox_max_current.setMaximumWidth(90)
        ramp.addWidget(self.label_max_current, 0, 0)
        ramp.addWidget(self.spinBox_max_current, 0, 1)
        self.label_step = QtWidgets.QLabel("Step [mA]:")
        self.spinBox_step_mA = QtWidgets.QSpinBox()
        self.spinBox_step_mA.setRange(1, 10000)
        self.spinBox_step_mA.setValue(1)
        self.spinBox_step_mA.setMaximumWidth(90)
        ramp.addWidget(self.label_step, 0, 2)
        ramp.addWidget(self.spinBox_step_mA, 0, 3)
        self.label_hold_duration = QtWidgets.QLabel("Hold time [s]:")
        self.spinBox_hold_duration = QtWidgets.QSpinBox()
        self.spinBox_hold_duration.setRange(1, 36000)
        self.spinBox_hold_duration.setValue(1)
        self.spinBox_hold_duration.setMaximumWidth(90)
        ramp.addWidget(self.label_hold_duration, 1, 0)
        ramp.addWidget(self.spinBox_hold_duration, 1, 1)
        self.label_start_current = QtWidgets.QLabel("Start current [mA]:")
        self.spinBox_start_current = QtWidgets.QSpinBox()
        self.spinBox_start_current.setRange(1, 10000)
        self.spinBox_start_current.setValue(10)
        self.spinBox_start_current.setMaximumWidth(90)
        ramp.addWidget(self.label_start_current, 1, 2)
        ramp.addWidget(self.spinBox_start_current, 1, 3)
        ramp.addItem(
            QtWidgets.QSpacerItem(
                0,
                0,
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Minimum,
            ),
            0,
            4,
            2,
            1,
        )
        grid.addLayout(ramp, 2, 0, 1, 2)

        # Hold/Stop button spans the main columns to stay visible
        self.pushButton_hold_current = QtWidgets.QPushButton("Hold current now!")
        self.pushButton_hold_current.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.pushButton_hold_current.setMinimumWidth(220)
        grid.addWidget(self.pushButton_hold_current, 3, 0, 1, 2)

        # Reverse sweep and loops controls
        rev = QtWidgets.QHBoxLayout()
        self.checkBox_reverse = QtWidgets.QCheckBox("Reverse to zero after max")
        self.checkBox_reverse.setChecked(True)
        self.spinBox_loops = QtWidgets.QSpinBox()
        self.spinBox_loops.setRange(0, 100000)
        self.spinBox_loops.setSpecialValueText("∞")
        self.spinBox_loops.setValue(1)
        self.checkBox_infinite_loops = QtWidgets.QCheckBox("∞")
        self.checkBox_infinite_loops.setToolTip("Repeat indefinitely")
        rev.addWidget(self.checkBox_reverse)
        rev.addSpacing(12)
        rev.addWidget(QtWidgets.QLabel("Loops:"))
        rev.addWidget(self.spinBox_loops)
        rev.addWidget(self.checkBox_infinite_loops)
        rev.addStretch(1)
        grid.addLayout(rev, 4, 0, 1, 2)

        # Voltage limit behaviour
        limit_grid = QtWidgets.QGridLayout()
        limit_grid.setContentsMargins(0, 0, 0, 0)
        limit_grid.setHorizontalSpacing(10)
        limit_grid.setVerticalSpacing(6)
        limit_grid.setColumnStretch(6, 1)

        self.label_voltage_limit = QtWidgets.QLabel("Voltage limit [V]:")
        limit_grid.addWidget(self.label_voltage_limit, 0, 0)
        self.spinBox_max_voltage = QtWidgets.QSpinBox()
        self.spinBox_max_voltage.setRange(1, 200)
        self.spinBox_max_voltage.setValue(30)
        self.spinBox_max_voltage.setMaximumWidth(90)
        limit_grid.addWidget(self.spinBox_max_voltage, 0, 1)

        self.label_channel = QtWidgets.QLabel("Channel:")
        limit_grid.addWidget(self.label_channel, 0, 2)
        self.spinBox_channel = QtWidgets.QSpinBox()
        self.spinBox_channel.setRange(0, 4)
        self.spinBox_channel.setValue(3)
        self.spinBox_channel.setMaximumWidth(60)
        self.spinBox_channel.setToolTip("Set to 0 to skip channel selection (for single-channel supplies).")
        limit_grid.addWidget(self.spinBox_channel, 0, 3)

        self.checkBox_reset_on_start = QtWidgets.QCheckBox("Reset supply on start")
        self.checkBox_reset_on_start.setChecked(True)
        limit_grid.addWidget(self.checkBox_reset_on_start, 0, 4, 1, 3)

        self.label_limit_action = QtWidgets.QLabel("When the limit is hit:")
        limit_grid.addWidget(self.label_limit_action, 1, 0, 1, 2)
        self.comboBox_max_voltage_action = QtWidgets.QComboBox()
        self.comboBox_max_voltage_action.addItem("Ask every time", "ask")
        self.comboBox_max_voltage_action.addItem("Hold current (stop increasing)", "hold")
        self.comboBox_max_voltage_action.addItem("Reverse to zero", "reverse")
        self.comboBox_max_voltage_action.addItem("Stop measurement", "stop")
        self.comboBox_max_voltage_action.setToolTip(
            "Choose how the logger reacts when the power supply reaches its voltage compliance limit"
        )
        limit_grid.addWidget(self.comboBox_max_voltage_action, 1, 2, 1, 5)
        grid.addLayout(limit_grid, 5, 0, 1, 2)

        # Name builder (file name preset)
        gb_name = QtWidgets.QGroupBox("File name preset")
        name_grid = QtWidgets.QGridLayout(gb_name)
        self.comboBox_name_preset = QtWidgets.QComboBox()
        self.comboBox_name_preset.addItems(["Current annealing", "Custom"])
        name_grid.addWidget(QtWidgets.QLabel("Preset:"), 0, 0)
        name_grid.addWidget(self.comboBox_name_preset, 0, 1)
        # Fields for the "Current annealing" preset
        self.lineEdit_composition = InfoLineEdit("Chemical composition, e.g., Ni51Fe26Ga21")
        try:
            self.lineEdit_composition.set_validation(r"^[A-Za-z0-9]+$", "Use only letters and numbers")  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_composition.setText("Ni51Fe26Ga21")
        self.lineEdit_composition.setMinimumWidth(300)
        self.lineEdit_microwire = InfoLineEdit("Microwire identifier, e.g., 1_2")
        try:
            self.lineEdit_microwire.set_validation(r"^[A-Za-z0-9_]+$", "Use only letters, numbers, or '_' ")  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_microwire.setText("1_2")
        self.lineEdit_microwire.setMinimumWidth(300)
        self.lineEdit_sample = SampleSpinBox()
        self.lineEdit_sample.setMinimumWidth(300)
        self.lineEdit_sample.setText("s1")
        self.label_composition = QtWidgets.QLabel("Composition:")
        name_grid.addWidget(self.label_composition, 1, 0)
        name_grid.addWidget(self.lineEdit_composition, 1, 1)
        self.label_microwire = QtWidgets.QLabel("Microwire:")
        name_grid.addWidget(self.label_microwire, 2, 0)
        name_grid.addWidget(self.lineEdit_microwire, 2, 1)
        self.label_sample = QtWidgets.QLabel("Sample:")
        name_grid.addWidget(self.label_sample, 3, 0)
        self.sample_row_widget = QtWidgets.QWidget()
        sample_row = QtWidgets.QHBoxLayout(self.sample_row_widget)
        sample_row.setContentsMargins(0, 0, 0, 0)
        sample_row.setSpacing(0)
        sample_row.addWidget(self.lineEdit_sample)
        sample_row.setStretch(0, 1)
        name_grid.addWidget(self.sample_row_widget, 3, 1)
        self.lineEdit_load = InfoLineEdit("Applied load in MPa, e.g., 30 MPa")
        try:
            self.lineEdit_load.set_validation(
                r"^[A-Za-z0-9 _.,+-]*$",
                "Use letters, numbers, spaces, and the characters '._+-'",
            )  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_load.setMinimumWidth(300)
        self.lineEdit_load.setPlaceholderText("Load, e.g., 30 MPa")
        self.label_load = QtWidgets.QLabel("Load:")
        name_grid.addWidget(self.label_load, 4, 0)
        name_grid.addWidget(self.lineEdit_load, 4, 1)
        self.lineEdit_notes = InfoLineEdit("Optional notes to append to the preset name")
        try:
            self.lineEdit_notes.set_validation(
                r"^[A-Za-z0-9 _.,+-]*$",
                "Use letters, numbers, spaces, and the characters '._+-'",
            )  # type: ignore[attr-defined]
        except Exception:
            pass
        self.lineEdit_notes.setMinimumWidth(300)
        self.lineEdit_notes.setPlaceholderText("Notes, e.g., rough pass, test sweep")
        self.label_notes = QtWidgets.QLabel("Notes:")
        name_grid.addWidget(self.label_notes, 5, 0)
        name_grid.addWidget(self.lineEdit_notes, 5, 1)
        # Field for the "Custom" preset
        self.lineEdit_custom_name = InfoLineEdit("Custom file name (safe characters)")
        self.lineEdit_custom_name.setMinimumWidth(300)
        self.label_custom_name = QtWidgets.QLabel("Custom name:")
        name_grid.addWidget(self.label_custom_name, 6, 0)
        name_grid.addWidget(self.lineEdit_custom_name, 6, 1)
        # Hidden by default; shown only when "Custom" preset is selected
        self.label_custom_name.hide()
        self.lineEdit_custom_name.hide()
        name_grid.addItem(
            QtWidgets.QSpacerItem(
                0,
                0,
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Minimum,
            ),
            7,
            0,
        )
        self.pushButton_reset_preset = QtWidgets.QPushButton("Reset")
        name_grid.addWidget(
            self.pushButton_reset_preset,
            7,
            1,
            1,
            1,
            QtCore.Qt.AlignmentFlag.AlignRight,
        )
        grid.addWidget(gb_name, 6, 0, 1, 2)

        # Process progress and time remaining
        self.progressBar_process = QtWidgets.QProgressBar()
        grid.addWidget(self.progressBar_process, 7, 0, 1, 2)
        self.label_time_remaining = QtWidgets.QLabel("Time remaining: N/A")
        grid.addWidget(self.label_time_remaining, 8, 0, 1, 2)
        self.label_time_to_limit = QtWidgets.QLabel("To limit: N/A")
        grid.addWidget(self.label_time_to_limit, 9, 0, 1, 2)

        # Live values group
        self.groupBox_live_values = QtWidgets.QGroupBox("Live values")
        lv = QtWidgets.QGridLayout(self.groupBox_live_values)
        lcd_current = QtWidgets.QLCDNumber()
        lcd_current.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        lcd_current.setDigitCount(6)
        lcd_current.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.label_mA = QtWidgets.QLabel("mA")
        lcd_resistance = QtWidgets.QLCDNumber()
        lcd_resistance.setSegmentStyle(QtWidgets.QLCDNumber.SegmentStyle.Filled)
        lcd_resistance.setDigitCount(6)
        lcd_resistance.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.lcd_current_mA = lcd_current
        self.lcd_resistance = lcd_resistance
        self.label_Ohm = QtWidgets.QLabel("Ohm")
        lv.addWidget(lcd_current, 0, 0)
        lv.addWidget(self.label_mA, 0, 1)
        lv.addWidget(lcd_resistance, 0, 2)
        lv.addWidget(self.label_Ohm, 0, 3)
        grid.addWidget(self.groupBox_live_values, 10, 0, 1, 2)

        # Hold resistance and percent
        hr_layout = QtWidgets.QHBoxLayout()
        self.label_resistance_at_hold_current = QtWidgets.QLabel("0")
        self.label_resistance_percent_from_hold = QtWidgets.QLabel("0")
        self.label_resistance_ohm_suffix = QtWidgets.QLabel("Ohm")
        self.label_percent_suffix = QtWidgets.QLabel("%")
        hr_layout.addWidget(QtWidgets.QLabel("Hold resistance:"))
        hr_layout.addWidget(self.label_resistance_at_hold_current)
        hr_layout.addWidget(self.label_resistance_ohm_suffix)
        hr_layout.addSpacing(16)
        hr_layout.addWidget(QtWidgets.QLabel("Percent from hold:"))
        hr_layout.addWidget(self.label_resistance_percent_from_hold)
        hr_layout.addWidget(self.label_percent_suffix)
        hr_layout.addStretch(1)
        grid.addLayout(hr_layout, 11, 0, 1, 2)

        # Start/Stop and reverse buttons (pinned below the scroll area)
        self.pushButton_start_process = QtWidgets.QPushButton("Start annealing process")
        bfont = QtGui.QFont()
        bfont.setPointSize(12)
        self.pushButton_start_process.setFont(bfont)
        self.pushButton_start_process.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.pushButton_show_history = QtWidgets.QPushButton("Measurement history")
        self.pushButton_show_history.setFont(bfont)
        self.pushButton_show_history.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.pushButton_reverse_now = QtWidgets.QPushButton("Reverse current now")
        self.pushButton_reverse_now.setFont(bfont)
        self.pushButton_reverse_now.setEnabled(False)
        self.pushButton_reverse_now.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        sticky_buttons_layout.addWidget(self.pushButton_start_process)
        sticky_buttons_layout.addWidget(self.pushButton_show_history)
        sticky_buttons_layout.addWidget(self.pushButton_reverse_now)

        left_container_layout.setStretch(0, 1)

        frame_layout_proc = QtWidgets.QVBoxLayout(self.frame_process_settings)
        frame_layout_proc.setContentsMargins(0, 0, 0, 0)
        frame_layout_proc.addWidget(gb_proc)

        # ------------------------------------------------------------------
        # Commands and responses (collapsible)
        # ------------------------------------------------------------------
        self.frame_command_and_response = QtWidgets.QFrame(self.centralWidget)
        self.frame_command_and_response.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        main_layout.addWidget(self.frame_command_and_response)

        frame_layout_cmd = QtWidgets.QVBoxLayout(self.frame_command_and_response)
        frame_layout_cmd.setContentsMargins(0, 0, 0, 0)

        # Toggle header
        header = QtWidgets.QToolButton()
        header.setText("Commands and responses")
        header.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        header.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        header.setCheckable(True)
        header.setChecked(False)
        frame_layout_cmd.addWidget(header)

        # Content container
        self._cmd_container = QtWidgets.QWidget()
        vcmd = QtWidgets.QVBoxLayout(self._cmd_container)
        vcmd.setContentsMargins(8, 4, 8, 8)

        hl = QtWidgets.QHBoxLayout()
        self.lineEdit_serial_command = QtWidgets.QLineEdit()
        self.pushButton_send_serial_command = QtWidgets.QPushButton("Send")
        hl.addWidget(self.lineEdit_serial_command, stretch=1)
        hl.addWidget(self.pushButton_send_serial_command)
        vcmd.addLayout(hl)

        self.label_last_command = QtWidgets.QLabel("")
        self.label_last_command.setWordWrap(True)
        vcmd.addWidget(self.label_last_command)
        self.label_serial_response = QtWidgets.QLabel("")
        self.label_serial_response.setWordWrap(True)
        vcmd.addWidget(self.label_serial_response)

        frame_layout_cmd.addWidget(self._cmd_container)
        self._cmd_container.setVisible(False)

        def _toggle_cmds(checked: bool) -> None:
            self._cmd_container.setVisible(checked)
            header.setArrowType(QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow)

        header.toggled.connect(_toggle_cmds)

        # Status bar
        self.statusBar = QtWidgets.QStatusBar(MainWindow)
        MainWindow.setStatusBar(self.statusBar)

        QtCore.QMetaObject.connectSlotsByName(MainWindow)

