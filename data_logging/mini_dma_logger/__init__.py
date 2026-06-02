"""Mini DMA logger package."""

from __future__ import annotations

from typing import Any

__all__ = ["MainWindow", "main"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .mini_dma_logger import MainWindow, main

        return {"MainWindow": MainWindow, "main": main}[name]
    raise AttributeError(name)
