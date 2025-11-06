"""Compatibility shim for the relocated stress dependence module."""

from __future__ import annotations

import warnings as _warnings

from plotting.plugins.stress_dependence import core as _impl
from plotting.plugins.stress_dependence.core import *  # noqa: F401,F403

_warnings.warn(
    "plotting.stress_dependence.core is deprecated; import "
    "plotting.plugins.stress_dependence.core instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = getattr(
    _impl,
    "__all__",
    [name for name in globals() if not name.startswith("_")],
)
