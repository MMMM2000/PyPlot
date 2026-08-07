from __future__ import annotations

import json
import os
import base64
import hashlib
import pickle
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import launcher
from microwire_data_builder import project_package, safe_codec
from microwire_data_builder.legacy_migration import migrate_legacy_project_trusted
from microwire_data_builder import storage as builder_storage
from microwire_data_builder import ui as builder_ui
from microwire_eda import core as eda_core


def _payload(*, marker: str = "one") -> dict[str, object]:
    encoded = safe_codec.encode_envelope(
        {
            "marker": marker,
            "raw": b"binary-data",
            "array": np.asarray([[1, 2], [3, 4]], dtype=np.int16),
        }
    )
    return {
        "kind": project_package.PROJECT_KIND,
        "saved_at": "2026-07-15 12:00",
        "sections": {
            "annealing": {
                "section": "annealing",
                "title": "Current annealing",
                "columns": ["Composition", "Microwire", "Sample", "State"],
                "rows": [{
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "1/1",
                    "Sample": "Ni50Fe27Ga23 1/1",
                    "State": "No transition",
                }],
                "index": [0],
                "extra": {"reviewed": True},
                "sources": ["copy-only"],
                "processed": {"copy-only": "ok"},
                "payloads": {"annealing_records": encoded},
            },
            "assemble": {
                "section": "assemble",
                "title": "Assemble",
                "columns": ["Composition", "Microwire", "Sample", "Current density A/mm²"],
                "rows": [{
                    "Composition": "Ni50Fe27Ga23",
                    "Microwire": "1/1",
                    "Sample": "Ni50Fe27Ga23 1/1",
                    "Current density A/mm²": 4.2,
                }],
                "index": [0],
                "selected_columns": ["Sample", "Current density A/mm²"],
                "column_order": ["Sample", "Current density A/mm²"],
                "payloads": {
                    "deferred_audit": safe_codec.encode_envelope(
                        {"reviewed": True, "raw": b"deferred-binary"}
                    )
                },
            },
        },
    }


def _rewrite_archive(source: Path, target: Path, mutate) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        items = [(info.filename, archive.read(info), info.compress_type) for info in archive.infolist()]
    items = mutate(items)
    with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
        for name, raw, compression in items:
            archive.writestr(name, raw, compress_type=compression)


def test_v3_round_trip_uses_split_entries_and_content_addressed_blobs(tmp_path: Path) -> None:
    target = tmp_path / "project.pydpj"
    index = project_package.write_project_package(target, _payload())

    with zipfile.ZipFile(target, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        assert names[:2] == ["mimetype", "manifest.json"]
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert infos[1].compress_type == zipfile.ZIP_STORED
        assert "sections/annealing/state.json" in names
        assert "sections/annealing/table.json" in names
        assert "sections/annealing/payloads/annealing_records.json" in names
        blob_infos = [
            info for info in infos if info.filename.startswith("blobs/sha256/")
        ]
        assert blob_infos
        assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in blob_infos)

    materialized = index.materialize()
    section = materialized["sections"]["annealing"]
    decoded = safe_codec.decode_envelope(section["payloads"]["annealing_records"])
    assert decoded["marker"] == "one"
    assert decoded["raw"] == b"binary-data"
    np.testing.assert_array_equal(decoded["array"], np.asarray([[1, 2], [3, 4]], dtype=np.int16))
    assert materialized["sections"]["assemble"]["selected_columns"] == [
        "Sample", "Current density A/mm²"
    ]


def test_payload_resolver_decodes_only_records_matching_table_paths(tmp_path: Path) -> None:
    first_path = tmp_path / "first.VSM-HYS-DATA"
    second_path = tmp_path / "second.VSM-HYS-DATA"
    records = [
        builder_ui.VsmHysteresisRecord(
            path=first_path,
            sample="Ni50Fe27Ga23 1/1",
            data=pd.DataFrame({"Applied Field [Oe]": [1.0], "Signal [emu]": [2.0]}),
            temperature=100.0,
            angle=0.0,
            key=("Ni50Fe27Ga23", 1, 1, None),
            label="first",
        ),
        builder_ui.VsmHysteresisRecord(
            path=second_path,
            sample="Ni50Fe27Ga23 1/2",
            data=pd.DataFrame({"Applied Field [Oe]": [3.0], "Signal [emu]": [4.0]}),
            temperature=200.0,
            angle=90.0,
            key=("Ni50Fe27Ga23", 1, 2, None),
            label="second",
        ),
    ]
    payload = _payload()
    payload["sections"]["vsm_hysteresis"] = {
        "columns": ["Composition", "Microwire", "VSM hysteresis graphs", "_sources"],
        "rows": [],
        "index": [],
        "payloads": {
            "vsm_hysteresis_records": safe_codec.encode_envelope(records),
        },
    }
    target = tmp_path / "subset.pydpj"
    index = project_package.write_project_package(target, payload)

    loaded = project_package.ProjectPayloadResolver(index).load_records_for_paths(
        "vsm_hysteresis",
        "vsm_hysteresis_records",
        [second_path],
    )

    assert len(loaded) == 1
    assert loaded[0].path == second_path
    assert loaded[0].label == "second"
    assert loaded[0].data.iloc[0]["Signal [emu]"] == pytest.approx(4.0)


def test_streaming_codec_uses_columnar_dataframe_blobs_without_row_expansion() -> None:
    frame = pd.DataFrame({
        "temperature": np.linspace(100.0, 500.0, 20_000),
        "moment": np.linspace(-2.0, 2.0, 20_000),
    })
    blobs: dict[str, bytes] = {}

    def sink(buffer: memoryview) -> tuple[str, int]:
        raw = bytes(buffer)
        digest = hashlib.sha256(raw).hexdigest()
        blobs[digest] = raw
        return digest, len(raw)

    encoded_text = "".join(safe_codec.iterencode_envelope_with_blobs(frame, sink))
    encoded = json.loads(encoded_text)
    assert "dataframe-columnar" in encoded_text
    assert '\"rows\"' not in encoded_text
    assert len(blobs) == 2
    restored = safe_codec.decode_envelope(
        encoded,
        blob_resolver=lambda digest, size: blobs[digest],
    )
    pd.testing.assert_frame_equal(restored, frame)


def test_streaming_codec_accepts_realistic_large_tma_dataframe() -> None:
    rows, columns = 147_079, 15  # 2,206,185 cells; R2 had 2,206,176.
    values = np.arange(rows, dtype=np.float64)
    frame = pd.DataFrame({f"field_{index}": values + index for index in range(columns)})
    blobs: dict[str, bytes] = {}

    def sink(buffer: memoryview) -> tuple[str, int]:
        raw = bytes(buffer)
        digest = hashlib.sha256(raw).hexdigest()
        blobs[digest] = raw
        return digest, len(raw)

    encoded = json.loads("".join(
        safe_codec.iterencode_envelope_with_blobs(frame, sink)
    ))
    restored = safe_codec.decode_envelope(
        encoded, blob_resolver=lambda digest, _size: blobs[digest]
    )

    assert len(blobs) == columns
    pd.testing.assert_frame_equal(restored, frame)


def test_streaming_writer_accepts_presplit_section_and_payload_files(tmp_path: Path) -> None:
    frame = pd.DataFrame({"field": np.linspace(0.0, 1.0, 10_000)})
    state_path = tmp_path / "state.json"
    table_path = tmp_path / "table.json"
    payload_path = tmp_path / "payload.json"
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir()
    state_path.write_text('{"section":"vsm_temperature_scan"}', encoding="utf-8")
    table_path.write_text(
        '{"columns":["State"],"rows":[{"State":"No transition"}],"index":[0]}',
        encoding="utf-8",
    )
    blob_paths: dict[str, Path] = {}

    def sink(buffer: memoryview) -> tuple[str, int]:
        digest = hashlib.sha256(buffer).hexdigest()
        blob_path = blob_dir / digest
        blob_path.write_bytes(buffer)
        blob_paths[digest] = blob_path
        return digest, len(buffer)

    with payload_path.open("w", encoding="utf-8") as handle:
        handle.writelines(safe_codec.iterencode_envelope_with_blobs(frame, sink))
    section = project_package.StagedProjectSection(
        state_path=state_path,
        table_path=table_path,
        payloads={
            "vsm_temperature_scan_records": project_package.StagedEncodedPayload(
                payload_path, blob_paths
            )
        },
    )
    output = tmp_path / "staged.pydpj"

    index = project_package.write_project_package_streaming(
        output,
        [("vsm_temperature_scan", section)],
    )

    assert index.read_section("vsm_temperature_scan", load_payloads=False)["rows"] == [
        {"State": "No transition"}
    ]
    restored = project_package.ProjectPayloadResolver(index).load(
        "vsm_temperature_scan", "vsm_temperature_scan_records"
    )
    pd.testing.assert_frame_equal(restored, frame)


def test_v3_lazy_index_detects_file_replacement_before_section_read(tmp_path: Path) -> None:
    target = tmp_path / "project.pydpj"
    index = project_package.write_project_package(target, _payload(marker="old"))
    replacement = tmp_path / "replacement.pydpj"
    project_package.write_project_package(replacement, _payload(marker="new"))
    os.replace(replacement, target)

    with pytest.raises(safe_codec.SafeCodecError, match="changed on disk"):
        index.read_section("annealing")


def test_v3_validated_index_is_deeply_immutable(tmp_path: Path) -> None:
    target = tmp_path / "project.pydpj"
    index = project_package.write_project_package(target, _payload())

    with pytest.raises(TypeError):
        index.manifest["revision"] = 99  # type: ignore[index]
    with pytest.raises(TypeError):
        index.sections["annealing"]["state"] = "other.json"  # type: ignore[index]


def test_v3_reader_checks_manifest_digest_even_if_stat_fingerprint_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "project.pydpj"
    index = project_package.write_project_package(target, _payload(marker="old"))
    replacement = tmp_path / "replacement.pydpj"
    project_package.write_project_package(replacement, _payload(marker="new"))
    os.replace(replacement, target)
    monkeypatch.setattr(
        project_package.FileFingerprint,
        "capture",
        classmethod(lambda _cls, _path: index.fingerprint),
    )

    with pytest.raises(safe_codec.SafeCodecError, match="manifest changed"):
        index.read_section("annealing")


def test_v3_save_preserves_unloaded_section_logical_entry_digests(tmp_path: Path) -> None:
    source = tmp_path / "source.pydpj"
    source_index = project_package.write_project_package(source, _payload(marker="source"))
    original_paths = {
        source_index.sections["annealing"]["state"],
        source_index.sections["annealing"]["table"],
        *source_index.sections["annealing"]["payloads"].values(),
    }
    original_digests = {path: source_index.entries[path].sha256 for path in original_paths}

    updated_payload = source_index.project_header()
    updated_payload["sections"] = {"assemble": source_index.read_section("assemble")}
    updated_payload["sections"]["assemble"]["rows"][0]["Current density A/mm²"] = 5.1
    output = tmp_path / "updated.pydpj"
    updated = project_package.write_project_package(
        output,
        updated_payload,
        source_index=source_index,
        loaded_sections={"assemble"},
    )

    assert updated.read_section("annealing")["rows"][0]["State"] == "No transition"
    assert {path: updated.entries[path].sha256 for path in original_paths} == original_digests
    assert updated.manifest["revision"] == source_index.manifest["revision"] + 1


@pytest.mark.parametrize(
    ("section_key", "payload_id"),
    [
        ("annealing", "annealing_records"),
        ("vsm_temperature_scan", "vsm_temperature_scan_records"),
        ("mini_dma", "mini_dma_records"),
    ],
)
def test_v3_explicit_review_payload_tombstone_is_not_resurrected(
    tmp_path: Path, section_key: str, payload_id: str
) -> None:
    source = tmp_path / f"{section_key}-source.pydpj"
    project_package.write_project_package(source, {
        "kind": project_package.PROJECT_KIND,
        "sections": {
            section_key: {
                "section": section_key,
                "columns": ["State"],
                "rows": [{"State": "No transition"}],
                "index": [0],
                "payloads": {
                    payload_id: safe_codec.encode_envelope({"reviewed": True})
                },
            }
        },
    })
    source_index = project_package.inspect_project_package(source)
    section = source_index.read_section(section_key, load_payloads=False)
    section["payloads"] = {}
    section[project_package.DELETED_PAYLOADS_KEY] = [payload_id]
    output = tmp_path / f"{section_key}-cleared.pydpj"

    saved = project_package.write_project_package(
        output,
        {"kind": project_package.PROJECT_KIND, "sections": {section_key: section}},
        source_index=source_index,
        loaded_sections={section_key},
    )

    assert payload_id not in saved.sections[section_key]["payloads"]
    assert saved.read_section(section_key)["rows"] == [{"State": "No transition"}]


def test_v3_rejects_path_traversal_and_backslashes(tmp_path: Path) -> None:
    assert project_package._safe_entry_path("sections\\annealing\\state.json") is False  # noqa: SLF001
    source = tmp_path / "source.pydpj"
    project_package.write_project_package(source, _payload())
    bad = tmp_path / "bad.pydpj"

    def mutate(items):
        items.append(("../escape.json", b"{}", zipfile.ZIP_DEFLATED))
        return items

    _rewrite_archive(source, bad, mutate)
    with pytest.raises(safe_codec.SafeCodecError, match="Unsafe Builder package entry"):
        project_package.inspect_project_package(bad)


def test_v3_rejects_duplicate_and_unlisted_entries(tmp_path: Path) -> None:
    source = tmp_path / "source.pydpj"
    project_package.write_project_package(source, _payload())
    duplicate = tmp_path / "duplicate.pydpj"

    def add_duplicate(items):
        entry = next(item for item in items if item[0] == "sections/annealing/state.json")
        items.append(entry)
        return items

    with pytest.warns(UserWarning, match="Duplicate name"):
        _rewrite_archive(source, duplicate, add_duplicate)
    with pytest.raises(safe_codec.SafeCodecError, match="Duplicate Builder package entry"):
        project_package.inspect_project_package(duplicate)

    unlisted = tmp_path / "unlisted.pydpj"
    _rewrite_archive(
        source,
        unlisted,
        lambda items: items + [("extra.json", b"{}", zipfile.ZIP_DEFLATED)],
    )
    with pytest.raises(safe_codec.SafeCodecError, match="missing or unlisted"):
        project_package.inspect_project_package(unlisted)


def test_v3_rejects_entry_checksum_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source.pydpj"
    project_package.write_project_package(source, _payload())
    tampered = tmp_path / "tampered.pydpj"

    def mutate(items):
        return [
            (
                name,
                raw.replace(b"No transition", b"Yo transition")
                if name == "sections/annealing/table.json"
                else raw,
                compression,
            )
            for name, raw, compression in items
        ]

    _rewrite_archive(source, tampered, mutate)
    index = project_package.inspect_project_package(tampered)
    with pytest.raises(safe_codec.SafeCodecError, match="checksum failed"):
        index.read_section("annealing")


def test_v3_rejects_unknown_required_feature_and_duplicate_manifest_key(tmp_path: Path) -> None:
    source = tmp_path / "source.pydpj"
    project_package.write_project_package(source, _payload())
    unknown = tmp_path / "unknown.pydpj"

    def unknown_feature(items):
        result = []
        for name, raw, compression in items:
            if name == "manifest.json":
                manifest = json.loads(raw)
                manifest["required_features"].append("future-required")
                raw = json.dumps(manifest, separators=(",", ":")).encode()
            result.append((name, raw, compression))
        return result

    _rewrite_archive(source, unknown, unknown_feature)
    with pytest.raises(safe_codec.SafeCodecError, match="unsupported feature"):
        project_package.inspect_project_package(unknown)

    duplicate = tmp_path / "duplicate-key.pydpj"
    def duplicate_key(items):
        result = []
        for name, raw, compression in items:
            if name == "manifest.json":
                text = raw.decode()
                text = text.replace('{"format":', '{"format":"duplicate","format":', 1)
                raw = text.encode()
            result.append((name, raw, compression))
        return result
    _rewrite_archive(source, duplicate, duplicate_key)
    with pytest.raises(safe_codec.SafeCodecError, match="Duplicate JSON object key"):
        project_package.inspect_project_package(duplicate)


def test_v3_optional_extensions_explicitly_own_their_entries(tmp_path: Path) -> None:
    source = tmp_path / "source.pydpj"
    project_package.write_project_package(source, _payload())
    extension = tmp_path / "extension.pydpj"
    extension_path = "extensions/future/metadata.json"
    extension_raw = b'{"future":true}'

    def add_extension(items):
        result = []
        for name, raw, compression in items:
            if name == "manifest.json":
                manifest = json.loads(raw)
                manifest["optional_features"] = [
                    {"id": "future", "entries": [extension_path]}
                ]
                manifest["entries"].append({
                    "path": extension_path,
                    "role": "extension:future",
                    "media_type": "application/json",
                    "sha256": hashlib.sha256(extension_raw).hexdigest(),
                    "bytes": len(extension_raw),
                })
                raw = json.dumps(manifest, separators=(",", ":")).encode()
            result.append((name, raw, compression))
        result.append((extension_path, extension_raw, zipfile.ZIP_DEFLATED))
        return result

    _rewrite_archive(source, extension, add_extension)
    index = project_package.inspect_project_package(extension, verify_entries=True)
    feature = index.manifest["optional_features"][0]
    assert feature["id"] == "future"
    assert tuple(feature["entries"]) == (extension_path,)

    mismatched = tmp_path / "mismatched-extension.pydpj"
    def break_ownership(items):
        result = []
        for name, raw, compression in items:
            if name == "manifest.json":
                manifest = json.loads(raw)
                manifest["optional_features"][0]["entries"] = [
                    "extensions/future/missing.json"
                ]
                raw = json.dumps(manifest, separators=(",", ":")).encode()
            result.append((name, raw, compression))
        return result

    _rewrite_archive(extension, mismatched, break_ownership)
    with pytest.raises(safe_codec.SafeCodecError, match="ownership"):
        project_package.inspect_project_package(mismatched)


def test_v3_atomic_write_preserves_existing_target_when_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "project.pydpj"
    target.write_bytes(b"existing")
    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))

    with pytest.raises(OSError, match="blocked"):
        project_package.write_project_package(target, _payload())

    assert target.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".project.pydpj.*.tmp"))


def test_package_sniff_rejects_oversized_mimetype_without_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "oversized-mimetype.pydpj"
    with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
        archive.writestr(
            "mimetype", b"x" * (1024 * 1024), compress_type=zipfile.ZIP_STORED
        )
    monkeypatch.setattr(
        zipfile.ZipFile,
        "read",
        lambda *_args, **_kwargs: pytest.fail("oversized mimetype was read"),
    )

    assert not project_package.is_project_package(target)


def test_v3_publish_failure_never_deletes_a_replaced_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "new-project.pydpj"
    real_replace = os.replace

    def replace_with_intruder(source: Path | str, destination: Path | str) -> None:
        if Path(destination) == target:
            assert target.exists()
            target.unlink()
            target.write_bytes(b"concurrent-owner")
            raise OSError("publish race")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace_with_intruder)

    with pytest.raises(OSError, match="publish race"):
        project_package.write_project_package(
            target, _payload(), replace_existing=False
        )

    assert target.read_bytes() == b"concurrent-owner"


def test_v3_prepare_and_section_worker_leave_payload_lazy(tmp_path: Path) -> None:
    target = tmp_path / "project.pydpj"
    project_package.write_project_package(target, _payload())

    prepared = builder_ui._prepare_project_payload_for_gui(target)  # noqa: SLF001

    assert isinstance(prepared.package_index, project_package.ProjectIndex)
    assert isinstance(prepared.payload_resolver, project_package.ProjectPayloadResolver)
    assert prepared.payload["sections"] == {}
    assert prepared.decoded_payload_count == 0

    results: list[dict[str, object]] = []
    errors: list[object] = []
    worker = builder_ui._ProjectSectionLoadWorker(  # noqa: SLF001
        prepared.package_index,
        prepared.payload_resolver,
        "annealing",
    )
    worker.finished.connect(results.append)
    worker.failed.connect(errors.append)
    worker.run()

    assert not errors
    assert len(results) == 1
    section = results[0]
    assert "payloads" not in section
    loaders = section[builder_ui.PROJECT_LAZY_PAYLOAD_LOADERS_KEY]
    assert callable(loaders["annealing_records"])
    assert prepared.payload_resolver.budget.bytes < prepared.package_index.fingerprint.size
    decoded = loaders["annealing_records"]()
    assert decoded["marker"] == "one"
    assert decoded["raw"] == b"binary-data"


def test_store_lazy_payload_loader_is_transactional_and_resolves_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    store_cls = builder_storage.MiniDatabaseStore
    store_cls._memory_data = {}
    store_cls._memory_payloads = {}
    store_cls._payload_loaders = {}
    store_cls._payload_tombstones = set()
    store_cls._memory_transactions = []
    calls: list[str] = []
    store = store_cls("annealing")
    transaction = store_cls.begin_memory_transaction()
    store.register_payload_loader(
        "annealing_records", lambda: calls.append("load") or {"review": "No transition"}
    )
    assert store.has_payload_loader("annealing_records")
    assert calls == []
    transaction.commit_memory_only()

    assert store.has_payload_loader("annealing_records")
    assert store.load_payload("annealing_records") == {"review": "No transition"}
    assert store.load_payload("annealing_records") == {"review": "No transition"}
    assert calls == ["load"]
    assert not store.has_payload_loader("annealing_records")


def test_launcher_trusted_migration_uses_worker_and_outputs_v3(tmp_path: Path) -> None:
    source = tmp_path / "legacy.pydpj"
    output = tmp_path / "packaged.pydpj"
    source.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": project_package.PROJECT_KIND,
                "sections": {
                    "annealing": {
                        "columns": ["State"],
                        "rows": [{"State": "No transition"}],
                        "index": [0],
                        "payloads": {
                            "annealing_records": {
                                "encoding": "pickle-base64",
                                "value": base64.b64encode(
                                    pickle.dumps([{"status": "no_transition"}])
                                ).decode("ascii"),
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("MICROWIRE_BUILDER_MIGRATION_WORKER", None)
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "launcher.py"),
            "--microwire-builder-trusted-migrate",
            str(source),
            "--microwire-builder-migration-output",
            str(output),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "builder_migration_progress" in completed.stderr
    migrated = project_package.inspect_project_package(output)
    assert migrated.manifest["version"] == 3
    assert safe_codec.decode_envelope(
        migrated.read_section("annealing")["payloads"]["annealing_records"]
    ) == [{"status": "no_transition"}]
    assert migrated.manifest["migration"]["source_name"] == source.name
    assert migrated.manifest["migration"]["pickle_payload_count"] == 1


def test_trusted_migration_reencodes_safe_payload_in_child_and_externalizes_binary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy-safe.pydpj"
    output = tmp_path / "packaged-safe.pydpj"
    raw_binary = b"safe-binary" * 100_000
    source.write_text(json.dumps({
        "kind": project_package.PROJECT_KIND,
        "version": 1,
        "sections": {
            "annealing": {
                "columns": [], "rows": [], "index": [],
                "payloads": {
                    "annealing_records": safe_codec.encode_envelope(
                        {"raw": raw_binary}
                    )
                },
            }
        },
    }), encoding="utf-8")

    result = migrate_legacy_project_trusted(source, output)

    migrated = project_package.inspect_project_package(output, verify_entries=True)
    with zipfile.ZipFile(output, "r") as archive:
        blob_infos = [
            info
            for info in archive.infolist()
            if info.filename.startswith("blobs/sha256/")
        ]
    assert len(blob_infos) == 1
    assert blob_infos[0].compress_type == zipfile.ZIP_STORED
    assert result["legacy_payloads_migrated"] == 0
    assert migrated.manifest["migration"]["pickle_payload_count"] == 0
    assert len(migrated.blobs) == 1
    with migrated.open_reader() as reader:
        encoded = reader.read_payload_with_blob_refs(
            "annealing", "annealing_records"
        )
    assert "$blob" in encoded["value"]["items"][0][1]
    assert project_package.ProjectPayloadResolver(migrated).load(
        "annealing", "annealing_records"
    ) == {"raw": raw_binary}


def test_trusted_migration_cancellation_leaves_no_partial_output(tmp_path: Path) -> None:
    source = tmp_path / "legacy.pydpj"
    source.write_text(json.dumps({
        "kind": project_package.PROJECT_KIND,
        "version": 1,
        "saved_at": "2026-07-15 12:00",
        "sections": {"annealing": _payload()["sections"]["annealing"]},
    }), encoding="utf-8")
    output = tmp_path / "cancelled.pydpj"

    with pytest.raises(safe_codec.SafeCodecError, match="cancelled"):
        migrate_legacy_project_trusted(
            source,
            output,
            cancelled=lambda: True,
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".cancelled.pydpj.*.tmp"))


def test_builder_window_loads_visible_v3_graph_payload_for_overview(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    store_cls = builder_storage.MiniDatabaseStore
    store_cls._memory_data = {}
    store_cls._memory_payloads = {}
    store_cls._payload_loaders = {}
    store_cls._payload_tombstones = set()
    store_cls._memory_transactions = []
    store_cls._pending_sections = set()
    store_cls._pending_payloads = set()
    target = tmp_path / "project.pydpj"
    project_package.write_project_package(target, _payload())
    read_paths: list[str] = []
    original_read_entry = project_package.ProjectReader.read_entry

    def tracked_read_entry(reader, path):
        read_paths.append(path)
        return original_read_entry(reader, path)

    monkeypatch.setattr(project_package.ProjectReader, "read_entry", tracked_read_entry)
    app = builder_ui.QtWidgets.QApplication.instance() or builder_ui.QtWidgets.QApplication([])
    window = builder_ui.BuilderWindow()
    try:
        window._auto_open_last = False
        window._auto_open_latest_database = False
        window.statusBar().showMessage("Inspecting project...")
        window._load_project_from_path(target)
        deadline = time.monotonic() + 15
        while window._project_load_in_progress and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert not window._project_load_in_progress
        assert window._project_path == target
        assert window.statusBar().currentMessage() == ""
        assert isinstance(window._project_package_index, project_package.ProjectIndex)
        assert window.annealing_section.data.table.iloc[0]["State"] == "No transition"
        assert not window.annealing_section.store.has_payload_loader("annealing_records")
        loaded_payload = window.annealing_section.store.load_payload("annealing_records")
        assert loaded_payload["marker"] == "one"
        exported = window.annealing_section.export_project_payload()
        assert exported["payloads"] == {}
        assert "sections/assemble/table.json" not in read_paths
        assert "assemble" in window._deferred_project_section_keys

        window.tab_widget.setCurrentWidget(window.assembly_section)
        deadline = time.monotonic() + 10
        while "assemble" in window._deferred_project_section_keys and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert "assemble" not in window._deferred_project_section_keys
        assert "sections/assemble/table.json" in read_paths
    finally:
        window._dirty = False
        window.close()
        window.deleteLater()
        app.processEvents()


def test_builder_save_spools_loaded_raw_payload_and_preserves_deferred_entries(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    save_errors: list[str] = []
    monkeypatch.setattr(
        builder_ui.QtWidgets.QMessageBox,
        "critical",
        lambda _parent, _title, message: save_errors.append(str(message)),
    )
    store_cls = builder_storage.MiniDatabaseStore
    store_cls._memory_data = {}
    store_cls._memory_payloads = {}
    store_cls._payload_loaders = {}
    store_cls._payload_tombstones = set()
    store_cls._memory_transactions = []
    store_cls._pending_sections = set()
    store_cls._pending_payloads = set()
    source = tmp_path / "source.pydpj"
    saved = tmp_path / "saved.pydpj"
    source_index = project_package.write_project_package(source, _payload())
    deferred_paths = {
        source_index.sections["assemble"]["state"],
        source_index.sections["assemble"]["table"],
        *source_index.sections["assemble"]["payloads"].values(),
    }
    source_digests = {
        path: source_index.entries[path].sha256 for path in deferred_paths
    }
    app = builder_ui.QtWidgets.QApplication.instance() or builder_ui.QtWidgets.QApplication([])
    window = builder_ui.BuilderWindow()
    try:
        window._auto_open_last = False
        window._auto_open_latest_database = False
        window._load_project_from_path(source)
        deadline = time.monotonic() + 15
        while window._project_load_in_progress and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert "assemble" in window._deferred_project_section_keys
        loaded_payload = window.annealing_section.store.load_payload("annealing_records")
        monkeypatch.setattr(
            builder_ui,
            "_encode_project_payload",
            lambda _value: pytest.fail("v3 Save base64-encoded a loaded raw payload"),
        )

        window._write_project_file(saved)

        assert save_errors == []
        assert saved.is_file()
        saved_index = project_package.inspect_project_package(saved, verify_entries=True)
        assert {
            path: saved_index.entries[path].sha256 for path in deferred_paths
        } == source_digests
        saved_payload = project_package.ProjectPayloadResolver(saved_index).load(
            "annealing", "annealing_records"
        )
        assert saved_payload["marker"] == loaded_payload["marker"]
        assert saved_payload["raw"] == loaded_payload["raw"]
        np.testing.assert_array_equal(saved_payload["array"], loaded_payload["array"])
        assert not list((tmp_path / "storage").glob("microwire-builder-save-payloads-*"))
        assert not list(saved.parent.glob(f".{saved.name}.payloads.*"))
    finally:
        window._dirty = False
        window.close()
        window.deleteLater()
        app.processEvents()


def test_stale_deferred_worker_cannot_import_into_new_project(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    store_cls = builder_storage.MiniDatabaseStore
    store_cls._memory_data = {}
    store_cls._memory_payloads = {}
    store_cls._payload_loaders = {}
    store_cls._payload_tombstones = set()
    store_cls._memory_transactions = []
    store_cls._pending_sections = set()
    store_cls._pending_payloads = set()
    payload_a = _payload(marker="a")
    payload_b = _payload(marker="b")
    payload_a["sections"]["assemble"]["selected_columns"] = ["A marker"]
    payload_b["sections"]["assemble"]["selected_columns"] = ["B marker"]
    project_a = tmp_path / "a.pydpj"
    project_b = tmp_path / "b.pydpj"
    project_package.write_project_package(project_a, payload_a)
    project_package.write_project_package(project_b, payload_b)
    started = threading.Event()
    release = threading.Event()
    original_run = builder_ui._ProjectSectionLoadWorker.run  # noqa: SLF001

    def delayed_run(worker) -> None:
        if (  # noqa: SLF001
            worker._package_index.path == project_a
            and worker._section_key == "assemble"
        ):
            started.set()
            release.wait(15)
        original_run(worker)

    monkeypatch.setattr(builder_ui._ProjectSectionLoadWorker, "run", delayed_run)
    app = builder_ui.QtWidgets.QApplication.instance() or builder_ui.QtWidgets.QApplication([])
    window = builder_ui.BuilderWindow()
    try:
        window._auto_open_last = False
        window._auto_open_latest_database = False
        window._load_project_from_path(project_a)
        deadline = time.monotonic() + 15
        while window._project_load_in_progress and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        window._load_deferred_project_section_async("assemble")
        deadline = time.monotonic() + 5
        while not started.is_set() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        assert started.is_set()

        window._load_project_from_path(project_b)
        deadline = time.monotonic() + 15
        while window._project_load_in_progress and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        assert window._project_path == project_b
        assert "assemble" in window._deferred_project_section_keys
        release.set()
        deadline = time.monotonic() + 10
        while window._deferred_project_section_threads and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        assert "assemble" in window._deferred_project_section_keys

        window.tab_widget.setCurrentWidget(window.assembly_section)
        deadline = time.monotonic() + 10
        while "assemble" in window._deferred_project_section_keys and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        assert "B marker" in window.assembly_section._selected_columns
        assert "A marker" not in window.assembly_section._selected_columns
    finally:
        release.set()
        deadline = time.monotonic() + 10
        while window._deferred_project_section_threads and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        window._dirty = False
        window.close(); window.deleteLater(); app.processEvents()


def test_save_is_blocked_while_current_deferred_worker_is_pending(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    store_cls = builder_storage.MiniDatabaseStore
    store_cls._memory_data = {}
    store_cls._memory_payloads = {}
    store_cls._payload_loaders = {}
    store_cls._payload_tombstones = set()
    store_cls._memory_transactions = []
    store_cls._pending_sections = set()
    store_cls._pending_payloads = set()
    source = tmp_path / "source.pydpj"
    saved = tmp_path / "saved.pydpj"
    project_package.write_project_package(source, _payload())
    started = threading.Event()
    release = threading.Event()
    original_run = builder_ui._ProjectSectionLoadWorker.run  # noqa: SLF001

    def delayed_run(worker) -> None:
        if worker._section_key == "assemble":  # noqa: SLF001
            started.set()
            release.wait(15)
        original_run(worker)

    monkeypatch.setattr(builder_ui._ProjectSectionLoadWorker, "run", delayed_run)
    app = builder_ui.QtWidgets.QApplication.instance() or builder_ui.QtWidgets.QApplication([])
    window = builder_ui.BuilderWindow()
    try:
        window._auto_open_last = False
        window._auto_open_latest_database = False
        window._load_project_from_path(source)
        deadline = time.monotonic() + 15
        while window._project_load_in_progress and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        window._load_deferred_project_section_async("assemble")
        deadline = time.monotonic() + 5
        while not started.is_set() and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        assert started.is_set()
        assert window._deferred_project_section_pending == {"assemble"}

        window._write_project_file(saved)

        assert not saved.exists()
        assert window._project_package_index.path == source
        release.set()
        deadline = time.monotonic() + 10
        while window._deferred_project_section_pending and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        assert not window._deferred_project_section_pending
        window._write_project_file(saved)
        assert saved.is_file()
    finally:
        release.set()
        deadline = time.monotonic() + 10
        while window._deferred_project_section_threads and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        window._dirty = False
        window.close(); window.deleteLater(); app.processEvents()


def test_deferred_transition_completion_forces_workspace_rebuild(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    store_cls = builder_storage.MiniDatabaseStore
    store_cls._memory_data = {}
    store_cls._memory_payloads = {}
    store_cls._payload_loaders = {}
    store_cls._payload_tombstones = set()
    store_cls._memory_transactions = []
    store_cls._pending_sections = set()
    store_cls._pending_payloads = set()
    payload = _payload()
    payload["sections"]["mini_dma"] = {
        "columns": ["TMA"],
        "rows": [{"TMA": "run"}],
        "index": [0],
        "payloads": {},
    }
    target = tmp_path / "project.pydpj"
    index = project_package.write_project_package(target, payload)
    app = builder_ui.QtWidgets.QApplication.instance() or builder_ui.QtWidgets.QApplication([])
    window = builder_ui.BuilderWindow()
    refresh_calls: list[dict[str, object]] = []
    dirty_calls: list[object] = []
    try:
        window._auto_open_last = False
        window._auto_open_latest_database = False
        window._project_package_index = index
        window._project_payload_resolver = project_package.ProjectPayloadResolver(index)
        window._deferred_project_section_keys = {"mini_dma"}
        window._deferred_project_section_pending = set()
        window.tab_widget.blockSignals(True)
        window.tab_widget.setCurrentWidget(window.transitions_section)
        window.tab_widget.blockSignals(False)
        window.transitions_section.set_active(True, refresh=False)
        monkeypatch.setattr(
            window.transitions_section,
            "mark_workspaces_dirty",
            lambda view=None: dirty_calls.append(view),
        )
        monkeypatch.setattr(
            window.transitions_section,
            "refresh_current_workspace",
            lambda **kwargs: refresh_calls.append(dict(kwargs)),
        )

        window._load_deferred_project_section_async("mini_dma")
        deadline = time.monotonic() + 10
        while window._deferred_project_section_pending and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)

        assert not window._deferred_project_section_pending
        assert "mini_dma" not in window._deferred_project_section_keys
        assert dirty_calls == [None]
        assert refresh_calls == [{"force": True}]
    finally:
        deadline = time.monotonic() + 10
        while window._deferred_project_section_threads and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        window._dirty = False
        window.close(); window.deleteLater(); app.processEvents()


def test_deferred_tma_transition_queue_does_not_decode_monolithic_payload(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", "1")
    store_cls = builder_storage.MiniDatabaseStore
    store_cls._memory_data = {}
    store_cls._memory_payloads = {}
    store_cls._payload_loaders = {}
    store_cls._payload_tombstones = set()
    store_cls._memory_transactions = []
    store_cls._pending_sections = set()
    store_cls._pending_payloads = set()
    source_path = tmp_path / "TMA run 01"
    payload = {
        "kind": project_package.PROJECT_KIND,
        "saved_at": "2026-07-16 10:00",
        "sections": {
            "mini_dma": {
                "section": "mini_dma",
                "title": "TMA",
                "columns": [
                    "_sample", "Composition", "Microwire", "TMA graphs",
                    "_group_key", "_sources",
                ],
                "rows": [{
                    "_sample": "Ni44Fe27Ga23Cu3Co3 1/1",
                    "Composition": "Ni44Fe27Ga23Cu3Co3",
                    "Microwire": "1/1",
                    "TMA graphs": ["TMA run 01"],
                    "_group_key": "Ni44Fe27Ga23Cu3Co3|1|1|",
                    "_sources": [str(source_path)],
                }],
                "index": [0],
                "extra": {"mini_dma_transition_reviews": {"records": {}}},
                "sources": [],
                "processed": {},
                "payloads": {
                    "mini_dma_records": safe_codec.encode_envelope(
                        [{"large_monolithic_payload_must_remain_lazy": True}]
                    )
                },
            }
        },
    }
    target = tmp_path / "project.pydpj"
    index = project_package.write_project_package(target, payload)
    resolver = project_package.ProjectPayloadResolver(index)
    load_calls: list[tuple[str, str]] = []
    original_load = resolver.load

    def tracked_load(section_key: str, payload_id: str):
        load_calls.append((section_key, payload_id))
        return original_load(section_key, payload_id)

    monkeypatch.setattr(resolver, "load", tracked_load)
    app = builder_ui.QtWidgets.QApplication.instance() or builder_ui.QtWidgets.QApplication([])
    window = builder_ui.BuilderWindow()
    try:
        window._auto_open_last = False
        window._auto_open_latest_database = False
        window._project_package_index = index
        window._project_payload_resolver = resolver
        window._deferred_project_section_keys = {"mini_dma"}
        window._deferred_project_section_pending = set()
        window.tab_widget.blockSignals(True)
        window.tab_widget.setCurrentWidget(window.transitions_section)
        window.tab_widget.blockSignals(False)

        window._load_deferred_project_section_async("mini_dma", decode_payloads=False)
        deadline = time.monotonic() + 10
        while window._deferred_project_section_pending and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)

        assert not window._deferred_project_section_pending
        assert load_calls == []
        records = window.mini_dma_section._all_mini_dma_records
        assert len(records) == 1
        assert records[0].path == source_path
        assert records[0].label == "TMA run 01"
        assert records[0].data.empty
        assert window.mini_dma_section.store.has_payload_loader("mini_dma_records")
    finally:
        deadline = time.monotonic() + 10
        while window._deferred_project_section_threads and time.monotonic() < deadline:
            app.processEvents(); time.sleep(0.005)
        window._dirty = False
        window.close(); window.deleteLater(); app.processEvents()


def test_transition_workspace_loads_only_the_selected_peer_dependencies(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MICROWIRE_BUILDER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("MICROWIRE_BUILDER_SETTINGS_FILE", str(tmp_path / "settings.ini"))
    app = builder_ui.QtWidgets.QApplication.instance() or builder_ui.QtWidgets.QApplication([])
    window = builder_ui.BuilderWindow()
    requested: list[tuple[str, bool]] = []
    try:
        window._auto_open_last = False
        window._auto_open_latest_database = False
        window._deferred_project_section_keys = {
            "annealing", "current_density", "vsm_temperature_scan",
            "transition_temps", "mini_dma",
        }
        monkeypatch.setattr(
            window,
            "_load_deferred_project_section_async",
            lambda key, *, decode_payloads=False: requested.append(
                (key, bool(decode_payloads))
            ),
        )
        window.tab_widget.blockSignals(True)
        window.tab_widget.setCurrentWidget(window.transitions_section)
        window.tab_widget.blockSignals(False)

        window.transitions_section.tab_widget.setCurrentIndex(2)
        window._load_current_deferred_project_sections()
        assert requested == [("mini_dma", False)]

        requested.clear()
        window.transitions_section.tab_widget.setCurrentIndex(1)
        window._load_current_deferred_project_sections()
        assert requested == [
            ("transition_temps", False),
            ("vsm_temperature_scan", True),
        ]

        requested.clear()
        window.transitions_section.tab_widget.setCurrentIndex(0)
        window._load_current_deferred_project_sections()
        assert requested == [
            ("annealing", True),
            ("current_density", False),
        ]
    finally:
        window._dirty = False
        window.close(); window.deleteLater(); app.processEvents()


def test_v3_is_consumed_by_eda_word_and_assemble_helpers(tmp_path: Path) -> None:
    target = tmp_path / "project.pydpj"
    project_package.write_project_package(target, _payload())

    eda_frame = eda_core._load_project_frame(target)  # noqa: SLF001
    assert eda_frame.iloc[0]["Sample"] == "Ni50Fe27Ga23 1/1"

    word_frame, _artifacts = launcher._load_project_word_report_frame(  # noqa: SLF001
        target,
        None,
        tmp_path / "word",
        include_origin=False,
    )
    assert "Sample" in word_frame.columns

    payload, copied, rebuild = launcher._prepare_assemble_export_project_payload(  # noqa: SLF001
        source_project=target,
        output_path=tmp_path / "assemble.xlsx",
        working_copy_dir=tmp_path / "working",
        copy_project=False,
        force_rebuild=False,
        rebuild_sections=None,
    )
    assert payload["version"] == 3
    assert copied is None
    assert rebuild is None
