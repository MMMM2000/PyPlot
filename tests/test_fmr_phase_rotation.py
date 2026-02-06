from __future__ import annotations

import numpy as np

from plotting.plugins.fmr.core import (
    estimate_phase_rotation_angle,
    rotate_lockin_phase,
)


def test_rotate_lockin_phase_90_degrees() -> None:
    x = np.array([1.0, 0.0])
    y = np.array([0.0, 1.0])
    x_rot, y_rot = rotate_lockin_phase(x, y, 90.0)
    assert np.allclose(x_rot, [0.0, 1.0], atol=1e-9)
    assert np.allclose(y_rot, [-1.0, 0.0], atol=1e-9)


def test_estimate_phase_rotation_angle_recovers_known_rotation() -> None:
    field = np.linspace(-1.0, 1.0, 1201)
    x_true = -(field / 0.06) * np.exp(-(field / 0.22) ** 2)
    y_true = 0.04 + 0.03 * field

    known_angle = 27.5
    theta = np.deg2rad(known_angle)
    measured_x = x_true * np.cos(theta) - y_true * np.sin(theta)
    measured_y = x_true * np.sin(theta) + y_true * np.cos(theta)

    estimated = estimate_phase_rotation_angle(field, measured_x, measured_y)
    assert abs(estimated - known_angle) < 0.4

    recovered_x, recovered_y = rotate_lockin_phase(measured_x, measured_y, estimated)
    assert np.allclose(recovered_x, x_true, atol=1e-2)
    assert np.allclose(recovered_y, y_true, atol=1e-2)
