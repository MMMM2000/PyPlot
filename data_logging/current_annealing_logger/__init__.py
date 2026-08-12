"""Current annealing logger.

Keep package import headless.  The dedicated controller imports
``process_backend`` in a spawned Windows process and must not transitively load
Qt, matplotlib, or launcher-only UI helpers merely because Python initializes
this package first.
"""

from __future__ import annotations

from typing import Any

__all__ = ["main", "MainWindow"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .current_annealing_logger import MainWindow, main

        return {"main": main, "MainWindow": MainWindow}[name]
    raise AttributeError(name)
