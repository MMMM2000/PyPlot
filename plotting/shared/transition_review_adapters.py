"""Build portable review drafts from Current Annealing and TMA measurements."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from plotting.plugins.current_annealing import core as annealing_core
from plotting.plugins.mini_dma import core as tma_core
from plotting.shared.transition_review import (
    dataframe_fingerprint,
    make_review,
    make_target,
    source_file_entry,
)


ANALYSIS_VERSION = "pyplot-transition-review-v1"


def _sample_from_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    return {
        key: metadata[key]
        for key in ("composition", "microwire", "sample", "load")
        if metadata.get(key) not in (None, "")
    }


def current_annealing_review_draft(
    measurement_path: Path,
    *,
    sample: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(measurement_path)
    frame = annealing_core.load_file(str(path))
    fingerprint = dataframe_fingerprint(
        frame,
        namespace="current_annealing",
        columns=("I_mA", "R_Ohm"),
    )
    auto_values: dict[str, float] = {}
    for summary in annealing_core.summarize_transition_loops(frame):
        loop = int(summary.loop_index or 1)
        for label, value in (
            ("As", summary.as_current_mA),
            ("Af", summary.af_current_mA),
            ("Ms", summary.ms_current_mA),
            ("Mf", summary.mf_current_mA),
        ):
            if value is not None:
                auto_values[f"{label}{loop}"] = float(value)
    target = make_target(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        target_key="graph",
        status="unreviewed",
        auto_values=auto_values,
    )
    return make_review(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        targets=[target],
        source_files=[source_file_entry(path, relative_to=path.parent)],
        sample=sample,
        analysis={"name": "current_annealing_tangent", "version": ANALYSIS_VERSION},
    )


def tma_review_draft(run_path: Path) -> dict[str, Any]:
    run = tma_core.load_run(Path(run_path))
    if not tma_core.supports_transition_review(run):
        raise ValueError(f"TMA run does not support transition review: {run_path}")
    fingerprint = dataframe_fingerprint(
        run.frame,
        namespace="tma",
        columns=(
            "elapsed_s",
            "automation_phase",
            "automation_target_value",
            "current_mA",
            "strain_pct",
            "resistance_ohm",
            "stress_mpa",
            "load_g",
        ),
    )
    summary = tma_core.summarize_current_sweep(run)
    targets: list[dict[str, Any]] = []
    for item in summary.targets:
        target_key = f"stress_mpa:{float(item.stress_mpa):.9g}"
        auto_values = {
            label: float(value)
            for label, value in (
                ("As", item.as_current_mA),
                ("Af", item.af_current_mA),
                ("Ms", item.ms_current_mA),
                ("Mf", item.mf_current_mA),
            )
            if value is not None
        }
        target = make_target(
            family="tma",
            measurement_fingerprint=fingerprint,
            target_key=target_key,
            status="unreviewed",
            auto_values=auto_values,
        )
        target["target"] = {
            "stress_mpa": float(item.stress_mpa),
            "load_g": None if item.load_g is None else float(item.load_g),
        }
        targets.append(target)
    if not targets:
        raise ValueError(f"No reviewable TMA targets found: {run_path}")
    measurement = run.measurement_path
    return make_review(
        family="tma",
        measurement_fingerprint=fingerprint,
        targets=targets,
        source_files=[source_file_entry(measurement, relative_to=measurement.parent)],
        sample={"sample": run.sample_name},
        analysis={"name": "tma_current_sweep_tangent", "version": ANALYSIS_VERSION},
    )


__all__ = [
    "ANALYSIS_VERSION",
    "current_annealing_review_draft",
    "tma_review_draft",
]
