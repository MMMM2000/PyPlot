"""Plugin namespace for PyPlot."""

from .base import PyPlotPlugin, ExternalPlotterPlugin, EmbeddedWidgetPlugin
from .vsm_hysteresis import VSMHysteresisPlugin

__all__ = [
    "PyPlotPlugin",
    "ExternalPlotterPlugin",
    "EmbeddedWidgetPlugin",
    "VSMHysteresisPlugin",
]
