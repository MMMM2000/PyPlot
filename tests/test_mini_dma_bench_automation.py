from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_logging.mini_dma_logger import bench_automation


def _write_recipe(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recipe": {
                    "mode": "current_sweep_strain",
                    "setup": {
                        "preload_stress_mpa": 20.0,
                        "preload_duration_s": 5.0,
                    },
                    "current_sweep": {
                        "basis": "strain_pct",
                        "target_start": 0.0,
                        "target_end": 1.0,
                        "target_step": 1.0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_mini_dma_bench_plan_rejects_unarmed_execution(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(bench_automation.MiniDmaBenchAutomationError, match="armed"):
        bench_automation.load_mini_dma_bench_plan(plan_path)


def test_mini_dma_bench_plan_dry_run_validates_recipe_paths(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    summary_path = tmp_path / "summary.json"
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "summary_path": str(summary_path),
                "runs": [{"name": "trial", "recipe_path": str(recipe_path), "repeat": 2}],
            }
        ),
        encoding="utf-8",
    )

    summary = bench_automation.run_mini_dma_bench_plan(plan_path)

    assert summary["mode"] == "dry_run"
    assert summary["run_count"] == 2
    assert summary["runs"][0]["status"] == "validated"
    assert summary_path.exists()


def test_mini_dma_bench_plan_requires_automated_lengths_for_execution(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(bench_automation.MiniDmaBenchAutomationError, match="length_setup"):
        bench_automation.load_mini_dma_bench_plan(plan_path)


def test_mini_dma_bench_plan_executes_runs_with_automated_setup_lengths(tmp_path: Path) -> None:
    recipe_path = tmp_path / "iso-strain.recipe.json"
    _write_recipe(recipe_path)
    plan_path = tmp_path / "bench-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "mini_dma_bench_sequence",
                "execute": True,
                "armed": True,
                "operator_confirmation": bench_automation.MINI_DMA_BENCH_CONFIRMATION,
                "log_dir": str(tmp_path / "logs"),
                "default_max_run_duration_s": 1,
                "length_setup": {
                    "starting_length_mm": 20.0,
                    "preload_length_mm": 20.4,
                },
                "runs": [{"name": "trial", "recipe_path": str(recipe_path)}],
            }
        ),
        encoding="utf-8",
    )
    events: list[tuple[str, object]] = []

    class _FakeApp:
        def processEvents(self) -> None:
            events.append(("process", None))

    class _FakeWindow:
        def __init__(self, log_dir: str | None = None, *, persist_settings: bool = True) -> None:
            events.append(("init", log_dir))
            self._automation_active = False
            self._session_active = False
            self._session_json_path = tmp_path / "logs" / "run01" / "metadata.json"

        def set_length_setup_automation_values(
            self,
            *,
            starting_length_mm: float | None,
            preload_length_mm: float | None,
        ) -> None:
            events.append(("lengths", (starting_length_mm, preload_length_mm)))

        def _load_recipe_from_path(self, path: Path) -> None:
            events.append(("recipe", path.name))

        def _start_auto_ramp(self) -> None:
            events.append(("start", None))

        def close(self) -> None:
            events.append(("close", None))

    summary = bench_automation.run_mini_dma_bench_plan(
        plan_path,
        app_factory=lambda _qt_args: _FakeApp(),
        window_factory=_FakeWindow,
        sleep_fn=lambda _seconds: None,
    )

    assert summary["mode"] == "execute"
    assert summary["runs"][0]["status"] == "completed"
    assert ("lengths", (20.0, 20.4)) in events
    assert ("recipe", "iso-strain.recipe.json") in events
    assert ("start", None) in events
