"""Common utilities shared across plotting modules."""

from __future__ import annotations

import pandas as pd

# Default save options used across plotting modules.  ``SAVE_DPI`` controls the
# resolution when saving raster formats such as PNG while ``SAVE_FORMAT``
# selects the image format/extension.  Users may adjust these globals before
# invoking the plotting functions to override the defaults.
SAVE_DPI: int = 1000
SAVE_FORMAT: str = "png"

# Flags controlled by the master launcher
# ``CHECK_OUTLIERS`` enables outlier detection while ``AUTO_REMOVE_OUTLIERS``
# skips the confirmation dialog and removes detected outliers automatically.
CHECK_OUTLIERS: bool = False
AUTO_REMOVE_OUTLIERS: bool = False


def maybe_handle_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` with statistical outliers optionally removed."""
    if not CHECK_OUTLIERS:
        return df
    from .temperature_sensitivity.core import handle_outliers
    return handle_outliers(df)


def maybe_handle_outliers_series(series: pd.Series, filename: str) -> pd.Series:
    """Return ``series`` with outliers removed when detection is enabled."""
    if not CHECK_OUTLIERS:
        return series
    df = pd.DataFrame({"sum": series, "filename": filename, "line": range(len(series))})
    df = maybe_handle_outliers(df)
    return df["sum"].reset_index(drop=True)

