from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd
from PIL import Image, ImageDraw
from PyQt6 import QtCore, QtWidgets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotting.plugins.base import PyPlotPlugin
from plotting.pyplot.app import PyPlotWorkbench
from plotting.shared.origin import origin_session
from plotting.shared.utils import ensure_app_theme

ARTIFACTS_ROOT = ROOT / "artifacts" / "origin-plugin-verify"


@dataclass
class PluginVerificationResult:
    plugin: str
    route: str
    shared_workbooks: bool
    plot_tabs: int
    exported: int = 0
    plotted: int = 0
    images: list[str] = field(default_factory=list)
    contact_sheet: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _snapshot_settings(*, organization: str, application: str) -> dict[str, object]:
    settings = QtCore.QSettings(organization, application)
    snapshot: dict[str, object] = {}
    for key in settings.allKeys():
        snapshot[key] = settings.value(key)
    return snapshot


def _restore_settings(
    *,
    organization: str,
    application: str,
    snapshot: dict[str, object],
) -> None:
    settings = QtCore.QSettings(organization, application)
    settings.clear()
    for key, value in snapshot.items():
        settings.setValue(key, value)
    settings.sync()


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    ensure_app_theme(app)
    return app


def _pump_events(app: QtWidgets.QApplication, iterations: int = 8) -> None:
    for _ in range(max(1, iterations)):
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)


def _activate_plugin(window: PyPlotWorkbench, plugin_name: str) -> PyPlotPlugin:
    combo = getattr(window, "_plotter_combo", None)
    if not isinstance(combo, QtWidgets.QComboBox):
        raise RuntimeError("PyPlot plugin combo box is unavailable.")
    index = combo.findText(plugin_name)
    if index < 0:
        raise RuntimeError(f"Plugin not found: {plugin_name}")
    combo.setCurrentIndex(index)
    plugin = window._current_plugin
    if plugin is None:
        raise RuntimeError(f"Failed to activate plugin: {plugin_name}")
    return plugin


def _close_window(window: PyPlotWorkbench, app: QtWidgets.QApplication) -> None:
    clear_dirty = getattr(window, "_clear_project_dirty", None)
    if callable(clear_dirty):
        with contextlib.suppress(Exception):
            clear_dirty()
    with contextlib.suppress(Exception):
        setattr(window, "_project_dirty", False)
    tracked = getattr(window, "_open_windows", None)
    if isinstance(tracked, list):
        tracked.clear()
    with contextlib.suppress(Exception):
        window.close()
    _pump_events(app, iterations=4)


def _reset_origin() -> None:
    try:
        with origin_session(keep_open=False):
            return
    except Exception:
        return


def _export_shared_origin(
    window: PyPlotWorkbench,
    plugin_name: str,
    output_dir: Path,
) -> tuple[int, int, list[str], list[str], list[str]]:
    images: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    window._prune_shared_plot_workbooks()
    workbooks = window._shared_plot_workbooks_for_plugin(plugin_name)
    if not workbooks:
        return 0, 0, images, warnings, ["No shared plot workbooks available."]

    try:
        exported, plotted, push_errors = window._push_workbooks_to_origin(
            workbooks,
            create_graphs=True,
            keep_origin_open=True,
        )
    except Exception as exc:
        return 0, 0, images, warnings, [str(exc)]

    errors.extend(str(item) for item in (push_errors or []))
    try:
        import originpro as op_module  # type: ignore
        graphs = list(op_module.graph_list("p"))
        for index, graph in enumerate(graphs, start=1):
            lname = getattr(graph, "lname", None) or getattr(graph, "name", None) or f"graph_{index}"
            path = output_dir / f"{index:02d}_{_safe_stem(str(lname))}.png"
            save_fig = getattr(graph, "save_fig", None)
            if not callable(save_fig):
                warnings.append(f"{plugin_name}: graph {lname} has no save_fig.")
                continue
            try:
                save_fig(str(path))
            except Exception as exc:
                warnings.append(f"{plugin_name}: save_fig failed for {lname}: {exc}")
                continue
            if path.exists():
                images.append(str(path))
    except Exception as exc:
        warnings.append(f"{plugin_name}: unable to enumerate/save Origin graphs after export: {exc}")
    return int(exported), int(plotted), images, warnings, errors


def _export_custom_origin(
    plugin: PyPlotPlugin,
    output_dir: Path,
) -> tuple[int, int, list[str], list[str], list[str]]:
    images: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    try:
        import originpro as op_module  # type: ignore
    except Exception as exc:
        return 0, 0, images, warnings, [str(exc)]
    original_exit = getattr(op_module, "exit", None)
    if callable(original_exit):
        try:
            setattr(op_module, "exit", lambda *args, **kwargs: None)
        except Exception:
            original_exit = None
    try:
        plugin.open_origin()
    except Exception as exc:
        if callable(original_exit):
            with contextlib.suppress(Exception):
                setattr(op_module, "exit", original_exit)
        return 0, 0, images, warnings, [str(exc)]
    if callable(original_exit):
        with contextlib.suppress(Exception):
            setattr(op_module, "exit", original_exit)

    try:
        with origin_session(keep_open=False) as op:
            graphs = list(op.graph_list("p"))
            for index, graph in enumerate(graphs, start=1):
                lname = getattr(graph, "lname", None) or getattr(graph, "name", None) or f"graph_{index}"
                path = output_dir / f"{index:02d}_{_safe_stem(str(lname))}.png"
                save_fig = getattr(graph, "save_fig", None)
                if not callable(save_fig):
                    warnings.append(f"{plugin.name}: graph {lname} has no save_fig.")
                    continue
                save_fig(str(path))
                if path.exists():
                    images.append(str(path))
                else:
                    warnings.append(f"{plugin.name}: save_fig did not produce {path.name}.")
    except Exception as exc:
        errors.append(str(exc))

    return len(images), len(images), images, warnings, errors


def _safe_stem(value: str, fallback: str = "graph") -> str:
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_")
    return stem or fallback


def _make_contact_sheet(image_paths: list[str], output_path: Path, *, columns: int = 2) -> str | None:
    if not image_paths:
        return None
    thumbs: list[tuple[Image.Image, str]] = []
    thumb_w = 560
    thumb_h = 360
    caption_h = 40
    padding = 20
    for image_path in image_paths:
        path = Path(image_path)
        if not path.exists():
            continue
        with Image.open(path) as original:
            image = original.convert("RGB")
            image.thumbnail((thumb_w, thumb_h))
            thumbs.append((image.copy(), path.stem))
    if not thumbs:
        return None
    rows = (len(thumbs) + columns - 1) // columns
    sheet_w = columns * (thumb_w + padding) + padding
    sheet_h = rows * (thumb_h + caption_h + padding) + padding
    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (thumb, caption) in enumerate(thumbs):
        row = index // columns
        col = index % columns
        x = padding + col * (thumb_w + padding)
        y = padding + row * (thumb_h + caption_h + padding)
        image_x = x + (thumb_w - thumb.width) // 2
        image_y = y + (thumb_h - thumb.height) // 2
        sheet.paste(thumb, (image_x, image_y))
        draw.rectangle((x, y, x + thumb_w, y + thumb_h), outline="#999999", width=1)
        draw.text((x, y + thumb_h + 10), caption[:68], fill="black")
    sheet.save(output_path)
    return str(output_path)


def _first_files(root: Path, pattern: str, count: int) -> list[Path]:
    return sorted(root.rglob(pattern))[:count]


def _setup_folder_import(window: PyPlotWorkbench, folder: Path) -> PyPlotPlugin:
    resolved = folder.resolve()
    window._select_directories = lambda _parent=None, *, title, start_dir: [str(resolved)]  # type: ignore[assignment]
    window._import_data_from_folder()
    plugin = window._current_plugin
    if plugin is None:
        raise RuntimeError(f"Folder import did not activate a plugin for {resolved}")
    plugin.generate()
    return plugin


def _setup_selected_paths(window: PyPlotWorkbench, paths: list[Path]) -> PyPlotPlugin:
    plugin = window._current_plugin
    if plugin is None:
        raise RuntimeError("No active plugin after selected-path setup.")
    resolved = [path.resolve() for path in paths]
    window._commit_selected_paths(resolved)
    loader = getattr(plugin, "load_data", None)
    if callable(loader):
        loader()
    generator = getattr(plugin, "generate", None)
    if callable(generator):
        generator()
    return plugin


def _setup_generated_hsw_distribution(window: PyPlotWorkbench, temp_dir: Path) -> PyPlotPlugin:
    paths: list[Path] = []
    for suffix in ("A", "B"):
        path = temp_dir / f"dist_{suffix}.txt"
        path.write_text("1,0;2,0\n1,1;2,1\n1,2;2,2\n1,3;2,3\n", encoding="utf-8")
        paths.append(path)
    return _setup_selected_paths(window, paths)


def _setup_generated_hsw_load_compare(window: PyPlotWorkbench, temp_dir: Path) -> PyPlotPlugin:
    paths: list[Path] = []
    for load in ("2,5", "5", "7,5"):
        path = temp_dir / f"FeSiB 85_10 s2-2a 47mA {load}a.txt"
        path.write_text("1,0;2,0\n1,1;2,1\n1,2;2,2\n", encoding="utf-8")
        paths.append(path)
    return _setup_selected_paths(window, paths)


def _setup_generated_strain_3d(window: PyPlotWorkbench, temp_dir: Path) -> PyPlotPlugin:
    source = temp_dir / "strain_input.xlsx"
    pd.DataFrame(
        {
            "Composition": ["Ni50Fe27Ga23", "Ni50Fe27Ga23", "Ni50Fe27Ga23"],
            "Microwire": ["5_4", "5_5", "5_6"],
            "Strain (%)": [1.0, 2.0, 3.0],
            "Temperature (C)": [20.0, 40.0, 60.0],
            "Stress (MPa)": [100.0, 120.0, 140.0],
        }
    ).to_excel(source, index=False)
    return _setup_selected_paths(window, [source])


def _setup_hysteresis_loops(window: PyPlotWorkbench) -> PyPlotPlugin:
    files = sorted((ROOT / "sample_data" / "hysteresis_loops").glob("*.dat"))[:2]
    if not files:
        raise RuntimeError("No hysteresis loop sample files found.")
    plugin = _setup_selected_paths(window, files)
    mode_combo = getattr(plugin, "_mode_combo", None)
    if isinstance(mode_combo, QtWidgets.QComboBox):
        mode_combo.setCurrentText("Combined")
    plugin.generate()
    return plugin


def _setup_current_annealing(window: PyPlotWorkbench) -> PyPlotPlugin:
    files = sorted((ROOT / "sample_data" / "current_annealing").glob("*.txt"))[:1]
    if not files:
        raise RuntimeError("No current annealing sample files found.")
    return _setup_selected_paths(window, files)


def _setup_shape_memory(window: PyPlotWorkbench) -> PyPlotPlugin:
    files = sorted((ROOT / "sample_data" / "manual_stress-strain").glob("*.txt"))[:2]
    if len(files) < 2:
        raise RuntimeError("Not enough shape-memory sample files found.")
    return _setup_selected_paths(window, files)


def _setup_vsm_hysteresis(window: PyPlotWorkbench) -> PyPlotPlugin:
    files = _first_files(ROOT / "sample_data" / "VSM_data" / "vsm_hyst_loops", "*.VSM-HYS-DATA", 4)
    if not files:
        raise RuntimeError("No VSM hysteresis sample files found.")
    return _setup_selected_paths(window, files)


def _setup_vsm_temp_scan(window: PyPlotWorkbench) -> PyPlotPlugin:
    files = _first_files(ROOT / "sample_data" / "VSM_data" / "vsm_temperature_scan", "*.VSM-TSCN-DATA", 4)
    if not files:
        raise RuntimeError("No VSM temperature-scan sample files found.")
    return _setup_selected_paths(window, files)


def _setup_vsm_isotherms(window: PyPlotWorkbench) -> PyPlotPlugin:
    files = _first_files(ROOT / "sample_data" / "VSM_data" / "vsm_isotherms", "*.VSM-VIR-DATA", 4)
    if not files:
        raise RuntimeError("No VSM isotherm sample files found.")
    return _setup_selected_paths(window, files)


def _setup_dma(window: PyPlotWorkbench) -> PyPlotPlugin:
    files = sorted((ROOT / "sample_data" / "DMA").glob("*.txt"))[:1]
    if not files:
        raise RuntimeError("No DMA sample files found.")
    return _setup_selected_paths(window, files)


def _setup_fmr(window: PyPlotWorkbench) -> PyPlotPlugin:
    files = sorted((ROOT / "sample_data" / "FMR").glob("*.csv"))[:1]
    if not files:
        raise RuntimeError("No FMR sample files found.")
    return _setup_selected_paths(window, files)


PLUGIN_SPECS: list[tuple[str, str, Callable[[PyPlotWorkbench, Path], PyPlotPlugin]]] = [
    ("Temperature Dependence", "shared", lambda w, t: _setup_folder_import(w, ROOT / "sample_data" / "temperature_dependence")),
    ("Temperature Sensitivity", "custom", lambda w, t: _setup_folder_import(w, ROOT / "sample_data" / "temperature_dependence")),
    ("Stress Dependence", "shared", lambda w, t: _setup_folder_import(w, ROOT / "sample_data" / "stress_dependence")),
    ("Stress Sensitivity", "custom", lambda w, t: _setup_folder_import(w, ROOT / "sample_data" / "stress_dependence")),
    ("Shape Memory Stress/Strain", "shared", lambda w, t: _setup_shape_memory(w)),
    ("Hysteresis Loops", "shared", lambda w, t: _setup_hysteresis_loops(w)),
    ("Hsw Distribution", "custom", lambda w, t: _setup_generated_hsw_distribution(w, t)),
    ("Hsw Load Compare", "shared", lambda w, t: _setup_generated_hsw_load_compare(w, t)),
    ("Maxion Continuous", "custom", lambda w, t: _setup_selected_paths(w, [ROOT / "sample_data" / "Maxion" / "1 final 2 coils.txt"])),
    ("PDF Plotter", "custom", lambda w, t: _setup_selected_paths(w, [ROOT / "sample_data" / "pdf_data" / "sample1.pdf"])),
    ("Strain 3D Plot", "shared", lambda w, t: _setup_generated_strain_3d(w, t)),
    ("Current Annealing", "shared", lambda w, t: _setup_current_annealing(w)),
    ("VSM Hysteresis Loops", "shared", lambda w, t: _setup_vsm_hysteresis(w)),
    ("VSM Temperature Scan", "custom", lambda w, t: _setup_vsm_temp_scan(w)),
    ("VSM Isotherms", "shared", lambda w, t: _setup_vsm_isotherms(w)),
    ("DMA Iso-Stress", "shared", lambda w, t: _setup_dma(w)),
    ("FMR", "custom", lambda w, t: _setup_fmr(w)),
]


def verify_plugins(
    output_root: Path = ARTIFACTS_ROOT,
    *,
    plugin_filter: set[str] | None = None,
) -> list[PluginVerificationResult]:
    app = _ensure_app()
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[PluginVerificationResult] = []
    settings_snapshot = _snapshot_settings(
        organization="MicrowireLab",
        application="PyPlotWorkbench",
    )
    settings = QtCore.QSettings("MicrowireLab", "PyPlotWorkbench")
    settings.clear()
    settings.sync()
    specs = [
        spec
        for spec in PLUGIN_SPECS
        if plugin_filter is None or spec[0] in plugin_filter
    ]
    try:
        for plugin_name, route, setup in specs:
            plugin_dir = output_root / _safe_stem(plugin_name)
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)
            plugin_dir.mkdir(parents=True, exist_ok=True)
            _reset_origin()
            window = PyPlotWorkbench(initial_plotter=plugin_name)
            temp_dir = plugin_dir / "inputs"
            temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                plugin = _activate_plugin(window, plugin_name)
                plugin.settings_widget()
                plugin = setup(window, temp_dir)
                _pump_events(app, iterations=12)
                plot_tabs = len(getattr(plugin, "_plot_tabs", []) or [])
                result = PluginVerificationResult(
                    plugin=plugin_name,
                    route=route,
                    shared_workbooks=bool(getattr(plugin, "uses_shared_plot_workbooks", True)),
                    plot_tabs=plot_tabs,
                )
                if route == "shared":
                    exported, plotted, images, warnings, errors = _export_shared_origin(window, plugin_name, plugin_dir)
                else:
                    exported, plotted, images, warnings, errors = _export_custom_origin(plugin, plugin_dir)
                result.exported = exported
                result.plotted = plotted
                result.images = images
                result.warnings.extend(warnings)
                result.errors.extend(errors)
                result.contact_sheet = _make_contact_sheet(images, plugin_dir / "contact_sheet.png")
                results.append(result)
            except Exception as exc:
                results.append(
                    PluginVerificationResult(
                        plugin=plugin_name,
                        route=route,
                        shared_workbooks=False,
                        plot_tabs=0,
                        errors=[str(exc)],
                    )
                )
            finally:
                _close_window(window, app)
    finally:
        _restore_settings(
            organization="MicrowireLab",
            application="PyPlotWorkbench",
            snapshot=settings_snapshot,
        )
    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps([asdict(item) for item in results], indent=2), encoding="utf-8")
    overall_sheet_inputs = [item.images[0] for item in results if item.images]
    _make_contact_sheet(overall_sheet_inputs, output_root / "overall_contact_sheet.png", columns=3)
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Origin export screenshots for PyPlot plugins.")
    parser.add_argument(
        "--plugin",
        action="append",
        dest="plugins",
        help="Limit verification to a single plugin name. Repeat for multiple plugins.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ARTIFACTS_ROOT,
        help="Artifact output directory (default: artifacts/origin-plugin-verify).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    plugin_filter = set(args.plugins or []) or None
    results = verify_plugins(args.output_dir, plugin_filter=plugin_filter)
    failures = 0
    for item in results:
        status = "OK" if not item.errors else "ERROR"
        print(f"[{status}] {item.plugin}: route={item.route} plots={item.plot_tabs} origin={len(item.images)}")
        for warning in item.warnings:
            print(f"  warning: {warning}")
        for error in item.errors:
            print(f"  error: {error}")
        if item.errors:
            failures += 1
    print(f"Summary: {len(results) - failures} ok, {failures} failed")
    print(f"Artifacts: {ARTIFACTS_ROOT}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
