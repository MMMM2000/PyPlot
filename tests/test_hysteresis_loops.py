import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

import matplotlib
matplotlib.use("Agg")

from plotting.plugins.hysteresis_loops import core


def test_load_and_plot_hysteresis_loop():
    path = 'sample_data/hysteresis_loops/FeSiBP 159_9 s1 200C.dat'
    x, y = core.load_loop(path)
    assert len(x) == len(y) and len(x) > 0
    fig = core.plot_loops([path], mode='Combined', show=False)
    assert hasattr(fig, 'axes') and len(fig.axes) == 1
