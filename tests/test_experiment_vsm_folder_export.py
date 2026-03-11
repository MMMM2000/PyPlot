from __future__ import annotations

from pathlib import Path

import pandas as pd

from experiments import vsm_folder_export as module


def test_collect_files_filters_non_vsm_names(tmp_path: Path) -> None:
    valid_hys = tmp_path / "20260101-Hys-a090-T030-00.VSM-Hys-Data"
    valid_tscan = tmp_path / "20260101-TScn-a000-T030-00.VSM-TScn-Data"
    invalid = tmp_path / "notes.txt"
    valid_hys.write_text("x", encoding="utf-8")
    valid_tscan.write_text("x", encoding="utf-8")
    invalid.write_text("x", encoding="utf-8")

    files = module._collect_files(tmp_path, recursive=False)

    assert files == [valid_hys, valid_tscan]


def test_convert_folder_reports_parse_skip_reason(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ok_file = tmp_path / "20260101-Hys-a000-T030-00.VSM-Hys-Data"
    bad_file = tmp_path / "20260101-Hys-a090-T030-00.VSM-Hys-Data"
    ok_file.write_text("content", encoding="utf-8")
    bad_file.write_text("content", encoding="utf-8")
    out_dir = tmp_path / "out"
    messages: list[str] = []

    def _fake_read(path: Path) -> pd.DataFrame:
        if path == bad_file:
            raise ValueError("broken file")
        return pd.DataFrame({"A": [1.0], "B": [2.0]})

    monkeypatch.setattr(module, "_read_vsm_file", _fake_read)

    converted, skipped = module.convert_folder(
        tmp_path,
        out_dir,
        recursive=False,
        log=messages.append,
    )

    assert converted == 1
    assert skipped == 1
    assert any("Skipped:" in message and "parse failed" in message for message in messages)
