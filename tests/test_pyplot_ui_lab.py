from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from plotting.ui_lab import LAUNCHER_VARIANTS, LauncherPrototype, PlotWorkspacePrototype


def test_launcher_filters_tools_and_reports_empty_state(qtbot) -> None:
    launcher = LauncherPrototype()
    qtbot.addWidget(launcher)
    launcher.show()

    launcher.search.setText("annealing")
    assert launcher.tools.count() == 1
    assert launcher.selected_tool() is not None

    launcher.search.setText("definitely absent")
    assert launcher.tools.count() == 0
    assert launcher.selection_summary.text() == "No matching tools"
    assert not launcher.open_button.isEnabled()


def test_launcher_variants_use_their_review_sizes_and_plotting_category(qtbot) -> None:
    for key, variant in LAUNCHER_VARIANTS.items():
        launcher = LauncherPrototype(variant=key)
        qtbot.addWidget(launcher)
        launcher.show()

        assert launcher.size() == variant.size
        assert launcher.categories.tabText(launcher.categories.currentIndex()) == "Plotting"
        assert launcher.tools.count() >= 20
        assert launcher.selected_tool() is not None


def test_launcher_opens_real_workspace_for_selected_plot(qtbot) -> None:
    launcher = LauncherPrototype()
    qtbot.addWidget(launcher)
    launcher.show()

    launcher.search.setText("Current Annealing")
    workspace = launcher.open_selected()

    assert isinstance(workspace, PlotWorkspacePrototype)
    qtbot.addWidget(workspace)
    assert workspace.isVisible()
    assert workspace.mdi.subWindowList()
    qtbot.wait(50)
    workspace.close()
    qtbot.wait(20)


def test_graph_defaults_to_movable_half_width_window(qtbot) -> None:
    workspace = PlotWorkspacePrototype()
    qtbot.addWidget(workspace)
    # Roughly the logical workspace available in a fixed physical viewport at
    # 150% Windows scaling.
    workspace.resize(1000, 667)
    workspace.show()
    qtbot.waitUntil(lambda: workspace.mdi.viewport().width() > 400)
    qtbot.wait(20)

    graph = workspace.mdi.activeSubWindow()
    assert graph is not None
    assert not workspace.properties_dock.isVisible()
    ratio = graph.width() / workspace.mdi.viewport().width()
    assert 0.45 <= ratio <= 0.60
    assert not graph.isMaximized()
    assert graph.windowFlags() & QtCore.Qt.WindowType.SubWindow


def test_active_graph_can_toggle_maximized_and_restore(qtbot) -> None:
    workspace = PlotWorkspacePrototype()
    qtbot.addWidget(workspace)
    workspace.show()
    graph = workspace.mdi.activeSubWindow()
    assert graph is not None

    workspace.toggle_active_graph_maximized()
    qtbot.waitUntil(graph.isMaximized)
    workspace.toggle_active_graph_maximized()
    qtbot.waitUntil(lambda: not graph.isMaximized())


def test_background_task_is_timer_driven_and_finishes(qtbot) -> None:
    workspace = PlotWorkspacePrototype()
    qtbot.addWidget(workspace)
    workspace.show()

    with qtbot.waitSignal(workspace.task_finished, timeout=2000):
        workspace.start_background_task(interval_ms=1)

    assert workspace.task_progress.value() == 100
    assert workspace.task_label.text() == "Ready"
    assert not workspace._task_timer.isActive()  # noqa: SLF001 - prototype contract
    assert workspace.mdi.isEnabled()


def test_core_controls_expose_accessible_names(qtbot) -> None:
    launcher = LauncherPrototype()
    workspace = PlotWorkspacePrototype()
    qtbot.addWidget(launcher)
    qtbot.addWidget(workspace)

    assert launcher.search.accessibleName()
    assert launcher.tools.accessibleName()
    assert launcher.open_button.accessibleName()
    assert workspace.mdi.accessibleName()
    assert workspace.task_progress.accessibleName()
