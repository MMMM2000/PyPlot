import pytest

from plotting.stress_dependence.core import parse_metadata as sd_parse
from plotting.stress_sensitivity.core import parse_metadata as ss_parse
from plotting.hsw_load_compare.core import parse_metadata as hl_parse


def test_parse_metadata_accepts_arbitrary_sample_number():
    fname = "FeSiB 85_10 foo-3a 47mA 2,5a"
    md_sd = sd_parse(fname)
    assert md_sd and md_sd["sample_end"] == "foo-3a"

    md_ss = ss_parse(fname)
    assert md_ss and md_ss["sample_end"] == "foo-3a"
    assert md_ss.get("sample") == "foo-3"

    md_hl = hl_parse(fname)
    assert md_hl and md_hl["sample_end"] == "foo-3a"
