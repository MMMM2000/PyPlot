"""Compatibility wrapper for the relocated PyPlot application module."""

from .pyplot.app import *  # noqa: F401,F403
from .pyplot.app import __all__ as _app_all

__all__ = list(_app_all)
