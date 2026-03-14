from __future__ import annotations

import argparse
import json
import os
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
