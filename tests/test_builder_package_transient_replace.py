"""Bounded Windows-sharing retries preserve Builder stale-target/source guards."""
from pathlib import Path

import pytest

from microwire_data_builder import project_package as package
from microwire_data_builder.safe_codec import SafeCodecError


def payload():
    return {"kind": package.PROJECT_KIND, "sections": {"microscope": {
        "columns": ["Composition", "Microwire", "Brittle"],
        "rows": [{"Composition": "SyntheticAlloy", "Microwire": "1/1", "Brittle": None}],
        "index": [0], "payloads": {},
    }}}


def windows_error(code=5):
    error = PermissionError("Synthetic transient Windows sharing error")
    error.winerror = code
    return error


def write(target, *, streaming=False, replace_existing=False, source_index=None):
    if streaming:
        return package.write_project_package_streaming(
            target, payload()["sections"].items(), replace_existing=replace_existing)
    return package.write_project_package(target, payload(), replace_existing=replace_existing,
                                         source_index=source_index)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("code", [5, 32, 33])
def test_transient_windows_error_retries_and_publishes_valid_package(tmp_path, monkeypatch, streaming, code):
    target = tmp_path / "new.pydpj"
    real_replace = package.os.replace
    attempts, waits = [], []

    def flaky(source, destination):
        attempts.append(Path(destination))
        if len(attempts) < 3:
            raise windows_error(code)
        real_replace(source, destination)

    monkeypatch.setattr(package.os, "replace", flaky)
    monkeypatch.setattr(package.time, "sleep", waits.append)
    index = write(target, streaming=streaming)
    assert len(attempts) == 3
    assert waits == pytest.approx([0.1, 0.2])
    assert index.read_section("microscope")["rows"][0]["Composition"] == "SyntheticAlloy"
    package.inspect_project_package(target, verify_entries=True)
    assert not list(tmp_path.glob(".*.save.lock"))
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("replace_existing", [False, True])
def test_retry_refuses_changed_target_and_preserves_competing_file(tmp_path, monkeypatch, replace_existing):
    target = tmp_path / "project.pydpj"
    if replace_existing:
        target.write_bytes(b"original target")
    calls, waits = [], []

    def competing_replace(source, destination):
        calls.append(destination)
        target.write_bytes(b"new external owner")
        raise windows_error()

    monkeypatch.setattr(package.os, "replace", competing_replace)
    monkeypatch.setattr(package.time, "sleep", waits.append)
    with pytest.raises(SafeCodecError, match="target changed"):
        write(target, replace_existing=replace_existing)
    assert len(calls) == 1
    assert waits == [0.1]
    assert target.read_bytes() == b"new external owner"
    assert not list(tmp_path.glob(".*.save.lock"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_retry_rechecks_indexed_source_and_never_publishes_changed_source(tmp_path, monkeypatch):
    source = tmp_path / "source.pydpj"
    source_index = write(source)
    target = tmp_path / "candidate.pydpj"
    calls = []

    def change_source(source_file, destination):
        calls.append(destination)
        source.write_bytes(b"source changed by external owner")
        raise windows_error()

    monkeypatch.setattr(package.os, "replace", change_source)
    monkeypatch.setattr(package.time, "sleep", lambda _: None)
    with pytest.raises(SafeCodecError, match="changed on disk"):
        write(target, source_index=source_index)
    assert len(calls) == 1
    assert source.read_bytes() == b"source changed by external owner"
    assert not target.exists()  # Only our unchanged empty placeholder is cleaned up.
    assert not list(tmp_path.glob(".*.save.lock"))


def test_persistent_transient_error_is_bounded_and_cleans_only_owned_placeholder(tmp_path, monkeypatch):
    target = tmp_path / "candidate.pydpj"
    calls, waits = [], []

    def blocked(*args):
        calls.append(args)
        raise windows_error(5)

    monkeypatch.setattr(package.os, "replace", blocked)
    monkeypatch.setattr(package.time, "sleep", waits.append)
    with pytest.raises(PermissionError):
        write(target)
    assert len(calls) == 6
    assert len(waits) == 5
    assert sum(waits) == pytest.approx(1.5)
    assert not target.exists()
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.save.lock"))


@pytest.mark.parametrize("code", [None, 2, 112])
def test_other_errors_are_not_retried_and_original_target_survives(tmp_path, monkeypatch, code):
    target = tmp_path / "existing.pydpj"
    target.write_bytes(b"original")
    calls, waits = [], []

    def blocked(*args):
        calls.append(args)
        if code is None:
            raise OSError("Synthetic generic write failure")
        raise windows_error(code)

    monkeypatch.setattr(package.os, "replace", blocked)
    monkeypatch.setattr(package.time, "sleep", waits.append)
    with pytest.raises(OSError):
        write(target, replace_existing=True)
    assert len(calls) == 1
    assert waits == []
    assert target.read_bytes() == b"original"


def test_retry_replaces_unchanged_existing_target(tmp_path, monkeypatch):
    target = tmp_path / "existing.pydpj"
    target.write_bytes(b"original")
    real_replace = package.os.replace
    calls = []

    def flaky(source, destination):
        calls.append(destination)
        if len(calls) == 1:
            raise windows_error(32)
        real_replace(source, destination)

    monkeypatch.setattr(package.os, "replace", flaky)
    monkeypatch.setattr(package.time, "sleep", lambda _: None)
    write(target, replace_existing=True)
    assert len(calls) == 2
    assert package.inspect_project_package(target).read_section("microscope")["rows"]
