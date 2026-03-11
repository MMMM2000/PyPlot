from __future__ import annotations

import numpy as np

from plotting.plugins.vsm_hysteresis.vsm_hysteresis_loops import (
    _collect_crossings_x_at_y,
    _collect_crossings_y_at_x,
)


def test_crossings_x_at_y_skips_non_scalar_points() -> None:
    x_values = np.array([np.array([-2.0, -1.0]), -0.5, 0.5, 1.5], dtype=object)
    y_values = np.array([-1.0, -0.2, 0.2, 1.0], dtype=object)

    crossings = _collect_crossings_x_at_y(x_values, y_values)

    assert crossings
    assert all(np.isfinite(value) for value in crossings)
    assert any(abs(value) <= 1e-9 for value in crossings)


def test_crossings_y_at_x_handles_object_wrapped_scalars() -> None:
    x_values = np.array([-1.0, np.array([0.0]), 1.0], dtype=object)
    y_values = np.array([-2.0, 0.0, 2.0], dtype=object)

    crossings = _collect_crossings_y_at_x(x_values, y_values)

    assert crossings
    assert all(np.isfinite(value) for value in crossings)
    assert any(abs(value) <= 1e-9 for value in crossings)
