from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from PyQt6 import QtCore, QtWidgets

from microwire_data_builder.ui import AssemblySection, VideoSection, _row_to_microwire_key


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
        }
    }

    missing_row = pd.Series(
        {
            "Composition": "Ni50Fe27Ga23",
            "Draw": 6,
            "Piece": 2,
            "_group_key": "Ni50Fe27Ga23|6|2",
            "Length (m)": None,
            "Winding speed (m/min)": 71.0,
            "Video end length (m)": None,
            "Video microwire length (m)": None,
        }
    )
    filled_row = missing_row.copy()
    filled_row["Length (m)"] = 27.88
    filled_row["Video end length (m)"] = 42.0
    filled_row["Video microwire length (m)"] = 14.12

    missing_bg = section._background_brush_for_cell(missing_row, "Length (m)")
    filled_bg = section._background_brush_for_cell(filled_row, "Length (m)")
    end_bg = section._background_brush_for_cell(filled_row, "Video end length (m)")

    assert missing_bg is not None and missing_bg.color().name() == "#3a0a0a"
    assert filled_bg is not None and filled_bg.color().name() == "#0f3b26"
    assert end_bg is not None and end_bg.color().name() == "#0f3b26"


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
