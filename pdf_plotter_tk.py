#!/usr/bin/env python3
"""
Quick PDF data plotter (Tkinter)
--------------------------------
Select one or more PDFs that contain lines with 4 semicolon-separated columns:
  Col1 = T1
  Col2 = T2
  Col3 = Force (N)
  Col4 = Strain (mm)

You can plot Y (T1 / T2 / T2-T1 / T1+T2) versus X (Force or Strain).

Requires: pip install PyPDF2 matplotlib
"""

import re
import sys
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from typing import List, Tuple

# Use matplotlib for plotting
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    from PyPDF2 import PdfReader
except ImportError:
    raise SystemExit("Missing dependency: install with 'pip install PyPDF2 matplotlib'")

NumberRow = Tuple[float, float, float, float]  # T1, T2, Force, Strain

def parse_pdf_to_rows(path: str) -> List[NumberRow]:
    """Extract lines with 4 semicolon-separated values from a PDF and return numeric rows.

    - Accepts comma decimal separator and optional spaces.
    - Ignores lines that don't parse cleanly.
    """
    rows: List[NumberRow] = []
    reader = PdfReader(path)
    # Regex to capture 4 fields separated by semicolons; tolerate spaces
    # Group pattern for a number with optional minus and decimal comma or dot.
    num = r"-?\d+(?:[.,]\d+)?"
    line_pattern = re.compile(rf"\s*({num})\s*;\s*({num})\s*;\s*({num})\s*;\s*({num})\s*")

    for page in reader.pages:
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            m = line_pattern.fullmatch(raw_line.strip())
            if not m:
                # Also try to normalize multiple spaces, stray characters
                candidate = re.sub(r"[^\d;,\.\-\s]", "", raw_line).strip()
                m = line_pattern.fullmatch(candidate)
            if m:
                try:
                    # Replace comma with dot for decimals
                    t1 = float(m.group(1).replace(",", "."))
                    t2 = float(m.group(2).replace(",", "."))
                    force = float(m.group(3).replace(",", "."))
                    strain = float(m.group(4).replace(",", "."))
                    rows.append((t1, t2, force, strain))
                except ValueError:
                    continue
    return rows

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF T1/T2 Plotter")
        self.geometry("900x600")

        self.files: List[str] = []
        self.rows: List[NumberRow] = []

        # Controls frame
        ctrl = ttk.Frame(self, padding=10)
        ctrl.pack(side=tk.TOP, fill=tk.X)

        self.open_btn = ttk.Button(ctrl, text="Open PDF(s)", command=self.open_files)
        self.open_btn.pack(side=tk.LEFT)

        ttk.Label(ctrl, text="Y:").pack(side=tk.LEFT, padx=(12, 4))
        self.y_var = tk.StringVar(value="T1")
        self.y_menu = ttk.Combobox(ctrl, textvariable=self.y_var, state="readonly",
                                   values=["T1", "T2", "T2-T1", "T1+T2"])
        self.y_menu.pack(side=tk.LEFT)

        ttk.Label(ctrl, text="X:").pack(side=tk.LEFT, padx=(12, 4))
        self.x_var = tk.StringVar(value="Force (N)")
        self.x_menu = ttk.Combobox(ctrl, textvariable=self.x_var, state="readonly",
                                   values=["Force (N)", "Strain (mm)"])
        self.x_menu.pack(side=tk.LEFT)

        self.plot_btn = ttk.Button(ctrl, text="Plot", command=self.plot_data, state=tk.DISABLED)
        self.plot_btn.pack(side=tk.LEFT, padx=12)

        self.status = tk.StringVar(value="No files loaded.")
        self.status_lbl = ttk.Label(ctrl, textvariable=self.status)
        self.status_lbl.pack(side=tk.LEFT, padx=12)

        # Matplotlib figure area
        self.fig, self.ax = plt.subplots()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def open_files(self):
        paths = filedialog.askopenfilenames(title="Select PDF files", filetypes=[("PDF files", "*.pdf")])
        if not paths:
            return
        self.files = list(paths)
        self.rows.clear()
        total = 0
        for p in self.files:
            try:
                r = parse_pdf_to_rows(p)
                self.rows.extend(r)
                total += len(r)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to parse {p}:\n{e}")
        if total == 0:
            self.status.set("No numeric rows found. Check the PDF contents.")
            self.plot_btn.config(state=tk.DISABLED)
        else:
            self.status.set(f"Loaded {len(self.files)} file(s), {total} rows.")
            self.plot_btn.config(state=tk.NORMAL)

    def compute_xy(self) -> Tuple[List[float], List[float]]:
        y_choice = self.y_var.get()
        x_choice = self.x_var.get()
        x_vals: List[float] = []
        y_vals: List[float] = []
        for t1, t2, force, strain in self.rows:
            if y_choice == "T1":
                y = t1
            elif y_choice == "T2":
                y = t2
            elif y_choice == "T2-T1":
                y = t2 - t1
            elif y_choice == "T1+T2":
                y = t1 + t2
            else:
                continue

            if x_choice.startswith("Force"):
                x = force
            else:
                x = strain

            x_vals.append(x)
            y_vals.append(y)
        return x_vals, y_vals

    def plot_data(self):
        if not self.rows:
            messagebox.showinfo("No data", "Load a PDF first.")
            return
        x, y = self.compute_xy()
        if not x:
            messagebox.showinfo("No data", "No rows after selection.")
            return

        self.ax.clear()
        self.ax.plot(x, y, marker='o', linestyle='-')
        self.ax.set_xlabel(self.x_var.get())
        self.ax.set_ylabel(self.y_var.get())
        title = f"{self.y_var.get()} vs {self.x_var.get()}"
        if self.files:
            # Show just the last filename if many
            import os
            title += "  —  " + (os.path.basename(self.files[-1]) if len(self.files) == 1 else f"{len(self.files)} files")
        self.ax.set_title(title)
        self.ax.grid(True, which="both", linestyle="--", alpha=0.4)
        self.fig.tight_layout()
        self.canvas.draw()

if __name__ == "__main__":
    app = App()
    app.mainloop()
