from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotting.pyplot.app import PyPlotWorkbench
from plotting.pyplot.window import OBJECT_TREE_STATE_ROLE
from plotting.shared.utils import ensure_app_theme

ARTIFACTS_ROOT = ROOT / "artifacts" / "pyplot-gui-audit"


@dataclass
class StageCapture:
    name: str
    image: str | None = None
    extra_images: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PluginCapture:
    plugin: str
    plot_button_text: str
    plot_enabled: bool
    open_origin_enabled: bool
    export_origin_enabled: bool
    export_txt_enabled: bool
    window_image: str | None = None
    panel_image: str | None = None
    settings_image: str | None = None
    notes: list[str] = field(default_factory=list)


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


def _safe_stem(value: str, fallback: str = "image") -> str:
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_")
    return stem or fallback


def _make_contact_sheet(image_paths: list[str], output_path: Path, *, columns: int = 3) -> str | None:
    if not image_paths:
        return None
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    thumbs: list[tuple[Any, str]] = []
    thumb_w = 520
    thumb_h = 320
    caption_h = 36
    padding = 16
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
    width = columns * (thumb_w + padding) + padding
    height = rows * (thumb_h + caption_h + padding) + padding
    sheet = Image.new("RGB", (width, height), "white")
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
        draw.text((x, y + thumb_h + 8), caption[:64], fill="black")
    sheet.save(output_path)
    return str(output_path)


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


def _capture_widget(
    widget: QtWidgets.QWidget | None,
    path: Path,
    *,
    min_width: int = 320,
    min_height: int = 180,
) -> str | None:
    if not isinstance(widget, QtWidgets.QWidget):
        return None
    pixmap = widget.grab()
    if pixmap.isNull() or pixmap.width() < 48 or pixmap.height() < 48:
        size = widget.size()
        hint = widget.sizeHint()
        min_hint = widget.minimumSizeHint()
        width = max(size.width(), hint.width(), min_hint.width(), min_width)
        height = max(size.height(), hint.height(), min_hint.height(), min_height)
        old_size = widget.size()
        old_visible = widget.isVisible()
        with contextlib.suppress(Exception):
            widget.resize(width, height)
        with contextlib.suppress(Exception):
            if not old_visible:
                widget.show()
        pixmap = QtGui.QPixmap(width, height)
        pixmap.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(pixmap)
        try:
            widget.render(painter)
        finally:
            painter.end()
        with contextlib.suppress(Exception):
            widget.resize(old_size)
        if not old_visible:
            with contextlib.suppress(Exception):
                widget.hide()
    if pixmap.isNull():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(path))
    return str(path)


def _activate_plugin(window: PyPlotWorkbench, plugin_name: str) -> None:
    combo = getattr(window, "_plotter_combo", None)
    if not isinstance(combo, QtWidgets.QComboBox):
        raise RuntimeError("Plugin selector is unavailable.")
    index = combo.findText(plugin_name)
    if index < 0:
        raise RuntimeError(f"Plugin not found: {plugin_name}")
    combo.setCurrentIndex(index)


def _collect_plugin_names(window: PyPlotWorkbench) -> list[str]:
    combo = getattr(window, "_plotter_combo", None)
    if not isinstance(combo, QtWidgets.QComboBox):
        return []
    names: list[str] = []
    for index in range(combo.count()):
        data = combo.itemData(index)
        if isinstance(data, str) and data.strip():
            names.append(data)
    return names


def _find_object_tree_line_item(tree: QtWidgets.QTreeWidget) -> QtWidgets.QTreeWidgetItem | None:
    def walk(item: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem | None:
        for idx in range(item.childCount()):
            child = item.child(idx)
            data = child.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "line":
                return child
            found = walk(child)
            if found is not None:
                return found
        return None

    for index in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(index)
        if top is None:
            continue
        found = walk(top)
        if found is not None:
            return found
    return None


def _stage_capture(
    captures: list[StageCapture],
    *,
    name: str,
    widget: QtWidgets.QWidget | None,
    output_dir: Path,
    extra_widgets: list[tuple[str, QtWidgets.QWidget | None]] | None = None,
    notes: list[str] | None = None,
) -> None:
    image_path = _capture_widget(widget, output_dir / f"{len(captures) + 1:02d}_{_safe_stem(name)}.png")
    result = StageCapture(name=name, image=image_path, notes=list(notes or []))
    for label, extra_widget in extra_widgets or []:
        extra_path = _capture_widget(
            extra_widget,
            output_dir / f"{len(captures) + 1:02d}_{_safe_stem(name)}_{_safe_stem(label)}.png",
        )
        if extra_path:
            result.extra_images.append(extra_path)
    captures.append(result)


def _run_shared_audit(window: PyPlotWorkbench, app: QtWidgets.QApplication, output_dir: Path) -> list[StageCapture]:
    captures: list[StageCapture] = []
    window.resize(1680, 1040)
    window.show()
    window.activateWindow()
    _pump_events(app, iterations=10)

    _stage_capture(
        captures,
        name="initial_window",
        widget=window,
        output_dir=output_dir,
        extra_widgets=[("project_tree", getattr(window, "project_tree", None))],
    )

    show_data_menu = getattr(window, "_show_data_menu", None)
    if callable(show_data_menu):
        show_data_menu()
        _pump_events(app, iterations=4)
        _stage_capture(
            captures,
            name="data_menu",
            widget=getattr(window, "_data_menu", None),
            output_dir=output_dir,
            notes=["Shared Data menu opened."],
        )
        data_menu = getattr(window, "_data_menu", None)
        if isinstance(data_menu, QtWidgets.QMenu):
            data_menu.hide()
            _pump_events(app, iterations=2)

    new_workbook = getattr(window, "_create_new_workbook", None)
    if callable(new_workbook):
        new_workbook()
        _pump_events(app, iterations=6)
        _stage_capture(
            captures,
            name="new_workbook",
            widget=window,
            output_dir=output_dir,
            extra_widgets=[("project_tree", getattr(window, "project_tree", None))],
            notes=["Created a manual workbook and opened its worksheet tab."],
        )

    project_search = getattr(window, "project_tree_search", None)
    if isinstance(project_search, QtWidgets.QLineEdit):
        project_search.clear()
        project_search.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        QtTest.QTest.keyClicks(project_search, "workbook")
        _pump_events(app, iterations=4)
        _stage_capture(
            captures,
            name="project_tree_search",
            widget=getattr(window, "project_tree", None),
            output_dir=output_dir,
            notes=["Filtered the Project Explorer with a search query."],
        )
        project_search.clear()
        _pump_events(app, iterations=2)

    create_blank = getattr(window, "_create_blank_graph", None)
    if callable(create_blank):
        create_blank()
        _pump_events(app, iterations=6)
        axes = window._current_axes()
        if axes is not None:
            axes.plot([0.0, 1.0, 2.0], [1.0, 2.0, 1.5], label="Series A")
            axes.plot([0.0, 1.0, 2.0], [2.0, 1.5, 2.3], label="Series B")
            axes.legend(loc="best")
            try:
                axes.figure.canvas.draw()
            except Exception:
                pass
        current_tab = window.tab_widget.currentWidget()
        if isinstance(current_tab, QtWidgets.QWidget):
            with contextlib.suppress(Exception):
                window._rebuild_object_manager_for_tab(current_tab)
        _pump_events(app, iterations=6)
        _stage_capture(
            captures,
            name="blank_graph_with_object_manager",
            widget=window,
            output_dir=output_dir,
            extra_widgets=[("object_tree", getattr(window, "object_tree", None))],
        )

    object_tree = getattr(window, "object_tree", None)
    if isinstance(object_tree, QtWidgets.QTreeWidget):
        line_item = _find_object_tree_line_item(object_tree)
        if line_item is not None:
            line_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
            _pump_events(app, iterations=4)
            _stage_capture(
                captures,
                name="object_manager_toggle_hidden",
                widget=window,
                output_dir=output_dir,
                extra_widgets=[("object_tree", object_tree)],
                notes=["Toggled a plotted line off from the Object Manager."],
            )
            object_tree = getattr(window, "object_tree", None)
            if isinstance(object_tree, QtWidgets.QTreeWidget):
                refreshed_item = _find_object_tree_line_item(object_tree)
                if refreshed_item is not None:
                    refreshed_item.setCheckState(0, QtCore.Qt.CheckState.Checked)
                    _pump_events(app, iterations=3)

    open_graph_format = getattr(window, "_open_graph_format_dialog", None)
    if callable(open_graph_format):
        opened = False
        with contextlib.suppress(Exception):
            opened = bool(open_graph_format())
        _pump_events(app, iterations=4)
        if opened:
            dialog = getattr(window, "_graph_format_dialog", None)
            _stage_capture(
                captures,
                name="graph_format_dialog",
                widget=dialog if isinstance(dialog, QtWidgets.QWidget) else window,
                output_dir=output_dir,
                notes=["Opened the shared Graph Formatting dialog."],
            )
            if isinstance(dialog, QtWidgets.QDialog):
                dialog.close()
                _pump_events(app, iterations=2)

    begin_progress = getattr(window, "_begin_task_progress", None)
    update_progress = getattr(window, "_update_task_progress", None)
    end_progress = getattr(window, "_end_task_progress", None)
    if callable(begin_progress) and callable(update_progress) and callable(end_progress):
        begin_progress("GUI audit progress...", maximum=5, value=1)
        update_progress(value=3, maximum=5, title="Running shared GUI checks...")
        _pump_events(app, iterations=4)
        _stage_capture(
            captures,
            name="task_progress_popup",
            widget=getattr(window, "_task_progress_dialog", None),
            output_dir=output_dir,
            notes=["Displayed the shared popup progress indicator."],
        )
        end_progress()
        _pump_events(app, iterations=2)

    if callable(create_blank):
        create_blank()
        create_blank()
        _pump_events(app, iterations=6)
        targets_getter = getattr(window, "_window_menu_arrangement_targets", None)
        cascade = getattr(window, "_window_menu_cascade", None)
        tile = getattr(window, "_window_menu_tile", None)
        targets = targets_getter() if callable(targets_getter) else []
        if callable(tile):
            with contextlib.suppress(Exception):
                tile(targets, orientation="vertical")
            _pump_events(app, iterations=6)
            _stage_capture(
                captures,
                name="window_tile_vertical",
                widget=window,
                output_dir=output_dir,
                notes=["Applied the shared vertical tiling arrangement."],
            )
            with contextlib.suppress(Exception):
                tile(targets, orientation="horizontal")
            _pump_events(app, iterations=6)
            _stage_capture(
                captures,
                name="window_tile_horizontal",
                widget=window,
                output_dir=output_dir,
                notes=["Applied the shared horizontal tiling arrangement."],
            )
        if callable(cascade):
            with contextlib.suppress(Exception):
                cascade(targets)
            _pump_events(app, iterations=6)
            _stage_capture(
                captures,
                name="window_cascade",
                widget=window,
                output_dir=output_dir,
                notes=["Applied the shared cascade arrangement."],
            )

    return captures


def _run_plugin_audit(window: PyPlotWorkbench, app: QtWidgets.QApplication, output_dir: Path) -> list[PluginCapture]:
    captures: list[PluginCapture] = []
    with contextlib.suppress(Exception):
        setattr(window, "_prompt_plugin_window_choice", lambda _name: False)
    for plugin_name in _collect_plugin_names(window):
        notes: list[str] = []
        try:
            _activate_plugin(window, plugin_name)
            _pump_events(app, iterations=6)
        except Exception as exc:
            captures.append(
                PluginCapture(
                    plugin=plugin_name,
                    plot_button_text="",
                    plot_enabled=False,
                    open_origin_enabled=False,
                    export_origin_enabled=False,
                    export_txt_enabled=False,
                    notes=[f"Activation failed: {exc}"],
                )
            )
            continue

        plot_action = getattr(window, "plot_button", None)
        open_origin_action = getattr(window, "open_origin_button", None)
        export_origin_action = getattr(window, "export_origin_button", None)
        export_txt_action = getattr(window, "export_button", None)
        plot_text = plot_action.text() if isinstance(plot_action, QtGui.QAction) else ""
        capture = PluginCapture(
            plugin=plugin_name,
            plot_button_text=str(plot_text or ""),
            plot_enabled=bool(plot_action.isEnabled()) if isinstance(plot_action, QtGui.QAction) else False,
            open_origin_enabled=bool(open_origin_action.isEnabled()) if isinstance(open_origin_action, QtGui.QAction) else False,
            export_origin_enabled=bool(export_origin_action.isEnabled()) if isinstance(export_origin_action, QtGui.QAction) else False,
            export_txt_enabled=bool(export_txt_action.isEnabled()) if isinstance(export_txt_action, QtGui.QAction) else False,
        )
        capture.window_image = _capture_widget(
            window,
            output_dir / f"{len(captures) + 1:02d}_{_safe_stem(plugin_name)}_window.png",
        )
        plugin = getattr(window, "_current_plugin", None)
        panel_widget = None
        settings_widget = None
        if plugin is not None:
            with contextlib.suppress(Exception):
                panel_widget = plugin.panel_widget()
            with contextlib.suppress(Exception):
                settings_widget = plugin.settings_widget()
        if isinstance(panel_widget, QtWidgets.QWidget):
            capture.panel_image = _capture_widget(
                panel_widget,
                output_dir / f"{len(captures) + 1:02d}_{_safe_stem(plugin_name)}_panel.png",
                min_width=420,
                min_height=260,
            )
        if isinstance(settings_widget, QtWidgets.QWidget):
            capture.settings_image = _capture_widget(
                settings_widget,
                output_dir / f"{len(captures) + 1:02d}_{_safe_stem(plugin_name)}_settings.png",
                min_width=460,
                min_height=300,
            )
        capture.notes.extend(notes)
        captures.append(capture)
    return captures


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


def run_audit(output_root: Path) -> dict[str, Any]:
    app = _ensure_app()
    output_root.mkdir(parents=True, exist_ok=True)
    settings_snapshot = _snapshot_settings(
        organization="MicrowireLab",
        application="PyPlotWorkbench",
    )
    window = PyPlotWorkbench()
    summary: dict[str, Any] = {
        "shared_stages": [],
        "plugins": [],
        "stage_contact_sheet": None,
        "plugin_contact_sheet": None,
    }
    try:
        shared_dir = output_root / "shared"
        plugin_dir = output_root / "plugins"
        shared_dir.mkdir(parents=True, exist_ok=True)
        plugin_dir.mkdir(parents=True, exist_ok=True)

        shared_stages = _run_shared_audit(window, app, shared_dir)
        plugin_captures = _run_plugin_audit(window, app, plugin_dir)

        summary["shared_stages"] = [asdict(item) for item in shared_stages]
        summary["plugins"] = [asdict(item) for item in plugin_captures]

        shared_images: list[str] = []
        for item in shared_stages:
            if item.image:
                shared_images.append(item.image)
            shared_images.extend(item.extra_images)
        plugin_images = [
            image
            for item in plugin_captures
            for image in (item.window_image, item.panel_image, item.settings_image)
            if image
        ]

        summary["stage_contact_sheet"] = _make_contact_sheet(
            shared_images,
            output_root / "shared_contact_sheet.png",
            columns=2,
        )
        summary["plugin_contact_sheet"] = _make_contact_sheet(
            plugin_images,
            output_root / "plugin_contact_sheet.png",
            columns=3,
        )
        summary_path = output_root / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        summary["summary_json"] = str(summary_path)
        return summary
    finally:
        _close_window(window, app)
        _restore_settings(
            organization="MicrowireLab",
            application="PyPlotWorkbench",
            snapshot=settings_snapshot,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit shared PyPlot GUI flows and capture screenshots.")
    parser.add_argument(
        "--output-root",
        default=str(ARTIFACTS_ROOT),
        help="Directory for temporary GUI-audit screenshots.",
    )
    args = parser.parse_args()
    output_root = Path(args.output_root).resolve()
    if output_root.exists():
        shutil.rmtree(output_root)
    summary = run_audit(output_root)
    print(f"Shared stages: {len(summary.get('shared_stages', []))}")
    print(f"Plugins audited: {len(summary.get('plugins', []))}")
    print(f"Artifacts: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
