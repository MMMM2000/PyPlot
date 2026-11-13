from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from data_logging.sample_identity import IdentityConflict, PhysicalSample, SampleRegistry, sample_number


@pytest.mark.parametrize("label, expected", [("s1", 1), ("s999", 999), ("s0", None),
    ("s01", None), ("s2-1", None), ("", None), ("S1", None), ("s-1", None)])
def test_canonical_labels(label, expected):
    assert sample_number(label) == expected


def test_suggestion_does_not_allocate_and_never_recycles(tmp_path):
    registry = SampleRegistry(tmp_path / "samples.db")
    assert registry.suggested_label("Ni", "12/2") == "s1"
    assert registry.known("Ni", "12/2") == ()
    row = registry.create("Ni", "12/2", "s23", source="test bench")
    assert registry.known("Ni", "12/2") == (row,)
    assert registry.suggested_label("Ni", "12/2") == "s24"
    assert registry.suggested_label("Fe", "12/2") == "s1"
    assert registry.suggested_label("Ni", "5/1") == "s1"
    assert SampleRegistry(registry.path).known("Ni", "12/2") == (row,)
    assert registry.known("Ni", "12_2") == (row,)
    assert registry.known("Ni", "012 / 02") == (row,)
    with pytest.raises(IdentityConflict):
        registry.create("Ni", "12_2", "s23", source="another logger")


def test_new_piece_and_remeasurement_are_distinct(tmp_path):
    registry = SampleRegistry(tmp_path / "samples.db")
    parent = registry.create("Ni", "12/2", "s1", source="bench")
    child = registry.create("Ni", "12/2", "s2", source="bench", parent_uid=parent.uid)
    assert child.parent_uid == parent.uid
    with pytest.raises(IdentityConflict):
        registry.create("Ni", "12/2", "s1", source="remeasurement")
    with pytest.raises(ValueError):
        registry.create("Fe", "12/2", "s3", source="bench", parent_uid=parent.uid)
    assert len(registry.known("Ni", "12/2")) == 2


def test_offline_collision_is_preserved_not_renumbered(tmp_path):
    first, second = [SampleRegistry(tmp_path / name) for name in ("a.db", "b.db")]
    a = first.create("Ni", "12/2", "s1", source="bench A")
    b = second.create("Ni", "12/2", "s1", source="bench B")
    first.merge((b,))
    assert set(first.conflicts("Ni", "12/2")["s1"]) == {a, b}
    first.merge((b,))
    assert len(first.known("Ni", "12/2")) == 2
    assert second.known("Ni", "12/2") == (b,)


def test_legacy_labels_preserved_and_import_is_atomic(tmp_path):
    registry = SampleRegistry(tmp_path / "samples.db")
    old = PhysicalSample("legacy-id", "Ni", "12/2", "s2-1", "reviewed import")
    registry.merge((old,))
    assert registry.known("Ni", "12/2") == (old,)
    assert registry.suggested_label("Ni", "12/2") == "s1"
    with pytest.raises(IdentityConflict):
        registry.merge((replace(old, uid="new"), replace(old, label="s3")))
    assert registry.known("Ni", "12/2") == (old,)
    with pytest.raises(ValueError):
        registry.create("Ni", "12/2", "s2-1", source="bench")


def test_same_workstation_race_does_not_double_allocate(tmp_path):
    registry = SampleRegistry(tmp_path / "samples.db")
    def reserve(_):
        try:
            return registry.create("Ni", "12/2", "s1", source="bench")
        except IdentityConflict:
            return None
    with ThreadPoolExecutor(max_workers=4) as workers:
        outcomes = list(workers.map(reserve, range(8)))
    assert sum(row is not None for row in outcomes) == 1
    assert len(registry.known("Ni", "12/2")) == 1


def test_general_logger_accepts_new_and_legacy_labels(qtbot):
    from PyQt6.QtWidgets import QWidget, QLineEdit
    from data_logging.data_logger.file_name_builder import FileNameBuilderWidget
    parent = QWidget()
    qtbot.addWidget(parent)
    widget = FileNameBuilderWidget(parent, QLineEdit(parent))
    for field in (widget.s_number, widget.t_number):
        for label in ("s1", "s23", "s999", "s2-1"):
            assert field._pattern.fullmatch(label)
        for label in ("s0", "s01", "s"):
            assert not field._pattern.fullmatch(label)


def test_chooser_cancel_select_and_create_are_explicit(qtbot, tmp_path):
    from data_logging.sample_identity_ui import SampleChooser
    registry = SampleRegistry(tmp_path / "samples.db")
    cancelled = SampleChooser(registry, "Ni", "12/2")
    qtbot.addWidget(cancelled)
    assert cancelled.label.text() == "s1"
    cancelled.reject()
    assert registry.known("Ni", "12/2") == ()
    create = SampleChooser(registry, "Ni", "12/2")
    qtbot.addWidget(create)
    create._create()
    assert create.selected_label == "s1"
    existing = SampleChooser(registry, "Ni", "12/2")
    qtbot.addWidget(existing)
    existing.records.setCurrentRow(0)
    existing._select_existing()
    assert existing.selected_label == "s1"
    assert len(registry.known("Ni", "12/2")) == 1


def test_chooser_blocks_conflicting_import_and_stale_suggestion(qtbot, tmp_path):
    from data_logging.sample_identity_ui import SampleChooser
    registry = SampleRegistry(tmp_path / "samples.db")
    dialog = SampleChooser(registry, "Ni", "12/2")
    qtbot.addWidget(dialog)
    row = registry.create("Ni", "12/2", "s1", source="another logger")
    dialog._create()
    assert dialog.selected_label is None
    assert "already recorded" in dialog.error.text()
    stale_existing = SampleChooser(registry, "Ni", "12/2")
    qtbot.addWidget(stale_existing)
    stale_existing.records.setCurrentRow(0)
    registry.merge((replace(row, uid="another-physical-piece", source="offline bench"),))
    stale_existing._select_existing()
    assert stale_existing.selected_label is None
    assert "conflict" in stale_existing.error.text()
    conflict = SampleChooser(registry, "Ni", "12/2")
    qtbot.addWidget(conflict)
    conflict.records.setCurrentRow(0)
    conflict._select_existing()
    assert conflict.selected_label is None


def test_spinbox_adapter_emits_change_and_preserves_selected_uuid(qtbot, tmp_path, monkeypatch):
    from PyQt6 import QtCore, QtWidgets
    from data_logging.current_annealing_logger.ui_en import SampleSpinBox
    from data_logging import sample_identity_ui as ui
    spin = SampleSpinBox()
    qtbot.addWidget(spin)
    comp, wire = QtWidgets.QLineEdit("Ni"), QtWidgets.QLineEdit("12_2")
    qtbot.addWidget(comp); qtbot.addWidget(wire)
    monkeypatch.setattr(QtCore.QStandardPaths, "writableLocation", lambda _: str(tmp_path))
    def choose(dialog):
        dialog.label.setText("s23")
        dialog._create()
        return dialog.result()
    monkeypatch.setattr(ui.SampleChooser, "exec", choose)
    ui.attach_sample_chooser(spin, comp, wire)
    with qtbot.waitSignal(spin.textChanged, timeout=1000):
        next(action for action in spin.children() if getattr(action, "text", lambda: "")() == "Samples").trigger()
    assert spin.text() == "s23"
    metadata = ui.selection_metadata(spin, "Ni", "12/2", "s23")
    assert metadata["uid"]
    assert metadata["status"] == "explicitly_selected_local"
    assert "uid" not in ui.selection_metadata(spin, "Fe", "12/2", "s23")
    assert "uid" not in ui.selection_metadata(spin, "Ni", "12/2", "s24")


def test_adapter_bounds_checked_before_registration(qtbot, tmp_path):
    from data_logging.sample_identity_ui import SampleChooser
    registry = SampleRegistry(tmp_path / "samples.db")
    chooser = SampleChooser(registry, "Ni", "12/2", accepts_label=lambda label: label == "s1")
    qtbot.addWidget(chooser)
    chooser.label.setText("s9999999999")
    chooser._create()
    assert registry.known("Ni", "12/2") == ()
    assert chooser.selected_record is None


def test_exchange_roundtrip_preserves_uuid_and_detects_offline_collisions(tmp_path):
    first, second = [SampleRegistry(tmp_path / name) for name in ("first.db", "second.db")]
    original = first.create("Demo", "12/2", "s1", source="first")
    second.create("Demo", "12/2", "s1", source="second")
    records = SampleRegistry.parse_snapshot(first.snapshot())
    assert records == (original,)
    second.merge(records)
    assert len(second.conflicts("Demo", "12/2")["s1"]) == 2
    second.merge(records)
    assert len(second.known("Demo", "12/2")) == 2


@pytest.mark.parametrize("payload", ['{}', '{"schema_version":2}', 'null', '[1]',
    '{"schema_version":1,"application":"PyPlot physical samples","records":[{}]}'])
def test_invalid_exchange_does_not_become_registry_records(payload):
    with pytest.raises(ValueError):
        SampleRegistry.parse_snapshot(payload)


@pytest.mark.parametrize("field_index", [0, 1, 2])
def test_identity_does_not_revive_after_editing_back(qtbot, field_index):
    from PyQt6 import QtWidgets
    from data_logging.sample_identity_ui import attach_sample_chooser, selection_metadata
    fields = [QtWidgets.QLineEdit(value) for value in ("s1", "Ni", "12/2")]
    for field in fields:
        qtbot.addWidget(field)
    sample, comp, wire = fields
    attach_sample_chooser(sample, comp, wire)
    sample.setProperty("physicalSample", dict(uid="piece-A", composition="Ni", wire="12/2", label="s1"))
    original = fields[field_index].text()
    fields[field_index].setText("different")
    fields[field_index].setText(original)
    assert "uid" not in selection_metadata(sample, comp.text(), wire.text(), sample.text())


def test_spinbox_same_label_restore_clears_uuid_even_with_signals_blocked(qtbot):
    from data_logging.current_annealing_logger.ui_en import SampleSpinBox
    from data_logging.sample_identity_ui import selection_metadata
    sample = SampleSpinBox()
    qtbot.addWidget(sample)
    sample.setText("s1")
    sample.setProperty("physicalSample", dict(uid="piece-A", composition="Ni", wire="12/2", label="s1"))
    sample.blockSignals(True)
    sample.setText("s1")
    assert sample.signalsBlocked()
    assert "uid" not in selection_metadata(sample, "Ni", "12/2", "s1")


def test_tma_same_label_project_restore_clears_uuid_with_blocked_signals(qtbot):
    from pathlib import Path
    from types import SimpleNamespace
    from PyQt6 import QtWidgets
    from data_logging.mini_dma_logger.mini_dma_logger import MainWindow
    from data_logging.sample_identity_ui import selection_metadata
    sample, comp, wire = [QtWidgets.QLineEdit(value) for value in ("s1", "Ni", "12/2")]
    for field in (sample, comp, wire):
        qtbot.addWidget(field)
        field.blockSignals(True)
    sample.setProperty("physicalSample", dict(uid="piece-A", composition="Ni", wire="12/2", label="s1"))
    view = SimpleNamespace(edit_name_specimen=sample, edit_name_composition=comp,
                           edit_name_wire=wire, label_project_status=QtWidgets.QLabel(),
                           _apply_fallback_diameter_if_possible=lambda **kwargs: True,
                           _refresh_recipe_sample_label=lambda: None,
                           _refresh_equivalent_labels=lambda: None)
    match = SimpleNamespace(path=Path("synthetic.pydpj"), matched_row={"Composition": "Ni", "Microwire": "12/2", "Specimen": "s1"},
                            diameter_mm=None, current_mA=None, section="synthetic")
    MainWindow._apply_project_match(view, match, update_identity=True, quiet=True)
    assert "uid" not in selection_metadata(sample, "Ni", "12/2", "s1")
