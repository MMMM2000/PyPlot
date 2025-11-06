"""Compatibility shim for the relocated temperature sensitivity module."""

from __future__ import annotations

import warnings as _warnings

from plotting.plugins.temperature_sensitivity import core as _impl
from plotting.plugins.temperature_sensitivity.core import *  # noqa: F401,F403

_warnings.warn(
    "plotting.temperature_sensitivity.core is deprecated; import "
    "plotting.plugins.temperature_sensitivity.core instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = getattr(
    _impl,
    "__all__",
    [name for name in globals() if not name.startswith("_")],
)
