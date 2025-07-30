"""Common utilities shared across plotting modules."""

from __future__ import annotations

import pandas as pd

# Flag controlled by the master launcher to enable outlier detection
CHECK_OUTLIERS: bool = False


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

