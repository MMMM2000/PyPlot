"""Prototype UI exploring an iOS/macOS "liquid glass" aesthetic."""

from __future__ import annotations

import sys
from typing import Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.utils import ensure_app_theme, install_standard_menu


class GlassBackground(QtWidgets.QWidget):
    """Widget that paints a soft multi-stop gradient with light blooms."""

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # pragma: no cover - painting
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        gradient = QtGui.QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QtGui.QColor(18, 42, 110))
        gradient.setColorAt(0.45, QtGui.QColor(20, 24, 67))
        gradient.setColorAt(1.0, QtGui.QColor(12, 14, 32))
        painter.fillRect(self.rect(), gradient)

        def _glow(center: QtCore.QPointF, radius: float, color: QtGui.QColor) -> None:
            bloom = QtGui.QRadialGradient(center, radius)
            bloom.setColorAt(0.0, color)
            bloom.setColorAt(1.0, QtGui.QColor(color.red(), color.green(), color.blue(), 0))
            painter.setBrush(bloom)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(center, radius, radius)

        width = self.width()
        height = self.height()
        _glow(QtCore.QPointF(width * 0.25, height * 0.18), width * 0.35, QtGui.QColor(255, 255, 255, 65))
        _glow(QtCore.QPointF(width * 0.75, height * 0.28), width * 0.40, QtGui.QColor(108, 164, 255, 80))
        _glow(QtCore.QPointF(width * 0.6, height * 0.8), width * 0.55, QtGui.QColor(255, 109, 120, 70))

        super().paintEvent(event)


class PillLabel(QtWidgets.QLabel):
    """Rounded status badge used on glass cards."""

    def __init__(self, text: str, color: QtGui.QColor, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMargin(4)
        self.setStyleSheet(
            "QLabel {"
            "  color: white;"
            "  padding: 2px 10px;"
            f"  background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 160);"
            "  border-radius: 12px;"
            "  font-size: 11pt;"
            "}"
        )


class GlassCard(QtWidgets.QFrame):
    """Semi-transparent content block with drop shadow and frosted border."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        description: str,
        *,
        accent: QtGui.QColor,
        buttons: Iterable[QtWidgets.QAbstractButton] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("glassCard")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setColor(QtGui.QColor(0, 0, 0, 120))
        shadow.setOffset(0, 20)
        self.setGraphicsEffect(shadow)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 24)

        title_row = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel(title)
        title_font = QtGui.QFont()
        title_font.setPointSize(18)
        title_font.setWeight(QtGui.QFont.Weight.Medium)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: white; letter-spacing: 0.5px;")
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        badge = PillLabel(subtitle, accent)
        title_row.addWidget(badge)
        layout.addLayout(title_row)

        body = QtWidgets.QLabel(description)
        body.setWordWrap(True)
        body.setStyleSheet("color: rgba(240, 245, 255, 210); font-size: 11.5pt;")
        layout.addWidget(body)

        if buttons:
            actions = QtWidgets.QHBoxLayout()
            actions.addStretch(1)
            for btn in buttons:
                btn.setMinimumWidth(120)
                btn.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
                actions.addWidget(btn)
            layout.addLayout(actions)

        self.setStyleSheet(
            "#glassCard {"
            "  background-color: rgba(255, 255, 255, 42);"
            "  border: 1px solid rgba(255, 255, 255, 95);"
            "  border-radius: 22px;"
            "}"
            "#glassCard QPushButton {"
            "  color: white;"
            "  font-size: 11pt;"
            "  padding: 6px 18px;"
            "  border-radius: 16px;"
            "  border: 1px solid rgba(255, 255, 255, 120);"
            "  background-color: rgba(30, 144, 255, 160);"
            "}"
            "#glassCard QPushButton:hover {"
            "  background-color: rgba(80, 184, 255, 200);"
            "}"
        )


class LiquidGlassWindow(QtWidgets.QMainWindow):
    """Sample workspace composed of layered glass cards."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Liquid Glass UI Demo")
        self.resize(1100, 720)

        background = GlassBackground(self)
        self.setCentralWidget(background)

        root_layout = QtWidgets.QVBoxLayout(background)
        root_layout.setContentsMargins(32, 42, 32, 32)
        root_layout.setSpacing(28)

        header = QtWidgets.QLabel("Microwire Studio")
        header_font = QtGui.QFont()
        header_font.setPointSize(32)
        header_font.setWeight(QtGui.QFont.Weight.DemiBold)
        header.setFont(header_font)
        header.setStyleSheet("color: white; letter-spacing: 1.2px;")
        subheader = QtWidgets.QLabel("Curate experiments, monitor annealing runs and explore prototypes from a single glass desk.")
        subheader.setStyleSheet("color: rgba(230, 235, 255, 210); font-size: 12pt;")
        subheader.setWordWrap(True)

        header_block = QtWidgets.QVBoxLayout()
        header_block.addWidget(header)
        header_block.addWidget(subheader)
        root_layout.addLayout(header_block)

        cards_row = QtWidgets.QHBoxLayout()
        cards_row.setSpacing(28)

        run_button = QtWidgets.QPushButton("Start Live Run")
        plan_button = QtWidgets.QPushButton("Plan Sequence")
        card1 = GlassCard(
            "Annealing Session",
            "Live",
            "Monitor the PyVISA logger in real time. The card highlights contact warnings, dwell segments, and voltage ceilings without hunting for them in the console.",
            accent=QtGui.QColor(0, 181, 173),
            buttons=[run_button, plan_button],
        )

        data_button = QtWidgets.QPushButton("Open Data Room")
        card2 = GlassCard(
            "Data Library",
            "Curated",
            "Every dataset pinned here keeps its provenance: acquisition settings, outlier notes, and Origin exports. Drag files from Finder/Explorer to add them instantly.",
            accent=QtGui.QColor(255, 143, 102),
            buttons=[data_button],
        )

        theme_button = QtWidgets.QPushButton("Preview Theme")
        card3 = GlassCard(
            "Design Lab",
            "Concept",
            "Experiment with liquid glass components—layered gradients, frosted panes, luminous pills—to prototype the next iteration of the control room UI.",
            accent=QtGui.QColor(120, 101, 255),
            buttons=[theme_button],
        )

        cards_row.addWidget(card1, 1)
        cards_row.addWidget(card2, 1)
        cards_row.addWidget(card3, 1)
        root_layout.addLayout(cards_row)

        timeline = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        timeline.setRange(0, 100)
        timeline.setValue(45)
        timeline.setStyleSheet(
            "QSlider::groove:horizontal {"
            "  height: 6px;"
            "  border-radius: 3px;"
            "  background: rgba(255, 255, 255, 110);"
            "}"
            "QSlider::handle:horizontal {"
            "  width: 22px;"
            "  margin: -10px 0;"
            "  border-radius: 11px;"
            "  background: rgba(255, 255, 255, 210);"
            "  border: 2px solid rgba(255, 255, 255, 150);"
            "}"
        )
        timeline_label = QtWidgets.QLabel("Phase progression — 45 % complete")
        timeline_label.setStyleSheet("color: rgba(225, 232, 255, 210); font-size: 11.5pt;")
        timeline_block = QtWidgets.QVBoxLayout()
        timeline_block.addWidget(timeline_label)
        timeline_block.addWidget(timeline)
        root_layout.addLayout(timeline_block)

        install_standard_menu(self, help_topic="experiment_liquid_glass")


def main() -> None | QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        ensure_app_theme(app)
        owns_app = True
    window = LiquidGlassWindow()
    window.show()
    if owns_app:
        app.exec()
    return window


if __name__ == "__main__":
    main()
