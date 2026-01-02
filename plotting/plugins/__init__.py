"""Plugin namespace for PyPlot."""

from .base import (
    PyPlotPlugin,
    ExternalPlotterPlugin,
    EmbeddedWidgetPlugin,
    register_plugin,
    get_plugin_registry,
    iter_registered_plugins,
)
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
from .vsm_temperature_scan import VSMTemperatureScanPlugin
from .dma_iso_stress import DmaIsoStressPlugin


def builtin_plugin_registry() -> dict[str, type[PyPlotPlugin]]:
    """Return the registry of built-in PyPlot plugins."""

    return get_plugin_registry()

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
    "VSMTemperatureScanPlugin",
    "DmaIsoStressPlugin",
    "register_plugin",
    "get_plugin_registry",
    "iter_registered_plugins",
    "builtin_plugin_registry",
]
