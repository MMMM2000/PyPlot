"""Shared cursor-readout helpers for the PyPlot status bar."""

from __future__ import annotations

import math

from PyQt6 import QtCore, QtGui

_CURSOR_PLACEHOLDER = "x: --   y: --"


def _format_pair(x_value: float, y_value: float, *, compact: bool) -> str:
    if compact:
        return f"{x_value:.4g}, {y_value:.4g}"
    return f"x: {x_value:.4g}   y: {y_value:.4g}"


def cursor_readout_text(
    x_value: float | None,
    y_value: float | None,
    *,
    available_px: int = 0,
    metrics: QtGui.QFontMetrics | None = None,
) -> str:
    """Return a clipped/compact cursor readout for status-bar display."""

    if x_value is None or y_value is None:
        return _CURSOR_PLACEHOLDER
    if not (math.isfinite(x_value) and math.isfinite(y_value)):
        return _CURSOR_PLACEHOLDER

    expanded = _format_pair(float(x_value), float(y_value), compact=False)
    if metrics is None or available_px <= 0:
        return expanded
    if metrics.horizontalAdvance(expanded) <= available_px:
        return expanded

    compact = _format_pair(float(x_value), float(y_value), compact=True)
    if metrics.horizontalAdvance(compact) <= available_px:
        return compact
    return metrics.elidedText(
        compact,
        QtCore.Qt.TextElideMode.ElideRight,
        max(8, int(available_px)),
    )
