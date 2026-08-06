from __future__ import annotations

from dataclasses import dataclass

from PyQt6 import QtCore, QtGui, QtWidgets


@dataclass(frozen=True)
class BenchDeviceState:
    status: str
    summary: str
    detail: str
    color: str


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
    safety_summary: str
    owner: str
    owner_detail: str
    owner_visible: bool
    devices: dict[str, BenchDeviceState]


class _StatusDot(QtWidgets.QWidget):
    def __init__(self, color: str = "#69727d", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QtGui.QColor(color)
        self.setFixedSize(9, 9)

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
    selected = QtCore.pyqtSignal(str)

    def __init__(self, key: str, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("tmaBenchDeviceRow")
        self.setProperty("benchDevice", True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{title} hardware settings")

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(10, 7, 8, 7)
        root.setSpacing(8)
        self.dot = _StatusDot(parent=self)
        root.addWidget(self.dot)

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
        root.addWidget(names)
        root.addStretch(1)

        self.summary_label = QtWidgets.QLabel("-", self)
        self.summary_label.setProperty("role", "benchDeviceSummary")
        self.summary_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.summary_label.setMinimumWidth(125)
        root.addWidget(self.summary_label)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.selected.emit(self.key)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter, QtCore.Qt.Key.Key_Space):
            self.selected.emit(self.key)
            event.accept()
            return
        super().keyPressEvent(event)

    def apply_state(self, state: BenchDeviceState) -> None:
        self.dot.set_color(state.color)
        self.status_label.setText(state.status)
        self.summary_label.setText(state.summary)
        self.summary_label.setToolTip(state.detail)
        self.setToolTip(f"{state.detail}\n\nOpen hardware settings")


class TmaBenchWorkspace(QtWidgets.QWidget):
    connect_all_requested = QtCore.pyqtSignal()
    settings_requested = QtCore.pyqtSignal()
    device_selected = QtCore.pyqtSignal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tmaBenchWorkspace")
        self.device_rows: dict[str, BenchDeviceRow] = {}
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        toolbar = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Bench status", self)
        title.setProperty("role", "inspectorTitle")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        self.settings_button = QtWidgets.QPushButton("Hardware settings", self)
        self.settings_button.setObjectName("bench_hardware_settings")
        self.settings_button.clicked.connect(self.settings_requested)
        toolbar.addWidget(self.settings_button)
        root.addLayout(toolbar)

        readiness = QtWidgets.QWidget(self)
        readiness.setObjectName("tmaBenchReadiness")
        readiness_layout = QtWidgets.QHBoxLayout(readiness)
        readiness_layout.setContentsMargins(9, 5, 8, 7)
        readiness_layout.setSpacing(9)
        self.readiness_dot = _StatusDot(parent=readiness)
        readiness_layout.addWidget(self.readiness_dot)
        words = QtWidgets.QWidget(readiness)
        words_layout = QtWidgets.QVBoxLayout(words)
        words_layout.setContentsMargins(0, 0, 0, 0)
        words_layout.setSpacing(0)
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

        devices_title = QtWidgets.QLabel("Devices", self)
        devices_title.setProperty("role", "inspectorTitle")
        root.addWidget(devices_title)
        for key, title in (
            ("scale", "Scale"),
            ("motor", "Motor"),
            ("supply", "Power supply"),
            ("ir", "IR temperature"),
        ):
            row = BenchDeviceRow(key, title, self)
            row.selected.connect(self.device_selected)
            self.device_rows[key] = row
            root.addWidget(row)

        self.safety_label = QtWidgets.QLabel("", self)
        self.safety_label.setObjectName("tmaBenchSafetyLine")
        self.safety_label.setProperty("role", "metricSecondary")
        self.safety_label.setWordWrap(True)
        root.addWidget(self.safety_label)

        self.owner_widget = QtWidgets.QWidget(self)
        owner_layout = QtWidgets.QVBoxLayout(self.owner_widget)
        owner_layout.setContentsMargins(9, 5, 9, 5)
        owner_layout.setSpacing(0)
        self.owner_label = QtWidgets.QLabel("", self.owner_widget)
        self.owner_label.setProperty("role", "benchDeviceSummary")
        self.owner_detail_label = QtWidgets.QLabel("", self.owner_widget)
        self.owner_detail_label.setProperty("role", "metricSecondary")
        self.owner_detail_label.setWordWrap(True)
        owner_layout.addWidget(self.owner_label)
        owner_layout.addWidget(self.owner_detail_label)
        root.addWidget(self.owner_widget)
        root.addStretch(1)

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
        self.safety_label.setText(state.safety_summary)
        self.owner_label.setText(state.owner)
        self.owner_detail_label.setText(state.owner_detail)
        self.owner_widget.setVisible(state.owner_visible)
        for key, device_state in state.devices.items():
            row = self.device_rows.get(key)
            if row is not None:
                row.apply_state(device_state)


__all__ = [
    "BenchDeviceState",
    "BenchWorkspaceState",
    "TmaBenchWorkspace",
]
