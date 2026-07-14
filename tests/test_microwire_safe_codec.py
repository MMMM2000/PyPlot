from __future__ import annotations

import base64
import json
import logging
import os
import pickle
from types import SimpleNamespace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PyQt6 import QtWidgets

import launcher
from microwire_data_builder import safe_codec
from microwire_data_builder import storage as builder_storage
from microwire_data_builder import ui as builder_ui
from microwire_data_builder.core import MiniDmaRecord, VsmTemperatureScanRecord
from microwire_data_builder.legacy_migration import (
    migrate_legacy_project_trusted,
    migrate_legacy_store_trusted,
)


class _MaliciousPayload:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self):
        expression = (
            "__import__('pathlib').Path(" + repr(str(self.marker)) + ").write_text('executed')"
        )
        return eval, (expression,)


def _legacy_envelope(value: object) -> dict[str, str]:
    return {
        "encoding": "pickle-base64",
        "value": base64.b64encode(pickle.dumps(value)).decode("ascii"),
    }


def _reset_store_state() -> None:
    builder_storage.MiniDatabaseStore._memory_data = {}
    builder_storage.MiniDatabaseStore._memory_payloads = {}
    builder_storage.MiniDatabaseStore._pending_sections = set()
    builder_storage.MiniDatabaseStore._pending_payloads = set()
    builder_storage.MiniDatabaseStore._pending_section_values = {}
    builder_storage.MiniDatabaseStore._pending_payload_values = {}
    builder_storage.MiniDatabaseStore._memory_transactions = []
    builder_storage.MiniDatabaseStore._disk_writes_suspended = 0
    builder_storage.MiniDatabaseStore._discard_writes_depth = 0
    builder_storage.MiniDatabaseStore._blocked_sections = set()
    builder_storage.MiniDatabaseStore._blocked_payloads = set()


def test_ordinary_project_launcher_and_store_reads_never_execute_legacy_pickle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "executed.txt"
    encoded = _legacy_envelope(_MaliciousPayload(marker))
    project = tmp_path / "legacy.pydpj"
    project.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "sections": {
                    "mini_dma": {"payloads": {"mini_dma_records": encoded}},
                    "assemble": {
                        "columns": ["Composition", "Microwire"],
                        "rows": [{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    prepared = builder_ui._prepare_project_payload_for_gui(project)  # noqa: SLF001
    sections = prepared.payload["sections"]
    assert prepared.decoded_payload_count == 0
    assert prepared.diagnostics
    assert sections["assemble"]["rows"][0]["Microwire"] == "12/2"
    assert launcher._mini_dma_records_from_sections(sections) == []  # noqa: SLF001
    assert launcher._decode_word_project_payload(encoded) is None  # noqa: SLF001

    storage_root = tmp_path / "store"
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: storage_root)
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("mini_dma")
    store.meta_path.write_text(
        json.dumps({"sources": ["safe-source"], "processed": {}, "extra": {}}),
        encoding="utf-8",
    )
    store.legacy_table_path.write_bytes(pickle.dumps(_MaliciousPayload(marker)))
    store.legacy_payload_path("mini_dma_records").write_bytes(
        pickle.dumps(_MaliciousPayload(marker))
    )
    assert store.load().sources == ["safe-source"]
    assert store.load_payload("mini_dma_records") is None
    assert not marker.exists()


def test_v2_codec_round_trips_builder_records_reviews_and_assemble_state(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "Current (mA)": pd.Series([10.0, 20.0], dtype="float64"),
            "State": ["No transition", "Not observed"],
        },
        index=pd.Index([3, 4], name="row"),
    )
    payload = {
        "records": [
            MiniDmaRecord(
                path=tmp_path / "tma" / "measurement.csv",
                sample="Ni50Fe27Ga23 12_2",
                data=frame,
                key=("Ni50Fe27Ga23", 12, 2),
                strain_summary=("50 MPa: 5% @ 20 mA",),
            ),
            VsmTemperatureScanRecord(
                path=tmp_path / "vsm.dat",
                sample="Ni50Fe27Ga23 12_2",
                data=frame.copy(),
                key=("Ni50Fe27Ga23", 12, 2),
            ),
        ],
        "reviews": {
            "ca": {"graph-a": {"status": "manual_adjusted", "values": {"As1": 31.0}}},
            "vsm": {"scan-a": {"status": "no_transition"}},
            "tma": {
                "run-a::50 MPa": {
                    "status": "manual_adjusted",
                    "manual_values_mA": {"As": 31.0},
                    "cleared_labels": ["Ms"],
                }
            },
        },
        "assemble": {
            "selected_columns": {"Composition", "Microwire", "TMA transition status"},
            "column_order": ["Microwire", "Composition", "TMA transition status"],
            "rows": frame,
        },
        "typed": {
            "series": frame["Current (mA)"],
            "array": np.asarray([[1, 2], [3, 4]], dtype=np.int16),
            "date": date(2026, 7, 14),
            "datetime": datetime(2026, 7, 14, 9, 30),
            "path": tmp_path / "data",
            "tuple": (1, "two"),
        },
    }

    decoded = safe_codec.decode_envelope(safe_codec.encode_envelope(payload))

    assert isinstance(decoded["records"][0], MiniDmaRecord)
    pd.testing.assert_frame_equal(decoded["records"][0].data, frame)
    assert decoded["reviews"] == payload["reviews"]
    assert decoded["assemble"]["selected_columns"] == payload["assemble"]["selected_columns"]
    assert decoded["assemble"]["column_order"] == payload["assemble"]["column_order"]
    pd.testing.assert_frame_equal(decoded["assemble"]["rows"], frame)
    np.testing.assert_array_equal(decoded["typed"]["array"], payload["typed"]["array"])
    assert decoded["typed"]["date"] == date(2026, 7, 14)
    assert decoded["typed"]["path"] == tmp_path / "data"


def test_v2_project_prepare_decodes_safe_payload(tmp_path: Path) -> None:
    encoded = safe_codec.encode_envelope(
        {"records": {"graph-a": {"status": "no_transition"}}}
    )
    project = tmp_path / "safe.pydpj"
    project.write_text(
        json.dumps(
            {
                "version": 2,
                "kind": "MicrowireDataBuilder",
                "sections": {"annealing": {"payloads": {"transition_reviews": encoded}}},
            }
        ),
        encoding="utf-8",
    )

    prepared = builder_ui._prepare_project_payload_for_gui(project)  # noqa: SLF001

    assert prepared.diagnostics == ()
    assert prepared.decoded_payload_count == 1
    assert prepared.payload["sections"]["annealing"][builder_ui.PROJECT_DECODED_PAYLOADS_KEY][
        "transition_reviews"
    ]["records"]["graph-a"]["status"] == "no_transition"


def test_invalid_v2_payload_fails_preparation_instead_of_partial_load(tmp_path: Path) -> None:
    project = tmp_path / "corrupt-v2.pydpj"
    project.write_text(
        json.dumps(
            {
                "version": 2,
                "kind": "MicrowireDataBuilder",
                "sections": {
                    "assemble": {
                        "rows": [{"Composition": "safe-existing-row"}],
                        "payloads": {
                            "records": {
                                "encoding": safe_codec.CODEC_ENCODING,
                                "version": safe_codec.CODEC_VERSION,
                                "value": {"$type": "unknown"},
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(safe_codec.SafeCodecError, match="assemble.records"):
        builder_ui._prepare_project_payload_for_gui(project)  # noqa: SLF001


def test_degraded_project_refuses_all_normal_saves_and_rollback_restores_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "legacy.pydpj"
    original = b'{"version":1,"blocked":"pickle-base64"}'
    source.write_bytes(original)
    warnings: list[str] = []
    fake = SimpleNamespace(
        _project_degraded_safe_mode=True,
        _project_path=source,
        logger=SimpleNamespace(warning=lambda message: warnings.append(str(message))),
    )
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")

    builder_ui.BuilderWindow._write_project_file(fake, source)  # noqa: SLF001
    builder_ui.BuilderWindow._write_project_file(fake, tmp_path / "lossy-copy.pydpj")  # noqa: SLF001

    assert source.read_bytes() == original
    assert not (tmp_path / "lossy-copy.pydpj").exists()
    assert len(warnings) == 2 and "degraded safe mode" in warnings[0]

    checkbox = SimpleNamespace(setChecked=lambda _checked: None)
    assembly = SimpleNamespace(
        _apply_export_settings=lambda _settings: None,
        graph_panel_checkbox=checkbox,
        _refresh_preview_source_filter_options=lambda: None,
        _refresh_preview_frame=lambda: None,
    )
    rollback_target = tmp_path / "previous.pydpj"
    rollback_window = SimpleNamespace(
        sections={},
        assembly_section=assembly,
        _show_imported_action=None,
        _project_path=tmp_path / "new.pydpj",
        _project_degraded_safe_mode=False,
        _dirty=False,
        _update_project_title=lambda: None,
        _update_project_actions=lambda: None,
    )
    builder_ui.BuilderWindow._restore_project_load_state(  # noqa: SLF001
        rollback_window,
        {
            "sections": {},
            "assembly": {},
            "project_path": rollback_target,
            "project_degraded_safe_mode": True,
            "dirty": True,
            "paused_timers": [],
        },
    )
    assert rollback_window._project_path == rollback_target
    assert rollback_window._project_degraded_safe_mode is True
    assert rollback_window._dirty is True


def test_project_export_exception_preserves_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "existing.pydpj"
    target.write_bytes(b"existing-project")
    critical: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "critical",
        lambda _parent, _title, message: critical.append(str(message)),
    )
    fake = SimpleNamespace(
        _project_degraded_safe_mode=False,
        _build_project_payload=lambda: (_ for _ in ()).throw(RuntimeError("export failed")),
    )

    builder_ui.BuilderWindow._write_project_file(fake, target)  # noqa: SLF001

    assert target.read_bytes() == b"existing-project"
    assert critical and "existing file was not changed" in critical[0]


def test_builder_window_initializes_degraded_mode_before_project_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = builder_ui.BuilderWindow()
    try:
        assert window._project_path is None
        assert window._project_degraded_safe_mode is False
        snapshot = window._capture_project_load_state()
        assert snapshot["project_degraded_safe_mode"] is False
        window._resume_project_load_timers(snapshot)
    finally:
        window._dirty = False
        window.close()
        window.deleteLater()
        app.processEvents()


def test_trusted_project_migration_requires_distinct_new_output(tmp_path: Path) -> None:
    source = tmp_path / "legacy.pydpj"
    output = tmp_path / "migrated.pydpj"
    source.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "sections": {
                    "annealing": {
                        "payloads": {
                            "annealing_records": _legacy_envelope(
                                [{"status": "no_transition"}]
                            )
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = migrate_legacy_project_trusted(source, output)

    assert result["legacy_payloads_migrated"] == 1
    assert source.read_text(encoding="utf-8").find("pickle-base64") >= 0
    migrated = json.loads(output.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert safe_codec.decode_envelope(
        migrated["sections"]["annealing"]["payloads"]["annealing_records"]
    ) == [{"status": "no_transition"}]
    with pytest.raises(safe_codec.SafeCodecError):
        migrate_legacy_project_trusted(source, source)
    with pytest.raises(FileExistsError):
        migrate_legacy_project_trusted(source, output)


def test_trusted_project_migration_never_overwrites_concurrent_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "legacy.pydpj"
    output = tmp_path / "raced-output.pydpj"
    source.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "MicrowireDataBuilder",
                "sections": {"annealing": {"payloads": {}}},
            }
        ),
        encoding="utf-8",
    )
    original_link = os.link

    def _race_link(source_path: str | os.PathLike[str], target_path: str | os.PathLike[str]) -> None:
        Path(target_path).write_bytes(b"concurrent-owner")
        original_link(source_path, target_path)

    monkeypatch.setattr(os, "link", _race_link)

    with pytest.raises(FileExistsError):
        migrate_legacy_project_trusted(source, output)
    assert output.read_bytes() == b"concurrent-owner"


def test_safe_store_round_trip_and_memory_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "store"
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: storage_root)
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("annealing")
    data = builder_storage.MiniDatabaseData(
        sources=["source-a"],
        processed={"source-a": 12.5},
        table=pd.DataFrame([{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2"}]),
        extra={"reviews": {"graph-a": {"status": "no_transition"}}},
    )
    store.save(data)
    store.save_payload("annealing_records", [{"status": "manual_adjusted"}])
    assert store.table_path.suffix == ".json"
    assert store.payload_path("annealing_records").suffix == ".json"

    _reset_store_state()
    loaded = builder_storage.MiniDatabaseStore("annealing")
    assert loaded.load().extra == data.extra
    pd.testing.assert_frame_equal(loaded.load().table, data.table)
    assert loaded.load_payload("annealing_records") == [{"status": "manual_adjusted"}]

    transaction = builder_storage.MiniDatabaseStore.begin_memory_transaction()
    loaded.save_payload("annealing_records", [{"status": "no_transition"}])
    assert loaded.load_payload("annealing_records") == [{"status": "no_transition"}]
    transaction.rollback()
    assert loaded.load_payload("annealing_records") == [{"status": "manual_adjusted"}]

    loaded.clear_table()
    _reset_store_state()
    after_clear = builder_storage.MiniDatabaseStore("annealing").load()
    assert after_clear.table.empty
    assert after_clear.sources == ["source-a"]
    assert after_clear.processed == {"source-a": 12.5}
    assert after_clear.extra == data.extra


def test_trusted_store_migration_is_atomic_copy_only_and_rejects_unsafe_outputs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy-store"
    base = source / "mini_databases"
    payload_dir = base / "payloads"
    payload_dir.mkdir(parents=True)
    meta = base / "annealing.json"
    table_pickle = base / "annealing.pkl"
    payload_pickle = payload_dir / "annealing_annealing_records.pkl"
    meta.write_text(
        json.dumps({"sources": ["source-a"], "processed": {}, "extra": {}}),
        encoding="utf-8",
    )
    table_pickle.write_bytes(
        pickle.dumps(pd.DataFrame([{"Composition": "Ni50Fe27Ga23"}]))
    )
    payload_pickle.write_bytes(pickle.dumps([{"status": "no_transition"}]))
    original_bytes = {
        path: path.read_bytes() for path in (meta, table_pickle, payload_pickle)
    }
    output = tmp_path / "migrated-store"

    manifest = migrate_legacy_store_trusted(source, output)

    assert manifest["legacy_tables_migrated"] == 1
    assert manifest["legacy_payloads_migrated"] == 1
    assert all(path.read_bytes() == content for path, content in original_bytes.items())
    stored = safe_codec.decode_envelope(
        json.loads((output / "mini_databases" / "annealing.store.json").read_text(encoding="utf-8"))
    )
    assert stored["sources"] == ["source-a"]
    assert stored["table"].iloc[0]["Composition"] == "Ni50Fe27Ga23"
    payload = safe_codec.decode_envelope(
        json.loads(
            (output / "mini_databases" / "payloads" / "annealing_annealing_records.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert payload == [{"status": "no_transition"}]
    with pytest.raises(safe_codec.SafeCodecError):
        migrate_legacy_store_trusted(source, source / "child-output")
    with pytest.raises(FileExistsError):
        migrate_legacy_store_trusted(source, output)


def test_trusted_store_migration_never_overwrites_concurrent_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "legacy-store"
    base = source / "mini_databases"
    base.mkdir(parents=True)
    (base / "annealing.json").write_text(
        json.dumps({"sources": [], "processed": {}, "extra": {}}),
        encoding="utf-8",
    )
    output = tmp_path / "raced-store"
    original_rename = os.rename

    def _race_rename(source_path: str | os.PathLike[str], target_path: str | os.PathLike[str]) -> None:
        output.mkdir()
        (output / "owner.txt").write_text("concurrent-owner", encoding="utf-8")
        original_rename(source_path, target_path)

    monkeypatch.setattr(os, "rename", _race_rename)
    with pytest.raises(FileExistsError):
        migrate_legacy_store_trusted(source, output)
    assert (output / "owner.txt").read_text(encoding="utf-8") == "concurrent-owner"
    assert not (output / "mini_databases").exists()


def test_store_migration_setup_failure_releases_os_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "legacy-store"
    base = source / "mini_databases"
    base.mkdir(parents=True)
    (base / "annealing.json").write_text(
        json.dumps({"sources": [], "processed": {}, "extra": {}}), encoding="utf-8"
    )
    output = tmp_path / "migrated"
    import microwire_data_builder.legacy_migration as migration

    original_mkdtemp = migration.tempfile.mkdtemp
    monkeypatch.setattr(
        migration.tempfile,
        "mkdtemp",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("setup failed")),
    )
    with pytest.raises(OSError, match="setup failed"):
        migrate_legacy_store_trusted(source, output)

    monkeypatch.setattr(migration.tempfile, "mkdtemp", original_mkdtemp)
    assert migrate_legacy_store_trusted(source, output)["sections"] == ["annealing"]

def test_ordinary_clear_operations_preserve_blocked_legacy_pickle_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "legacy-store"
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: storage_root)
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("annealing")
    table_bytes = pickle.dumps(pd.DataFrame([{"unsafe": "legacy"}]))
    payload_bytes = pickle.dumps([{"unsafe": "legacy"}])
    store.meta_path.write_text(
        json.dumps({"sources": ["safe"], "processed": {}, "extra": {"kept": True}}),
        encoding="utf-8",
    )
    store.legacy_table_path.write_bytes(table_bytes)
    store.legacy_payload_path("records").write_bytes(payload_bytes)

    store.clear_table()
    store.clear_payload("records")

    assert store.legacy_table_path.read_bytes() == table_bytes
    assert store.legacy_payload_path("records").read_bytes() == payload_bytes


def test_corrupt_safe_store_is_not_cached_or_overwritten_until_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "store"
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: storage_root)
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("annealing")
    corrupt = b'{"encoding":"microwire-json","version":2,"value":{"$type":"unknown"}}'
    store.table_path.write_bytes(corrupt)

    with pytest.raises(safe_codec.SafeCodecError, match="Failed to decode safe Builder store"):
        store.load()
    assert "annealing" not in builder_storage.MiniDatabaseStore._memory_data
    with pytest.raises(safe_codec.SafeCodecError, match="blocked after a decode failure"):
        store.save(builder_storage.MiniDatabaseData(sources=["replacement"]))
    assert store.table_path.read_bytes() == corrupt

    quarantined = tmp_path / "quarantine" / "annealing.store.json"
    original_link = os.link
    with monkeypatch.context() as race_patch:
        def _race_link(source_path: str | os.PathLike[str], target_path: str | os.PathLike[str]) -> None:
            Path(target_path).write_bytes(b"concurrent-owner")
            original_link(source_path, target_path)

        race_patch.setattr(os, "link", _race_link)
        with pytest.raises(FileExistsError):
            store.quarantine_corrupt_store(quarantined)
    assert store.table_path.read_bytes() == corrupt
    assert quarantined.read_bytes() == b"concurrent-owner"
    quarantined.unlink()
    assert store.quarantine_corrupt_store(quarantined) == quarantined
    assert quarantined.read_bytes() == corrupt
    store.save(builder_storage.MiniDatabaseData(sources=["replacement"]))
    _reset_store_state()
    assert builder_storage.MiniDatabaseStore("annealing").load().sources == ["replacement"]


def test_safe_store_schema_errors_block_but_successful_external_repair_unblocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: tmp_path / "store")
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("vsm")
    safe_codec.atomic_write_json(
        store.table_path,
        safe_codec.encode_envelope(
            {"sources": "not-a-list", "processed": {}, "table": [], "extra": []}
        ),
    )
    with pytest.raises(safe_codec.SafeCodecError, match="sources must be a list"):
        store.load()
    assert "vsm" in builder_storage.MiniDatabaseStore._blocked_sections

    repaired = {
        "sources": ["source-a"],
        "processed": {"source-a": 1.0},
        "table": pd.DataFrame([{"status": "No transition"}]),
        "extra": {"reviews": {}},
    }
    safe_codec.atomic_write_json(store.table_path, safe_codec.encode_envelope(repaired))
    loaded = store.load()
    assert loaded.sources == ["source-a"]
    assert "vsm" not in builder_storage.MiniDatabaseStore._blocked_sections
    loaded.extra["saved"] = True
    store.save(loaded)


def test_unreadable_legacy_metadata_is_a_visible_blocked_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: tmp_path / "store")
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("annealing")
    store.meta_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(safe_codec.SafeCodecError, match="Failed to read legacy Builder metadata"):
        store.load()
    assert "annealing" not in builder_storage.MiniDatabaseStore._memory_data
    assert "annealing" in builder_storage.MiniDatabaseStore._blocked_sections


def test_corrupt_safe_store_does_not_crash_section_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: tmp_path / "store")
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("base")
    store.table_path.write_text("{not json", encoding="utf-8")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    section = builder_ui.MiniDatabaseSection(
        logging.getLogger("test.blocked-section"), lambda _level, _message: None
    )
    try:
        assert section.data.table.empty
        assert not section.refresh_button.isEnabled()
        assert not section.source_button.isEnabled()
        assert "blocked and opened read-only" in section.status_label.text()
        assert "base" not in builder_storage.MiniDatabaseStore._memory_data
    finally:
        section.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("name", ["a/b", r"a\b", "a?b", "../outside", "/absolute"])
def test_noncanonical_payload_names_are_rejected_without_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setattr(builder_storage, "_storage_root", lambda: tmp_path / "store")
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("annealing")

    with pytest.raises(ValueError, match="Invalid Builder payload name"):
        store.payload_path(name)
    with pytest.raises(ValueError, match="Invalid Builder payload name"):
        store.legacy_payload_path(name)


def test_codec_rejects_nonfinite_raw_unknown_tags_oversize_and_bad_dataframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(safe_codec.SafeCodecError):
        safe_codec.decode_value(float("nan"))
    with pytest.raises(safe_codec.SafeCodecError):
        safe_codec.decode_value({"$type": "subprocess"})
    with pytest.raises(safe_codec.SafeCodecError):
        safe_codec.decode_value(
            {"$type": "dataclass", "class": "os:system", "state": {"$type": "dict", "items": []}}
        )

    monkeypatch.setattr(safe_codec, "MAX_BINARY_BYTES", 2)
    assert safe_codec.decode_value(
        {"$type": "bytes", "value": base64.b64encode(b"ok").decode("ascii")}
    ) == b"ok"
    with pytest.raises(safe_codec.SafeCodecError):
        safe_codec.decode_value(
            {"$type": "bytes", "value": base64.b64encode(b"too large").decode("ascii")}
        )

    bad_frame = safe_codec.encode_value(pd.DataFrame([[1, 2]], columns=["a", "b"]))
    bad_frame["rows"]["items"][0]["items"].pop()
    with pytest.raises(safe_codec.SafeCodecError):
        safe_codec.decode_value(bad_frame)


def test_codec_enforces_aggregate_budget_and_ndarray_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(safe_codec, "MAX_DECODE_ITEMS", 2)
    with pytest.raises(safe_codec.SafeCodecError, match="Aggregate codec item budget"):
        safe_codec.decode_value(
            {"$type": "list", "items": [1, 2, 3]}
        )

    monkeypatch.setattr(safe_codec, "MAX_NDARRAY_RANK", 2)
    with pytest.raises(safe_codec.SafeCodecError, match="ndarray rank"):
        safe_codec.decode_value(
            {
                "$type": "ndarray",
                "dtype": "uint8",
                "shape": [0, 0, 0],
                "data": "",
            }
        )


def test_codec_rejects_missing_and_extra_tag_fields_and_wrong_object_adapter() -> None:
    with pytest.raises(safe_codec.SafeCodecError, match="missing or unexpected"):
        safe_codec.decode_envelope(
            {"encoding": safe_codec.CODEC_ENCODING, "version": safe_codec.CODEC_VERSION}
        )
    with pytest.raises(safe_codec.SafeCodecError, match="Malformed"):
        safe_codec.decode_value({"$type": "pd.NA", "extra": True})
    with pytest.raises(safe_codec.SafeCodecError, match="No object adapter"):
        safe_codec.decode_value(
            {
                "$type": "object",
                "class": "microwire_data_builder.core:MiniDmaRecord",
                "state": {"$type": "dict", "items": []},
            }
        )
    bad_index = safe_codec.encode_value(pd.Index([1, 2]))
    bad_index["dtype"] = "definitely-not-a-dtype"
    with pytest.raises(safe_codec.SafeCodecError, match="Index dtype"):
        safe_codec.decode_value(bad_index)
    bad_series = safe_codec.encode_value(pd.Series([1, 2]))
    bad_series["dtype"] = "definitely-not-a-dtype"
    with pytest.raises(safe_codec.SafeCodecError, match="pandas dtype"):
        safe_codec.decode_value(bad_series)
    with pytest.raises(safe_codec.SafeCodecError, match="FabricationIndex state"):
        safe_codec.decode_value(
            {
                "$type": "object",
                "class": "microwire_data_builder.core:FabricationIndex",
                "state": {
                    "$type": "dict",
                    "items": [["draw_level", {"$type": "dict", "items": []}]],
                },
            }
        )


def test_atomic_json_size_limit_preserves_existing_file_and_store_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "bounded.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(safe_codec, "MAX_JSON_BYTES", 24)
    with pytest.raises(safe_codec.SafeCodecError, match="safe file limit"):
        safe_codec.atomic_write_json(target, {"value": "x" * 100})
    assert target.read_bytes() == b"old"

    monkeypatch.setattr(builder_storage, "_storage_root", lambda: tmp_path / "store")
    _reset_store_state()
    store = builder_storage.MiniDatabaseStore("annealing")
    monkeypatch.setattr(safe_codec, "MAX_JSON_BYTES", 10_000)
    store.save(builder_storage.MiniDatabaseData(sources=["old"]))
    old_bytes = store.table_path.read_bytes()
    monkeypatch.setattr(safe_codec, "MAX_JSON_BYTES", 24)
    with pytest.raises(safe_codec.SafeCodecError):
        store.save(builder_storage.MiniDatabaseData(sources=["new-long-source"]))
    assert store.table_path.read_bytes() == old_bytes
    assert store.load().sources == ["old"]
