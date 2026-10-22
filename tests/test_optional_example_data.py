from pathlib import Path

import pytest

from plotting.pyplot.visual_check import run_shape_memory_visual_check
from plotting.shared import paths
from scripts import verify_origin_plugins


def test_input_default_and_visual_check_do_not_require_bundled_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    assert paths.sample_dir() == str(tmp_path / "Downloads")
    result = run_shape_memory_visual_check(output_dir=tmp_path / "visual", include_origin=False)
    assert result.errors == ["No input files were provided for manual stress/strain visual check."]


def test_origin_verification_rejects_missing_inputs_before_opening_an_app(tmp_path: Path, monkeypatch) -> None:
    def unexpected_app():
        pytest.fail("Missing input must be rejected before opening Qt or Origin")

    monkeypatch.setattr(verify_origin_plugins, "_ensure_app", unexpected_app)
    with pytest.raises(ValueError, match="existing disposable"):
        verify_origin_plugins.verify_plugins(tmp_path / "out", data_root=tmp_path / "missing")


def test_emulator_missing_optional_examples_produce_synthetic_lines(tmp_path: Path) -> None:
    with pytest.warns(DeprecationWarning):
        from emulators import virtual_serial_emulator_gui as emulator

    four = emulator._read_lines(tmp_path / "stress_dependence" / "missing.txt")
    six = emulator._read_lines(tmp_path / "Maxion" / "missing.txt")
    assert len(four) == len(six) == 60
    assert four[0] == "1.000;2.000;3.000;4.000"
    assert six[-1] == "1.590;2.590;3.590;4.590;5.590;6.590"
