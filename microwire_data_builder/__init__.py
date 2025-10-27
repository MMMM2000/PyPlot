"""Microwire fabrication and annealing database builder."""

from __future__ import annotations

from .core import (
    BuilderConfig,
    BuildResult,
    BuildStats,
    FabricationIndex,
    build_database,
    build_fabrication_index,
    LOGGER_NAME,
)
try:
    from .ui import BuilderWindow, main, run_app
except Exception:  # pragma: no cover - optional UI dependencies
    BuilderWindow = None  # type: ignore[assignment]

    def main() -> None:
        raise ImportError(
            "Microwire builder UI dependencies are not installed. Install the extras "
            "from requirements.txt to launch the Qt application."
        )

    def run_app() -> None:
        main()

    BuilderApp = None  # type: ignore[assignment]
else:
    # Backwards compatibility for older imports expecting a Tkinter-style name.
    BuilderApp = BuilderWindow

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
