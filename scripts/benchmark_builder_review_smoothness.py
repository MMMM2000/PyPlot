"""Benchmark Microwire Data Builder transition-review responsiveness."""

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

import pandas as pd
from PyQt6 import QtCore, QtWidgets

from microwire_data_builder import ui as builder_ui
from microwire_data_builder.core import MiniDmaRecord
from scripts.benchmark_current_annealing_review import run_benchmark as run_current_annealing_benchmark


class _HeartbeatProbe(QtCore.QObject):
    def __init__(self, interval_ms: int = 20) -> None:
        super().__init__()
        self.interval_ms = max(int(interval_ms), 5)
        self.max_lag_ms = 0.0
        self.samples = 0
        self._last_s: float | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._last_s = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        if self._last_s is not None:
            elapsed_ms = (now - self._last_s) * 1000.0
            self.max_lag_ms = max(self.max_lag_ms, max(0.0, elapsed_ms - self.interval_ms))
            self.samples += 1
        self._last_s = now


def _ensure_app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _measure(
    label: str,
    iterations: int,
    callback: Callable[[int], None],
    app: QtWidgets.QApplication,
) -> dict[str, object]:
    timings_ms: list[float] = []
    heartbeat = _HeartbeatProbe()
    heartbeat.start()
    app.processEvents()
    try:
        for index in range(iterations):
            start = time.perf_counter()
            callback(index)
            app.processEvents()
            timings_ms.append((time.perf_counter() - start) * 1000.0)
    finally:
        app.processEvents()
        heartbeat.stop()
    ordered = sorted(timings_ms)
    p95_index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.95))))
    return {
        "label": label,
        "iterations": iterations,
        "median_ms": statistics.median(timings_ms),
        "p95_ms": ordered[p95_index],
        "max_ms": max(timings_ms),
        "min_ms": min(timings_ms),
        "event_loop_max_lag_ms": heartbeat.max_lag_ms,
        "event_loop_samples": heartbeat.samples,
        "samples_ms": timings_ms,
    }


def _mini_dma_record() -> MiniDmaRecord:
    run_path = REPO_ROOT / "sample_data" / "mini dma" / "Ni50Fe27Ga23 12_2 test_run32"
    return MiniDmaRecord(
        path=run_path,
        sample="Ni50Fe27Ga23 12_2",
        data=pd.DataFrame(),
        key=("Ni50Fe27Ga23", 12, 2, None),
        label=run_path.name,
    )


def _mini_dma_dialog() -> tuple[
    QtWidgets.QApplication,
    builder_ui._MiniDmaTransitionReviewDialog,  # type: ignore[name-defined]
    dict[str, dict[str, object]],
]:
    app = _ensure_app()
    logger = logging.getLogger("builder_review_smoothness")
    record = _mini_dma_record()
    stored: dict[str, dict[str, object]] = {}

    def _set_review(record_id: str, payload: dict[str, object]) -> None:
        stored[record_id] = dict(payload)

    entries = builder_ui._mini_dma_transition_review_entries([record], logger)  # noqa: SLF001
    dialog = builder_ui._MiniDmaTransitionReviewDialog(  # noqa: SLF001
        [record],
        logger,
        review_provider=lambda: stored,
        review_setter=_set_review,
    )
    run_key = dialog._runs[0].key  # noqa: SLF001
    dialog._handle_load_finished(  # noqa: SLF001
        builder_ui._MiniDmaTransitionReviewLoadResult(run_key, entries)  # noqa: SLF001
    )
    dialog.resize(1280, 820)
    dialog.show()
    app.processEvents()
    if dialog._visible_refs:  # noqa: SLF001
        dialog.tree.setCurrentItem(dialog._tree_items[dialog._visible_refs[0]])  # noqa: SLF001
        app.processEvents()
    return app, dialog, stored


def _close(dialog: QtWidgets.QDialog, app: QtWidgets.QApplication) -> None:
    dialog.hide()
    dialog.deleteLater()
    app.processEvents()


def _run_mini_dma_benchmark(iterations: int) -> dict[str, object]:
    results: dict[str, object] = {"actions": {}}
    for label, callback_factory in (
        (
            "accept_next",
            lambda dialog: lambda _index: dialog._accept_current_and_next(),  # noqa: SLF001
        ),
        (
            "no_transition_next",
            lambda dialog: lambda _index: dialog._set_current_review(  # noqa: SLF001
                builder_ui.MINI_DMA_REVIEW_STATUS_NO_TRANSITION,
                move_next=True,
            ),
        ),
        (
            "exclude_next",
            lambda dialog: lambda _index: dialog._set_current_review(  # noqa: SLF001
                builder_ui.MINI_DMA_REVIEW_STATUS_EXCLUDED,
                move_next=True,
            ),
        ),
        (
            "next_unreviewed",
            lambda dialog: lambda _index: dialog._select_next_unreviewed(),  # noqa: SLF001
        ),
    ):
        app, dialog, _stored = _mini_dma_dialog()
        try:
            results["actions"][label] = _measure(label, iterations, callback_factory(dialog), app)  # type: ignore[index]
        finally:
            _close(dialog, app)
    return results


def run_benchmark(iterations: int, record_count: int) -> dict[str, object]:
    iterations = max(1, int(iterations))
    record_count = max(int(record_count), iterations + 5)
    return {
        "iterations": iterations,
        "record_count": record_count,
        "current_annealing": run_current_annealing_benchmark(iterations, record_count),
        "mini_dma": _run_mini_dma_benchmark(iterations),
        "interpretation": {
            "pyqtgraph": (
                "Synthetic review actions are measured directly here; use p95/max action "
                "latency and event_loop_max_lag_ms before considering a plot-library migration."
            )
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--record-count", type=int, default=80)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("artifacts/builder_review_smoothness/timings.json"),
    )
    args = parser.parse_args()
    results = run_benchmark(args.iterations, args.record_count)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
