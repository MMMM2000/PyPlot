from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from data_logging.mini_dma_logger import mini_dma_logger as mini_dma_mod
from data_logging.mini_dma_logger.run_cleanup import (
    archive_cleanup_candidates,
    discover_cleanup_candidates_for_run,
)


def _write_run(
    root: Path,
    name: str,
    *,
    mode: str = mini_dma_mod.CURRENT_SWEEP_STRESS,
    stop_reason: str = "recipe_completed",
    microwire: str = "12/2",
    first_overheating: bool = False,
    rows: int = 3,
) -> Path:
    run_dir = root / name
    run_dir.mkdir(parents=True)
    metadata = {
        "created_utc": f"2026-06-10T12:00:{len(name):02d}+00:00",
        "sample_name": f"Ni50Fe27Ga23 {microwire} sample",
        "name_fields": {
            "composition": "Ni50Fe27Ga23",
            "microwire": microwire,
            "specimen": "",
            "condition": "trained",
        },
        "recipe_mode": mode,
        "recipe_summary": f"{mode} cleanup test",
        "stop": {
            "reason": stop_reason,
            "label": "Wire break or contact loss" if stop_reason == "wire_break_or_contact_loss" else "Recipe completed",
            "detail": "synthetic stop",
        },
        "controlled_current_sweep": {
            "mode": mode,
            "first_overheating": first_overheating,
        },
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with (run_dir / "measurement.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["elapsed_s", "stress_mpa"])
        writer.writeheader()
        for index in range(rows):
            writer.writerow({"elapsed_s": index * 2.0, "stress_mpa": 10 + index})
    return run_dir


def test_cleanup_groups_sibling_runs_by_metadata_not_folder_name(tmp_path: Path) -> None:
    root = tmp_path / "mini_dma"
    current = _write_run(
        root,
        "Ni50Fe27Ga23 12_2 iso-stress_run04",
        stop_reason="wire_break_or_contact_loss",
        mode=mini_dma_mod.CURRENT_SWEEP_STRESS,
    )
    mislabeled = _write_run(
        root,
        "Ni50Fe27Ga23 12_2 iso-current-looking-folder",
        mode=mini_dma_mod.CURRENT_SWEEP_STRESS,
    )
    _write_run(root, "Ni50Fe27Ga23 12_3 iso-stress", microwire="12/3")
    _write_run(root, "Ni50Fe27Ga23 12_2 iso-strain", mode=mini_dma_mod.CURRENT_SWEEP_STRAIN)
    _write_run(root / "archive", "old-archived", mode=mini_dma_mod.CURRENT_SWEEP_STRESS)
    _write_run(root / "automated", "automated-run", mode=mini_dma_mod.CURRENT_SWEEP_STRESS)

    candidates = discover_cleanup_candidates_for_run(current)

    assert {candidate.path for candidate in candidates} == {current, mislabeled}
    current_candidate = next(candidate for candidate in candidates if candidate.path == current)
    older_candidate = next(candidate for candidate in candidates if candidate.path == mislabeled)
    assert current_candidate.is_current_run is True
    assert current_candidate.suggested_action == "keep"
    assert older_candidate.suggested_action == "archive"
    assert older_candidate.measurement_rows == 3
    assert older_candidate.duration_s == pytest.approx(4.0)


def test_cleanup_keeps_preconditioning_runs_suggested_keep(tmp_path: Path) -> None:
    root = tmp_path / "mini_dma"
    current = _write_run(root, "run03", stop_reason="wire_break_or_contact_loss")
    preconditioning = _write_run(root, "run01 first-overheating", first_overheating=True)
    normal = _write_run(root, "run02")

    candidates = discover_cleanup_candidates_for_run(current)

    by_path = {candidate.path: candidate for candidate in candidates}
    assert by_path[preconditioning].is_preconditioning is True
    assert by_path[preconditioning].suggested_action == "keep"
    assert by_path[normal].suggested_action == "archive"


def test_archive_moves_selected_older_runs_and_refuses_current_run(tmp_path: Path) -> None:
    root = tmp_path / "mini_dma"
    current = _write_run(root, "run03", stop_reason="wire_break_or_contact_loss")
    older = _write_run(root, "run02")
    candidates = discover_cleanup_candidates_for_run(current)

    with pytest.raises(ValueError, match="current run"):
        archive_cleanup_candidates(candidates, [current], archive_name="cleanup-test")

    moves = archive_cleanup_candidates(candidates, [older], archive_name="cleanup-test")

    assert not older.exists()
    assert len(moves) == 1
    assert moves[0].destination == root / "archive" / "cleanup-test" / "run02"
    assert moves[0].destination.exists()
    assert current.exists()


def test_run_cleanup_dialog_defaults_to_archiving_only_older_non_preconditioning(
    tmp_path: Path,
    qtbot,
) -> None:
    root = tmp_path / "mini_dma"
    current = _write_run(root, "run03", stop_reason="wire_break_or_contact_loss")
    archive = _write_run(root, "run02")
    keep = _write_run(root, "run01 first-overheating", first_overheating=True)
    candidates = discover_cleanup_candidates_for_run(current)

    dialog = mini_dma_mod.RunCleanupReviewDialog(candidates)
    qtbot.addWidget(dialog)

    assert dialog.selected_archive_paths() == [archive]
    assert keep not in dialog.selected_archive_paths()
    assert current not in dialog.selected_archive_paths()
