"""Benchmark current annealing transition-review dialog interactions."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd
from PyQt6 import QtWidgets

from microwire_data_builder import ui as builder_ui
from microwire_data_builder.core import MeasurementMetadata, MeasurementRecord


def _synthetic_record(index: int) -> MeasurementRecord:
    setpoint = 40 + (index % 60)
    up_current = np.linspace(1.0, float(setpoint), 90)
    down_current = np.linspace(float(setpoint), 1.0, 90)
    up_drop = np.clip(1.0 - np.abs(up_current - (setpoint * 0.62)) / 5.0, 0.0, 1.0)
    down_rise = np.clip(((setpoint * 0.32) - down_current) / 4.0, 0.0, 1.0)
    up_resistance = 100.0 + (0.5 * index) + (0.15 * up_current) - (8.0 * up_drop)
    down_resistance = 92.0 + (0.5 * index) + (7.0 * down_rise)
    frame = pd.DataFrame(
        {
            "I_mA": np.r_[up_current, down_current],
            "R_Ohm": np.r_[up_resistance, down_resistance],
        }
    )
    name = f"Ni44Fe27Ga23Cu3Co3 1_{index + 1} {setpoint}mA 2loops.txt"
    return MeasurementRecord(
        path=Path(name),
        metadata=MeasurementMetadata(
            composition_token="Ni44Fe27Ga23Cu3Co3",
            draw_x=1,
            piece_y=index + 1,
            setpoint_mA=setpoint,
            alt_variant=False,
            measurement_id=name,
            file_name=name,
            relpath=name,
            timestamp_mtime_utc="2026-06-19T00:00:00+00:00",
        ),
        dataframe=frame,
        sanity_ok=True,
        sanity_error=0.0,
    )


def _dialog(record_count: int) -> tuple[QtWidgets.QApplication, builder_ui._AnnealingTransitionReviewDialog, dict[str, dict[str, object]]]:  # type: ignore[name-defined]
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    records = [_synthetic_record(index) for index in range(record_count)]
    stored: dict[str, dict[str, object]] = {}

    def _set_values(key: str, values: dict[str, object]) -> None:
        stored[key] = dict(values)

    dialog = builder_ui._AnnealingTransitionReviewDialog(  # noqa: SLF001
        records,
        logging.getLogger("benchmark"),
        transition_reviews_provider=lambda: stored,
        transition_reviews_setter=_set_values,
    )
    dialog.resize(1280, 780)
    dialog.show()
    app.processEvents()
    return app, dialog, stored


def _close(dialog: QtWidgets.QDialog, app: QtWidgets.QApplication) -> None:
    dialog.hide()
    dialog.deleteLater()
    app.processEvents()


def _measure(label: str, iterations: int, callback: Callable[[int], None], app: QtWidgets.QApplication) -> dict[str, object]:
    timings_ms: list[float] = []
    for index in range(iterations):
        start = time.perf_counter()
        callback(index)
        app.processEvents()
        timings_ms.append((time.perf_counter() - start) * 1000.0)
    ordered = sorted(timings_ms)
    p95_index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.95))))
    return {
        "label": label,
        "iterations": iterations,
        "median_ms": statistics.median(timings_ms),
        "p95_ms": ordered[p95_index],
        "max_ms": max(timings_ms),
        "min_ms": min(timings_ms),
        "samples_ms": timings_ms,
    }


def run_benchmark(iterations: int, record_count: int) -> dict[str, object]:
    results: dict[str, object] = {
        "iterations": iterations,
        "record_count": record_count,
        "actions": {},
    }

    app, dialog, _stored = _dialog(record_count)
    try:
        dialog._tree.setCurrentItem(dialog._tree.topLevelItem(0))  # noqa: SLF001
        dialog._phase_controls.set_target("As1")  # noqa: SLF001
        results["actions"]["graph_click_line_placement"] = _measure(  # type: ignore[index]
            "graph_click_line_placement",
            iterations,
            lambda index: dialog._handle_plot_pick(8.0 + (index * 0.1)),  # noqa: SLF001
            app,
        )
    finally:
        _close(dialog, app)

    for label, method_name in (
        ("accept_next", "_accept_current_and_next"),
        ("no_transition_next", "_mark_current_no_transition"),
        ("exclude_graph_next", "_exclude_current_graph"),
    ):
        app, dialog, _stored = _dialog(record_count)
        try:
            results["actions"][label] = _measure(  # type: ignore[index]
                label,
                iterations,
                lambda _index, name=method_name: getattr(dialog, name)(),  # noqa: SLF001
                app,
            )
        finally:
            _close(dialog, app)

    app, dialog, _stored = _dialog(record_count)
    try:
        results["actions"]["next_unreviewed"] = _measure(  # type: ignore[index]
            "next_unreviewed",
            iterations,
            lambda _index: dialog._select_next_unreviewed(fallback_next=True),  # noqa: SLF001
            app,
        )
    finally:
        _close(dialog, app)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--record-count", type=int, default=80)
    parser.add_argument("--out", type=Path, default=Path("artifacts/current_annealing_review_performance/timings.json"))
    args = parser.parse_args()
    results = run_benchmark(
        iterations=max(1, int(args.iterations)),
        record_count=max(int(args.record_count), int(args.iterations) + 5),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
