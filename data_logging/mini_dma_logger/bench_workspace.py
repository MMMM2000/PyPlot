from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets


@dataclass(frozen=True)
class BenchDeviceState:
    status: str
    summary: str
    detail: str
    color: str
    action: str = ""
    action_enabled: bool = True


@dataclass(frozen=True)
class BenchWorkspaceState:
    mode: str
    headline: str
    detail: str
    color: str
    primary_action: str
    primary_enabled: bool
    settings_enabled: bool
    settings_editable: bool
    position_reference: str
    load_reference: str
    travel_limits: str
    load_guard: str
    owner: str
    owner_detail: str
    devices: dict[str, BenchDeviceState]


class _StatusDot(QtWidgets.QWidget):
    def __init__(self, color: str = "#69727d", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QtGui.QColor(color)
        self.setFixedSize(10, 10)

    def set_color(self, color: str) -> None:
        self._color = QtGui.QColor(color)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


class BenchDeviceRow(QtWidgets.QFrame):
    action_requested = QtCore.pyqtSignal(str)

    def __init__(self, key: str, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._expanded = False
        self.setObjectName("tmaBenchDeviceRow")
        self.setProperty("benchDevice", True)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 9, 10, 9)
        root.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        self.dot = _StatusDot(parent=self)
        header.addWidget(self.dot)
        names = QtWidgets.QWidget(self)
        names_layout = QtWidgets.QVBoxLayout(names)
        names_layout.setContentsMargins(0, 0, 0, 0)
        names_layout.setSpacing(0)
        self.title_label = QtWidgets.QLabel(title, names)
        self.title_label.setProperty("role", "benchDeviceName")
        self.status_label = QtWidgets.QLabel("Disconnected", names)
        self.status_label.setProperty("role", "metricSecondary")
        names_layout.addWidget(self.title_label)
        names_layout.addWidget(self.status_label)
        header.addWidget(names)
        header.addStretch(1)
        self.summary_label = QtWidgets.QLabel("-", self)
        self.summary_label.setProperty("role", "benchDeviceSummary")
        self.summary_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.summary_label.setMinimumWidth(135)
        header.addWidget(self.summary_label)
        self.action_button = QtWidgets.QPushButton("", self)
        self.action_button.setObjectName(f"bench_{key}_action")
        self.action_button.setMinimumWidth(74)
        self.action_button.clicked.connect(lambda: self.action_requested.emit(self.key))
        header.addWidget(self.action_button)
        self.expand_button = QtWidgets.QToolButton(self)
        self.expand_button.setObjectName(f"bench_{key}_expand")
        self.expand_button.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.expand_button.setToolTip("Show device details")
        self.expand_button.clicked.connect(self.toggle_details)
        header.addWidget(self.expand_button)
        root.addLayout(header)

        self.details_widget = QtWidgets.QWidget(self)
        details_layout = QtWidgets.QHBoxLayout(self.details_widget)
        details_layout.setContentsMargins(18, 1, 32, 1)
        self.detail_label = QtWidgets.QLabel("", self.details_widget)
        self.detail_label.setProperty("role", "metricSecondary")
        self.detail_label.setWordWrap(True)
        details_layout.addWidget(self.detail_label, 1)
        self.details_widget.hide()
        root.addWidget(self.details_widget)

    def toggle_details(self) -> None:
        self._expanded = not self._expanded
        self.details_widget.setVisible(self._expanded)
        self.expand_button.setArrowType(
            QtCore.Qt.ArrowType.DownArrow
            if self._expanded
            else QtCore.Qt.ArrowType.RightArrow
        )

    def apply_state(self, state: BenchDeviceState) -> None:
        self.dot.set_color(state.color)
        self.status_label.setText(state.status)
        self.summary_label.setText(state.summary)
        self.summary_label.setToolTip(state.summary)
        self.detail_label.setText(state.detail)
        self.action_button.setText(state.action)
        self.action_button.setVisible(bool(state.action))
        self.action_button.setEnabled(state.action_enabled)


class TmaBenchWorkspace(QtWidgets.QWidget):
    connect_all_requested = QtCore.pyqtSignal()
    settings_requested = QtCore.pyqtSignal()
    safety_requested = QtCore.pyqtSignal()
    device_action_requested = QtCore.pyqtSignal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tmaBenchWorkspace")
        self.device_rows: dict[str, BenchDeviceRow] = {}
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        toolbar = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Bench readiness", self)
        title.setProperty("role", "inspectorTitle")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        self.settings_button = QtWidgets.QPushButton("Hardware settings", self)
        self.settings_button.setObjectName("bench_hardware_settings")
        self.settings_button.clicked.connect(self.settings_requested)
        toolbar.addWidget(self.settings_button)
        root.addLayout(toolbar)

        readiness = QtWidgets.QFrame(self)
        readiness.setObjectName("tmaBenchReadiness")
        readiness_layout = QtWidgets.QHBoxLayout(readiness)
        readiness_layout.setContentsMargins(13, 11, 13, 11)
        readiness_layout.setSpacing(9)
        self.readiness_dot = _StatusDot(parent=readiness)
        readiness_layout.addWidget(self.readiness_dot)
        words = QtWidgets.QWidget(readiness)
        words_layout = QtWidgets.QVBoxLayout(words)
        words_layout.setContentsMargins(0, 0, 0, 0)
        words_layout.setSpacing(1)
        self.headline_label = QtWidgets.QLabel("Bench disconnected", words)
        self.headline_label.setProperty("role", "benchHeadline")
        self.detail_label = QtWidgets.QLabel("", words)
        self.detail_label.setProperty("role", "metricSecondary")
        self.detail_label.setWordWrap(True)
        words_layout.addWidget(self.headline_label)
        words_layout.addWidget(self.detail_label)
        readiness_layout.addWidget(words, 1)
        self.primary_button = QtWidgets.QPushButton("Auto-connect hardware", readiness)
        self.primary_button.setObjectName("adaptivePrimaryAction")
        self.primary_button.clicked.connect(self.connect_all_requested)
        readiness_layout.addWidget(self.primary_button)
        root.addWidget(readiness)

        devices_header = QtWidgets.QHBoxLayout()
        devices_title = QtWidgets.QLabel("Devices", self)
        devices_title.setProperty("role", "inspectorTitle")
        devices_header.addWidget(devices_title)
        devices_header.addStretch(1)
        ownership_hint = QtWidgets.QLabel("Live values and ownership", self)
        ownership_hint.setProperty("role", "metricSecondary")
        devices_header.addWidget(ownership_hint)
        root.addLayout(devices_header)

        for key, title in (
            ("scale", "Scale"),
            ("motor", "Motor"),
            ("supply", "Power supply"),
            ("ir", "IR temperature"),
        ):
            row = BenchDeviceRow(key, title, self)
            row.action_requested.connect(self.device_action_requested)
            self.device_rows[key] = row
            root.addWidget(row)

        summaries = QtWidgets.QHBoxLayout()
        summaries.setSpacing(10)
        summaries.addWidget(self._build_safety_summary(), 1)
        summaries.addWidget(self._build_owner_summary(), 1)
        root.addLayout(summaries)
        root.addStretch(1)

    def _build_safety_summary(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame(self)
        frame.setObjectName("tmaBenchSummary")
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Safety and references", frame)
        title.setProperty("role", "inspectorTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.safety_button = QtWidgets.QPushButton("Edit", frame)
        self.safety_button.clicked.connect(self.safety_requested)
        header.addWidget(self.safety_button)
        layout.addLayout(header)
        self.safety_labels: dict[str, QtWidgets.QLabel] = {}
        for key, name in (
            ("position", "Position zero"),
            ("load", "Load reference"),
            ("limits", "Travel limits"),
            ("guard", "Load guard"),
        ):
            row = QtWidgets.QWidget(frame)
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(QtWidgets.QLabel(name, row))
            row_layout.addStretch(1)
            value = QtWidgets.QLabel("-", row)
            value.setProperty("role", "benchDeviceSummary")
            value.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            row_layout.addWidget(value)
            self.safety_labels[key] = value
            layout.addWidget(row)
        return frame

    def _build_owner_summary(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame(self)
        frame.setObjectName("tmaBenchSummary")
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        title = QtWidgets.QLabel("Bench ownership", frame)
        title.setProperty("role", "inspectorTitle")
        layout.addWidget(title)
        self.owner_label = QtWidgets.QLabel("No active recipe", frame)
        self.owner_label.setProperty("role", "benchDeviceSummary")
        self.owner_label.setWordWrap(True)
        layout.addWidget(self.owner_label)
        self.owner_detail_label = QtWidgets.QLabel("", frame)
        self.owner_detail_label.setProperty("role", "metricSecondary")
        self.owner_detail_label.setWordWrap(True)
        layout.addWidget(self.owner_detail_label)
        layout.addStretch(1)
        return frame

    def apply_state(self, state: BenchWorkspaceState) -> None:
        self.setProperty("benchMode", state.mode)
        self.style().unpolish(self)
        self.style().polish(self)
        self.readiness_dot.set_color(state.color)
        self.headline_label.setText(state.headline)
        self.detail_label.setText(state.detail)
        self.primary_button.setText(state.primary_action)
        self.primary_button.setVisible(bool(state.primary_action))
        self.primary_button.setEnabled(state.primary_enabled)
        self.settings_button.setEnabled(state.settings_enabled)
        self.safety_button.setEnabled(state.settings_editable)
        self.safety_labels["position"].setText(state.position_reference)
        self.safety_labels["load"].setText(state.load_reference)
        self.safety_labels["limits"].setText(state.travel_limits)
        self.safety_labels["guard"].setText(state.load_guard)
        self.owner_label.setText(state.owner)
        self.owner_detail_label.setText(state.owner_detail)
        for key, device_state in state.devices.items():
            row = self.device_rows.get(key)
            if row is not None:
                row.apply_state(device_state)


__all__ = [
    "BenchDeviceState",
    "BenchWorkspaceState",
    "TmaBenchWorkspace",
]
