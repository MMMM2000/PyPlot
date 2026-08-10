"""Experimental utilities and prototypes exposed through the launcher."""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import import_module
from typing import Callable, Dict, cast

from PyQt6 import QtWidgets


LOGGER = logging.getLogger(__name__)

ExperimentFactory = Callable[..., QtWidgets.QWidget | None]


def _notify_unavailable(name: str, exc: BaseException) -> None:
    LOGGER.warning("Experiment %s is unavailable: %s", name, exc, exc_info=True)
    parent: QtWidgets.QWidget | None = QtWidgets.QApplication.activeWindow()
    QtWidgets.QMessageBox.critical(
        parent,
        "Experiment unavailable",
        f"{name} could not be loaded:\n{exc}",
    )


@lru_cache(maxsize=None)
def _resolve(module: str, attr: str = "main") -> ExperimentFactory:
    module_obj = import_module(module)
    target: object = module_obj
    for segment in attr.split("."):
        target = getattr(target, segment)
    if not callable(target):
        raise TypeError(f"{module}.{attr} is not callable")
    return cast(ExperimentFactory, target)


def _lazy(module: str, attr: str = "main", *, label: str | None = None) -> ExperimentFactory:
    experiment_name = label or module.split(".")[-1]

    def factory(*args: object, **kwargs: object) -> QtWidgets.QWidget | None:
        try:
            resolver = _resolve(module, attr)
        except Exception as exc:  # pragma: no cover - dynamic dependency failures
            _notify_unavailable(experiment_name, exc)
            return None
        try:
            return resolver(*args, **kwargs)
        except Exception as exc:
            _notify_unavailable(experiment_name, exc)
            return None

    return factory


EXPERIMENTS: Dict[str, ExperimentFactory] = {
    "TMA UI Design Lab - Instrument Refined": _lazy(
        "experiments.tma_ui_design_lab",
        "launch_refined",
        label="TMA UI Design Lab - Instrument Refined",
    ),
    "TMA UI Design Lab - Adaptive Workspace": _lazy(
        "experiments.tma_ui_design_lab",
        "launch_adaptive",
        label="TMA UI Design Lab - Adaptive Workspace",
    ),
    "TMA UI Design Lab - Adaptive Workspace v2": _lazy(
        "experiments.tma_ui_design_lab",
        "launch_adaptive_v2",
        label="TMA UI Design Lab - Adaptive Workspace v2",
    ),
    "TMA UI Design Lab - Adaptive Workspace v3": _lazy(
        "experiments.tma_ui_design_lab",
        "launch_adaptive_v3",
        label="TMA UI Design Lab - Adaptive Workspace v3",
    ),
    "TMA UI Design Lab - Adaptive Workspace v4": _lazy(
        "experiments.tma_ui_design_lab",
        "launch_adaptive_v4",
        label="TMA UI Design Lab - Adaptive Workspace v4",
    ),
    "TMA UI Design Lab - Adaptive Iso-load": _lazy(
        "experiments.tma_recipe_workspace_lab",
        "launch_iso_load",
        label="TMA UI Design Lab - Adaptive Iso-load",
    ),
    "TMA UI Design Lab - Adaptive Iso-strain": _lazy(
        "experiments.tma_recipe_workspace_lab",
        "launch_iso_strain",
        label="TMA UI Design Lab - Adaptive Iso-strain",
    ),
    "TMA UI Design Lab - Hardware Workspace": _lazy(
        "experiments.tma_hardware_workspace_lab",
        "launch",
        label="TMA UI Design Lab - Hardware Workspace",
    ),
    "TMA UI Design Lab - Plot-First Control Room": _lazy(
        "experiments.tma_ui_design_lab",
        "launch_plot_first",
        label="TMA UI Design Lab - Plot-First Control Room",
    ),
    "Strain Worksheet Updater": _lazy(
        "experiments.strain_worksheet_updater", label="Strain Worksheet Updater"
    ),
    "Current Annealing Unit Converter": _lazy(
        "experiments.current_annealing_converter", label="Current Annealing Unit Converter"
    ),
    "Thermal Camera Viewer": _lazy(
        "experiments.thermal_camera_viewer", label="Thermal Camera Viewer"
    ),
    "VSM Folder Export": _lazy(
        "experiments.vsm_folder_export", "launch_gui", label="VSM Folder Export"
    ),
}

__all__ = ["EXPERIMENTS"]
