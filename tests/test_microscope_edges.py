"""Invented images only: no captured microscope images are test fixtures."""
import json
import math

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from data_logging.microscope_logger.edges import parallel_width, refine_parallel_edges


def band(angle=0, width=40, blur=0, noise=0):
    y, x = np.indices((280, 360), dtype=float)
    theta = math.radians(angle)
    tangent = np.array([math.cos(theta), math.sin(theta)])
    normal = np.array([-math.sin(theta), math.cos(theta)])
    centre = np.array([180, 140])
    distance = (x - centre[0]) * normal[0] + (y - centre[1]) * normal[1]
    # Unit-pixel antialiasing places the half-contrast boundaries at +/-width/2.
    image = 220.0 - 200.0 * np.clip(width / 2 + 0.5 - abs(distance), 0, 1)
    if blur:
        image = gaussian_filter(image, blur)
    if noise:
        image += np.random.default_rng(912).normal(0, noise, image.shape)
    a = centre - 95 * tangent - width / 2 * normal
    b = centre + 95 * tangent - width / 2 * normal
    c = centre + width / 2 * normal
    return image, a, b, c


def test_parallel_caliper_is_perpendicular_not_point_to_point():
    result = parallel_width((10, 20), (110, 20), (90, 50))
    assert result["width_px"] == 30
    assert result["width_start"] == [60, 20]
    assert result["width_end"] == [60, 50]
    assert result["edge2"] == [[10, 50], [110, 50]]


@pytest.mark.parametrize("angle", [0, 25, 75, -32, 180])
def test_parallel_caliper_rotation(angle):
    _, a, b, c = band(angle)
    result = parallel_width(a, b, c)
    assert result["width_px"] == pytest.approx(40)
    assert np.dot(np.array(result["width_end"]) - result["width_start"], b - a) == pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize("a,b,c", [((1, 1), (1, 1), (2, 2)),
                                    ((1, 1), (4, 4), (2, 2)),
                                    ((math.nan, 1), (4, 4), (2, 3)),
                                    ((1, 1, 1), (4, 4), (2, 3))])
def test_invalid_parallel_guides_rejected(a, b, c):
    with pytest.raises(ValueError):
        parallel_width(a, b, c)


@pytest.mark.parametrize("angle", [0, 24, 65, -28])
def test_automatic_edges_on_rotated_band(angle):
    image, a, b, c = band(angle, blur=1, noise=2)
    result = refine_parallel_edges(image, a, b, c)
    assert result["width_px"] == pytest.approx(40, abs=1)
    assert len(result["profiles"]) == 15
    assert result["summary"]["median_px"] == pytest.approx(40, abs=1)
    assert result["requires_review"] is True
    assert result["calibration_status"] == "Uncalibrated"
    for profile in result["profiles"]:
        assert np.linalg.norm(np.array(profile["end"]) - profile["start"]) == pytest.approx(profile["width_px"])
    json.dumps(result, allow_nan=False)


def test_reversed_edge_side_and_point_order_are_supported():
    image, a, b, c = band(12, blur=1)
    result = refine_parallel_edges(image, b, a, c)
    assert result["width_px"] == pytest.approx(40, abs=1)
    upper = (a + b) / 2
    offset = c - upper
    result = refine_parallel_edges(image, a + offset, b + offset, upper)
    assert result["width_px"] == pytest.approx(40, abs=1)


def test_skewed_manual_guide_is_refined():
    image, a, b, c = band(0, blur=1)
    a[1] -= 5
    b[1] += 5
    result = refine_parallel_edges(image, a, b, c)
    assert result["width_px"] == pytest.approx(40, abs=0.7)


def test_blur_is_reported_as_threshold_sensitivity_not_accuracy():
    image, a, b, c = band(17, blur=5)
    result = refine_parallel_edges(image, a, b, c)
    assert result["width_px"] == pytest.approx(40, abs=1)
    assert result["threshold_sensitivity"]["max_px"] > 4
    assert any("sensitive" in flag for flag in result["quality_flags"])


@pytest.mark.parametrize("image", [np.full((280, 360), 200),
                                    np.tile(np.arange(360), (280, 1)),
                                    np.random.default_rng(14).normal(200, 1, (280, 360)),
                                    np.ones((280, 360)) * math.nan,
                                    np.ones((280, 360), dtype=complex),
                                    np.zeros((280, 360, 3))])
def test_no_supported_band_is_rejected(image):
    with pytest.raises(ValueError):
        refine_parallel_edges(image, (80, 120), (280, 120), (180, 160))


@pytest.mark.parametrize("samples", [0, 2, 102, 10.5, True])
def test_invalid_profile_count(samples):
    image, a, b, c = band()
    with pytest.raises(ValueError):
        refine_parallel_edges(image, a, b, c, samples=samples)


def test_outside_or_too_small_guide_is_rejected():
    image, a, b, c = band()
    for points in [((-1, 120), b, c), (a, a + (3, 0), c), (a, b, a + (0, 1))]:
        with pytest.raises(ValueError):
            refine_parallel_edges(image, *points)


def test_profiles_needing_background_outside_image_are_rejected():
    image = np.full((100, 220), 220.0)
    image[:40] = 20
    with pytest.raises(ValueError, match="profiles"):
        refine_parallel_edges(image, (30, 1), (190, 1), (100, 40))
