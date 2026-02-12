from __future__ import annotations

from pathlib import Path

from plotting.pyplot import window as module


class _WorkbookLoadHarness:
    def _build_workbook_shell(self, path: Path) -> module.WorkbookData:
        return module.WorkbookData(
            key=f"workbook::{path.name}",
            name=path.stem,
            worksheets=[],
            source=path,
            folder=path.parent,
        )

    def _create_worksheet_from_frame(
        self,
        workbook: module.WorkbookData,
        sheet_name: str,
        frame,
    ) -> module.WorksheetData:
        columns = {
            str(column): module.WorksheetColumnMeta(long_name=str(column))
            for column in frame.columns
        }
        return module.WorksheetData(
            key=f"{workbook.key}::{sheet_name}",
            name=sheet_name,
            dataframe=frame,
            columns=columns,
            source=workbook.source,
            workbook_key=workbook.key,
            axis_roles="",
        )


def test_load_workbook_accepts_vsm_vir_extension(tmp_path: Path) -> None:
    path = tmp_path / "sample.VSM-VIR-DATA"
    path.write_text("line1\nline2\n", encoding="utf-8")
    harness = _WorkbookLoadHarness()

    loaded = module.PyPlotWindow._load_workbook_from_file(harness, path)

    assert loaded is not None
    workbook, worksheets = loaded
    assert workbook.worksheets
    assert len(worksheets) == 1
    worksheet = worksheets[0]
    assert list(worksheet.dataframe.columns) == ["value"]
    assert len(worksheet.dataframe.index) == 2
