from __future__ import annotations

import argparse
import base64
import json
import os
import pickle
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6 import QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import launcher as launcher_module
from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import TabDescriptor
from plotting.shared.toolkit import theme_manager


def _write_hysteresis_source(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "150 6.2e-10",
                "75 6.1e-10",
                "0 -6.0e-10",
                "-75 -6.1e-10",
                "-150 -6.2e-10",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_microwire_word_graph_sections_record_reference_and_origin_status() -> None:
    from microwire_data_builder.core import OriginArtifact

    source_only = {
        "Manual stress/strain graphs": [
            "20mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
            "30mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
        ],
    }
    with_origin = {
        "Manual stress/strain graphs": ["30mA"],
        "Manual stress/strain graphs (Origin)": "shape_memory.oggu",
    }

    source_section = launcher_module._microwire_word_graph_sections_for_row(source_only)[
        "Manual stress/strain"
    ]
    assert source_section["included"] is True
    assert source_section["reason"] == "reference_content"
    assert source_section["graphs"] == []
    assert source_section["references"] == [
        "20mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
        "30mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
    ]

    missing_section = launcher_module._microwire_word_graph_sections_for_row(with_origin)[
        "Manual stress/strain"
    ]
    assert missing_section["included"] is True
    assert missing_section["reason"] == "reference_content"
    assert missing_section["graphs"] == []
    assert missing_section["references"] == ["30mA"]
    assert missing_section["missing_origin_descriptors"] == ["shape_memory.oggu"]

    artifact_section = launcher_module._microwire_word_graph_sections_for_row(
        with_origin,
        {
            "shape_memory.oggu": OriginArtifact(
                descriptor="shape_memory.oggu",
                object_path=Path("shape_memory.oggu"),
                display_text="shape memory",
            )
        },
    )["Manual stress/strain"]
    assert artifact_section["included"] is True
    assert artifact_section["reason"] == "accepted_origin_object"
    assert artifact_section["graphs"] == ["shape_memory.oggu"]
    assert artifact_section["references"] == ["30mA"]


def test_microwire_word_graph_sections_accept_legacy_shape_memory_columns() -> None:
    from microwire_data_builder.core import OriginArtifact

    row = {
        "Shape memory stress/strain graphs": ["30mA"],
        "Shape memory stress/strain graphs (Origin)": "legacy_shape_memory.oggu",
    }

    section = launcher_module._microwire_word_graph_sections_for_row(
        row,
        {
            "legacy_shape_memory.oggu": OriginArtifact(
                descriptor="legacy_shape_memory.oggu",
                object_path=Path("legacy_shape_memory.oggu"),
                display_text="legacy shape memory",
            )
        },
    )["Manual stress/strain"]
    assert section["included"] is True
    assert section["reason"] == "accepted_origin_object"
    assert section["graphs"] == ["legacy_shape_memory.oggu"]
    assert section["references"] == ["30mA"]


def _wait_for_registry(window: launcher_module.MasterLauncher, app: QtWidgets.QApplication) -> None:
    for _ in range(40):
        app.processEvents()
        if getattr(window, "_registry_loaded", False):
            return
    raise AssertionError("Launcher registry did not finish loading in time.")


@pytest.mark.parametrize(
    ("name", "module", "resource_tag"),
    [
        (
            "Current Annealing Logger",
            "data_logging.current_annealing_logger.current_annealing_logger",
            "current_annealing",
        ),
        (
            "AC Susceptibility Logger",
            "data_logging.ac_susceptibility_logger.ac_susceptibility_logger",
            "ac_susceptibility",
        ),
        (
            "Mini DMA Logger",
            "data_logging.mini_dma_logger.mini_dma_logger",
            "mini_dma",
        ),
    ],
)
def test_hardware_experiment_loggers_launch_in_child_process(
    name: str,
    module: str,
    resource_tag: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    monkeypatch.setattr(
        launcher_module,
        "launch_experiment_process",
        lambda spec: launched.append(spec),
    )

    result = launcher_module.LOGGERS[name]()

    assert result is None
    assert launched
    spec = launched[0]
    assert getattr(spec, "display_name") == name
    assert getattr(spec, "module") == module
    assert getattr(spec, "resource_tag") == resource_tag


def test_launcher_plotting_list_refreshes_using_last_opened_order(
    monkeypatch,
) -> None:
    app = _ensure_app()
    fake_registry = {
        "loggers": {},
        "plotters": {
            "ZZ Plot A": lambda: None,
            "ZZ Plot B": lambda: None,
        },
        "emulators": {},
    }
    monkeypatch.setattr(launcher_module, "_build_registry", lambda: fake_registry)

    window = launcher_module.MasterLauncher()
    try:
        _wait_for_registry(window, app)
        assert window._sort_modes.get("plotters") == "last_used"  # noqa: SLF001 - test hook

        now = time.time()
        window._settings.setValue("launcher_last_order/seq", 200)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot A", 100)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot B", 200)
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot A", now - 100.0)  # noqa: SLF001 - test hook
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot B", now)  # noqa: SLF001 - test hook
        window._refresh_list("plotters")  # noqa: SLF001 - test hook
        app.processEvents()
        assert window.plot_list.item(0).text() == "ZZ Plot B"

        # Simulate tool usage while the launcher is hidden, then restore it:
        # _restore_launcher should refresh the visible order from settings.
        window._settings.setValue("launcher_last_order/seq", 250)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot A", 250)
        window._settings.setValue("launcher_last_order/plotters/ZZ Plot B", 200)
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot A", now + 200.0)  # noqa: SLF001 - test hook
        window._settings.setValue("launcher_last_used/plotters/ZZ Plot B", now)  # noqa: SLF001 - test hook
        window.hide()
        window._restore_launcher()  # noqa: SLF001 - test hook
        app.processEvents()
        assert window.plot_list.item(0).text() == "ZZ Plot A"
    finally:
        window.close()
        app.processEvents()


def test_graph_option_defaults_apply_figure_size_to_new_plot_tabs() -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window._graph_option_defaults_global = window._clean_graph_option_payload(  # noqa: SLF001 - test hook
            {
                "figure_width": 8.4,
                "figure_height": 5.6,
                "figure_width_auto": False,
                "figure_height_auto": False,
            }
        )

        fig = Figure(figsize=(3.0, 3.0))
        axes = fig.add_subplot(111)
        axes.set_title("Example")
        axes.set_xlabel("X")
        axes.set_ylabel("Y")
        canvas = FigureCanvas(fig)

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)

        descriptor = TabDescriptor(
            kind="unit_test",
            title="Example",
            root_label="Example Plot",
            x_label="X",
            y_label="Y",
            canvas=canvas,
            axes=axes,
            lines={},
            metadata={"plugin": "Unit Test Plugin"},
        )
        index = window.tab_widget.addTab(tab, "Example Plot")
        window.tab_widget.setCurrentIndex(index)
        window._register_plot_tab(tab, canvas, axes, descriptor)  # noqa: SLF001 - test hook

        width_in, height_in = fig.get_size_inches()
        assert width_in == pytest.approx(8.4, rel=1e-3)
        assert height_in == pytest.approx(5.6, rel=1e-3)
    finally:
        window.close()
        app.processEvents()


def test_launcher_detects_pyplot_automation_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(  # noqa: SLF001 - internal parser
        [
            "--pyplot-plugin",
            "Hysteresis Loops",
            "--pyplot-import",
            "sample_data/hysteresis_loops",
            "--pyplot-plot",
        ]
    )
    assert launcher_module._is_pyplot_automation_requested(args) is True  # noqa: SLF001


def test_launcher_detects_pyplot_session_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(  # noqa: SLF001 - internal parser
        [
            "--pyplot-session-send",
            "--pyplot-session-id",
            "example-session",
            "--pyplot-session-command-json",
            '{"action":"state"}',
        ]
    )
    assert launcher_module._is_pyplot_session_requested(args) is True  # noqa: SLF001


def test_pyplot_session_command_payload_includes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_payloads: list[dict[str, object]] = []
    socket_timeouts: list[float] = []

    class FakeSocket:
        def __init__(self) -> None:
            self._response_sent = False

        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def settimeout(self, timeout: float) -> None:
            socket_timeouts.append(timeout)

        def sendall(self, raw: bytes) -> None:
            sent_payloads.append(json.loads(raw.decode("utf-8")))

        def recv(self, _size: int) -> bytes:
            if self._response_sent:
                return b""
            self._response_sent = True
            return b'{"status":"ok"}\n'

    def fake_create_connection(address: tuple[str, int], timeout: float) -> FakeSocket:
        assert address == ("127.0.0.1", 4567)
        socket_timeouts.append(timeout)
        return FakeSocket()

    monkeypatch.setattr(
        launcher_module,
        "_get_session_record",
        lambda _session_id: {"host": "127.0.0.1", "port": 4567, "token": "secret"},
    )
    monkeypatch.setattr(launcher_module.socket, "create_connection", fake_create_connection)

    response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
        "session-id",
        {"action": "open_origin"},
        timeout_s=240.0,
    )

    assert response == {"status": "ok"}
    assert sent_payloads == [
        {
            "token": "secret",
            "command": {"action": "open_origin"},
            "timeout_s": 240.0,
        }
    ]
    assert socket_timeouts == [240.0, 240.0]


def test_launcher_detects_microwire_eda_cli_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--microwire-eda",
            "sample.pydpj",
            "--rows",
            "filtered",
            "--out",
            "artifacts/eda",
        ]
    )
    assert launcher_module._is_microwire_eda_requested(args) is True  # noqa: SLF001
    assert args.rows == "filtered"
    assert args.out == "artifacts/eda"
    assert args.microwire_eda_copy_project is True
    assert args.microwire_eda_force_project_rebuild is False


def test_launcher_detects_mini_dma_bench_plan_flag() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--mini-dma-bench-plan",
            "bench-plan.json",
        ]
    )
    assert launcher_module._is_mini_dma_bench_requested(args) is True  # noqa: SLF001
    assert args.mini_dma_bench_plan == "bench-plan.json"


def test_launcher_detects_microwire_word_report_cli_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--microwire-word-report",
            "Ni50Fe27Ga23_12_2.csv",
            "--microwire-word-sample",
            "Ni50Fe27Ga23 12/2",
            "--out",
            "artifacts/word-report",
        ]
    )

    assert launcher_module._is_microwire_word_report_requested(args) is True  # noqa: SLF001
    assert args.microwire_word_report == "Ni50Fe27Ga23_12_2.csv"
    assert args.microwire_word_sample == "Ni50Fe27Ga23 12/2"
    assert args.microwire_word_origin is True
    assert args.out == "artifacts/word-report"


def test_launcher_detects_microwire_word_job_flag() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--microwire-word-job",
            "jobs/word-export.json",
        ]
    )

    assert launcher_module._is_microwire_word_job_requested(args) is True  # noqa: SLF001
    assert args.microwire_word_job == "jobs/word-export.json"


def test_run_microwire_word_job_dry_run_writes_status_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "database.pydpj"
    source.write_text('{"sections": {}}', encoding="utf-8")
    job_path = tmp_path / "word-job.json"
    job_path.write_text(
        json.dumps(
            {
                "version": 1,
                "job_type": "microwire_word_export",
                "job_id": "dry_word",
                "source": str(source),
                "output_dir": str(tmp_path / "reports"),
                "sample": "Ni50Fe27Ga23 12/2",
                "include_origin": True,
                "force_project_rebuild": True,
                "graphs_only": True,
                "dry_run": True,
                "paths": {
                    "status": str(tmp_path / "status.json"),
                    "progress": str(tmp_path / "progress.json"),
                    "manifest": str(tmp_path / "manifest.json"),
                    "log": str(tmp_path / "job.log"),
                    "cancel": str(tmp_path / "cancel.requested"),
                },
            }
        ),
        encoding="utf-8-sig",
    )
    args = argparse.Namespace(microwire_word_job=str(job_path))

    exit_code = launcher_module._run_microwire_word_job_cli(args)  # noqa: SLF001

    assert exit_code == 0
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert status["state"] == "succeeded"
    assert status["dry_run"] is True
    assert progress["events"][-1]["event"] == "validated"
    assert manifest["job_type"] == "microwire_word_export"
    assert manifest["dry_run"] is True
    assert "--microwire-word-report" in manifest["equivalent_command"]
    assert "--microwire-word-graphs-only" in manifest["equivalent_command"]
    output = capsys.readouterr().out
    assert "dry_run=true" in output
    assert "manifest=" in output


def test_run_microwire_word_job_honors_pre_start_cancel(tmp_path: Path) -> None:
    source = tmp_path / "database.pydpj"
    source.write_text('{"sections": {}}', encoding="utf-8")
    cancel = tmp_path / "cancel.requested"
    cancel.write_text("stop", encoding="utf-8")
    job_path = tmp_path / "word-job.json"
    job_path.write_text(
        json.dumps(
            {
                "version": 1,
                "job_type": "microwire_word_export",
                "job_id": "cancelled_word",
                "source": str(source),
                "dry_run": True,
                "paths": {
                    "status": str(tmp_path / "status.json"),
                    "progress": str(tmp_path / "progress.json"),
                    "manifest": str(tmp_path / "manifest.json"),
                    "log": str(tmp_path / "job.log"),
                    "cancel": str(cancel),
                },
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(microwire_word_job=str(job_path))

    exit_code = launcher_module._run_microwire_word_job_cli(args)  # noqa: SLF001

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 130
    assert status["state"] == "cancelled"
    assert manifest["state"] == "cancelled"


def test_run_microwire_word_report_cli_accepts_rvst_csv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "Ni50Fe27Ga23_12_2.csv"
    source.write_text(
        "\n".join(
            [
                "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm",
                "2026-02-06T08:22:38;0.1;-100;-40.5;43.2903",
                "2026-02-06T08:22:48;10.1;-90;-39.0;43.2882",
                "2026-02-06T08:22:58;20.1;-80;-37.5;43.2700",
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "reports"
    args = argparse.Namespace(
        microwire_word_report=str(source),
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_force_project_rebuild=False,
        microwire_word_origin=False,
        out=str(output_dir),
    )

    exit_code = launcher_module._run_microwire_word_report_cli(args)  # noqa: SLF001 - internal automation hook

    report_path = output_dir / "Ni50Fe27Ga23_12-2.docx"
    assert exit_code == 0
    assert report_path.exists()
    output = capsys.readouterr().out
    assert "reports=1" in output
    assert str(report_path) in output


def test_microwire_word_report_project_merges_section_rows_and_rvst(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = tmp_path / "copied" / "microwire_project_copy.pydpj"
    project_path.parent.mkdir()
    annealing_path = data_root / "current annealing" / "Ni50Fe27Ga23 12_2 s1 1000mA.txt"
    annealing_path.parent.mkdir(parents=True)
    annealing_path.write_text("0.1 40 1\n0.2 41 1\n", encoding="utf-8")
    rvt_path = data_root / "RvsT" / "RvsT" / "Ni50Fe27Ga23_12_2.csv"
    rvt_path.parent.mkdir(parents=True)
    rvt_path.write_text(
        "\n".join(
            [
                "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm",
                "2026-02-06T08:22:38;0.1;-100;-40.5;43.2903",
                "2026-02-06T08:22:48;10.1;-90;-39.0;43.2882",
            ]
        ),
        encoding="utf-8",
    )
    mini_dma_path = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 test_run32" / "measurement.csv"
    mini_dma_path.parent.mkdir(parents=True)
    mini_dma_path.write_text(
        "\n".join(
            [
                "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,resistance_ohm,current_measured_mA",
                "0.1,current,50,1,0.0,100.0,1.0",
                "0.2,current,50,1,0.1,101.0,2.0",
            ]
        ),
        encoding="utf-8",
    )
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "assemble": {"rows": [], "columns": []},
                    "annealing": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "_sources": [str(annealing_path)],
                            }
                        ]
                    },
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "d (µm)": 19.1,
                                "D (µm)": 58.6,
                                "d/D": 0.326,
                                "_core_image": str(tmp_path / "core.jpg"),
                            }
                        ]
                    },
                    "vsm_temperature_scan": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "VSM temperature scan graphs": ["scan-a", "scan-b"],
                            }
                        ]
                    },
                    "strain": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Legacy strain": 1.37,
                            }
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_origin=False,
    )

    frame, origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        args,
        tmp_path / "reports",
    )

    copied_projects = list((tmp_path / "reports" / "_project_copy").glob("*.pydpj"))
    assert len(copied_projects) == 1
    assert copied_projects[0] != project_path
    assert copied_projects[0].read_text(encoding="utf-8") == project_path.read_text(encoding="utf-8")
    assert origin_artifacts == {}
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["Composition"] == "Ni50Fe27Ga23"
    assert row["Microwire"] == "12/2"
    assert row["d (µm)"] == 19.1
    assert row["D (µm)"] == 58.6
    assert row["d/D"] == 0.326
    assert row["Legacy strain"] == 1.37
    assert row["Figure — 1000 mA"] == annealing_path.name
    assert row["VSM temperature scan graphs"] == ["scan-a", "scan-b"]
    assert row["R vs T graphs"] == [rvt_path.name]
    assert row["R vs T points"] == 2
    assert row["R vs T temperature range (deg C)"] == "-40.5 to -39"
    assert row["Mini DMA graphs"] == mini_dma_path.parent.name


def test_microwire_word_report_project_replaces_stale_mini_dma_sources_with_active_runs(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = data_root / "microwire_project_copy.pydpj"
    project_path.parent.mkdir(parents=True)
    active_a = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 heat shield iso-stress_run03"
    active_b = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 baseline-50mpa-01"
    archived = data_root / "mini DMA" / "archive" / "Ni50Fe27Ga23 12_2 old_run01"
    for path in (active_a, active_b, archived):
        path.mkdir(parents=True)
        (path / "measurement.csv").write_text(
            "\n".join(
                [
                    "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,resistance_ohm,current_measured_mA",
                    "0.1,current,50,1,0.0,100.0,1.0",
                    "0.2,current,50,1,0.1,101.0,2.0",
                ]
            ),
            encoding="utf-8",
        )
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "mini_dma": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Mini DMA graphs": ["stale archived run"],
                                "_sources": [str(archived)],
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_origin=False,
    )

    frame, _origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        args,
        tmp_path / "reports",
    )

    assert len(frame) == 1
    row = frame.iloc[0]
    assert set(row["_word_mini_dma_sources"]) == {str(active_a), str(active_b)}
    mini_dma_graphs = row["Mini DMA graphs"]
    assert set(mini_dma_graphs) == {active_a.name, active_b.name}
    assert archived.name not in mini_dma_graphs
    assert "stale archived run" not in mini_dma_graphs


def test_microwire_word_report_project_blocks_stale_mini_dma_when_newest_active_run_unfinished(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = data_root / "microwire_project_copy.pydpj"
    project_path.parent.mkdir(parents=True)
    old_finished = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 old_run01"
    newest_running = data_root / "mini DMA" / "Ni50Fe27Ga23 12_2 active_run02"
    for path, metadata in (
        (
            old_finished,
            {
                "sample_name": "Ni50Fe27Ga23 12/2",
                "created_utc": "2026-06-01 09:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 09:20:00",
            },
        ),
        (
            newest_running,
            {
                "sample_name": "Ni50Fe27Ga23 12/2",
                "created_utc": "2026-06-01 10:00:00",
                "session_state": "running",
            },
        ),
    ):
        path.mkdir(parents=True)
        (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (path / "measurement.csv").write_text(
            "\n".join(
                [
                    "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,resistance_ohm,current_measured_mA",
                    "0.1,current,50,1,0.0,100.0,1.0",
                    "0.2,current,50,1,0.1,101.0,2.0",
                ]
            ),
            encoding="utf-8",
        )
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "mini_dma": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Mini DMA graphs": ["stale old run"],
                                "_sources": [str(old_finished)],
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    frame, _origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        argparse.Namespace(microwire_word_sample="Ni50Fe27Ga23 12/2", microwire_word_origin=False),
        tmp_path / "reports",
    )

    assert len(frame) == 1
    row = frame.iloc[0]
    assert not launcher_module._word_project_value_items(row.get("_word_mini_dma_sources"))  # noqa: SLF001
    assert not launcher_module._word_project_value_items(row.get("Mini DMA graphs"))  # noqa: SLF001


def test_microwire_word_report_project_uses_shape_memory_payload_sources(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = data_root / "microwire_project_copy.pydpj"
    project_path.parent.mkdir(parents=True)
    manual_root = data_root / "manual stress-strain"
    first_path = manual_root / "Ni50Fe27Ga23 12_2 0mA.txt"
    second_path = manual_root / "Ni50Fe27Ga23 12_2 50mA fracture.txt"
    manual_root.mkdir(parents=True)
    for path in (first_path, second_path):
        path.write_text("0.1 0.01\n0.2 0.02\n", encoding="utf-8")
    records = [
        SimpleNamespace(
            key=("Ni50Fe27Ga23", 12, 2, None),
            sample="Ni50Fe27Ga23 12-2",
            label="0mA - Ni50Fe27Ga23 12_2 0mA",
            path=first_path,
        ),
        SimpleNamespace(
            key=("Ni50Fe27Ga23", 12, 2, None),
            sample="Ni50Fe27Ga23 12-2",
            label="50mA fracture - Ni50Fe27Ga23 12_2 50mA fracture",
            path=second_path,
        ),
    ]
    encoded_records = {
        "encoding": "pickle-base64",
        "value": base64.b64encode(pickle.dumps(records)).decode("ascii"),
    }
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "shape_memory_stress_strain": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Manual stress/strain graphs": "0mA - Ni50Fe27Ga23 12_2 0mA",
                                "_sources": [str(first_path)],
                            }
                        ],
                        "payloads": {
                            "shape_memory_stress_strain_records": encoded_records,
                        },
                    },
                    "assemble": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                                "Manual stress/strain graphs": [
                                    "0mA - Ni50Fe27Ga23 12_2 0mA",
                                    "50mA fracture - Ni50Fe27Ga23 12_2 50mA fracture",
                                ],
                            }
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_origin=False,
    )

    frame, origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        args,
        tmp_path / "reports",
    )

    assert origin_artifacts == {}
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["Manual stress/strain graphs"] == [
        "0mA - Ni50Fe27Ga23 12_2 0mA",
        "50mA fracture - Ni50Fe27Ga23 12_2 50mA fracture",
    ]
    assert row["_word_shape_memory_stress_strain_sources"] == [
        str(first_path),
        str(second_path),
    ]


def test_microwire_word_report_project_exports_rvst_through_pyplot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "Praha"
    project_path = data_root / "microwire_project_copy.pydpj"
    project_path.parent.mkdir(parents=True)
    rvt_path = data_root / "RvsT" / "RvsT" / "Ni50Fe27Ga23_12_2.csv"
    rvt_path.parent.mkdir(parents=True)
    rvt_path.write_text(
        "\n".join(
            [
                "iso_time;t_elapsed_s;sp_c;pv_c;resistance_ohm",
                "2026-02-06T08:22:38;0.1;-100;-40.5;43.2903",
                "2026-02-06T08:22:48;10.1;-90;-39.0;43.2882",
            ]
        ),
        encoding="utf-8",
    )
    project_path.write_text(
        json.dumps(
            {
                "kind": "microwire_data_builder",
                "version": 1,
                "sections": {
                    "assemble": {"rows": [], "columns": []},
                    "microscope": {
                        "rows": [
                            {
                                "Composition": "Ni50Fe27Ga23",
                                "Microwire": "12/2",
                            }
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    captured: list[tuple[str, list[Path]]] = []

    def fake_export_pyplot_origin_artifacts_for_paths(**kwargs: object) -> list[object]:
        captured.append(
            (
                str(kwargs["plugin_name"]),
                [Path(path) for path in kwargs["paths"]],  # type: ignore[index]
                str(kwargs.get("plot_mode") or "raw"),
            )
        )
        descriptor = "rvst_residual.oggu" if kwargs.get("plot_mode") == "residual" else "rvst.oggu"
        return [
            argparse.Namespace(
                descriptor=descriptor,
                display_text="R vs T residual from PyPlot" if kwargs.get("plot_mode") == "residual" else "R vs T from PyPlot",
            )
        ]

    monkeypatch.setattr(
        launcher_module,
        "_export_pyplot_origin_artifacts_for_paths",
        fake_export_pyplot_origin_artifacts_for_paths,
    )
    args = argparse.Namespace(
        microwire_word_sample="Ni50Fe27Ga23 12/2",
        microwire_word_origin=True,
    )

    frame, origin_artifacts = launcher_module._load_microwire_word_report_frame(  # noqa: SLF001
        project_path,
        args,
        tmp_path / "reports",
    )

    assert captured == [("R vs T", [rvt_path], "raw"), ("R vs T", [rvt_path], "residual")]
    assert origin_artifacts["rvst.oggu"].display_text == "R vs T from PyPlot"
    assert origin_artifacts["rvst_residual.oggu"].display_text == "R vs T residual from PyPlot"
    assert frame.iloc[0]["R vs T graphs (Origin)"] == "rvst.oggu"
    assert frame.iloc[0]["R vs T residual graphs (Origin)"] == "rvst_residual.oggu"


@pytest.mark.parametrize(
    ("name", "module", "resource_tag"),
    [
        (
            "Mini DMA Logger",
            "data_logging.mini_dma_logger.mini_dma_logger",
            "mini_dma",
        ),
        (
            "Current Annealing Logger",
            "data_logging.current_annealing_logger.current_annealing_logger",
            "current_annealing",
        ),
    ],
)
def test_hardware_experiment_loggers_launch_in_child_process(
    name: str,
    module: str,
    resource_tag: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    monkeypatch.setattr(
        launcher_module,
        "launch_experiment_process",
        lambda spec: launched.append(spec),
    )

    result = launcher_module.LOGGERS[name]()

    assert result is None
    assert launched
    spec = launched[0]
    assert getattr(spec, "display_name") == name
    assert getattr(spec, "module") == module
    assert getattr(spec, "resource_tag") == resource_tag


def test_experiment_process_cli_dispatches_registered_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    class _Module:
        @staticmethod
        def main() -> None:
            called.append("main")

    monkeypatch.setattr(
        launcher_module,
        "import_module",
        lambda module: _Module
        if module == "data_logging.current_annealing_logger.current_annealing_logger"
        else pytest.fail(f"unexpected module import: {module}"),
    )

    args, _qt_args = launcher_module._parse_launcher_args(
        ["--experiment-process", "current_annealing"]
    )

    assert launcher_module._is_experiment_process_requested(args)
    assert launcher_module._run_experiment_process(args) == 0
    assert called == ["main"]


def test_run_microwire_eda_cli_passes_copy_safe_and_findings_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def _fake_generate_report(config: object) -> object:
        captured["config"] = config
        return argparse.Namespace(
            report_path=tmp_path / "report.html",
            workbook_path=tmp_path / "summary.xlsx",
            csv_path=tmp_path / "dataset.csv",
            manifest_path=tmp_path / "manifest.json",
            findings_json_path=tmp_path / "findings.json",
            findings_md_path=tmp_path / "findings.md",
            copied_project_path=tmp_path / "working" / "copy.pydpj",
            findings=[{"headline": "Top signal"}],
        )

    import microwire_eda.core as eda_core

    monkeypatch.setattr(eda_core, "generate_report", _fake_generate_report)

    args = argparse.Namespace(
        microwire_eda=str(tmp_path / "source.pydpj"),
        rows="all",
        out=str(tmp_path / "artifacts"),
        microwire_eda_title="CLI EDA",
        microwire_eda_working_copy_dir=str(tmp_path / "working"),
        microwire_eda_copy_project=True,
        microwire_eda_force_project_rebuild=True,
        microwire_eda_legacy_breakage=False,
        microwire_eda_composition_splits=False,
        microwire_eda_findings=True,
    )

    exit_code = launcher_module._run_microwire_eda_cli(args)  # noqa: SLF001 - internal automation hook

    assert exit_code == 0
    config = captured["config"]
    assert getattr(config, "copy_project") is True
    assert getattr(config, "force_project_rebuild") is True
    assert getattr(config, "working_copy_dir") == tmp_path / "working"
    assert getattr(config, "include_legacy_breakage_analysis") is False
    assert getattr(config, "include_composition_splits") is False
    assert getattr(config, "write_findings") is True
    output = capsys.readouterr().out
    assert "findings_json=" in output
    assert "copied_project=" in output
    assert "finding=Top signal" in output


def test_launcher_pyplot_automation_generates_summary_and_artifacts(tmp_path: Path) -> None:
    _ensure_app()
    source = _write_hysteresis_source(tmp_path / "250C sample.dat")
    screenshot_path = tmp_path / "window.png"
    plot_path = tmp_path / "plot.png"
    summary_path = tmp_path / "summary.json"
    args = argparse.Namespace(
        pyplot_list_plugins=False,
        pyplot_plugin="Hysteresis Loops",
        pyplot_import=[str(source)],
        pyplot_plot=True,
        pyplot_open_graph_format=True,
        pyplot_open_origin=False,
        pyplot_screenshot=str(screenshot_path),
        pyplot_plot_image=str(plot_path),
        pyplot_summary_json=str(summary_path),
        pyplot_show_window=False,
        pyplot_wait_ms=0,
        visual_check=False,
    )

    exit_code = launcher_module._run_pyplot_automation(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 0
    assert screenshot_path.exists()
    assert plot_path.exists()
    assert summary_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["plugin"] == "Hysteresis Loops"
    assert summary["tab_count"] >= 1
    assert summary["current_tab_has_axes"] is True
    assert summary["graph_format_visible"] is True
    assert summary["status"] == "ok"
    assert summary["kind"] == "pyplot"
    assert summary["version"] == 1
    assert summary["window_image"] == str(screenshot_path.resolve())
    assert summary["current_plot_image"] == str(plot_path.resolve())


@pytest.mark.parametrize(
    ("recipe_payload", "message_fragment"),
    [
        (None, "file not found"),
        ("{not-json", "not valid JSON"),
        ({"kind": "builder", "version": 1}, "project"),
        ({"kind": "pyplot", "version": 99}, "Only version 1 is supported"),
        ({"kind": "pyplot", "version": 1, "plugin": "Nope"}, "Unknown PyPlot plugin"),
    ],
)
def test_automation_recipe_validation_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    recipe_payload: dict[str, object] | str | None,
    message_fragment: str,
) -> None:
    recipe_path = tmp_path / "recipe.json"
    if isinstance(recipe_payload, dict):
        recipe_path.write_text(json.dumps(recipe_payload), encoding="utf-8")
    elif isinstance(recipe_payload, str):
        recipe_path.write_text(recipe_payload, encoding="utf-8")
    args = argparse.Namespace(automation_recipe=str(recipe_path))

    exit_code = launcher_module._run_automation_recipe(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 2
    assert message_fragment in capsys.readouterr().out


def test_builder_automation_recipe_updates_vsm_temperature_scan_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    scan_path = tmp_path / "202602010101-TSCN-a000-example.txt"
    scan_path.write_text(
        "\n".join(
            [
                "@Samplename: Ni50Fe27Ga23 5-4",
                "@@End of Header.",
                "Time_since_start Applied_Field Signal_X_direction Sample_Temperature_For_Plot_",
                "New Section: Section 0:",
                "0 10000 0.00051 25.0",
                "1 10000 0.00050 26.0",
            ]
        ),
        encoding="utf-8",
    )
    bad_scan_path = tmp_path / "bad-scan.txt"
    bad_scan_path.write_text("not a VSM temperature scan", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "vsm_temperature_scan",
                        "paths": [str(scan_path), str(bad_scan_path)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    second_exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )
    assert second_exit_code == 0
    assert project_path.read_text(encoding="utf-8") != output_project.read_text(encoding="utf-8")
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    section_payload = output_payload["sections"]["vsm_temperature_scan"]
    assert section_payload["rows"]
    assert section_payload["payloads"]["vsm_temperature_scan_records"]["encoding"] == "pickle-base64"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"
    assert manifest["copied_project"] == str(output_project.resolve())
    assert manifest["commands"][0]["record_count"] == 1
    assert manifest["commands"][0]["updated_count"] == 1
    assert manifest["commands"][0]["skipped_count"] == 1
    assert manifest["commands"][0]["skipped_sources"] == [str(bad_scan_path)]
    assert '"kind": "builder"' in capsys.readouterr().out


def test_builder_automation_recipe_updates_annealing_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    annealing_path = tmp_path / "Ni50Fe27Ga23 12_2 s1 1000mA.txt"
    annealing_path.write_text("0.1 0.2 2.0\n0.2 0.4 2.0\n", encoding="utf-8")
    bad_path = tmp_path / "bad_annealing.txt"
    bad_path.write_text("not valid annealing data\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "annealing",
                        "paths": [str(annealing_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["annealing"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    section_payload = output_payload["sections"]["annealing"]
    assert section_payload["payloads"]["annealing_records"]["encoding"] == "pickle-base64"
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["Composition"] == "Ni50Fe27Ga23"
    assert row["Microwire"] == "12/2"
    assert row["_sources"] == [str(annealing_path)]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "annealing"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"


def test_builder_automation_recipe_updates_microscope_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    core_image = tmp_path / "Ni50Fe27Ga23 12_2 core.jpg"
    glass_image = tmp_path / "Ni50Fe27Ga23 12_2 glass.jpg"
    core_image.write_bytes(b"core image")
    glass_image.write_bytes(b"glass image")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "microscope",
                        "paths": [str(core_image), str(glass_image)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["microscope"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    section_payload = output_payload["sections"]["microscope"]
    assert section_payload["payloads"]["microscope_index"]["encoding"] == "pickle-base64"
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["Composition"] == "Ni50Fe27Ga23"
    assert row["Microwire"] == "12/2"
    assert row["_core_image"] == str(core_image)
    assert row["_glass_image"] == str(glass_image)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "microscope"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 2
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"


def test_builder_automation_recipe_updates_vsm_hysteresis_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    hysteresis_path = tmp_path / "Ni50Fe27Ga23 12_2 202507101320-Hys-a140-T-30-00.VSM-Hys-Data"
    hysteresis_path.write_text(
        "\n".join(
            [
                "@Section 0",
                "Column 0: Time since start, Time [s]",
                "Column 1: Applied Field, Applied Field [Oe]",
                "Column 2: Signal parallel with sample, Moment [emu]",
                "@@END Columns",
                "@@End of Header.",
                "@@Data",
                "New Section: Section 0:",
                "0.0 0.0 0.0",
                "1.0 5.0 0.2",
                "2.0 -5.0 -0.2",
                "@@END Data",
            ]
        ),
        encoding="utf-8",
    )
    bad_path = tmp_path / "bad_hysteresis.VSM-Hys-Data"
    bad_path.write_text("not a VSM hysteresis file", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "vsm_hysteresis",
                        "paths": [str(hysteresis_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["vsm_hysteresis"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    section_payload = output_payload["sections"]["vsm_hysteresis"]
    assert section_payload["payloads"]["vsm_hysteresis_records"]["encoding"] == "pickle-base64"
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["_sample"] == "Ni50Fe27Ga23 12-2"
    expected_graphs = ["T-30C — 202507101320-Hys-a140-T-30-00"]
    assert row["VSM hysteresis graphs"] == expected_graphs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "vsm_hysteresis"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"
    assert assemble_row["VSM hysteresis graphs"] == expected_graphs


def test_builder_automation_recipe_updates_dma_iso_stress_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    source_fixture = Path("tests/fixtures/dma_iso_stress/minimal_iso_stress.txt")
    dma_path = tmp_path / "Ni50Fe27Ga23 12_2.txt"
    dma_path.write_text(source_fixture.read_text(encoding="utf-8"), encoding="utf-8")
    bad_path = tmp_path / "bad_dma.txt"
    bad_path.write_text("not valid DMA data\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "dma_iso_stress",
                        "paths": [str(dma_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["dma_iso_stress"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    section_payload = output_payload["sections"]["dma_iso_stress"]
    assert section_payload["payloads"]["dma_iso_stress_records"]["encoding"] == "pickle-base64"
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["_sample"] == "Ni50Fe27Ga23 12-2"
    expected_graphs = ["Ni50Fe27Ga23 12_2"]
    assert row["DMA iso-stress graphs"] == expected_graphs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "dma_iso_stress"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"
    assert assemble_row["DMA iso-stress graphs"] == expected_graphs


def test_builder_automation_recipe_updates_fmr_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    fmr_path = tmp_path / "Ni50Fe27Ga23 12_2.csv"
    fmr_path.write_text(
        "\n".join(
            [
                "Sample Name,Ni50Fe27Ga23 12_2",
                "Freq,35.8 GHz",
                "Time,Field,X,Y",
                "s,Oe,V,V",
                "0,-100,1.0,0.1",
                "1,0,0.5,0.2",
                "2,100,0.2,0.3",
            ]
        ),
        encoding="utf-8",
    )
    bad_path = tmp_path / "bad_fmr.csv"
    bad_path.write_text("not valid FMR data\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "fmr",
                        "paths": [str(fmr_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["fmr"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    section_payload = output_payload["sections"]["fmr"]
    assert section_payload["payloads"]["fmr_records"]["encoding"] == "pickle-base64"
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["_sample"] == "Ni50Fe27Ga23 12-2"
    expected_graphs = ["Ni50Fe27Ga23 12_2"]
    assert row["FMR graphs"] == expected_graphs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "fmr"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"
    assert assemble_row["FMR graphs"] == expected_graphs


def test_builder_automation_recipe_updates_shape_memory_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    shape_path = tmp_path / "Ni50Fe27Ga23 12_2.txt"
    shape_path.write_text(
        "\n".join(
            [
                "Displacement\tLoad\tStrain\tStress",
                "mm\tg\t%\tMPa",
                "0.00\t0.00\t0.00\t0.00",
                "0.10\t5.00\t0.20\t25.00",
                "0.20\t8.00\t0.40\t50.00",
                "0.15\t4.00\t0.30\t35.00",
            ]
        ),
        encoding="utf-8",
    )
    bad_path = tmp_path / "bad_shape_memory.txt"
    bad_path.write_text("not valid manual stress strain data\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "shape_memory_stress_strain",
                        "paths": [str(shape_path), str(bad_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["shape_memory_stress_strain"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    section_payload = output_payload["sections"]["shape_memory_stress_strain"]
    assert section_payload["payloads"]["shape_memory_stress_strain_records"]["encoding"] == "pickle-base64"
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["_sample"] == "Ni50Fe27Ga23 12-2"
    expected_graphs = ["Ni50Fe27Ga23 12_2"]
    assert row["Manual stress/strain graphs"] == expected_graphs
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    update_command = manifest["commands"][0]
    assert update_command["section"] == "shape_memory_stress_strain"
    assert update_command["record_count"] == 1
    assert update_command["updated_count"] == 1
    assert update_command["skipped_count"] == 1
    assert update_command["skipped_sources"] == [str(bad_path)]
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Composition"] == "Ni50Fe27Ga23"
    assert assemble_row["Microwire"] == "12/2"
    assert assemble_row["Manual stress/strain graphs"] == expected_graphs


def _write_mini_dma_run(path: Path, *, sample_name: str = "Ni50Fe27Ga23 12_2") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": sample_name,
                "initial_length_mm": 10.0,
                "created_utc": "2026-06-01 09:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 09:10:00",
            }
        ),
        encoding="utf-8",
    )
    rows = [
        "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,stress_mpa,resistance_ohm,current_set_mA,current_measured_mA,position_mm",
        "0,current,50,1,0.00,50,100,1,1,0.000",
        "1,current,50,1,0.05,50,101,10,10,0.005",
        "2,current,50,1,0.10,50,102,20,20,0.010",
        "3,current,100,2,0.15,100,110,1,1,0.015",
        "4,current,100,2,0.25,100,112,10,10,0.025",
        "5,current,100,2,0.35,100,114,20,20,0.035",
    ]
    (path / "measurement.csv").write_text("\n".join(rows), encoding="utf-8")
    return path


def _write_transition_mini_dma_run(
    path: Path,
    *,
    sample_name: str = "Ni50Fe27Ga23 12_2",
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "sample_name": sample_name,
                "initial_length_mm": 10.0,
                "wire_diameter_mm": 0.0191,
                "created_utc": "2026-06-01 09:00:00",
                "session_state": "finished",
                "finished_utc": "2026-06-01 09:10:00",
            }
        ),
        encoding="utf-8",
    )
    heating_current = np.linspace(1.0, 100.0, 120)
    cooling_current = np.linspace(100.0, 1.0, 120)

    def piecewise(current: np.ndarray, start: float, finish: float) -> np.ndarray:
        before = 4.0 - current * 0.002
        start_value = 4.0 - start * 0.002
        transition = start_value - (current - start) * 0.04
        finish_value = start_value - (finish - start) * 0.04
        after = finish_value - (current - finish) * 0.003
        return np.where(current < start, before, np.where(current <= finish, transition, after))

    current = np.concatenate([heating_current, cooling_current])
    strain = np.concatenate(
        [
            piecewise(heating_current, 30.0, 70.0),
            piecewise(cooling_current, 25.0, 65.0),
        ]
    )
    rows = [
        "elapsed_s,automation_phase,automation_target_value,plateau_index,strain_pct,stress_mpa,resistance_ohm,current_set_mA,current_measured_mA,position_mm"
    ]
    for index, (current_mA, strain_pct) in enumerate(zip(current, strain, strict=True)):
        rows.append(
            ",".join(
                [
                    str(index),
                    "current",
                    "50",
                    "1",
                    f"{strain_pct:.6f}",
                    "50",
                    "100",
                    f"{current_mA:.6f}",
                    f"{current_mA:.6f}",
                    f"{strain_pct / 10.0:.6f}",
                ]
            )
        )
    (path / "measurement.csv").write_text("\n".join(rows), encoding="utf-8")
    return path


def test_builder_automation_recipe_updates_mini_dma_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    run_path = _write_mini_dma_run(tmp_path / "Ni50Fe27Ga23 12_2 test_run01")
    bad_run = tmp_path / "bad-run"
    bad_run.mkdir()
    (bad_run / "measurement.csv").write_text("not,a,mini,dma\n", encoding="utf-8")
    output_project = tmp_path / "out" / "updated.pydpj"
    manifest_path = tmp_path / "out" / "manifest.json"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "manifest_path": str(manifest_path),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "mini_dma",
                        "paths": [str(run_path), str(bad_run)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["mini_dma"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    section_payload = output_payload["sections"]["mini_dma"]
    assert section_payload["rows"]
    row = section_payload["rows"][0]
    assert row["Mini DMA strain by stress/load"] == [
        "50 MPa: 0.1% @ 20 mA",
        "100 MPa: 0.2% @ 20 mA",
    ]
    assert row["Mini DMA break point"] == ""
    assert section_payload["payloads"]["mini_dma_records"]["encoding"] == "pickle-base64"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    command = manifest["commands"][0]
    assert command["section"] == "mini_dma"
    assert command["record_count"] == 1
    assert command["updated_count"] == 1
    assert command["skipped_count"] == 1
    assert str(bad_run / "measurement.csv") in command["skipped_sources"]
    rebuild_command = manifest["commands"][1]
    assert rebuild_command["action"] == "rebuild_assemble"
    assert rebuild_command["status"] == "ok"
    assemble_rows = output_payload["sections"]["assemble"]["rows"]
    assert assemble_rows
    assemble_row = assemble_rows[0]
    assert assemble_row["Mini DMA graphs"] == [run_path.name]
    assert assemble_row["Mini DMA strain by stress/load"] == [
        "50 MPa: 0.1% @ 20 mA",
        "100 MPa: 0.2% @ 20 mA",
    ]


def test_builder_automation_recipe_updates_mini_dma_transition_currents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    run_path = _write_transition_mini_dma_run(tmp_path / "Ni50Fe27Ga23 12_2 test_run02")
    output_project = tmp_path / "out" / "updated.pydpj"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "working_copy_dir": str(tmp_path / "working"),
                "output_project": str(output_project),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "mini_dma",
                        "paths": [str(run_path)],
                    },
                    {
                        "action": "rebuild_assemble",
                        "sections": ["mini_dma"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    output_payload = json.loads(output_project.read_text(encoding="utf-8"))
    row = output_payload["sections"]["mini_dma"]["rows"][0]
    assert row["Mini DMA transition currents by stress/load"] == [
        "50 MPa / 1.46 g: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA"
    ]
    assemble_row = output_payload["sections"]["assemble"]["rows"][0]
    assert assemble_row["Mini DMA transition currents by stress/load"] == [
        "50 MPa / 1.46 g: As 30 mA, Af 70 mA, Ms 65 mA, Mf 25 mA"
    ]


def test_builder_automation_recipe_promotes_database_latest_and_archives_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    database_dir = tmp_path / "microwire_database"
    database_dir.mkdir()
    latest_project = database_dir / "microwire_database_latest.pydpj"
    latest_project.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    latest_manifest = database_dir / "update_manifest_latest.json"
    latest_manifest.write_text(
        json.dumps({"kind": "builder", "status": "old"}),
        encoding="utf-8",
    )
    scan_path = tmp_path / "202602010101-TSCN-a000-example.txt"
    scan_path.write_text(
        "\n".join(
            [
                "@Samplename: Ni50Fe27Ga23 5-4",
                "@@End of Header.",
                "Time_since_start Applied_Field Signal_X_direction Sample_Temperature_For_Plot_",
                "New Section: Section 0:",
                "0 10000 0.00051 25.0",
                "1 10000 0.00050 26.0",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "database_dir": str(database_dir),
                "timestamp": "2026-05-26_1512",
                "commands": [
                    {
                        "action": "update_section",
                        "section": "vsm_temperature_scan",
                        "paths": [str(scan_path)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    assert latest_project.exists()
    assert (database_dir / "update_manifest_latest.json").exists()
    archived_project = database_dir / "archive" / "microwire_database_2026-05-26_1512.pydpj"
    archived_manifest = database_dir / "archive" / "update_manifest_2026-05-26_1512.json"
    assert archived_project.exists()
    assert archived_manifest.exists()
    latest_payload = json.loads(latest_project.read_text(encoding="utf-8"))
    assert latest_payload["sections"]["vsm_temperature_scan"]["rows"]
    latest_manifest_payload = json.loads(
        (database_dir / "update_manifest_latest.json").read_text(encoding="utf-8")
    )
    assert latest_manifest_payload["database"]["latest_project"] == str(latest_project.resolve())
    assert latest_manifest_payload["database"]["archived_project"] == str(archived_project.resolve())


def test_builder_automation_recipe_can_exclude_named_subdirectories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_app()
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    project_path = tmp_path / "microwire_project.pydpj"
    project_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "saved_at": "2026-05-25 10:00",
                "sections": {},
            }
        ),
        encoding="utf-8",
    )
    data_root = tmp_path / "mini_dma"
    good_run = _write_mini_dma_run(data_root / "good_run")
    archived_run = _write_mini_dma_run(data_root / "archive" / "old_run", sample_name="Ni50Fe27Ga23 12_3")
    output_project = tmp_path / "out" / "updated.pydpj"
    recipe_path = tmp_path / "builder_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "builder",
                "version": 1,
                "project": str(project_path),
                "output_project": str(output_project),
                "commands": [
                    {
                        "action": "update_section",
                        "section": "mini_dma",
                        "paths": [str(data_root)],
                        "exclude_dir_names": ["archive"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    payload = json.loads(output_project.read_text(encoding="utf-8"))
    rows = payload["sections"]["mini_dma"]["rows"]
    assert len(rows) == 1
    assert str(good_run) in rows[0]["_sources"]
    assert str(archived_run) not in rows[0]["_sources"]


def test_automation_recipe_rejects_origin_when_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "plugin": "Hysteresis Loops",
                "open_origin": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher_module, "_origin_is_available", lambda: False)
    args = argparse.Namespace(automation_recipe=str(recipe_path))

    exit_code = launcher_module._run_automation_recipe(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 2
    assert "Origin automation is unavailable" in capsys.readouterr().out


def test_automation_recipe_generates_manifest_and_plot_exports(tmp_path: Path) -> None:
    _ensure_app()
    first = _write_hysteresis_source(tmp_path / "250C Sample A.dat")
    second = _write_hysteresis_source(tmp_path / "300C Sample B.dat")
    manifest_path = tmp_path / "artifacts" / "manifest.json"
    window_path = tmp_path / "artifacts" / "window.png"
    current_plot_path = tmp_path / "artifacts" / "current.png"
    plot_dir = tmp_path / "artifacts" / "plots"
    recipe_path = tmp_path / "job.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "plugin": "Hysteresis Loops",
                "imports": [first.name, second.name],
                "generate": True,
                "exports": {
                    "window_image": "artifacts/window.png",
                    "current_plot_image": "artifacts/current.png",
                    "plot_images_dir": "artifacts/plots",
                },
                "manifest_path": "artifacts/manifest.json",
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(automation_recipe=str(recipe_path))

    exit_code = launcher_module._run_automation_recipe(args, [])  # noqa: SLF001 - internal automation hook

    assert exit_code == 0
    assert manifest_path.exists()
    assert window_path.exists()
    assert current_plot_path.exists()
    assert plot_dir.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"
    assert manifest["plugin"] == "Hysteresis Loops"
    assert manifest["kind"] == "pyplot"
    assert manifest["version"] == 1
    assert manifest["window_image"] == str(window_path.resolve())
    assert manifest["current_plot_image"] == str(current_plot_path.resolve())
    assert manifest["imported_paths"] == [str(first.resolve()), str(second.resolve())]
    assert manifest["plot_image_paths"]
    for index, exported in enumerate(manifest["plot_image_paths"], start=1):
        path = Path(exported)
        assert path.exists()
        assert path.name.startswith(f"{index:02d}-")


def test_automation_recipe_can_save_and_reload_pyplot_project(tmp_path: Path) -> None:
    _ensure_app()
    source = _write_hysteresis_source(tmp_path / "250C sample.dat")
    project_path = tmp_path / "artifacts" / "saved_project.pypj"
    save_manifest_path = tmp_path / "artifacts" / "save_manifest.json"
    load_manifest_path = tmp_path / "artifacts" / "load_manifest.json"

    save_recipe = tmp_path / "save_job.json"
    save_recipe.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "plugin": "Hysteresis Loops",
                "imports": [source.name],
                "generate": True,
                "save_project": "artifacts/saved_project.pypj",
                "manifest_path": "artifacts/save_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    save_args = argparse.Namespace(automation_recipe=str(save_recipe))

    save_exit_code = launcher_module._run_automation_recipe(save_args, [])  # noqa: SLF001 - internal automation hook

    assert save_exit_code == 0
    assert project_path.exists()

    load_recipe = tmp_path / "load_job.json"
    load_recipe.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "load_project": "artifacts/saved_project.pypj",
                "manifest_path": "artifacts/load_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    load_args = argparse.Namespace(automation_recipe=str(load_recipe))

    load_exit_code = launcher_module._run_automation_recipe(load_args, [])  # noqa: SLF001 - internal automation hook

    assert load_exit_code == 0
    save_manifest = json.loads(save_manifest_path.read_text(encoding="utf-8"))
    load_manifest = json.loads(load_manifest_path.read_text(encoding="utf-8"))
    assert save_manifest["saved_project"] == str(project_path.resolve())
    assert load_manifest["loaded_project"] == str(project_path.resolve())
    assert load_manifest["plugin"] == "Hysteresis Loops"
    assert load_manifest["tab_count"] >= 1
    assert load_manifest["workbook_count"] >= 1


def test_automation_recipe_can_build_graphs_and_layout_figure(tmp_path: Path) -> None:
    _ensure_app()
    csv_path = tmp_path / "builder.csv"
    csv_path.write_text(
        "\n".join(
            [
                "field,flux_a,flux_b,flux_c,flux_d",
                "0,1.0,3.5,0.8,2.6",
                "1,2.0,2.7,1.4,2.3",
                "2,3.2,1.9,1.8,1.7",
                "3,4.0,1.2,2.1,1.0",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "layout_job.json"
    manifest_path = tmp_path / "layout_manifest.json"
    plot_dir = tmp_path / "plots"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "imports": [csv_path.name],
                "build_graphs": [
                    {
                        "title": "Graph 1",
                        "series": [
                            {"workbook": "builder.csv", "worksheet": "builder", "x_column": "field", "y_column": "flux_a", "label": "A"},
                            {"workbook": "builder.csv", "worksheet": "builder", "x_column": "field", "y_column": "flux_b", "label": "B"},
                        ],
                    },
                    {
                        "title": "Graph 2",
                        "series": [
                            {"workbook": "builder.csv", "worksheet": "builder", "x_column": "field", "y_column": "flux_c", "label": "C"},
                            {"workbook": "builder.csv", "worksheet": "builder", "x_column": "field", "y_column": "flux_d", "label": "D"},
                        ],
                    },
                ],
                "create_figures": [
                    {
                        "title": "Two Panel Figure",
                        "rows": 2,
                        "cols": 1,
                        "share_x": True,
                        "share_y": True,
                        "panel_labels": "lower",
                        "source_titles": ["Graph 1", "Graph 2"],
                    }
                ],
                "exports": {
                    "plot_images_dir": "plots"
                },
                "manifest_path": "layout_manifest.json",
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001 - internal automation hook
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["tab_count"] >= 3
    assert "Two Panel Figure" in manifest["tab_labels"]
    assert plot_dir.exists()


def test_automation_recipe_can_export_all_figures_batch(tmp_path: Path) -> None:
    _ensure_app()
    csv_path = tmp_path / "batch.csv"
    csv_path.write_text(
        "\n".join(
            [
                "field,flux_a,flux_b",
                "0,1.0,3.5",
                "1,2.0,2.7",
                "2,3.2,1.9",
                "3,4.0,1.2",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "batch_job.json"
    manifest_path = tmp_path / "batch_manifest.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "imports": [csv_path.name],
                "build_graphs": [
                    {
                        "title": "Batch Graph",
                        "series": [
                            {"workbook": "batch.csv", "worksheet": "batch", "x_column": "field", "y_column": "flux_a", "label": "A"},
                            {"workbook": "batch.csv", "worksheet": "batch", "x_column": "field", "y_column": "flux_b", "label": "B"},
                        ],
                    }
                ],
                "exports": {
                    "all_figures": {
                        "dir": "exports",
                        "format": "png",
                        "dpi": 200
                    }
                },
                "manifest_path": "batch_manifest.json",
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["all_figure_export_paths"]
    for exported in manifest["all_figure_export_paths"]:
        assert Path(exported).exists()


def test_automation_recipe_can_capture_review_screenshots(tmp_path: Path) -> None:
    _ensure_app()
    csv_path = tmp_path / "review.csv"
    csv_path.write_text(
        "\n".join(
            [
                "field,flux_a",
                "0,1.0",
                "1,2.0",
                "2,3.2",
                "3,4.0",
            ]
        ),
        encoding="utf-8",
    )
    recipe_path = tmp_path / "review_job.json"
    manifest_path = tmp_path / "review_manifest.json"
    recipe_path.write_text(
        json.dumps(
            {
                "kind": "pyplot",
                "version": 1,
                "imports": [csv_path.name],
                "build_graphs": [
                    {
                        "title": "Review Graph",
                        "series": [
                            {"workbook": "review.csv", "worksheet": "review", "x_column": "field", "y_column": "flux_a", "label": "A"},
                        ],
                    }
                ],
                "exports": {
                    "review_screenshots": {
                        "dir": "review_artifacts",
                        "dark_gui": True
                    }
                },
                "manifest_path": "review_manifest.json",
            }
        ),
        encoding="utf-8",
    )

    exit_code = launcher_module._run_automation_recipe(  # noqa: SLF001
        argparse.Namespace(automation_recipe=str(recipe_path)),
        [],
    )

    assert exit_code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["review_paths"]
    for exported in manifest["review_paths"]:
        assert Path(exported).exists()


def test_live_pyplot_session_can_plot_capture_and_close(tmp_path: Path) -> None:
    source = _write_hysteresis_source(tmp_path / "250C session.dat")
    info_path = tmp_path / "session-info.json"
    launcher_path = Path(launcher_module.__file__).resolve()
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONUNBUFFERED"] = "1"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [
            sys.executable,
            str(launcher_path),
            "--pyplot-session-start",
            "--pyplot-plugin",
            "Hysteresis Loops",
            "--pyplot-session-info-file",
            str(info_path),
        ],
        cwd=str(launcher_path.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    try:
        info: dict[str, object] | None = None
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if info_path.exists():
                try:
                    info = json.loads(info_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    info = None
                if isinstance(info, dict) and info.get("session_id"):
                    break
            if process.poll() is not None:
                break
            time.sleep(0.2)
        assert process.poll() is None, process.stderr.read() if process.stderr is not None else ""
        assert isinstance(info, dict)
        session_id = str(info["session_id"])

        import_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "import_paths",
                "paths": [str(source)],
            },
        )
        assert import_response["status"] == "ok"

        generate_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "generate",
            },
        )
        assert generate_response["status"] == "ok"
        assert generate_response["state"]["plugin"] == "Hysteresis Loops"
        assert generate_response["state"]["tab_count"] >= 1

        plot_path = tmp_path / "live-session-plot.png"
        capture_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "capture_current_plot",
                "path": str(plot_path),
            },
        )
        assert capture_response["status"] == "ok"
        assert plot_path.exists()

        state_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "state",
            },
        )
        assert state_response["status"] == "ok"
        assert state_response["result"]["tab_count"] >= 1

        close_response = launcher_module._send_pyplot_session_command(  # noqa: SLF001
            session_id,
            {
                "action": "close",
            },
        )
        assert close_response["status"] == "ok"
        assert close_response["closing"] is True
        process.wait(timeout=20)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def test_review_capture_collapses_extra_tabs_and_restores_visibility(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(plotters={})
    try:
        window.resize(1500, 960)
        window.show()
        app.processEvents()

        def _add_plot(title: str, offset: float) -> QtWidgets.QWidget:
            fig = Figure(figsize=(4.8, 3.2))
            axes = fig.add_subplot(111)
            axes.plot([0.0, 1.0, 2.0], [offset, offset + 0.8, offset + 1.6], label=title)
            axes.set_title(title)
            axes.set_xlabel("X")
            axes.set_ylabel("Y")
            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(canvas)
            descriptor = TabDescriptor(
                kind="unit_test",
                title=title,
                root_label=title,
                x_label="X",
                y_label="Y",
                canvas=canvas,
                axes=axes,
                lines={},
                metadata={"plugin": "Unit Test Plugin"},
            )
            index = window.tab_widget.addTab(tab, title)
            window.tab_widget.setCurrentIndex(index)
            window._register_plot_tab(tab, canvas, axes, descriptor)  # noqa: SLF001
            return tab

        first = _add_plot("First Graph", 0.0)
        second = _add_plot("Second Graph", 1.0)
        window.tab_widget.setCurrentWidget(second)
        app.processEvents()

        visibility_before = [
            bool(window.tab_widget.isTabVisible(index))
            for index in range(window.tab_widget.count())
        ]
        review_paths = launcher_module._capture_review_screenshots(  # noqa: SLF001
            window,
            app,
            tmp_path,
        )

        assert review_paths
        assert (tmp_path / "pyplot-gui.png").exists()
        assert (tmp_path / "current-figure.png").exists()
        assert window.tab_widget.currentWidget() is second
        visibility_after = [
            bool(window.tab_widget.isTabVisible(index))
            for index in range(window.tab_widget.count())
        ]
        assert visibility_after == visibility_before
        assert first is not None
    finally:
        window._clear_project_dirty()  # noqa: SLF001
        window.close()
        app.processEvents()


def test_forced_dark_theme_applies_dark_palette() -> None:
    app = _ensure_app()
    manager = theme_manager()
    previous = manager.current_mode()
    try:
        manager.set_mode("dark")
        app.processEvents()
        color = app.palette().color(app.palette().ColorRole.Window)
        assert color.lightness() < 100
    finally:
        manager.set_mode(previous)
        app.processEvents()
