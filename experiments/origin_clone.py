"""Expose the Origin clone prototype through the launcher."""

from __future__ import annotations

from PyQt6 import QtWidgets

from origin_clone import main as origin_main


def main() -> QtWidgets.QWidget:
    """Launch the prototype and return the created window."""

    return origin_main()


__all__ = ["main"]
