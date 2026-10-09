from pathlib import Path

from scripts.benchmark_transition_detectors import (
    _compare_target,
    _exact_path,
    _metrics,
)


def test_exact_path_never_accepts_measurement_outside_roots(tmp_path: Path) -> None:
    approved = tmp_path / "Prague"
    outside = tmp_path / "Kosice" / "measurement.txt"
    approved.mkdir()
    outside.parent.mkdir()
    outside.write_text("data\n", encoding="utf-8")

    path, match = _exact_path(outside, [approved.resolve()])

    assert path is None
    assert match == "outside_roots"


def test_detector_metrics_distinguish_misses_from_no_transition_false_positives(
    tmp_path: Path,
) -> None:
    positive_rows, positive = _compare_target(
        "current_annealing",
        tmp_path / "positive.txt",
        {
            "target_key": "graph",
            "status": "manual_adjusted",
            "final_values": {"As1": 20.0, "Af1": 30.0},
        },
        {"As1": 22.0},
    )
    negative_rows, negative = _compare_target(
        "current_annealing",
        tmp_path / "negative.txt",
        {
            "target_key": "graph",
            "status": "no_transition",
            "final_values": {},
        },
        {"As1": 50.0},
    )

    metrics = _metrics([positive, negative])

    assert [row["outcome"] for row in positive_rows] == ["detected", "missed"]
    assert negative_rows[0]["outcome"] == "false_positive"
    assert metrics["label_detection_rate"] == 0.5
    assert metrics["no_transition_false_positive_rate"] == 1.0
    assert metrics["value_mae_mA"] == 2.0
