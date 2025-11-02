from plotting.shared import config


def test_load_default_config():
    cfg = config.load_config()
    assert 'stress_dependence' in cfg
    assert 'temperature_sensitivity' in cfg
    assert 'stress_sensitivity' in cfg
