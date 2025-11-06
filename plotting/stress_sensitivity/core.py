"""Compatibility shim for the relocated stress sensitivity module."""

from __future__ import annotations

import warnings as _warnings

from plotting.plugins.stress_sensitivity import core as _impl
from plotting.plugins.stress_sensitivity.core import *  # noqa: F401,F403

_warnings.warn(
    "plotting.stress_sensitivity.core is deprecated; import "
    "plotting.plugins.stress_sensitivity.core instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = getattr(
    _impl,
    "__all__",
    [name for name in globals() if not name.startswith("_")],
)
