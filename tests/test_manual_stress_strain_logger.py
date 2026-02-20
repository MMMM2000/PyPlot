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


def test_effective_l0_subtracts_start_offset() -> None:
    effective = logger_mod.MainWindow.effective_initial_length_mm(20.0, 0.1)
    assert effective == pytest.approx(19.9)


def test_strain_with_start10_uses_effective_l0() -> None:
    effective_l0 = logger_mod.MainWindow.effective_initial_length_mm(20.0, 0.1)
    assert effective_l0 is not None
    strain = logger_mod.MainWindow.strain_percent(0.1, effective_l0, 0.0)
    assert strain == pytest.approx(0.50251256, rel=1e-6)


def test_effective_l0_invalid_when_offset_too_large() -> None:
    effective = logger_mod.MainWindow.effective_initial_length_mm(0.1, 0.1)
    assert effective is None


def test_format_area_mm2_uses_scientific_notation_for_tiny_values() -> None:
    text = logger_mod.MainWindow._format_area_mm2(0.00019085)
    assert text == "1.909x10⁻⁴"


def test_format_area_mm2_uses_ui_format_for_regular_values() -> None:
    text = logger_mod.MainWindow._format_area_mm2(0.1234)
    assert text == "0.123"


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


def test_micrometer_display_uses_anchor_points_offset() -> None:
    value = logger_mod.MainWindow.micrometer_display_from_points(
        20.0,
        30,
        anchor_points=10.0,
    )
    assert value == 40


def test_micrometer_display_from_mm_uses_anchor_points_and_wraps() -> None:
    value = logger_mod.MainWindow.micrometer_display_from_mm(
        0.7,  # 70 points
        40,
        anchor_points=10.0,
    )
    assert value == 0


def test_auto_zero_anchor_inserted_for_first_start10_point() -> None:
    should_insert = logger_mod.MainWindow.should_insert_zero_anchor_point(
        existing_point_count=0,
        start_points=10,
        displacement_mm=0.1,
    )
    assert should_insert is True


def test_auto_zero_anchor_not_inserted_with_existing_points() -> None:
    should_insert = logger_mod.MainWindow.should_insert_zero_anchor_point(
        existing_point_count=1,
        start_points=10,
        displacement_mm=0.1,
    )
    assert should_insert is False


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


def test_extract_project_diameter_candidates_prefers_microscope_section() -> None:
    payload = {
        "kind": "MicrowireDataBuilder",
        "sections": {
            "microscope": {
                "rows": [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "5/4",
                        "d (µm)": 19.4,
                    }
                ]
            },
            "other": {
                "rows": [
                    {
                        "Composition": "Other",
                        "Microwire": "1/1",
                        "diameter": 11.0,
                    }
                ]
            },
        },
    }

    candidates = logger_mod.MainWindow.extract_project_diameter_candidates(payload)

    assert len(candidates) == 2
    assert candidates[0]["section"] == "microscope"
    assert candidates[0]["diameter_um"] == pytest.approx(19.4)


def test_extract_project_annealing_candidates_reads_row_sources() -> None:
    payload = {
        "kind": "MicrowireDataBuilder",
        "sections": {
            "annealing": {
                "rows": [
                    {
                        "Composition": "Ni50Fe27Ga23",
                        "Microwire": "9/3",
                        "_sources": [
                            "C:/data/Ni50Fe27Ga23 9_3 s1 1000mA.txt",
                            "C:/data/Ni50Fe27Ga23 9_3 s2 70mA.txt",
                        ],
                    }
                ]
            }
        },
    }

    candidates = logger_mod.MainWindow.extract_project_annealing_candidates(payload)
    assert len(candidates) == 1
    assert candidates[0]["composition"] == "Ni50Fe27Ga23"
    assert candidates[0]["microwire"] == "9/3"
    assert len(candidates[0]["sources"]) == 2


def test_choose_project_diameter_candidate_matches_underscore_microwire() -> None:
    candidates = [
        {
            "composition": "Ni50Fe27Ga23",
            "microwire": "5/4",
            "diameter_um": 19.4,
        },
        {
            "composition": "Ni50Fe27Ga23",
            "microwire": "6/2",
            "diameter_um": 15.0,
        },
    ]
    selected = logger_mod.MainWindow.choose_project_diameter_candidate(
        candidates,
        composition_hint="Ni50Fe27Ga23",
        microwire_hint="5_4",
    )
    assert selected == 0


def test_choose_project_annealing_candidate_matches_wire_hint() -> None:
    candidates = [
        {"composition": "Ni50Fe27Ga23", "microwire": "9/3", "sources": ["a.txt"]},
        {"composition": "Ni50Fe27Ga23", "microwire": "5/4", "sources": ["b.txt"]},
    ]
    selected = logger_mod.MainWindow.choose_project_annealing_candidate(
        candidates,
        composition_hint="Ni50Fe27Ga23",
        microwire_hint="9_3",
    )
    assert selected == 0


def test_annealing_setpoint_from_source_parses_ma_token() -> None:
    setpoint = logger_mod.MainWindow.annealing_setpoint_from_source(
        "Ni50Fe27Ga23 9_3 s1 1000mA.txt"
    )
    assert setpoint == pytest.approx(1000.0)


def test_annealing_current_bucket_detects_high_and_low() -> None:
    high_bucket = logger_mod.MainWindow.annealing_current_bucket(
        source_path="sample s1 1000mA.txt",
        currents_mA=[0.0, 1000.0],
    )
    low_bucket = logger_mod.MainWindow.annealing_current_bucket(
        source_path="sample s2 70mA.txt",
        currents_mA=[0.0, 70.0],
    )
    assert high_bucket == "high"
    assert low_bucket == "low"


def test_filter_annealing_sources_by_sample_prefers_matching_token() -> None:
    sources = [
        "Ni50Fe27Ga23 9_3 s1 1000mA.txt",
        "Ni50Fe27Ga23 9_3 s2 70mA.txt",
    ]
    filtered = logger_mod.MainWindow.filter_annealing_sources_by_sample(sources, "s2")
    assert filtered == ["Ni50Fe27Ga23 9_3 s2 70mA.txt"]


def test_format_single_axis_coord_reports_one_pair() -> None:
    class _Axis:
        @staticmethod
        def format_xdata(value: float) -> str:
            return f"{value:.3f}"

        @staticmethod
        def format_ydata(value: float) -> str:
            return f"{value:.3f}"

    text = logger_mod.MainWindow._format_single_axis_coord(_Axis(), 0.7825, 0.593)
    assert text == "(x, y) = (0.782, 0.593)"


def test_map_linear_value_maps_between_ranges() -> None:
    mapped = logger_mod.MainWindow._map_linear_value(
        0.5,
        src_min=0.0,
        src_max=1.0,
        dst_min=0.0,
        dst_max=50.0,
    )
    assert mapped == pytest.approx(25.0)


def test_microwire_display_text_keeps_slash_separator() -> None:
    assert logger_mod.MicrowireLineEdit.to_display_text("11_1") == "11/1"
    assert logger_mod.MicrowireLineEdit.to_display_text("11/1") == "11/1"


def test_microwire_filename_token_uses_underscore() -> None:
    assert logger_mod.MicrowireLineEdit.to_filename_token("11/1") == "11_1"


def test_countdown_seconds_left_uses_full_timeout_without_change() -> None:
    left = logger_mod.MainWindow.countdown_seconds_left(
        55,
        last_change_ts=None,
        now_ts=100.0,
    )
    assert left == 55


def test_countdown_seconds_left_counts_down_and_clamps_to_zero() -> None:
    left_mid = logger_mod.MainWindow.countdown_seconds_left(
        55,
        last_change_ts=100.0,
        now_ts=120.4,
    )
    left_zero = logger_mod.MainWindow.countdown_seconds_left(
        55,
        last_change_ts=100.0,
        now_ts=200.0,
    )
    assert left_mid == 35
    assert left_zero == 0


def test_dual_axis_coord_from_top_reports_both_ld_and_ss_pairs() -> None:
    class _Axis:
        def __init__(self, xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
            self._xlim = xlim
            self._ylim = ylim

        def get_xlim(self) -> tuple[float, float]:
            return self._xlim

        def get_ylim(self) -> tuple[float, float]:
            return self._ylim

        @staticmethod
        def format_xdata(value: float) -> str:
            return f"{value:.3f}"

        @staticmethod
        def format_ydata(value: float) -> str:
            return f"{value:.3f}"

    window = logger_mod.MainWindow.__new__(logger_mod.MainWindow)
    window.ax_raw = _Axis((0.0, 100.0), (0.0, 20.0))
    window.ax_overlay_top = _Axis((0.0, 10.0), (0.0, 20.0))
    window.ax_overlay_right = _Axis((0.0, 100.0), (0.0, 2000.0))

    text = window._format_dual_axis_coord_from_top(5.0, 10.0)

    assert text == "L/D (x, y) = (50.000, 10.000) | S/S (x, y) = (5.000, 1000.000)"
