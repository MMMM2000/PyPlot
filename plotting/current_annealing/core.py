"""Compatibility shim for the relocated current annealing core module."""

from __future__ import annotations

import warnings as _warnings

from plotting.plugins.current_annealing import core as _impl
from plotting.plugins.current_annealing.core import *  # noqa: F401,F403

_warnings.warn(
    "plotting.current_annealing.core is deprecated; import "
    "plotting.plugins.current_annealing.core instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = getattr(
    _impl,
    "__all__",
    [name for name in globals() if not name.startswith("_")],
)
