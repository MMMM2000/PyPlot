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
    assert importlib.import_module('data_logging.current_annealing_logger')
    assert importlib.import_module('data_logging.mini_dma_logger')
    assert importlib.import_module('data_logging.manual_stress_strain_logger')
    assert importlib.import_module('plotting.plugins.temperature_dependence')
    assert importlib.import_module('plotting.plugins.temperature_dependence.core')
    assert importlib.import_module('plotting.plugins.temperature_sensitivity.core')
    assert importlib.import_module('plotting.plugins.stress_dependence.core')
    assert importlib.import_module('plotting.plugins.stress_sensitivity.core')
    assert importlib.import_module('plotting.plugins.current_annealing.core')
    assert importlib.import_module('plotting.plugins.r_vs_t')
    assert importlib.import_module('plotting.plugins.r_vs_t.core')
    assert importlib.import_module('plotting.plugins.vsm_hysteresis.vsm_hysteresis_loops')
    assert importlib.import_module('plotting.plugins.vsm_isotherms')
    assert importlib.import_module('plotting.plugins.vsm_isotherms.core')
    assert importlib.import_module('plotting.plugins.dma_iso_stress')
    assert importlib.import_module('plotting.plugins.stress_sensitivity')
    assert importlib.import_module('emulators')
    assert importlib.import_module('emulators.virtual_serial_emulator_gui')
