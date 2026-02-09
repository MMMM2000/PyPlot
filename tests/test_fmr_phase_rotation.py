from __future__ import annotations

import numpy as np
import pytest

from plotting.plugins.fmr.core import (
    align_bidirectional_field_sweeps,
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


def test_align_bidirectional_field_sweeps_centers_forward_backward_resonance() -> None:
    forward_field = np.linspace(-800.0, 9800.0, 700)
    backward_field = np.linspace(9800.0, -800.0, 700)[1:]
    field = np.concatenate([forward_field, backward_field])

    left_center = 5750.0
    right_center = 5850.0
    forward_signal = -np.exp(-((forward_field - left_center) / 340.0) ** 2)
    backward_signal = -np.exp(-((backward_field - right_center) / 340.0) ** 2)
    signal = np.concatenate([forward_signal, backward_signal])

    adjusted, delta, applied = align_bidirectional_field_sweeps(field, signal)
    assert applied
    assert delta == pytest.approx(100.0, rel=0.15)

    turn = int(np.argmax(field))
    first = adjusted[: turn + 1]
    second = adjusted[turn + 1 :]
    first_sig = signal[: turn + 1]
    second_sig = signal[turn + 1 :]
    first_res = float(first[int(np.argmin(first_sig))])
    second_res = float(second[int(np.argmin(second_sig))])
    assert abs(second_res - first_res) < 5.0


def test_align_bidirectional_field_sweeps_skips_monotonic_data() -> None:
    field = np.linspace(-1000.0, 9000.0, 1200)
    signal = -np.exp(-((field - 5000.0) / 300.0) ** 2)
    adjusted, delta, applied = align_bidirectional_field_sweeps(field, signal)
    assert not applied
    assert delta == pytest.approx(0.0)
    assert np.allclose(adjusted, field)
