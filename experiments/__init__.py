"""Experimental utilities and prototypes exposed through the launcher."""

from __future__ import annotations

from typing import Callable, Dict

from PyQt6 import QtWidgets

from . import strain_worksheet_updater
from . import vsm_origin_workbench
from . import origin_clone

EXPERIMENTS: Dict[str, Callable[[], QtWidgets.QWidget | None]] = {
    "Strain Worksheet Updater": strain_worksheet_updater.main,
    "VSM Origin Workbench": vsm_origin_workbench.main,
    "Origin Clone (Prototype)": origin_clone.main,
}

__all__ = ["EXPERIMENTS"]
