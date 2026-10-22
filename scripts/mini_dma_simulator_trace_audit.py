"""Offline diagnostic on copied CSV runs; not a calibrated plant or controller replay.

Fit an incremental linear load model on the first 60% of one run only. Report
one-step predictions (observed previous load) separately from open-loop rollout
(one initial load, then recorded position/current inputs). Neither is closed-loop.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def distribution(values):
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    return None if not a.size else {
        "count": int(a.size), "median": float(np.median(a)),
        "p95": float(np.quantile(a, .95)), "max": float(np.max(a)),
    }


def read_run(path: Path):
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8-sig"))
    rows = []
    rejected = 0
    with (path / "measurement.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                values = [float(row[k]) for k in
                          ("elapsed_s", "raw_position_mm", "current_measured_mA", "load_g", "stress_mpa")]
                if not np.isfinite(values).all():
                    raise ValueError("nonfinite")
                rows.append(values)
            except (ValueError, KeyError, TypeError):
                rejected += 1
    data = np.asarray(rows)
    if len(data) < 20 or np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError(f"{path.name}: need >=20 strictly time-ordered finite observations")
    raw_times = []
    with (path / "scale_raw.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            raw_times.append(float(row["elapsed_s"]))
    diameter = metadata.get("wire_diameter_mm")
    residual = None
    if diameter and diameter > 0:
        conversion = .00980665 / (np.pi * diameter ** 2 / 4)
        residual = distribution(np.abs(data[:, 4] - data[:, 3] * conversion))
    record = {
        "name": path.name, "source_copy": str(path.resolve()),
        "files": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                  for p in sorted(path.iterdir()) if p.suffix in {".csv", ".json"}},
        "sample_name": metadata.get("sample_name"),
        "created_utc": metadata.get("created_utc"),
        "initial_length_mm": metadata.get("initial_length_mm"),
        "wire_diameter_mm": diameter,
        "diameter_semantics": "recorded wire_diameter_mm; core identity not independently verified",
        "heat_history": "not established; recipe first-overheating is not prior specimen history",
        "source_revision": metadata.get("source_revision", metadata.get("source")),
        "control": metadata.get("control"), "logging": metadata.get("logging"),
        "measurement_interval_s": distribution(np.diff(data[:, 0])),
        "raw_scale_interval_s": distribution(np.diff(raw_times)),
        "rejected_measurement_rows": rejected,
        "stress_conversion_abs_residual_mpa": residual,
        "time_alignment": "channels audited separately; elapsed origins are not assumed equal",
    }
    return data, record


def design(data):
    # Inputs are recorded outcomes, not a model of commanded actuator motion.
    return np.column_stack((np.diff(data[:, 1]),
                            np.diff(data[:, 2] ** 2), np.diff(data[:, 0])))


def evaluate(data, coefficients, name):
    predicted_delta = design(data) @ coefficients
    observed = data[1:, 3]
    conditional = data[:-1, 3] + predicted_delta
    rollout = data[0, 3] + np.cumsum(predicted_delta)
    residual = conditional - observed
    persistence = data[:-1, 3] - observed
    metrics = {
        "name": name, "samples": len(observed),
        "one_step_rmse_g": float(np.sqrt(np.mean(residual ** 2))),
        "persistence_rmse_g": float(np.sqrt(np.mean(persistence ** 2))),
        "rollout_rmse_g": float(np.sqrt(np.mean((rollout - observed) ** 2))),
        "one_step_abs_residual_g": distribution(np.abs(residual)),
        "rollout_abs_residual_g": distribution(np.abs(rollout - observed)),
    }
    for label, mask in (("current_changing", np.abs(np.diff(data[:, 2])) > .05),
                        ("current_steady", np.abs(np.diff(data[:, 2])) <= .05)):
        metrics[label] = distribution(np.abs(residual[mask]))
    return metrics, (data[1:, 0], observed, conditional, rollout, residual)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-run", required=True, type=Path)
    parser.add_argument("--heldout-run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.train_run.resolve() == args.heldout_run.resolve():
        parser.error("held-out run must differ from the training run")
    for source in (args.train_run.resolve(), args.heldout_run.resolve()):
        if args.out.resolve() == source or source in args.out.resolve().parents:
            parser.error("output must be outside evidence directories")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("output must be empty; preserve earlier reports")
    train, train_record = read_run(args.train_run)
    heldout, heldout_record = read_run(args.heldout_run)
    split = int(len(train) * .6)
    x = design(train[:split])
    y = np.diff(train[:split, 3])
    scale = np.linalg.norm(x, axis=0)
    scale[scale == 0] = 1
    scaled_coeff, _, rank, singular = np.linalg.lstsq(x / scale, y, rcond=None)
    coeff = scaled_coeff / scale
    report = {
        "schema_version": 1, "source_sha256": sha256(Path(__file__)),
        "status": "diagnostic recorded-input replay; not controller or plant validation",
        "fit": {"training_rows": split, "split_fraction": .6,
                "features": ["delta_position_mm", "delta_current_squared_mA2", "delta_time_s"],
                "coefficients": coeff.tolist(), "scaled_rank": int(rank),
                "scaled_condition": float(singular[0] / singular[-1]) if singular[-1] > 0 else None},
        "runs": [train_record, heldout_record],
        "limitations": [
            "Closed-loop records confound motion, thermal response, target changes and drift.",
            "No fitted coefficient is promoted to a physical calibration.",
            "Measured position/current are exogenous replay inputs; future controller actions are not predicted.",
            "One-step prediction observes previous load; rollout does not.",
            "No latency, backlash, core-diameter or heat-history identification is claimed.",
            "Whole-run holdout includes fault/terminal regimes; no exclusions selected after fitting.",
        ],
    }
    evaluations = [evaluate(train[:split], coeff, "training"),
                   evaluate(train[split:], coeff, "heldout_segment"),
                   evaluate(heldout, coeff, "heldout_run")]
    report["evaluations"] = [item[0] for item in evaluations]
    args.out.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 2, figsize=(13, 10), layout="constrained")
    for (metrics, arrays), (left, right) in zip(evaluations, axes):
        t, observed, conditional, rollout, residual = arrays
        left.plot(t, observed, label="measured", lw=.7)
        left.plot(t, rollout, label="recorded-input rollout", lw=.7)
        left.set(title=metrics["name"], xlabel="elapsed (s)", ylabel="load (g)")
        left.legend()
        right.plot(t, residual, lw=.6, label="one-step residual")
        right.axhline(0, color="black", lw=.5)
        right.set(xlabel="elapsed (s)", ylabel="predicted minus measured (g)")
        with (args.out / (metrics["name"] + ".csv")).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["elapsed_s", "measured_g", "conditional_g", "rollout_g", "residual_g"])
            writer.writerows(zip(*arrays))
    fig.savefig(args.out / "heldout_residuals.png", dpi=140)
    plt.close(fig)
    report["outputs"] = {p.name: sha256(p) for p in args.out.iterdir() if p.is_file()}
    (args.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"fit": report["fit"], "evaluations": report["evaluations"]}, indent=2))


if __name__ == "__main__":
    main()
