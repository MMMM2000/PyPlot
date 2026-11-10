"""Read source-folder defaults without opening or changing any research source."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class SettingsReader(Protocol):
    def value(self, key: str, defaultValue: str = "", *, type: type = str) -> str: ...


def _text(settings: SettingsReader | None, key: str) -> str:
    if settings is None:
        return ""
    return str(settings.value(key, "", type=str) or "").strip()


def source_defaults(
    tma_settings: SettingsReader,
    microscope_settings: SettingsReader | None = None,
) -> dict[str, str]:
    """Prefer microscope choices; otherwise reuse TMA's project and fabrication paths.

    No source is loaded here and no path is created. TMA's acquisition log folder
    is deliberately not shared with microscope sessions. A dedicated sibling of
    the Builder project is suggested when the microscope has no saved output.
    """
    project = _text(microscope_settings, "project_path") or _text(tma_settings, "builder_project_path")
    fabrication = (_text(microscope_settings, "fabrication_path")
                   or _text(tma_settings, "fabrication_folder_path"))
    output = _text(microscope_settings, "output_directory")
    if not output and project:
        output = str(Path(project).parent / "microscope_measurements")
    return {"project_path": project, "fabrication_path": fabrication, "output_directory": output}


def read_tma_source_defaults(microscope_settings: SettingsReader | None = None) -> dict[str, str]:
    """Read the same TMA preference namespace, without constructing its logger.

    Explicit NativeFormat keeps production TMA settings readable when a review
    instance isolates the microscope's own settings in an INI directory. Tests
    should inject private settings into ``source_defaults`` instead of calling
    this production reader against a user's persistent preferences.
    """
    from PyQt6 import QtCore

    ini_dir = os.environ.get("MINI_DMA_QSETTINGS_INI_DIR", "").strip()
    if ini_dir:
        settings = QtCore.QSettings(str(Path(ini_dir) / "mini_dma_logger.ini"),
                                   QtCore.QSettings.Format.IniFormat)
    else:
        settings = QtCore.QSettings(QtCore.QSettings.Format.NativeFormat,
                                   QtCore.QSettings.Scope.UserScope,
                                   "microwire", "mini_dma_logger")
    return source_defaults(settings, microscope_settings)


__all__ = ["source_defaults", "read_tma_source_defaults"]
