from __future__ import annotations

from types import ModuleType

_WINDOW_MODULE: ModuleType | None = None


def window_api() -> ModuleType:
    """Return the lazily-imported `plotting.pyplot.window` module."""

    global _WINDOW_MODULE
    if _WINDOW_MODULE is None:
        from plotting.pyplot import window as pyplot_window

        _WINDOW_MODULE = pyplot_window
    return _WINDOW_MODULE
