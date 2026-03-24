from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PyQt6 import QtCore, QtWidgets

from microwire_data_builder import storage as builder_storage
from microwire_data_builder import ui as builder_ui
from microwire_data_builder.ui import (
    AssemblySection,
    DataFrameModel,
    FabricationSection,
    VideoSection,
    VIDEO_END_LENGTH_COLUMN,
    VIDEO_MW_LENGTH_COLUMN,
    _VideoReviewDialog,
    _TableSearchProxyModel,
    _possible_source_mismatches,
    _row_to_microwire_key,
)


def _assembly_stub() -> AssemblySection:
    section = AssemblySection.__new__(AssemblySection)
    section._imported_rows = {}
    section._show_imported = True
    return section


def test_merge_imported_rows_handles_pd_na_without_ambiguity() -> None:
    section = _assembly_stub()
    base = pd.DataFrame(
        [
            {
                "Composition": "Ni55Fe18Ga27",
                "Microwire": "4/1",
                "Mass (g)": pd.NA,
                "Data source": "Measured",
            }
        ]
    )
    key = _row_to_microwire_key(base.iloc[0])
    assert key
    section._imported_rows = {
        key: {
            "Composition": "Ni55Fe18Ga27",
            "Microwire": "4/1",
            "Mass (g)": 1.25,
        }
    }

    merged = section._merge_imported_rows(base)

    assert float(merged.at[0, "Mass (g)"]) == 1.25
    assert merged.at[0, "Data source"] == "Measured + Imported"


def test_merge_imported_payload_handles_pd_na_existing_values() -> None:
    section = _assembly_stub()
    section._imported_rows = {
        "row-key": {
            "Composition": "Ni55Fe18Ga27",
            "Microwire": "4/1",
            "Mass (g)": pd.NA,
        }
    }

    stats = section._merge_imported_payload(
        {
            "row-key": {
                "Composition": "Ni55Fe18Ga27",
                "Microwire": "4/1",
                "Mass (g)": 2.0,
            }
        }
    )

    assert stats["updated_samples"] == 1
    assert stats["added_fields"] == 1
    assert float(section._imported_rows["row-key"]["Mass (g)"]) == 2.0


def test_video_overrides_tolerate_missing_composition_column() -> None:
    section = VideoSection.__new__(VideoSection)
    section._overrides = {}
    section._fabrication_lookup_cache = {}
    section._fabrication_table = lambda: pd.DataFrame()

    frame = pd.DataFrame(
        [
            {
                "Microwire": "4/1",
                "Draw": 4,
                "Piece": 1,
            }
        ]
    )

    updated = section._apply_overrides_to_table(frame)

    assert "Composition" in updated.columns
    assert updated.at[0, "Composition"] == ""


def test_video_completion_colours_follow_missing_and_filled_cells() -> None:
    section = VideoSection.__new__(VideoSection)
    section._overrides = {}
    section._fabrication_lookup_cache = {
        "Ni50Fe27Ga23|6|2": {
            "Length (m)": None,
            "Winding speed (m/min)": 71.0,
            "Notes": None,
        }
    }
    source_key = (str(Path("C:/videos/source.mkv")),)
    section._video_source_status_cache = {source_key: True}

    missing_row = pd.Series(
        {
            "Composition": "Ni50Fe27Ga23",
            "Draw": 6,
            "Piece": 2,
            "_group_key": "Ni50Fe27Ga23|6|2",
            "Length (m)": None,
            "Winding speed (m/min)": 71.0,
            "Video end length (m)": None,
            "Video wire range (m)": None,
            "_sources": list(source_key),
        }
    )
    filled_row = missing_row.copy()
    filled_row["Length (m)"] = 27.88
    filled_row["Video end length (m)"] = 42.0
    filled_row["Video wire range (m)"] = "42-14"

    missing_bg = section._background_brush_for_cell(missing_row, "Length (m)")
    filled_bg = section._background_brush_for_cell(filled_row, "Length (m)")
    end_bg = section._background_brush_for_cell(filled_row, "Video end length (m)")
    notes_bg = section._background_brush_for_cell(missing_row, "Notes")
    notes_fg = section._foreground_brush_for_cell(missing_row, "Notes")

    assert missing_bg is not None and missing_bg.color().name() == "#3a0a0a"
    assert filled_bg is not None and filled_bg.color().name() == "#0f3b26"
    assert end_bg is not None and end_bg.color().name() == "#0f3b26"
    assert notes_bg is None
    assert notes_fg is None


def test_video_missing_sources_highlight_whole_row_red() -> None:
    section = VideoSection.__new__(VideoSection)
    section._overrides = {}
    section._fabrication_lookup_cache = {}
    section._video_source_status_cache = {}
    row = pd.Series(
        {
            "Composition": "Mn58.1Ni4.3Si18.5Sn18.8",
            "Microwire": "3/2",
            "_sources": [],
        }
    )

    background = section._background_brush_for_cell(row, "Composition")
    foreground = section._foreground_brush_for_cell(row, "Composition")

    assert background is not None and background.color().name() == "#3a0a0a"
    assert foreground is not None and foreground.color().name() == "#ffd6d6"


def test_video_section_open_button_enables_and_opens_selected_sources(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        source_path = tmp_path / "sample.mkv"
        source_path.write_bytes(b"video")
        frame = pd.DataFrame(
            [
                {
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "6/2",
                    "Draw": 6,
                    "Piece": 2,
                    "_group_key": "Ni50Fe27Ga23|6|2",
                    "_sources": [str(source_path)],
                }
            ]
        )
        section.model.set_frame(frame)
        opened: list[Path] = []
        section._open_file = lambda path: opened.append(Path(path)) or True  # type: ignore[method-assign]

        proxy_index = section._search_proxy.index(0, 0)
        assert proxy_index.isValid()
        section.table_view.selectionModel().select(
            proxy_index,
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        section._update_open_sources_enabled()

        assert section.open_sources_button.isEnabled() is True

        section._open_selected_sources()

        assert opened == [source_path]
    finally:
        section.close()


def test_video_section_selection_maps_proxy_rows_back_to_source_rows(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section._store_overrides = lambda: None  # type: ignore[method-assign]
        first = tmp_path / "first.mkv"
        second = tmp_path / "second.mkv"
        first.write_bytes(b"video")
        second.write_bytes(b"video")
        frame = pd.DataFrame(
            [
                {
                    "Composition": "BComp",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "_group_key": "BComp|1|1",
                    "_sources": [str(first)],
                },
                {
                    "Composition": "AComp",
                    "Microwire": "1/2",
                    "Draw": 1,
                    "Piece": 2,
                    "_group_key": "AComp|1|2",
                    "_sources": [str(second)],
                },
            ]
        )
        section.model.set_frame(frame)
        section._video_source_status_cache.clear()
        section.table_view.sortByColumn(0, QtCore.Qt.SortOrder.AscendingOrder)
        app.processEvents()

        proxy_index = section._search_proxy.index(0, 0)
        assert proxy_index.isValid()
        section.table_view.selectionModel().select(
            proxy_index,
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        opened: list[Path] = []
        section._open_file = lambda path: opened.append(Path(path)) or True  # type: ignore[method-assign]

        section._open_selected_sources()

        assert opened == [second]
    finally:
        section.close()


def test_video_review_dialog_updates_total_length_and_advances(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section._store_overrides = lambda: None  # type: ignore[method-assign]
        section._fabrication_table = lambda: pd.DataFrame()  # type: ignore[method-assign]
        first = tmp_path / "first.mkv"
        second = tmp_path / "second.mkv"
        first.write_bytes(b"video")
        second.write_bytes(b"video")
        frame = pd.DataFrame(
            [
                {
                    "Composition": "CompA",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "_group_key": "CompA|1|1",
                    "_sources": [str(first)],
                    VIDEO_END_LENGTH_COLUMN: None,
                },
                {
                    "Composition": "CompA",
                    "Microwire": "1/2",
                    "Draw": 1,
                    "Piece": 2,
                    "_group_key": "CompA|1|2",
                    "_sources": [str(second)],
                    VIDEO_END_LENGTH_COLUMN: None,
                },
            ]
        )
        section.model.set_frame(frame)
        section.data.table = frame.copy()
        dialog = _VideoReviewDialog(section)
        opened: list[Path] = []
        section._open_file = lambda path: opened.append(Path(path)) or True  # type: ignore[method-assign]

        dialog.load_source_row(0, open_video=True)
        assert opened == [first]

        col_idx = next(
            index
            for index, (column, _label, _editable) in enumerate(dialog._DISPLAY_COLUMNS)
            if column == VIDEO_END_LENGTH_COLUMN
        )
        item = dialog.table.item(0, col_idx)
        assert item is not None
        item.setText("12.5")
        assert float(section.model.frame().iloc[0][VIDEO_END_LENGTH_COLUMN]) == 12.5
        computed_idx = next(
            index
            for index, (column, _label, _editable) in enumerate(dialog._DISPLAY_COLUMNS)
            if column == VIDEO_MW_LENGTH_COLUMN
        )
        computed_item = dialog.table.item(0, computed_idx)
        assert computed_item is not None

        dialog._open_next_video()
        assert opened[-1] == second
        assert dialog._current_source_row == 1
    finally:
        section.close()


def test_video_review_dialog_highlights_overwrites_and_restores_previous_value(
    tmp_path: Path,
) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section._store_overrides = lambda: None  # type: ignore[method-assign]
        source_path = tmp_path / "sample.mkv"
        source_path.write_bytes(b"video")
        fabrication_frame = pd.DataFrame(
            [
                {
                    "Composition": "CompA",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "Length (m)": 27.88,
                    "Notes": "factory note",
                }
            ]
        )
        section._fabrication_table = lambda: fabrication_frame  # type: ignore[method-assign]
        section._refresh_fabrication_lookup_cache()
        frame = pd.DataFrame(
            [
                {
                    "Composition": "CompA",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "_group_key": "CompA|1|1",
                    "_sources": [str(source_path)],
                    "Length (m)": 27.88,
                    "Notes": "",
                    VIDEO_END_LENGTH_COLUMN: None,
                    VIDEO_MW_LENGTH_COLUMN: None,
                }
            ]
        )
        section.model.set_frame(frame)
        section.data.table = frame.copy()
        dialog = _VideoReviewDialog(section)
        dialog.load_source_row(0)

        length_col = next(
            index
            for index, (column, _label, _editable) in enumerate(dialog._DISPLAY_COLUMNS)
            if column == "Length (m)"
        )
        length_item = dialog.table.item(0, length_col)
        assert length_item is not None
        length_item.setText("28.4")

        updated_series = section.model.frame().iloc[0]
        background = section._background_brush_for_cell(updated_series, "Length (m)")
        foreground = section._foreground_brush_for_cell(updated_series, "Length (m)")
        tooltip = section._tooltip_for_cell(updated_series, "Length (m)")

        assert background is not None and background.color().name() == "#4a3806"
        assert foreground is not None and foreground.color().name() == "#facc15"
        assert tooltip is not None and "Previous value: 27.88" in tooltip

        assert dialog._restore_previous_value(length_col) is True
        restored_value = section.model.frame().iloc[0]["Length (m)"]
        assert float(restored_value) == pytest.approx(27.88)
    finally:
        section.close()


def test_video_overrides_snapshot_excludes_history_metadata() -> None:
    section = VideoSection.__new__(VideoSection)
    section._overrides = {
        "CompA|1|1": {
            "Length (m)": 28.4,
            section._OVERRIDE_META_KEY: {
                section._OVERRIDE_HISTORY_KEY: {
                    "Length (m)": {
                        "previous": 27.88,
                        "original": 27.88,
                    }
                }
            },
        }
    }

    snapshot = section.overrides_snapshot()

    assert snapshot == {"CompA|1|1": {"Length (m)": 28.4}}


def test_video_section_process_preserves_overlay_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        video_path = tmp_path / "CompA 1_2 detail.mkv"
        video_path.write_bytes(b"video")
        section._fabrication_table = lambda: pd.DataFrame()  # type: ignore[method-assign]
        summary = builder_ui.VideoMetricsSummary(
            temperatures=[401.5],
            underpressures=[-0.62],
            winding_speeds=[13.4],
            glass_feeds=[31.8],
            sources={video_path},
        )
        calls: list[list[Path]] = []

        def fake_collect(
            paths: list[Path],
            logger: logging.Logger,
            progress_callback=None,
        ) -> dict[tuple[str, int, int | None], builder_ui.VideoMetricsSummary]:
            del logger, progress_callback
            calls.append([Path(path) for path in paths])
            return {("CompA", 1, 2): summary}

        monkeypatch.setattr(builder_ui, "_collect_video_metrics", fake_collect)

        result = section.process([video_path])

        assert calls == [[video_path]]
        assert len(result.table.index) == 1
        row = result.table.iloc[0]
        assert float(row[builder_ui.CORE_TEMPERATURE_COLUMN]) == pytest.approx(401.5)
        assert float(row["Underpressure"]) == pytest.approx(-0.62)
        assert float(row["Winding speed (m/min)"]) == pytest.approx(13.4)
        assert float(row["Glass feeding (mm/min)"]) == pytest.approx(31.8)
    finally:
        section.close()


def test_video_end_length_propagates_to_all_rows_in_same_draw() -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section._store_overrides = lambda: None  # type: ignore[method-assign]
        section._fabrication_table = lambda: pd.DataFrame()  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "CompA",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "_group_key": "CompA|1|1",
                    "_sources": [str(Path("C:/videos/one.mkv"))],
                    "Length (m)": 6.0,
                    VIDEO_END_LENGTH_COLUMN: None,
                },
                {
                    "Composition": "CompA",
                    "Microwire": "1/2",
                    "Draw": 1,
                    "Piece": 2,
                    "_group_key": "CompA|1|2",
                    "_sources": [str(Path("C:/videos/one.mkv"))],
                    "Length (m)": 8.0,
                    VIDEO_END_LENGTH_COLUMN: None,
                },
            ]
        )
        section.model.set_frame(frame)
        section.data.table = frame.copy()
        top_left = section.model.index(0, frame.columns.get_loc(VIDEO_END_LENGTH_COLUMN))
        section.model.setData(top_left, "25.0")

        updated = section.model.frame()
        assert float(updated.iloc[0][VIDEO_END_LENGTH_COLUMN]) == 25.0
        assert float(updated.iloc[1][VIDEO_END_LENGTH_COLUMN]) == 25.0
    finally:
        section.close()


def test_video_end_length_propagation_tracks_sibling_override_history() -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section._store_overrides = lambda: None  # type: ignore[method-assign]
        section._fabrication_table = lambda: pd.DataFrame()  # type: ignore[method-assign]
        source_key = (str(Path("C:/videos/one.mkv")),)
        section._video_source_status_cache = {source_key: True}
        frame = pd.DataFrame(
            [
                {
                    "Composition": "CompA",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "_group_key": "CompA|1|1",
                    "_sources": list(source_key),
                    "Length (m)": 6.0,
                    VIDEO_END_LENGTH_COLUMN: 10.0,
                },
                {
                    "Composition": "CompA",
                    "Microwire": "1/2",
                    "Draw": 1,
                    "Piece": 2,
                    "_group_key": "CompA|1|2",
                    "_sources": list(source_key),
                    "Length (m)": 8.0,
                    VIDEO_END_LENGTH_COLUMN: 10.0,
                },
            ]
        )
        section.model.set_frame(frame)
        section.data.table = frame.copy()
        top_left = section.model.index(0, frame.columns.get_loc(VIDEO_END_LENGTH_COLUMN))
        assert section.model.setData(top_left, "25.0") is True

        updated = section.model.frame()
        sibling = updated.iloc[1]
        tooltip = section._tooltip_for_cell(sibling, VIDEO_END_LENGTH_COLUMN)
        background = section._background_brush_for_cell(sibling, VIDEO_END_LENGTH_COLUMN)
        foreground = section._foreground_brush_for_cell(sibling, VIDEO_END_LENGTH_COLUMN)

        assert tooltip is not None
        assert "10" in tooltip
        assert background is not None and background.color().name() == "#4a3806"
        assert foreground is not None and foreground.color().name() == "#facc15"

        assert section.restore_previous_value(1, VIDEO_END_LENGTH_COLUMN) is True
        restored = section.model.frame()
        assert float(restored.iloc[0][VIDEO_END_LENGTH_COLUMN]) == 10.0
        assert float(restored.iloc[1][VIDEO_END_LENGTH_COLUMN]) == 10.0
    finally:
        section.close()


def test_video_end_length_propagation_notifies_all_changed_rows() -> None:
    class _DummyIndex:
        def __init__(self, row: int, column: int) -> None:
            self._row = row
            self._column = column

        def row(self) -> int:
            return self._row

        def column(self) -> int:
            return self._column

    class _DummyModel(QtCore.QObject):
        dataChanged = QtCore.pyqtSignal(object, object, list)

        def __init__(self, frame: pd.DataFrame) -> None:
            super().__init__()
            self._frame = frame

        def frame(self) -> pd.DataFrame:
            return self._frame

        def index(self, row: int, column: int) -> _DummyIndex:
            return _DummyIndex(row, column)

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section._store_overrides = lambda: None  # type: ignore[method-assign]
        section._fabrication_table = lambda: pd.DataFrame()  # type: ignore[method-assign]
        frame = pd.DataFrame(
            [
                {
                    "Composition": "CompA",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "_group_key": "CompA|1|1",
                    "_sources": [str(Path("C:/videos/one.mkv"))],
                    "Length (m)": 6.0,
                    VIDEO_END_LENGTH_COLUMN: None,
                    VIDEO_MW_LENGTH_COLUMN: None,
                },
                {
                    "Composition": "CompA",
                    "Microwire": "1/2",
                    "Draw": 1,
                    "Piece": 2,
                    "_group_key": "CompA|1|2",
                    "_sources": [str(Path("C:/videos/one.mkv"))],
                    "Length (m)": 8.0,
                    VIDEO_END_LENGTH_COLUMN: None,
                    VIDEO_MW_LENGTH_COLUMN: None,
                },
            ]
        )
        section.data.table = frame.copy()
        events: list[tuple[int, int, int, int, tuple[QtCore.Qt.ItemDataRole, ...]]] = []

        def _capture(
            top_left: _DummyIndex,
            bottom_right: _DummyIndex,
            roles: list[QtCore.Qt.ItemDataRole],
        ) -> None:
            events.append(
                (
                    top_left.row(),
                    bottom_right.row(),
                    top_left.column(),
                    bottom_right.column(),
                    tuple(roles),
                )
            )

        end_col = frame.columns.get_loc(VIDEO_END_LENGTH_COLUMN)
        edited_frame = frame.copy()
        edited_frame.iat[0, end_col] = 25.0
        section.data.table = edited_frame
        section.model = _DummyModel(edited_frame)  # type: ignore[assignment]
        section.model.dataChanged.connect(_capture)

        edited = _DummyIndex(0, end_col)
        section._handle_cell_edited(
            edited,
            edited,
            (QtCore.Qt.ItemDataRole.EditRole,),
        )

        range_col = frame.columns.get_loc(VIDEO_MW_LENGTH_COLUMN)
        assert any(
            top_row == 0
            and bottom_row == 1
            and left_col == end_col
            and right_col == end_col
            for top_row, bottom_row, left_col, right_col, _roles in events
        )
        assert any(
            top_row == 0
            and bottom_row == 1
            and left_col == range_col
            and right_col == range_col
            for top_row, bottom_row, left_col, right_col, _roles in events
        )
        assert all(
            QtCore.Qt.ItemDataRole.EditRole not in roles
            for _top_row, _bottom_row, _left_col, _right_col, roles in events
        )
    finally:
        section.close()


def test_video_review_dialog_advance_skips_blank_notes(tmp_path: Path) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    try:
        section._store_overrides = lambda: None  # type: ignore[method-assign]
        section._fabrication_table = lambda: pd.DataFrame()  # type: ignore[method-assign]
        source_path = tmp_path / "sample.mkv"
        source_path.write_bytes(b"video")
        frame = pd.DataFrame(
            [
                {
                    "Composition": "CompA",
                    "Microwire": "1/1",
                    "Draw": 1,
                    "Piece": 1,
                    "_group_key": "CompA|1|1",
                    "_sources": [str(source_path)],
                    "Length (m)": 6.0,
                    "Piece date": "2026-03-24",
                    "Resistance (Ω)": 1.2,
                    "Core temperature (°C)": 400.0,
                    "Glass temperature (°C)": 200.0,
                    "Winding speed (m/min)": 12.0,
                    "Glass feeding (mm/min)": 3.0,
                    "Underpressure": -0.5,
                    "Production datetime": "2026-03-24 10:00",
                    "Notes": "",
                    VIDEO_END_LENGTH_COLUMN: 25.0,
                    VIDEO_MW_LENGTH_COLUMN: "25-19",
                }
            ]
        )
        section.model.set_frame(frame)
        section.data.table = frame.copy()
        dialog = _VideoReviewDialog(section)
        dialog.load_source_row(0)

        current_column = next(
            index
            for index, (column, _label, _editable) in enumerate(dialog._DISPLAY_COLUMNS)
            if column == "Production datetime"
        )
        notes_column = next(
            index
            for index, (column, _label, _editable) in enumerate(dialog._DISPLAY_COLUMNS)
            if column == "Notes"
        )
        dialog._advance_after_commit(current_column, section.model.frame().iloc[0])

        assert dialog.table.currentColumn() != notes_column
    finally:
        section.close()


def test_suspend_disk_writes_flushes_latest_state_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: tmp_path)
    builder_storage.MiniDatabaseStore._memory_data = {}
    builder_storage.MiniDatabaseStore._memory_payloads = {}
    builder_storage.MiniDatabaseStore._pending_sections = set()
    builder_storage.MiniDatabaseStore._pending_payloads = set()
    builder_storage.MiniDatabaseStore._disk_writes_suspended = 0
    try:
        store = builder_storage.MiniDatabaseStore("videos")
        store.save(
            builder_storage.MiniDatabaseData(
                sources=["old-source"],
                processed={"old-source": 1.0},
                table=pd.DataFrame([{"Composition": "OldComp", "Microwire": "1/1"}]),
                extra={"flag": "old"},
            )
        )

        with builder_storage.MiniDatabaseStore.suspend_disk_writes():
            store.save(
                builder_storage.MiniDatabaseData(
                    sources=["new-source"],
                    processed={"new-source": 2.0},
                    table=pd.DataFrame([{"Composition": "NewComp", "Microwire": "2/3"}]),
                    extra={"flag": "new"},
                )
            )
            store.save_payload("video_index", {"fresh": True})

        builder_storage.MiniDatabaseStore._memory_data = {}
        builder_storage.MiniDatabaseStore._memory_payloads = {}

        fresh_store = builder_storage.MiniDatabaseStore("videos")
        loaded = fresh_store.load()

        assert loaded.sources == ["new-source"]
        assert loaded.processed == {"new-source": 2.0}
        assert loaded.extra == {"flag": "new"}
        assert loaded.table.to_dict(orient="records") == [
            {"Composition": "NewComp", "Microwire": "2/3"}
        ]
        assert fresh_store.load_payload("video_index") == {"fresh": True}
    finally:
        builder_storage.MiniDatabaseStore._memory_data = {}
        builder_storage.MiniDatabaseStore._memory_payloads = {}
        builder_storage.MiniDatabaseStore._pending_sections = set()
        builder_storage.MiniDatabaseStore._pending_payloads = set()
        builder_storage.MiniDatabaseStore._disk_writes_suspended = 0


def test_video_cumulative_length_uses_raw_fabrication_pieces_not_just_visible_rows() -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    section = VideoSection(logging.getLogger("test"), lambda *_args: None)
    fabrication_store = builder_ui.MiniDatabaseStore("fabrication")
    original_data = fabrication_store.load()
    original_raw = fabrication_store.load_payload("fabrication_index_raw")
    try:
        raw_index = builder_ui.FabricationIndex()
        for piece, length in ((1, 10.0), (2, 20.0), (3, 30.0), (4, 40.0), (5, 50.0), (10, 70.0)):
            raw_index.set_piece("CompA", 1, piece, {"length_m": length})
        fabrication_store.save_payload("fabrication_index_raw", raw_index)

        frame = pd.DataFrame(
            [
                {
                    "Composition": "CompA",
                    "Microwire": "1/5",
                    "Draw": 1,
                    "Piece": 5,
                    "_group_key": "CompA|1|5",
                    "_sources": [str(Path("C:/videos/one.mkv"))],
                    "Length (m)": 50.0,
                    VIDEO_END_LENGTH_COLUMN: 495.6,
                },
                {
                    "Composition": "CompA",
                    "Microwire": "1/10",
                    "Draw": 1,
                    "Piece": 10,
                    "_group_key": "CompA|1|10",
                    "_sources": [str(Path("C:/videos/one.mkv"))],
                    "Length (m)": 70.0,
                    VIDEO_END_LENGTH_COLUMN: 495.6,
                },
            ]
        )

        updated = section._apply_overrides_to_table(frame)

        assert updated.iloc[0][VIDEO_MW_LENGTH_COLUMN] == "346-396"
        assert updated.iloc[1][VIDEO_MW_LENGTH_COLUMN] == "276-346"
    finally:
        fabrication_store.save(original_data)
        if original_raw is not None:
            fabrication_store.save_payload("fabrication_index_raw", original_raw)
        else:
            fabrication_store.clear_payload("fabrication_index_raw")
        section.close()


def test_video_section_filters_candidates_to_measured_wires(tmp_path: Path) -> None:
    section = VideoSection.__new__(VideoSection)
    candidates = [
        tmp_path / "Ni50Fe27Ga23" / "3.Ni50Fe27Ga23 17042024 0850" / "2024-04-17 08-44-39.mkv",
        tmp_path / "Co69" / "9.Co69 01012024 0800" / "2024-01-01 08-00-00.mkv",
        tmp_path / "Ni50Fe27Ga23" / "3.Ni50Fe27Ga23 17042024 0850" / "Ni50Fe27Ga23 3_2 detail.mkv",
    ]
    filtered = section._filter_candidates_for_relevance(
        candidates,
        {"Ni50Fe27Ga23": {3: {2}}},
        {"Ni50Fe27Ga23"},
    )

    assert filtered == [candidates[0], candidates[2]]


def test_fabrication_rows_with_missing_source_files_highlight_red() -> None:
    section = FabricationSection.__new__(FabricationSection)
    section._source_status_cache = {}
    row = pd.Series(
        {
            "Composition": "Ni48Fe25Ga23Co4",
            "Data source": "Microscope only",
            "_source_paths": [],
        }
    )

    background = section._background_brush_for_cell(row, "Composition")
    foreground = section._foreground_brush_for_cell(row, "Composition")

    assert background is not None and background.color().name() == "#3a0a0a"
    assert foreground is not None and foreground.color().name() == "#ffd6d6"


def test_possible_source_mismatches_suggests_nearby_folders(tmp_path: Path) -> None:
    (tmp_path / "Ni54Fe17Ga27Co2").mkdir()
    (tmp_path / "Ni48Fe27Ga21Cu2").mkdir()
    (tmp_path / "Co69").mkdir()

    matches = _possible_source_mismatches(
        "Ni54Fe17Ga23Co2",
        [str(tmp_path)],
    )

    assert "Ni54Fe17Ga27Co2" in matches


def test_table_search_proxy_less_than_returns_python_bool() -> None:
    model = DataFrameModel(pd.DataFrame({"Value": [np.float64(2.0), np.float64(1.0)]}))
    proxy = _TableSearchProxyModel()
    proxy.setSourceModel(model)

    result = proxy.lessThan(model.index(0, 0), model.index(1, 0))

    assert isinstance(result, bool)
