import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from plotting.stress_dependence.core import (
    parse_metadata as sd_parse,
    explain_metadata_failure,
)
from plotting.stress_sensitivity.core import parse_metadata as ss_parse
from plotting.plugins.hsw_load_compare.core import parse_metadata as hl_parse
from plotting.temperature_sensitivity.core import parse_metadata as ts_parse
from plotting.temperature_dependence.core import parse_metadata as td_parse


def test_parse_metadata_accepts_arbitrary_sample_number():
    fname = "FeSiB 85_10 foo-3a 47mA 2,5a"
    md_sd = sd_parse(fname)
    assert md_sd and md_sd["sample_end"] == "foo-3a"

    md_ss = ss_parse(fname)
    assert md_ss and md_ss["sample_end"] == "foo-3a"
    assert md_ss.get("sample") == "foo-3"

    md_hl = hl_parse(fname)
    assert md_hl and md_hl["sample_end"] == "foo-3a"


def test_temperature_filename_parsing():
    md = ts_parse("FeSiB 85_10 74mA 25C")
    assert md and not md["continuous"] and md["temp_val"] == 25

    md_cont = ts_parse("FeSiB 85_10 74mA 25-100C")
    assert md_cont and md_cont["continuous"] and md_cont["temp_val"] is None

    md_td = td_parse("FeSiB 85_10 74mA 100C")
    assert md_td and md_td["temp_val"] == 100


def test_stress_dependence_reports_missing_anneal_token():
    reason = explain_metadata_failure("FeSiB 85_10 sampleA 150a")
    assert "annealing" in reason.lower()
