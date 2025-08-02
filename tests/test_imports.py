import importlib


def test_packages_importable():
    assert importlib.import_module('plotting')
    assert importlib.import_module('data_logger')
    assert importlib.import_module('plotting.temperature_dependence')
    assert importlib.import_module('plotting.stress_sensitivity')
