"""Public API for the data_logger package.

This package contains the serial data logger GUI implementation in
``data_logger.py``. Expose the commonly used entry points at the package level
so callers can do ``from data_logging import data_logger`` and then call
``data_logger.main()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .data_logger import MainWindow as MainWindow
    from .data_logger import main as main


def __getattr__(name: str) -> Any:
    if name in {"main", "MainWindow"}:
        from .data_logger import MainWindow, main

        globals()["main"] = main
        globals()["MainWindow"] = MainWindow
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["main", "MainWindow"]
