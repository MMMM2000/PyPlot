from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from plotting.shared.transition_analysis import fit_tangent_transition
from plotting.plugins.vsm_temperature_scan.core import VSMTemperatureScanProcessor


def _piecewise_transition(x: np.ndarray, start: float, finish: float) -> np.ndarray:
    return np.piecewise(
        x,
        [x <= start, (x > start) & (x < finish), x >= finish],
        [
            lambda value: 0.01 * value,
            lambda value: 0.3 + 0.09 * (value - start),
            lambda value: 0.3 + 0.09 * (finish - start) + 0.012 * (value - finish),
        ],
    )


def test_tangent_transition_fit_recovers_piecewise_intersections() -> None:
    x = np.linspace(0.0, 100.0, 101)
    y = _piecewise_transition(x, 30.0, 70.0)

    result = fit_tangent_transition(x, y, min_segment_points=8)

    assert result is not None
    assert result.start_x == pytest.approx(30.0, abs=1.0)
    assert result.finish_x == pytest.approx(70.0, abs=1.0)
    assert result.transition_slope > result.before.slope
    assert result.transition_slope > result.after.slope


def test_tangent_transition_fit_rejects_noisy_linear_false_positive() -> None:
    x = np.linspace(0.0, 100.0, 101)
    rng = np.random.default_rng(1)
    y = 0.1 + (0.02 * x) + rng.normal(0.0, 0.02, len(x))

    result = fit_tangent_transition(x, y, min_segment_points=8)

    assert result is None


def test_tangent_transition_fit_rejects_unsupported_jump_extrapolation() -> None:
    x = np.array(
        [
            1.2,
            5.0,
            10.0,
            15.0,
            20.0,
            25.8,
            28.6,
            31.0,
            31.9,
            33.4,
            34.7,
            35.9,
            36.7,
            38.4,
            40.0,
            45.0,
            55.0,
            65.0,
            75.0,
            79.8,
        ]
    )
    y = np.array(
        [
            10.9,
            10.8,
            10.7,
            10.6,
            10.5,
            10.4,
            10.25,
            10.2,
            2.4,
            2.3,
            2.0,
            1.5,
            0.6,
            0.23,
            0.2,
            0.16,
            0.1,
            0.07,
            0.04,
            0.03,
        ]
    )

    result = fit_tangent_transition(x, y, min_segment_points=4)

    assert result is None


def test_vsm_temperature_processor_estimates_heating_and_cooling_points() -> None:
    processor = VSMTemperatureScanProcessor()
    processor.set_split_directions(True)
    heating_x = np.linspace(0.0, 100.0, 101)
    cooling_x = heating_x[::-1]
    frame = pd.DataFrame(
        {
            "temperature": np.concatenate([heating_x, cooling_x]),
            "field": [10000.0] * 202,
            "signal": np.concatenate(
                [
                    _piecewise_transition(heating_x, 30.0, 70.0),
                    _piecewise_transition(cooling_x, 25.0, 65.0),
                ]
            ),
            "section_index": [0] * 101 + [1] * 101,
        }
    )

    points = processor.estimate_transition_points(frame)

    assert points["As"] == pytest.approx(30.0, abs=1.0)
    assert points["Af"] == pytest.approx(70.0, abs=1.0)
    assert points["Ms"] == pytest.approx(65.0, abs=1.0)
    assert points["Mf"] == pytest.approx(25.0, abs=1.0)
