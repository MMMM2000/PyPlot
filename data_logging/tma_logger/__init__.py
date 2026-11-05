"""Canonical TMA logger package.

The historical :mod:`data_logging.mini_dma_logger` namespace remains available
for saved projects and external scripts. New launch and controller code should
import from this package.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MainWindow", "main"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .tma_logger import MainWindow, main

        return {"MainWindow": MainWindow, "main": main}[name]
    raise AttributeError(name)
