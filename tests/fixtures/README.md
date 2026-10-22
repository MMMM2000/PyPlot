# Test Fixtures

This directory stores stable, minimal input fixtures and expected outputs used by
parser and smoke tests.

Layout:
- `dma_iso_stress/`: TA DMA IsoStress text fixture + expected parsed output.
- `vsm_temperature_scan/`: VSM scan text fixture + expected parsed output.

Keep fixtures deterministic and intentionally small so tests stay fast. Do not
add real research data, instrument recordings, or copies of shared projects.

`tests/synthetic_data.py` writes invented examples to pytest's disposable roots:

- TMA: nine stress targets, five points each, known per-curve/global lengths,
  current/resistance power pairs, and peak strain after peak current.
- Builder TMA review: one analytic paired transition and eight flat traces keep
  automatic accepted/rejected cases and empty marker state independently covered.
- VSM: header and filename metadata, mixed extension case, duplicate temperatures
  with different curve lengths, and M(H,T)=T*H/1e6 for an analytic entropy oracle.
- Legacy signals: semicolon columns with temperature/load/direction filename tokens.
- PDF: deterministic four/six-column documents with decimal commas. Expected rows
  keep force distinct from the derived T2-T1 column.
- Builder/video discovery: tiny fabrication workbooks with two pieces per draw;
  an explicitly non-playable video placeholder exercises path linking only.

These examples establish format and algorithm contracts. They do not establish
scientific validity for any physical specimen. Selected Drive-copy validation is
separate; see [the data policy](../../docs/sample-data-policy.md).
