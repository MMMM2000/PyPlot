from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if "--render-all" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtWidgets


ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "logger-ui-design-lab"
SCREENSHOTS = ROOT / "screenshots"
SOURCE_REFERENCE = ROOT / "source-current-tma.png"


@dataclass(frozen=True)
class Direction:
    key: str
    name: str
    subtitle: str
    accent: str


DIRECTIONS = {
    "refined": Direction(
        "refined",
        "Instrument Refined",
        "The current instrument, edited for clarity",
        "#4f9cf9",
    ),
    "adaptive": Direction(
        "adaptive",
        "Adaptive Workspace",
        "The workspace follows the experiment stage",
        "#e8ad43",
    ),
    "adaptive-v2": Direction(
        "adaptive-v2",
        "Adaptive Workspace v2",
        "Less chrome, stronger signal hierarchy",
        "#e8ad43",
    ),
    "adaptive-v3": Direction(
        "adaptive-v3",
        "Adaptive Workspace v3",
        "Run-state and recipe-editing workflow study",
        "#e8ad43",
    ),
    "adaptive-v4": Direction(
        "adaptive-v4",
        "Adaptive Workspace v4",
        "Target-linked inspection workspace",
        "#e8ad43",
    ),
    "plot-first": Direction(
        "plot-first",
        "Plot-First Control Room",
        "Maximum signal visibility with controls in context",
        "#38c892",
    ),
}


C = {
    "window": "#111316",
    "surface": "#191c20",
    "surface2": "#20242a",
    "surface3": "#272c33",
    "base": "#14171a",
    "line": "#343a42",
    "line_soft": "#292e35",
    "text": "#edf0f3",
    "muted": "#9ca5af",
    "faint": "#69727d",
    "orange": "#f4b942",
    "violet": "#a98bff",
    "blue": "#58a6ff",
    "green": "#35cc77",
    "coral": "#ff7080",
    "teal": "#32c7bb",
    "red": "#c62828",
}


def fixture() -> dict[str, list[float]]:
    count = 520
    time_s: list[float] = []
    stress: list[float] = []
    target: list[float] = []
    current: list[float] = []
    resistance: list[float] = []
    strain: list[float] = []
    displacement: list[float] = []
    for i in range(count):
        x = i / (count - 1)
        cycle = min(19, int(x * 20))
        phase = (x * 20) % 1.0
        tri = 1.0 - abs(phase * 2.0 - 1.0)
        target_mpa = 50.0 * (cycle + 1)
        transform = 1.0 / (1.0 + math.exp(-(tri - 0.48) * 14.0))
        ripple = 7.5 * math.sin(i * 0.23) + 2.5 * math.sin(i * 0.71)
        time_s.append(x * 15000.0)
        target.append(target_mpa)
        stress.append(target_mpa + ripple + 13.0 * (tri - 0.5))
        current.append(1.0 + tri * 29.0)
        resistance.append(270.0 + 145.0 * transform + cycle * 1.8 + 4.0 * math.sin(i * 0.12))
        strain_pct = cycle * 0.48 + transform * (2.7 + cycle * 0.08)
        strain.append(strain_pct)
        displacement.append(strain_pct * 0.44)
    return {
        "time": time_s,
        "stress": stress,
        "target": target,
        "current": current,
        "resistance": resistance,
        "strain": strain,
        "displacement": displacement,
    }


DATA = fixture()


def moving_average(values: list[float], radius: int = 4) -> list[float]:
    result: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        result.append(sum(values[start:end]) / (end - start))
    return result


PROCESSED_STRESS = moving_average(DATA["stress"])
RAW_STRESS = [
    value + 5.5 * math.sin(index * 0.91) + 2.2 * math.sin(index * 1.73)
    for index, value in enumerate(DATA["stress"])
]


class PlotPanel(QtWidgets.QWidget):
    def __init__(
        self,
        title: str,
        series: list[tuple[list[float], list[float], str, str]],
        x_label: str,
        y_label: str,
        *,
        compact: bool = False,
        show_legend: bool = False,
        right_series: list[tuple[list[float], list[float], str, str]] | None = None,
        right_y_label: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.series = series
        self.x_label = x_label
        self.y_label = y_label
        self.compact = compact
        self.show_legend = show_legend
        self.right_series = list(right_series or [])
        self.right_y_label = right_y_label
        self.setMinimumHeight(155 if compact else 230)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor(C["surface"]))
        margin_left = 48 if self.compact else 58
        margin_right = 48 if self.right_series else 18
        margin_top = 34
        margin_bottom = 38 if self.compact else 44
        plot = QtCore.QRectF(
            margin_left,
            margin_top,
            max(10, self.width() - margin_left - margin_right),
            max(10, self.height() - margin_top - margin_bottom),
        )

        title_font = QtGui.QFont(self.font())
        title_font.setPointSizeF(9.2 if self.compact else 10.2)
        title_font.setWeight(QtGui.QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QtGui.QColor(C["text"]))
        painter.drawText(QtCore.QRectF(12, 6, self.width() - 24, 24), QtCore.Qt.AlignmentFlag.AlignCenter, self.title)

        painter.setPen(QtGui.QPen(QtGui.QColor(C["line_soft"]), 1))
        for i in range(5):
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(QtCore.QPointF(plot.left(), y), QtCore.QPointF(plot.right(), y))
        for i in range(6):
            x = plot.left() + plot.width() * i / 5
            painter.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
        painter.setPen(QtGui.QPen(QtGui.QColor(C["line"]), 1))
        painter.drawRect(plot)

        all_series = [*self.series, *self.right_series]
        all_x = [v for xs, _ys, _color, _label in all_series for v in xs]
        all_y = [v for _xs, ys, _color, _label in self.series for v in ys]
        if not all_x or not all_y:
            return
        x_min, x_max = min(all_x), max(all_x)
        y_min, y_max = min(all_y), max(all_y)
        y_pad = max((y_max - y_min) * 0.08, 1e-6)
        y_min -= y_pad
        y_max += y_pad

        right_y_min = 0.0
        right_y_max = 1.0
        if self.right_series:
            all_right_y = [v for _xs, ys, _color, _label in self.right_series for v in ys]
            right_y_min, right_y_max = min(all_right_y), max(all_right_y)
            right_y_pad = max((right_y_max - right_y_min) * 0.08, 1e-6)
            right_y_min -= right_y_pad
            right_y_max += right_y_pad

        def point(x: float, y: float, low: float = y_min, high: float = y_max) -> QtCore.QPointF:
            px = plot.left() + (x - x_min) / max(x_max - x_min, 1e-9) * plot.width()
            py = plot.bottom() - (y - low) / max(high - low, 1e-9) * plot.height()
            return QtCore.QPointF(px, py)

        painter.save()
        painter.setClipRect(plot.adjusted(1, 1, -1, -1))
        for xs, ys, color, _label in self.series:
            path = QtGui.QPainterPath()
            for index, (x, y) in enumerate(zip(xs, ys)):
                p = point(x, y)
                if index == 0:
                    path.moveTo(p)
                else:
                    path.lineTo(p)
            painter.setPen(QtGui.QPen(QtGui.QColor(color), 1.55 if self.compact else 1.8))
            painter.drawPath(path)
        for xs, ys, color, _label in self.right_series:
            path = QtGui.QPainterPath()
            for index, (x, y) in enumerate(zip(xs, ys)):
                p = point(x, y, right_y_min, right_y_max)
                if index == 0:
                    path.moveTo(p)
                else:
                    path.lineTo(p)
            painter.setPen(QtGui.QPen(QtGui.QColor(color), 1.55 if self.compact else 1.8))
            painter.drawPath(path)
        painter.restore()

        if self.show_legend:
            legend_font = QtGui.QFont(self.font())
            legend_font.setPointSizeF(7.5)
            painter.setFont(legend_font)
            x = plot.right() - 8
            for _xs, _ys, color, series_label in reversed(all_series):
                text_width = painter.fontMetrics().horizontalAdvance(series_label)
                x -= text_width
                painter.setPen(QtGui.QColor(C["muted"]))
                painter.drawText(QtCore.QPointF(x, 24), series_label)
                x -= 17
                painter.setPen(QtGui.QPen(QtGui.QColor(color), 2))
                painter.drawLine(QtCore.QPointF(x, 20), QtCore.QPointF(x + 10, 20))
                x -= 14

        axis_font = QtGui.QFont(self.font())
        axis_font.setPointSizeF(7.6 if self.compact else 8.2)
        painter.setFont(axis_font)
        painter.setPen(QtGui.QColor(C["muted"]))
        painter.drawText(QtCore.QRectF(plot.left(), plot.bottom() + 12, plot.width(), 22), QtCore.Qt.AlignmentFlag.AlignCenter, self.x_label)
        painter.save()
        painter.translate(14, plot.center().y())
        painter.rotate(-90)
        painter.drawText(QtCore.QRectF(-plot.height() / 2, -10, plot.height(), 20), QtCore.Qt.AlignmentFlag.AlignCenter, self.y_label)
        painter.restore()
        if self.right_series:
            painter.save()
            painter.translate(self.width() - 13, plot.center().y())
            painter.rotate(90)
            painter.drawText(
                QtCore.QRectF(-plot.height() / 2, -10, plot.height(), 20),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                self.right_y_label,
            )
            painter.restore()

        tick_font = QtGui.QFont(axis_font)
        tick_font.setPointSizeF(7.1)
        painter.setFont(tick_font)
        for i in range(3):
            xv = x_min + (x_max - x_min) * i / 2
            x = plot.left() + plot.width() * i / 2
            label = f"{xv:.0f}" if abs(xv) >= 10 else f"{xv:.1f}"
            painter.drawText(QtCore.QRectF(x - 30, plot.bottom() + 1, 60, 16), QtCore.Qt.AlignmentFlag.AlignCenter, label)
            yv = y_max - (y_max - y_min) * i / 2
            y = plot.top() + plot.height() * i / 2
            painter.drawText(QtCore.QRectF(1, y - 8, margin_left - 7, 16), QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter, f"{yv:.0f}")
            if self.right_series:
                right_yv = right_y_max - (right_y_max - right_y_min) * i / 2
                painter.drawText(
                    QtCore.QRectF(plot.right() + 5, y - 8, margin_right - 7, 16),
                    QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                    f"{right_yv:.1f}",
                )


class StatusDot(QtWidgets.QWidget):
    def __init__(self, color: str = C["green"], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = QtGui.QColor(color)
        self.setFixedSize(10, 10)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(self.color)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


def label(text: str, role: str = "body") -> QtWidgets.QLabel:
    widget = QtWidgets.QLabel(text)
    widget.setProperty("role", role)
    widget.setWordWrap(role in {"hint", "body"})
    return widget


def hline() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
    line.setObjectName("separator")
    return line


def section(title: str, *, subtitle: str | None = None) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
    frame = QtWidgets.QFrame()
    frame.setProperty("surface", True)
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(9)
    layout.addWidget(label(title, "section"))
    if subtitle:
        layout.addWidget(label(subtitle, "hint"))
    return frame, layout


def field_row(name: str, value: str, unit: str = "") -> QtWidgets.QWidget:
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    title = label(name, "field")
    title.setFixedWidth(116)
    edit = QtWidgets.QLineEdit(value)
    edit.setMinimumWidth(90)
    layout.addWidget(title)
    layout.addWidget(edit, 1)
    if unit:
        unit_label = label(unit, "unit")
        unit_label.setFixedWidth(56)
        layout.addWidget(unit_label)
    return row


def switch_row(name: str, checked: bool, detail: str = "") -> QtWidgets.QWidget:
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    check = QtWidgets.QCheckBox(name)
    check.setChecked(checked)
    layout.addWidget(check)
    layout.addStretch(1)
    if detail:
        layout.addWidget(label(detail, "unit"))
    return row


CURRENT_DENSITY_PER_MA = 13.5
LOAD_G_PER_MPA = 0.00753


def compact_number(value: float, *, significant_digits: int = 4) -> str:
    return f"{float(value):.{significant_digits}g}"


def alternate_unit_value(key: str, value: float) -> str:
    if key in {"current_start_mA", "current_end_mA"}:
        return f"{compact_number(value * CURRENT_DENSITY_PER_MA)} A/mm2"
    if key == "current_ramp_mA_s":
        return f"{compact_number(value * CURRENT_DENSITY_PER_MA)} A/mm2/s"
    if key in {"stress_end_mpa", "stress_step_mpa", "return_target_mpa"}:
        return f"{compact_number(value * LOAD_G_PER_MPA)} g"
    if key == "stress_ramp_mpa_s":
        return f"{compact_number(value * LOAD_G_PER_MPA)} g/s"
    return ""


class CompactDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """Keep editable precision without padding displayed values with zeroes."""

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt override
        return f"{float(value):.{self.decimals()}f}".rstrip("0").rstrip(".")


def status_row(name: str, value: str, color: str = C["green"]) -> QtWidgets.QWidget:
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 1, 0, 1)
    layout.setSpacing(8)
    layout.addWidget(StatusDot(color))
    layout.addWidget(label(name, "field"))
    layout.addStretch(1)
    layout.addWidget(label(value, "value"))
    return row


def dual_value_row(
    name: str,
    primary: str,
    secondary: str,
    color: str = C["green"],
) -> QtWidgets.QWidget:
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 1, 0, 1)
    layout.setSpacing(8)
    layout.addWidget(StatusDot(color))
    layout.addWidget(label(name, "field"))
    layout.addStretch(1)
    values = QtWidgets.QWidget()
    values_layout = QtWidgets.QVBoxLayout(values)
    values_layout.setContentsMargins(0, 0, 0, 0)
    values_layout.setSpacing(0)
    primary_label = label(primary, "value")
    secondary_label = label(secondary, "hint")
    primary_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
    secondary_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
    values_layout.addWidget(primary_label)
    values_layout.addWidget(secondary_label)
    layout.addWidget(values)
    return row


def metric(name: str, value: str, unit: str = "", secondary: str = "") -> QtWidgets.QWidget:
    box = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(box)
    layout.setContentsMargins(8, 5, 8, 5)
    layout.setSpacing(0)
    layout.addWidget(label(name, "metric-name"))
    value_label = label(value + (f" {unit}" if unit else ""), "metric-value")
    layout.addWidget(value_label)
    if secondary:
        layout.addWidget(label(secondary, "hint"))
    return box


def stress_plot(compact: bool = False) -> PlotPanel:
    return PlotPanel(
        "Stress and target vs time",
        [
            (DATA["time"], DATA["stress"], C["orange"], "stress"),
            (DATA["time"], DATA["target"], C["violet"], "target"),
        ],
        "Time (s)",
        "Stress (MPa)",
        compact=compact,
    )


def stress_control_plot(compact: bool = False) -> PlotPanel:
    return PlotPanel(
        "Stress control vs time",
        [
            (DATA["time"], RAW_STRESS, C["faint"], "raw"),
            (DATA["time"], PROCESSED_STRESS, C["orange"], "processed"),
            (DATA["time"], DATA["target"], C["violet"], "target"),
        ],
        "Time (s)",
        "Stress (MPa)",
        compact=compact,
        show_legend=True,
    )


def current_plot(compact: bool = False) -> PlotPanel:
    return PlotPanel(
        "Current and resistance vs time",
        [
            (DATA["time"], DATA["current"], C["coral"], "current"),
            (DATA["time"], [(v - 260) / 5 for v in DATA["resistance"]], C["teal"], "resistance"),
        ],
        "Time (s)",
        "Current (mA)",
        compact=compact,
    )


def strain_plot(compact: bool = False) -> PlotPanel:
    return PlotPanel(
        "Strain and displacement vs current",
        [
            (DATA["current"], DATA["strain"], C["green"], "strain"),
            (DATA["current"], DATA["displacement"], C["blue"], "displacement"),
        ],
        "Measured current (mA)",
        "Strain (%)",
        compact=compact,
    )


def load_plot(compact: bool = False) -> PlotPanel:
    return PlotPanel(
        "Load and tensile displacement vs time",
        [
            (DATA["time"], [v * 0.00753 for v in DATA["stress"]], C["orange"], "load"),
            (DATA["time"], DATA["displacement"], C["blue"], "displacement"),
        ],
        "Time (s)",
        "Load (g)",
        compact=compact,
    )


def target_segment(target_index: int, field: str) -> list[float]:
    return series_segment(DATA[field], target_index)


def series_segment(values: list[float], target_index: int) -> list[float]:
    start = target_index * len(values) // 20
    end = (target_index + 1) * len(values) // 20
    return values[start:end]


def target_time(target_index: int) -> list[float]:
    values = target_segment(target_index, "time")
    if not values:
        return []
    return [value - values[0] for value in values]


def strain_outcome_plot(target_mpa: int | None = None) -> PlotPanel:
    completed_colors = (
        "#24563b",
        "#286344",
        "#2a7049",
        "#2d7d50",
        "#2f8956",
        "#31965c",
        "#33a362",
        "#34af69",
        "#35bb70",
    )
    series: list[tuple[list[float], list[float], str, str]] = []
    target_indexes = range(10) if target_mpa is None else (target_mpa // 50 - 1,)
    for target_index in target_indexes:
        series_target_mpa = (target_index + 1) * 50
        color = C["orange"] if series_target_mpa == 500 else completed_colors[target_index]
        series.append(
            (
                target_segment(target_index, "current"),
                target_segment(target_index, "strain"),
                color,
                f"{series_target_mpa} MPa",
            )
        )
    title = (
        "Strain vs current | all measured stress targets"
        if target_mpa is None
        else f"Strain vs current | {target_mpa} MPa"
    )
    panel = PlotPanel(
        title,
        series,
        "Measured current (mA)",
        "Strain (%)",
    )
    panel.setObjectName("strain_outcome_plot")
    return panel


def resistance_outcome_plot(target_mpa: int | None = None) -> PlotPanel:
    completed_colors = (
        "#1d5854",
        "#206660",
        "#23736c",
        "#268079",
        "#298d85",
        "#2b9a92",
        "#2da79e",
        "#2fb4aa",
        "#31c0b6",
    )
    series: list[tuple[list[float], list[float], str, str]] = []
    target_indexes = range(10) if target_mpa is None else (target_mpa // 50 - 1,)
    for target_index in target_indexes:
        series_target_mpa = (target_index + 1) * 50
        color = C["orange"] if series_target_mpa == 500 else completed_colors[target_index]
        series.append(
            (
                target_segment(target_index, "current"),
                target_segment(target_index, "resistance"),
                color,
                f"{series_target_mpa} MPa",
            )
        )
    title = (
        "Resistance vs current | all measured stress targets"
        if target_mpa is None
        else f"Resistance vs current | {target_mpa} MPa"
    )
    panel = PlotPanel(
        title,
        series,
        "Measured current (mA)",
        "Resistance (ohm)",
    )
    panel.setObjectName("resistance_outcome_plot")
    return panel


def stress_progress_plot(target_mpa: int | None = None) -> PlotPanel:
    if target_mpa is None:
        times = DATA["time"]
        processed = PROCESSED_STRESS
        targets = DATA["target"]
    else:
        target_index = target_mpa // 50 - 1
        times = target_time(target_index)
        processed = series_segment(PROCESSED_STRESS, target_index)
        targets = target_segment(target_index, "target")
    panel = PlotPanel(
        "Stress / load progress",
        [
            (times, processed, C["orange"], "processed"),
            (times, targets, C["violet"], "target"),
        ],
        "Time (s)",
        "Stress (MPa)",
        compact=True,
        right_series=[
            (
                times,
                [value * LOAD_G_PER_MPA for value in processed],
                C["orange"],
                "load",
            )
        ],
        right_y_label="Load (g)",
    )
    panel.setObjectName("stress_progress_plot")
    return panel


def strain_progress_plot(target_mpa: int | None = None) -> PlotPanel:
    if target_mpa is None:
        times = DATA["time"]
        strains = DATA["strain"]
        displacements = DATA["displacement"]
    else:
        target_index = target_mpa // 50 - 1
        times = target_time(target_index)
        strains = target_segment(target_index, "strain")
        displacements = target_segment(target_index, "displacement")
    panel = PlotPanel(
        "Strain / displacement progress",
        [(times, strains, C["green"], "strain")],
        "Time (s)",
        "Strain (%)",
        compact=True,
        right_series=[
            (times, displacements, C["green"], "displacement")
        ],
        right_y_label="Displacement (mm)",
    )
    panel.setObjectName("strain_progress_plot")
    return panel


def current_progress_plot(target_mpa: int | None = None) -> PlotPanel:
    if target_mpa is None:
        times = DATA["time"]
        current = DATA["current"]
    else:
        target_index = target_mpa // 50 - 1
        times = target_time(target_index)
        current = target_segment(target_index, "current")
    panel = PlotPanel(
        "Current progress",
        [(times, current, C["coral"], "current")],
        "Time (s)",
        "Current (mA)",
        compact=True,
    )
    panel.setObjectName("current_progress_plot")
    return panel


class ResistanceTargetPanel(QtWidgets.QFrame):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("surface", True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(label("Resistance vs current", "section"))
        layout.addWidget(label("One stress target at a time", "hint"))
        selector_row = QtWidgets.QHBoxLayout()
        selector_row.addWidget(label("Stress target", "field"))
        selector_row.addStretch(1)
        self.selector = QtWidgets.QComboBox()
        self.selector.setObjectName("resistance_target_selector")
        for target_mpa in range(50, 501, 50):
            self.selector.addItem(f"{target_mpa} MPa", target_mpa)
        self.selector.setCurrentIndex(self.selector.count() - 1)
        selector_row.addWidget(self.selector)
        layout.addLayout(selector_row)
        self.plot = PlotPanel("500 MPa", [], "Current (mA)", "Resistance (ohm)", compact=True)
        self.plot.setObjectName("resistance_target_plot")
        self.plot.setMinimumHeight(175)
        layout.addWidget(self.plot)
        self.selector.currentIndexChanged.connect(self._update_target)
        self._update_target(self.selector.currentIndex())

    def _update_target(self, index: int) -> None:
        target_mpa = int(self.selector.itemData(index))
        target_index = max(0, target_mpa // 50 - 1)
        self.plot.title = f"{target_mpa} MPa target"
        self.plot.series = [
            (
                target_segment(target_index, "current"),
                target_segment(target_index, "resistance"),
                C["teal"],
                "resistance",
            )
        ]
        self.plot.update()


class RemainingSweepsDialog(QtWidgets.QDialog):
    applied = QtCore.pyqtSignal(dict)

    FIELD_SPECS = (
        ("current_start_mA", "Current start", 1.0, "mA", 0.0, 5000.0, 2),
        ("current_end_mA", "Current end", 30.0, "mA", 0.0, 5000.0, 2),
        ("current_ramp_mA_s", "Current ramp", 0.4, "mA/s", 0.01, 1000.0, 2),
        ("stress_end_mpa", "Final stress", 1000.0, "MPa", 50.0, 100000.0, 1),
        ("stress_step_mpa", "Stress step", 50.0, "MPa", 0.1, 100000.0, 1),
        ("stress_ramp_mpa_s", "Stress ramp", 5.0, "MPa/s", 0.01, 100000.0, 2),
        ("return_target_mpa", "Return target", 50.0, "MPa", 0.0, 100000.0, 1),
    )

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update remaining sweeps")
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.setMinimumWidth(730)
        self.controls: dict[str, QtWidgets.QDoubleSpinBox] = {}
        self.equivalent_labels: dict[str, QtWidgets.QLabel] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        root.addWidget(label("Update remaining sweeps", "page-title"))
        root.addWidget(label("Active sweep: 500 MPa | Changes begin at 550 MPa", "hint"))

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.addWidget(label("Setting", "metric-name"), 0, 0)
        grid.addWidget(label("Current", "metric-name"), 0, 1)
        grid.addWidget(label("Proposed", "metric-name"), 0, 2)
        grid.addWidget(label("Equivalent", "metric-name"), 0, 3)
        for row, (key, title, value, unit, minimum, maximum, decimals) in enumerate(
            self.FIELD_SPECS,
            start=1,
        ):
            grid.addWidget(label(title, "field"), row, 0)
            grid.addWidget(label(f"{value:g} {unit}", "value"), row, 1)
            control = CompactDoubleSpinBox()
            control.setObjectName(f"remaining_{key}")
            control.setRange(minimum, maximum)
            control.setDecimals(decimals)
            control.setValue(value)
            control.setSuffix(f" {unit}")
            grid.addWidget(control, row, 2)
            equivalent = label(alternate_unit_value(key, value), "hint")
            equivalent.setObjectName(f"remaining_{key}_equivalent")
            grid.addWidget(equivalent, row, 3)
            control.valueChanged.connect(
                lambda proposed, field_key=key, target=equivalent: target.setText(
                    alternate_unit_value(field_key, float(proposed))
                )
            )
            self.controls[key] = control
            self.equivalent_labels[key] = equivalent
        root.addLayout(grid)

        hold = QtWidgets.QCheckBox("Pause current ramp while stress target recovers")
        hold.setObjectName("remaining_hold_enabled")
        hold.setChecked(True)
        root.addWidget(hold)
        root.addWidget(hline())
        root.addWidget(label("The current 500 MPa sweep remains unchanged.", "hint"))

        buttons = QtWidgets.QDialogButtonBox()
        cancel = buttons.addButton("Cancel", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        apply_button = buttons.addButton("Apply to 10 remaining targets", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        apply_button.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        apply_button.clicked.connect(self._apply)
        root.addWidget(buttons)

    def _apply(self) -> None:
        payload = {key: float(control.value()) for key, control in self.controls.items()}
        payload["hold_enabled"] = bool(self.findChild(QtWidgets.QCheckBox, "remaining_hold_enabled").isChecked())
        self.applied.emit(payload)
        self.accept()


class DesignWindow(QtWidgets.QMainWindow):
    def __init__(self, direction: Direction) -> None:
        super().__init__()
        self.direction = direction
        self.stage_buttons: list[QtWidgets.QPushButton] = []
        self.remaining_editor: RemainingSweepsDialog | None = None
        self.last_remaining_update: dict[str, float | bool] | None = None
        self.remaining_current_value: QtWidgets.QLabel | None = None
        self.remaining_current_equivalent: QtWidgets.QLabel | None = None
        self.remaining_stress_value: QtWidgets.QLabel | None = None
        self.remaining_stress_equivalent: QtWidgets.QLabel | None = None
        self.target_follow_button: QtWidgets.QPushButton | None = None
        self.target_tree: QtWidgets.QTreeWidget | None = None
        self.target_tree_items: dict[int | None, QtWidgets.QTreeWidgetItem] = {}
        self.target_view_context: QtWidgets.QLabel | None = None
        self.target_return_button: QtWidgets.QPushButton | None = None
        self.target_result_tabs: QtWidgets.QTabWidget | None = None
        self.target_outcome_plot: PlotPanel | None = None
        self.target_resistance_plot: PlotPanel | None = None
        self.target_stress_progress: PlotPanel | None = None
        self.target_strain_progress: PlotPanel | None = None
        self.target_current_progress: PlotPanel | None = None
        self.target_view_mpa: int | None = 500
        self.target_follow_active = True
        self.setWindowTitle(f"TMA UI Design Lab - {direction.name}")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 720)
        self._apply_style()
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_prepare())
        self.stack.addWidget(self._build_run())
        self.stack.addWidget(self._build_review())
        root.addWidget(self.stack, 1)
        root.addWidget(self._build_dock())
        self.set_stage("run")

    def _apply_style(self) -> None:
        accent = self.direction.accent
        base_widgets = (
            f"QMainWindow {{ background: {C['window']}; }} "
            f"QWidget {{ color: {C['text']}; background: transparent; "
            "font-family: 'Segoe UI'; font-size: 13px; }}"
            if self.direction.key in {"adaptive-v2", "adaptive-v3", "adaptive-v4"}
            else f"QMainWindow, QWidget {{ background: {C['window']}; color: {C['text']}; font-family: 'Segoe UI'; font-size: 13px; }}"
        )
        self.setStyleSheet(
            f"""
            {base_widgets}
            QFrame[surface='true'] {{ background: {C['surface']}; border: 1px solid {C['line_soft']}; border-radius: 4px; }}
            QFrame[selected='true'] {{ background: {C['surface2']}; border: 0; border-left: 3px solid {accent}; border-radius: 2px; }}
            QFrame#header {{ background: {C['surface']}; border-bottom: 1px solid {C['line']}; }}
            QFrame#dock {{ background: {C['surface']}; border-top: 1px solid {C['line']}; }}
            QFrame#separator {{ color: {C['line_soft']}; background: {C['line_soft']}; max-height: 1px; border: 0; }}
            QLabel[role='product'] {{ font-size: 20px; font-weight: 700; }}
            QLabel[role='direction'] {{ color: {C['muted']}; font-size: 11px; }}
            QLabel[role='section'] {{ font-size: 14px; font-weight: 650; }}
            QLabel[role='page-title'] {{ font-size: 17px; font-weight: 650; }}
            QLabel[role='hint'] {{ color: {C['muted']}; font-size: 11px; }}
            QLabel[role='field'] {{ color: {C['text']}; }}
            QLabel[role='unit'], QLabel[role='metric-name'] {{ color: {C['muted']}; font-size: 11px; }}
            QLabel[role='value'] {{ color: {C['text']}; font-weight: 600; }}
            QLabel[role='metric-value'] {{ font-size: 18px; font-weight: 700; }}
            QLabel[role='phase'] {{ background: {accent}; color: #101214; border-radius: 3px; padding: 4px 8px; font-size: 11px; font-weight: 750; }}
            QPushButton {{ background: {C['surface2']}; border: 1px solid {C['line']}; border-radius: 4px; padding: 7px 12px; min-height: 20px; }}
            QPushButton:hover {{ background: {C['surface3']}; }}
            QPushButton:pressed {{ background: {C['base']}; }}
            QPushButton[stage='true'] {{ border: 0; background: transparent; color: {C['muted']}; padding: 8px 12px; }}
            QPushButton[stage='true'][active='true'] {{ color: {C['text']}; border-bottom: 2px solid {accent}; font-weight: 650; }}
            QPushButton#primary {{ background: {accent}; color: #101214; border-color: {accent}; font-weight: 700; }}
            QPushButton#danger {{ background: {C['red']}; color: white; border-color: #e34b4b; font-weight: 750; min-width: 132px; }}
            QPushButton#followActive:checked {{ background: {accent}; color: #101214; border-color: {accent}; font-weight: 700; }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {C['base']}; border: 1px solid {C['line']}; border-radius: 3px; padding: 6px 8px; selection-background-color: {accent}; }}
            QTreeWidget#targetNavigator {{ background: transparent; border: 0; outline: 0; }}
            QTreeWidget#targetNavigator::item {{ min-height: 27px; padding: 2px 5px; color: {C['muted']}; }}
            QTreeWidget#targetNavigator::item:selected {{ background: {C['surface2']}; color: {C['text']}; border-left: 3px solid {accent}; }}
            QCheckBox {{ spacing: 8px; }}
            QCheckBox::indicator {{ width: 15px; height: 15px; }}
            QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; }}
            QProgressBar {{ background: {C['base']}; border: 1px solid {C['line']}; border-radius: 3px; text-align: center; min-height: 20px; }}
            QProgressBar::chunk {{ background: {accent}; }}
            QScrollArea {{ border: 0; background: transparent; }}
            QScrollBar:vertical {{ background: {C['base']}; width: 10px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {C['line']}; min-height: 28px; border-radius: 4px; }}
            QTabWidget::pane {{ border: 0; }}
            QTabBar::tab {{ background: transparent; color: {C['muted']}; padding: 8px 13px; border-bottom: 1px solid {C['line']}; }}
            QTabBar::tab:selected {{ color: {C['text']}; border-bottom: 2px solid {accent}; }}
            """
        )

    def _build_header(self) -> QtWidgets.QWidget:
        header = QtWidgets.QFrame()
        header.setObjectName("header")
        header.setFixedHeight(76 if self.direction.key != "plot-first" else 66)
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(16)
        brand = QtWidgets.QWidget()
        brand_layout = QtWidgets.QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)
        brand_layout.addWidget(label("TMA Logger", "product"))
        brand_layout.addWidget(label(self.direction.subtitle, "direction"))
        brand.setMinimumWidth(255)
        layout.addWidget(brand)
        for stage in ("Prepare", "Run", "Review"):
            button = QtWidgets.QPushButton(stage)
            button.setProperty("stage", True)
            button.setProperty("stage_key", stage.lower())
            button.clicked.connect(lambda _checked=False, value=stage.lower(): self.set_stage(value))
            self.stage_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        if self.direction.key in {"adaptive-v3", "adaptive-v4"}:
            layout.addWidget(metric("TENSION", "508 MPa", secondary="3.83 g"))
            layout.addWidget(metric("STRAIN", "7.84 %"))
            layout.addWidget(metric("CURRENT", "18.6 mA", secondary="251 A/mm2"))
        elif self.direction.key != "plot-first":
            for name, value in (("LOAD", "3.83 g"), ("STRESS", "508 MPa"), ("STRAIN", "7.84 %"), ("SUPPLY", "18.6 mA")):
                layout.addWidget(metric(name, value))
        else:
            layout.addWidget(label("Ni48Fe25Ga27 2/5   9.7 um", "value"))
            layout.addWidget(label("508 MPa / 500 MPa", "value"))
        emergency = QtWidgets.QPushButton("EMERGENCY STOP")
        emergency.setObjectName("danger")
        layout.addWidget(emergency)
        return header

    def _build_dock(self) -> QtWidgets.QWidget:
        dock = QtWidgets.QFrame()
        dock.setObjectName("dock")
        dock.setFixedHeight(66)
        layout = QtWidgets.QHBoxLayout(dock)
        layout.setContentsMargins(18, 9, 18, 9)
        layout.setSpacing(10)
        self.dock_state = label("Ready to start", "value")
        self.dock_detail = label("Estimated 13,185 points | 1 h 50 min", "hint")
        status = QtWidgets.QWidget()
        status_layout = QtWidgets.QVBoxLayout(status)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(1)
        status_layout.addWidget(self.dock_state)
        status_layout.addWidget(self.dock_detail)
        layout.addWidget(status, 1)
        log_button = QtWidgets.QPushButton("Run log")
        layout.addWidget(log_button)
        self.start_button = QtWidgets.QPushButton("Start recipe")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._handle_primary_action)
        layout.addWidget(self.start_button)
        self.pause_button = QtWidgets.QPushButton("Pause")
        layout.addWidget(self.pause_button)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(lambda: self.set_stage("review"))
        layout.addWidget(self.stop_button)
        return dock

    def _handle_primary_action(self) -> None:
        stage = self.stack.currentIndex()
        if stage == 0:
            self.set_stage("run")
        elif stage == 1 and self.direction.key in {"adaptive-v3", "adaptive-v4"}:
            self.open_remaining_sweeps_editor()
        elif stage == 2:
            self.set_stage("prepare")

    def open_remaining_sweeps_editor(self) -> RemainingSweepsDialog:
        if self.remaining_editor is not None and self.remaining_editor.isVisible():
            self.remaining_editor.raise_()
            self.remaining_editor.activateWindow()
            return self.remaining_editor
        dialog = RemainingSweepsDialog(self)
        dialog.applied.connect(self._apply_remaining_sweeps_update)
        dialog.finished.connect(lambda _result: setattr(self, "remaining_editor", None))
        self.remaining_editor = dialog
        dialog.show()
        return dialog

    def _apply_remaining_sweeps_update(self, payload: dict[str, float | bool]) -> None:
        self.last_remaining_update = dict(payload)
        if self.remaining_current_value is not None:
            self.remaining_current_value.setText(
                f"{float(payload['current_start_mA']):g} - {float(payload['current_end_mA']):g} mA"
            )
        if self.remaining_current_equivalent is not None:
            start_density = float(payload["current_start_mA"]) * CURRENT_DENSITY_PER_MA
            end_density = float(payload["current_end_mA"]) * CURRENT_DENSITY_PER_MA
            self.remaining_current_equivalent.setText(
                f"{compact_number(start_density)} - {compact_number(end_density)} A/mm2"
            )
        if self.remaining_stress_value is not None:
            self.remaining_stress_value.setText(
                f"550 - {float(payload['stress_end_mpa']):g} MPa"
            )
        if self.remaining_stress_equivalent is not None:
            end_load_g = float(payload["stress_end_mpa"]) * LOAD_G_PER_MPA
            self.remaining_stress_equivalent.setText(
                f"{compact_number(550 * LOAD_G_PER_MPA)} - {compact_number(end_load_g)} g"
            )
        self.dock_detail.setText("Remaining targets updated | Active 500 MPa sweep unchanged")

    def set_stage(self, stage: str) -> None:
        index = {"prepare": 0, "run": 1, "review": 2}[stage]
        self.stack.setCurrentIndex(index)
        for button in self.stage_buttons:
            active = button.property("stage_key") == stage
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        if stage == "prepare":
            self.dock_state.setText("Ready to start")
            self.dock_detail.setText("Preflight complete | Estimated 13,185 points | 1 h 50 min")
            self.start_button.setText("Start recipe")
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(False)
        elif stage == "run":
            if self.direction.key in {"adaptive-v3", "adaptive-v4"}:
                self.dock_state.setText("At 500 MPa | stress recovery hold at 18.6 mA")
                self.dock_detail.setText(
                    "Target 10/20 | Motor relaxing | Overall 48% | ETA 54 min"
                )
            else:
                self.dock_state.setText("At 500 MPa | sweeping upward at 18.6 mA")
                self.dock_detail.setText("Target 10/20 | Overall 48% | ETA 54 min")
            self.start_button.setText("Update remaining sweeps")
            self.pause_button.setEnabled(True)
            self.stop_button.setEnabled(True)
        else:
            self.dock_state.setText("Recipe complete | 100/100")
            self.dock_detail.setText("Run summary and quality checks are ready")
            self.start_button.setText("New run")
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(False)

    def _build_prepare(self) -> QtWidgets.QWidget:
        return {
            "refined": self._prepare_refined,
            "adaptive": self._prepare_adaptive,
            "adaptive-v2": self._prepare_adaptive,
            "adaptive-v3": self._prepare_adaptive,
            "adaptive-v4": self._prepare_adaptive,
            "plot-first": self._prepare_plot_first,
        }[self.direction.key]()

    def _build_run(self) -> QtWidgets.QWidget:
        return {
            "refined": self._run_refined,
            "adaptive": self._run_adaptive,
            "adaptive-v2": self._run_adaptive_v2,
            "adaptive-v3": self._run_adaptive_v3,
            "adaptive-v4": self._run_adaptive_v4,
            "plot-first": self._run_plot_first,
        }[self.direction.key]()

    def _build_review(self) -> QtWidgets.QWidget:
        return {
            "refined": self._review_refined,
            "adaptive": self._review_adaptive,
            "adaptive-v2": self._review_adaptive,
            "adaptive-v3": self._review_adaptive,
            "adaptive-v4": self._review_adaptive,
            "plot-first": self._review_plot_first,
        }[self.direction.key]()

    def _sample_section(self) -> QtWidgets.QFrame:
        frame, layout = section("Sample", subtitle="Identity and trusted geometry")
        layout.addWidget(field_row("Composition", "Ni48Fe25Ga27"))
        layout.addWidget(field_row("Microwire", "2/5"))
        layout.addWidget(field_row("Diameter", "9.7", "um"))
        layout.addWidget(field_row("Gauge length", "42.1", "mm"))
        layout.addWidget(status_row("Builder match", "exact", C["green"]))
        return frame

    def _recipe_section(self) -> QtWidgets.QFrame:
        frame, layout = section("Iso-stress current sweep", subtitle="Stress sequence and current limits")
        layout.addWidget(field_row("Stress range", "50 - 1000", "MPa"))
        layout.addWidget(field_row("Stress step", "50", "MPa"))
        layout.addWidget(field_row("Stress ramp", "5", "MPa/s"))
        layout.addWidget(field_row("Current range", "1 - 30", "mA"))
        layout.addWidget(field_row("Current ramp", "0.4", "mA/s"))
        layout.addWidget(switch_row("Pause while target recovers", True))
        layout.addWidget(switch_row("First overheating", False, "not required"))
        return frame

    def _hardware_section(self) -> QtWidgets.QFrame:
        frame, layout = section("Hardware", subtitle="Connection and ownership preflight")
        layout.addWidget(status_row("Scale", "COM6 | streaming"))
        layout.addWidget(status_row("Tic motor", "connected"))
        layout.addWidget(status_row("Shared HMP", "COM3 | broker"))
        layout.addWidget(status_row("CH3 motor rail", "owned"))
        layout.addWidget(status_row("CH4 current", "owned"))
        layout.addWidget(status_row("IR probe", "streaming"))
        return frame

    def _preflight_section(self) -> QtWidgets.QFrame:
        frame, layout = section("Preflight", subtitle="Everything required before motion or output")
        for text in (
            "Sample identity and diameter verified",
            "Zero-load reference captured",
            "Gauge length measured",
            "Motor position inside travel limits",
            "Supply channels exclusively leased",
            "Output folder writable",
        ):
            layout.addWidget(status_row(text, "ready"))
        return frame

    def _target_list(self, compact: bool = False) -> QtWidgets.QWidget:
        frame, layout = section("Stress targets", subtitle="10 of 20 active")
        for target in range(50, 1050, 50):
            if compact and target not in {350, 400, 450, 500, 550, 600, 650}:
                continue
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(8)
            color = C["green"] if target < 500 else self.direction.accent if target == 500 else C["faint"]
            row_layout.addWidget(StatusDot(color))
            text = label(f"{target} MPa", "value" if target == 500 else "field")
            row_layout.addWidget(text)
            row_layout.addStretch(1)
            row_layout.addWidget(label(f"{target * 0.00753:.2f} g", "unit"))
            layout.addWidget(row)
        layout.addStretch(1)
        return frame

    def _target_list_v2(self) -> QtWidgets.QWidget:
        frame, layout = section("Stress targets", subtitle="10 of 20 active")
        layout.setSpacing(2)
        for target in range(50, 1050, 50):
            row = QtWidgets.QFrame()
            row.setProperty("selected", target == 500)
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(8, 5, 8, 5)
            row_layout.setSpacing(7)
            color = C["green"] if target < 500 else self.direction.accent if target == 500 else C["faint"]
            row_layout.addWidget(StatusDot(color))
            row_layout.addWidget(label(f"{target} MPa", "value" if target == 500 else "field"))
            row_layout.addStretch(1)
            row_layout.addWidget(label(f"{target * 0.00753:.2f} g", "unit"))
            layout.addWidget(row)
        layout.addStretch(1)
        return frame

    def _target_navigator_v4(self) -> QtWidgets.QWidget:
        frame, layout = section("Stress targets", subtitle="10 of 20 active")
        layout.setSpacing(7)
        follow = QtWidgets.QPushButton("Follow active target")
        follow.setObjectName("followActive")
        follow.setCheckable(True)
        follow.setChecked(True)
        follow.toggled.connect(self._set_follow_active)
        self.target_follow_button = follow
        layout.addWidget(follow)

        tree = QtWidgets.QTreeWidget()
        tree.setObjectName("targetNavigator")
        tree.setColumnCount(2)
        tree.setHeaderHidden(True)
        tree.setRootIsDecorated(False)
        tree.setIndentation(0)
        tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.setColumnWidth(0, 118)
        all_item = QtWidgets.QTreeWidgetItem(["All targets", ""])
        all_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, None)
        tree.addTopLevelItem(all_item)
        self.target_tree_items[None] = all_item
        for target_mpa in range(50, 1050, 50):
            status = "active" if target_mpa == 500 else ""
            item = QtWidgets.QTreeWidgetItem(
                [
                    f"{target_mpa} MPa{f'  {status}' if status else ''}",
                    f"{target_mpa * LOAD_G_PER_MPA:.2f} g",
                ]
            )
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, target_mpa)
            color = C["green"] if target_mpa < 500 else self.direction.accent if target_mpa == 500 else C["faint"]
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(color)))
            item.setForeground(1, QtGui.QBrush(QtGui.QColor(C["muted"])))
            if target_mpa == 500:
                font = item.font(0)
                font.setWeight(QtGui.QFont.Weight.DemiBold)
                item.setFont(0, font)
            tree.addTopLevelItem(item)
            self.target_tree_items[target_mpa] = item
        tree.itemClicked.connect(self._inspect_target_item)
        tree.setCurrentItem(self.target_tree_items[500])
        self.target_tree = tree
        layout.addWidget(tree, 1)
        return frame

    def _set_follow_active(self, checked: bool) -> None:
        if checked:
            self._set_target_view(500, follow_active=True)

    def _inspect_target_item(
        self,
        item: QtWidgets.QTreeWidgetItem,
        _column: int,
    ) -> None:
        target = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if self.target_follow_button is not None:
            blocker = QtCore.QSignalBlocker(self.target_follow_button)
            self.target_follow_button.setChecked(False)
            del blocker
        self._set_target_view(None if target is None else int(target), follow_active=False)

    @staticmethod
    def _copy_plot_state(destination: PlotPanel, source: PlotPanel) -> None:
        destination.title = source.title
        destination.series = source.series
        destination.right_series = source.right_series
        destination.right_y_label = source.right_y_label
        destination.update()
        source.deleteLater()

    def _set_target_view(self, target_mpa: int | None, *, follow_active: bool) -> None:
        self.target_view_mpa = target_mpa
        self.target_follow_active = follow_active
        if self.target_tree is not None:
            self.target_tree.setCurrentItem(self.target_tree_items[target_mpa])
        if self.target_follow_button is not None and self.target_follow_button.isChecked() != follow_active:
            blocker = QtCore.QSignalBlocker(self.target_follow_button)
            self.target_follow_button.setChecked(follow_active)
            del blocker

        if self.target_view_context is not None:
            if follow_active:
                self.target_view_context.setText("Following active target | 500 MPa")
            elif target_mpa is None:
                self.target_view_context.setText("Comparing all measured targets | live target 500 MPa")
            else:
                self.target_view_context.setText(
                    f"Inspecting {target_mpa} MPa | live target remains 500 MPa"
                )
        if self.target_return_button is not None:
            self.target_return_button.setText("Following active" if follow_active else "Return to active")
            self.target_return_button.setEnabled(not follow_active)

        if self.target_outcome_plot is not None:
            self._copy_plot_state(self.target_outcome_plot, strain_outcome_plot(target_mpa))
        if self.target_resistance_plot is not None:
            self._copy_plot_state(
                self.target_resistance_plot,
                resistance_outcome_plot(target_mpa),
            )
        if self.target_stress_progress is not None:
            self._copy_plot_state(self.target_stress_progress, stress_progress_plot(target_mpa))
        if self.target_strain_progress is not None:
            self._copy_plot_state(self.target_strain_progress, strain_progress_plot(target_mpa))
        if self.target_current_progress is not None:
            self._copy_plot_state(self.target_current_progress, current_progress_plot(target_mpa))

    def _live_controls(self) -> QtWidgets.QFrame:
        frame, layout = section("Live adjustments", subtitle="Apply atomically to remaining sweeps")
        layout.addWidget(field_row("Current end", "30", "mA"))
        layout.addWidget(field_row("Current ramp", "0.4", "mA/s"))
        layout.addWidget(field_row("Target band", "adaptive", ""))
        apply_button = QtWidgets.QPushButton("Apply to remaining sweeps")
        apply_button.setObjectName("primary")
        layout.addWidget(apply_button)
        return frame

    def _live_controls_v2(self) -> QtWidgets.QFrame:
        frame, layout = section("Live adjustments", subtitle="Values for remaining sweeps")
        layout.addWidget(field_row("Current end", "30", "mA"))
        layout.addWidget(field_row("Current ramp", "0.4", "mA/s"))
        layout.addWidget(field_row("Target band", "adaptive", ""))
        return frame

    def _run_status(self) -> QtWidgets.QFrame:
        frame, layout = section("Current target", subtitle="500 MPa | sweep 10 of 20")
        progress = QtWidgets.QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(48)
        progress.setFormat("Overall 48% | ETA 54 min")
        layout.addWidget(progress)
        layout.addWidget(status_row("Stress center", "508 MPa", self.direction.accent))
        layout.addWidget(status_row("Acceptance band", "+/- 11 MPa"))
        layout.addWidget(status_row("Measured current", "18.6 mA"))
        layout.addWidget(status_row("Motor", "3.912 mm"))
        layout.addWidget(label("Processed stress remains centered while the transformation response evolves.", "hint"))
        return frame

    def _run_status_v2(self) -> QtWidgets.QFrame:
        frame, layout = section("Active sweep", subtitle="500 MPa | target 10 of 20")
        progress = QtWidgets.QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(48)
        progress.setFormat("48% | ETA 54 min")
        layout.addWidget(progress)
        values = QtWidgets.QGridLayout()
        values.setHorizontalSpacing(12)
        values.setVerticalSpacing(6)
        for index, item in enumerate(
            (
                ("PROCESSED", "508 MPa"),
                ("RAW", "515 MPa"),
                ("BAND", "+/- 11 MPa"),
                ("CURRENT", "18.6 mA"),
            )
        ):
            values.addWidget(metric(*item), index // 2, index % 2)
        layout.addLayout(values)
        layout.addWidget(hline())
        layout.addWidget(status_row("Control state", "recovering target", self.direction.accent))
        layout.addWidget(status_row("Motor", "3.912 mm"))
        return frame

    def _run_status_v3(self) -> QtWidgets.QFrame:
        frame, layout = section("Active sweep", subtitle="500 MPa | target 10 of 20")
        progress = QtWidgets.QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(48)
        progress.setFormat("48% | ETA 54 min")
        layout.addWidget(progress)
        values = QtWidgets.QGridLayout()
        values.setHorizontalSpacing(12)
        values.setVerticalSpacing(6)
        for index, item in enumerate(
            (
                ("TARGET", "500 MPa", "", "3.765 g"),
                ("PROCESSED", "508 MPa", "", "3.825 g"),
                ("ERROR / BAND", "+8 / +/-11 MPa", "", "+0.0602 / +/-0.0828 g"),
                ("CURRENT HELD", "18.6 mA", "", "251 A/mm2"),
            )
        ):
            values.addWidget(metric(*item), index // 2, index % 2)
        layout.addLayout(values)
        layout.addWidget(hline())
        layout.addWidget(status_row("Motor", "relaxing | 3.912 mm", self.direction.accent))
        layout.addWidget(status_row("Raw stress", "515 MPa", C["faint"]))
        return frame

    def _remaining_recipe_v3(self) -> QtWidgets.QFrame:
        frame, layout = section("Remaining recipe", subtitle="Begins after the active 500 MPa sweep")
        current_row = dual_value_row(
            "Current range",
            "1 - 30 mA",
            "13.5 - 405 A/mm2",
            C["faint"],
        )
        current_labels = current_row.findChildren(QtWidgets.QLabel)
        self.remaining_current_value = current_labels[-2]
        self.remaining_current_equivalent = current_labels[-1]
        stress_row = dual_value_row(
            "Stress targets",
            "550 - 1000 MPa",
            "4.142 - 7.53 g",
            C["faint"],
        )
        stress_labels = stress_row.findChildren(QtWidgets.QLabel)
        self.remaining_stress_value = stress_labels[-2]
        self.remaining_stress_equivalent = stress_labels[-1]
        layout.addWidget(current_row)
        layout.addWidget(stress_row)
        layout.addWidget(status_row("Stress step", "50 MPa", C["faint"]))
        layout.addWidget(status_row("Current ramp", "0.4 mA/s", C["faint"]))
        layout.addWidget(status_row("Target tolerance", "automatic | +/-11 MPa", C["green"]))
        return frame

    def _quality_section(self) -> QtWidgets.QFrame:
        frame, layout = section("Quality checks", subtitle="Run-level checks before export")
        layout.addWidget(status_row("Target completion", "20 / 20"))
        layout.addWidget(status_row("Median stress error", "4.1 MPa"))
        layout.addWidget(status_row("95th percentile error", "11.8 MPa"))
        layout.addWidget(status_row("Run log", "complete"))
        layout.addWidget(status_row("Sensor sidecars", "complete"))
        layout.addWidget(status_row("Electrical continuity", "preserved"))
        return frame

    def _files_section(self) -> QtWidgets.QFrame:
        frame, layout = section("Generated files", subtitle="Ni48Fe25Ga27 2_5 iso-stress_run04")
        for file_name, detail in (
            ("measurement.csv", "13,184 rows"),
            ("control_trace.csv", "controller decisions"),
            ("scale_raw.csv", "raw sensor stream"),
            ("run_summary.png", "operator summary"),
            ("metadata.json", "finished"),
        ):
            layout.addWidget(status_row(file_name, detail))
        open_button = QtWidgets.QPushButton("Open run folder")
        layout.addWidget(open_button)
        return frame

    def _prepare_refined(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        tabs = QtWidgets.QTabWidget()
        tabs.setFixedWidth(405)
        recipe_scroll = QtWidgets.QScrollArea()
        recipe_scroll.setWidgetResizable(True)
        recipe_content = QtWidgets.QWidget()
        recipe_layout = QtWidgets.QVBoxLayout(recipe_content)
        recipe_layout.setContentsMargins(8, 8, 8, 8)
        recipe_layout.addWidget(self._sample_section())
        recipe_layout.addWidget(self._recipe_section())
        recipe_layout.addStretch(1)
        recipe_scroll.setWidget(recipe_content)
        tabs.addTab(recipe_scroll, "Recipe")
        tabs.addTab(self._hardware_section(), "Hardware")
        layout.addWidget(tabs)
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        overview, overview_layout = section("Run overview", subtitle="Sample and sequence are ready; no output has been enabled")
        metrics = QtWidgets.QHBoxLayout()
        for item in (("TARGETS", "20"), ("CURRENT", "1 - 30 mA"), ("DURATION", "1 h 50 min"), ("POINTS", "13,185")):
            metrics.addWidget(metric(*item))
        overview_layout.addLayout(metrics)
        right_layout.addWidget(overview)
        body = QtWidgets.QHBoxLayout()
        body.addWidget(self._preflight_section(), 1)
        body.addWidget(self._hardware_section(), 1)
        right_layout.addLayout(body, 1)
        layout.addWidget(right, 1)
        return page

    def _run_refined(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        sidebar = QtWidgets.QWidget()
        side_layout = QtWidgets.QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(10)
        side_layout.addWidget(self._run_status())
        side_layout.addWidget(self._target_list(compact=True))
        side_layout.addWidget(self._live_controls())
        side_layout.addStretch(1)
        sidebar_scroll = QtWidgets.QScrollArea()
        sidebar_scroll.setFixedWidth(370)
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setWidget(sidebar)
        layout.addWidget(sidebar_scroll)
        plots = QtWidgets.QGridLayout()
        plots.setSpacing(12)
        plots.addWidget(load_plot(), 0, 0)
        plots.addWidget(stress_plot(), 0, 1)
        plots.addWidget(current_plot(), 1, 0)
        plots.addWidget(strain_plot(), 1, 1)
        layout.addLayout(plots, 1)
        return page

    def _review_refined(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        sidebar = QtWidgets.QWidget()
        sidebar.setFixedWidth(385)
        side_layout = QtWidgets.QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(10)
        side_layout.addWidget(self._quality_section())
        side_layout.addWidget(self._files_section())
        side_layout.addStretch(1)
        layout.addWidget(sidebar)
        plots = QtWidgets.QGridLayout()
        plots.setSpacing(12)
        plots.addWidget(stress_plot(), 0, 0, 1, 2)
        plots.addWidget(strain_plot(), 1, 0)
        plots.addWidget(current_plot(), 1, 1)
        layout.addLayout(plots, 1)
        return page

    def _prepare_adaptive(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(label("Prepare experiment", "page-title"))
        heading.addStretch(1)
        heading.addWidget(label("All checks update before hardware output is enabled", "hint"))
        layout.addLayout(heading)
        columns = QtWidgets.QHBoxLayout()
        columns.setSpacing(12)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(self._sample_section())
        left.addWidget(self._recipe_section())
        left.addStretch(1)
        columns.addLayout(left, 4)
        middle = QtWidgets.QVBoxLayout()
        middle.addWidget(self._preflight_section())
        reference, reference_layout = section("Previous measurements", subtitle="Exact identity match from Builder project")
        reference_layout.addWidget(status_row("TMA run 03", "11.2% strain"))
        reference_layout.addWidget(status_row("Current annealing", "425 C peak"))
        reference_layout.addWidget(label("Use these as context; launch values remain operator controlled.", "hint"))
        middle.addWidget(reference)
        middle.addStretch(1)
        columns.addLayout(middle, 4)
        right = QtWidgets.QVBoxLayout()
        right.addWidget(self._hardware_section())
        output, output_layout = section("Output", subtitle="New run is allocated only after confirmation")
        output_layout.addWidget(field_row("Folder", "G:/Praha/TMA"))
        output_layout.addWidget(field_row("Run name", "Ni48Fe25Ga27 2_5"))
        output_layout.addWidget(status_row("Storage", "writable"))
        right.addWidget(output)
        right.addStretch(1)
        columns.addLayout(right, 3)
        layout.addLayout(columns, 1)
        return page

    def _run_adaptive(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        targets = self._target_list(compact=False)
        targets.setFixedWidth(200)
        layout.addWidget(targets)
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(12)
        headline = QtWidgets.QHBoxLayout()
        headline.addWidget(label("500 MPa | recovering target center", "page-title"))
        headline.addStretch(1)
        headline.addWidget(label("Processed 508 MPa   Raw 515 MPa   18.6 mA", "value"))
        center_layout.addLayout(headline)
        center_layout.addWidget(stress_plot(), 3)
        support = QtWidgets.QHBoxLayout()
        support.setSpacing(12)
        support.addWidget(current_plot(compact=True), 1)
        support.addWidget(strain_plot(compact=True), 1)
        center_layout.addLayout(support, 2)
        layout.addWidget(center, 1)
        inspector = QtWidgets.QWidget()
        inspector.setFixedWidth(285)
        inspector_layout = QtWidgets.QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(10)
        inspector_layout.addWidget(self._run_status())
        inspector_layout.addWidget(self._live_controls())
        context, context_layout = section("Run context")
        context_layout.addWidget(status_row("Sample", "Ni48Fe25Ga27 2/5"))
        context_layout.addWidget(status_row("Diameter", "9.7 um"))
        context_layout.addWidget(status_row("Temperature", "37.8 C"))
        inspector_layout.addWidget(context)
        inspector_layout.addStretch(1)
        layout.addWidget(inspector)
        return page

    def _run_adaptive_v2(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)

        targets = self._target_list_v2()
        targets.setFixedWidth(205)
        layout.addWidget(targets)

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(12)
        headline = QtWidgets.QHBoxLayout()
        headline.setSpacing(12)
        headline.addWidget(label("500 MPa", "page-title"))
        headline.addWidget(label("recovering target center", "hint"))
        headline.addStretch(1)
        headline.addWidget(status_row("Scale", "3.83 g"))
        headline.addWidget(status_row("Temperature", "37.8 C"))
        center_layout.addLayout(headline)
        center_layout.addWidget(stress_plot(), 3)
        support = QtWidgets.QHBoxLayout()
        support.setSpacing(12)
        support.addWidget(current_plot(compact=True), 1)
        support.addWidget(strain_plot(compact=True), 1)
        center_layout.addLayout(support, 2)
        layout.addWidget(center, 1)

        inspector = QtWidgets.QWidget()
        inspector.setFixedWidth(300)
        inspector_layout = QtWidgets.QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(12)
        inspector_layout.addWidget(self._run_status_v2())
        inspector_layout.addWidget(self._live_controls_v2())
        inspector_layout.addStretch(1)
        layout.addWidget(inspector)
        return page

    def _run_adaptive_v3(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)

        targets = self._target_list_v2()
        targets.setFixedWidth(205)
        layout.addWidget(targets)

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(12)
        headline = QtWidgets.QHBoxLayout()
        headline.setSpacing(10)
        headline.addWidget(label("500 MPa", "page-title"))
        headline.addWidget(label("STRESS RECOVERY HOLD", "phase"))
        headline.addWidget(label("Current held at 18.6 mA while the motor relaxes", "hint"))
        headline.addStretch(1)
        headline.addWidget(status_row("Temperature", "37.8 C"))
        center_layout.addLayout(headline)
        center_layout.addWidget(strain_outcome_plot(), 3)
        support = QtWidgets.QHBoxLayout()
        support.setSpacing(10)
        support.addWidget(stress_progress_plot(), 1)
        support.addWidget(strain_progress_plot(), 1)
        support.addWidget(current_progress_plot(), 1)
        center_layout.addLayout(support, 2)
        layout.addWidget(center, 1)

        inspector = QtWidgets.QWidget()
        inspector.setFixedWidth(320)
        inspector_layout = QtWidgets.QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(0)
        tabs = QtWidgets.QTabWidget()
        tabs.setObjectName("run_inspector_tabs")
        active_page = QtWidgets.QWidget()
        active_layout = QtWidgets.QVBoxLayout(active_page)
        active_layout.setContentsMargins(0, 10, 0, 0)
        active_layout.setSpacing(10)
        active_layout.addWidget(self._run_status_v3())
        active_layout.addWidget(ResistanceTargetPanel())
        active_layout.addStretch(1)
        tabs.addTab(active_page, "Active sweep")
        recipe_page = QtWidgets.QWidget()
        recipe_layout = QtWidgets.QVBoxLayout(recipe_page)
        recipe_layout.setContentsMargins(0, 10, 0, 0)
        recipe_layout.setSpacing(10)
        recipe_layout.addWidget(self._remaining_recipe_v3())
        recipe_layout.addStretch(1)
        tabs.addTab(recipe_page, "Remaining recipe")
        inspector_layout.addWidget(tabs)
        layout.addWidget(inspector)
        return page

    def _run_adaptive_v4(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)

        targets = self._target_navigator_v4()
        targets.setFixedWidth(215)
        layout.addWidget(targets)

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        headline = QtWidgets.QHBoxLayout()
        headline.setSpacing(10)
        headline.addWidget(label("500 MPa", "page-title"))
        headline.addWidget(label("STRESS RECOVERY HOLD", "phase"))
        headline.addWidget(label("Current held at 18.6 mA while the motor relaxes", "hint"))
        headline.addStretch(1)
        headline.addWidget(status_row("Temperature", "37.8 C"))
        center_layout.addLayout(headline)

        view_bar = QtWidgets.QHBoxLayout()
        self.target_view_context = label("Following active target | 500 MPa", "value")
        self.target_view_context.setObjectName("target_view_context")
        view_bar.addWidget(self.target_view_context)
        view_bar.addStretch(1)
        self.target_return_button = QtWidgets.QPushButton("Following active")
        self.target_return_button.setObjectName("return_to_active_target")
        self.target_return_button.setEnabled(False)
        self.target_return_button.clicked.connect(
            lambda: self._set_target_view(500, follow_active=True)
        )
        view_bar.addWidget(self.target_return_button)
        center_layout.addLayout(view_bar)

        self.target_result_tabs = QtWidgets.QTabWidget()
        self.target_result_tabs.setObjectName("target_result_tabs")
        self.target_outcome_plot = strain_outcome_plot(500)
        self.target_resistance_plot = resistance_outcome_plot(500)
        self.target_result_tabs.addTab(self.target_outcome_plot, "Strain vs current")
        self.target_result_tabs.addTab(self.target_resistance_plot, "Resistance vs current")
        center_layout.addWidget(self.target_result_tabs, 3)
        support = QtWidgets.QHBoxLayout()
        support.setSpacing(10)
        self.target_stress_progress = stress_progress_plot(500)
        self.target_strain_progress = strain_progress_plot(500)
        self.target_current_progress = current_progress_plot(500)
        support.addWidget(self.target_stress_progress, 1)
        support.addWidget(self.target_strain_progress, 1)
        support.addWidget(self.target_current_progress, 1)
        center_layout.addLayout(support, 2)
        layout.addWidget(center, 1)

        inspector = QtWidgets.QWidget()
        inspector.setFixedWidth(320)
        inspector_layout = QtWidgets.QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(0)
        tabs = QtWidgets.QTabWidget()
        tabs.setObjectName("run_inspector_tabs")
        active_page = QtWidgets.QWidget()
        active_layout = QtWidgets.QVBoxLayout(active_page)
        active_layout.setContentsMargins(0, 10, 0, 0)
        active_layout.setSpacing(10)
        active_layout.addWidget(self._run_status_v3())
        active_layout.addStretch(1)
        tabs.addTab(active_page, "Active sweep")
        recipe_page = QtWidgets.QWidget()
        recipe_layout = QtWidgets.QVBoxLayout(recipe_page)
        recipe_layout.setContentsMargins(0, 10, 0, 0)
        recipe_layout.setSpacing(10)
        recipe_layout.addWidget(self._remaining_recipe_v3())
        recipe_layout.addStretch(1)
        tabs.addTab(recipe_page, "Remaining recipe")
        inspector_layout.addWidget(tabs)
        layout.addWidget(inspector)

        self._set_target_view(500, follow_active=True)
        return page

    def _review_adaptive(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        metrics_row = QtWidgets.QHBoxLayout()
        metrics_row.addWidget(label("Review completed run", "page-title"), 1)
        for item in (("MAX STRESS", "1008 MPa"), ("STRAIN SPAN", "11.8 %"), ("MEDIAN ERROR", "4.1 MPa"), ("DURATION", "4 h 12 min")):
            metrics_row.addWidget(metric(*item))
        layout.addLayout(metrics_row)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        plots = QtWidgets.QVBoxLayout()
        plots.addWidget(strain_plot(), 3)
        plots.addWidget(stress_plot(compact=True), 2)
        body.addLayout(plots, 1)
        inspector = QtWidgets.QWidget()
        inspector.setFixedWidth(340)
        inspector_layout = QtWidgets.QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(10)
        inspector_layout.addWidget(self._quality_section())
        inspector_layout.addWidget(self._files_section())
        inspector_layout.addStretch(1)
        body.addWidget(inspector)
        layout.addLayout(body, 1)
        return page

    def _prepare_plot_first(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(label("Configure one run", "page-title"))
        heading.addStretch(1)
        heading.addWidget(label("Ni48Fe25Ga27 2/5 | trusted diameter 9.7 um", "value"))
        layout.addLayout(heading)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        form = QtWidgets.QVBoxLayout()
        form.addWidget(self._sample_section())
        form.addWidget(self._recipe_section())
        form.addStretch(1)
        body.addLayout(form, 5)
        center = QtWidgets.QVBoxLayout()
        center.addWidget(self._preflight_section())
        center.addWidget(self._hardware_section())
        center.addStretch(1)
        body.addLayout(center, 4)
        preview = QtWidgets.QVBoxLayout()
        expected, expected_layout = section("Expected measurement", subtitle="Based on exact prior sample history")
        expected_layout.addWidget(strain_plot(compact=True))
        expected_layout.addWidget(label("Prior run: 11.2% strain span, transformation from 12-24 mA.", "hint"))
        preview.addWidget(expected, 1)
        body.addLayout(preview, 6)
        layout.addLayout(body, 1)
        return page

    def _run_plot_first(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        targets = self._target_list(compact=False)
        targets.setFixedWidth(180)
        layout.addWidget(targets)
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        status = QtWidgets.QHBoxLayout()
        status.addWidget(label("500 MPa", "page-title"))
        status.addWidget(label("Target center 500 | Processed 508 | Raw 515", "value"))
        status.addStretch(1)
        status.addWidget(label("18.6 mA   3.912 mm   37.8 C", "value"))
        center_layout.addLayout(status)
        center_layout.addWidget(stress_plot(), 4)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addWidget(current_plot(compact=True), 1)
        bottom.addWidget(strain_plot(compact=True), 1)
        bottom.addWidget(load_plot(compact=True), 1)
        center_layout.addLayout(bottom, 2)
        layout.addWidget(center, 1)
        tools = QtWidgets.QWidget()
        tools.setFixedWidth(245)
        tools_layout = QtWidgets.QVBoxLayout(tools)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(10)
        tools_layout.addWidget(self._run_status())
        tools_layout.addWidget(self._live_controls())
        tools_layout.addStretch(1)
        layout.addWidget(tools)
        return page

    def _review_plot_first(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        header = QtWidgets.QHBoxLayout()
        header.addWidget(label("Completed run | transformation overview", "page-title"), 1)
        for item in (("STRAIN", "11.8 %"), ("ERROR P95", "11.8 MPa"), ("TARGETS", "20 / 20")):
            header.addWidget(metric(*item))
        layout.addLayout(header)
        plots = QtWidgets.QHBoxLayout()
        plots.setSpacing(10)
        plots.addWidget(strain_plot(), 3)
        plots.addWidget(stress_plot(), 2)
        layout.addLayout(plots, 3)
        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(self._quality_section(), 1)
        footer.addWidget(self._files_section(), 1)
        footer.addWidget(current_plot(compact=True), 2)
        layout.addLayout(footer, 2)
        return page


def install_palette(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
    if font_path.exists():
        QtGui.QFontDatabase.addApplicationFont(str(font_path))
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(C["window"]))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(C["text"]))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(C["base"]))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(C["surface"]))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(C["text"]))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(C["surface2"]))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(C["text"]))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(C["blue"]))
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(C["window"]))
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, QtGui.QColor(C["faint"]))
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(C["faint"]))
    app.setPalette(palette)
    font = QtGui.QFont("Segoe UI", 9)
    app.setFont(font)


def render_all(app: QtWidgets.QApplication) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    captures: list[tuple[str, str, Path]] = []
    for direction in DIRECTIONS.values():
        window = DesignWindow(direction)
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        for stage in ("prepare", "run", "review"):
            window.set_stage(stage)
            app.processEvents()
            path = SCREENSHOTS / f"{direction.key}-{stage}.png"
            render_widget(window, path)
            captures.append((direction.name, stage.title(), path))
        if direction.key in {"adaptive-v3", "adaptive-v4"}:
            window.set_stage("run")
            dialog = window.open_remaining_sweeps_editor()
            app.processEvents()
            render_widget(dialog, SCREENSHOTS / f"{direction.key}-update-remaining.png")
            dialog.close()
            app.processEvents()
        if direction.key == "adaptive-v4":
            window.set_stage("run")
            assert window.target_tree is not None
            window.target_tree.itemClicked.emit(window.target_tree_items[None], 0)
            app.processEvents()
            render_widget(window, SCREENSHOTS / "adaptive-v4-all-targets.png")
            assert window.target_result_tabs is not None
            window.target_result_tabs.setCurrentIndex(1)
            app.processEvents()
            render_widget(window, SCREENSHOTS / "adaptive-v4-all-resistance.png")
            window.target_result_tabs.setCurrentIndex(0)
            window.target_tree.itemClicked.emit(window.target_tree_items[300], 0)
            app.processEvents()
            render_widget(window, SCREENSHOTS / "adaptive-v4-300-mpa.png")
            window.target_result_tabs.setCurrentIndex(1)
            app.processEvents()
            render_widget(window, SCREENSHOTS / "adaptive-v4-300-resistance.png")
            window.target_result_tabs.setCurrentIndex(0)
            assert window.target_return_button is not None
            window.target_return_button.click()
            app.processEvents()
        window.resize(1280, 768)
        window.set_stage("run")
        app.processEvents()
        render_widget(window, SCREENSHOTS / f"{direction.key}-run-1280x768.png")
        window.close()
        app.processEvents()
    create_contact_sheet(captures)
    create_reference_comparison()
    create_adaptive_comparison()


def render_widget(widget: QtWidgets.QWidget, path: Path) -> None:
    pixmap = QtGui.QPixmap(widget.size())
    pixmap.fill(QtGui.QColor(C["window"]))
    widget.render(pixmap)
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save {path}")


def create_contact_sheet(captures: list[tuple[str, str, Path]]) -> None:
    tile_w, tile_h = 460, 288
    label_h = 30
    gap = 12
    outer = 18
    columns = len(DIRECTIONS)
    width = outer * 2 + tile_w * columns + gap * (columns - 1)
    height = outer * 2 + (tile_h + label_h) * 3 + gap * 2
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor(C["window"]))
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    font = QtGui.QFont("Segoe UI", 10)
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(C["text"]))
    for index, (direction, stage, path) in enumerate(captures):
        col = index // 3
        row = index % 3
        x = outer + col * (tile_w + gap)
        y = outer + row * (tile_h + label_h + gap)
        source = QtGui.QImage(str(path))
        scaled = source.scaled(tile_w, tile_h, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
        painter.drawText(QtCore.QRectF(x, y, tile_w, label_h), QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter, f"{direction} - {stage}")
        painter.drawImage(QtCore.QPointF(x, y + label_h), scaled)
    painter.end()
    output = SCREENSHOTS / "all-directions-contact-sheet.png"
    if not image.save(str(output), "PNG"):
        raise RuntimeError(f"Could not save {output}")


def create_reference_comparison() -> None:
    if not SOURCE_REFERENCE.exists():
        return
    sources = [
        ("Current TMA", SOURCE_REFERENCE),
        ("Instrument Refined", SCREENSHOTS / "refined-run.png"),
        ("Adaptive Workspace", SCREENSHOTS / "adaptive-run.png"),
        ("Plot-First Control Room", SCREENSHOTS / "plot-first-run.png"),
    ]
    tile_w, tile_h = 680, 425
    label_h, gap, outer = 36, 16, 20
    width = outer * 2 + tile_w * 2 + gap
    height = outer * 2 + (tile_h + label_h) * 2 + gap
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor(C["window"]))
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    font = QtGui.QFont("Segoe UI", 11)
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(C["text"]))
    for index, (name, path) in enumerate(sources):
        row, col = divmod(index, 2)
        x = outer + col * (tile_w + gap)
        y = outer + row * (tile_h + label_h + gap)
        source = QtGui.QImage(str(path))
        scaled = source.scaled(
            tile_w,
            tile_h,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawText(
            QtCore.QRectF(x, y, tile_w, label_h),
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            name,
        )
        painter.drawImage(QtCore.QPointF(x, y + label_h), scaled)
    painter.end()
    output = SCREENSHOTS / "source-vs-directions.png"
    if not image.save(str(output), "PNG"):
        raise RuntimeError(f"Could not save {output}")


def create_adaptive_comparison() -> None:
    sources = [
        ("Adaptive Workspace v1", SCREENSHOTS / "adaptive-run.png"),
        ("Adaptive Workspace v2", SCREENSHOTS / "adaptive-v2-run.png"),
        ("Adaptive Workspace v3", SCREENSHOTS / "adaptive-v3-run.png"),
        ("Adaptive Workspace v4", SCREENSHOTS / "adaptive-v4-run.png"),
    ]
    tile_w, tile_h = 600, 375
    label_h, gap, outer = 36, 16, 20
    image = QtGui.QImage(
        outer * 2 + tile_w * len(sources) + gap * (len(sources) - 1),
        outer * 2 + tile_h + label_h,
        QtGui.QImage.Format.Format_ARGB32,
    )
    image.fill(QtGui.QColor(C["window"]))
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    font = QtGui.QFont("Segoe UI", 11)
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(C["text"]))
    for index, (name, path) in enumerate(sources):
        x = outer + index * (tile_w + gap)
        source = QtGui.QImage(str(path))
        scaled = source.scaled(
            tile_w,
            tile_h,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawText(
            QtCore.QRectF(x, outer, tile_w, label_h),
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            name,
        )
        painter.drawImage(QtCore.QPointF(x, outer + label_h), scaled)
    painter.end()
    output = SCREENSHOTS / "adaptive-v1-v2-v3-v4.png"
    if not image.save(str(output), "PNG"):
        raise RuntimeError(f"Could not save {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone TMA logger UI design prototypes")
    parser.add_argument("--direction", choices=tuple(DIRECTIONS), default="adaptive")
    parser.add_argument("--stage", choices=("prepare", "run", "review"), default="run")
    parser.add_argument("--render-all", action="store_true")
    return parser.parse_args()


def launch(
    *_args: object,
    direction: str = "adaptive",
    stage: str = "run",
    **_kwargs: object,
) -> DesignWindow:
    """Open a tracked design-lab window from the PyPlot launcher."""
    window = DesignWindow(DIRECTIONS[direction])
    window.set_stage(stage)
    window.show()
    return window


def launch_refined(*args: object, **kwargs: object) -> DesignWindow:
    return launch(*args, direction="refined", **kwargs)


def launch_adaptive(*args: object, **kwargs: object) -> DesignWindow:
    return launch(*args, direction="adaptive", **kwargs)


def launch_adaptive_v2(*args: object, **kwargs: object) -> DesignWindow:
    return launch(*args, direction="adaptive-v2", **kwargs)


def launch_adaptive_v3(*args: object, **kwargs: object) -> DesignWindow:
    return launch(*args, direction="adaptive-v3", **kwargs)


def launch_adaptive_v4(*args: object, **kwargs: object) -> DesignWindow:
    return launch(*args, direction="adaptive-v4", **kwargs)


def launch_plot_first(*args: object, **kwargs: object) -> DesignWindow:
    return launch(*args, direction="plot-first", **kwargs)


def main() -> int:
    args = parse_args()
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("TMA UI Design Lab")
    install_palette(app)
    if args.render_all:
        render_all(app)
        return 0
    window = DesignWindow(DIRECTIONS[args.direction])
    window.set_stage(args.stage)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
