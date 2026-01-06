#!/usr/bin/env python3
"""
DMA TXT -> Origin plotter (Temperature vs Strain)

Features
- Simple Tkinter UI to choose one or more TXT files OR a folder (with optional recursion)
- Parses TA Instruments DMA export (.txt) like the provided samples
- Sends data to Origin via originpro: one workbook, one graph, one curve per file
- X axis: Temperature (°C), Y axis: Strain (%)

Requirements
- Python with Tkinter (bundled with most Python installs)
- originpro (OriginLab Python package) with Origin installed

Notes
- The parser looks for lines that start with "Step time" and then reads the numeric table
  that follows. It extracts the columns named exactly "Temperature" and "Strain".
- If a file contains multiple table sections, all rows are concatenated.
- Non‑critical errors are shown in a message box; unsupported files are skipped.
"""
from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import json
import os
import queue
import re
import sys
import threading
from pathlib import Path
from typing import Dict, List, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

# --- Optional: make import error friendlier
try:
    import originpro as op  # type: ignore
except Exception as e:  # don't crash import; we'll validate on Plot
    op = None  # type: ignore
    _origin_import_error = e
else:
    _origin_import_error = None
try:
    from plotting.shared.origin import origin_session
except Exception:
    origin_session = None  # type: ignore


NUMERIC = re.compile(r"^[\s\t]*[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?")
SPLIT = re.compile(r"\t+|\s{2,}")  # tabs or 2+ spaces


def _is_numeric_line(line: str) -> bool:
    return bool(NUMERIC.match(line))


def _try_float(tok: str) -> float | None:
    try:
        return float(tok)
    except Exception:
        return None


def parse_dma_txt(filepath: Path) -> Dict[int, Tuple[List[float], List[float]]]:
    """Parse a TA DMA exported .txt file for IsoStress data.

    Returns a dictionary mapping an approximate stress level (in MPa, rounded to an
    integer) to a pair of (Temperature, Strain) lists.
    """
    datasets: Dict[int, Tuple[List[float], List[float]]] = {}

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if "IsoStress" in line:
            # Found an IsoStress block: find the header that follows
            j = i + 1
            header_line_idx = -1
            while j < n and not lines[j].strip().startswith("[step]"):
                if lines[j].strip().startswith("Step time"):
                    header_line_idx = j
                    break
                j += 1

            if header_line_idx == -1:
                i += 1
                continue

            header = SPLIT.split(lines[header_line_idx].strip())
            k = header_line_idx + 1

            # Skip units line
            if k < n and ("°C" in lines[k] or "%" in lines[k] or "MPa" in lines[k]):
                k += 1

            # Get column indices
            temp_idx = next((idx for idx, name in enumerate(header) if name.strip().lower().startswith("temperature")), -1)
            strain_idx = next((idx for idx, name in enumerate(header) if name.strip().lower() == "strain"), -1)
            stress_idx = next((idx for idx, name in enumerate(header) if name.strip().lower() == "stress"), -1)

            if -1 in (temp_idx, strain_idx, stress_idx):
                i = k
                continue

            # Read data rows
            temps, strains, stresses = [], [], []
            while k < n:
                row_line = lines[k].strip()
                if not row_line or not _is_numeric_line(row_line):
                    break
                toks = SPLIT.split(row_line)
                if max(temp_idx, strain_idx, stress_idx) < len(toks):
                    t = _try_float(toks[temp_idx])
                    e = _try_float(toks[strain_idx])
                    s = _try_float(toks[stress_idx])
                    if all(v is not None for v in (t, e, s)):
                        temps.append(t)
                        strains.append(e)
                        stresses.append(s)
                k += 1

            if stresses:
                avg_stress = round(sum(stresses) / len(stresses))
                if avg_stress not in datasets:
                    datasets[avg_stress] = ([], [])
                datasets[avg_stress][0].extend(temps)
                datasets[avg_stress][1].extend(strains)
            i = k
        else:
            i += 1

    # Keep data in original order (heating/cooling sequence)
    return datasets


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Temperature–Strain -> Origin")
        self.minsize(760, 460)

        self.config_path = Path.home() / ".dma_isostress_ui.json"

        # Top controls
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=12, pady=10)

        self.btn_files = ttk.Button(top, text="Select Files…", command=self.select_files)
        self.btn_files.pack(side=tk.LEFT)

        self.btn_folder = ttk.Button(top, text="Select Folder…", command=self.select_folder)
        self.btn_folder.pack(side=tk.LEFT, padx=8)

        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Include subfolders", variable=self.recursive_var).pack(side=tk.LEFT, padx=12)

        self.clear_btn = ttk.Button(top, text="Clear", command=self.clear)
        self.clear_btn.pack(side=tk.LEFT, padx=8)

        # File list
        mid = ttk.Frame(self)
        mid.pack(fill=tk.BOTH, expand=True, padx=12)
        self.files_list = tk.Listbox(mid, selectmode=tk.EXTENDED)
        self.files_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.files_list.yview)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.files_list.config(yscrollcommand=yscroll.set)

        # Bottom controls
        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=12, pady=10)

        self.plot_btn = ttk.Button(bottom, text="Plot in Origin", command=self.plot_in_origin)
        self.plot_btn.pack(side=tk.LEFT)

        self.plot_mpl_btn = ttk.Button(bottom, text="Plot in Matplotlib", command=self.plot_in_matplotlib)
        self.plot_mpl_btn.pack(side=tk.LEFT, padx=8)

        self.export_btn = ttk.Button(bottom, text="Export to TXT", command=self.export_to_txt)
        self.export_btn.pack(side=tk.LEFT, padx=8)

        self.keep_files_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Keep file list", variable=self.keep_files_var).pack(side=tk.LEFT, padx=12)

        self.progress = ttk.Progressbar(bottom, orient=tk.HORIZONTAL, mode='indeterminate')
        self.progress.pack_forget()

        ttk.Button(bottom, text="Quit", command=self.destroy).pack(side=tk.RIGHT)

        # Hints
        hint = ttk.Label(self, text=(
            "Select one or more TA DMA .txt files, or choose a folder.\n"
            "Each file will be parsed and plotted as a separate curve (Temperature vs Strain)."
        ))
        hint.pack(fill=tk.X, padx=12, pady=(0, 10))

        # Keep a shadow set for quick membership checks
        self._file_set: set[Path] = set()

        self.load_config()

    def destroy(self):
        self.save_config()
        super().destroy()

    def load_config(self):
        if not self.config_path.exists():
            return
        try:
            with open(self.config_path, "r") as f:
                config = json.load(f)
            
            self.keep_files_var.set(config.get("keep_files", False))
            if self.keep_files_var.get():
                paths = [Path(p) for p in config.get("files", [])]
                self._add_paths(paths)
        except Exception as e:
            print(f"Error loading config: {e}")

    def save_config(self):
        try:
            config = {"keep_files": self.keep_files_var.get()}
            if self.keep_files_var.get():
                config["files"] = [self.files_list.get(i) for i in range(self.files_list.size())]
            
            with open(self.config_path, "w") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    # --- UI helpers -----------------------------------------------------
    def clear(self) -> None:
        self.files_list.delete(0, tk.END)
        self._file_set.clear()

    def _add_paths(self, paths: List[Path]) -> None:
        added = 0
        for p in paths:
            if p.exists() and p.is_file() and p.suffix.lower() == ".txt" and p not in self._file_set:
                self.files_list.insert(tk.END, str(p))
                self._file_set.add(p)
                added += 1
        if added == 0:
            messagebox.showinfo("No files added", "No new .txt files were added.")

    def select_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Select DMA .txt files",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        self._add_paths([Path(p) for p in paths])

    def select_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Select a folder with DMA .txt files")
        if not folder:
            return
        
        self.progress.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        self.progress.start()
        self.file_queue = queue.Queue()
        self.search_thread = threading.Thread(
            target=self._search_files_threaded,
            args=(Path(folder), self.recursive_var.get(), self.file_queue),
            daemon=True
        )
        self.search_thread.start()
        self.after(100, self._check_search_thread)

    def _search_files_threaded(self, base: Path, recursive: bool, q: queue.Queue):
        try:
            if recursive:
                files = list(base.rglob("*.txt"))
            else:
                files = list(base.glob("*.txt"))
            files.sort()
            q.put(files)
        except Exception as e:
            q.put(e)

    def _check_search_thread(self):
        try:
            result = self.file_queue.get_nowait()
            self.progress.stop()
            self.progress.pack_forget()
            if isinstance(result, Exception):
                messagebox.showerror("Error", f"Failed to search for files: {result}")
            else:
                self._add_paths(result)
        except queue.Empty:
            self.after(100, self._check_search_thread)

    def plot_in_matplotlib(self):
        if plt is None:
            messagebox.showerror("Matplotlib not found", "Please install matplotlib: pip install matplotlib")
            return

        file_paths = [Path(self.files_list.get(i)) for i in range(self.files_list.size())]
        if not file_paths:
            messagebox.showwarning("No files", "Please add at least one .txt file.")
            return

        for fp in file_paths:
            try:
                stress_datasets = parse_dma_txt(fp)
                if not stress_datasets:
                    continue

                plt.figure() # new figure for each file
                for stress_val, (T, E) in sorted(stress_datasets.items()):
                    if T:
                        plt.plot(T, E, label=f"{stress_val} MPa")
                
                plt.title(fp.stem)
                plt.xlabel("Temperature (°C)")
                plt.ylabel("Strain (%)")
                plt.legend()
                plt.grid(True)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to plot {fp.name}: {e}")
        
        plt.show() # Show all figures

    def export_to_txt(self):
        file_paths = [Path(self.files_list.get(i)) for i in range(self.files_list.size())]
        if not file_paths:
            messagebox.showwarning("No files", "Please add at least one .txt file.")
            return

        exported_count = 0
        for fp in file_paths:
            try:
                stress_datasets = parse_dma_txt(fp)
                if not stress_datasets:
                    continue

                # Save to Downloads folder
                downloads_dir = Path.home() / "Downloads"
                output_path = downloads_dir / f"{fp.stem}.processed.txt"
                
                long_names = []
                units = []
                comments = []
                data_cols = []

                sorted_datasets = sorted(stress_datasets.items())

                for stress_val, (T, E) in sorted_datasets:
                    if not T: continue
                    
                    long_names.append("Temperature")
                    units.append("°C")
                    comments.append(f"{stress_val} MPa")
                    data_cols.append(T)

                    long_names.append("Strain")
                    units.append("%")
                    comments.append(f"{stress_val} MPa")
                    data_cols.append(E)

                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write("\t".join(long_names) + "\n")
                    f.write("\t".join(units) + "\n")
                    f.write("\t".join(comments) + "\n")
                    
                    max_len = max(len(c) for c in data_cols) if data_cols else 0
                    for i in range(max_len):
                        row_data = []
                        for col in data_cols:
                            row_data.append(str(col[i]) if i < len(col) else "")
                        f.write("\t".join(row_data) + "\n")
                
                exported_count += 1
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export {fp.name}: {e}")

        if exported_count > 0:
            messagebox.showinfo("Export complete", f"Exported {exported_count} file(s).")

    # --- Origin plotting ------------------------------------------------
    def plot_in_origin(self) -> None:
        if self.files_list.size() == 0:
            messagebox.showwarning("No files", "Please add at least one .txt file.")
            return

        self.progress.pack_forget()

        if op is None or origin_session is None:
            messagebox.showerror(
                "originpro not available",
                (
                    f"Could not import originpro: {_origin_import_error}\n\n"
                    "Please install/configure Origin's Python package and try again."
                ),
            )
            return

        file_paths = [Path(self.files_list.get(i)) for i in range(self.files_list.size())]
        plotted_count = 0
        skipped_files: List[str] = []

        with origin_session(keep_open=True) as origin:
            for fp in file_paths:
                try:
                    stress_datasets = parse_dma_txt(fp)
                    if not stress_datasets:
                        skipped_files.append(f"{fp.name} (no IsoStress data found)")
                        continue

                    # Create a new workbook and graph for each file
                    book = origin.new_book('w')
                    book.lname = fp.stem
                    wks = book[0]
                    wks.name = 'Data'

                    graph = origin.new_graph()
                    graph.title = fp.stem
                    layer = graph[0]

                    colx = 0
                    for stress_val, (T, E) in sorted(stress_datasets.items()):
                        if not T:
                            continue

                        label = f"{stress_val} MPa"

                        wks.from_list(colx, T)
                        col_t = wks.obj.Columns(colx)
                        col_t.LongName = "Temperature"
                        col_t.Units = "°C"
                        col_t.Comment = label
                        col_t.Type = 3  # X

                        wks.from_list(colx + 1, E)
                        col_e = wks.obj.Columns(colx + 1)
                        col_e.LongName = "Strain"
                        col_e.Units = "%"
                        col_e.Comment = label
                        col_e.Type = 4  # Y

                        layer.add_plot(wks, coly=colx + 1, colx=colx)
                        colx += 2

                    try:
                        wks.header_rows("LUC")
                    except Exception:
                        pass

                    layer.rescale()
                    layer.axis(0).title = "Temperature (°C)"  # 0 = bottom axis
                    layer.axis(1).title = "Strain (%)"        # 1 = left axis
                    layer.add_legend()
                    plotted_count += 1

                except Exception as ex:
                    import traceback
                    tb_str = traceback.format_exc()
                    skipped_files.append(f"{fp.name} (error: {ex})\n{tb_str}")

        # Report
        if skipped_files:
            error_win = tk.Toplevel(self)
            error_win.title("Plotting warnings")
            error_win.minsize(600, 400)
            text = tk.Text(error_win, wrap=tk.WORD)
            text.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
            
            msg = f"Plotted {plotted_count} file(s) in separate graphs.\n\nSkipped or had issues with:\n" + "\n".join(skipped_files)
            text.insert(tk.END, msg)
            text.config(state=tk.DISABLED)

            ttk.Button(error_win, text="Close", command=error_win.destroy).pack(pady=5)

        elif plotted_count > 0:
            messagebox.showinfo("Done", f"Plotted {plotted_count} file(s) in separate graphs.")
        else:
            messagebox.showerror("Nothing to plot", "No valid datasets were found in any of the selected files.")

if __name__ == "__main__":
    # Tk themed widgets look nicer on recent Python versions
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = App()
    app.mainloop()
