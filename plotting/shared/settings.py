"""Shared configuration helpers for PyPlot."""

from __future__ import annotations

from PyQt6 import QtCore


def get_settings() -> QtCore.QSettings:
    """Return the global QSettings instance used across plotting modules."""

    return QtCore.QSettings("microwire", "plotting")


__all__ = ["get_settings"]
