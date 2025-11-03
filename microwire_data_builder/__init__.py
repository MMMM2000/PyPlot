"""Microwire fabrication and annealing database builder."""

from __future__ import annotations

import logging
from typing import Any, Callable, Tuple

from .core import (
    BuilderConfig,
    BuildResult,
    BuildStats,
    FabricationIndex,
    build_database,
    build_fabrication_index,
    LOGGER_NAME,
)

_UI_IMPORT_ERROR: Exception | None = None
_UI_IMPORT_MESSAGE: str | None = None
_BuilderWindow: Any | None = None
_main_impl: Callable[[], Any] | None = None
_run_app_impl: Callable[[], Any] | None = None


def _ensure_ui() -> Tuple[Any, Callable[[], Any], Callable[[], Any]]:
    """Load the Qt UI lazily so core imports can run without the heavy dependencies."""

    global _UI_IMPORT_ERROR, _UI_IMPORT_MESSAGE, _BuilderWindow, _main_impl, _run_app_impl
    if _BuilderWindow is not None and _main_impl is not None and _run_app_impl is not None:
        return _BuilderWindow, _main_impl, _run_app_impl
    if _UI_IMPORT_ERROR is not None:
        assert _UI_IMPORT_MESSAGE is not None
        raise ImportError(_UI_IMPORT_MESSAGE) from _UI_IMPORT_ERROR
    try:
        from . import ui as _ui
    except Exception as exc:  # pragma: no cover - optional UI dependencies
        _UI_IMPORT_ERROR = exc
        _UI_IMPORT_MESSAGE = (
            "Microwire builder UI dependencies are not installed. Install the extras "
            "from requirements.txt to launch the Qt application.\n\n"
            f"Original error: {exc}"
        )
        logging.getLogger(LOGGER_NAME).exception(
            "Microwire Data Builder UI import failed; the optional dependencies may be missing.",
            exc_info=exc,
        )
        raise ImportError(_UI_IMPORT_MESSAGE) from exc
    _BuilderWindow = _ui.BuilderWindow
    _main_impl = _ui.main
    _run_app_impl = _ui.run_app
    return _BuilderWindow, _main_impl, _run_app_impl


def main() -> Any:
    """Launch the Qt UI, importing it on demand."""

    _, impl, _ = _ensure_ui()
    return impl()


def run_app() -> Any:
    """Entry point compatible with legacy scripts."""

    _, _, impl = _ensure_ui()
    return impl()


def __getattr__(name: str) -> Any:
    if name in {"BuilderWindow", "BuilderApp"}:
        window_cls, _, _ = _ensure_ui()
        globals()["BuilderWindow"] = window_cls
        globals()["BuilderApp"] = window_cls
        return window_cls
    raise AttributeError(name)


__all__ = [
    "BuilderApp",
    "BuilderWindow",
    "BuilderConfig",
    "BuildResult",
    "BuildStats",
    "FabricationIndex",
    "LOGGER_NAME",
    "build_database",
    "build_fabrication_index",
    "main",
    "run_app",
]
