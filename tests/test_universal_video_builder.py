from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pandas as pd
import pytest
from PyQt6 import QtGui, QtWidgets

import launcher as launcher_module
from microwire_data_builder import storage as builder_storage
from microwire_data_builder import ui as builder_ui
from microwire_data_builder import universal_video_builder as uvb
from microwire_data_builder.ui import VIDEO_END_LENGTH_COLUMN


def _sample_builder_root(tmp_path: Path) -> Path:
    source_root = (
        Path(__file__).resolve().parents[1]
        / "sample_data"
        / "database_builder"
        / "microwire data"
    )
    target_root = tmp_path / "microwire data"
    shutil.copytree(source_root / "Ni50Fe27Ga23", target_root / "Ni50Fe27Ga23")
    shutil.copytree(source_root / "Ni54Fe23Ga15", target_root / "Ni54Fe23Ga15")
    return target_root


def _build_section(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[uvb.UniversalVideoSection, Path]:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: storage_root)
    root = _sample_builder_root(tmp_path)
    section = uvb.UniversalVideoSection(logging.getLogger("test"), lambda *_: None)
    qtbot.addWidget(section)
    section.set_sources([str(root)])
    paths = uvb.scan_universal_video_inputs([root])
    result = section.process(paths)
    section._handle_worker_finished(result)
    return section, root


def test_launcher_registers_universal_video_builder() -> None:
    assert "Universal Video Builder" in launcher_module.BUILDERS


def test_universal_video_builder_process_links_fabrication_and_videos(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    section, _root = _build_section(qtbot, monkeypatch, tmp_path)

    frame = section.model.frame()
    assert not frame.empty

    ni54 = frame[
        (frame["Composition"] == "Ni54Fe23Ga15")
        & (frame["Microwire"] == "1/1")
    ]
    assert not ni54.empty
    ni54_sources = ni54.iloc[0]["_sources"]
    assert ni54_sources
    assert any(str(path).endswith(".mkv") for path in ni54_sources)

    ni50 = frame[
        (frame["Composition"] == "Ni50Fe27Ga23")
        & (frame["Microwire"] == "2/1")
    ]
    assert not ni50.empty
    assert ni50.iloc[0]["Length (m)"] is not None


def test_universal_video_builder_add_workflow_filters_draws_and_expands_all_pieces(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    section, _root = _build_section(qtbot, monkeypatch, tmp_path)

    available = section._available_rows_frame()
    expected = available[
        (available["Composition"] == "Ni50Fe27Ga23")
        & (available["Draw"].astype(int).isin([2, 4]))
    ].copy()
    assert not expected.empty

    empty = pd.DataFrame(columns=available.columns)
    section.data.table = empty
    section.store.save(section.data)
    section.model.set_frame(empty)

    section.composition_combo.setEditText("Ni50Fe27Ga23")
    section._refresh_draw_options()
    section.draw_menu.set_selected_values([2, 4])
    section._refresh_piece_options()
    assert section.piece_combo.currentData() is None

    section._add_selected_microwires()

    frame = section.model.frame()
    assert len(frame.index) == len(expected.index)
    assert set(frame["Draw"].astype(int)) == {2, 4}


def test_universal_video_builder_review_behaviour_uses_manual_values_and_video_opening(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    section, _root = _build_section(qtbot, monkeypatch, tmp_path)

    frame = section.model.frame()
    source_row = int(
        frame.index[
            (frame["Composition"] == "Ni54Fe23Ga15")
            & (frame["Microwire"] == "1/1")
        ][0]
    )

    row = section._row_series(source_row)
    assert row is not None
    initial_brush = section._background_brush_for_cell(row, VIDEO_END_LENGTH_COLUMN)
    assert isinstance(initial_brush, QtGui.QBrush)
    assert initial_brush.color().name().lower() == "#3b2a12"

    opened_paths: list[Path] = []
    monkeypatch.setattr(
        section,
        "_open_file",
        lambda path: opened_paths.append(Path(path)) or True,
    )
    opened, missing, _label = section._open_video_sources_for_row(source_row)
    assert opened is True
    assert missing == []
    assert opened_paths

    assert section._set_source_row_value(source_row, VIDEO_END_LENGTH_COLUMN, 210.5) is True
    updated = section._row_series(source_row)
    assert updated is not None
    filled_brush = section._background_brush_for_cell(updated, VIDEO_END_LENGTH_COLUMN)
    assert isinstance(filled_brush, QtGui.QBrush)
    assert filled_brush.color().name().lower() == "#0f3b26"

    assert section._set_source_row_value(source_row, VIDEO_END_LENGTH_COLUMN, 208.0) is True
    overwritten = section._row_series(source_row)
    assert overwritten is not None
    overwrite_brush = section._background_brush_for_cell(overwritten, VIDEO_END_LENGTH_COLUMN)
    assert isinstance(overwrite_brush, QtGui.QBrush)
    assert overwrite_brush.color().name().lower() == "#4a3806"


def test_universal_video_builder_missing_video_rows_stay_red(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    section, _root = _build_section(qtbot, monkeypatch, tmp_path)

    frame = section.model.frame()
    missing_video_row = frame[
        (frame["Composition"] == "Ni50Fe27Ga23")
        & (frame["Microwire"] == "2/1")
    ].iloc[0]

    background = section._background_brush_for_cell(missing_video_row, "Composition")
    foreground = section._foreground_brush_for_cell(missing_video_row, "Composition")
    assert isinstance(background, QtGui.QBrush)
    assert isinstance(foreground, QtGui.QBrush)
    assert background.color().name().lower() == "#3a0a0a"
    assert foreground.color().name().lower() == "#ffd6d6"


def test_universal_video_builder_project_round_trip_restores_manual_values(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: storage_root)
    root = _sample_builder_root(tmp_path)

    window = uvb.UniversalVideoBuilderWindow()
    qtbot.addWidget(window)
    window.section.set_sources([str(root)])
    result = window.section.process(uvb.scan_universal_video_inputs([root]))
    window.section._handle_worker_finished(result)

    frame = window.section.model.frame()
    source_row = int(
        frame.index[
            (frame["Composition"] == "Ni54Fe23Ga15")
            & (frame["Microwire"] == "1/1")
        ][0]
    )
    assert window.section._set_source_row_value(source_row, VIDEO_END_LENGTH_COLUMN, 211.25) is True
    if isinstance(window.section.search_edit, QtWidgets.QLineEdit):
        window.section.search_edit.setText("Ni54Fe23Ga15")

    project_path = tmp_path / "universal_video.pydpj"
    window._write_project_file(project_path)

    restored = uvb.UniversalVideoBuilderWindow()
    qtbot.addWidget(restored)
    restored._load_project_from_path(project_path)

    restored_frame = restored.section.model.frame()
    restored_row = restored_frame[
        (restored_frame["Composition"] == "Ni54Fe23Ga15")
        & (restored_frame["Microwire"] == "1/1")
    ].iloc[0]
    assert restored_row[VIDEO_END_LENGTH_COLUMN] == pytest.approx(211.25)
    assert isinstance(restored.section.search_edit, QtWidgets.QLineEdit)
    assert restored.section.search_edit.text() == "Ni54Fe23Ga15"
    assert restored._project_path == project_path


def test_universal_video_builder_scan_does_not_call_ocr_video_analysis(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    section, root = _build_section(qtbot, monkeypatch, tmp_path)

    import microwire_data_builder.core as builder_core

    def _fail(*_args, **_kwargs):
        raise AssertionError("OCR video extraction should not be used in the universal builder")

    monkeypatch.setattr(builder_core, "extract_video_metrics", _fail, raising=False)

    result = section.process(uvb.scan_universal_video_inputs([root]))
    assert not result.table.empty


def test_universal_video_builder_open_file_falls_back_to_platform_launcher(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    section, _root = _build_section(qtbot, monkeypatch, tmp_path)
    video_path = tmp_path / "demo.mkv"
    video_path.write_bytes(b"test")

    urls: list[str] = []
    commands: list[list[str]] = []

    def _fake_open_url(url):
        urls.append(url.toString())
        return False

    def _fake_run(cmd, check, stdout, stderr):
        commands.append(list(cmd))

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(builder_ui.QtGui.QDesktopServices, "openUrl", _fake_open_url)
    monkeypatch.setattr(builder_ui.subprocess, "run", _fake_run)
    monkeypatch.setattr(builder_ui.sys, "platform", "darwin")

    assert section._open_file(video_path) is True
    assert urls
    assert commands == [["open", str(video_path)]]
