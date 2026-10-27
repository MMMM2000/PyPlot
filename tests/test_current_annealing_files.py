import json

import pytest

from plotting.shared.current_annealing_files import discover_measurements, resolve_measurement_path


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_mixed_collection_keeps_distinct_legacy_imports_and_skips_support_files(tmp_path):
    text = "0.01 0.10 10\n0.02 0.20 10\n"
    flat = _write(tmp_path, "sample 20mA.txt", text)
    imported = _write(tmp_path, "legacy imports/sample 20mA.txt", text)
    csv = _write(tmp_path, "run/measurement.csv", "measured_current_mA,resistance_ohm\n10,20\n")
    nested = _write(tmp_path, "vacuum test/run/measurement.csv", csv.read_text())
    _write(tmp_path, "run/run_log.txt", text)
    _write(tmp_path, "notes.txt", text)
    _write(tmp_path, "readme.txt", "A collection of current annealing measurements")
    _write(tmp_path, "metadata/old/measurement.txt", text)
    _write(tmp_path, "archive/old/measurement.txt", text)
    _write(tmp_path, "sample 20mA.transition-review.json", "{}")

    assert discover_measurements(tmp_path) == [imported, csv, flat]
    assert discover_measurements(tmp_path, recursive=True) == [imported, csv, flat, nested]


def test_metadata_selected_csv_is_single_run_and_loads_correct_columns(tmp_path):
    from plotting.plugins.current_annealing.core import load_file

    source = _write(tmp_path, "run/capture.csv", "measured_current_mA,voltage_V,resistance_ohm\n10,0.2,20\n20,0.4,20\n")
    _write(tmp_path, "run/metadata.json", json.dumps({"data_file": source.name}))
    _write(tmp_path, "run/measurement.txt", "stale export")
    assert resolve_measurement_path(source.parent) == source
    assert discover_measurements(tmp_path, recursive=True) == [source]
    frame = load_file(source.parent)
    assert frame["I_mA"].tolist() == [10, 20]
    assert frame["R_Ohm"].tolist() == [20, 20]
    assert frame["V_V"].tolist() == [0.2, 0.4]


@pytest.mark.parametrize("metadata", ["{invalid", '{"data_file":"../outside.csv"}', '{"data_file":42}'])
def test_invalid_or_external_metadata_falls_back_to_local_measurement(tmp_path, metadata):
    source = _write(tmp_path, "run/measurement.txt", "0.01 0.1 10\n")
    _write(tmp_path, "outside.csv", "measured_current_mA,resistance_ohm\n99,20\n")
    _write(tmp_path, "run/metadata.json", metadata)
    assert resolve_measurement_path(source.parent) == source


def test_discovery_supports_decimal_comma_and_labelled_kosice_files(tmp_path):
    decimal = _write(tmp_path, "sample.txt", "0,01\t0,1\t10,0\n")
    labelled = _write(tmp_path, "sample.dat", "Cycle Ireal_mA Voltage_V Resistance_Ohm\n1 10 0.1 10\n")
    assert discover_measurements(tmp_path) == [labelled, decimal]


def test_folder_migration_preserves_normalized_scientific_identity(tmp_path):
    from plotting.plugins.current_annealing.core import load_file
    from plotting.shared.transition_review import dataframe_fingerprint, sidecar_path_for_measurement

    old = _write(tmp_path, "sample 1mA.txt", "0.0002 0.002 10\n0.0005 0.005 10\n0.001 0.01 10\n")
    new = _write(tmp_path, "sample 1mA/measurement.txt", old.read_text())
    before, after = load_file(old), load_file(new)
    assert before["I_mA"].tolist() == pytest.approx([0.2, 0.5, 1.0])
    assert dataframe_fingerprint(before, namespace="current_annealing", columns=["I_mA", "R_Ohm"]) == dataframe_fingerprint(after, namespace="current_annealing", columns=["I_mA", "R_Ohm"])
    assert sidecar_path_for_measurement(new, family="current_annealing") == new.parent / "transition_review.json"
