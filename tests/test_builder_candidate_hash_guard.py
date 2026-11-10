"""Publication can bind reviewed candidate bytes as well as the old target."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from microwire_data_builder import project_maintenance as maintenance, project_package as pp
from microwire_data_builder.safe_codec import SafeCodecError


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_packages(tmp_path):
    target, candidate, backup = (tmp_path / name for name in
                                  ("synthetic-target.pydpj", "synthetic-candidate.pydpj", "backup.pydpj"))
    table = {"columns": ["Composition", "Microwire", "d (µm)"],
             "rows": [{"Composition": "SyntheticAlloy", "Microwire": "1/1", "d (µm)": 10.0}],
             "index": [0]}
    pp.write_project_package(target, {"kind": pp.PROJECT_KIND, "sections": {
        "microscope": {**table, "extra": {"synthetic": True}}}})
    table["rows"][0]["d (µm)"] = 12.0
    maintenance.update_project_tables(target, candidate, {"microscope": table})
    return target, candidate, backup


@pytest.mark.parametrize("guarded", [False, True])
def test_exact_candidate_guard_and_legacy_no_argument_both_publish(tmp_path, guarded):
    target, candidate, backup = fixture_packages(tmp_path)
    previous = target.read_bytes()
    expected = digest(candidate)
    options = {"expected_candidate_sha256": expected} if guarded else {}
    receipt = maintenance.publish_verified_project(candidate, target, backup=backup,
        expected_target_sha256=digest(target), **options)
    assert target.read_bytes() == candidate.read_bytes()
    assert backup.read_bytes() == previous
    assert receipt["published_sha256"] == expected
    assert receipt["strict_verification_passed"] is True


@pytest.mark.parametrize("expected", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64,
                                      "a" * 64 + "\n", True, 123, b"a" * 64])
def test_malformed_candidate_hash_is_rejected_before_any_publication(tmp_path, expected):
    target, candidate, backup = fixture_packages(tmp_path)
    previous = target.read_bytes()
    with pytest.raises(SafeCodecError, match="Expected candidate SHA-256 must be a lowercase hex digest"):
        maintenance.publish_verified_project(candidate, target, backup=backup,
            expected_target_sha256=digest(target), expected_candidate_sha256=expected)
    assert target.read_bytes() == previous
    assert not backup.exists()
    assert not list(tmp_path.glob(".project-publish-*"))
    assert not list(tmp_path.glob(".*.save.lock"))


def test_mismatched_candidate_cannot_reach_staging_backup_or_replace(tmp_path, monkeypatch):
    target, candidate, backup = fixture_packages(tmp_path)
    previous = target.read_bytes()

    def unexpected(*args, **kwargs):
        pytest.fail("A mismatched candidate reached a publication mutation")

    monkeypatch.setattr(maintenance.tempfile, "mkstemp", unexpected)
    monkeypatch.setattr(maintenance, "_copy_exclusive", unexpected)
    monkeypatch.setattr(maintenance.os, "replace", unexpected)
    with pytest.raises(SafeCodecError, match="expected candidate hash does not match"):
        maintenance.publish_verified_project(candidate, target, backup=backup,
            expected_target_sha256=digest(target), expected_candidate_sha256="0" * 64)
    assert target.read_bytes() == previous
    assert not backup.exists()
    assert not list(tmp_path.glob(".*.save.lock"))


def test_candidate_replaced_between_review_and_child_publication_is_rejected(tmp_path):
    target, candidate, backup = fixture_packages(tmp_path)
    reviewed_hash = digest(candidate)
    previous = target.read_bytes()
    replacement = tmp_path / "synthetic-unreviewed-candidate.pydpj"
    section = pp.inspect_project_package(candidate).read_section("microscope", load_payloads=False)
    table = {key: section[key] for key in ("columns", "rows", "index")}
    table["rows"][0]["d (µm)"] = 13.0
    maintenance.update_project_tables(target, replacement, {"microscope": table})
    candidate.write_bytes(replacement.read_bytes())
    pp.inspect_project_package(candidate, verify_entries=True)  # Valid, but unreviewed.
    with pytest.raises(SafeCodecError, match="expected candidate hash does not match"):
        maintenance.publish_verified_project(candidate, target, backup=backup,
            expected_target_sha256=digest(target), expected_candidate_sha256=reviewed_hash)
    assert target.read_bytes() == previous
    assert not backup.exists()
    assert not list(tmp_path.glob(".project-publish-*"))


def test_candidate_guard_remains_bound_to_staged_copy_hash(tmp_path, monkeypatch):
    target, candidate, backup = fixture_packages(tmp_path)
    previous = target.read_bytes()
    reviewed_hash = digest(candidate)
    copy = maintenance.shutil.copyfileobj

    def damaged_copy(source, destination, length):
        copy(source, destination, length)
        destination.write(b"synthetic staging corruption")

    monkeypatch.setattr(maintenance.shutil, "copyfileobj", damaged_copy)
    with pytest.raises(SafeCodecError, match="Candidate changed during staging"):
        maintenance.publish_verified_project(candidate, target, backup=backup,
            expected_target_sha256=digest(target), expected_candidate_sha256=reviewed_hash)
    assert target.read_bytes() == previous
    assert not backup.exists()
    assert not list(tmp_path.glob(".project-publish-*"))


@pytest.mark.parametrize("guard", ["exact", "mismatch", "uppercase", "omitted"])
def test_cli_requires_exact_candidate_when_flag_is_supplied(tmp_path, guard):
    target, candidate, backup = fixture_packages(tmp_path)
    previous = target.read_bytes()
    expected = digest(candidate)
    script = Path(__file__).resolve().parents[1] / "scripts" / "publish_builder_project.py"
    command = [sys.executable, str(script), str(candidate), str(target), "--backup", str(backup),
               "--expected-target-sha256", digest(target)]
    if guard != "omitted":
        supplied = expected if guard == "exact" else "0" * 64 if guard == "mismatch" else expected.upper()
        command.extend(["--expected-candidate-sha256", supplied])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if guard in {"exact", "omitted"}:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["published_sha256"] == expected
        assert backup.read_bytes() == previous
        assert target.read_bytes() == candidate.read_bytes()
    else:
        assert result.returncode != 0
        assert "candidate" in result.stderr.lower()
        assert target.read_bytes() == previous
        assert not backup.exists()
        assert not list(tmp_path.glob(".project-publish-*"))
