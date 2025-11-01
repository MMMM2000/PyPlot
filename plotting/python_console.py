"""Compatibility wrapper for the relocated Python console widget."""

from .pyplot.console import *  # noqa: F401,F403
from .pyplot.console import __all__ as _console_all

__all__ = list(_console_all)
