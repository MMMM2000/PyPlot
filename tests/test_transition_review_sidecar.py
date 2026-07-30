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

    assert dialog.review_unit_row.isVisible() is False
    dialog.show()
    qtbot.wait(20)
    assert dialog.review_unit_row.isVisible() is True
    assert [dialog.review_unit_combo.itemText(index) for index in range(2)] == [
        "Cycle 1",
        "Cycle 2",
    ]
    assert set(dialog.choice_buttons) == {"As1", "Af1", "Ms1", "Mf1"}
    for label in tuple(dialog.choice_buttons):
        dialog.choice_buttons[label]["auto"].click()
    assert not dialog.save_button.isEnabled()

    dialog.review_unit_combo.setCurrentIndex(1)
    assert set(dialog.choice_buttons) == {"As2", "Af2", "Ms2", "Mf2"}
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

    dialog.review_unit_combo.setCurrentIndex(0)
    assert all(
        dialog.choice_buttons[label]["auto"].isChecked()
        for label in ("As1", "Af1", "Ms1", "Mf1")
    )


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


def test_review_queue_is_lazy_and_stops_after_cancel(tmp_path, monkeypatch) -> None:
    from plotting.shared import transition_review_dialog as review_dialog

    first = tmp_path / "first" / "measurement.txt"
    second = tmp_path / "second" / "measurement.txt"
    third = tmp_path / "third" / "measurement.txt"
    calls = []

    def fake_review(parent, path, *, sample=None, queue_position=None):
        calls.append((path, sample, queue_position))
        return path != second

    monkeypatch.setattr(review_dialog, "review_current_annealing_file", fake_review)

    completed = review_dialog.review_current_annealing_files(
        None,
        [first, second, third],
        sample_for_path=lambda path: {"sample": path.parent.name},
    )

    assert completed == 1
    assert calls == [
        (first, {"sample": "first"}, (1, 3)),
        (second, {"sample": "second"}, (2, 3)),
    ]


def test_tma_review_queue_reports_each_run_position(tmp_path, monkeypatch) -> None:
    from plotting.shared import transition_review_dialog as review_dialog

    run_dirs = [tmp_path / "run-a", tmp_path / "run-b"]
    calls = []

    def fake_review(parent, path, *, queue_position=None):
        calls.append((path, queue_position))
        return True

    monkeypatch.setattr(review_dialog, "review_tma_run", fake_review)

    assert review_dialog.review_tma_runs(None, run_dirs) == 2
    assert calls == [(run_dirs[0], (1, 2)), (run_dirs[1], (2, 2))]


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
