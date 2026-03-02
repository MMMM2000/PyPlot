from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from plotting.pyplot.window import (
    PyPlotWindow,
    WorkbookData,
    WorksheetColumnMeta,
    WorksheetData,
)


def _make_sheet(*, workbook_key: object, sheet_name: str, signal_values: list[float]) -> WorksheetData:
    frame = pd.DataFrame(
        {
            "temperature": list(range(len(signal_values))),
            "signal": signal_values,
        }
    )
    sheet_key = (workbook_key, sheet_name)
    return WorksheetData(
        key=sheet_key,
        name=sheet_name,
        dataframe=frame,
        columns={
            "temperature": WorksheetColumnMeta(long_name="Temperature"),
            "signal": WorksheetColumnMeta(long_name="Signal"),
        },
        workbook_key=workbook_key,
    )


def test_detect_outlier_rows_flags_isolated_spike() -> None:
    frame = pd.DataFrame(
        {
            "temperature": list(range(30)),
            "signal": [1.0] * 29 + [20.0],
            "label": ["x"] * 30,
        }
    )

    mask, column_hits = PyPlotWindow._detect_outlier_rows(frame)

    assert int(mask.sum()) == 1
    assert mask.iloc[29]
    assert column_hits == {"signal": 1}


def test_collect_and_apply_outlier_findings_updates_worksheets() -> None:
    workbook_key = ("test", "outliers")
    worksheet = _make_sheet(workbook_key=workbook_key, sheet_name="Sheet1", signal_values=[1.0] * 29 + [20.0])
    workbook = WorkbookData(key=workbook_key, name="Workbook A", worksheets=[worksheet.key])

    calls = {
        "refresh": 0,
        "sync": 0,
        "actions": 0,
        "dirty": 0,
    }

    holder = SimpleNamespace(
        _worksheets={worksheet.key: worksheet},
        _workbooks={workbook_key: workbook},
        _worksheet_models={},
        _detect_outlier_rows=PyPlotWindow._detect_outlier_rows,
        _refresh_imported_data_summary=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
        _sync_shared_action_states=lambda: calls.__setitem__("sync", calls["sync"] + 1),
        _update_worksheet_actions=lambda: calls.__setitem__("actions", calls["actions"] + 1),
        _mark_project_dirty=lambda: calls.__setitem__("dirty", calls["dirty"] + 1),
    )

    findings = PyPlotWindow._collect_outlier_findings(holder)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.workbook_name == "Workbook A"
    assert finding.row_indices == [29]

    removed = PyPlotWindow._apply_outlier_findings(holder, findings)
    assert removed == 1
    assert len(holder._worksheets[worksheet.key].dataframe.index) == 29
    assert calls == {"refresh": 1, "sync": 1, "actions": 1, "dirty": 1}


def test_format_outlier_findings_contains_rows_and_columns() -> None:
    workbook_key = ("test", "outliers")
    worksheet = _make_sheet(workbook_key=workbook_key, sheet_name="Sheet1", signal_values=[1.0] * 29 + [20.0])
    workbook = WorkbookData(key=workbook_key, name="Workbook A", worksheets=[worksheet.key])
    holder = SimpleNamespace(
        _worksheets={worksheet.key: worksheet},
        _workbooks={workbook_key: workbook},
        _detect_outlier_rows=PyPlotWindow._detect_outlier_rows,
    )

    findings = PyPlotWindow._collect_outlier_findings(holder)
    text = PyPlotWindow._format_outlier_findings(findings)

    assert "Workbook A / Sheet1" in text
    assert "Rows: 30" in text
    assert "Columns: signal (1)" in text


def test_outlier_preview_frame_includes_original_row_numbers() -> None:
    workbook_key = ("test", "outliers")
    worksheet = _make_sheet(workbook_key=workbook_key, sheet_name="Sheet1", signal_values=[1.0] * 29 + [20.0])
    workbook = WorkbookData(key=workbook_key, name="Workbook A", worksheets=[worksheet.key])
    holder = SimpleNamespace(
        _worksheets={worksheet.key: worksheet},
        _workbooks={workbook_key: workbook},
        _detect_outlier_rows=PyPlotWindow._detect_outlier_rows,
    )

    findings = PyPlotWindow._collect_outlier_findings(holder)
    preview = PyPlotWindow._outlier_preview_frame(worksheet.dataframe, findings[0])

    assert list(preview.columns) == ["Row", "temperature", "signal"]
    assert int(preview.iloc[0]["Row"]) == 30
    assert float(preview.iloc[0]["signal"]) == 20.0
