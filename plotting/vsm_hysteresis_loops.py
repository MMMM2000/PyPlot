"""Compatibility shim for the relocated VSM hysteresis loops module."""

from __future__ import annotations

import warnings as _warnings

from plotting.plugins.vsm_hysteresis import vsm_hysteresis_loops as _impl
from plotting.plugins.vsm_hysteresis.vsm_hysteresis_loops import *  # noqa: F401,F403

_warnings.warn(
    "plotting.vsm_hysteresis_loops is deprecated; import "
    "plotting.plugins.vsm_hysteresis.vsm_hysteresis_loops instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = getattr(
    _impl,
    "__all__",
    [name for name in globals() if not name.startswith("_")],
)
