import sys

from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.utils import ensure_app_theme, install_standard_menu
from . import pyvisa_current_annealing_logger as pyvisa_module
from .pyvisa_current_annealing_logger import PyVISAAnnealingLogger


class GlassBackground(QtWidgets.QWidget):
    """Gradient surface with soft blooms inspired by macOS 26."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event: QtGui.QPaintEvent | None = None) -> None:  # pragma: no cover - painting
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        gradient = QtGui.QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QtGui.QColor(58, 101, 202))
        gradient.setColorAt(0.45, QtGui.QColor(34, 54, 128))
        gradient.setColorAt(1.0, QtGui.QColor(14, 20, 56))
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
        _glow(QtCore.QPointF(width * 0.18, height * 0.18), width * 0.42, QtGui.QColor(255, 255, 255, 80))
        _glow(QtCore.QPointF(width * 0.78, height * 0.28), width * 0.5, QtGui.QColor(126, 178, 255, 90))
        _glow(QtCore.QPointF(width * 0.56, height * 0.82), width * 0.6, QtGui.QColor(255, 122, 150, 80))

        super().paintEvent(event)


class PillLabel(QtWidgets.QLabel):
    """Rounded badge used for status and accent tags."""

    def __init__(self, text: str, color: QtGui.QColor, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMargin(4)
        self.setStyleSheet(
            "QLabel {"
            "  color: white;"
            "  padding: 2px 10px;"
            f"  background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 170);"
            "  border-radius: 12px;"
            "  font-size: 11pt;"
            "}"
        )


class FrostedPanel(QtWidgets.QFrame):
    """Semi-transparent container that hosts the logger."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        description: str,
        *,
        accent: QtGui.QColor,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("frostedPanel")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setColor(QtGui.QColor(0, 0, 0, 140))
        shadow.setOffset(0, 26)
        self.setGraphicsEffect(shadow)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(28, 26, 28, 30)

        header = QtWidgets.QHBoxLayout()
        title_label = QtWidgets.QLabel(title)
        title_font = QtGui.QFont()
        title_font.setPointSize(22)
        title_font.setWeight(QtGui.QFont.Weight.Medium)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: rgba(245, 248, 255, 230); letter-spacing: 0.6px;")
        header.addWidget(title_label)
        header.addStretch(1)
        header.addWidget(PillLabel(subtitle, accent))
        layout.addLayout(header)

        body = QtWidgets.QLabel(description)
        body.setWordWrap(True)
        body.setStyleSheet("color: rgba(236, 240, 255, 210); font-size: 12pt;")
        layout.addWidget(body)

        self._content = QtWidgets.QWidget(self)
        self._content.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._content_layout = QtWidgets.QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(18)
        layout.addWidget(self._content, stretch=1)

        self.setStyleSheet(
            "#frostedPanel {"
            "  background-color: rgba(255, 255, 255, 46);"
            "  border: 1px solid rgba(255, 255, 255, 110);"
            "  border-radius: 28px;"
            "}"
        )

    def add_widget(self, widget: QtWidgets.QWidget) -> None:
        self._content_layout.addWidget(widget)


class InfoTile(QtWidgets.QFrame):
    """Compact glass tile describing comparison tips."""

    def __init__(self, title: str, body: str, *, accent: QtGui.QColor, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("glassHint")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)

        label = QtWidgets.QLabel(title)
        font = QtGui.QFont()
        font.setPointSize(14)
        font.setWeight(QtGui.QFont.Weight.DemiBold)
        label.setFont(font)
        label.setStyleSheet("color: rgba(245, 248, 255, 230); letter-spacing: 0.4px;")
        layout.addWidget(label)

        pill = PillLabel("Guidance", accent)
        layout.addWidget(pill, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)

        body_label = QtWidgets.QLabel(body)
        body_label.setWordWrap(True)
        body_label.setStyleSheet("color: rgba(235, 240, 255, 210); font-size: 10.5pt;")
        layout.addWidget(body_label)

        self.setStyleSheet(
            "#glassHint {"
            "  background-color: rgba(255, 255, 255, 32);"
            "  border: 1px solid rgba(255, 255, 255, 90);"
            "  border-radius: 20px;"
            "}"
        )


def _apply_glass_skin(logger: PyVISAAnnealingLogger) -> None:
    """Restyle the PyVISA logger so it feels at home on a glass surface."""

    logger.setObjectName("glassLogger")
    logger.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = logger.layout()
    if isinstance(layout, QtWidgets.QBoxLayout):
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(20)

    logger.setStyleSheet(
        "#glassLogger {"
        "  background-color: rgba(16, 24, 54, 150);"
        "  border: 1px solid rgba(255, 255, 255, 85);"
        "  border-radius: 24px;"
        "  color: rgba(238, 243, 255, 230);"
        "}"
        "#glassLogger QLabel {"
        "  color: rgba(235, 240, 255, 225);"
        "}"
        "#glassLogger QPlainTextEdit, #glassLogger QTextEdit {"
        "  background-color: rgba(10, 16, 40, 150);"
        "  border: 1px solid rgba(255, 255, 255, 70);"
        "  border-radius: 18px;"
        "  color: rgba(220, 228, 255, 220);"
        "}"
        "#glassLogger QLineEdit,"
        "#glassLogger QComboBox,"
        "#glassLogger QSpinBox,"
        "#glassLogger QDoubleSpinBox {"
        "  background-color: rgba(255, 255, 255, 40);"
        "  border: 1px solid rgba(255, 255, 255, 110);"
        "  border-radius: 16px;"
        "  padding: 6px 10px;"
        "  color: rgba(244, 248, 255, 230);"
        "}"
        "#glassLogger QCheckBox {"
        "  color: rgba(235, 240, 255, 220);"
        "}"
        "#glassLogger QPushButton {"
        "  border-radius: 18px;"
        "  padding: 8px 20px;"
        "  border: 1px solid rgba(255, 255, 255, 120);"
        "  background-color: rgba(120, 186, 255, 190);"
        "  color: white;"
        "  font-weight: 500;"
        "}"
        "#glassLogger QPushButton:hover {"
        "  background-color: rgba(150, 210, 255, 210);"
        "}"
        "#glassLogger QPushButton:disabled {"
        "  background-color: rgba(255, 255, 255, 45);"
        "  border: 1px solid rgba(255, 255, 255, 70);"
        "  color: rgba(240, 240, 240, 140);"
        "}"
        "#glassLogger QMenuBar {"
        "  background-color: transparent;"
        "  color: rgba(235, 240, 255, 220);"
        "}"
        "#glassLogger QMenu {"
        "  background-color: rgba(16, 24, 54, 235);"
        "  color: rgba(238, 243, 255, 230);"
        "  border: 1px solid rgba(255, 255, 255, 80);"
        "}"
        "#glassLogger QMenu::item:selected {"
        "  background-color: rgba(120, 186, 255, 210);"
        "}"
    )


class LiquidGlassWindow(QtWidgets.QMainWindow):
    """Conceptual workspace embedding the PyVISA logger in liquid glass."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Liquid Glass UI Demo — PyVISA Logger")
        self.resize(1240, 760)

        self._open_windows: list[QtWidgets.QWidget] = []

        background = GlassBackground(self)
        self.setCentralWidget(background)

        root_layout = QtWidgets.QVBoxLayout(background)
        root_layout.setContentsMargins(38, 48, 38, 38)
        root_layout.setSpacing(30)

        header = QtWidgets.QLabel("Microwire Studio — Liquid Glass")
        header_font = QtGui.QFont()
        header_font.setPointSize(34)
        header_font.setWeight(QtGui.QFont.Weight.DemiBold)
        header.setFont(header_font)
        header.setStyleSheet("color: rgba(245, 248, 255, 238); letter-spacing: 1.1px;")

        subheader = QtWidgets.QLabel(
            "Compare the production PyVISA current annealing logger with a macOS 26-inspired"
            " glass treatment. The controls, shortcuts, and safety prompts are unchanged; only"
            " the presentation shifts to layered translucency."
        )
        subheader.setWordWrap(True)
        subheader.setStyleSheet("color: rgba(236, 240, 255, 210); font-size: 12.5pt;")

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(16)
        button_row.addStretch(1)
        classic_button = QtWidgets.QPushButton("Launch Classic PyVISA Logger")
        classic_button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        classic_button.clicked.connect(self._launch_classic_logger)
        concept_button = QtWidgets.QPushButton("Open Serial Logger for Context")
        concept_button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        concept_button.clicked.connect(self._launch_serial_logger)
        for btn in (classic_button, concept_button):
            btn.setStyleSheet(
                "QPushButton {"
                "  color: rgba(18, 24, 42, 240);"
                "  font-size: 11.5pt;"
                "  padding: 8px 20px;"
                "  border-radius: 18px;"
                "  background-color: rgba(255, 255, 255, 220);"
                "}"
                "QPushButton:hover {"
                "  background-color: rgba(255, 255, 255, 240);"
                "}"
            )
            button_row.addWidget(btn)
        root_layout.addWidget(header)
        root_layout.addWidget(subheader)
        root_layout.addLayout(button_row)

        panel = FrostedPanel(
            "PyVISA Current Annealing",
            "Feature parity",
            "Every control from the standard logger is preserved—resource discovery, dwell and loop"
            " planning, contact monitoring, and export helpers—now wrapped in a translucent card",
            accent=QtGui.QColor(90, 180, 255),
        )
        self.logger = PyVISAAnnealingLogger()
        _apply_glass_skin(self.logger)
        panel.add_widget(self.logger)
        root_layout.addWidget(panel, stretch=1)

        tips_row = QtWidgets.QHBoxLayout()
        tips_row.setSpacing(22)
        tips_row.addWidget(
            InfoTile(
                "How to compare",
                "Open the classic logger from the button above and place it beside this window."
                " Run a dry session (no hardware required) to see how console text, plots, and"
                " dialogs inherit the liquid-glass styling while behaviour stays identical.",
                accent=QtGui.QColor(120, 186, 255),
            ),
            stretch=1,
        )
        tips_row.addWidget(
            InfoTile(
                "Design notes",
                "Buttons adopt soft capsules, text boxes glow slightly when focused, and the"
                " console floats above the background. Use this space to gather feedback before"
                " introducing a theme toggle to the production tools.",
                accent=QtGui.QColor(255, 143, 160),
            ),
            stretch=1,
        )
        root_layout.addLayout(tips_row)

        install_standard_menu(self, help_topic="experiment_liquid_glass")

    def _launch_classic_logger(self) -> None:
        window = pyvisa_module.main()
        self._track_window(window)

    def _launch_serial_logger(self) -> None:
        from data_logging.current_annealing_logger import current_annealing_logger

        window = current_annealing_logger.main()
        self._track_window(window)

    def _track_window(self, window: QtWidgets.QWidget | None) -> None:
        if not isinstance(window, QtWidgets.QWidget):
            return
        self._open_windows.append(window)

        def _cleanup(_: object = None, w: QtWidgets.QWidget = window) -> None:
            try:
                self._open_windows.remove(w)
            except ValueError:
                pass

        window.destroyed.connect(_cleanup)


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
