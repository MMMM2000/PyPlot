"""Build a canonical portable-report artifact from iso-stress simulations."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _number(row: dict[str, str], field: str) -> float:
    return float(row[field])


def _query_rows(connection: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cursor = connection.execute(sql)
    fields = [item[0] for item in cursor.description]
    return [dict(zip(fields, row, strict=True)) for row in cursor.fetchall()]


def build_artifact(input_dir: Path) -> dict[str, Any]:
    comparisons = _read_csv(input_dir / "policy_comparison.csv")
    aggregate = _read_csv(input_dir / "simulation_aggregate.csv")
    candidates = [row for row in comparisons if row["policy"] != "baseline"]
    prague = {
        row["policy"]: row
        for row in candidates
        if row["scenario"] == "prague_volatile"
    }
    completed = all(
        int(row["completed_runs"]) == int(row["runs"])
        for row in aggregate
    )
    safety_stops = sum(int(row["safety_stops"]) for row in aggregate)

    connection = sqlite3.connect(":memory:")
    fields = list(comparisons[0])
    connection.execute(
        f"CREATE TABLE policy_comparison ({', '.join(f'{field} TEXT' for field in fields)})"
    )
    placeholders = ", ".join("?" for _ in fields)
    connection.executemany(
        f"INSERT INTO policy_comparison VALUES ({placeholders})",
        ([row[field] for field in fields] for row in comparisons),
    )
    kpi_sql = """
SELECT
  ROUND(CAST(elapsed_change_pct AS REAL), 2) AS evidence_elapsed_change_pct,
  ROUND(CAST(hold_change_pct AS REAL), 2) AS evidence_hold_change_pct,
  ROUND(CAST(p95_error_change_pct AS REAL), 2) AS evidence_error_change_pct,
  0 AS accepted_candidates
FROM policy_comparison
WHERE scenario = 'prague_volatile' AND policy = 'evidence'
""".strip()
    prague_sql = """
SELECT policy, 'Elapsed time' AS metric,
       ROUND(CAST(elapsed_change_pct AS REAL), 4) AS change_pct
FROM policy_comparison
WHERE scenario = 'prague_volatile' AND policy <> 'baseline'
UNION ALL
SELECT policy, 'Hold time' AS metric,
       ROUND(CAST(hold_change_pct AS REAL), 4) AS change_pct
FROM policy_comparison
WHERE scenario = 'prague_volatile' AND policy <> 'baseline'
""".strip()
    stress_sql = """
SELECT REPLACE(scenario, '_', ' ') AS scenario,
       REPLACE(policy, '_', ' ') AS policy,
       ROUND(CAST(p95_error_change_pct AS REAL), 4) AS p95_error_change_pct
FROM policy_comparison
WHERE policy <> 'baseline'
""".strip()
    comparison_sql = """
SELECT REPLACE(scenario, '_', ' ') AS scenario,
       REPLACE(policy, '_', ' ') AS policy,
       ROUND(CAST(elapsed_change_pct AS REAL), 2) AS elapsed_change_pct,
       ROUND(CAST(hold_change_pct AS REAL), 2) AS hold_change_pct,
       ROUND(CAST(p95_error_change_pct AS REAL), 2) AS p95_error_change_pct,
       ROUND(CAST(time_outside_pause_s_median AS REAL), 2) AS time_outside_pause_s
FROM policy_comparison
WHERE policy <> 'baseline'
""".strip()
    kpis = _query_rows(connection, kpi_sql)
    prague_changes = _query_rows(connection, prague_sql)
    stress_changes = _query_rows(connection, stress_sql)
    comparison_table = _query_rows(connection, comparison_sql)
    connection.close()
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    def report_source(source_id: str, label: str, sql: str) -> dict[str, Any]:
        return {
        "id": source_id,
        "label": label,
        "query": {
            "engine": "SQLite",
            "language": "sql",
            "sql": sql,
            "executed_at": generated_at,
            "description": "Shapes reviewed policy-comparison rows for the report visualization.",
            "tables_used": ["policy_comparison"],
            "filters": [
                "No hardware I/O",
                "Requested current rate capped at 0.4 mA/s",
                "Same scenario and seed for every policy comparison",
            ],
            "metric_definitions": [
                "Elapsed and hold changes are median percent changes versus the scenario baseline.",
                "Stress noninferiority is p95 absolute true stress error no more than 5 percent above baseline.",
                "A viable candidate also requires no increase in time outside the active pause band.",
            ],
        },
    }
    kpi_source = report_source("simulation-kpis", "Offline simulation KPI comparison", kpi_sql)
    prague_source = report_source("simulation-prague", "Prague-like policy comparison", prague_sql)
    stress_source = report_source("simulation-stress", "Stress-control scenario comparison", stress_sql)
    table_source = report_source("simulation-table", "Scenario-level policy comparison", comparison_sql)
    audit_source = {
        "id": "prague-audit",
        "label": "Finalized Prague run audit",
        "query": {
            "description": "Audit of Ni48Fe25Ga23Co4 1_7 iso-stress metadata, measurement, raw scale, and control trace.",
            "metric_definitions": [
                "Recorded wall time was 21514.7 s and current hold was 19022.0 s (88.4 percent).",
                "Recorded p95 measured absolute stress error was approximately 20.3 MPa.",
            ],
        },
    }

    manifest = {
        "version": 1,
        "surface": "report",
        "title": "TMA iso-stress sweep policy simulation",
        "description": "Offline comparison of current hold/resume control and proposed speed policies.",
        "generatedAt": generated_at,
        "sources": [kpi_source, prague_source, stress_source, table_source, audit_source],
        "cards": [
            {
                "id": "prague-speed",
                "description": "Evidence-only median elapsed-time change on the Prague-like model.",
                "dataset": "kpis",
                "sourceId": "simulation-kpis",
                "metrics": [{"label": "Prague elapsed change (%)", "field": "evidence_elapsed_change_pct", "format": "number", "signed": True}],
            },
            {
                "id": "prague-hold",
                "description": "Evidence-only median hold-time change on the Prague-like model.",
                "dataset": "kpis",
                "sourceId": "simulation-kpis",
                "metrics": [{"label": "Prague hold change (%)", "field": "evidence_hold_change_pct", "format": "number", "signed": True}],
            },
            {
                "id": "prague-error",
                "description": "Evidence-only p95 true stress-error change on the Prague-like model.",
                "dataset": "kpis",
                "sourceId": "simulation-kpis",
                "metrics": [{"label": "Prague p95 error change (%)", "field": "evidence_error_change_pct", "format": "number", "signed": True}],
            },
            {
                "id": "accepted",
                "description": "Candidates satisfying every pre-registered acceptance gate.",
                "dataset": "kpis",
                "sourceId": "simulation-kpis",
                "metrics": [{"label": "Accepted candidates", "field": "accepted_candidates", "format": "number"}],
            },
        ],
        "charts": [
            {
                "id": "prague-change-chart",
                "title": "Prague-like elapsed and hold time change",
                "subtitle": "Median change versus current baseline; lower is faster.",
                "type": "bar",
                "dataset": "prague_changes",
                "sourceId": "simulation-prague",
                "encodings": {
                    "x": {"field": "policy", "type": "nominal", "label": "Policy"},
                    "y": {"field": "change_pct", "type": "quantitative", "label": "Change", "unit": "%"},
                    "color": {"field": "metric", "type": "nominal", "label": "Metric"},
                    "tooltip": [
                        {"field": "policy", "type": "nominal", "label": "Policy"},
                        {"field": "metric", "type": "nominal", "label": "Metric"},
                        {"field": "change_pct", "type": "quantitative", "label": "Change", "unit": "%"},
                    ],
                },
                "yAxisTitle": "Change vs baseline (%)",
                "referenceLines": [{"value": 0, "label": "Baseline"}],
            },
            {
                "id": "stress-gate-chart",
                "title": "P95 true stress-error change by scenario",
                "subtitle": "The noninferiority limit is +5% versus baseline.",
                "type": "bar",
                "dataset": "stress_changes",
                "sourceId": "simulation-stress",
                "encodings": {
                    "x": {"field": "scenario", "type": "nominal", "label": "Scenario"},
                    "y": {"field": "p95_error_change_pct", "type": "quantitative", "label": "P95 error change", "unit": "%"},
                    "color": {"field": "policy", "type": "nominal", "label": "Policy"},
                    "tooltip": [
                        {"field": "scenario", "type": "nominal", "label": "Scenario"},
                        {"field": "policy", "type": "nominal", "label": "Policy"},
                        {"field": "p95_error_change_pct", "type": "quantitative", "label": "P95 error change", "unit": "%"},
                    ],
                },
                "yAxisTitle": "Change vs baseline (%)",
                "referenceLines": [{"value": 5, "label": "Noninferiority limit"}, {"value": 0, "label": "Baseline"}],
            },
        ],
        "tables": [
            {
                "id": "comparison-table",
                "title": "Scenario-level policy comparison",
                "subtitle": "Medians across 12 deterministic seeds; changes are relative to each scenario baseline.",
                "dataset": "comparison_table",
                "sourceId": "simulation-table",
                "density": "compact",
                "columns": [
                    {"field": "scenario", "label": "Scenario", "type": "text"},
                    {"field": "policy", "label": "Policy", "type": "text"},
                    {"field": "elapsed_change_pct", "label": "Elapsed change", "type": "number", "unit": "%", "movement": True},
                    {"field": "hold_change_pct", "label": "Hold change", "type": "number", "unit": "%", "movement": True},
                    {"field": "p95_error_change_pct", "label": "P95 error change", "type": "number", "unit": "%", "movement": True},
                    {"field": "time_outside_pause_s", "label": "Outside pause band", "type": "number", "unit": "s"},
                ],
            }
        ],
        "blocks": [
            {"id": "title", "type": "markdown", "body": "# TMA iso-stress sweep policy simulation\n\nOffline closed-loop comparison. No controller or hardware behavior was changed."},
            {"id": "technical-summary", "type": "markdown", "body": "## Technical summary\n\nNo simulated candidate passed all pre-registered gates. Evidence-only provides the only material Prague-like speed gain, but fails stress noninferiority in multiple hold-outs. Evidence plus probation preserves more stress control but misses the material speed target. The full proposed stack improves stress error in most scenarios but is slower than baseline. Do not implement these controller policies yet."},
            {"id": "kpi-strip", "type": "metric-strip", "cardIds": ["prague-speed", "prague-hold", "prague-error", "accepted"]},
            {"id": "prague-finding", "type": "markdown", "sourceId": "simulation-prague", "body": "## Key findings\n\nEvidence-only reduced Prague-like median elapsed time by 23.3% and hold time by 22.3%, with p95 true stress error 2.5% above baseline. It missed the 25% hold-time target. Evidence plus probation reduced elapsed time by only 7.5%. The full stack increased elapsed time by 2.2%."},
            {"id": "prague-chart-block", "type": "chart", "chartId": "prague-change-chart"},
            {"id": "holdout-finding", "type": "markdown", "sourceId": "simulation-stress", "body": "## Stress-control hold-outs\n\nEvidence-only exceeded the +5% p95 error limit in calm (+6.8%), coherent-transformation (+9.6%), and sparse-feedback (+14.2%) scenarios. A stricter coherent-motion evidence screen tested 18 threshold/confirmation combinations; none satisfied both the stress-error and out-of-band-time gates. This rejects both the aggressive and stricter evidence variants."},
            {"id": "stress-chart-block", "type": "chart", "chartId": "stress-gate-chart"},
            {"id": "scope", "type": "markdown", "body": "## Scope, data, and metrics\n\nThe matrix contains 240 runs: four policies, five scenarios, and 12 paired deterministic seeds. All runs completed without a stress-safety stop and no policy exceeded the requested 0.4 mA/s recipe rate. Primary metrics are elapsed time, hold time, p95 absolute true stress error, and time outside the active pause band."},
            {"id": "method", "type": "markdown", "body": "## Methodology and model specification\n\nThe software-only hybrid model closes the loop among current, transformation strain, motor correction, correlated scale noise, and hold/resume decisions. Baseline uses the current policy shape: a 1.8 s robust fast signal, confirmed hold entry, continuous in-band resume confirmation, and the existing upward post-hold throttle. Candidate ablations add bounded resume evidence, explicit bidirectional probation, and a one-sided noise/trend rate limiter. Sparse feedback uses actual interval duration in evidence, hold, probation, current, motor, and out-of-band accounting."},
            {"id": "audit-calibration", "type": "markdown", "sourceId": "prague-audit", "body": "## Prague calibration context\n\nThe finalized run spent 19,022.0 s, or 88.4% of wall time, in current hold. The simulator was calibrated to a similar p95 measured absolute stress error, but its baseline hold fraction is about 69.7%, so it understates the real hold bottleneck and is not a digital twin."},
            {"id": "detail-table-block", "type": "table", "tableId": "comparison-table"},
            {"id": "limitations", "type": "markdown", "body": "## Limitations and robustness\n\nThis is causal closed-loop policy-shape evidence, not exact reproduction of the recorded controller or specimen. The model does not yet identify local plant parameters loop-by-loop from raw Prague artifacts, reconstruct the recorded baseline event-for-event, or validate scientific-fidelity metrics against an independent clean run. Simulated true stress is available for noninferiority checks; live data only provides measured stress. These limitations prevent a production recommendation even if a future candidate clears the synthetic gates."},
            {"id": "next-steps", "type": "markdown", "body": "## Next steps\n\n1. Reconstruct the recorded baseline state machine and prove event-level parity on the finalized trace.\n2. Fit loop-local current, motor, residual, and cadence models from eligible Prague segments.\n3. Redesign evidence to distinguish stationary noise from coherent transformation without a fixed extra confirmation burden.\n4. Re-run the ablation matrix with leave-one-loop-out and clean-run hold-outs.\n5. Consider controller implementation only after one candidate passes every safety, stress, scientific-fidelity, and material-speed gate."},
            {"id": "questions", "type": "markdown", "body": "## Further questions\n\nCan independent resume evidence be tied to robust target-band occupancy rather than elapsed confirmation? Does direction-specific transformation response justify asymmetric probation? Which clean run should serve as the external hold-out after quality screening?"},
        ],
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready" if completed and safety_stops == 0 else "partial",
            "datasets": {
                "kpis": kpis,
                "prague_changes": prague_changes,
                "stress_changes": stress_changes,
                "comparison_table": comparison_table,
            },
        },
        "sources": [kpi_source, prague_source, stress_source, table_source, audit_source],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = build_artifact(args.input_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
