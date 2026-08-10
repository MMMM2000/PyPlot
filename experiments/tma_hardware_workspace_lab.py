from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if "--render-all" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets

from experiments.tma_ui_design_lab import C, StatusDot, hline, install_palette, label


SCREENSHOTS = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "tma-adaptive-workspace"
    / "hardware-study"
)


@dataclass(frozen=True)
class BenchState:
    key: str
    label: str
    headline: str
    detail: str
    primary_action: str
    accent: str
    configuration_locked: bool = False


STATES = {
    "disconnected": BenchState(
        "disconnected",
        "Disconnected",
        "Bench disconnected",
        "No instruments are connected. The rig remains idle.",
        "Connect all",
        C["faint"],
    ),
    "partial": BenchState(
        "partial",
        "Partially connected",
        "2 of 4 devices ready",
        "Motor and IR need attention before a recipe can start.",
        "Retry failed",
        C["orange"],
    ),
    "ready": BenchState(
        "ready",
        "Ready",
        "Bench ready",
        "Required devices and references are ready for setup motion.",
        "Ready",
        C["green"],
    ),
    "active": BenchState(
        "active",
        "Active run",
        "Recipe owns the bench",
        "Connections and hardware settings are locked until the run stops.",
        "Run active",
        C["blue"],
        configuration_locked=True,
    ),
}


@dataclass(frozen=True)
class DevicePresentation:
    status: str
    summary: str
    detail: str
    color: str
    action: str
    enabled: bool = True


DEVICE_STATES: dict[str, dict[str, DevicePresentation]] = {
    "disconnected": {
        "scale": DevicePresentation("Disconnected", "No load data", "USB scale | expected 5 Hz", C["faint"], "Connect"),
        "motor": DevicePresentation("Disconnected", "Position unavailable", "Tic 36v4 | compact motion profile", C["faint"], "Connect"),
        "supply": DevicePresentation("Disconnected", "No channel lease", "Shared HMP broker | CH3 motor, CH4 sweep", C["faint"], "Connect"),
        "ir": DevicePresentation("Optional", "IR disabled", "MLX90614 temperature input", C["faint"], "Enable"),
    },
    "partial": {
        "scale": DevicePresentation("Ready", "0.018 g | 5.1 Hz", "Zero reference captured | raw stream stable", C["green"], "Details"),
        "motor": DevicePresentation("Needs attention", "Tic not responding", "Last known position 0.0000 mm | VIN unavailable", C["orange"], "Retry"),
        "supply": DevicePresentation("Ready", "CH3 + CH4 reserved", "HMP4040 shared broker | outputs off", C["green"], "Details"),
        "ir": DevicePresentation("Unavailable", "No temperature data", "Optional input | last probe timed out", C["orange"], "Retry"),
    },
    "ready": {
        "scale": DevicePresentation("Ready", "0.018 g | 5.1 Hz", "Zero reference captured | raw stream stable", C["green"], ""),
        "motor": DevicePresentation("Ready", "0.0000 mm | 24.1 V", "Tic 36v4 | motion profile loaded", C["green"], ""),
        "supply": DevicePresentation("Ready", "CH3 + CH4 reserved", "HMP4040 shared broker | outputs off", C["green"], ""),
        "ir": DevicePresentation("Ready", "24.8 C | 4.0 Hz", "MLX90614 | optional monitoring enabled", C["green"], ""),
    },
    "active": {
        "scale": DevicePresentation("Streaming", "3.765 g | 5.0 Hz", "Session target attached | sidecar recording", C["green"], "", False),
        "motor": DevicePresentation("Controlled", "3.9120 mm | relaxing", "Recipe control owns the motion queue", C["orange"], "", False),
        "supply": DevicePresentation("Controlled", "18.60 mA | 24.33 V", "CH3 + CH4 leased by this TMA session", C["blue"], "", False),
        "ir": DevicePresentation("Streaming", "37.8 C | 4.0 Hz", "Session temperature recording active", C["green"], "", False),
    },
}


class DeviceRow(QtWidgets.QFrame):
    def __init__(self, key: str, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName(f"device_{key}")
        self.setProperty("deviceRow", True)
        self._expanded = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 10, 12, 10)
        root.setSpacing(8)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        self.dot = StatusDot(C["faint"])
        header.addWidget(self.dot)
        names = QtWidgets.QWidget(self)
        name_layout = QtWidgets.QVBoxLayout(names)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(0)
        name_layout.addWidget(label(title, "device-name"))
        self.status_label = label("Disconnected", "hint")
        name_layout.addWidget(self.status_label)
        header.addWidget(names)
        header.addStretch(1)
        self.summary_label = label("", "device-summary")
        self.summary_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.summary_label)
        self.action_button = QtWidgets.QPushButton("Connect")
        self.action_button.setObjectName(f"{key}_action")
        self.action_button.setMinimumWidth(86)
        header.addWidget(self.action_button)
        self.expand_button = QtWidgets.QToolButton()
        self.expand_button.setObjectName(f"{key}_expand")
        self.expand_button.setToolTip("Show device details")
        self.expand_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.expand_button.clicked.connect(self.toggle_details)
        header.addWidget(self.expand_button)
        root.addLayout(header)
        self.details = QtWidgets.QWidget(self)
        detail_layout = QtWidgets.QHBoxLayout(self.details)
        detail_layout.setContentsMargins(20, 2, 40, 2)
        self.detail_label = label("", "hint")
        detail_layout.addWidget(self.detail_label)
        detail_layout.addStretch(1)
        self.details.hide()
        root.addWidget(self.details)

    def toggle_details(self) -> None:
        self._expanded = not self._expanded
        self.details.setVisible(self._expanded)
        self.expand_button.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if self._expanded else QtCore.Qt.ArrowType.RightArrow
        )

    def apply(self, presentation: DevicePresentation) -> None:
        self.dot.color = QtGui.QColor(presentation.color)
        self.dot.update()
        self.status_label.setText(presentation.status)
        self.summary_label.setText(presentation.summary)
        self.detail_label.setText(presentation.detail)
        self.action_button.setText(presentation.action)
        self.action_button.setEnabled(presentation.enabled)
        self.action_button.setVisible(bool(presentation.action))


class MotionSettingsMenu(QtWidgets.QMenu):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        action = QtWidgets.QWidgetAction(self)
        panel = QtWidgets.QWidget()
        panel.setObjectName("motionSettingsPanel")
        layout = QtWidgets.QFormLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)
        self.step = QtWidgets.QDoubleSpinBox()
        self.step.setRange(0.25, 1000.0)
        self.step.setValue(25.0)
        self.step.setSuffix(" um")
        self.speed = QtWidgets.QDoubleSpinBox()
        self.speed.setRange(0.05, 10.0)
        self.speed.setValue(1.0)
        self.speed.setSuffix(" mm/s")
        layout.addRow("Jog step", self.step)
        layout.addRow("Move speed", self.speed)
        action.setDefaultWidget(panel)
        self.addAction(action)


class ManualMotionPanel(QtWidgets.QFrame):
    motion_changed = QtCore.pyqtSignal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("surface", True)
        self._motion = "idle"
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(label("Manual motion", "section"))
        heading.addStretch(1)
        self.settings_button = QtWidgets.QToolButton()
        self.settings_button.setObjectName("motion_settings")
        self.settings_button.setToolTip("Jog step and move speed")
        self.settings_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.settings_menu = MotionSettingsMenu(self.settings_button)
        self.settings_button.setMenu(self.settings_menu)
        self.settings_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        heading.addWidget(self.settings_button)
        root.addLayout(heading)
        self.connection_hint = label("Connect the bench before moving the motor.", "hint")
        root.addWidget(self.connection_hint)
        self.connect_button = QtWidgets.QPushButton("Auto-connect hardware")
        self.connect_button.setObjectName("manual_connect")
        root.addWidget(self.connect_button)
        moves = QtWidgets.QHBoxLayout()
        moves.setSpacing(8)
        self.up_button = QtWidgets.QPushButton("Increase tension")
        self.up_button.setObjectName("move_up")
        self.up_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowUp))
        self.down_button = QtWidgets.QPushButton("Relax")
        self.down_button.setObjectName("move_down")
        self.down_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowDown))
        moves.addWidget(self.up_button, 1)
        moves.addWidget(self.down_button, 1)
        root.addLayout(moves)
        self.stop_button = QtWidgets.QPushButton("Stop motion")
        self.stop_button.setObjectName("stop_motion")
        self.stop_button.hide()
        root.addWidget(self.stop_button)
        self.up_button.pressed.connect(lambda: self.set_motion("increasing tension"))
        self.down_button.pressed.connect(lambda: self.set_motion("relaxing"))
        self.up_button.released.connect(lambda: self.set_motion("idle"))
        self.down_button.released.connect(lambda: self.set_motion("idle"))
        self.stop_button.clicked.connect(lambda: self.set_motion("idle"))

    def set_motion(self, motion: str) -> None:
        self._motion = motion
        moving = motion != "idle"
        self.stop_button.setVisible(moving)
        self.connection_hint.setText(
            f"Motor {motion}. Release the control to stop."
            if moving
            else "Hold a direction to move; release it to stop."
        )
        self.motion_changed.emit(motion)

    def apply_state(self, state: BenchState) -> None:
        connected = state.key in {"ready", "active"}
        enabled = state.key == "ready"
        if not enabled:
            self.set_motion("idle")
        self.connect_button.setVisible(not connected)
        self.up_button.setEnabled(enabled)
        self.down_button.setEnabled(enabled)
        self.settings_button.setEnabled(enabled)
        if state.key == "active":
            self.connection_hint.setText("Manual motion is locked while the recipe owns the motor.")
        elif enabled:
            self.connection_hint.setText("Hold a direction to move; release it to stop.")
        else:
            self.connection_hint.setText("Connect the bench before moving the motor.")


class SafetyDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Safety and references")
        self.setModal(True)
        self.resize(470, 330)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.position_zero = QtWidgets.QDoubleSpinBox()
        self.position_zero.setRange(-10.0, 10.0)
        self.position_zero.setDecimals(4)
        self.position_zero.setSuffix(" mm")
        self.load_zero = QtWidgets.QDoubleSpinBox()
        self.load_zero.setRange(-100.0, 100.0)
        self.load_zero.setDecimals(4)
        self.load_zero.setValue(0.018)
        self.load_zero.setSuffix(" g")
        self.lower = QtWidgets.QDoubleSpinBox()
        self.lower.setRange(-20.0, 20.0)
        self.lower.setValue(-5.0)
        self.lower.setSuffix(" mm")
        self.upper = QtWidgets.QDoubleSpinBox()
        self.upper.setRange(-20.0, 20.0)
        self.upper.setValue(5.0)
        self.upper.setSuffix(" mm")
        form.addRow("Position reference", self.position_zero)
        form.addRow("Load reference", self.load_zero)
        form.addRow("Lower travel limit", self.lower)
        form.addRow("Upper travel limit", self.upper)
        layout.addLayout(form)
        self.guard = QtWidgets.QCheckBox("Prevent tension moves above the configured load guard")
        self.guard.setChecked(True)
        layout.addWidget(self.guard)
        layout.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class HardwareSettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hardware settings")
        self.resize(760, 520)
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("hardware_settings_tabs")
        self.tabs.addTab(self._connections_page(), "Connections")
        self.tabs.addTab(self._profiles_page(), "Device profiles")
        self.tabs.addTab(self._optional_page(), "Optional IR")
        self.tabs.addTab(self._service_page(), "Service and diagnostics")
        layout.addWidget(self.tabs, 1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _page() -> tuple[QtWidgets.QWidget, QtWidgets.QFormLayout]:
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        return page, form

    def _connections_page(self) -> QtWidgets.QWidget:
        page, form = self._page()
        for name, value in (
            ("Scale", "Auto-detect USB scale"),
            ("Motor", "Tic native USB"),
            ("Power supply", "Shared HMP broker"),
        ):
            combo = QtWidgets.QComboBox()
            combo.addItems([value, "Disabled", "Manual selection"])
            form.addRow(name, combo)
        return page

    def _profiles_page(self) -> QtWidgets.QWidget:
        page, form = self._page()
        profile = QtWidgets.QComboBox()
        profile.addItems(["Tic 36v4 | compact motion", "Tic 36v4 | high torque"])
        form.addRow("Motor profile", profile)
        motor_channel = QtWidgets.QComboBox()
        motor_channel.addItems(["CH3", "CH1", "CH2", "CH4"])
        sweep_channel = QtWidgets.QComboBox()
        sweep_channel.addItems(["CH4", "CH1", "CH2", "CH3"])
        form.addRow("Motor supply", motor_channel)
        form.addRow("Current sweep", sweep_channel)
        voltage = QtWidgets.QDoubleSpinBox()
        voltage.setRange(0.0, 80.0)
        voltage.setValue(35.0)
        voltage.setSuffix(" V")
        form.addRow("Sweep voltage limit", voltage)
        return page

    def _optional_page(self) -> QtWidgets.QWidget:
        page, form = self._page()
        enabled = QtWidgets.QCheckBox("Enable non-contact temperature monitoring")
        enabled.setChecked(True)
        form.addRow("IR sensor", enabled)
        rate = QtWidgets.QDoubleSpinBox()
        rate.setRange(0.5, 20.0)
        rate.setValue(4.0)
        rate.setSuffix(" Hz")
        form.addRow("Polling rate", rate)
        return page

    def _service_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(label("These actions are for setup, maintenance, or fault diagnosis.", "hint"))
        actions = (
            "Physically tare scale",
            "Probe scale connection",
            "Calibrate Tic position",
            "Manual power-supply output",
            "Provision shared broker",
            "Firmware and wiring tools",
        )
        for text in actions:
            button = QtWidgets.QPushButton(text)
            button.setProperty("serviceAction", True)
            layout.addWidget(button)
        layout.addStretch(1)
        return page


class HardwareWorkspaceWindow(QtWidgets.QMainWindow):
    def __init__(self, initial_state: str = "partial") -> None:
        super().__init__()
        self.setWindowTitle("TMA Hardware Workspace Study")
        self.resize(1440, 900)
        self.device_rows: dict[str, DeviceRow] = {}
        self.state_buttons: dict[str, QtWidgets.QPushButton] = {}
        self.current_state = STATES["disconnected"]
        self._apply_style()
        self._build()
        self.set_state(initial_state)

    def _apply_style(self) -> None:
        accent = C["orange"]
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {C['window']}; }}
            QWidget {{ color: {C['text']}; background: transparent; font-family: 'Segoe UI'; font-size: 13px; }}
            QFrame#header {{ background: {C['surface']}; border-bottom: 1px solid {C['line']}; }}
            QFrame[surface='true'] {{ background: {C['surface']}; border: 1px solid {C['line_soft']}; border-radius: 4px; }}
            QFrame[deviceRow='true'] {{ background: {C['surface']}; border: 1px solid {C['line_soft']}; border-radius: 4px; }}
            QFrame#separator {{ color: {C['line_soft']}; background: {C['line_soft']}; max-height: 1px; border: 0; }}
            QLabel[role='product'] {{ font-size: 20px; font-weight: 700; }}
            QLabel[role='direction'] {{ color: {C['muted']}; font-size: 11px; }}
            QLabel[role='page-title'] {{ font-size: 19px; font-weight: 700; }}
            QLabel[role='section'] {{ font-size: 14px; font-weight: 650; }}
            QLabel[role='device-name'] {{ font-weight: 650; }}
            QLabel[role='device-summary'] {{ font-size: 13px; font-weight: 600; }}
            QLabel[role='hint'] {{ color: {C['muted']}; font-size: 11px; }}
            QLabel[role='state'] {{ color: {C['muted']}; font-size: 11px; font-weight: 650; }}
            QPushButton {{ background: {C['surface2']}; border: 1px solid {C['line']}; border-radius: 4px; padding: 7px 12px; min-height: 20px; }}
            QPushButton:hover {{ background: {C['surface3']}; }}
            QPushButton:disabled {{ color: {C['faint']}; background: {C['surface']}; }}
            QPushButton#primary {{ background: {accent}; color: #101214; border-color: {accent}; font-weight: 700; }}
            QPushButton#danger {{ background: {C['red']}; color: white; border-color: #e34b4b; font-weight: 750; min-width: 132px; }}
            QPushButton[stateSelector='true'] {{ border: 0; border-bottom: 2px solid transparent; background: transparent; color: {C['muted']}; }}
            QPushButton[stateSelector='true']:checked {{ color: {C['text']}; border-bottom-color: {accent}; font-weight: 650; }}
            QPushButton#stop_motion {{ border-color: {C['orange']}; color: {C['orange']}; }}
            QToolButton {{ background: {C['surface2']}; border: 1px solid {C['line']}; border-radius: 3px; padding: 5px; }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {C['base']}; border: 1px solid {C['line']}; border-radius: 3px; padding: 6px 8px; selection-background-color: {accent}; }}
            QTabWidget::pane {{ border: 1px solid {C['line_soft']}; }}
            QTabBar::tab {{ background: transparent; color: {C['muted']}; padding: 8px 13px; border-bottom: 1px solid {C['line']}; }}
            QTabBar::tab:selected {{ color: {C['text']}; border-bottom: 2px solid {accent}; }}
            QMenu {{ background: {C['surface']}; border: 1px solid {C['line']}; }}
            QWidget#motionSettingsPanel {{ background: {C['surface']}; }}
            """
        )

    def _build(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(18, 16, 18, 18)
        body_layout.setSpacing(14)
        body_layout.addWidget(self._bench_column(), 7)
        body_layout.addWidget(self._operations_column(), 3)
        root.addWidget(body, 1)

    def _header(self) -> QtWidgets.QWidget:
        header = QtWidgets.QFrame()
        header.setObjectName("header")
        header.setFixedHeight(76)
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(18, 8, 18, 8)
        brand = QtWidgets.QWidget()
        brand_layout = QtWidgets.QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(1)
        brand_layout.addWidget(label("TMA Logger", "product"))
        brand_layout.addWidget(label("Bench readiness and manual setup", "direction"))
        layout.addWidget(brand)
        layout.addStretch(1)
        layout.addWidget(label("Hardware workspace study", "state"))
        emergency = QtWidgets.QPushButton("EMERGENCY STOP")
        emergency.setObjectName("danger")
        layout.addWidget(emergency)
        return header

    def _bench_column(self) -> QtWidgets.QWidget:
        column = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        state_bar = QtWidgets.QHBoxLayout()
        state_bar.setSpacing(2)
        state_bar.addWidget(label("Preview state", "state"))
        state_bar.addSpacing(10)
        for key, state in STATES.items():
            button = QtWidgets.QPushButton(state.label)
            button.setCheckable(True)
            button.setProperty("stateSelector", True)
            button.clicked.connect(lambda _checked=False, selected=key: self.set_state(selected))
            self.state_buttons[key] = button
            state_bar.addWidget(button)
        state_bar.addStretch(1)
        self.settings_button = QtWidgets.QPushButton("Hardware settings")
        self.settings_button.setObjectName("hardware_settings")
        self.settings_button.clicked.connect(self.open_hardware_settings)
        state_bar.addWidget(self.settings_button)
        layout.addLayout(state_bar)

        readiness = QtWidgets.QFrame()
        readiness.setProperty("surface", True)
        readiness_layout = QtWidgets.QHBoxLayout(readiness)
        readiness_layout.setContentsMargins(16, 14, 16, 14)
        self.readiness_dot = StatusDot(C["faint"])
        readiness_layout.addWidget(self.readiness_dot)
        words = QtWidgets.QWidget()
        words_layout = QtWidgets.QVBoxLayout(words)
        words_layout.setContentsMargins(0, 0, 0, 0)
        words_layout.setSpacing(2)
        self.headline = label("Bench disconnected", "page-title")
        self.detail = label("", "hint")
        words_layout.addWidget(self.headline)
        words_layout.addWidget(self.detail)
        readiness_layout.addWidget(words, 1)
        self.primary_button = QtWidgets.QPushButton("Connect all")
        self.primary_button.setObjectName("primary")
        self.primary_button.setMinimumWidth(126)
        self.primary_button.clicked.connect(lambda: self.set_state("ready"))
        readiness_layout.addWidget(self.primary_button)
        layout.addWidget(readiness)

        device_header = QtWidgets.QHBoxLayout()
        device_header.addWidget(label("Devices", "section"))
        device_header.addStretch(1)
        device_header.addWidget(label("Live values and ownership", "hint"))
        layout.addLayout(device_header)
        for key, title in (("scale", "Scale"), ("motor", "Motor"), ("supply", "Power supply"), ("ir", "IR temperature")):
            row = DeviceRow(key, title)
            row.action_button.clicked.connect(lambda _checked=False, selected=key: self._device_action(selected))
            self.device_rows[key] = row
            layout.addWidget(row)
        layout.addStretch(1)
        return column

    def _operations_column(self) -> QtWidgets.QWidget:
        column = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.motion_panel = ManualMotionPanel()
        self.motion_panel.connect_button.clicked.connect(lambda: self.set_state("ready"))
        layout.addWidget(self.motion_panel)

        safety = QtWidgets.QFrame()
        safety.setProperty("surface", True)
        safety_layout = QtWidgets.QVBoxLayout(safety)
        safety_layout.setContentsMargins(14, 12, 14, 14)
        safety_layout.setSpacing(9)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(label("Safety and references", "section"))
        top.addStretch(1)
        self.edit_safety_button = QtWidgets.QPushButton("Edit")
        self.edit_safety_button.setObjectName("edit_safety")
        self.edit_safety_button.clicked.connect(self.open_safety)
        top.addWidget(self.edit_safety_button)
        safety_layout.addLayout(top)
        safety_layout.addWidget(self._summary_line("Position zero", "0.0000 mm", C["green"]))
        safety_layout.addWidget(self._summary_line("Load reference", "0.0180 g", C["green"]))
        safety_layout.addWidget(hline())
        safety_layout.addWidget(self._summary_line("Travel limits", "-5.0 to +5.0 mm", C["blue"]))
        safety_layout.addWidget(self._summary_line("Load guard", "Enabled", C["green"]))
        layout.addWidget(safety)

        ownership = QtWidgets.QFrame()
        ownership.setProperty("surface", True)
        ownership_layout = QtWidgets.QVBoxLayout(ownership)
        ownership_layout.setContentsMargins(14, 12, 14, 14)
        ownership_layout.setSpacing(8)
        ownership_layout.addWidget(label("Bench ownership", "section"))
        self.owner_label = label("No active recipe", "device-summary")
        ownership_layout.addWidget(self.owner_label)
        self.owner_detail = label("Manual setup may use connected devices.", "hint")
        ownership_layout.addWidget(self.owner_detail)
        layout.addWidget(ownership)
        layout.addStretch(1)
        return column

    @staticmethod
    def _summary_line(name: str, value: str, color: str) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(8)
        layout.addWidget(StatusDot(color))
        layout.addWidget(label(name, "hint"))
        layout.addStretch(1)
        layout.addWidget(label(value, "device-summary"))
        return row

    def _device_action(self, key: str) -> None:
        if self.current_state.configuration_locked:
            return
        if self.current_state.key == "disconnected":
            self.set_state("partial")
            return
        if self.current_state.key == "partial" and key in {"motor", "ir"}:
            self.set_state("ready")
            return
        self.device_rows[key].toggle_details()

    def set_state(self, key: str) -> None:
        state = STATES.get(key, STATES["disconnected"])
        self.current_state = state
        for button_key, button in self.state_buttons.items():
            button.setChecked(button_key == state.key)
        self.readiness_dot.color = QtGui.QColor(state.accent)
        self.readiness_dot.update()
        self.headline.setText(state.headline)
        self.detail.setText(state.detail)
        self.primary_button.setText(state.primary_action)
        self.primary_button.setEnabled(state.key in {"disconnected", "partial"})
        self.primary_button.setVisible(state.key in {"disconnected", "partial"})
        for device_key, presentation in DEVICE_STATES[state.key].items():
            self.device_rows[device_key].apply(presentation)
        self.settings_button.setEnabled(not state.configuration_locked)
        self.edit_safety_button.setEnabled(not state.configuration_locked)
        self.motion_panel.apply_state(state)
        if state.key == "active":
            self.owner_label.setText("TMA recipe | session 20260805-1424")
            self.owner_detail.setText("Scale, motor, CH3 and CH4 are reserved by the active run.")
        else:
            self.owner_label.setText("No active recipe")
            self.owner_detail.setText("Manual setup may use connected devices.")

    def open_hardware_settings(self) -> HardwareSettingsDialog | None:
        if self.current_state.configuration_locked:
            return None
        dialog = HardwareSettingsDialog(self)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        self._settings_dialog = dialog
        return dialog

    def open_safety(self) -> SafetyDialog | None:
        if self.current_state.configuration_locked:
            return None
        dialog = SafetyDialog(self)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        self._safety_dialog = dialog
        return dialog


def render_widget(widget: QtWidgets.QWidget, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = QtGui.QPixmap(widget.size())
    pixmap.fill(QtGui.QColor(C["window"]))
    widget.render(pixmap)
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save {path}")


def render_all(app: QtWidgets.QApplication) -> None:
    for key in STATES:
        window = HardwareWorkspaceWindow(key)
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        render_widget(window, SCREENSHOTS / f"hardware-{key}-1440x900.png")
        if key == "ready":
            window.resize(1280, 768)
            app.processEvents()
            render_widget(window, SCREENSHOTS / "hardware-ready-1280x768.png")
            settings = window.open_hardware_settings()
            assert settings is not None
            settings.tabs.setCurrentIndex(3)
            app.processEvents()
            render_widget(settings, SCREENSHOTS / "hardware-settings-service.png")
            settings.close()
            app.processEvents()
        window.close()
        app.processEvents()


def launch(*args: object, **kwargs: object) -> HardwareWorkspaceWindow:
    del args, kwargs
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
    install_palette(app)
    window = HardwareWorkspaceWindow("partial")
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TMA hardware workspace design study")
    parser.add_argument("--render-all", action="store_true")
    parser.add_argument("--state", choices=tuple(STATES), default="partial")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    install_palette(app)
    if args.render_all:
        render_all(app)
        return 0
    window = HardwareWorkspaceWindow(args.state)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
