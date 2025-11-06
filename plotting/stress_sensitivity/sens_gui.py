"""Compatibility wrapper providing the stress sensitivity backend helper."""

from __future__ import annotations

import warnings as _warnings

from plotting.shared.toolkit import restore_backend_choice

_warnings.warn(
    "plotting.stress_sensitivity.sens_gui is deprecated; import "
    "plotting.shared.toolkit.restore_backend_choice instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["restore_backend_choice"]
