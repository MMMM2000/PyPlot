"""Compatibility shim for the relocated burnthrough helper."""

from __future__ import annotations

import warnings as _warnings

from plotting.plugins.current_annealing import burnthrough as _impl
from plotting.plugins.current_annealing.burnthrough import *  # noqa: F401,F403

_warnings.warn(
    "plotting.current_annealing.burnthrough is deprecated; import "
    "plotting.plugins.current_annealing.burnthrough instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = getattr(
    _impl,
    "__all__",
    [name for name in globals() if not name.startswith("_")],
)
