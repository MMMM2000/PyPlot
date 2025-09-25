"""Helpers for trimming burn-through artefacts from annealing curves."""

from __future__ import annotations

from typing import Tuple

import numpy as np


def trim_burnthrough_glitch(
    currents: np.ndarray, resistances: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """Drop the trailing burn-through sample when it distorts the plot scale."""

    count = currents.size
    if count < 3:
        return currents, resistances

    deltas = np.diff(currents.astype(float))
    finite = np.abs(deltas[np.isfinite(deltas)])
    if finite.size == 0:
        return currents, resistances

    last_drop = float(currents[-2] - currents[-1])
    if not np.isfinite(last_drop) or last_drop <= 0:
        return currents, resistances

    typical = float(np.median(finite))
    spread = (
        float(np.quantile(finite, 0.75) - np.quantile(finite, 0.25))
        if finite.size > 1
        else 0.0
    )
    span = float(np.nanmax(currents) - np.nanmin(currents)) if count else 0.0
    threshold = max(typical * 12.0, spread * 8.0 if spread > 0 else 0.0, span * 0.15)
    previous = float(currents[-2])
    relative_drop = last_drop / max(abs(previous), 1e-12)

    if last_drop > threshold or relative_drop > 0.25:
        return currents[:-1], resistances[:-1]

    # Some measurements barely dip in current but jump sharply in resistance on
    # the burn-through sample. Detect that spike so the Y-axis stays focused on
    # the useful portion of the ramp.
    resistance_jump = float(resistances[-1] - resistances[-2])
    if resistance_jump <= 0 or not np.isfinite(resistance_jump):
        return currents, resistances

    res_deltas = np.diff(resistances.astype(float))
    res_finite = np.abs(res_deltas[np.isfinite(res_deltas)])
    if res_finite.size == 0:
        return currents, resistances

    res_typical = float(np.median(res_finite))
    res_spread = (
        float(np.quantile(res_finite, 0.75) - np.quantile(res_finite, 0.25))
        if res_finite.size > 1
        else 0.0
    )
    res_span = float(np.nanmax(resistances) - np.nanmin(resistances)) if count else 0.0
    res_threshold = max(
        res_typical * 8.0,
        res_spread * 6.0 if res_spread > 0 else 0.0,
        res_span * 0.2,
    )
    res_previous = max(abs(float(resistances[-2])), 1e-12)
    res_relative = resistance_jump / res_previous

    if resistance_jump > res_threshold or res_relative > 0.2:
        return currents[:-1], resistances[:-1]
    return currents, resistances


# Backwards compatibility for modules that imported the private helper.
_trim_burnthrough_glitch = trim_burnthrough_glitch

