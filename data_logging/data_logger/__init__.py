"""Public API for the data_logger package.

This package contains the serial data logger GUI implementation in
``data_logger.py``. Expose the commonly used entry points at the package level
so callers can do ``from data_logging import data_logger`` and then call
``data_logger.main()``.
"""

from .data_logger import main, MainWindow  # re-export for convenient access

__all__ = ["main", "MainWindow"]
