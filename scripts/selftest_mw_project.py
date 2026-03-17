from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd
from PyQt6 import QtWidgets

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from microwire_data_builder import storage as builder_storage
from microwire_data_builder.storage import MiniDatabaseData, MiniDatabaseStore
from microwire_data_builder.ui import (
    FabricationSection,
    VideoSection,
    _microwire_parts_from_label_safe,
)


def _payload_to_frame(payload: Dict[str, Any]) -> pd.DataFrame:
    columns = payload.get("columns")
    rows = payload.get("rows")
    if isinstance(columns, list) and isinstance(rows, list):
        return pd.DataFrame(rows, columns=[str(column) for column in columns])
    if isinstance(rows, list):
        return pd.DataFrame(rows)
    return pd.DataFrame()


def _build_fake_annealing_records(payload: Dict[str, Any]) -> List[object]:
    records: List[object] = []
    for row in payload.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        composition = str(row.get("Composition") or "").strip()
        microwire = str(row.get("Microwire") or "").strip()
        if not composition or not microwire:
            continue
        parsed = _microwire_parts_from_label_safe(microwire)
        if parsed is None:
            continue
        draw, piece, _suffix = parsed
        metadata = SimpleNamespace(
            composition_token=composition,
            draw_x=int(draw),
            piece_y=int(piece),
        )
        records.append(SimpleNamespace(metadata=metadata))
    return records


def _keys_from_frame(frame: pd.DataFrame) -> Set[Tuple[str, int, int]]:
    keys: Set[Tuple[str, int, int]] = set()
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return keys
    for _, row in frame.iterrows():
        composition = str(row.get("Composition") or "").strip()
        if not composition or composition == "Imported data:":
            continue
        try:
            draw = int(row.get("Draw"))
            piece = int(row.get("Piece"))
        except (TypeError, ValueError):
            continue
        keys.add((composition, draw, piece))
    return keys


def _project_section(payload: Dict[str, Any], name: str) -> Dict[str, Any]:
    sections = payload.get("sections", {})
    section = sections.get(name, {})
    if isinstance(section, dict):
        return section
    return {}


def _prepare_isolated_storage(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    builder_storage._storage_root = lambda: root  # type: ignore[assignment]


def _persist_result(section: FabricationSection | VideoSection, result: Any) -> None:
    payload_map = dict(section.data.extra.get("payloads", {}))
    for name, payload in result.payloads.items():
        section.store.save_payload(name, payload)
        payload_map[name] = name
    section.data.extra["payloads"] = payload_map
    section.data.processed = dict(result.processed)
    section.data.table = result.table
    section.store.save(section.data)
    section.model.set_frame(result.table)


def run_selftest(project_path: Path, output_dir: Path) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    copied_project = output_dir / f"{project_path.stem}_selftest{project_path.suffix}"
    shutil.copy2(project_path, copied_project)

    isolated_storage = output_dir / "selftest_storage"
    if isolated_storage.exists():
        shutil.rmtree(isolated_storage)
    _prepare_isolated_storage(isolated_storage)

    payload = json.loads(project_path.read_text(encoding="utf-8"))
    sections = payload.get("sections", {})

    annealing_payload = _project_section(payload, "annealing")
    microscope_payload = _project_section(payload, "microscope")
    fabrication_payload = _project_section(payload, "fabrication")
    videos_payload = _project_section(payload, "videos")

    annealing_store = MiniDatabaseStore("annealing")
    annealing_store.save(MiniDatabaseData(table=_payload_to_frame(annealing_payload)))
    annealing_store.save_payload(
        "annealing_records",
        _build_fake_annealing_records(annealing_payload),
    )

    microscope_store = MiniDatabaseStore("microscope")
    microscope_store.save(MiniDatabaseData(table=_payload_to_frame(microscope_payload)))

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    logger = logging.getLogger("mw-selftest")
    logger.setLevel(logging.INFO)

    fabrication = FabricationSection(logger, lambda *_args: None)
    fabrication.import_project_payload(fabrication_payload)
    fabrication_candidates = fabrication._collect_candidates()
    fabrication_result = fabrication.process(fabrication_candidates)
    _persist_result(fabrication, fabrication_result)

    video = VideoSection(logger, lambda *_args: None)
    video.import_project_payload(videos_payload)
    video.set_sources(fabrication.data.sources)
    video_candidates = video._collect_candidates()
    video_result = video.process(video_candidates)
    _persist_result(video, video_result)

    relevant_map, _relevant_compositions = fabrication._load_relevant_map()
    expected_keys: Set[Tuple[str, int, int]] = set()
    for composition, draws in relevant_map.items():
        for draw, pieces in draws.items():
            if draw is None:
                continue
            for piece in pieces:
                if piece is None:
                    continue
                expected_keys.add((str(composition), int(draw), int(piece)))

    fabrication_frame = fabrication_result.table if isinstance(fabrication_result.table, pd.DataFrame) else pd.DataFrame()
    video_frame = video_result.table if isinstance(video_result.table, pd.DataFrame) else pd.DataFrame()

    fabrication_keys = _keys_from_frame(fabrication_frame)
    video_keys = _keys_from_frame(video_frame)
    video_placeholder_rows = []
    if isinstance(video_frame, pd.DataFrame) and not video_frame.empty and "Microwire" in video_frame.columns:
        video_placeholder_rows = [
            {
                "Composition": str(row.get("Composition") or "").strip(),
                "Microwire": str(row.get("Microwire") or "").strip(),
            }
            for _, row in video_frame.iterrows()
            if str(row.get("Microwire") or "").strip().endswith("/?")
        ]

    fabrication.data.table = fabrication_frame
    missing_entries = fabrication._missing_data_entries()

    report = {
        "project_source": str(project_path),
        "project_copy": str(copied_project),
        "fabrication_candidates": len(fabrication_candidates),
        "fabrication_rows": len(fabrication_frame.index),
        "fabrication_expected_wires": len(expected_keys),
        "fabrication_missing_keys": [
            f"{composition} {draw}/{piece}"
            for composition, draw, piece in sorted(expected_keys - fabrication_keys)
        ],
        "fabrication_missing_data_entries": missing_entries,
        "video_candidates": len(video_candidates),
        "video_rows": len(video_frame.index),
        "video_extra_keys": [
            f"{composition} {draw}/{piece}"
            for composition, draw, piece in sorted(video_keys - expected_keys)
        ],
        "video_missing_keys": [
            f"{composition} {draw}/{piece}"
            for composition, draw, piece in sorted(expected_keys - video_keys)
        ],
        "video_placeholder_rows": video_placeholder_rows,
    }

    fabrication.close()
    video.close()
    app.quit()

    report_path = output_dir / "mw_project_selftest_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh a copied Microwire Data Builder project and report fabrication/video issues.")
    parser.add_argument(
        "project",
        type=Path,
        help="Path to the source .pydpj project",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts") / "mw_project_selftest",
        help="Directory for the copied project and report",
    )
    args = parser.parse_args(argv)

    report = run_selftest(args.project.resolve(), args.output_dir.resolve())
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
