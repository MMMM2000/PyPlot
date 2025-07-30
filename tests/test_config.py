from pyqt6_plotting import config


def test_load_default_config():
    cfg = config.load_config()
    assert 'stress_dependence' in cfg
    assert 'temperature_sensitivity' in cfg
