"""Microwire EDA analysis and UI entry points."""

from __future__ import annotations

from typing import Any

from .core import (
    MicrowireEdaAnalysis,
    MicrowireEdaConfig,
    MicrowireEdaResult,
    detect_input_kind,
    generate_report,
    load_analysis_frame,
    load_input_frame,
    run_analysis,
    write_analysis_artifacts,
)

_UI_IMPORT_ERROR: Exception | None = None
_UI_IMPORT_MESSAGE: str | None = None
_WindowClass: Any | None = None
_main_impl: Any | None = None
_launch_impl: Any | None = None


def _ensure_ui() -> tuple[Any, Any, Any]:
    global _UI_IMPORT_ERROR, _UI_IMPORT_MESSAGE, _WindowClass, _main_impl, _launch_impl
    if _WindowClass is not None and _main_impl is not None and _launch_impl is not None:
        return _WindowClass, _main_impl, _launch_impl
    if _UI_IMPORT_ERROR is not None:
        assert _UI_IMPORT_MESSAGE is not None
        raise ImportError(_UI_IMPORT_MESSAGE) from _UI_IMPORT_ERROR
    try:
        from . import ui as _ui
    except Exception as exc:  # pragma: no cover - optional UI dependency path
        _UI_IMPORT_ERROR = exc
        _UI_IMPORT_MESSAGE = (
            "Microwire EDA UI dependencies are unavailable in this environment.\n\n"
            f"Original error: {exc}"
        )
        raise ImportError(_UI_IMPORT_MESSAGE) from exc
    _WindowClass = _ui.MicrowireEdaWindow
    _main_impl = _ui.main
    _launch_impl = _ui.launch_eda_window
    return _WindowClass, _main_impl, _launch_impl


def main() -> Any:
    _, impl, _ = _ensure_ui()
    return impl()


def launch_eda_window(*args: Any, **kwargs: Any) -> Any:
    _, _, impl = _ensure_ui()
    return impl(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name == "MicrowireEdaWindow":
        window_cls, _, _ = _ensure_ui()
        globals()["MicrowireEdaWindow"] = window_cls
        return window_cls
    raise AttributeError(name)


__all__ = [
    "MicrowireEdaAnalysis",
    "MicrowireEdaConfig",
    "MicrowireEdaResult",
    "detect_input_kind",
    "generate_report",
    "load_analysis_frame",
    "load_input_frame",
    "run_analysis",
    "write_analysis_artifacts",
    "launch_eda_window",
    "main",
]
