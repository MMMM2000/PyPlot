"""Guarded diameter imports use synthetic projects, PNGs and private stores only."""
from __future__ import annotations

import copy
import csv
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import zlib

import pytest

from data_logging.microscope_logger.measurements import make_calibration, measure_line, save_session
from data_logging.microscope_logger.project_diameters import prepare_diameter_update, publish_diameter_update
from data_logging.microscope_logger.samples import load_project_samples
from data_logging.microscope_logger.session_revisions import update_session
from microwire_data_builder import project_package as pp, safe_codec


CONTEXT = dict(objective="10x", image_size=(200, 120), camera_id="synthetic-camera", zoom=1.0)


def _png(tone=90):
    def chunk(kind, raw):
        return struct.pack(">I", len(raw)) + kind + raw + struct.pack(">I", zlib.crc32(kind + raw) & 0xFFFFFFFF)
    pixels = b"".join(b"\0" + bytes([tone, 100, 110]) * 200 for _ in range(120))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 200, 120, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _source(tmp_path, *, empty_microscope=False):
    rows = [{"Composition": "SyntheticAlloy", "Microwire": "12/2", "_key": "SyntheticAlloy|12|2",
             "d (µm)": 20.0, "D (µm)": 70.0, "d/D": 0.286, "Brittle": None,
             "_images": [], "_core_image": None, "_glass_image": None},
            {"Composition": "SyntheticAlloy", "Microwire": "12/2OE", "_key": "SyntheticAlloy|12|2|OE",
             "d (µm)": 25.0, "D (µm)": 75.0, "Brittle": "brittle", "_images": []}]
    key = "SyntheticAlloy|12|2"
    target = tmp_path / "source.pydpj"
    sections = {"microscope": dict(section="microscope", title="Microscope", columns=list(dict.fromkeys(k for row in rows for k in row)),
        rows=[] if empty_microscope else rows, index=[] if empty_microscope else [0, 1], sources=[], processed={},
        extra={"overrides": {key: {"d": 20.0, "D": 70.0}},
               "validated": {key: {"d": 20.0, "D": 70.0, "d_reviewed": True, "D_reviewed": True,
                                   "timestamp": "synthetic-old", "sources": [], "preserved_review_field": "yes"}},
               "preserved_extra": {"sentinel": 42}, "payloads": {"old_payload": "old_payload"}},
        payloads={"old_payload": safe_codec.encode_envelope({"raw": b"synthetic old measurement bytes"})}),
        "assemble": dict(columns=["Composition", "Microwire", "d (µm)", "D (µm)", "d/D", "Brittle", "Mass (g)"],
            rows=[{"Composition": "SyntheticAlloy", "Microwire": "12/2", "d (µm)": 20.0, "D (µm)": 70.0,
                   "d/D": 0.286, "Brittle": None, "Mass (g)": 0.123}], index=[0]),
        "videos": dict(columns=[], rows=[], index=[], extra={"unrelated": "state"},
            payloads={"unrelated_video": safe_codec.encode_envelope({"bytes": b"synthetic video payload"})})}
    pp.write_project_package(target, dict(kind=pp.PROJECT_KIND, sections=sections))
    return target


def _capture(tmp_path, source, *, kinds=("core", "core", "outer"), defect=None, stale_binding=False,
             unknown_camera=False, distinct_reference=False):
    choice = next(choice for choice in load_project_samples(source) if choice.microwire == "12/2")
    sample = dict(composition=choice.composition, microwire=choice.microwire, source_kind="project",
                  source_path=str(source.resolve()), source_sha256="b" * 64 if stale_binding else choice.source_sha256,
                  source_rows=list(choice.source_rows))
    raw = _png()
    reference = _png(91) if distinct_reference else raw
    sha = hashlib.sha256(reference).hexdigest()
    context = dict(CONTEXT, camera_id="" if unknown_camera else CONTEXT["camera_id"])
    calibration = make_calibration(50, 25, **context)
    evidence = dict(image_sha256=sha, image_file=f"reference_{sha}.png", start=[5, 5], end=[55, 5],
                    created_utc="2026-01-01T00:00:00Z", source="Synthetic known reference")
    records = []
    core_number = 0
    for kind in kinds:
        pixels = 120 if kind == "outer" else 24 + 2 * core_number
        core_number += kind == "core"
        record = measure_line([5, 50], [5 + pixels, 50], **context, calibration=calibration, kind=kind)
        record.update(sample=copy.deepcopy(sample), review_status="Reviewed", calibration_evidence=copy.deepcopy(evidence))
        records.append(record)
    if defect == "unreviewed":
        records[0]["review_status"] = "Unreviewed"
    elif defect == "wrong-source":
        records[0]["sample"]["source_path"] = str(tmp_path / "other.pydpj")
    elif defect == "wrong-binding":
        records[0]["sample"]["source_rows"][0]["row_index"] = 999
    metadata = dict(context, sample=sample)
    folder = save_session(tmp_path / "captures", raw, records, metadata, reference_images={sha: reference})
    if defect == "camera-context":
        # A forged on-disk record contradicts its immutable calibration snapshot.
        path = folder / "session.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["measurements"][0]["camera_id"] = "other-camera"
        path.write_text(json.dumps(payload), encoding="utf-8")
    return folder, records, metadata


def _prepare(tmp_path, source, folder, ids):
    return prepare_diameter_update(source, tmp_path / "reviews", composition="SyntheticAlloy", microwire="12/2",
        session_directory=folder, measurement_ids=ids, expected_sha256=_hash(source))


def test_multiple_explicit_rows_mean_embed_full_evidence_and_preserve_other_data(tmp_path):
    source = _source(tmp_path)
    source_hash = _hash(source)
    original = pp.inspect_project_package(source, verify_entries=True)
    folder, records, metadata = _capture(tmp_path, source)
    revision = update_session(folder, records, metadata)
    review = _prepare(tmp_path, source, folder, [1, 2, 3])
    assert _hash(source) == source_hash
    assert review.summaries["core"]["mean_um"] == 12.5
    assert review.summaries["core"]["count"] == 2
    assert review.summaries["core"]["std_sample_um"] == pytest.approx(0.7071067811865476)
    candidate = pp.inspect_project_package(Path(review.candidate_path), verify_entries=True)
    section = candidate.read_section("microscope", load_payloads=False)
    row, variant = section["rows"]
    assert (row["d (µm)"], row["D (µm)"], row["d/D"]) == (12.5, 60.0, 0.208)
    assert variant["d (µm)"] == 25.0
    assert variant["Brittle"] == "brittle"
    assert row["Brittle"] is None
    assert row["_core_image"] is None and row["_glass_image"] is None
    assert section["extra"]["validated"]["SyntheticAlloy|12|2"]["preserved_review_field"] == "yes"
    assert section["extra"]["preserved_extra"] == {"sentinel": 42}
    event = section["extra"]["microscope_diameter_history"][-1]
    assert event["previous_state"]["validated"]["timestamp"] == "synthetic-old"
    assert event["previous_state"]["override"] == {"d": 20.0, "D": 70.0}
    assert any(change["section"] == "assemble" and change["previous"] == 70.0 for change in event["changes"])
    envelope = candidate.read_payload("microscope", event["payload_id"])
    evidence = safe_codec.decode_envelope(envelope)
    for name, entry in evidence["files"].items():
        assert entry["bytes"] == (folder / name).read_bytes()
        assert hashlib.sha256(entry["bytes"]).hexdigest() == entry["sha256"]
    assert str(revision.relative_to(folder)).replace("\\", "/") + "/session.json" in evidence["files"]
    assert evidence["event"]["selected_records"] == json.loads((revision / "session.json").read_text(encoding="utf-8"))["measurements"]
    assembly = candidate.read_section("assemble", load_payloads=False)["rows"][0]
    assert (assembly["d (µm)"], assembly["D (µm)"], assembly["Mass (g)"]) == (12.5, 60.0, 0.123)
    for name, descriptor in original.entries.items():
        if descriptor.role in {"blob", "payload"} or name.startswith("sections/videos/"):
            assert candidate.entries[name] == descriptor
            assert candidate.read_entry(name) == original.read_entry(name)
    assert review.verification["diameter_override_roundtrip_passed"] is True


@pytest.mark.parametrize("kind,expected", [("core", (12.0, 70.0)), ("outer", (20.0, 60.0))])
def test_single_kind_preserves_existing_other_and_old_snapshot_provenance(tmp_path, kind, expected):
    source = _source(tmp_path)
    folder, _, _ = _capture(tmp_path, source, kinds=(kind,), stale_binding=True)
    review = _prepare(tmp_path, source, folder, [1])
    section = pp.inspect_project_package(Path(review.candidate_path)).read_section("microscope", load_payloads=False)
    assert (section["rows"][0]["d (µm)"], section["rows"][0]["D (µm)"]) == expected
    assert section["extra"]["microscope_diameter_history"][-1]["original_source_snapshots"][0]["differs_from_current_target"] is True


@pytest.mark.parametrize("defect,match", [("unreviewed", "Reviewed"), ("wrong-source", "different Builder project"),
                                        ("wrong-binding", "source row binding"), ("camera-context", "Calibration does not match")])
def test_invalid_records_rejected_before_candidate(tmp_path, defect, match):
    source = _source(tmp_path)
    folder, _, _ = _capture(tmp_path, source, defect=defect)
    with pytest.raises(ValueError, match=match):
        _prepare(tmp_path, source, folder, [1])
    assert not (tmp_path / "reviews").exists()


@pytest.mark.parametrize("defect", ["csv", "image", "reference", "missing-reference"])
def test_changed_or_missing_evidence_rejected(tmp_path, defect):
    source = _source(tmp_path)
    folder, records, _ = _capture(tmp_path, source)
    if defect == "csv":
        (folder / "measurements.csv").write_bytes(b"forged csv")
    elif defect == "image":
        (folder / "image.png").write_bytes(b"changed image")
    else:
        reference = folder / f"reference_{records[0]['calibration_evidence']['image_sha256']}.png"
        if defect == "reference":
            reference.write_bytes(b"changed reference")
        else:
            reference.unlink()
    with pytest.raises((ValueError, OSError)):
        _prepare(tmp_path, source, folder, [1])
    assert not (tmp_path / "reviews").exists()


def test_core_larger_than_existing_outer_rejected_without_historical_averaging(tmp_path):
    source = _source(tmp_path)
    folder, records, metadata = _capture(tmp_path, source, kinds=("core",))
    records[0] = dict(measure_line([5, 50], [185, 50], **CONTEXT, calibration=make_calibration(50, 25, **CONTEXT), kind="core"),
                      sample=records[0]["sample"], review_status="Reviewed", calibration_evidence=records[0]["calibration_evidence"])
    update_session(folder, records, metadata)
    with pytest.raises(ValueError, match="cannot exceed"):
        _prepare(tmp_path, source, folder, [1])


def test_known_sample_without_microscope_row_gets_one_without_brittleness_inference(tmp_path):
    source = _source(tmp_path, empty_microscope=True)
    folder, _, _ = _capture(tmp_path, source, kinds=("outer",))
    review = _prepare(tmp_path, source, folder, [1])
    section = pp.inspect_project_package(Path(review.candidate_path)).read_section("microscope", load_payloads=False)
    assert len(section["rows"]) == 1
    assert section["rows"][0]["_key"] == "SyntheticAlloy|12|2"
    assert section["rows"][0]["Brittle"] is None
    assert section["rows"][0]["d (µm)"] == 20.0


@pytest.mark.parametrize("distinct_reference", [False, True])
def test_unknown_camera_calibration_must_be_bound_to_exact_imported_image(tmp_path, distinct_reference):
    source = _source(tmp_path)
    folder, _, _ = _capture(tmp_path, source, kinds=("core",), unknown_camera=True,
                            distinct_reference=distinct_reference)
    if distinct_reference:
        with pytest.raises(ValueError, match="unknown camera"):
            _prepare(tmp_path, source, folder, [1])
    else:
        assert _prepare(tmp_path, source, folder, [1]).summaries["core"]["mean_um"] == 12.0


def test_later_ui_metadata_does_not_relabel_old_reviewed_acquisition(tmp_path):
    source = _source(tmp_path)
    folder, records, metadata = _capture(tmp_path, source, kinds=("core",))
    metadata.update(objective="40x", camera_id="later-ui-camera", zoom=2.0)
    update_session(folder, records, metadata)
    review = _prepare(tmp_path, source, folder, [1])
    assert review.summaries["core"]["mean_um"] == 12.0
    candidate = pp.inspect_project_package(Path(review.candidate_path))
    event = candidate.read_section("microscope", load_payloads=False)["extra"]["microscope_diameter_history"][-1]
    assert event["selected_records"][0]["camera_id"] == "synthetic-camera"


@pytest.mark.parametrize("legacy_columns", [{"review_status", "reviewed_utc"},
    {"review_status", "reviewed_utc", "brittle", "brittle_observed_utc"}])
def test_legacy_base_csv_and_current_reviewed_revision_import_without_rewriting_history(tmp_path, legacy_columns):
    source = _source(tmp_path)
    folder, records, metadata = _capture(tmp_path, source, defect="unreviewed")
    csv_path = folder / "measurements.csv"
    parsed = csv.DictReader(io.StringIO(csv_path.read_text(encoding="utf-8")))
    fields = [field for field in parsed.fieldnames if field not in legacy_columns]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(list(parsed))
    legacy_bytes = buffer.getvalue().encode("utf-8")
    csv_path.write_bytes(legacy_bytes)
    records[0]["review_status"] = "Reviewed"
    update_session(folder, records, metadata)
    review = _prepare(tmp_path, source, folder, [1, 3])
    candidate = pp.inspect_project_package(Path(review.candidate_path))
    event = candidate.read_section("microscope", load_payloads=False)["extra"]["microscope_diameter_history"][-1]
    evidence = safe_codec.decode_envelope(candidate.read_payload("microscope", event["payload_id"]))
    assert evidence["files"]["measurements.csv"]["bytes"] == legacy_bytes
    assert csv_path.read_bytes() == legacy_bytes


def test_publication_guard_backup_receipt_and_capture_staleness(tmp_path):
    source = _source(tmp_path)
    original = source.read_bytes()
    folder, records, metadata = _capture(tmp_path, source)
    review = _prepare(tmp_path, source, folder, [1, 3])
    with pytest.raises(ValueError, match="review record changed"):
        publish_diameter_update(replace(review, microwire="12/3"), backup=tmp_path / "wrong-backup.pydpj", receipt_path=tmp_path / "wrong.json")
    lock = source.parent / f".{source.name}.save.lock"
    lock.write_text("synthetic lock", encoding="utf-8")
    with pytest.raises(ValueError, match="did not finish"):
        publish_diameter_update(review, backup=tmp_path / "locked.pydpj", receipt_path=tmp_path / "locked.json")
    assert source.read_bytes() == original and lock.exists()
    lock.unlink()
    receipt = publish_diameter_update(review, backup=tmp_path / "backup.pydpj", receipt_path=tmp_path / "receipt.json")
    assert (tmp_path / "backup.pydpj").read_bytes() == original
    assert _hash(source) == review.candidate_sha256
    assert receipt["diameter_update"]["summaries"]["outer"]["mean_um"] == 60.0
    with pytest.raises(FileExistsError):
        publish_diameter_update(review, backup=tmp_path / "backup.pydpj", receipt_path=tmp_path / "receipt.json")
    # A later capture revision invalidates a separate still-unpublished review.
    update_session(folder, records, metadata)
    with pytest.raises(ValueError, match="capture evidence changed"):
        from data_logging.microscope_logger.project_diameters import _assert_session_unchanged
        _assert_session_unchanged(folder, review.evidence_inventory)


def test_candidate_swap_between_parent_check_and_script_is_rejected_before_target_mutation(tmp_path, monkeypatch):
    from data_logging.microscope_logger import project_diameters as backend
    source = _source(tmp_path)
    original = source.read_bytes()
    folder, _, _ = _capture(tmp_path, source, kinds=("core",))
    review = _prepare(tmp_path, source, folder, [1])
    candidate = Path(review.candidate_path)
    index = pp.inspect_project_package(candidate)
    microscope = index.read_section("microscope", load_payloads=False)
    microscope["extra"]["synthetic_unreviewed_change"] = True
    alternate = tmp_path / "unreviewed-candidate.pydpj"
    pp.write_project_package(alternate, dict(kind=pp.PROJECT_KIND, sections={"microscope": microscope}),
                             source_index=index, loaded_sections={"microscope"})
    original_run = backend.subprocess.run

    def swap_at_launch(command, **kwargs):
        assert command[-2:] == ["--expected-candidate-sha256", review.candidate_sha256]
        candidate.write_bytes(alternate.read_bytes())  # A synthetic concurrent whole-file replacement.
        return original_run(command, **kwargs)

    monkeypatch.setattr(backend.subprocess, "run", swap_at_launch)
    backup, receipt = tmp_path / "race-backup.pydpj", tmp_path / "race-receipt.json"
    with pytest.raises(ValueError, match="did not finish"):
        publish_diameter_update(review, backup=backup, receipt_path=receipt)
    assert source.read_bytes() == original
    assert not backup.exists()
    assert not (source.parent / f".{source.name}.save.lock").exists()
    failure = json.loads(receipt.read_text(encoding="utf-8"))
    assert failure["publication_failed"] is True
    assert "Candidate changed" in Path(failure["stderr_path"]).read_text(encoding="utf-8")
