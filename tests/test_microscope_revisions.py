from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct
import zlib

import pytest

from data_logging.microscope_logger.measurements import make_calibration, measure_line, save_session
from data_logging.microscope_logger import session_revisions as revisions


CONTEXT = {"objective": "4x", "image_size": (100, 80), "camera_id": "synthetic-camera", "zoom": 1}


def png(width=100, height=80):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    data = b"".join(b"\0" + bytes([80, 120, 150]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b""))


def record(composition="Synthetic A", calibrated=False):
    calibration = make_calibration(50, 10, **CONTEXT) if calibrated else None
    result = measure_line((10, 10), (20, 20), calibration=calibration, **CONTEXT)
    result["sample"] = {"composition": composition, "microwire": "10/4", "source_kind": "manual"}
    return result


def load_latest(base):
    pointer = json.loads((base / "latest.json").read_text())
    data = (base / pointer["manifest"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == pointer["manifest_sha256"]
    return pointer, json.loads(data)


def test_revisions_preserve_initial_files_and_previous_revisions(tmp_path):
    base = save_session(tmp_path, png(), [], CONTEXT)
    originals = {name: (base / name).read_bytes() for name in ("image.png", "session.json", "measurements.csv")}
    first = revisions.update_session(base, [record()], dict(CONTEXT, notes="first"))
    first_bytes = (first / "session.json").read_bytes()
    first_pointer, first_payload = load_latest(base)
    second = revisions.update_session(base, [record(), record("Synthetic B")], dict(CONTEXT, notes="second"))
    pointer, payload = load_latest(base)
    assert first != second
    assert pointer["revision_id"] != first_pointer["revision_id"]
    assert first_payload["image"]["file"] == "../../image.png"
    assert payload["metadata"]["notes"] == "second"
    assert [r["sample"]["composition"] for r in payload["measurements"]] == ["Synthetic A", "Synthetic B"]
    assert (first / "session.json").read_bytes() == first_bytes
    assert all((base / name).read_bytes() == raw for name, raw in originals.items())
    assert list(base.rglob("image.png")) == [base / "image.png"]
    assert not (first / "image.png").exists()
    assert "Synthetic A" in (second / "measurements.csv").read_text()


def test_calibration_references_are_immutable_portable_and_not_repeated(tmp_path):
    base = save_session(tmp_path, png(), [], CONTEXT)
    reference = png(200, 160)
    digest = hashlib.sha256(reference).hexdigest()
    measured = record(calibrated=True)
    measured["calibration_evidence"] = {"image_sha256": digest, "image_file": f"reference_{digest}.png"}
    metadata = dict(CONTEXT, calibration_evidence=measured["calibration_evidence"])
    original_record, original_metadata = copy.deepcopy(measured), copy.deepcopy(metadata)
    first = revisions.update_session(base, [measured], metadata, reference_images={digest: reference})
    second = revisions.update_session(base, [measured], metadata)
    _, payload = load_latest(base)
    file = payload["measurements"][0]["calibration_evidence"]["image_file"]
    assert (second / file).resolve() == base / f"reference_{digest}.png"
    assert (second / file).resolve().read_bytes() == reference
    assert len(list(base.rglob(f"reference_{digest}.png"))) == 1
    assert measured == original_record and metadata == original_metadata
    assert not list(first.glob("reference_*"))


@pytest.mark.parametrize("defect", ["image", "manifest", "wrong-app", "fake-width", "bad-reference"])
def test_tampered_inputs_never_replace_latest(tmp_path, defect):
    base = save_session(tmp_path, png(), [], CONTEXT)
    revisions.update_session(base, [record()], CONTEXT)
    previous = (base / "latest.json").read_bytes()
    measured = record()
    references = None
    if defect == "image":
        (base / "image.png").write_bytes(png(101, 80))
    elif defect == "manifest":
        payload = json.loads((base / "session.json").read_text())
        payload["metadata"]["notes"] = "changed"
        (base / "session.json").write_text(json.dumps(payload))
    elif defect == "wrong-app":
        payload = json.loads((base / "session.json").read_text())
        payload["application"] = "different application"
        (base / "session.json").write_text(json.dumps(payload))
    elif defect == "fake-width":
        measured["width_px"] = 999
    else:
        references = {"0" * 64: png(200, 160)}
    with pytest.raises(ValueError):
        revisions.update_session(base, [measured], CONTEXT, reference_images=references)
    assert (base / "latest.json").read_bytes() == previous


def test_changed_existing_reference_is_never_overwritten(tmp_path):
    reference = png(200, 160)
    digest = hashlib.sha256(reference).hexdigest()
    base = save_session(tmp_path, png(), [], CONTEXT, reference_images={digest: reference})
    revisions.update_session(base, [], CONTEXT)
    previous = (base / "latest.json").read_bytes()
    (base / f"reference_{digest}.png").write_bytes(b"tampered reference")
    with pytest.raises(ValueError):
        revisions.update_session(base, [], CONTEXT, reference_images={digest: reference})
    assert (base / f"reference_{digest}.png").read_bytes() == b"tampered reference"
    assert (base / "latest.json").read_bytes() == previous


def test_failure_before_pointer_publication_keeps_previous_latest(tmp_path, monkeypatch):
    base = save_session(tmp_path, png(), [], CONTEXT)
    revisions.update_session(base, [record()], CONTEXT)
    previous = (base / "latest.json").read_bytes()
    previous_revisions = set((base / "revisions").iterdir())
    def fail_replace(*args):
        raise OSError("Synthetic crash before pointer publication")
    monkeypatch.setattr(revisions.os, "replace", fail_replace)
    with pytest.raises(OSError, match="Synthetic crash"):
        revisions.update_session(base, [record("new sample")], CONTEXT)
    assert (base / "latest.json").read_bytes() == previous
    load_latest(base)
    assert previous_revisions < set((base / "revisions").iterdir())


def test_corrupt_latest_manifest_is_not_silently_replaced(tmp_path):
    base = save_session(tmp_path, png(), [], CONTEXT)
    folder = revisions.update_session(base, [record()], CONTEXT)
    previous = (base / "latest.json").read_bytes()
    (folder / "session.json").write_text("tampered revision")
    with pytest.raises(ValueError, match="latest revision failed"):
        revisions.update_session(base, [], CONTEXT)
    assert (base / "latest.json").read_bytes() == previous
