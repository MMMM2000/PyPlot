"""Source-default tests use private INI preferences, never production app-data."""
from pathlib import Path

from PyQt6 import QtCore

from data_logging.microscope_logger.sources_config import read_tma_source_defaults, source_defaults


def _settings(path: Path, **values):
    settings = QtCore.QSettings(str(path), QtCore.QSettings.Format.IniFormat)
    for key, value in values.items():
        settings.setValue(key, value)
    settings.sync()
    return settings


def test_tma_sources_are_reused_without_reading_or_creating_them(tmp_path):
    project = tmp_path / "nonexistent" / "database.pydpj"
    fabrication = tmp_path / "fabrication-not-created"
    tma = _settings(tmp_path / "tma.ini", builder_project_path=str(project),
                    fabrication_folder_path=str(fabrication), log_dir=str(tmp_path / "tma-only"))
    result = source_defaults(tma)
    assert result == {"project_path": str(project), "fabrication_path": str(fabrication),
                      "output_directory": str(project.parent / "microscope_measurements")}
    assert not project.parent.exists()
    assert not fabrication.exists()
    assert "tma-only" not in result["output_directory"]


def test_microscope_choices_override_tma_paths(tmp_path):
    tma = _settings(tmp_path / "tma.ini", builder_project_path="old.pydpj",
                    fabrication_folder_path="old-production")
    own = _settings(tmp_path / "microscope.ini", project_path="chosen.pydpj",
                    fabrication_path="chosen-production", output_directory="chosen-output")
    before = (tmp_path / "tma.ini").read_bytes()
    assert source_defaults(tma, own) == {"project_path": "chosen.pydpj",
                                       "fabrication_path": "chosen-production",
                                       "output_directory": "chosen-output"}
    assert (tmp_path / "tma.ini").read_bytes() == before


def test_empty_defaults_remain_unknown_and_partial_overrides_fall_back(tmp_path):
    empty = _settings(tmp_path / "empty.ini")
    assert source_defaults(empty) == {"project_path": "", "fabrication_path": "", "output_directory": ""}
    tma = _settings(tmp_path / "tma.ini", builder_project_path="reference.pydpj",
                    fabrication_folder_path="production")
    own = _settings(tmp_path / "own.ini", project_path="  ", output_directory="  chosen-output  ")
    assert source_defaults(tma, own) == {"project_path": "reference.pydpj", "fabrication_path": "production",
                                       "output_directory": "chosen-output"}


def test_explicit_tma_ini_override_is_read_only(tmp_path, monkeypatch):
    tma_path = tmp_path / "mini_dma_logger.ini"
    _settings(tma_path, builder_project_path="fixture.pydpj", fabrication_folder_path="fixture-production")
    before = tma_path.read_bytes()
    monkeypatch.setenv("MINI_DMA_QSETTINGS_INI_DIR", str(tmp_path))
    result = read_tma_source_defaults()
    assert result["project_path"] == "fixture.pydpj"
    assert result["fabrication_path"] == "fixture-production"
    assert tma_path.read_bytes() == before
