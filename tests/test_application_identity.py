from __future__ import annotations

import pytest

from plotting.shared import application_identity


def test_experiment_application_icons_are_distinct(qapp) -> None:
    tma = application_identity.experiment_application_icon("tma")
    annealing = application_identity.experiment_application_icon("current_annealing")

    assert not tma.isNull()
    assert not annealing.isNull()
    assert tma.cacheKey() != annealing.cacheKey()


def test_experiment_application_icon_rejects_unknown_kind(qapp) -> None:
    with pytest.raises(ValueError, match="Unsupported experiment application icon"):
        application_identity.experiment_application_icon("unknown")


def test_windows_app_identity_is_skipped_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(application_identity.sys, "platform", "linux")

    assert not application_identity.set_windows_app_user_model_id("PyPlot.Test")
