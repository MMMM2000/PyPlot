from __future__ import annotations

from pathlib import Path

import pytest

from data_logging.mini_dma_logger.kosice_import import (
    build_annealing_folder_index,
    load_annealing_curve,
    parse_annealing_filename,
)


def test_loads_three_column_tab_separated_txt(tmp_path: Path) -> None:
    path = tmp_path / "Ni44Fe27Ga23Cu3Co3 1_7 s2 100mA cycle2.txt"
    path.write_text(
        "# Current (mA)\tVoltage (V)\tResistance (Ohm)\n"
        "1.5\t0.15\t100\n"
        "2.0\t0.22\t110\n",
        encoding="utf-8",
    )

    frame = load_annealing_curve(path)

    assert frame["I_mA"].tolist() == pytest.approx([1.5, 2.0])
    assert frame["V_V"].tolist() == pytest.approx([0.15, 0.22])
    assert frame["R_Ohm"].tolist() == pytest.approx([100.0, 110.0])
    assert frame["I_A"].tolist() == pytest.approx([0.0015, 0.002])


def test_loads_legacy_four_column_dat_using_real_current_and_resistance(tmp_path: Path) -> None:
    path = tmp_path / "Ni48Fe27Ga23Cu1Co1-1_1.dat"
    path.write_text(
        "ID\n"
        "Iset(mA) Ireal (mA) Ureal (V) R(ohm)\n"
        "1.0 0.8 0.096 120.0\n"
        "2.0 1.9 0.247 130.0\n",
        encoding="utf-8",
    )

    frame = load_annealing_curve(path)

    assert frame["I_mA"].tolist() == pytest.approx([0.8, 1.9])
    assert frame["V_V"].tolist() == pytest.approx([0.096, 0.247])
    assert frame["R_Ohm"].tolist() == pytest.approx([120.0, 130.0])


def test_loads_current_six_column_dat_schema(tmp_path: Path) -> None:
    path = tmp_path / "Ni44Fe27Ga23Cu3Co3_1-1_noload-cyc3-4.dat"
    path.write_text(
        "Cycle\tIset_mA\tIreal_mA\tVoltage_V\tResistance_Ohm\tPower_W\n"
        "1\t1.00\t0.70\t0.07100\t101.42857\t0.00005\n"
        "1\t2.00\t2.00\t0.24300\t121.50000\t0.00049\n",
        encoding="utf-8",
    )

    frame = load_annealing_curve(path)

    assert frame["I_mA"].tolist() == pytest.approx([0.7, 2.0])
    assert frame["V_V"].tolist() == pytest.approx([0.071, 0.243])
    assert frame["R_Ohm"].tolist() == pytest.approx([101.42857, 121.5])


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Ni48Fe27Ga23Cu1Co1-1_1.dat", ("Ni48Fe27Ga23Cu1Co1", 1, 1, "")),
        ("Ni46Fe27Ga23Cu2Co2-2_7-No1.dat", ("Ni46Fe27Ga23Cu2Co2", 2, 7, "No1")),
        (
            "Ni44Fe27Ga23Cu3Co3 1_7 s2 100mA cycle2.txt",
            ("Ni44Fe27Ga23Cu3Co3", 1, 7, "s2 100mA cycle2"),
        ),
        (
            "Ni44Fe27Ga23Cu3Co3_1-1_noload-cyc3-4.dat",
            ("Ni44Fe27Ga23Cu3Co3", 1, 1, "noload-cyc3-4"),
        ),
    ],
)
def test_filename_identity_is_conservative_and_retains_run_annotations(
    filename: str,
    expected: tuple[str, int, int, str],
) -> None:
    record = parse_annealing_filename(Path(filename))

    assert record is not None
    assert (record.composition, record.draw, record.piece, record.annotation) == expected


def test_index_reports_opju_as_unsupported_and_builds_exact_suggestions(tmp_path: Path) -> None:
    annealing = tmp_path / "Current Annealing"
    annealing.mkdir()
    (tmp_path / "Stress-Strain-Ni50Fe27Ga23-CuCo.opju").write_bytes(b"not parsed")
    (annealing / "Ni44Fe27Ga23Cu3Co3 1_7 s2 100mA.txt").write_text(
        "# Current (mA)\tVoltage (V)\tResistance (Ohm)\n1\t0.1\t100\n",
        encoding="utf-8",
    )
    (annealing / "notes.txt").write_text("unrelated", encoding="utf-8")

    index = build_annealing_folder_index(tmp_path, source_label="Košice folder")

    assert index.suggestions() == {"Ni44Fe27Ga23Cu3Co3": ("1/7",)}
    assert len(index.matching("ni44fe27ga23cu3co3", "1_7")) == 1
    assert index.matching("Ni44Fe27Ga23Cu3Co3", "1/8") == ()
    assert [path.suffix for path in index.unsupported_files] == [".opju"]
    assert [path.name for path in index.skipped_files] == ["notes.txt"]


def test_index_honours_cancellation(tmp_path: Path) -> None:
    annealing = tmp_path / "Current Annealing"
    annealing.mkdir()
    (annealing / "Ni44Fe27Ga23Cu3Co3 1_7 s2 100mA.txt").write_text(
        "1\t0.1\t100\n",
        encoding="utf-8",
    )

    with pytest.raises(InterruptedError, match="cancelled"):
        build_annealing_folder_index(
            tmp_path,
            source_label="Košice folder",
            cancelled=lambda: True,
        )
