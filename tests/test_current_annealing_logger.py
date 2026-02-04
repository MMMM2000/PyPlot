from __future__ import annotations

import importlib

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="Qt widgets backend is unavailable", exc_type=ImportError)


logger_mod = importlib.import_module("data_logging.current_annealing_logger.current_annealing_logger")


def test_percent_from_hold_handles_zero() -> None:
    assert logger_mod.MainWindow._percent_from_hold(10.0, 0.0) is None


def test_percent_from_hold_nominal() -> None:
    assert logger_mod.MainWindow._percent_from_hold(200.0, 100.0) == pytest.approx(200.0)


def test_percent_from_hold_handles_nan() -> None:
    assert logger_mod.MainWindow._percent_from_hold(float("nan"), 100.0) is None
