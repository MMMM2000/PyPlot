from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from data_logging.microscope_logger.samples import (
    load_fabrication_samples,
    load_project_samples,
)


def _project(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.pydpj"
    path.write_text(json.dumps({
        "kind": "MicrowireDataBuilder",
        "version": 2,
        "sections": {
            "assemble": {"rows": [
                {"Composition": "SyntheticAlloy", "Microwire": "12_2"},
                {"Composition": "SyntheticAlloy", "Microwire": "3/1"},
                {"Composition": "SyntheticAlloy", "Microwire": "12/2 variant B"},
                {"Composition": "", "Microwire": "5/1"},
            ]},
            "microscope": {"rows": [
                {"Composition": "SyntheticAlloy", "Wire": "12/2"},
            ], "payloads": {"ignored": "not a measurement fixture"}},
        },
    }), encoding="utf-8")
    return path


def test_project_choices_keep_all_bindings_and_source_hash(tmp_path: Path):
    path = _project(tmp_path)
    original = path.read_bytes()
    samples = load_project_samples(path)
    assert [s.microwire for s in samples] == ["3/1", "12/2", "12/2 variant B"]
    choice = samples[1]
    assert choice.composition == "SyntheticAlloy"
    assert choice.source_kind == "project"
    assert choice.source_sha256 == hashlib.sha256(original).hexdigest()
    assert [(r["section"], r["row_index"]) for r in choice.source_rows] == [
        ("assemble", 0), ("microscope", 0)]
    assert choice.source_rows[0]["source_microwire"] == "12_2"
    assert path.read_bytes() == original


def test_project_uses_table_reader_and_rejects_changed_source(tmp_path: Path, monkeypatch):
    from microwire_data_builder import project_package

    path = _project(tmp_path)
    called = []

    def changing_reader(target):
        called.append(target)
        target.write_text("{}", encoding="utf-8")
        return {"sections": {}}

    monkeypatch.setattr(project_package, "load_project_table_projection", changing_reader)
    with pytest.raises(ValueError, match="changed while reading"):
        load_project_samples(path)
    assert called == [path.resolve()]


def test_packaged_project_does_not_read_measurement_payloads(tmp_path: Path, monkeypatch):
    from microwire_data_builder import project_package

    original_json = _project(tmp_path)
    payload = json.loads(original_json.read_text(encoding="utf-8"))
    target = tmp_path / "packaged.pydpj"
    project_package.write_project_package(target, payload)
    original = target.read_bytes()
    original_read = project_package.ProjectReader.read_entry

    def guarded_read(self, entry):
        assert "/payloads/" not in entry
        assert not entry.startswith("blobs/")
        return original_read(self, entry)

    monkeypatch.setattr(project_package.ProjectReader, "read_entry", guarded_read)
    choices = load_project_samples(target)
    assert len(choices) == 3
    assert len(choices[1].source_rows) == 2
    assert target.read_bytes() == original


def test_fabrication_uses_existing_piece_parser_and_preserves_workbook(tmp_path: Path):
    from synthetic_data import write_fabrication_root

    root = write_fabrication_root(tmp_path / "fabrication")
    originals = {p: p.read_bytes() for p in root.rglob("*.xlsx")}
    # Office lock files are not parsed as workbooks.
    (root / "~$locked.xlsx").write_bytes(b"not an Excel workbook")
    samples = load_fabrication_samples(root)
    assert [(s.composition, s.microwire) for s in samples] == [
        ("Ni50Fe27Ga23", "2/1"), ("Ni50Fe27Ga23", "2/2"),
        ("Ni50Fe27Ga23", "4/1"), ("Ni50Fe27Ga23", "4/2"),
        ("Ni54Fe23Ga15", "1/1"), ("Ni54Fe23Ga15", "1/2"),
    ]
    binding = samples[0].source_rows[0]
    assert binding["binding_type"] == "fabrication_piece_key"
    assert (binding["draw"], binding["piece"]) == (2, 1)
    source = Path(binding["workbook"])
    assert binding["workbook_sha256"] == hashlib.sha256(originals[source]).hexdigest()
    assert all(p.read_bytes() == original for p, original in originals.items())


def test_empty_sources_and_bad_paths_do_not_invent_samples(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert load_fabrication_samples(empty) == []
    with pytest.raises(FileNotFoundError):
        load_project_samples(tmp_path / "missing.pydpj")
    with pytest.raises(ValueError, match="fabrication folder"):
        load_fabrication_samples(_project(tmp_path))
