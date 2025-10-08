"""Experimental utilities and prototypes exposed through the launcher."""

from __future__ import annotations

from typing import Callable, Dict

from PyQt6 import QtWidgets

from . import pyvisa_current_annealing_logger
from . import microwire_data_builder
from . import strain_worksheet_updater
from . import strain_3d_plotter
from . import vsm_plotter

EXPERIMENTS: Dict[str, Callable[[], QtWidgets.QWidget | None]] = {
    "PyVISA Current Annealing Logger": pyvisa_current_annealing_logger.main,
    "Microwire Data Builder": microwire_data_builder.main,
    "Strain Worksheet Updater": strain_worksheet_updater.main,
    "Strain 3D Plot Explorer": strain_3d_plotter.main,
    "VSM Plot Explorer": vsm_plotter.main,
}

__all__ = ["EXPERIMENTS"]
