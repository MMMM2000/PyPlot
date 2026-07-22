"""Generate the standard TMA optimization campaign report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from data_logging.mini_dma_logger.campaign import (
    campaign_root,
    discover_campaign_run_dirs,
    load_campaign,
    nested,
    read_csv_rows,
)


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _series(rows: list[dict[str, str]], name: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _float(row.get(name))
        if value is not None:
            values.append(value)
    return values


def _hold_spans(rows: list[dict[str, str]]) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    start: float | None = None
    previous: float | None = None
    for row in rows:
        elapsed = _float(row.get("elapsed_s"))
        if elapsed is None:
            continue
        phase = str(row.get("automation_phase") or "")
        if phase == "current_hold":
            if start is None:
                start = elapsed
            previous = elapsed
        elif start is not None:
            spans.append((start, previous if previous is not None else start))
            start = None
            previous = None
    if start is not None:
        spans.append((start, previous if previous is not None else start))
    return spans


def _plot_run(run_dir: Path, image_path: Path) -> dict[str, Any]:
    rows = read_csv_rows(run_dir / "measurement.csv")
    metadata: dict[str, Any] = {}
    metadata_path = run_dir / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
    elapsed = _series(rows, "elapsed_s")
    stress = _series(rows, "stress_mpa")
    strain = _series(rows, "strain_pct")
    current = _series(rows, "current_measured_mA")
    set_current = _series(rows, "current_set_mA")
    hold_spans = _hold_spans(rows)

    image_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.8), constrained_layout=True)
    ax = axes[0]
    if elapsed and stress:
        ax.plot(elapsed[: len(stress)], stress[: len(elapsed)], color="#1f77b4", lw=1.2)
    for start, end in hold_spans:
        ax.axvspan(start, end, color="#f6a03b", alpha=0.25)
    ax.set_title("Stress vs Time")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Stress (MPa)")
    ax.grid(True, alpha=0.25)

    ax = axes[1]
    current_axis = current if current else set_current
    if current_axis and strain:
        ax.plot(current_axis[: len(strain)], strain[: len(current_axis)], color="#2ca02c", lw=1.2)
        hold_current: list[float] = []
        hold_strain: list[float] = []
        for row in rows:
            if str(row.get("automation_phase") or "") != "current_hold":
                continue
            x = _float(row.get("current_measured_mA")) or _float(row.get("current_set_mA"))
            y = _float(row.get("strain_pct"))
            if x is not None and y is not None:
                hold_current.append(x)
                hold_strain.append(y)
        if hold_current:
            ax.scatter(hold_current, hold_strain, color="#d62728", s=12, label="current hold", zorder=3)
            ax.legend(loc="best")
    ax.set_title("Strain vs Current")
    ax.set_xlabel("Measured current (mA)")
    ax.set_ylabel("Strain (%)")
    ax.grid(True, alpha=0.25)
    fig.suptitle(run_dir.name)
    fig.savefig(image_path, dpi=150)
    plt.close(fig)
    stop = metadata.get("stop") if isinstance(metadata.get("stop"), dict) else {}
    source_control = metadata.get("source_control") if isinstance(metadata.get("source_control"), dict) else {}
    return {
        "run_dir": str(run_dir),
        "image_path": str(image_path),
        "measurement_rows": len(rows),
        "hold_spans": len(hold_spans),
        "stop_reason": stop.get("reason", ""),
        "stop_detail": stop.get("detail", ""),
        "git_branch": source_control.get("branch", ""),
        "git_commit": source_control.get("commit", ""),
        "max_stress_mpa": max(stress) if stress else None,
        "max_measured_current_mA": max(current) if current else None,
    }


def generate_report(manifest_path: Path | str) -> dict[str, Any]:
    path = Path(manifest_path)
    manifest = load_campaign(path)
    root = campaign_root(manifest, path)
    report_path = root / str(nested(manifest, "reporting", "report_path") or "reports/mini_dma_optimization_report.pdf")
    image_dir = root / str(nested(manifest, "reporting", "image_dir") or "reports/images")
    summary_path = root / str(nested(manifest, "reporting", "summary_path") or "analysis/summary.json")
    run_dirs = discover_campaign_run_dirs(manifest, path)
    rows: list[dict[str, Any]] = []
    for index, run_dir in enumerate(run_dirs, start=1):
        rows.append(_plot_run(run_dir, image_dir / f"{index:02d}_{run_dir.name}.png"))

    report_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(report_path), pagesize=A4)
    width, height = A4
    pdf.setTitle(str(nested(manifest, "campaign", "title") or "TMA optimization report"))
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(0.7 * inch, height - 0.8 * inch, "TMA Optimization Report")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(0.7 * inch, height - 1.05 * inch, f"Campaign: {nested(manifest, 'campaign', 'id') or ''}")
    pdf.drawString(0.7 * inch, height - 1.22 * inch, f"Runs: {len(rows)}")
    y = height - 1.55 * inch
    for row in rows[:22]:
        pdf.drawString(
            0.7 * inch,
            y,
            f"{Path(row['run_dir']).name}: rows={row['measurement_rows']} hold_spans={row['hold_spans']} stop={row['stop_reason']}",
        )
        y -= 0.18 * inch
    if not rows:
        pdf.drawString(0.7 * inch, y, "No run folders found. Add runs under raw_runs/ or manifest runs[].")
    pdf.showPage()
    for row in rows:
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(0.55 * inch, height - 0.5 * inch, Path(row["run_dir"]).name)
        pdf.drawImage(row["image_path"], 0.35 * inch, 1.0 * inch, width=7.6 * inch, height=3.35 * inch, preserveAspectRatio=True)
        pdf.showPage()
    pdf.save()

    summary = {"campaign": nested(manifest, "campaign", "id") or "", "report_path": str(report_path), "runs": rows}
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a standard TMA optimization campaign report.")
    parser.add_argument("manifest", help="Path to campaign.yaml or campaign.json.")
    parser.add_argument("--json", action="store_true", help="Print summary JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = generate_report(args.manifest)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"Generated TMA report: {summary['report_path']}")
        print(f"Runs: {len(summary['runs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
