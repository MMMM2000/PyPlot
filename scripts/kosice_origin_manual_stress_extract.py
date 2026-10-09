from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from microwire_data_builder.kosice_origin_extract import (
    OriginWorksheetExport,
    infer_sample_key,
    normalized_manual_stress_traces,
    write_builder_ready_manual_stress_txt,
)

DEFAULT_OPJU = Path(
    r"G:\Shared drives\Charakterizácia mikrodrôtov\shape memory database\Kosice\Stress-Strain-Ni50Fe27Ga23-CuCo.opju"
)
DEFAULT_OUTPUT_DIR = Path("artifacts/kosice_origin_extract")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract Košice manual stress-strain worksheets from an Origin .opju copy."
    )
    parser.add_argument("--opju", type=Path, default=DEFAULT_OPJU, help="Source Origin project.")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for manifest and normalized worksheet exports.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only check access/imports and write a status manifest; do not open Origin.",
    )
    args = parser.parse_args(argv)

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "kosice_origin_extract_manifest.json"
    status: dict[str, Any] = _base_manifest(args.opju, out_dir)

    source_status = _source_status(args.opju)
    status["source_status"] = source_status
    origin_status = _origin_status()
    status["origin_status"] = origin_status

    if args.dry_run or not source_status.get("readable") or not origin_status.get("available"):
        status["status"] = "blocked" if not args.dry_run else "dry_run"
        status["blockers"] = [
            blocker
            for blocker in (
                None if source_status.get("readable") else source_status.get("message"),
                None if origin_status.get("available") else origin_status.get("message"),
            )
            if blocker
        ]
        manifest_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return 0 if args.dry_run else 2

    try:
        exports, inventory = _extract_with_origin(args.opju, out_dir)
    except Exception as exc:
        status["status"] = "failed"
        status["error"] = f"{type(exc).__name__}: {exc}"
        status["blockers"] = [status["error"]]
        manifest_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return 1

    status["status"] = "ok"
    status["origin_inventory"] = inventory
    status["worksheets"] = [export.as_manifest_entry() for export in exports]
    status["worksheet_count"] = len(exports)
    manifest_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


def _base_manifest(source: Path, out_dir: Path) -> dict[str, Any]:
    return {
        "kind": "kosice_origin_manual_stress_extract",
        "version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_opju": str(source),
        "output_dir": str(out_dir),
        "builder_target_section": "shape_memory_stress_strain",
        "normalized_columns": ["displacement_mm", "load_g", "strain_pct", "stress_mpa"],
        "public_export_note": (
            "Keep Košice/Origin provenance visible in Builder manifests and source paths; "
            "hide or unify it only in public Excel export presentation."
        ),
    }


def _source_status(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except Exception as exc:
        return {"readable": False, "message": f"{type(exc).__name__}: {exc}"}
    return {
        "readable": True,
        "size_bytes": int(stat.st_size),
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _origin_status() -> dict[str, Any]:
    try:
        import originpro as op  # type: ignore
    except Exception as exc:
        return {"available": False, "message": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "module": getattr(op, "__file__", ""),
        "version": getattr(op, "__version__", "unknown"),
    }


def _extract_with_origin(
    source_opju: Path,
    out_dir: Path,
) -> tuple[list[OriginWorksheetExport], dict[str, Any]]:
    import originpro as op  # type: ignore

    work_dir = out_dir / "origin_copy"
    work_dir.mkdir(parents=True, exist_ok=True)
    copied_opju = work_dir / source_opju.name
    shutil.copy2(source_opju, copied_opju)

    csv_dir = out_dir / "normalized_csv"
    txt_dir = out_dir / "builder_txt"
    csv_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)

    exports: list[OriginWorksheetExport] = []
    inventory: dict[str, Any] = {"pages": []}
    opened = False
    try:
        try:
            op.set_show(False)
        except Exception:
            pass
        opened = bool(op.open(str(copied_opju.resolve()), readonly=True, asksave=False))
        if not opened:
            raise RuntimeError(f"Origin did not open copied project: {copied_opju}")
        inventory = _origin_inventory(op)
        for workbook in _origin_pages(op, "w"):
            workbook_name = _safe_text(getattr(workbook, "name", ""))
            workbook_long = _safe_text(getattr(workbook, "lname", ""))
            for sheet in workbook:
                sheet_name = _safe_text(getattr(sheet, "name", ""))
                sheet_long = _safe_text(getattr(sheet, "lname", ""))
                try:
                    frame = sheet.to_df()
                except Exception:
                    frame = None
                labels = _labels(sheet, "L")
                units = _labels(sheet, "U")
                if frame is None or frame.empty:
                    frame = _worksheet_frame_from_columns(sheet, labels)
                if frame is None or frame.empty:
                    continue
                traces = normalized_manual_stress_traces(
                    frame,
                    column_labels=labels,
                    unit_labels=units,
                )
                if not traces:
                    continue
                sample_key = infer_sample_key(workbook_name, workbook_long, sheet_name, sheet_long)
                for trace in traces:
                    stem = _safe_stem(
                        "_".join(
                            part
                            for part in (
                                sample_key,
                                workbook_long or workbook_name,
                                sheet_name,
                                trace.trace_label,
                            )
                            if part
                        )
                    )
                    output_csv = csv_dir / f"{stem}.csv"
                    output_txt = txt_dir / f"{stem}.txt"
                    trace.frame.to_csv(output_csv, index=False)
                    write_builder_ready_manual_stress_txt(trace.frame, output_txt)
                    exports.append(
                        OriginWorksheetExport(
                            sample_key=sample_key,
                            workbook=workbook_name,
                            workbook_long_name=workbook_long,
                            sheet=sheet_name,
                            sheet_long_name=sheet_long,
                            source_columns=trace.source_columns,
                            units=trace.units,
                            row_count=int(len(trace.frame.index)),
                            output_csv_path=str(output_csv),
                            output_txt_path=str(output_txt),
                        )
                    )
    finally:
        try:
            op.exit()
        except Exception:
            pass
    return exports, inventory


def _origin_inventory(op: object) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    for page in _origin_pages(op, "w"):
        page_entry: dict[str, Any] = {
            "type": "workbook",
            "name": _safe_text(getattr(page, "name", "")),
            "long_name": _safe_text(getattr(page, "lname", "")),
        }
        sheets: list[dict[str, Any]] = []
        try:
            sheet_iter = iter(page)
        except Exception:
            sheet_iter = iter(())
        for sheet in sheet_iter:
            sheet_entry: dict[str, Any] = {
                "name": _safe_text(getattr(sheet, "name", "")),
                "long_name": _safe_text(getattr(sheet, "lname", "")),
            }
            for attr in ("rows", "cols", "shape"):
                try:
                    value = getattr(sheet, attr)
                except Exception:
                    continue
                try:
                    json.dumps(value)
                    sheet_entry[attr] = value
                except TypeError:
                    sheet_entry[attr] = str(value)
            labels = _labels(sheet, "L")
            units = _labels(sheet, "U")
            if labels:
                sheet_entry["labels"] = labels
            if units:
                sheet_entry["units"] = units
            sheets.append(sheet_entry)
        page_entry["sheets"] = sheets
        pages.append(page_entry)
    return {"pages": pages, "page_count": len(pages)}


def _origin_pages(op: object, page_type: str) -> list[object]:
    try:
        return list(op.pages(page_type))
    except Exception:
        return []


def _labels(sheet: object, row_type: str) -> list[str]:
    try:
        values = sheet.get_labels(row_type)
    except Exception:
        return []
    return [str(value or "") for value in values]


def _worksheet_frame_from_columns(sheet: object, labels: list[str]) -> Any:
    try:
        column_count = int(getattr(sheet, "cols", 0) or 0)
    except Exception:
        return None
    if column_count <= 0:
        return None
    data: dict[str, list[object]] = {}
    max_len = 0
    for index in range(column_count):
        name = labels[index].strip() if index < len(labels) and labels[index].strip() else f"col{index + 1}"
        if name in data:
            name = f"{name}__{index + 1}"
        try:
            values = list(sheet.to_list(index))
        except Exception:
            values = []
        max_len = max(max_len, len(values))
        data[name] = values
    if not data or max_len <= 0:
        return None
    for name, values in data.items():
        if len(values) < max_len:
            values.extend([None] * (max_len - len(values)))
    import pandas as pd

    return pd.DataFrame(data)


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _safe_stem(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "worksheet"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
