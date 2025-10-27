"""Experimental utilities and prototypes exposed through the launcher."""

from __future__ import annotations

from typing import Callable, Dict

from PyQt6 import QtWidgets

from . import strain_worksheet_updater
from . import origin_clone
from . import current_annealing_converter
from . import ocr_debug
from . import paddleocr_vl_pdf

EXPERIMENTS: Dict[str, Callable[[], QtWidgets.QWidget | None]] = {
    "Strain Worksheet Updater": strain_worksheet_updater.main,
    "Origin Clone (Prototype)": origin_clone.main,
    "Current Annealing Unit Converter": current_annealing_converter.main,
    "Microscope OCR Debug": ocr_debug.main,
    "PaddleOCR-VL PDF Converter": paddleocr_vl_pdf.main,
}

__all__ = ["EXPERIMENTS"]
