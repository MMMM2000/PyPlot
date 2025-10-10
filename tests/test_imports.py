import importlib

import pytest

pytest.importorskip(
    "PyQt6.QtWidgets",
    reason="GUI dependencies unavailable in headless CI",
    exc_type=ImportError,
)


def test_packages_importable():
    assert importlib.import_module('plotting')
    assert importlib.import_module('data_logging')
    assert importlib.import_module('data_logging.data_logger')
    assert importlib.import_module('plotting.temperature_dependence')
    assert importlib.import_module('plotting.stress_sensitivity')
    assert importlib.import_module('emulators')
    assert importlib.import_module('emulators.virtual_serial_emulator_gui')
