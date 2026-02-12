from __future__ import annotations

import importlib

import pytest

pytest.importorskip(
    "PyQt6.QtWidgets",
    reason="Qt widgets backend is unavailable",
    exc_type=ImportError,
)

logger_mod = importlib.import_module(
    "data_logging.manual_stress_strain_logger.manual_stress_strain_logger"
)


def test_stress_conversion_from_grams_to_mpa() -> None:
    stress = logger_mod.MainWindow.stress_mpa_from_load_g(100.0, 0.1)
    assert stress == pytest.approx(124.862, rel=1e-3)


def test_stress_conversion_preserves_sign() -> None:
    stress = logger_mod.MainWindow.stress_mpa_from_load_g(-100.0, 0.1)
    assert stress == pytest.approx(-124.862, rel=1e-3)


def test_strain_percent_from_reference() -> None:
    strain = logger_mod.MainWindow.strain_percent(0.080, 20.0, 0.050)
    assert strain == pytest.approx(0.15)


def test_reference_uses_last_zero_before_loading() -> None:
    reference = None
    preload = True

    reference, preload = logger_mod.MainWindow.update_reference_state(
        reference,
        preload,
        displacement_mm=0.010,
        load_g=0.0,
    )
    assert reference == pytest.approx(0.010)
    assert preload is True

    reference, preload = logger_mod.MainWindow.update_reference_state(
        reference,
        preload,
        displacement_mm=0.020,
        load_g=0.0,
    )
    assert reference == pytest.approx(0.020)
    assert preload is True

    reference, preload = logger_mod.MainWindow.update_reference_state(
        reference,
        preload,
        displacement_mm=0.030,
        load_g=2.0,
    )
    assert reference == pytest.approx(0.020)
    assert preload is False

    # Once loading started, a later zero does not move the locked reference.
    reference, preload = logger_mod.MainWindow.update_reference_state(
        reference,
        preload,
        displacement_mm=0.040,
        load_g=0.0,
    )
    assert reference == pytest.approx(0.020)
    assert preload is False


def test_header_rows_include_long_names_and_units() -> None:
    header, units = logger_mod.MainWindow.header_rows()
    assert header == "Displacement\tLoad\tStrain\tStress"
    assert units == "mm\tg\t%\tMPa"


def test_effective_load_applies_offset() -> None:
    load = logger_mod.MainWindow.effective_load_from_raw(-0.002, 1.234)
    assert load == pytest.approx(1.232)


def test_cache_redirect_detection() -> None:
    assert logger_mod._looks_cache_redirect("C:/microwire_paddle_cache/home/Downloads")


def test_points_mode_uses_10e_minus_2_mm_scale() -> None:
    assert logger_mod.MM_PER_POINT == pytest.approx(0.01)


def test_micrometer_display_wraps_across_cycle() -> None:
    value = logger_mod.MainWindow.micrometer_display_from_points(20.0, 30)
    assert value == 0


def test_segment_split_detects_loading_unloading_loops() -> None:
    strains = [0.0, 0.05, 0.10, 0.06, 0.02, 0.08]
    segments = logger_mod.MainWindow.split_segments_by_strain_direction(strains)
    assert segments == [
        (1, 0, 2),
        (-1, 2, 4),
        (1, 4, 5),
    ]


def test_segment_styles_label_loops_by_direction() -> None:
    strains = [0.0, 0.1, 0.2, 0.1, 0.0, 0.2]
    styles = logger_mod.MainWindow.build_segment_styles(strains)
    labels = [entry[3] for entry in styles]
    assert labels == ["Loading 1", "Unloading 1", "Loading 2"]
