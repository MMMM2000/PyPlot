from __future__ import annotations

import ctypes
import sys
from functools import lru_cache

from PyQt6 import QtCore, QtGui


PYPLOT_LAUNCHER_APP_ID = "PyPlot.Launcher"
TMA_LOGGER_APP_ID = "PyPlot.TMA"
CURRENT_ANNEALING_LOGGER_APP_ID = "PyPlot.CurrentAnnealing"


def set_windows_app_user_model_id(app_id: str) -> bool:
    """Give a standalone Windows GUI process a stable taskbar identity."""

    if sys.platform != "win32":
        return False
    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            str(app_id)
        )
    except (AttributeError, OSError):
        return False
    return int(result) == 0


@lru_cache(maxsize=2)
def experiment_application_icon(kind: str) -> QtGui.QIcon:
    """Create a compact, dependency-free icon for an experiment logger."""

    normalized = str(kind).strip().casefold()
    if normalized not in {"tma", "current_annealing"}:
        raise ValueError(f"Unsupported experiment application icon: {kind!r}")

    size = 256
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

    background = QtGui.QColor("#155e75" if normalized == "tma" else "#9a3412")
    accent = QtGui.QColor("#fbbf24" if normalized == "tma" else "#fde047")
    foreground = QtGui.QColor("#f8fafc")
    rect = pixmap.rect().adjusted(12, 12, -12, -12)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(background)
    painter.drawRoundedRect(rect, size * 0.18, size * 0.18)

    font = painter.font()
    font.setBold(True)
    font.setPointSize(76 if normalized == "tma" else 68)
    painter.setFont(font)
    painter.setPen(QtGui.QPen(foreground))
    painter.drawText(
        rect.adjusted(0, -12, 0, 0),
        QtCore.Qt.AlignmentFlag.AlignCenter,
        "TMA" if normalized == "tma" else "CA",
    )

    painter.setPen(QtGui.QPen(accent, 15, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
    if normalized == "tma":
        painter.drawLine(64, 205, 192, 205)
        painter.drawLine(82, 185, 64, 205)
        painter.drawLine(82, 225, 64, 205)
        painter.drawLine(174, 185, 192, 205)
        painter.drawLine(174, 225, 192, 205)
    else:
        points = [
            QtCore.QPoint(58, 211),
            QtCore.QPoint(91, 176),
            QtCore.QPoint(119, 218),
            QtCore.QPoint(151, 174),
            QtCore.QPoint(198, 205),
        ]
        painter.drawPolyline(QtGui.QPolygon(points))

    painter.end()
    return QtGui.QIcon(pixmap)
