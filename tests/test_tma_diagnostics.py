from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from data_logging.mini_dma_logger import tma_diagnostics


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _finished_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "finished_run"
    logs = run_dir / "final_logs"
    logs.mkdir(parents=True)
    metadata = {
        "created_utc": "2026-07-16T08:00:00.000Z",
        "finished_utc": "2026-07-16T08:00:03.000Z",
        "session_state": "finished",
        "session_identity": "session-1",
        "sample_identity": {"sample_name": "NiFeGa 1/1", "microwire": "1/1"},
        "logging": {
            "measurement_csv": "final_logs/measured.csv",
            "control_trace_csv": "final_logs/commands.csv",
            "raw_scale_sidecar": "final_logs/balance.csv",
            "ui_telemetry_csv": "final_logs/ui.csv",
            "run_log_txt": "final_logs/session.log",
            "ir_temperature_sidecar": "final_logs/not_recorded_ir.csv",
            "log_interval_ms": 1000,
            "raw_scale_sample_count": 3,
            "raw_scale_session_rate_hz": 4.0,
            "raw_scale_max_gap_s": 0.5,
            "ui_telemetry_sample_count": 3,
            "run_log_complete": True,
            "run_log_incomplete_lines": 0,
            "run_log_incomplete_reason": None,
            "sensor_sidecars": {
                "raw_scale": {
                    "status": "complete",
                    "complete": True,
                    "accepted_rows": 3,
                    "written_rows": 3,
                    "lost_rows": 0,
                    "pending_rows": 0,
                    "reason": None,
                }
            },
        },
        "scale": {
            "profile": "kern_kcp",
            "port": "COM7",
            "baud": 9600,
            "poll_interval_ms": 250,
            "readability_g": 0.01,
        },
        "control": {
            "control_interval_ms": 250,
            "ui_heartbeat_interval_ms": 500,
            "graph_refresh_interval_ms": 1000,
            "force_control_profile": "kosice_adaptive",
        },
        "stop": {
            "reason": "recipe_completed",
            "category": "completed",
            "detail": "Synthetic completion.",
        },
        "source_control": {
            "capture_state": "complete",
            "branch": "codex/tma-dual-scale-control",
            "commit": "abc123",
            "dirty_state": "clean",
        },
        "control_logic": {"name": "mini_dma_control", "version": "test"},
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    _write_csv(
        logs / "measured.csv",
        ["elapsed_s", "timestamp_utc", "recipe_mode", "automation_phase"],
        [
            {"elapsed_s": 0, "timestamp_utc": "2026-07-16T08:00:00.000Z", "recipe_mode": "current_sweep", "automation_phase": "setup"},
            {"elapsed_s": 1, "timestamp_utc": "2026-07-16T08:00:01.000Z", "recipe_mode": "current_sweep", "automation_phase": "hold"},
            {"elapsed_s": 3, "timestamp_utc": "2026-07-16T08:00:03.000Z", "recipe_mode": "current_sweep", "automation_phase": "done"},
        ],
    )
    _write_csv(
        logs / "commands.csv",
        [
            "elapsed_s",
            "timestamp_utc",
            "automation_phase",
            "decision",
            "result",
            "reason",
            "correction_mm",
            "motor_step_mm",
            "target_mm",
        ],
        [
            {"elapsed_s": 0.5, "timestamp_utc": "2026-07-16T08:00:00.500Z", "automation_phase": "seek", "decision": "move", "result": "issued", "reason": "below_target", "correction_mm": 0.01},
            {"elapsed_s": 1.0, "timestamp_utc": "2026-07-16T08:00:01.000Z", "automation_phase": "hold", "decision": "hold", "result": "in_band", "reason": "stable", "correction_mm": 0},
        ],
    )
    _write_csv(
        logs / "balance.csv",
        ["elapsed_s", "timestamp_utc", "sample_index", "host_interval_ms", "raw_load_g"],
        [
            {"elapsed_s": 0.0, "timestamp_utc": "2026-07-16T08:00:00.000Z", "sample_index": 1, "host_interval_ms": 250, "raw_load_g": 10.0},
            {"elapsed_s": 0.25, "timestamp_utc": "2026-07-16T08:00:00.250Z", "sample_index": 2, "host_interval_ms": 250, "raw_load_g": 10.1},
            {"elapsed_s": 0.75, "timestamp_utc": "2026-07-16T08:00:00.750Z", "sample_index": 3, "host_interval_ms": 500, "raw_load_g": 10.2},
        ],
    )
    _write_csv(
        logs / "ui.csv",
        ["elapsed_s", "timestamp_utc", "actual_interval_ms", "handler_duration_ms"],
        [
            {"elapsed_s": 0.0, "timestamp_utc": "2026-07-16T08:00:00.000Z", "actual_interval_ms": 500, "handler_duration_ms": 2},
            {"elapsed_s": 0.5, "timestamp_utc": "2026-07-16T08:00:00.500Z", "actual_interval_ms": 500, "handler_duration_ms": 3},
            {"elapsed_s": 1.0, "timestamp_utc": "2026-07-16T08:00:01.000Z", "actual_interval_ms": 500, "handler_duration_ms": 4},
        ],
    )
    (logs / "session.log").write_text("run complete\n", encoding="ascii")
    return run_dir


def test_summary_uses_final_logging_paths_and_async_outcomes(tmp_path: Path) -> None:
    run_dir = _finished_run(tmp_path)

    summary = tma_diagnostics.build_diagnostic_summary(run_dir)

    assert summary["diagnostic_state"] == {
        "mode": "final",
        "session_state": "finished",
        "finished_utc": "2026-07-16T08:00:03.000Z",
        "lifecycle_finished": True,
        "pending_finalization": [],
    }
    assert summary["measurement"]["row_count"] == 3
    assert summary["control"]["decisions"] == {"hold": 1, "move": 1}
    assert summary["control"]["command_row_count"] == 1
    assert summary["raw_scale"]["host_interval_ms"]["max"] == 500.0
    assert summary["ui_timing"]["handler_duration_ms"]["p50"] == 3.0
    assert summary["source_control"]["branch"] == "codex/tma-dual-scale-control"
    assert summary["scale"]["profile"] == "kern_kcp"
    assert summary["control_settings"]["force_control_profile"] == "kosice_adaptive"
    assert summary["logging_finalization"]["sensor_sidecars"]["raw_scale"]["complete"] is True
    assert summary["source_files"]["measurement"]["path"] == "final_logs/measured.csv"
    assert summary["source_files"]["ir_temperature"]["available"] is False
    assert summary["run_timeline"]["streams"]["control"]["first_utc_offset_from_run_start_s"] == 0.5


def test_missing_optional_sidecars_are_reported_not_required(tmp_path: Path) -> None:
    run_dir = _finished_run(tmp_path)
    (run_dir / "final_logs" / "session.log").unlink()

    summary = tma_diagnostics.build_diagnostic_summary(run_dir)

    assert summary["source_files"]["run_log"]["available"] is False
    assert summary["source_files"]["ir_temperature"]["available"] is False
    assert summary["source_files"]["setup"]["available"] is False


def test_active_and_pending_runs_require_explicit_snapshot_mode(tmp_path: Path) -> None:
    run_dir = _finished_run(tmp_path)
    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["session_state"] = "running"
    metadata.pop("finished_utc")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(tma_diagnostics.DiagnosticError, match="active or incomplete"):
        tma_diagnostics.build_diagnostic_summary(run_dir)

    snapshot = tma_diagnostics.build_diagnostic_summary(run_dir, snapshot=True)
    assert snapshot["diagnostic_state"]["mode"] == "snapshot"
    assert snapshot["diagnostic_state"]["lifecycle_finished"] is False

    metadata["session_state"] = "finished"
    metadata["finished_utc"] = "2026-07-16T08:00:03.000Z"
    metadata["source_control"]["capture_state"] = "pending"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(tma_diagnostics.DiagnosticError, match="finalization is still pending"):
        tma_diagnostics.build_diagnostic_summary(run_dir)

    metadata["source_control"]["capture_state"] = "complete"
    metadata["logging"]["sensor_sidecars"]["raw_scale"].update(
        status="incomplete",
        complete=False,
        pending_rows=0,
        reason="close_timeout",
    )
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(tma_diagnostics.DiagnosticError, match="sidecar reconciliation"):
        tma_diagnostics.build_diagnostic_summary(run_dir)
    snapshot = tma_diagnostics.build_diagnostic_summary(run_dir, snapshot=True)
    assert snapshot["diagnostic_state"]["pending_finalization"] == [
        "raw_scale sidecar reconciliation"
    ]


def test_bundle_is_valid_deterministic_and_does_not_modify_run(tmp_path: Path) -> None:
    run_dir = _finished_run(tmp_path)
    before = {
        path.relative_to(run_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in run_dir.rglob("*")
        if path.is_file()
    }
    output_dir = tmp_path / "remote_bundle"

    bundle_path = tma_diagnostics.write_diagnostic_bundle(run_dir, output_dir=output_dir)
    first_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    bundle_path = tma_diagnostics.write_diagnostic_bundle(run_dir, output_dir=output_dir)
    second_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()

    assert first_hash == second_hash
    assert zipfile.is_zipfile(bundle_path)
    with zipfile.ZipFile(bundle_path) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert names == sorted(names)
        assert tma_diagnostics.SUMMARY_JSON in names
        assert tma_diagnostics.SUMMARY_MARKDOWN in names
        assert "run/final_logs/measured.csv" in names
        assert "run/final_logs/not_recorded_ir.csv" not in names
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
    after = {
        path.relative_to(run_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in run_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert output_dir not in run_dir.parents


def test_correction_sidecar_preserves_source_and_final_metadata_hashes(tmp_path: Path) -> None:
    run_dir = _finished_run(tmp_path)
    output_dir = tmp_path / "correction"

    path = tma_diagnostics.write_identity_correction(
        run_dir,
        corrected={"microwire": "1/2"},
        reason="Notebook transcription correction",
        operator="Test operator",
        output_dir=output_dir,
        timestamp_utc="2026-07-16T09:00:00Z",
    )

    payload = json.loads(path.read_text(encoding="ascii"))
    metadata_bytes = (run_dir / "metadata.json").read_bytes()
    assert payload["final_metadata_sha256"] == hashlib.sha256(metadata_bytes).hexdigest()
    assert payload["source_sha256"]["metadata"] == payload["final_metadata_sha256"]
    assert payload["source_sha256"]["measurement"] == hashlib.sha256(
        (run_dir / "final_logs" / "measured.csv").read_bytes()
    ).hexdigest()
    assert payload["effective_identity"]["microwire"] == "1/2"
    assert not (run_dir / tma_diagnostics.CORRECTION_JSON).exists()
