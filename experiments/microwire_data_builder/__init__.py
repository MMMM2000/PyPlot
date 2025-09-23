"""Microwire fabrication and annealing database builder."""

from __future__ import annotations

from .core import (
    ASSUMED_COLS,
    BuilderConfig,
    BuildResult,
    BuildStats,
    FabricationIndex,
    build_database,
    build_fabrication_index,
    LOGGER_NAME,
)
from .ui import BuilderWindow, main, run_app

# Backwards compatibility for older imports expecting a Tkinter-style name.
BuilderApp = BuilderWindow

__all__ = [
    "ASSUMED_COLS",
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
