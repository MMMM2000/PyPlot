# AC Susceptibility Analysis And Origin Handoff

This note is for continuing the completed `Ni50Fe27Ga23 12/2` AC susceptibility
analysis on a Windows PC that has Origin installed. The local PC can run the
repeatable Python analysis and produce CSV/PNG/Markdown artifacts; Origin export
and final DOCX assembly can be finished later on the other PC.

## Source Data

Use the post-calibration empty-coil baseline and the two completed microwire
sweep chunks:

```text
C:\Users\Martin Eliáš\Downloads\ac_susceptibility\ac_susc_empty_coil_baseline_20260528_170049.tsv
C:\Users\Martin Eliáš\Downloads\ac_susceptibility\12-2microwire_20260525_173416.tsv
C:\Users\Martin Eliáš\Downloads\ac_susceptibility\12-2microwire_20260527_111344.tsv
```

The current sample metadata used for the automated analysis:

```text
sample: Ni50Fe27Ga23 12/2
metallic core diameter: 17 um
glass outer diameter: 55 um
```

## Coil Metadata

The one-coil LCR measurement uses the excitation coil. The sensing coil is
recorded as metadata because it belongs to the same fixture family, but it is
not used in the current apparent-susceptibility formula.

Excitation coil, "budiaca cievka":

```text
length: 11 mm
inner diameter: 1.3 mm
outer diameter: 1.7 mm
turns: 350 in two layers
wire diameter: 0.05 mm
```

Sensing coil, "snímacia cievka":

```text
length: 1 mm
inner diameter: about 1.7 mm
outer diameter: about 3.6 mm from a measured recent coil
turns: 250
wire diameter: 0.05 mm
```

## Repeatable Command

Run from the PyPlot repo root with the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m data_logging.ac_susceptibility_logger.analysis `
  --sweep "$env:USERPROFILE\Downloads\ac_susceptibility\12-2microwire_20260525_173416.tsv" `
  --sweep "$env:USERPROFILE\Downloads\ac_susceptibility\12-2microwire_20260527_111344.tsv" `
  --baseline "$env:USERPROFILE\Downloads\ac_susceptibility\ac_susc_empty_coil_baseline_20260528_170049.tsv" `
  --out-dir "artifacts\ac_susceptibility_analysis\automation_12-2_20260529" `
  --sample-name "Ni50Fe27Ga23 12/2" `
  --core-diameter-um 17 `
  --glass-diameter-um 55 `
  --preview-dir "C:\tmp\codex-images"
```

`--preview-dir` copies the most important PNGs to an ASCII/simple path so the
Codex UI can preview them reliably when paths with diacritics fail.

## Formula

The current workflow reports apparent complex susceptibility from the
one-coil LCR measurement:

```text
chi_prime_app = (L_wire - L_empty) / (L_empty * filling_factor)
chi_double_prime_app = (R_wire - R_empty) / (2*pi*f*L_empty*filling_factor)
```

`filling_factor` defaults to the metallic core cross-section divided by the
excitation-coil inner cross-section:

```text
filling_factor = (core_diameter / excitation_coil_inner_diameter)^2
```

This is appropriate for comparing conditions within this fixture, but it is
still an apparent value. A publication-quality absolute susceptibility model
should revisit coil calibration, demagnetization, glass/core geometry, and the
empty-coil baseline choice.

## Output Files

The analysis module writes these repeatable artifacts:

```text
empty_coil_baseline_summary.csv
point_medians.csv
apparent_complex_susceptibility_points.csv
apparent_susceptibility_change_by_direction.csv
apparent_susceptibility_condition_ranking.csv
origin_chi_prime_curves.csv
origin_chi_double_prime_curves.csv
recommended_chi_prime_curves.png
recommended_chi_double_prime_curves.png
top_complex_susceptibility_curves.png
SUSCEPTIBILITY_REPORT.md
analysis_metadata.json
```

The Origin-ready CSVs are intentionally long-form. In Origin, import
`origin_chi_prime_curves.csv`, filter by selected frequency/excitation/current
direction, then plot `current_set_mA` against `chi_prime_app`. Use
`origin_chi_double_prime_curves.csv` the same way for the loss component.

## Current Best Conditions

The prior manual analysis ranked these conditions as strongest and cleanest:

```text
20 kHz, 20 mA excitation, H_ac about 8.00 Oe, delta chi' about 38.5, SNR about 52.6
20 kHz, 10 mA excitation, H_ac about 4.00 Oe, delta chi' about 34.8, SNR about 39.3
100 kHz, 20 mA excitation, delta chi' about 23.5, SNR about 36.9
100 kHz, 10 mA excitation, delta chi' about 23.0, SNR about 34.3
20 kHz, 5 mA excitation, delta chi' about 34.4, SNR about 30.6
```

Percent changes can look huge, including over 1000 percent, when the low-current
denominator is near zero or noisy. Prefer the curves, direction consistency,
absolute `delta chi_prime`, and SNR ranking over percent alone.

## Origin PC Follow-Up

On the Origin PC:

1. Pull this branch and rerun the command above, or copy the generated artifact
   directory from this PC.
2. Import `origin_chi_prime_curves.csv` and `origin_chi_double_prime_curves.csv`
   into Origin.
3. Build a graph template with current on the x-axis, up/down directions as
   separate series, and one panel or page per selected frequency/excitation.
4. Export Origin graph objects or high-resolution images.
5. Assemble the supervisor-facing DOCX from `SUSCEPTIBILITY_REPORT.md`, the
   Origin graphs, and a short methods section describing the one-coil apparent
   susceptibility formula.

Once the Origin workflow is stable for two or three datasets, it is worth adding
an automated PyPlot panel or plugin. For now, keeping this as a CLI/module is
better: the formula and preferred plotting style are still changing, and Codex
can operate the CLI end-to-end without Origin installed.
