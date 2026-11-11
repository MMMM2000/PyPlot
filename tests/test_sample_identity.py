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
