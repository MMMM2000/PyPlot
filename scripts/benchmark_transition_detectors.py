"""Benchmark current CA/TMA transition detectors against reviewed project data.

The project must be a disposable copy. Only exact measurement paths inside an
explicit --root are read. The script writes diagnostics under --out and never
writes measurement sidecars or raw data.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from microwire_data_builder.project_package import load_project
from plotting.shared.transition_review import utc_now_text
from plotting.shared.transition_review_adapters import (
    current_annealing_review_draft,
    tma_review_draft,
)
from scripts.backfill_transition_reviews import (
    _apply_ca_review,
    _apply_tma_reviews,
    _records,
)

LABEL_ORDER = (
    "As",
    "Af",
    "Ms",
    "Mf",
    "As1",
    "Af1",
    "Ms1",
    "Mf1",
    "As2",
    "Af2",
    "Ms2",
    "Mf2",
)
POSITIVE_STATUSES = {"accepted_auto", "manual_adjusted"}
SCORED_STATUSES = POSITIVE_STATUSES | {"no_transition"}


def _values(raw: object) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, float] = {}
    for label, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result[str(label)] = number
    return result


def _exact_path(raw: object, roots: Iterable[Path]) -> tuple[Path | None, str]:
    text = str(raw or "").strip()
    if not text:
        return None, "missing_path"
    path = Path(text)
    if not path.exists():
        return None, "missing_path"
    resolved = path.resolve()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        return None, "outside_roots"
    return resolved, "exact_path"


def _target_label(target: Mapping[str, Any]) -> str:
    metadata = target.get("target")
    if isinstance(metadata, Mapping) and metadata.get("stress_mpa") is not None:
        label = f"{float(metadata['stress_mpa']):.9g} MPa"
        sweep_index = int(metadata.get("sweep_index", 1) or 1)
        sweep_count = int(metadata.get("sweep_count", 1) or 1)
        if sweep_count > 1:
            label += f" sweep {sweep_index}/{sweep_count}"
        return label
    return str(target.get("target_key") or "graph")


def _compare_target(
    family: str,
    source: Path,
    target: Mapping[str, Any],
    current_auto: Mapping[str, float],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status = str(target.get("status") or "unreviewed")
    reviewed = _values(target.get("final_values"))
    auto = _values(current_auto)
    label = _target_label(target)
    comparison_rows: list[dict[str, Any]] = []
    errors: list[float] = []
    label_total = 0
    label_detected = 0
    if status in POSITIVE_STATUSES:
        ordered = [name for name in LABEL_ORDER if name in reviewed]
        ordered.extend(sorted(set(reviewed) - set(ordered)))
        for name in ordered:
            label_total += 1
            auto_value = auto.get(name)
            error = (
                abs(auto_value - reviewed[name])
                if auto_value is not None
                else None
            )
            if auto_value is not None:
                label_detected += 1
                errors.append(float(error))
            comparison_rows.append(
                {
                    "family": family,
                    "source": str(source),
                    "target": label,
                    "status": status,
                    "label": name,
                    "reviewed_value_mA": reviewed[name],
                    "auto_value_mA": auto_value,
                    "outcome": "detected" if auto_value is not None else "missed",
                    "abs_error_mA": error,
                }
            )
    elif status == "no_transition":
        if auto:
            for name, auto_value in sorted(auto.items()):
                comparison_rows.append(
                    {
                        "family": family,
                        "source": str(source),
                        "target": label,
                        "status": status,
                        "label": name,
                        "reviewed_value_mA": None,
                        "auto_value_mA": auto_value,
                        "outcome": "false_positive",
                        "abs_error_mA": None,
                    }
                )
        else:
            comparison_rows.append(
                {
                    "family": family,
                    "source": str(source),
                    "target": label,
                    "status": status,
                    "label": "",
                    "reviewed_value_mA": None,
                    "auto_value_mA": None,
                    "outcome": "true_negative",
                    "abs_error_mA": None,
                }
            )
    return comparison_rows, {
        "family": family,
        "source": str(source),
        "target": label,
        "status": status,
        "auto_label_count": len(auto),
        "label_total": label_total,
        "label_detected": label_detected,
        "errors_mA": errors,
        "no_transition_false_positive": status == "no_transition" and bool(auto),
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _metrics(targets: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [item for item in targets if item["status"] in POSITIVE_STATUSES]
    negative = [item for item in targets if item["status"] == "no_transition"]
    errors = [error for item in positive for error in item["errors_mA"]]
    label_total = sum(item["label_total"] for item in positive)
    label_detected = sum(item["label_detected"] for item in positive)
    false_positive_count = sum(
        item["no_transition_false_positive"] for item in negative
    )
    return {
        "target_count": len(targets),
        "status_counts": dict(sorted(Counter(item["status"] for item in targets).items())),
        "positive_target_count": len(positive),
        "no_transition_target_count": len(negative),
        "positive_target_auto_detection_rate": (
            sum(item["auto_label_count"] > 0 for item in positive) / len(positive)
            if positive
            else None
        ),
        "label_detection_rate": (
            label_detected / label_total if label_total else None
        ),
        "reviewed_label_count": label_total,
        "detected_label_count": label_detected,
        "no_transition_false_positive_rate": (
            false_positive_count / len(negative) if negative else None
        ),
        "no_transition_false_positive_targets": false_positive_count,
        "value_error_count": len(errors),
        "value_mae_mA": statistics.fmean(errors) if errors else None,
        "value_median_abs_error_mA": statistics.median(errors) if errors else None,
        "value_p90_abs_error_mA": _percentile(errors, 0.9),
        "value_within_2_mA_rate": (
            sum(error <= 2 for error in errors) / len(errors) if errors else None
        ),
        "value_within_5_mA_rate": (
            sum(error <= 5 for error in errors) / len(errors) if errors else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--root", action="append", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-ca", type=int)
    parser.add_argument("--max-tma-runs", type=int)
    args = parser.parse_args()

    roots = [root.resolve() for root in args.root]
    project_path = args.project.resolve()
    project = load_project(project_path)
    sections = project.get("sections")
    if not isinstance(sections, Mapping):
        raise SystemExit("Project has no sections mapping")

    rows: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    issues: list[str] = []

    annealing = sections.get("annealing")
    ca_records = (
        list(_records(annealing, "transition_reviews").values())
        if isinstance(annealing, Mapping)
        else []
    )
    if args.max_ca is not None:
        ca_records = ca_records[: max(0, args.max_ca)]
    for index, review in enumerate(ca_records, start=1):
        source, match = _exact_path(review.get("source_path"), roots)
        if source is None or not source.is_file():
            issues.append(f"current_annealing:{match}")
            continue
        try:
            draft = current_annealing_review_draft(source)
            current_auto = _values(draft["targets"][0].get("auto_values"))
            applied = copy.deepcopy(draft)
            _apply_ca_review(applied, review)
            target = applied["targets"][0]
            if str(target.get("status")) not in SCORED_STATUSES | {"excluded"}:
                continue
            new_rows, summary = _compare_target(
                "current_annealing", source, target, current_auto
            )
            rows.extend(new_rows)
            targets.append(summary)
        except Exception as exc:
            issues.append(f"current_annealing:invalid:{type(exc).__name__}")
        if index % 25 == 0 or index == len(ca_records):
            print(f"Current Annealing {index}/{len(ca_records)}", flush=True)

    tma_section = sections.get("mini_dma")
    tma_records = (
        _records(tma_section, "mini_dma_transition_reviews")
        if isinstance(tma_section, Mapping)
        else {}
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review in tma_records.values():
        grouped[str(review.get("record_path") or "")].append(review)
    grouped_items = list(grouped.items())
    if args.max_tma_runs is not None:
        grouped_items = grouped_items[: max(0, args.max_tma_runs)]
    for index, (record_path, reviews) in enumerate(grouped_items, start=1):
        source, match = _exact_path(record_path, roots)
        if source is None:
            issues.append(f"tma:{match}")
            continue
        run_dir = source if source.is_dir() else source.parent
        try:
            draft = tma_review_draft(run_dir)
            current_auto = {
                str(target.get("target_key") or ""): _values(
                    target.get("auto_values")
                )
                for target in draft["targets"]
            }
            applied = copy.deepcopy(draft)
            _apply_tma_reviews(applied, reviews)
            for target in applied["targets"]:
                if str(target.get("status")) not in SCORED_STATUSES | {"excluded"}:
                    continue
                new_rows, summary = _compare_target(
                    "tma",
                    run_dir,
                    target,
                    current_auto.get(str(target.get("target_key") or ""), {}),
                )
                rows.extend(new_rows)
                targets.append(summary)
        except Exception as exc:
            issues.append(f"tma:invalid:{type(exc).__name__}")
        print(f"TMA {index}/{len(grouped_items)}", flush=True)

    summary = {
        "schema": "transition_detector_benchmark_v1",
        "created_utc": utc_now_text(),
        "project": str(project_path),
        "roots": [str(root) for root in roots],
        "families": {
            family: _metrics([item for item in targets if item["family"] == family])
            for family in ("current_annealing", "tma")
        },
        "issues": dict(sorted(Counter(issues).items())),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    columns = [
        "family",
        "source",
        "target",
        "status",
        "label",
        "reviewed_value_mA",
        "auto_value_mA",
        "outcome",
        "abs_error_mA",
    ]
    with (args.out / "transition_detector_comparisons.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    (args.out / "transition_detector_benchmark.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

