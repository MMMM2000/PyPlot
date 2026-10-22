"""Small invented format examples. No values are copied from measurements.

Writers take an explicit disposable directory; importing this module does no IO.
Composition-shaped names exercise filename parsers, not specimen provenance.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path


def _text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_mini_dma_run(path: Path) -> Path:
    """Nine targets, five points each; peak strain occurs after peak current.

At 50 MPa: l0=50 mm, length=[50,51,52,53,50.5] mm,
current=[10,40,80,20,5] mA; peak strain=6% at 20 mA.
Subsequent targets shift l0 by 0.25 mm to distinguish global/per-curve l0.
"""
    path.mkdir(parents=True, exist_ok=True)
    _text(path / "metadata.json", json.dumps({
        "synthetic": True, "sample_name": "Ni50Fe27Ga23 12_2 synthetic",
        "initial_length_mm": 60.0, "wire_diameter_mm": 0.02,
        "created_utc": "2000-01-01 00:00:00", "session_state": "finished",
        "finished_utc": "2000-01-01 00:01:00",
        "recipe": {"recipe_mode": "current_sweep_stress"},
    }, indent=2))
    rows = []
    for index, target in enumerate(range(50, 451, 50)):
        l0 = 50.0 + index * 0.25
        for current, extension in zip((10, 40, 80, 20, 5), (0, 1, 2, 3, 0.5), strict=True):
            length = l0 + extension
            rows.append({
                "elapsed_s": len(rows), "recipe_mode": "current_sweep_stress",
                "automation_phase": "current", "automation_target_value": target,
                "plateau_index": index + 1, "strain_pct": (length / 60 - 1) * 100,
                "position_mm": length - 60, "stress_mpa": target,
                "load_g": target * 0.032, "resistance_ohm": 100 + current * 0.1,
                "current_measured_mA": current, "current_set_mA": current,
            })
    with (path / "measurement.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_fabrication_root(root: Path, *, integration: bool = False) -> Path:
    """Invented draw/piece tables; video is a discovery placeholder, never decoded."""
    from openpyxl import Workbook

    groups = {"Ni55Fe18Ga27": [4]} if integration else {
        "Ni50Fe27Ga23": [2, 4], "Ni54Fe23Ga15": [1],
    }
    for composition, draws in groups.items():
        folder = root / composition
        folder.mkdir(parents=True, exist_ok=True)
        book = Workbook()
        sheet = book.active
        sheet.append(["Draw", "Composition", "datum a cas vyroby", "notes"])
        for draw in draws:
            sheet.append([draw, composition, datetime(2000, 1, 2, 3, 4), "SYNTHETIC"])
        book.save(folder / f"{composition}.xlsx")
        for draw in draws:
            draw_dir = folder / f"{draw}.{composition} 02012000 0304"
            draw_dir.mkdir()
            book = Workbook()
            sheet = book.active
            sheet.append(["SYNTHETIC fabrication; no physical inventory"])
            sheet.append(["P.", "Datum", "dlzka (m)", "d (um)", "D (um)", "d/D"])
            for piece in (1, 2):
                sheet.append([piece, datetime(2000, 1, 2), 10 * draw + piece, 20, 30, 2 / 3])
            book.save(draw_dir / f"{draw}.{composition}.xlsx")
            if composition == "Ni54Fe23Ga15":
                _text(draw_dir / "2000-01-02 03-04-00.mkv", "SYNTHETIC path placeholder; not playable\n")
    return root


def write_review_run(path: Path) -> Path:
    """One paired transition plus eight flat traces for mixed review statuses.

Heating falls from 3% to 1% at 35-55 mA; cooling recovers at 15-35 mA.
The second target deliberately has no transition, exercising empty marker state.
"""
    import pandas as pd

    write_mini_dma_run(path)
    rows = []
    for index, target in enumerate(range(50, 451, 50)):
        for heating, currents in ((True, range(1, 91)), (False, range(89, 0, -1))):
            for current in currents:
                if index == 0:
                    start = 35 if heating else 15
                    strain = 3 - 2 * min(1, max(0, (current - start) / 20))
                else:
                    strain = 2 + current / 1000
                rows.append({
                    "elapsed_s": len(rows), "recipe_mode": "current_sweep_stress",
                    "automation_phase": "current", "automation_target_value": target,
                    "plateau_index": index + 1, "strain_pct": strain,
                    "position_mm": 60 * strain / 100, "stress_mpa": target,
                    "load_g": target * 0.032, "resistance_ohm": 100 + current * 0.1,
                    "current_measured_mA": current, "current_set_mA": current,
                })
    pd.DataFrame(rows).to_csv(path / "measurement.csv", index=False)
    return path


def write_pdf(path: Path, *, six_columns: bool = False) -> Path:
    """Real PDF container with invented semicolon rows and locale decimals."""
    from reportlab.pdfgen.canvas import Canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(path), invariant=1)
    canvas.setTitle("SYNTHETIC parser fixture")
    text = canvas.beginText(40, 800)
    text.textLine("SYNTHETIC parser fixture")
    for i in range(4):
        t1, t2, force, strain = i + 1, 2 * i + 3, 10 * i + 7, i / 10
        values = [t1, t2, t2 - t1, t1 + t2, force, strain] if six_columns else [t1, t2, force, strain]
        text.textLine(";".join(str(float(value)).replace(".", ",") for value in values))
    canvas.drawText(text)
    canvas.save()
    return path


def write_vsm_isotherms(root: Path) -> Path:
    """M(H,T) = T*H/1e6; analytic entropy is -H^2/2e6.

Duplicate 150 C scans have only 20 points; the full 101-point scan must win.
"""
    for angle in (0, 90):
        for temperature in (100, 125, 140, 150, 175):
            names = [(f"synthetic-a{angle:03}-T{temperature}.VSM-VIR-Data", 101)]
            if temperature == 150:
                names.append((f"short-a{angle:03}-T150.VSM-VIR-DATA", 20))
            if angle == 0 and temperature == 140:
                names = [("legacy-VIR-03.VSM-VIR-DATA", 101)]
            for name, points in names:
                lines = ["@Samplename: Synthetic alloy", f"Set Field Angle to {angle}",
                         f"Set Sample Temperature to {temperature}", "@@End of Header.",
                         "Time_since_start Applied_Field_For_Plot_ Signal_X_direction", "@@Data"]
                for i in range(points):
                    field = 10000 * i / (points - 1)
                    lines.append(f"{i} {field:.8f} {temperature * field / 1e6:.8f}")
                lines.append("@@END Data")
                _text(root / name, "\n".join(lines) + "\n")
    return root


def write_signal_inputs(root: Path) -> Path:
    for temperature in (25, 100):
        rows = [f"{1 + i / 100 + temperature / 1000};{2 + i / 100};{1 - temperature / 1000};{3 + i / 50 + temperature / 1000}" for i in range(30)]
        _text(root / "temperature_dependence" / f"Synthetic 2_1 77mA {temperature}C.txt", "\n".join(rows))
    for load in ("2,5", "17,5"):
        for direction in ("a", "b"):
            value = float(load.replace(",", "."))
            rows = [f"{1 + value / 100 + i / 1000};{2 + i / 1000};{1 - value / 100};{3 + value / 100 + i / 500}" for i in range(30)]
            _text(root / "stress_dependence" / f"Synthetic 85_10 s2-2a 47mA {load}{direction}.txt", "\n".join(rows))
    rows = [";".join(str(1 + channel + i / 100) for channel in range(6)) for i in range(30)]
    _text(root / "Maxion" / "1 synthetic 2 coils.txt", "\n".join(rows))
    return root
