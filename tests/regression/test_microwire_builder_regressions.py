from __future__ import annotations

import pandas as pd

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
