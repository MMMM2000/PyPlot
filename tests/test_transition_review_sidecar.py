from __future__ import annotations

import json

import pandas as pd
import pytest

from plotting.shared.transition_review import (
    TransitionReviewError,
    atomic_write_review,
    dataframe_fingerprint,
    load_review,
    make_review,
    make_target,
    sidecar_path_for_measurement,
    source_file_entry,
)


def _review(frame: pd.DataFrame) -> dict:
    fingerprint = dataframe_fingerprint(
        frame,
        namespace="current_annealing",
        columns=("I_mA", "R_ohm"),
    )
    target = make_target(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        target_key="graph",
        status="manual_adjusted",
        auto_values={"As1": 80.0, "Af1": 100.0, "Ms1": 60.0, "Mf1": 40.0},
        manual_values={"As1": 82.0, "Af1": 101.0},
        final_values={"As1": 82.0, "Af1": 101.0},
        cleared_labels=("Ms1", "Mf1"),
    )
    return make_review(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        targets=[target],
    )


def test_dataframe_fingerprint_is_path_independent_and_value_sensitive() -> None:
    first = pd.DataFrame({"I_mA": [1.0, 2.0], "R_ohm": [10.0, 9.0]})
    same = first.copy()
    changed = first.copy()
    changed.loc[1, "R_ohm"] = 8.5

    identity = dataframe_fingerprint(first, namespace="current_annealing")
    assert dataframe_fingerprint(same, namespace="current_annealing") == identity
    assert dataframe_fingerprint(changed, namespace="current_annealing") != identity


def test_atomic_review_round_trip_and_cleanup(tmp_path) -> None:
    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "R_ohm": [10.0, 9.0]})
    path = tmp_path / "transition_review.json"
    atomic_write_review(path, _review(frame))

    restored = load_review(path)
    assert restored["targets"][0]["status"] == "manual_adjusted"
    assert restored["targets"][0]["analysis_included"] is True
    assert restored["targets"][0]["final_values"] == {"As1": 82.0, "Af1": 101.0}
    assert restored["targets"][0]["cleared_labels"] == ["Mf1", "Ms1"]
    assert not list(tmp_path.glob("*.tmp"))


def test_archive_request_round_trip_is_explicit_and_validated(tmp_path) -> None:
    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "R_ohm": [10.0, 9.0]})
    payload = _review(frame)
    payload["archive_requested"] = True
    payload["archive_requested_utc"] = "2026-08-14T09:00:00Z"
    path = tmp_path / "transition_review.json"

    atomic_write_review(path, payload)
    restored = load_review(path)

    assert restored["archive_requested"] is True
    assert restored["archive_requested_utc"] == "2026-08-14T09:00:00Z"
    payload["archive_requested"] = "yes"
    with pytest.raises(TransitionReviewError, match="archive_requested"):
        atomic_write_review(path, payload)


def test_tma_review_round_trip_preserves_derived_transition_strain(tmp_path) -> None:
    fingerprint = "sha256:" + "d" * 64
    target = make_target(
        family="tma",
        measurement_fingerprint=fingerprint,
        target_key="stress_mpa:50",
        status="manual_adjusted",
        final_values={"As": 20.0, "Af": 40.0},
    )
    target["strain_at_transition_pct"] = {"As": 1.25, "Af": 0.45}
    target["strain_reference"] = {
        "method": "per_target_minimum_length",
        "l0_mm": 35.6,
    }
    payload = make_review(
        family="tma",
        measurement_fingerprint=fingerprint,
        targets=[target],
    )

    path = tmp_path / "transition_review.json"
    atomic_write_review(path, payload)
    restored = load_review(path)["targets"][0]

    assert restored["strain_at_transition_pct"] == {"As": 1.25, "Af": 0.45}
    assert restored["strain_reference"] == {
        "method": "per_target_minimum_length",
        "l0_mm": 35.6,
    }


def test_invalid_status_is_rejected(tmp_path) -> None:
    frame = pd.DataFrame({"I_mA": [1.0], "R_ohm": [10.0]})
    payload = _review(frame)
    payload["targets"][0]["status"] = "silently_accept"
    with pytest.raises(TransitionReviewError):
        atomic_write_review(tmp_path / "transition_review.json", payload)


def test_sidecar_paths_cover_new_folder_and_legacy_flat_ca(tmp_path) -> None:
    folder_measurement = tmp_path / "run01" / "measurement.txt"
    flat_measurement = tmp_path / "old_run.txt"
    assert sidecar_path_for_measurement(
        folder_measurement, family="current_annealing"
    ) == tmp_path / "run01" / "transition_review.json"
    assert sidecar_path_for_measurement(
        flat_measurement, family="current_annealing"
    ) == tmp_path / "old_run.transition-review.json"
    assert sidecar_path_for_measurement(
        tmp_path / "tma_run", family="tma"
    ) == tmp_path / "tma_run" / "transition_review.json"


def test_source_file_entry_uses_relative_path_and_sha256(tmp_path) -> None:
    path = tmp_path / "run" / "measurement.txt"
    path.parent.mkdir()
    path.write_text("data\n", encoding="utf-8")
    entry = source_file_entry(path, relative_to=tmp_path / "run")
    assert entry["path"] == "measurement.txt"
    assert entry["size_bytes"] == path.stat().st_size
    assert len(entry["sha256"]) == 64
    assert json.loads(json.dumps(entry)) == entry

from plotting.shared.transition_review_adapters import current_annealing_review_draft
from microwire_data_builder.core import MeasurementRecord, _load_annealing, _metadata_from_path
from microwire_data_builder.ui import _merge_portable_annealing_review, _portable_annealing_review
def test_current_annealing_folder_metadata_and_review_draft(tmp_path) -> None:
    run_dir = tmp_path / "portable_run"
    run_dir.mkdir()
    measurement = run_dir / "measurement.txt"
    rows = [
        f"{current / 1000:.6f}\t{0.1 + current / 10000:.6f}\t{10 - current / 100:.6f}"
        for current in range(1, 61)
    ]
    measurement.write_text(
        "# Current (mA)\tVoltage (V)\tResistance (Ohm)\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schema": "current_annealing_logger_metadata_v1",
                "composition": "Ni50Fe27Ga23",
                "microwire": "12_2",
                "max_current_mA": 60,
            }
        ),
        encoding="utf-8",
    )

    metadata = _metadata_from_path(measurement, tmp_path)
    assert metadata.composition_token == "Ni50Fe27Ga23"
    assert metadata.draw_x == 12
    assert metadata.piece_y == 2
    assert metadata.setpoint_mA == 60
    assert metadata.file_name == "portable_run"

    draft = current_annealing_review_draft(
        measurement,
        sample={"composition": "Ni50Fe27Ga23", "microwire": "12/2"},
    )
    assert draft["experiment_family"] == "current_annealing"
    assert draft["sample"]["microwire"] == "12/2"
    assert draft["targets"][0]["target_key"] == "graph"
def test_builder_imports_matching_ca_sidecar_and_preserves_conflict(tmp_path) -> None:
    run_dir = tmp_path / "reviewed_run"
    run_dir.mkdir()
    measurement = run_dir / "measurement.txt"
    rows = [f"{value / 1000:.6f}\t0.1\t{10 - value / 100:.6f}" for value in range(1, 61)]
    measurement.write_text("\n".join(rows) + "\n", encoding="utf-8")
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schema": "current_annealing_logger_metadata_v1",
                "composition": "Ni50Fe27Ga23",
                "microwire": "12_2",
                "max_current_mA": 60,
            }
        ),
        encoding="utf-8",
    )
    draft = current_annealing_review_draft(measurement)
    target = draft["targets"][0]
    target.update(
        status="manual_adjusted",
        included=True,
        manual_values={"As1": 20.0},
        final_values={"As1": 20.0},
    )
    atomic_write_review(run_dir / "transition_review.json", draft)
    metadata = _metadata_from_path(measurement, tmp_path)
    record = MeasurementRecord(
        measurement,
        metadata,
        _load_annealing(measurement, expected_setpoint_mA=60),
        True,
        0.0,
    )

    portable = _portable_annealing_review(record)
    assert portable["status"] == "manual_adjusted"
    assert portable["final_values_mA"] == {"As1": 20.0}
    conflict = _merge_portable_annealing_review(
        {"status": "accepted_auto", "final_values_mA": {"As1": 18.0}},
        portable,
    )
    assert conflict["status"] == "needs_attention"
    assert conflict["portable_conflict"] == "project_and_sidecar_differ"
    assert conflict["project_review"]["final_values_mA"] == {"As1": 18.0}

def test_review_dialog_does_not_overwrite_first_saved_target_on_open(tmp_path, qtbot) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "R_ohm": [10.0, 9.0]})
    payload = _review(frame)
    dialog = PortableTransitionReviewDialog(
        payload,
        {
            "graph": ReviewPlot(
                frame["I_mA"],
                frame["R_ohm"],
                "test run",
                "Resistance (ohm)",
            )
        },
        tmp_path / "transition_review.json",
    )
    qtbot.addWidget(dialog)

    target = dialog.payload["targets"][0]
    assert target["status"] == "manual_adjusted"
    assert target["manual_values"] == {"As1": 82.0, "Af1": 101.0}
    assert target["final_values"] == {"As1": 82.0, "Af1": 101.0}

    assert dialog.choice_buttons["As1"]["manual"].isChecked()
    assert dialog.choice_buttons["Af1"]["manual"].isChecked()
    assert dialog.choice_buttons["Ms1"]["not_observed"].isChecked()
    assert dialog.choice_buttons["Mf1"]["not_observed"].isChecked()
    assert dialog.values_table.rowCount() == 4
    assert dialog.target_panel.isHidden()
    assert dialog.save_button.isEnabled()
    assert not hasattr(dialog, "decision_group")
    assert not hasattr(dialog, "value_edits")
    assert not hasattr(dialog, "omit_button")

    dialog.show()
    qtbot.wait(20)
    canvas_top_left = dialog.canvas.mapTo(dialog, dialog.canvas.rect().topLeft())
    values_top_left = dialog.values_box.mapTo(
        dialog, dialog.values_box.rect().topLeft()
    )
    assert canvas_top_left.x() + dialog.canvas.width() < values_top_left.x()
    assert dialog.values_box.width() <= 390
    assert dialog.width() <= 1050
    assert dialog.height() <= 560

    dialog.exclude_check.setChecked(True)
    dialog._store_target_controls()  # noqa: SLF001
    assert target["status"] == "excluded"
    assert target["included"] is False
    assert target["analysis_included"] is False
    assert target["final_values"] == {"As1": 82.0, "Af1": 101.0}

    dialog.exclude_check.setChecked(False)
    for label in ("As1", "Af1", "Ms1", "Mf1"):
        dialog.choice_buttons[label]["not_observed"].click()
    assert target["status"] == "no_transition"
    assert target["included"] is False
    assert target["analysis_included"] is True
    assert target["manual_values"] == {}
    assert target["final_values"] == {}
    assert target["cleared_labels"] == ["Af1", "As1", "Mf1", "Ms1"]

    for label in ("As1", "Af1", "Ms1", "Mf1"):
        dialog.choice_buttons[label]["auto"].click()
    assert target["status"] == "accepted_auto"
    assert target["manual_values"] == {}
    assert target["final_values"] == {
        "As1": 80.0,
        "Af1": 100.0,
        "Ms1": 60.0,
        "Mf1": 40.0,
    }
    assert target["cleared_labels"] == []

    dialog.values_table.selectRow(0)
    event = type("PlotClick", (), {"inaxes": object(), "xdata": 83.5})()
    dialog._plot_clicked(event)  # noqa: SLF001
    assert dialog.choice_buttons["As1"]["manual"].isChecked()
    assert dialog.save_button.isEnabled()
    assert target["status"] == "manual_adjusted"
    assert target["manual_values"] == {"As1": 83.5}
    assert target["final_values"]["As1"] == 83.5
    assert target["final_values"]["Af1"] == 100.0

    dialog.choice_buttons["As1"]["not_observed"].click()
    assert target["cleared_labels"] == ["As1"]
    assert "As1" not in target["final_values"]
    assert target["final_values"]["Af1"] == 100.0

def test_review_dialog_disables_confusing_si_prefix_on_strain_axis(
    tmp_path, qtbot
) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    frame = pd.DataFrame({"current_mA": [0.0, 1.0], "strain_pct": [0.0, 0.15]})
    payload = _review(frame)
    dialog = PortableTransitionReviewDialog(
        payload,
        {
            "graph": ReviewPlot(
                frame["current_mA"],
                frame["strain_pct"],
                "strain run",
                "Strain (%)",
            )
        },
        tmp_path / "transition_review.json",
    )
    qtbot.addWidget(dialog)

    assert dialog.plot_item.getAxis("left").autoSIPrefix is False


def test_review_dialog_accept_auto_writes_the_review_sidecar(tmp_path, qtbot) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "R_ohm": [10.0, 9.0]})
    payload = _review(frame)
    target = payload["targets"][0]
    target.update(
        status="unreviewed",
        included=False,
        analysis_included=False,
        manual_values={},
        final_values={},
        cleared_labels=[],
    )
    original_revision = int(payload.get("review_revision", 0))
    sidecar = tmp_path / "transition_review.json"
    dialog = PortableTransitionReviewDialog(
        payload,
        {
            "graph": ReviewPlot(
                frame["I_mA"],
                frame["R_ohm"],
                "test run",
                "Resistance (ohm)",
            )
        },
        sidecar,
    )
    qtbot.addWidget(dialog)

    assert not dialog.save_button.isEnabled()
    for label in ("As1", "Af1", "Ms1", "Mf1"):
        dialog.choice_buttons[label]["auto"].click()
    assert dialog.save_button.isEnabled()
    dialog._save_and_accept()  # noqa: SLF001

    restored = load_review(sidecar)
    assert restored["review_revision"] == original_revision + 1
    assert restored["targets"][0]["status"] == "accepted_auto"
    assert restored["targets"][0]["final_values"] == {
        "As1": 80.0,
        "Af1": 100.0,
        "Ms1": 60.0,
        "Mf1": 40.0,
    }


def test_review_dialog_marks_complete_run_for_archive_without_moving_it(
    tmp_path, qtbot
) -> None:
    from PyQt6 import QtCore
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
        _review_units_from_payload,
    )

    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "R_ohm": [10.0, 9.0]})
    sidecar = tmp_path / "transition_review.json"
    dialog = PortableTransitionReviewDialog(
        _review(frame),
        {
            "graph": ReviewPlot(
                frame["I_mA"], frame["R_ohm"], "run", "Resistance (ohm)"
            )
        },
        sidecar,
    )
    qtbot.addWidget(dialog)
    original_status = dialog.payload["targets"][0]["status"]

    qtbot.mouseClick(dialog.archive_button, QtCore.Qt.MouseButton.LeftButton)
    target = dialog.payload["targets"][0]
    assert dialog.payload["archive_requested"] is True
    assert target["status"] == "excluded"
    assert target["analysis_included"] is False
    assert dialog.save_button.isEnabled()
    assert not dialog.values_box.isEnabled()
    assert "no files" in dialog.decision_summary.text().lower()

    qtbot.mouseClick(dialog.archive_button, QtCore.Qt.MouseButton.LeftButton)
    assert "archive_requested" not in dialog.payload
    assert target["status"] == original_status
    assert dialog.values_box.isEnabled()

    qtbot.mouseClick(dialog.archive_button, QtCore.Qt.MouseButton.LeftButton)
    dialog._save_and_accept()  # noqa: SLF001
    restored = load_review(sidecar)
    assert restored["archive_requested"] is True
    assert restored["targets"][0]["analysis_included"] is False
    assert {
        summary.state for summary in _review_units_from_payload(restored)
    } == {"archive_requested"}


def test_review_dialog_requires_each_choice_and_uses_human_tma_labels(
    tmp_path,
    qtbot,
) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "strain": [0.0, 0.1]})
    fingerprint = dataframe_fingerprint(frame, namespace="tma")
    targets = []
    plots = {}
    for stress, load in ((50.0, 1.46), (100.0, 2.92)):
        key = f"stress_mpa:{stress:.9g}"
        target = make_target(
            family="tma",
            measurement_fingerprint=fingerprint,
            target_key=key,
            auto_values={"As": 30.0, "Af": 70.0},
        )
        target["target"] = {"stress_mpa": stress, "load_g": load}
        targets.append(target)
        plots[key] = ReviewPlot(frame["I_mA"], frame["strain"], key, "Strain (%)")
    payload = make_review(
        family="tma",
        measurement_fingerprint=fingerprint,
        targets=targets,
    )
    dialog = PortableTransitionReviewDialog(
        payload,
        plots,
        tmp_path / "transition_review.json",
    )
    qtbot.addWidget(dialog)

    assert not dialog.target_panel.isHidden()
    assert dialog.target_list.item(0).text() == "50 MPa · 1.46 g"
    assert dialog.target_list.item(1).text() == "100 MPa · 2.92 g"
    assert not dialog.choice_buttons["Ms"]["auto"].isEnabled()
    assert not dialog.save_button.isEnabled()

    dialog.choice_buttons["As"]["manual"].click()
    dialog.manual_value_edit.setText("31.5")
    dialog.choice_buttons["Af"]["auto"].click()
    dialog.choice_buttons["Ms"]["not_observed"].click()
    dialog.choice_buttons["Mf"]["not_observed"].click()
    assert not dialog.save_button.isEnabled()
    assert "remaining target" in dialog.decision_summary.text()
    first = dialog.payload["targets"][0]
    assert first["status"] == "manual_adjusted"
    assert first["final_values"] == {"As": 31.5, "Af": 70.0}
    assert first["cleared_labels"] == ["Mf", "Ms"]

    dialog.target_list.setCurrentRow(1)
    for label in ("As", "Af", "Ms", "Mf"):
        dialog.choice_buttons[label]["not_observed"].click()
    assert dialog.save_button.isEnabled()
    assert dialog.payload["targets"][1]["status"] == "no_transition"
    assert "4 not observed" in dialog.decision_summary.text()


def test_tma_review_dialog_derives_point_strain_and_retains_it_when_excluded(
    tmp_path,
    qtbot,
) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    current = pd.Series([0.0, 10.0, 20.0, 10.0, 0.0])
    strain = pd.Series([0.0, 1.0, 2.0, 3.0, 4.0])
    frame = pd.DataFrame({"current_mA": current, "strain_pct": strain})
    fingerprint = dataframe_fingerprint(frame, namespace="tma")
    target = make_target(
        family="tma",
        measurement_fingerprint=fingerprint,
        target_key="stress_mpa:50",
        auto_values={"As": 5.0, "Af": 15.0, "Ms": 15.0, "Mf": 5.0},
    )
    target["target"] = {"stress_mpa": 50.0}
    payload = make_review(
        family="tma",
        measurement_fingerprint=fingerprint,
        targets=[target],
    )
    dialog = PortableTransitionReviewDialog(
        payload,
        {
            "stress_mpa:50": ReviewPlot(
                current,
                strain,
                "test TMA run",
                "Strain (%)",
                derives_transition_strain=True,
                strain_reference={
                    "method": "per_target_minimum_length",
                    "l0_mm": 35.6,
                },
            )
        },
        tmp_path / "transition_review.json",
    )
    qtbot.addWidget(dialog)

    for label in ("As", "Af", "Ms", "Mf"):
        dialog.choice_buttons[label]["auto"].click()

    stored = dialog.payload["targets"][0]
    assert stored["strain_at_transition_pct"] == pytest.approx(
        {"As": 0.5, "Af": 1.5, "Ms": 2.5, "Mf": 3.5}
    )
    assert stored["strain_reference"]["l0_mm"] == pytest.approx(35.6)
    dialog.values_table.selectRow(dialog._row_for_label("As"))  # noqa: SLF001
    assert "As strain: 0.5%" in dialog.derived_strain_label.text()

    dialog.exclude_check.setChecked(True)
    assert stored["status"] == "excluded"
    assert stored["strain_at_transition_pct"]["Af"] == pytest.approx(1.5)

    dialog.exclude_check.setChecked(False)
    for label in ("As", "Af", "Ms", "Mf"):
        dialog.choice_buttons[label]["not_observed"].click()
    assert stored["status"] == "no_transition"
    assert stored["strain_at_transition_pct"] == {}

def test_review_dialog_uses_pyqtgraph_and_reuses_marker_items(tmp_path, qtbot) -> None:
    import pyqtgraph as pg

    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "R_ohm": [10.0, 9.0]})
    dialog = PortableTransitionReviewDialog(
        _review(frame),
        {
            "graph": ReviewPlot(
                frame["I_mA"], frame["R_ohm"], "test run", "Resistance (ohm)"
            )
        },
        tmp_path / "transition_review.json",
    )
    qtbot.addWidget(dialog)

    assert isinstance(dialog.plot_widget, pg.PlotWidget)
    assert dialog.canvas is dialog.plot_widget
    first_auto = dialog._auto_marker_items["As1"]  # noqa: SLF001
    first_manual = dialog._manual_marker_items["As1"]  # noqa: SLF001
    dialog._draw_target()  # noqa: SLF001
    assert dialog._auto_marker_items["As1"] is first_auto  # noqa: SLF001
    assert dialog._manual_marker_items["As1"] is first_manual  # noqa: SLF001

    dialog._manual_marker_moved("As1", 84.25)  # noqa: SLF001
    target = dialog.payload["targets"][0]
    assert target["manual_values"]["As1"] == 84.25
    assert target["final_values"]["As1"] == 84.25


def test_review_dialog_draws_heating_and_cooling_as_separate_colours(
    tmp_path,
    qtbot,
) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    current = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0])
    resistance = pd.Series([10.0, 11.0, 12.0, 9.0, 8.0])
    frame = pd.DataFrame({'I_mA': current, 'R_ohm': resistance})
    dialog = PortableTransitionReviewDialog(
        _review(frame),
        {
            'graph': ReviewPlot(
                current,
                resistance,
                'one cycle',
                'Resistance (ohm)',
            )
        },
        tmp_path / 'transition_review.json',
    )
    qtbot.addWidget(dialog)

    heating_x, _heating_y = dialog.heating_curve_item.getData()
    cooling_x, _cooling_y = dialog.cooling_curve_item.getData()
    assert heating_x.tolist() == pytest.approx([1.0, 2.0, 3.0])
    assert cooling_x.tolist() == pytest.approx([3.0, 2.0, 1.0])
    assert dialog.heating_curve_item.opts['pen'].color().name() == '#ef4444'
    assert dialog.cooling_curve_item.opts['pen'].color().name() == '#3b82f6'
    heating_symbols, _ = dialog.heating_symbol_item.getData()
    cooling_symbols, _ = dialog.cooling_symbol_item.getData()
    assert heating_symbols.tolist() == pytest.approx([1.0, 2.0, 3.0])
    assert cooling_symbols.tolist() == pytest.approx([3.0, 2.0, 1.0])


def test_review_dialog_decimates_symbols_for_dense_traces(tmp_path, qtbot) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    current = pd.Series(
        [float(value) for value in range(1000)]
        + [float(value) for value in range(999, -1, -1)]
    )
    resistance = pd.Series(float(value) for value in range(len(current)))
    frame = pd.DataFrame({'I_mA': current, 'R_ohm': resistance})
    dialog = PortableTransitionReviewDialog(
        _review(frame),
        {'graph': ReviewPlot(current, resistance, 'dense', 'Resistance (ohm)')},
        tmp_path / 'transition_review.json',
    )
    qtbot.addWidget(dialog)

    heating_symbols, _ = dialog.heating_symbol_item.getData()
    cooling_symbols, _ = dialog.cooling_symbol_item.getData()
    assert 0 < len(heating_symbols) <= 180
    assert 0 < len(cooling_symbols) <= 180


def test_current_annealing_cycles_are_reviewed_independently(tmp_path, qtbot) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "R_ohm": [10.0, 9.0]})
    fingerprint = dataframe_fingerprint(
        frame, namespace="current_annealing", columns=("I_mA", "R_ohm")
    )
    auto = {
        "As1": 20.0,
        "Af1": 30.0,
        "Ms1": 18.0,
        "Mf1": 12.0,
        "As2": 22.0,
        "Af2": 32.0,
        "Ms2": 19.0,
        "Mf2": 13.0,
    }
    target = make_target(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        target_key="graph",
        auto_values=auto,
    )
    payload = make_review(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        targets=[target],
    )
    dialog = PortableTransitionReviewDialog(
        payload,
        {
            "graph": ReviewPlot(
                frame["I_mA"], frame["R_ohm"], "two cycles", "Resistance (ohm)"
            )
        },
        tmp_path / "transition_review.json",
    )
    qtbot.addWidget(dialog)

    dialog.show()
    qtbot.wait(20)
    assert dialog.review_unit_row.isVisible() is False
    assert [dialog.target_list.item(index).text() for index in range(2)] == [
        "Cycle 1",
        "Cycle 2",
    ]
    assert set(dialog.choice_buttons) == {"As1", "Af1", "Ms1", "Mf1"}
    assert {
        label for label, marker in dialog._auto_marker_items.items() if marker.isVisible()  # noqa: SLF001
    } == {"As1", "Af1", "Ms1", "Mf1"}
    for label in tuple(dialog.choice_buttons):
        dialog.choice_buttons[label]["auto"].click()
    assert not dialog.save_button.isEnabled()
    dialog.target_list.setCurrentRow(1)
    assert set(dialog.choice_buttons) == {"As2", "Af2", "Ms2", "Mf2"}
    assert {
        label for label, marker in dialog._auto_marker_items.items() if marker.isVisible()  # noqa: SLF001
    } == {"As2", "Af2", "Ms2", "Mf2"}
    assert dialog.payload["targets"][0]["final_values"] == {
        "As1": 20.0,
        "Af1": 30.0,
        "Ms1": 18.0,
        "Mf1": 12.0,
    }
    for label in tuple(dialog.choice_buttons):
        dialog.choice_buttons[label]["not_observed"].click()
    assert dialog.save_button.isEnabled()
    stored = dialog.payload["targets"][0]
    assert stored["status"] == "manual_adjusted"
    assert stored["cleared_labels"] == ["Af2", "As2", "Mf2", "Ms2"]

    dialog.target_list.setCurrentRow(0)
    assert all(
        dialog.choice_buttons[label]["auto"].isChecked()
        for label in ("As1", "Af1", "Ms1", "Mf1")
    )


def test_third_current_annealing_cycle_does_not_borrow_legacy_values(
    tmp_path, qtbot
) -> None:
    from plotting.plugins.current_annealing.core import AnnealingReviewCycle
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    frame = pd.DataFrame({"I_mA": [1.0, 2.0], "R_Ohm": [10.0, 11.0]})
    fingerprint = dataframe_fingerprint(
        frame, namespace="current_annealing", columns=("I_mA", "R_Ohm")
    )
    legacy_values = {
        f"{point}{cycle}": float(10 * cycle + offset)
        for cycle in (1, 2)
        for offset, point in enumerate(("As", "Af", "Ms", "Mf"), start=1)
    }
    target = make_target(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        target_key="graph",
        status="manual_adjusted",
        manual_values=legacy_values,
        final_values=legacy_values,
    )
    payload = make_review(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        targets=[target],
    )
    branch = AnnealingReviewCycle(frame, frame, True, "Recorded cooling ramp.")
    dialog = PortableTransitionReviewDialog(
        payload,
        {
            "graph": ReviewPlot(
                frame["I_mA"],
                frame["R_Ohm"],
                "three cycles",
                "Resistance (ohm)",
                unit_branches={f"Cycle {cycle}": branch for cycle in (1, 2, 3)},
            )
        },
        tmp_path / "transition_review.json",
    )
    qtbot.addWidget(dialog)
    dialog.target_list.setCurrentRow(2)

    assert set(dialog.choice_buttons) == {"As3", "Af3", "Ms3", "Mf3"}
    assert all(choice is None for choice in dialog._choices.values())  # noqa: SLF001
    for label in tuple(dialog.choice_buttons):
        dialog.choice_buttons[label]["not_observed"].click()
    dialog._store_target_controls()  # noqa: SLF001
    atomic_write_review(tmp_path / "transition_review.json", dialog.payload)
    stored = load_review(tmp_path / "transition_review.json")["targets"][0]
    assert set(stored["cleared_labels"]) >= {"As3", "Af3", "Ms3", "Mf3"}


def test_header_only_current_annealing_run_can_open_for_archive(tmp_path, qtbot) -> None:
    from plotting.shared.transition_review_dialog import (
        _build_current_annealing_review_dialog,
    )

    measurement = tmp_path / "interrupted.txt"
    measurement.write_text(
        "# Current (mA)\tVoltage (V)\tResistance (Ohm)\n",
        encoding="utf-8",
    )

    dialog = _build_current_annealing_review_dialog(None, measurement)
    qtbot.addWidget(dialog)

    assert dialog.target_list.count() == 1
    assert dialog.target_list.item(0).text() == "Current Annealing"
    assert dialog.archive_button.isEnabled()
    assert dialog.heating_curve_item.getData()[0] is None


def test_current_annealing_review_hides_absent_cooling_until_overridden(
    tmp_path,
    qtbot,
) -> None:
    from plotting.plugins.current_annealing.core import AnnealingReviewCycle
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        ReviewPlot,
    )

    heating = pd.DataFrame(
        {"I_mA": [1.0, 2.0, 3.0], "R_Ohm": [10.0, 11.0, 12.0]}
    )
    limited_tail = pd.DataFrame(
        {"I_mA": [3.0, 2.9, 2.8], "R_Ohm": [12.0, 12.5, 13.0]}
    )
    frame = pd.concat((heating, limited_tail), ignore_index=True)
    fingerprint = dataframe_fingerprint(
        frame, namespace="current_annealing", columns=("I_mA", "R_Ohm")
    )
    target = make_target(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        target_key="graph",
        status="manual_adjusted",
        auto_values={"As1": 1.5, "Af1": 2.5, "Ms1": 2.4, "Mf1": 1.4},
        manual_values={"Ms1": 2.35},
        final_values={"Ms1": 2.35},
    )
    payload = make_review(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        targets=[target],
    )
    branch = AnnealingReviewCycle(
        heating=heating,
        cooling=limited_tail,
        cooling_recorded=False,
        cooling_reason=(
            "No cooling ramp: voltage remained at its 30 V ceiling while "
            "resistance increased."
        ),
    )
    dialog = PortableTransitionReviewDialog(
        payload,
        {
            "graph": ReviewPlot(
                frame["I_mA"],
                frame["R_Ohm"],
                "voltage limited",
                "Resistance (ohm)",
                unit_branches={"Cycle 1": branch},
            )
        },
        tmp_path / "transition_review.json",
    )
    qtbot.addWidget(dialog)

    assert set(dialog.choice_buttons) == {"As1", "Af1"}
    cooling_x, _cooling_y = dialog.cooling_curve_item.getData()
    assert cooling_x is None or cooling_x.size == 0
    assert dialog.cooling_branch_check.isChecked() is False
    assert "30 V ceiling" in dialog.cooling_branch_reason.text()

    dialog.cooling_branch_check.click()

    assert set(dialog.choice_buttons) == {"As1", "Af1", "Ms1", "Mf1"}
    cooling_x, _cooling_y = dialog.cooling_curve_item.getData()
    assert cooling_x.tolist() == pytest.approx([3.0, 2.9, 2.8])
    assert dialog.choice_buttons["Ms1"]["manual"].isChecked()
    assert dialog.payload["targets"][0]["final_values"]["Ms1"] == pytest.approx(2.35)
    assert target.get("cooling_branch_overrides") is None
    assert dialog.payload["targets"][0]["cooling_branch_overrides"] == {
        "Cycle 1": True
    }

def test_tma_review_draft_preserves_repeated_stress_sweeps(
    tmp_path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from plotting.plugins.mini_dma import core as tma_core
    from plotting.shared.transition_review_adapters import tma_review_draft

    measurement = tmp_path / "measurement.csv"
    measurement.write_text("synthetic\n", encoding="utf-8")
    frame = pd.DataFrame(
        {
            "elapsed_s": [0.0, 1.0],
            "automation_target_value": [50.0, 50.0],
            "current_mA": [1.0, 2.0],
            "strain_pct": [0.0, 0.1],
            "resistance_ohm": [10.0, 9.0],
        }
    )
    run = SimpleNamespace(
        frame=frame,
        measurement_path=measurement,
        sample_name="synthetic sample",
    )
    summaries = SimpleNamespace(
        targets=(
            SimpleNamespace(
                stress_mpa=50.0,
                load_g=1.0,
                as_current_mA=10.0,
                af_current_mA=20.0,
                ms_current_mA=None,
                mf_current_mA=None,
            ),
            SimpleNamespace(
                stress_mpa=50.0,
                load_g=1.0,
                as_current_mA=12.0,
                af_current_mA=22.0,
                ms_current_mA=None,
                mf_current_mA=None,
            ),
        )
    )
    monkeypatch.setattr(tma_core, "load_run", lambda _path: run)
    monkeypatch.setattr(tma_core, "supports_transition_review", lambda _run: True)
    monkeypatch.setattr(tma_core, "summarize_current_sweep", lambda _run: summaries)

    draft = tma_review_draft(tmp_path)

    assert len(draft["targets"]) == 2
    first, second = draft["targets"]
    assert first["target_key"] == "stress_mpa:50|sweep:1"
    assert second["target_key"] == "stress_mpa:50|sweep:2"
    assert first["auto_values"] == {"As": 10.0, "Af": 20.0}
    assert second["auto_values"] == {"As": 12.0, "Af": 22.0}
    assert first["target"]["sweep_index"] == 1
    assert second["target"]["sweep_index"] == 2
    assert first["target"]["sweep_count"] == 2
    assert second["target"]["sweep_count"] == 2

def test_builder_imports_matching_tma_sidecar_by_stress_target(
    tmp_path, monkeypatch
) -> None:
    import logging
    from types import SimpleNamespace

    from microwire_data_builder import ui as builder_ui
    from plotting.shared import transition_review as review_module
    from plotting.shared import transition_review_adapters as adapter_module

    run_dir = tmp_path / "tma_run"
    run_dir.mkdir()
    (run_dir / "transition_review.json").write_text("{}", encoding="utf-8")
    fingerprint = "sha256:" + "a" * 64
    payload = {
        "experiment_family": "tma",
        "measurement_fingerprint": fingerprint,
        "review_revision": 2,
        "targets": [
            {
                "status": "manual_adjusted",
                "target": {"stress_mpa": 30.0},
                "auto_values": {"As": 20.0},
                "manual_values": {"As": 21.0},
                "final_values": {"As": 21.0},
                "strain_at_transition_pct": {"As": 1.75},
                "strain_reference": {
                    "method": "per_target_minimum_length",
                    "l0_mm": 35.6,
                },
                "cleared_labels": [],
            }
        ],
    }
    entry = SimpleNamespace(
        sample="sample",
        run_label="run",
        target_label="30 MPa",
        status="accepted",
        target_summary=SimpleNamespace(stress_mpa=30.0),
    )
    monkeypatch.setattr(review_module, "load_review", lambda _path: payload)
    monkeypatch.setattr(
        adapter_module,
        "tma_review_draft",
        lambda _path: {"measurement_fingerprint": fingerprint},
    )
    monkeypatch.setattr(
        builder_ui,
        "_mini_dma_transition_review_entries",
        lambda _records, _logger: [entry],
    )
    record = SimpleNamespace(path=run_dir)
    reviews = {}

    assert builder_ui._import_portable_tma_reviews(
        [record], reviews, logging.getLogger(__name__)
    ) is True
    imported = next(iter(reviews.values()))
    assert imported["status"] == "accepted"
    assert imported["analysis_included"] is True
    assert imported["values"] == {"As": 21.0}
    assert imported["manual_values_mA"] == {"As": 21.0}
    assert imported["strain_at_transition_pct"] == {"As": 1.75}
    assert imported["strain_reference"] == {
        "method": "per_target_minimum_length",
        "l0_mm": 35.6,
    }
    assert imported["portable_review_revision"] == 2


def test_backfill_candidate_never_uses_an_exact_path_outside_roots(tmp_path) -> None:
    from scripts.backfill_transition_reviews import _candidate

    allowed = tmp_path / "Prague"
    outside = tmp_path / "Kosice" / "measurement.txt"
    allowed.mkdir()
    outside.parent.mkdir()
    outside.write_text("outside\n", encoding="utf-8")

    candidate, match = _candidate(outside, (), [allowed.resolve()])
    assert candidate is None
    assert match == "outside_roots"

    inside = allowed / outside.name
    inside.write_text("inside\n", encoding="utf-8")
    candidate, match = _candidate(outside, (outside.name,), [allowed.resolve()])
    assert candidate == inside.resolve()
    assert match == "unique_name"

    candidate, match = _candidate(
        outside,
        (outside.name,),
        [allowed.resolve()],
        name_index={},
    )
    assert candidate is None
    assert match == "outside_roots"

def test_backfill_distinguishes_no_transition_from_excluded_values() -> None:
    from scripts.backfill_transition_reviews import _apply_ca_review, _apply_tma_reviews

    fingerprint = "sha256:" + "b" * 64
    ca_draft = make_review(
        family="current_annealing",
        measurement_fingerprint=fingerprint,
        targets=[
            make_target(
                family="current_annealing",
                measurement_fingerprint=fingerprint,
                target_key="graph",
                auto_values={"As1": 20.0},
            )
        ],
    )
    _apply_ca_review(
        ca_draft,
        {
            "status": "excluded",
            "final_values_mA": {"As1": 21.0},
        },
    )
    assert ca_draft["targets"][0]["included"] is False
    assert ca_draft["targets"][0]["analysis_included"] is False
    assert ca_draft["targets"][0]["final_values"] == {"As1": 21.0}

    _apply_ca_review(
        ca_draft,
        {
            "status": "no_transition",
            "final_values_mA": {"As1": 99.0},
        },
    )
    assert ca_draft["targets"][0]["included"] is False
    assert ca_draft["targets"][0]["analysis_included"] is True
    assert ca_draft["targets"][0]["final_values"] == {}

    _apply_ca_review(
        ca_draft,
        {
            "status": "accepted_auto",
            "auto_values_mA": {"As1": 20.0},
            "manual_values_mA": {"As1": 21.0},
            "final_values_mA": {"As1": 21.0},
        },
    )
    assert ca_draft["targets"][0]["status"] == "manual_adjusted"
    assert ca_draft["targets"][0]["included"] is True
    assert ca_draft["targets"][0]["analysis_included"] is True
    assert ca_draft["targets"][0]["final_values"] == {"As1": 21.0}

    tma_target = make_target(
        family="tma",
        measurement_fingerprint=fingerprint,
        target_key="stress_mpa:30",
        auto_values={"As": 20.0},
    )
    tma_target["target"] = {"stress_mpa": 30.0, "load_g": 1.0}
    tma_draft = make_review(
        family="tma",
        measurement_fingerprint=fingerprint,
        targets=[tma_target],
    )
    _apply_tma_reviews(
        tma_draft,
        [
            {
                "status": "excluded",
                "target_label": "30 MPa",
                "values": {"As": 22.0},
            }
        ],
    )
    assert tma_draft["targets"][0]["included"] is False
    assert tma_draft["targets"][0]["analysis_included"] is False
    assert tma_draft["targets"][0]["final_values"] == {"As": 22.0}

    _apply_tma_reviews(
        tma_draft,
        [
            {
                "status": "no_transition",
                "target_label": "30 MPa",
                "values": {"As": 99.0},
            }
        ],
    )
    assert tma_draft["targets"][0]["included"] is False
    assert tma_draft["targets"][0]["analysis_included"] is True
    assert tma_draft["targets"][0]["final_values"] == {}


def test_review_queue_shows_samples_runs_and_cycles_lazily(tmp_path, qtbot) -> None:
    from PyQt6 import QtCore, QtWidgets
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        PortableTransitionReviewQueueDialog,
        ReviewPlot,
        ReviewQueueEntry,
    )

    built: list[str] = []

    def builder(sample: str, sidecar_name: str):
        def build(parent):
            built.append(sample)
            frame = pd.DataFrame(
                {"I_mA": [10.0, 20.0, 10.0], "R_Ohm": [180.0, 160.0, 181.0]}
            )
            fingerprint = dataframe_fingerprint(
                frame,
                namespace=f"queue-{sample}",
                columns=("I_mA", "R_Ohm"),
            )
            target = make_target(
                family="current_annealing",
                measurement_fingerprint=fingerprint,
                target_key="graph",
                auto_values={
                    "As1": 12.0,
                    "Af1": 18.0,
                    "Ms1": 16.0,
                    "Mf1": 11.0,
                    "As2": 13.0,
                    "Af2": 19.0,
                    "Ms2": 17.0,
                    "Mf2": 12.0,
                },
            )
            payload = make_review(
                family="current_annealing",
                measurement_fingerprint=fingerprint,
                targets=[target],
            )
            plot = ReviewPlot(
                frame["I_mA"], frame["R_Ohm"], sample, "Resistance (ohm)"
            )
            return PortableTransitionReviewDialog(
                payload, {"graph": plot}, tmp_path / sidecar_name, parent
            )

        return build

    dialog = PortableTransitionReviewQueueDialog(
        [
            ReviewQueueEntry("Sample A", "100 mA", builder("Sample A", "a.json")),
            ReviewQueueEntry("Sample B", "60 mA", builder("Sample B", "b.json")),
            ReviewQueueEntry("Sample A", "70 mA", builder("Sample A 70", "a70.json")),
        ]
    )
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(30)

    header = dialog.tree.header()
    assert header.stretchLastSection() is False
    assert (
        header.sectionResizeMode(1)
        == QtWidgets.QHeaderView.ResizeMode.Interactive
    )
    assert header.sectionSize(1) == 112
    header.resizeSection(1, 96)
    assert header.sectionSize(1) == 96

    assert dialog.tree.topLevelItemCount() == 2
    sample_a = dialog.tree.topLevelItem(0)
    sample_b = dialog.tree.topLevelItem(1)
    assert sample_a.childCount() == 2
    assert sample_a.text(0) == "Sample A"
    assert sample_b.text(0) == "Sample B"
    assert built == ["Sample A"]
    run_a = sample_a.child(0)
    assert run_a.text(0) == "100 mA"
    assert [run_a.child(index).text(0) for index in range(run_a.childCount())] == [
        "Cycle 1",
        "Cycle 2",
    ]

    run_b = sample_b.child(0)
    dialog.tree.setCurrentItem(sample_b)
    qtbot.wait(20)
    assert built == ["Sample A", "Sample B"]
    assert [run_b.child(index).text(0) for index in range(run_b.childCount())] == [
        "Cycle 1",
        "Cycle 2",
    ]
    assert dialog.tree.currentItem() is run_b.child(0)

    dialog.tree.setCurrentItem(run_b)
    qtbot.wait(20)
    assert dialog.tree.currentItem() is run_b.child(0)
    dialog.tree.setCurrentItem(run_b.child(1))
    qtbot.wait(10)
    assert dialog._editors[1].target_list.currentRow() == 1  # noqa: SLF001

    dialog.tree.setCurrentItem(run_b.child(0))
    dialog.tree.setFocus()
    qtbot.keyClick(dialog.tree, QtCore.Qt.Key.Key_Up)
    qtbot.wait(10)
    run_a_70 = sample_a.child(1)
    assert dialog.tree.currentItem() is run_a_70.child(1)
    assert built == ["Sample A", "Sample B", "Sample A 70"]
    assert dialog._editors[2].target_list.currentRow() == 1  # noqa: SLF001

    qtbot.keyClick(dialog.tree, QtCore.Qt.Key.Key_Down)
    qtbot.wait(10)
    assert dialog.tree.currentItem() is run_b.child(0)
    assert dialog._editors[1].target_list.currentRow() == 0  # noqa: SLF001


def test_queue_keeps_measured_cycles_after_partial_review(tmp_path, qtbot) -> None:
    from PyQt6 import QtCore
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        PortableTransitionReviewQueueDialog,
        ReviewPlot,
        ReviewQueueEntry,
    )

    frame = pd.DataFrame(
        {'I_mA': [10.0, 20.0, 10.0], 'R_Ohm': [100.0, 90.0, 101.0]}
    )
    fingerprint = dataframe_fingerprint(frame, namespace='stable-cycle-review')
    target = make_target(
        family='current_annealing',
        measurement_fingerprint=fingerprint,
        target_key='graph',
        status='manual_adjusted',
        manual_values={'As1': 12.0, 'Af1': 18.0},
        final_values={'As1': 12.0, 'Af1': 18.0},
        cleared_labels=('Ms1', 'Mf1'),
    )
    payload = make_review(
        family='current_annealing',
        measurement_fingerprint=fingerprint,
        targets=[target],
    )
    plot = ReviewPlot(
        frame['I_mA'], frame['R_Ohm'], 'Two cycles', 'Resistance (ohm)',
        unit_series={
            'Cycle 1': (frame['I_mA'], frame['R_Ohm']),
            'Cycle 2': (
                pd.Series([11.0, 21.0, 11.0]),
                pd.Series([200.0, 180.0, 201.0]),
            ),
        },
    )

    def build(parent):
        return PortableTransitionReviewDialog(
            payload, {'graph': plot}, tmp_path / 'review.json', parent
        )

    queue = PortableTransitionReviewQueueDialog(
        [ReviewQueueEntry('Sample', 'Run', build)]
    )
    qtbot.addWidget(queue)
    queue.show()
    qtbot.wait(20)
    editor = queue._editors[0]  # noqa: SLF001
    run_item = queue._run_items[0]  # noqa: SLF001
    assert run_item.childCount() == 2
    assert queue.review_filter.currentText() == 'Unreviewed'
    queue.tree.setCurrentItem(run_item.child(0))
    qtbot.wait(20)
    assert editor.save_button.text() == 'Save and next cycle'
    assert editor.save_button.isEnabled()
    qtbot.mouseClick(editor.save_button, QtCore.Qt.MouseButton.LeftButton)
    qtbot.wait(20)
    assert queue.completed_count == 0
    assert not run_item.isHidden()
    assert not run_item.child(0).isHidden()
    assert not run_item.child(1).isHidden()
    assert queue._is_reviewed_state(  # noqa: SLF001
        run_item.child(0).data(1, QtCore.Qt.ItemDataRole.UserRole)
    )
    assert queue.tree.currentItem() is run_item.child(1)
    assert editor._active_unit_labels == ['As2', 'Af2', 'Ms2', 'Mf2']  # noqa: SLF001
    assert editor.heating_curve_item.getData()[1][0] == 200.0


def test_queue_shows_scientific_review_states_and_parent_progress(qtbot) -> None:
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewQueueDialog,
        ReviewQueueEntry,
        ReviewUnitSummary,
    )

    def should_not_load(_parent):
        raise AssertionError('reviewed entries must stay lazy')

    queue = PortableTransitionReviewQueueDialog(
        [
            ReviewQueueEntry(
                'Sample',
                'Run',
                should_not_load,
                saved=True,
                review_units=(
                    ReviewUnitSummary('Cycle 1', 'manual', 'As1=12 (manual)'),
                    ReviewUnitSummary('Cycle 2', 'excluded', 'excluded from analysis'),
                ),
            )
        ]
    )
    qtbot.addWidget(queue)
    run_item = queue._run_items[0]  # noqa: SLF001
    sample_item = queue.tree.topLevelItem(0)

    assert queue.review_filter.currentText() == 'Unreviewed'
    assert sample_item.text(1) == '2/2 reviewed'
    assert run_item.text(1) == '2/2 reviewed'
    assert 'Manual' in run_item.child(0).text(1)
    assert 'Excluded' in run_item.child(1).text(1)
    assert run_item.isHidden()


def test_review_state_classification_counts_no_transition_and_excluded_as_done() -> None:
    from plotting.shared.transition_review_dialog import _review_unit_state

    labels = ['As', 'Af', 'Ms', 'Mf']
    no_transition, _ = _review_unit_state(
        {'status': 'no_transition', 'cleared_labels': labels}, labels
    )
    excluded, _ = _review_unit_state(
        {
            'status': 'excluded',
            'manual_values': {'As': 10.0},
            'final_values': {'As': 10.0},
        },
        labels,
    )
    partial, _ = _review_unit_state(
        {
            'status': 'manual_adjusted',
            'manual_values': {'As': 10.0},
            'final_values': {'As': 10.0},
        },
        labels,
    )

    assert no_transition == 'no_transition'
    assert excluded == 'excluded'
    assert partial == 'partial'


def test_save_completed_run_keeps_only_next_editor_visible(tmp_path, qtbot) -> None:
    from PyQt6 import QtCore
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        PortableTransitionReviewQueueDialog,
        ReviewPlot,
        ReviewQueueEntry,
    )

    frame = pd.DataFrame(
        {'I_mA': [10.0, 20.0, 10.0], 'R_Ohm': [100.0, 90.0, 101.0]}
    )

    def builder(run_number: int):
        fingerprint = dataframe_fingerprint(
            frame, namespace=f'queue-run-{run_number}'
        )
        payload = make_review(
            family='current_annealing',
            measurement_fingerprint=fingerprint,
            targets=[
                make_target(
                    family='current_annealing',
                    measurement_fingerprint=fingerprint,
                    target_key='graph',
                    status='unreviewed',
                )
            ],
        )
        plot = ReviewPlot(
            frame['I_mA'],
            frame['R_Ohm'],
            f'Run {run_number}',
            'Resistance (ohm)',
            unit_series={'Cycle 1': (frame['I_mA'], frame['R_Ohm'])},
        )
        return lambda parent: PortableTransitionReviewDialog(
            payload,
            {'graph': plot},
            tmp_path / f'run-{run_number}.review.json',
            parent,
        )

    queue = PortableTransitionReviewQueueDialog(
        [
            ReviewQueueEntry('Sample', 'Run 1', builder(1)),
            ReviewQueueEntry('Sample', 'Run 2', builder(2)),
        ]
    )
    qtbot.addWidget(queue)
    queue.show()
    qtbot.wait(20)
    first = queue._editors[0]  # noqa: SLF001
    for label in first._active_unit_labels:  # noqa: SLF001
        qtbot.mouseClick(
            first.choice_buttons[label]['not_observed'],
            QtCore.Qt.MouseButton.LeftButton,
        )
    assert first.save_button.isEnabled()

    qtbot.mouseClick(first.save_button, QtCore.Qt.MouseButton.LeftButton)
    qtbot.wait(20)

    second = queue._editors[1]  # noqa: SLF001
    assert not first.isVisible()
    assert second.isVisible()
    assert sum(editor.isVisible() for editor in queue._editors.values()) == 1  # noqa: SLF001
    assert queue._current_index == 1  # noqa: SLF001


def test_mark_for_archive_saves_whole_run_and_advances_queue(tmp_path, qtbot) -> None:
    from PyQt6 import QtCore
    from plotting.shared.transition_review_dialog import (
        PortableTransitionReviewDialog,
        PortableTransitionReviewQueueDialog,
        ReviewPlot,
        ReviewQueueEntry,
    )

    frame = pd.DataFrame(
        {'I_mA': [10.0, 20.0, 10.0], 'R_Ohm': [100.0, 90.0, 101.0]}
    )

    def builder(run_number: int):
        fingerprint = dataframe_fingerprint(
            frame, namespace=f'archive-queue-{run_number}'
        )
        payload = make_review(
            family='current_annealing',
            measurement_fingerprint=fingerprint,
            targets=[
                make_target(
                    family='current_annealing',
                    measurement_fingerprint=fingerprint,
                    target_key='graph',
                )
            ],
        )
        plot = ReviewPlot(
            frame['I_mA'],
            frame['R_Ohm'],
            f'Run {run_number}',
            'Resistance (ohm)',
            unit_series={
                'Cycle 1': (frame['I_mA'], frame['R_Ohm']),
                'Cycle 2': (frame['I_mA'], frame['R_Ohm']),
            },
        )
        return lambda parent: PortableTransitionReviewDialog(
            payload,
            {'graph': plot},
            tmp_path / f'archive-run-{run_number}.json',
            parent,
        )

    queue = PortableTransitionReviewQueueDialog(
        [
            ReviewQueueEntry('Sample', 'Run 1', builder(1)),
            ReviewQueueEntry('Sample', 'Run 2', builder(2)),
        ]
    )
    qtbot.addWidget(queue)
    queue.show()
    qtbot.wait(20)
    first = queue._editors[0]  # noqa: SLF001

    qtbot.mouseClick(first.archive_button, QtCore.Qt.MouseButton.LeftButton)
    assert first.save_button.text() == 'Save marked run and next'
    qtbot.mouseClick(first.save_button, QtCore.Qt.MouseButton.LeftButton)
    qtbot.wait(20)

    assert load_review(tmp_path / 'archive-run-1.json')['archive_requested'] is True
    assert not first.isVisible()
    assert queue._editors[1].isVisible()  # noqa: SLF001
    assert queue._current_index == 1  # noqa: SLF001


def test_review_queue_wrappers_construct_lazy_entries(tmp_path, monkeypatch) -> None:
    from plotting.shared import transition_review_dialog as review_dialog
    from plotting.shared.transition_review_dialog import ReviewUnitSummary

    captured = []

    class FakeQueue:
        def __init__(self, entries, parent):
            captured.append((list(entries), parent))
            self.completed_count = len(entries)

        def exec(self):
            return 0

    monkeypatch.setattr(review_dialog, "PortableTransitionReviewQueueDialog", FakeQueue)
    ca_paths = [tmp_path / "Sample A" / "100mA.txt", tmp_path / "Sample B" / "60mA.txt"]
    completed = review_dialog.review_current_annealing_files(
        None,
        ca_paths,
        sample_for_path=lambda path: {"sample": path.parent.name},
        review_units_for_path=lambda path: (
            ReviewUnitSummary("Cycle 1", "manual", f"reviewed {path.name}"),
        ),
    )
    assert completed == 2
    assert [entry.sample_label for entry in captured[0][0]] == ["Sample A", "Sample B"]
    assert all(entry.saved for entry in captured[0][0])
    assert [entry.review_units[0].state for entry in captured[0][0]] == [
        "manual",
        "manual",
    ]

    tma_paths = [tmp_path / "Sample C", tmp_path / "Sample D"]
    completed = review_dialog.review_tma_runs(
        None,
        tma_paths,
        review_units_for_path=lambda _path: (
            ReviewUnitSummary("50 MPa", "no_transition"),
        ),
    )
    assert completed == 2
    assert [entry.sample_label for entry in captured[1][0]] == ["Sample C", "Sample D"]
    assert all(entry.review_units[0].state == "no_transition" for entry in captured[1][0])

def test_builder_imports_repeated_tma_sweeps_by_sweep_index(tmp_path, monkeypatch) -> None:
    import logging
    from types import SimpleNamespace

    from microwire_data_builder import ui as builder_ui
    from plotting.shared import transition_review as review_module
    from plotting.shared import transition_review_adapters as adapter_module

    run_dir = tmp_path / "tma-run"
    run_dir.mkdir()
    (run_dir / "transition_review.json").write_text("{}", encoding="utf-8")
    fingerprint = "sha256:" + "c" * 64
    payload = {
        "experiment_family": "tma",
        "measurement_fingerprint": fingerprint,
        "targets": [
            {
                "status": "accepted_auto",
                "target": {"stress_mpa": 50.0, "sweep_index": 1},
                "auto_values": {"As": 20.0},
                "final_values": {"As": 20.0},
            },
            {
                "status": "manual_adjusted",
                "target": {"stress_mpa": 50.0, "sweep_index": 2},
                "auto_values": {"As": 22.0},
                "manual_values": {"As": 23.0},
                "final_values": {"As": 23.0},
            },
        ],
    }
    entries = [
        SimpleNamespace(
            sample="sample",
            run_label="run",
            target_label=f"50 MPa - sweep {index}/2",
            status="accepted",
            sweep_index=index,
            target_summary=SimpleNamespace(stress_mpa=50.0),
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(review_module, "load_review", lambda _path: payload)
    monkeypatch.setattr(
        adapter_module,
        "tma_review_draft",
        lambda _path: {"measurement_fingerprint": fingerprint},
    )
    monkeypatch.setattr(
        builder_ui,
        "_mini_dma_transition_review_entries",
        lambda _records, _logger: entries,
    )
    reviews = {}

    assert builder_ui._import_portable_tma_reviews(
        [SimpleNamespace(path=run_dir)], reviews, logging.getLogger(__name__)
    ) is True
    assert {review["target_label"] for review in reviews.values()} == {
        "50 MPa - sweep 1/2",
        "50 MPa - sweep 2/2",
    }
    assert {review["values"]["As"] for review in reviews.values()} == {20.0, 23.0}
