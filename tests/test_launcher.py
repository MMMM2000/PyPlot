from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

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


def test_microwire_word_graph_sections_require_origin_graph_descriptors() -> None:
    source_only = {
        "Shape memory stress/strain graphs": [
            "20mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
            "30mA fracture -- Ni52Fe15Ga27Co6 2/1oe",
        ],
    }
    with_origin = {
        "Shape memory stress/strain graphs": ["30mA"],
        "Shape memory stress/strain graphs (Origin)": "shape_memory.oggu",
    }

    assert launcher_module._microwire_word_graph_sections_for_row(source_only) == {}
    assert launcher_module._microwire_word_graph_sections_for_row(with_origin) == {
        "Shape memory stress/strain": {
            "sources": [],
            "graphs": ["shape_memory.oggu"],
            "references": ["30mA", "shape_memory.oggu"],
        }
    }


def _wait_for_registry(window: launcher_module.MasterLauncher, app: QtWidgets.QApplication) -> None:
    for _ in range(40):
        app.processEvents()
        if getattr(window, "_registry_loaded", False):
            return
    raise AssertionError("Launcher registry did not finish loading in time.")


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


def test_launcher_detects_metadata_index_cli_flags() -> None:
    args, _qt_args = launcher_module._parse_launcher_args(
        [
            "--mini-dma-index-source",
            "mini=C:/runs/mini",
            "--mini-dma-index-output-dir",
            "artifacts/mini-index",
            "--current-annealing-index-source",
            "annealing=C:/runs/annealing",
            "--current-annealing-index-output-dir",
            "artifacts/annealing-index",
        ]
    )

    assert launcher_module._is_mini_dma_index_requested(args) is True  # noqa: SLF001
    assert launcher_module._is_current_annealing_index_requested(args) is True  # noqa: SLF001
    assert args.mini_dma_index_source == ["mini=C:/runs/mini"]
    assert args.mini_dma_index_output_dir == "artifacts/mini-index"
    assert args.current_annealing_index_source == ["annealing=C:/runs/annealing"]
    assert args.current_annealing_index_output_dir == "artifacts/annealing-index"


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


def test_run_metadata_index_clis_write_outputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mini_source = tmp_path / "mini"
    mini_run = mini_source / "run01"
    mini_run.mkdir(parents=True)
    (mini_run / "metadata.json").write_text(
        json.dumps({"sample_name": "mini sample"}),
        encoding="utf-8",
    )
    annealing_source = tmp_path / "annealing"
    annealing_metadata = annealing_source / "metadata" / "runA"
    annealing_metadata.mkdir(parents=True)
    (annealing_metadata / "metadata.json").write_text(
        json.dumps({"sample": "annealing sample"}),
        encoding="utf-8",
    )

    mini_output = tmp_path / "mini_index"
    annealing_output = tmp_path / "annealing_index"
    mini_args = argparse.Namespace(
        mini_dma_index_source=[f"mini={mini_source}"],
        mini_dma_index_output_dir=str(mini_output),
    )
    annealing_args = argparse.Namespace(
        current_annealing_index_source=[f"annealing={annealing_source}"],
        current_annealing_index_output_dir=str(annealing_output),
    )

    assert launcher_module._run_mini_dma_index_cli(mini_args) == 0  # noqa: SLF001
    assert launcher_module._run_current_annealing_index_cli(annealing_args) == 0  # noqa: SLF001

    output = capsys.readouterr().out
    assert "[mini-dma-index] rows=1" in output
    assert "[current-annealing-index] rows=1" in output
    assert (mini_output / "runs_index.csv").exists()
    assert (mini_output / "runs_index.jsonl").exists()
    assert (annealing_output / "current_annealing_index.csv").exists()
    assert (annealing_output / "current_annealing_index.jsonl").exists()


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


def test_shared_hmp_setup_is_not_a_launcher_experiment() -> None:
    assert "Shared HMP PSU Setup" not in launcher_module.LOGGERS


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
        ({"kind": "builder", "version": 1}, "reserved"),
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
