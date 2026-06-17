from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotting.plugins.shape_memory_stress_strain.origin_extract import (
    OriginColumn,
    OriginWorksheetExtract,
    build_manifest,
    copy_origin_project,
    infer_manual_column_map,
    infer_sample_key,
    safe_csv_stem,
    write_manifest,
)

DEFAULT_SOURCE = (
    "G:/Shared drives/Charakteriz\u00e1cia mikrodr\u00f4tov/"
    "shape memory database/Kosice/Stress-Strain-Ni50Fe27Ga23-CuCo.opju"
)
DEFAULT_OUTPUT = ROOT / "artifacts" / "kosice_origin_extract"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract worksheet CSVs and a manifest from the Kosice Origin "
            "manual stress-strain project."
        )
    )
    parser.add_argument("--source", type=Path, default=Path(DEFAULT_SOURCE))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest-name", default="manifest.json")
    parser.add_argument(
        "--show-origin",
        action="store_true",
        help="Show Origin during extraction. Hidden mode is used by default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not start Origin; write a manifest documenting what would run.",
    )
    return parser


def _labels(sheet: Any, label_type: str) -> list[str]:
    try:
        labels = sheet.get_labels(label_type)
    except Exception:
        labels = []
    return [str(value or "") for value in labels]


def _columns_for_sheet(sheet: Any, frame: pd.DataFrame) -> tuple[OriginColumn, ...]:
    short_names = _labels(sheet, "G")
    long_names = _labels(sheet, "L")
    units = _labels(sheet, "U")
    comments = _labels(sheet, "C")
    columns: list[OriginColumn] = []
    for index, frame_column in enumerate(frame.columns):
        columns.append(
            OriginColumn(
                index=index,
                short_name=short_names[index] if index < len(short_names) else str(frame_column),
                long_name=long_names[index] if index < len(long_names) else str(frame_column),
                units=units[index] if index < len(units) else "",
                comments=comments[index] if index < len(comments) else "",
            )
        )
    return tuple(columns)


def _open_and_extract(
    *,
    source_project: Path,
    source_copy: Path,
    output_root: Path,
    show_origin: bool,
) -> list[OriginWorksheetExtract]:
    import originpro as op  # type: ignore

    if getattr(op, "oext", False):
        op.set_show(bool(show_origin))

    op.open(str(source_copy), readonly=True, asksave=False)
    worksheets: list[OriginWorksheetExtract] = []
    csv_dir = output_root / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    project_sample = infer_sample_key(source_project.stem, default=source_project.stem)

    for workbook in op.pages("w"):
        workbook_name = str(getattr(workbook, "name", "") or "")
        workbook_lname = str(getattr(workbook, "lname", "") or "")
        for sheet in workbook:
            sheet_name = str(getattr(sheet, "name", "") or "")
            sheet_lname = str(getattr(sheet, "lname", "") or "")
            try:
                frame = sheet.to_df()
            except Exception:
                continue
            if not isinstance(frame, pd.DataFrame):
                continue
            try:
                shape_rows, shape_cols = sheet.shape
            except Exception:
                shape_rows, shape_cols = len(frame.index), len(frame.columns)
            columns = _columns_for_sheet(sheet, frame)
            sample_key = infer_sample_key(
                workbook_lname,
                workbook_name,
                sheet_lname,
                sheet_name,
                source_project.stem,
                default=project_sample,
            )
            csv_name = safe_csv_stem(sample_key, workbook_lname or workbook_name, sheet_lname or sheet_name)
            csv_path = csv_dir / f"{len(worksheets) + 1:03d}_{csv_name}.csv"
            frame.to_csv(csv_path, index=False)
            worksheets.append(
                OriginWorksheetExtract(
                    sample_key=sample_key,
                    workbook=workbook_name,
                    workbook_long_name=workbook_lname,
                    sheet=sheet_name,
                    sheet_long_name=sheet_lname,
                    columns=columns,
                    row_count=len(frame.index),
                    csv_path=csv_path,
                    source_project=source_project,
                    source_copy=source_copy,
                    sheet_rows=int(shape_rows or 0),
                    sheet_cols=int(shape_cols or 0),
                    manual_column_map=infer_manual_column_map(columns),
                )
            )
    return worksheets


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_root = args.out.resolve()
    source_project = args.source.resolve()
    manifest_path = output_root / args.manifest_name

    if not source_project.exists():
        manifest = build_manifest(
            source_project=source_project,
            source_copy=output_root / source_project.name,
            output_root=output_root,
            worksheets=[],
            origin_available=False,
            status="error",
            error=f"Source project does not exist: {source_project}",
        )
        write_manifest(manifest_path, manifest)
        print(f"Wrote {manifest_path}")
        return 2

    source_copy = copy_origin_project(source_project, output_root)
    if args.dry_run:
        manifest = build_manifest(
            source_project=source_project,
            source_copy=source_copy,
            output_root=output_root,
            worksheets=[],
            origin_available=False,
            status="dry-run",
            error=(
                "Dry run requested. Run without --dry-run on a Windows machine "
                "with Origin installed and licensed."
            ),
        )
        write_manifest(manifest_path, manifest)
        print(f"Wrote {manifest_path}")
        return 0

    origin_available = False
    try:
        worksheets = _open_and_extract(
            source_project=source_project,
            source_copy=source_copy,
            output_root=output_root,
            show_origin=bool(args.show_origin),
        )
        origin_available = True
        status = "ok"
        if worksheets and not any(worksheet.row_count for worksheet in worksheets):
            status = "metadata-only"
        manifest = build_manifest(
            source_project=source_project,
            source_copy=source_copy,
            output_root=output_root,
            worksheets=worksheets,
            origin_available=True,
            status=status,
            error=(
                "Origin automation opened the copied project and exposed worksheet "
                "labels/shapes, but no worksheet data rows were returned."
                if status == "metadata-only"
                else None
            ),
        )
        write_manifest(manifest_path, manifest)
        print(f"Wrote {manifest_path}")
        print(f"Exported {len(worksheets)} worksheet CSV file(s).")
        return 0
    except Exception as exc:
        manifest = build_manifest(
            source_project=source_project,
            source_copy=source_copy,
            output_root=output_root,
            worksheets=[],
            origin_available=origin_available,
            status="error",
            error=str(exc),
        )
        write_manifest(manifest_path, manifest)
        print(f"Wrote {manifest_path}")
        print(f"Origin extraction failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            import originpro as op  # type: ignore

            if getattr(op, "oext", False):
                op.exit()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
