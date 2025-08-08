from __future__ import annotations

import os
import re
import sys
from typing import List, Tuple

from PyQt6 import QtWidgets
import matplotlib.pyplot as plt

try:
    from PyPDF2 import PdfReader
except Exception as exc:  # pragma: no cover - optional dep at runtime
    PdfReader = None  # type: ignore

from ..utils import apply_system_theme

NumberRow = Tuple[float, float, float, float]  # T1, T2, Force, Strain


def parse_pdf_to_rows(path: str) -> List[NumberRow]:
    """Extract numeric rows from a PDF.

    Each valid line contains 4 semicolon-separated values:
    T1; T2; Force; Strain. Comma decimal separators are accepted.
    """
    if PdfReader is None:
        raise SystemExit("Missing dependency PyPDF2. Install it and retry.")

    rows: List[NumberRow] = []
    reader = PdfReader(path)
    num = r"-?\d+(?:[.,]\d+)?"
    line_pattern = re.compile(rf"\s*({num})\s*;\s*({num})\s*;\s*({num})\s*;\s*({num})\s*")

    for page in reader.pages:
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            m = line_pattern.fullmatch(raw_line.strip())
            if not m:
                # Try again after stripping stray characters
                candidate = re.sub(r"[^\d;,\.\-\s]", "", raw_line).strip()
                m = line_pattern.fullmatch(candidate)
            if m:
                try:
                    t1 = float(m.group(1).replace(",", "."))
                    t2 = float(m.group(2).replace(",", "."))
                    force = float(m.group(3).replace(",", "."))
                    strain = float(m.group(4).replace(",", "."))
                    rows.append((t1, t2, force, strain))
                except ValueError:
                    continue
    return rows


def ask_files(parent: QtWidgets.QWidget | None = None) -> List[str]:
    paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
        parent,
        "Select PDF files",
        "",
        "PDF files (*.pdf);;All files (*)",
    )
    if not paths:
        sys.exit("No files selected.")
    return list(paths)


def ask_options(parent: QtWidgets.QWidget | None = None) -> tuple[str, str] | None:
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("PDF Plot Settings")
    layout = QtWidgets.QGridLayout(dialog)

    y_label = QtWidgets.QLabel("Y variable:")
    y_combo = QtWidgets.QComboBox()
    y_combo.addItems(["T1+T2", "T1", "T2", "T2–T1"])  # Default: T1+T2
    y_combo.setCurrentIndex(0)

    x_label = QtWidgets.QLabel("X variable:")
    x_combo = QtWidgets.QComboBox()
    x_combo.addItems(["Force (N)", "Strain (mm)"])
    x_combo.setCurrentIndex(0)

    run_btn = QtWidgets.QPushButton("Run")
    run_btn.clicked.connect(dialog.accept)

    layout.addWidget(y_label, 0, 0)
    layout.addWidget(y_combo, 0, 1)
    layout.addWidget(x_label, 1, 0)
    layout.addWidget(x_combo, 1, 1)
    layout.addWidget(run_btn, 2, 0, 1, 2)
    dialog.setLayout(layout)

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None
    return y_combo.currentText(), x_combo.currentText()


def compute_xy(rows: List[NumberRow], y_name: str, x_name: str) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for t1, t2, force, strain in rows:
        if y_name == "T1":
            y = t1
        elif y_name == "T2":
            y = t2
        elif y_name in ("T2–T1", "T2-T1"):
            y = t2 - t1
        elif y_name == "T1+T2":
            y = t1 + t2
        else:
            continue
        x = force if x_name.startswith("Force") else strain
        xs.append(x)
        ys.append(y)
    return xs, ys


def main() -> None:
    paths = ask_files()
    opt = ask_options()
    if opt is None:
        return
    y_name, x_name = opt

    all_rows: list[NumberRow] = []
    total = 0
    for p in paths:
        try:
            rows = parse_pdf_to_rows(p)
            all_rows.extend(rows)
            total += len(rows)
        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "Error", f"Failed to parse {os.path.basename(p)}:\n{e}")
    if total == 0:
        QtWidgets.QMessageBox.information(None, "No data", "No numeric rows found. Check the PDFs.")
        return

    x, y = compute_xy(all_rows, y_name, x_name)
    if not x:
        QtWidgets.QMessageBox.information(None, "No data", "No data after selection.")
        return

    plt.figure()
    plt.plot(x, y, marker="o", linestyle="-")
    plt.xlabel(x_name)
    plt.ylabel(y_name)
    title = f"{y_name} vs {x_name} — {os.path.basename(paths[-1]) if len(paths)==1 else f'{len(paths)} files'}"
    plt.title(title)
    plt.grid(True, which="both", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    main()

