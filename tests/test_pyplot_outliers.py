from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
from matplotlib.collections import PathCollection
import numpy as np

from plotting.pyplot.window import (
    GraphLineState,
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


def test_detect_outlier_rows_flags_local_dropout_cluster_in_curve() -> None:
    baseline = [0.05 + index * 0.001 for index in range(40)]
    for index in (12, 13, 14, 15):
        baseline[index] = -0.01
    frame = pd.DataFrame(
        {
            "temperature": list(range(40)),
            "signal": baseline,
        }
    )

    mask, column_hits = PyPlotWindow._detect_outlier_rows(frame)

    flagged_rows = [index for index, flagged in enumerate(mask.tolist()) if flagged]
    assert flagged_rows == [12, 13, 14, 15]
    assert column_hits == {"signal": 4}


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


def test_outlier_preview_figure_highlights_flagged_points() -> None:
    workbook_key = ("test", "outliers")
    worksheet = _make_sheet(
        workbook_key=workbook_key,
        sheet_name="Sheet1",
        signal_values=[1.0] * 29 + [20.0],
    )
    workbook = WorkbookData(key=workbook_key, name="Workbook A", worksheets=[worksheet.key])
    holder = SimpleNamespace(
        _worksheets={worksheet.key: worksheet},
        _workbooks={workbook_key: workbook},
        _detect_outlier_rows=PyPlotWindow._detect_outlier_rows,
    )

    findings = PyPlotWindow._collect_outlier_findings(holder)
    figure = PyPlotWindow._create_outlier_preview_figure(holder, findings[0])

    assert figure is not None
    assert figure.axes
    axis = figure.axes[0]
    assert not axis.lines
    assert any(isinstance(collection, PathCollection) for collection in axis.collections)


def test_outlier_candidate_columns_respect_axis_roles() -> None:
    worksheet = WorksheetData(
        key="sheet",
        name="Sheet",
        dataframe=pd.DataFrame(
            {
                "Timestamp": ["t1", "t2"],
                "Elapsed (s)": [0.0, 1.0],
                "Setpoint (°C)": [10.0, 11.0],
                "Temperature (°C)": [9.8, 10.9],
                "Resistance (Ω)": [1.0, 1.1],
            }
        ),
        columns={},
        workbook_key="wb",
        axis_roles="MMMXY",
    )

    columns = PyPlotWindow._outlier_candidate_columns(worksheet.dataframe, worksheet)

    assert columns == ["Temperature (°C)", "Resistance (Ω)"]


def _make_line_state(*, x_values: list[float], y_values: list[float]) -> GraphLineState:
    line = SimpleNamespace(
        get_xdata=lambda: np.asarray(x_values, dtype=float),
        get_ydata=lambda: np.asarray(y_values, dtype=float),
        get_visible=lambda: True,
    )
    return GraphLineState(
        key=("Series", 1.0),
        label="Series",
        line=line,
        base_x=np.asarray(x_values, dtype=float),
        base_y=np.asarray(y_values, dtype=float),
        full_x=np.asarray(x_values, dtype=float),
        full_y=np.asarray(y_values, dtype=float),
    )


def test_detect_series_outliers_isolated_x_jump_flags_single_point() -> None:
    state = _make_line_state(
        x_values=[-60.0, -50.0, -40.0, -30.0, -20.0, 0.0, 100.0, 110.0, 120.0],
        y_values=[0.118, 0.121, 0.124, 0.127, 0.130, 0.159, 0.162, 0.168, 0.173],
    )

    methods = PyPlotWindow._compare_graph_outlier_methods([state])

    isolated = methods["isolated_x_jump"]
    assert isolated.total_flagged == 1
    assert isolated.masks_by_line[state.key].tolist() == [False, False, False, False, False, True, False, False, False]
