"""Plugin namespace for PyPlot."""

from .base import PyPlotPlugin, ExternalPlotterPlugin, EmbeddedWidgetPlugin
from .temperature_dependence import TemperatureDependencePlugin
from .temperature_sensitivity import TemperatureSensitivityPlugin
from .current_annealing import CurrentAnnealingPlugin
from .stress_dependence import StressDependencePlugin
from .stress_sensitivity import StressSensitivityPlugin
from .hsw_load_compare import HswLoadComparePlugin
from .maxion_continuous import MaxionContinuousPlugin
from .pdf_plotter import PdfPlotterPlugin
from .hysteresis_loops import HysteresisLoopsPlugin
from .hsw_distribution import HswDistributionPlugin
from .strain_3d_plot import Strain3DPlotPlugin
from .vsm_hysteresis import VSMHysteresisPlugin

__all__ = [
    "PyPlotPlugin",
    "ExternalPlotterPlugin",
    "EmbeddedWidgetPlugin",
    "TemperatureDependencePlugin",
    "TemperatureSensitivityPlugin",
    "CurrentAnnealingPlugin",
    "StressDependencePlugin",
    "StressSensitivityPlugin",
    "HswLoadComparePlugin",
    "MaxionContinuousPlugin",
    "PdfPlotterPlugin",
    "HysteresisLoopsPlugin",
    "HswDistributionPlugin",
    "Strain3DPlotPlugin",
    "VSMHysteresisPlugin",
]
