from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from data_logging.microscope_logger.project_annotations import (
    prepare_brittle_update, publish_brittle_update,
)
from microwire_data_builder import project_package as pp, safe_codec


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(tmp_path: Path, *, microscope: str = "populated") -> Path:
    # Every row and measurement in this fixture is deliberately synthetic.
    target = tmp_path / "synthetic-source.pydpj"
    microscope_rows = [
        {"Composition": "SyntheticAlloy", "Microwire": "12_2", "Brittle": "brittle",
         "d (µm)": 41.0, "D (µm)": 50.0, "_images": ["synthetic-brittle.png"],
         "_glass_image": "synthetic-brittle.png", "_core_image": None},
        {"Composition": "SyntheticAlloy", "Microwire": "12/2", "Brittle": None,
         "d (µm)": 42.0, "D (µm)": 51.0, "_images": []},
        {"Composition": "SyntheticAlloy", "Microwire": "12/2 NG", "Brittle": "brittle",
         "d (µm)": 43.0, "_images": []},
        {"Composition": "AnotherAlloy", "Microwire": "12/2", "Brittle": None,
         "d (µm)": 44.0, "_images": []},
    ] if microscope == "populated" else []
    microscope_columns = list(dict.fromkeys(key for row in microscope_rows for key in row))
    sections = {
        "assemble": {"columns": ["Composition", "Microwire", "Brittle", "d (µm)"],
                     "rows": [{"Composition": "SyntheticAlloy", "Microwire": "12/2",
                               "Brittle": "brittle", "d (µm)": 41.0}], "index": [21]},
        "microscope": {"section": "microscope", "title": "Microscope",
                       "columns": microscope_columns, "rows": microscope_rows,
                       "index": list(range(len(microscope_rows))),
                       "extra": {"synthetic-sentinel": "preserve"}, "sources": [],
                       "processed": {}, "payloads": {
                           "synthetic_deferred_measurements": safe_codec.encode_envelope(
                               {"synthetic_signal": np.arange(128, dtype=float)})}},
    }
    if microscope == "absent":
        sections.pop("microscope")
        sections["videos"] = {"columns": [], "rows": [], "index": [], "payloads": {
            "synthetic_deferred_measurements": safe_codec.encode_envelope({"sentinel": [1, 2, 3]})}}
    pp.write_project_package(target, {"kind": pp.PROJECT_KIND, "version": 3, "sections": sections})
    return target


@pytest.mark.parametrize("brittle", [True, False])
def test_prepare_preserves_package_payloads_variants_and_explicit_false(tmp_path, brittle):
    source = _project(tmp_path)
    old_hash = _hash(source)
    before = pp.inspect_project_package(source, verify_entries=True)
    review = prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                                    microwire="12/2", brittle=brittle, expected_sha256=old_hash)
    assert _hash(source) == old_hash
    candidate = pp.inspect_project_package(Path(review.candidate_path), verify_entries=True)
    changed_tables = {before.sections[key]["table"] for key in ("microscope", "assemble")}
    assert set(before.entries) == set(candidate.entries)
    for name, descriptor in before.entries.items():
        if name not in changed_tables:
            assert candidate.entries[name] == descriptor
            assert before.read_entry(name) == candidate.read_entry(name)
    microscope = candidate.read_section("microscope", load_payloads=False)["rows"]
    for row in microscope[:2]:
        assert row["_brittle_override"] is brittle
        assert row["Brittle"] == ("brittle" if brittle else None)
    assert microscope[0]["d (µm)"] == 41.0
    assert microscope[2]["Microwire"] == "12/2 NG"
    assert microscope[2]["Brittle"] == "brittle"
    assert "_brittle_override" not in microscope[2]
    assert microscope[3]["Brittle"] is None
    assert len(review.changes) == 3
    assert review.verification["explicit_brittle_survived_builder_open"] is True
    assert review.verification["logger_table_import_passed"] is True
    assert json.loads(Path(review.review_path).read_text(encoding="utf-8"))["candidate_sha256"] == _hash(Path(review.candidate_path))


@pytest.mark.parametrize("microscope", ["empty", "absent"])
def test_known_project_sample_gets_annotation_row_without_existing_image(tmp_path, microscope):
    source = _project(tmp_path, microscope=microscope)
    original = pp.inspect_project_package(source, verify_entries=True)
    review = prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                                    microwire="12/2", brittle=False, expected_sha256=_hash(source))
    candidate = pp.inspect_project_package(Path(review.candidate_path), verify_entries=True)
    section = candidate.read_section("microscope", load_payloads=False)
    assert len(section["rows"]) == 1
    row = section["rows"][0]
    assert row["Composition"] == "SyntheticAlloy"
    assert row["Microwire"] == "12/2"
    assert row["Brittle"] is None
    assert row["_brittle_override"] is False
    assert row["d (µm)"] is None
    assert any(change.get("inserted_row") for change in review.changes)
    for name, descriptor in original.entries.items():
        if descriptor.role not in {"table"}:
            assert candidate.entries[name] == descriptor


@pytest.mark.parametrize("composition,wire", [("WrongAlloy", "12/2"), ("SyntheticAlloy", "12/3"),
                                              ("SyntheticAlloy", "12/2 other-end")])
def test_unknown_or_different_variant_cannot_be_annotated(tmp_path, composition, wire):
    source = _project(tmp_path)
    old_hash = _hash(source)
    with pytest.raises(ValueError, match="not present"):
        prepare_brittle_update(source, tmp_path / "reviews", composition=composition,
                               microwire=wire, brittle=True, expected_sha256=old_hash)
    assert _hash(source) == old_hash
    assert not (tmp_path / "reviews").exists()


def test_stale_catalogue_and_non_bool_rejected_before_any_candidate(tmp_path):
    source = _project(tmp_path)
    with pytest.raises(ValueError, match="changed"):
        prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                               microwire="12/2", brittle=True, expected_sha256="0" * 64)
    with pytest.raises(ValueError, match="explicit"):
        prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                               microwire="12/2", brittle="false", expected_sha256=_hash(source))
    assert not (tmp_path / "reviews").exists()


def test_conflicting_sample_identity_columns_refuse_writeback(tmp_path):
    source = tmp_path / "conflicting-identity.pydpj"
    pp.write_project_package(source, {"kind": pp.PROJECT_KIND, "sections": {
        "microscope": {"columns": ["Composition", "Microwire", "Wire"],
                       "rows": [{"Composition": "SyntheticAlloy", "Microwire": "12/2", "Wire": "12/3"}],
                       "index": [0]}}})
    old_hash = _hash(source)
    with pytest.raises(ValueError, match="conflicting"):
        prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                               microwire="12/2", brittle=True, expected_sha256=old_hash)
    assert _hash(source) == old_hash
    assert not (tmp_path / "reviews").exists()


def test_guarded_publish_preserves_backup_and_receipt_and_rejects_stale_or_locked_source(tmp_path):
    source = _project(tmp_path)
    original_bytes = source.read_bytes()
    original_hash = _hash(source)
    review = prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                                    microwire="12/2", brittle=False, expected_sha256=original_hash)
    with pytest.raises(ValueError, match="review record changed"):
        publish_brittle_update(replace(review, brittle=True), backup=tmp_path / "tampered-backup.pydpj",
                               receipt_path=tmp_path / "tampered-receipt.json")
    source.write_bytes(original_bytes + b"synthetic concurrent change")
    with pytest.raises(ValueError, match="changed after review"):
        publish_brittle_update(review, backup=tmp_path / "stale-backup.pydpj",
                               receipt_path=tmp_path / "stale-receipt.json")
    source.write_bytes(original_bytes)
    lock = source.parent / f".{source.name}.save.lock"
    lock.write_text("synthetic other saver", encoding="utf-8")
    with pytest.raises(ValueError, match="publication failed"):
        publish_brittle_update(review, backup=tmp_path / "locked-backup.pydpj",
                               receipt_path=tmp_path / "locked-receipt.json")
    assert source.read_bytes() == original_bytes
    assert lock.read_text(encoding="utf-8") == "synthetic other saver"
    assert not (tmp_path / "locked-backup.pydpj").exists()
    lock.unlink()
    backup = tmp_path / "backup.pydpj"
    receipt_path = tmp_path / "receipt.json"
    receipt = publish_brittle_update(review, backup=backup, receipt_path=receipt_path)
    assert _hash(backup) == original_hash
    assert _hash(source) == review.candidate_sha256
    assert receipt["strict_verification_passed"] is True
    assert receipt["annotation"]["brittle"] is False
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    assert not lock.exists()
    with pytest.raises(FileExistsError):
        publish_brittle_update(review, backup=backup, receipt_path=receipt_path)


def test_legacy_project_requires_explicit_separate_migration(tmp_path):
    source = tmp_path / "synthetic-legacy.pydpj"
    source.write_text(json.dumps({"kind": pp.PROJECT_KIND, "version": 2, "sections": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="packaged"):
        prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                               microwire="12/2", brittle=True, expected_sha256=_hash(source))


def test_frozen_build_fails_before_candidate_preparation(tmp_path, monkeypatch):
    source = _project(tmp_path)
    old_hash = _hash(source)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(ValueError, match="Python/uv"):
        prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                               microwire="12/2", brittle=True, expected_sha256=old_hash)
    assert _hash(source) == old_hash
    assert not (tmp_path / "reviews").exists()


def test_publication_timeout_keeps_durable_status_after_possible_atomic_replace(tmp_path, monkeypatch):
    from data_logging.microscope_logger import project_annotations as annotations
    source = _project(tmp_path)
    original = source.read_bytes()
    original_hash = _hash(source)
    review = prepare_brittle_update(source, tmp_path / "reviews", composition="SyntheticAlloy",
                                    microwire="12/2", brittle=False, expected_sha256=original_hash)
    backup = tmp_path / "timeout-backup.pydpj"
    receipt = tmp_path / "timeout-receipt.json"

    def simulate_timeout_after_replace(command, **kwargs):
        assert kwargs["timeout"] == 300
        assert json.loads(receipt.read_text(encoding="utf-8"))["publication_pending"] is True
        backup.write_bytes(original)
        source.write_bytes(Path(review.candidate_path).read_bytes())
        kwargs["stderr"].write("synthetic timeout after atomic replacement\n")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(annotations.subprocess, "run", simulate_timeout_after_replace)
    with pytest.raises(ValueError, match="Inspect"):
        publish_brittle_update(review, backup=backup, receipt_path=receipt)
    preserved = json.loads(receipt.read_text(encoding="utf-8"))
    assert preserved["publication_result_uncertain"] is True
    assert preserved["source_sha256_after_failure"] == review.candidate_sha256
    assert preserved["backup_sha256_after_failure"] == original_hash
    assert "synthetic timeout" in Path(preserved["stderr_path"]).read_text(encoding="utf-8")
