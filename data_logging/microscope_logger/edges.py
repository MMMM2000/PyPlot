"""Parallel calipers and reviewed dark-band edge refinement in native pixels.

These tools report apparent optical boundaries, not validated material interfaces.
The automatic tool needs user-drawn guides and rejects insufficient edge evidence.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy.ndimage import gaussian_filter1d, map_coordinates


def _point(value: Sequence[float]) -> np.ndarray:
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Each guide point must contain finite x and y coordinates") from exc
    if result.shape != (2,) or not np.isfinite(result).all():
        raise ValueError("Each guide point must contain finite x and y coordinates")
    return result


def _inside(point: np.ndarray, shape: tuple[int, int]) -> bool:
    return bool(0 <= point[0] <= shape[1] - 1 and 0 <= point[1] <= shape[0] - 1)


def parallel_width(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> dict:
    """Return separation of AB and the parallel line through C.

    The distance is perpendicular to AB, regardless of where C lies along it.
    Image bounds are checked by the caller (or by ``refine_parallel_edges``).
    """
    first, last, opposite = map(_point, (a, b, c))
    direction = last - first
    length = float(np.linalg.norm(direction))
    if length <= 1e-9:
        raise ValueError("Choose two different points along the first edge")
    tangent = direction / length
    normal = np.array([-tangent[1], tangent[0]])
    signed = float(np.dot(opposite - first, normal))
    if abs(signed) <= 1e-9:
        raise ValueError("The opposite edge must be separated from the first edge")
    if signed < 0:
        normal = -normal
    width = abs(signed)
    middle = (first + last) / 2
    return {
        "width_px": width,
        "normal": normal.tolist(),
        "tangent": tangent.tolist(),
        "span_px": length,
        "width_start": middle.tolist(),
        "width_end": (middle + width * normal).tolist(),
        "edge1": [first.tolist(), last.tolist()],
        "edge2": [(first + width * normal).tolist(), (last + width * normal).tolist()],
    }


def _crossings(offsets: np.ndarray, values: np.ndarray, threshold: float,
               centre_index: int) -> tuple[float, float]:
    if values[centre_index] >= threshold:
        raise ValueError("The guide centre does not lie in a dark band")
    falling = np.flatnonzero((values[:-1] >= threshold) & (values[1:] < threshold))
    rising = np.flatnonzero((values[:-1] < threshold) & (values[1:] >= threshold))
    falling = falling[falling < centre_index]
    rising = rising[rising >= centre_index]
    if not len(falling) or not len(rising):
        raise ValueError("Two supported edges were not found")

    def interpolate(index):
        delta = values[index + 1] - values[index]
        return float(offsets[index] + (threshold - values[index]) / delta
                     * (offsets[index + 1] - offsets[index]))

    return interpolate(falling[-1]), interpolate(rising[0])


def _scan(image: np.ndarray, geometry: dict, samples: int, dynamic_range: float) -> tuple[list, list]:
    first = np.asarray(geometry["edge1"][0])
    tangent = np.asarray(geometry["tangent"])
    normal = np.asarray(geometry["normal"])
    width, length = geometry["width_px"], geometry["span_px"]
    margin = max(6.0, min(80.0, width * 0.65))
    step = 0.25
    offsets = np.arange(-margin, width + margin + step / 2, step)
    outside = (offsets < -margin * 0.4) | (offsets > width + margin * 0.4)
    centre = (offsets >= width * 0.3) & (offsets <= width * 0.7)
    centre_index = int(np.argmin(abs(offsets - width / 2)))
    accepted, rejected = [], []
    for index, fraction in enumerate(np.linspace(0.1, 0.9, samples), 1):
        origin = first + tangent * (fraction * length)
        # Average neighbouring parallel profiles, without using image padding.
        points = (origin[None, None, :] + offsets[None, :, None] * normal
                  + np.linspace(-1.0, 1.0, 5)[:, None, None] * tangent)
        x, y = points[:, :, 0], points[:, :, 1]
        if (x.min() < 0 or y.min() < 0 or x.max() > image.shape[1] - 1
                or y.max() > image.shape[0] - 1):
            rejected.append({"position": index, "reason": "Profile background falls outside the image"})
            continue
        profile = map_coordinates(image, [y.ravel(), x.ravel()], order=1,
                                  mode="nearest").reshape(x.shape).mean(axis=0)
        profile = gaussian_filter1d(profile, 0.5 / step)
        dark = float(np.median(profile[centre]))
        background = np.polyval(np.polyfit(offsets[outside], profile[outside], 1), offsets)
        noise = float(np.median(abs(np.diff(profile[outside]))))
        contrast = float(np.median(background) - dark)
        minimum_contrast = max(dynamic_range * 0.05, noise * 5)
        left_background = float(np.median(profile[offsets < -margin * 0.4]))
        right_background = float(np.median(profile[offsets > width + margin * 0.4]))
        if min(left_background - dark, right_background - dark) < minimum_contrast:
            rejected.append({"position": index, "reason": "Dark band lacks two bright backgrounds or sufficient contrast"})
            continue
        denominators = background - dark
        if float(np.min(denominators)) <= minimum_contrast * 0.2:
            rejected.append({"position": index, "reason": "Background normalization is unstable"})
            continue
        normalized = (profile - dark) / denominators
        try:
            low, high = _crossings(offsets, normalized, 0.5, centre_index)
            low40, high40 = _crossings(offsets, normalized, 0.4, centre_index)
            low60, high60 = _crossings(offsets, normalized, 0.6, centre_index)
            tolerance = max(4.0, width * 0.4)
            if abs(low) > tolerance or abs(high - width) > tolerance:
                raise ValueError("Detected edges are too far from the drawn guides")
            measured = high - low
            if measured < 2 or measured < width * 0.4 or measured > width * 1.8:
                raise ValueError("Detected dark-band width does not match the guide region")
        except ValueError as exc:
            rejected.append({"position": index, "reason": str(exc)})
            continue
        accepted.append({
            "position": index, "fraction": float(fraction),
            "span_position_px": float(fraction * length),
            "start": (origin + low * normal).tolist(),
            "end": (origin + high * normal).tolist(),
            "width_px": float(measured),
            "edge1_offset_px": low, "edge2_offset_px": high,
            "width_40pct_px": float(high40 - low40),
            "width_60pct_px": float(high60 - low60),
            "threshold_sensitivity_px": float(abs((high60 - low60) - (high40 - low40))),
            "contrast": contrast,
        })
    return accepted, rejected


def _fit(geometry: dict, profiles: list) -> dict:
    positions = np.array([p["span_position_px"] for p in profiles])
    left = np.array([p["edge1_offset_px"] for p in profiles])
    right = np.array([p["edge2_offset_px"] for p in profiles])
    centred = positions - positions.mean()
    denominator = float(2 * np.dot(centred, centred))
    if denominator <= 0:
        raise ValueError("Detected edge points do not span a usable length")
    slope = float(np.dot(centred, left - left.mean() + right - right.mean()) / denominator)
    intercept1 = float(left.mean() - slope * positions.mean())
    intercept2 = float(right.mean() - slope * positions.mean())
    origin = np.asarray(geometry["edge1"][0])
    tangent, normal = np.asarray(geometry["tangent"]), np.asarray(geometry["normal"])

    def edge_point(position, intercept):
        return origin + position * tangent + (slope * position + intercept) * normal

    first, last = float(positions.min()), float(positions.max())
    a, b = edge_point(first, intercept1), edge_point(last, intercept1)
    c = edge_point((first + last) / 2, intercept2)
    fitted = parallel_width(a, b, c)
    fitted["fit_angle_correction_degrees"] = math.degrees(math.atan(slope))
    return fitted


def refine_parallel_edges(grayscale: np.ndarray, a: Sequence[float], b: Sequence[float],
                          c: Sequence[float], *, samples: int = 15) -> dict:
    """Refine dark-band boundaries near user guides and expose review diagnostics.

    A/B lie along the first edge; C lies on the opposite edge. Background must
    be visible on both sides. Results remain pixel measurements. Threshold
    sensitivity and spatial spread are diagnostics, not uncertainty bounds.
    """
    image = np.asarray(grayscale)
    if image.ndim != 2 or min(image.shape, default=0) < 8:
        raise ValueError("Automatic edges require a two-dimensional grayscale image")
    if (not np.issubdtype(image.dtype, np.number)
            or np.issubdtype(image.dtype, np.complexfloating)
            or not np.isfinite(image).all()):
        raise ValueError("Image intensities must be finite numeric values")
    image = image.astype(float, copy=False)
    if isinstance(samples, bool) or not isinstance(samples, (int, np.integer)) or not 3 <= samples <= 101:
        raise ValueError("samples must be an integer between 3 and 101")
    points = list(map(_point, (a, b, c)))
    if not all(_inside(point, image.shape) for point in points):
        raise ValueError("Guide points must lie inside the native image")
    geometry = parallel_width(*points)
    if geometry["width_px"] < 3 or geometry["span_px"] < 8:
        raise ValueError("Draw guides at least 8 pixels long around a band at least 3 pixels wide")
    dynamic_range = float(np.max(image) - np.min(image))
    if dynamic_range <= np.finfo(float).eps:
        raise ValueError("Image has no usable contrast")
    profiles, rejected = _scan(image, geometry, int(samples), dynamic_range)
    minimum = max(3, math.ceil(samples * 0.6))
    if len(profiles) < minimum:
        raise ValueError(f"Only {len(profiles)} of {samples} profiles found supported dark-band edges; adjust the guides or focus")
    fitted = _fit(geometry, profiles)
    # Rescan normal to the fitted edge direction, correcting a slightly skewed guide.
    refined, refined_rejected = _scan(image, fitted, int(samples), dynamic_range)
    if len(refined) >= minimum:
        geometry, profiles, rejected = fitted, refined, refined_rejected
        fitted = _fit(geometry, profiles)
    widths = np.array([profile["width_px"] for profile in profiles])
    sensitivity = np.array([profile["threshold_sensitivity_px"] for profile in profiles])
    flags = []
    if len(profiles) < samples:
        flags.append("Some profiles were rejected; review coverage")
    if float(sensitivity.max()) > max(1.5, float(np.median(widths)) * 0.05):
        flags.append("Blur or edge shape makes width sensitive to threshold")
    if float(widths.std(ddof=1)) > max(1.0, float(widths.mean()) * 0.02):
        flags.append("Widths vary across the region; inspect focus, shape and boundaries")
    return dict(fitted, method="guided dark-band half-contrast parallel fit",
                calibration_status="Uncalibrated", profiles=profiles,
                summary={"mean_px": float(widths.mean()), "median_px": float(np.median(widths)),
                         "std_px": float(widths.std(ddof=1)), "min_px": float(widths.min()),
                         "max_px": float(widths.max()), "accepted": len(profiles), "requested": int(samples)},
                threshold_sensitivity={"mean_px": float(sensitivity.mean()), "max_px": float(sensitivity.max()),
                                       "thresholds": [0.4, 0.6]},
                quality_flags=flags, rejected_profiles=rejected,
                boundary="apparent dark-band edges", requires_review=True)


__all__ = ["parallel_width", "refine_parallel_edges"]
