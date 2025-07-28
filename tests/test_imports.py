import importlib


def test_packages_importable():
    assert importlib.import_module('pyqt6_plotting')
    assert importlib.import_module('pyqt6_logger')
