"""Experimental utilities and prototypes exposed through the launcher."""

from __future__ import annotations

from typing import Callable, Dict

from PyQt6 import QtWidgets

from . import data_plotter
from . import pyvisa_current_annealing_logger
from . import liquid_glass_gui

EXPERIMENTS: Dict[str, Callable[[], QtWidgets.QWidget | None]] = {
    "PyVISA Current Annealing Logger": pyvisa_current_annealing_logger.main,
    "Data Plotter Prototype": data_plotter.main,
    "Liquid Glass UI Demo": liquid_glass_gui.main,
}

__all__ = ["EXPERIMENTS"]
