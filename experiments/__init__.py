"""Experimental utilities and prototypes exposed through the launcher."""

from __future__ import annotations

from typing import Callable, Dict

from PyQt6 import QtWidgets

from . import pyvisa_current_annealing_logger
from . import liquid_glass_gui
from . import microwire_data_builder

EXPERIMENTS: Dict[str, Callable[[], QtWidgets.QWidget | None]] = {
    "PyVISA Current Annealing Logger": pyvisa_current_annealing_logger.main,
    "Liquid Glass UI Demo": liquid_glass_gui.main,
    "Microwire Data Builder": microwire_data_builder.main,
}

__all__ = ["EXPERIMENTS"]
