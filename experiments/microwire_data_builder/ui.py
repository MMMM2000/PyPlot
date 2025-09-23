"""Tkinter user interface for the microwire database builder."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Iterable, List

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import (
    LOGGER_NAME,
    BuilderConfig,
    build_database,
)


class TextHandler(logging.Handler):
    """Logging handler that appends messages to a Tkinter text widget."""

    def __init__(self, widget: tk.Text) -> None:
        super().__init__()
        self.widget = widget

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        self.widget.after(0, self._append, message)

    def _append(self, message: str) -> None:
        self.widget.configure(state="normal")
        self.widget.insert("end", message + "\n")
        self.widget.see("end")
        self.widget.configure(state="disabled")


class BuilderApp:
    """Tkinter front-end for the microwire database builder."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Microwire Data Builder")
        self.fabrication_paths: List[Path] = []
        self.annealing_paths: List[Path] = []

        default_output = Path.cwd() / "builder_output"
        self.output_dir_var = tk.StringVar(value=str(default_output))
        self.make_plots_var = tk.BooleanVar(value=False)
        self.export_excel_var = tk.BooleanVar(value=False)
        self.export_parquet_var = tk.BooleanVar(value=False)
        self.fabrication_recursive_var = tk.BooleanVar(value=True)
        self.anneal_recursive_var = tk.BooleanVar(value=True)

        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="Idle")
        self._running = False

        self._build_ui()
        self._configure_logging()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)

        fab_frame = ttk.LabelFrame(main, text="Fabrication spreadsheets (.xlsx)")
        fab_frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        fab_frame.columnconfigure(0, weight=1)

        self.fabrication_list = tk.Listbox(fab_frame, height=6, selectmode="browse")
        self.fabrication_list.grid(row=0, column=0, columnspan=3, sticky="nsew", pady=(0, 4))
        fab_frame.rowconfigure(0, weight=1)

        ttk.Button(fab_frame, text="Add files…", command=self._add_fabrication_files).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(fab_frame, text="Add folder…", command=self._add_fabrication_folder).grid(row=1, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(fab_frame, text="Clear", command=self._clear_fabrication).grid(row=1, column=2, sticky="ew")
        ttk.Checkbutton(
            fab_frame,
            text="Recursive scan",
            variable=self.fabrication_recursive_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w")

        anneal_frame = ttk.LabelFrame(main, text="Current-annealing files (.txt)")
        anneal_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        anneal_frame.columnconfigure(0, weight=1)

        self.anneal_list = tk.Listbox(anneal_frame, height=8, selectmode="browse")
        self.anneal_list.grid(row=0, column=0, columnspan=3, sticky="nsew", pady=(0, 4))
        anneal_frame.rowconfigure(0, weight=1)

        ttk.Button(anneal_frame, text="Add files…", command=self._add_anneal_files).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(anneal_frame, text="Add folder…", command=self._add_anneal_folder).grid(row=1, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(anneal_frame, text="Clear", command=self._clear_anneal).grid(row=1, column=2, sticky="ew")
        ttk.Checkbutton(
            anneal_frame,
            text="Recursive scan",
            variable=self.anneal_recursive_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w")

        options = ttk.LabelFrame(main, text="Options")
        options.grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        ttk.Checkbutton(options, text="Generate plots (PNG)", variable=self.make_plots_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(options, text="Also export Excel", variable=self.export_excel_var).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(options, text="Also export Parquet", variable=self.export_parquet_var).grid(row=2, column=0, sticky="w")

        output = ttk.LabelFrame(main, text="Output")
        output.grid(row=3, column=0, sticky="ew", padx=4, pady=4)
        output.columnconfigure(1, weight=1)
        ttk.Label(output, text="Directory:").grid(row=0, column=0, sticky="w")
        self.output_entry = ttk.Entry(output, textvariable=self.output_dir_var)
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(output, text="Browse…", command=self._select_output_dir).grid(row=0, column=2, sticky="ew")

        progress_frame = ttk.Frame(main)
        progress_frame.grid(row=4, column=0, sticky="ew", padx=4, pady=4)
        progress_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=1.0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(progress_frame, textvariable=self.progress_text_var)
        self.progress_label.grid(row=0, column=1, padx=(8, 0))

        log_frame = ttk.LabelFrame(main, text="Log")
        log_frame.grid(row=5, column=0, sticky="nsew", padx=4, pady=4)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=12, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.run_button = ttk.Button(main, text="Run", command=self.run_builder)
        self.run_button.grid(row=6, column=0, sticky="ew", padx=4, pady=(4, 0))

        for row in range(7):
            main.rowconfigure(row, weight=0)
        main.rowconfigure(5, weight=1)

    def _configure_logging(self) -> None:
        self.logger = logging.getLogger(LOGGER_NAME)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.text_handler = TextHandler(self.log_text)
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        self.text_handler.setFormatter(formatter)
        if self.text_handler not in self.logger.handlers:
            self.logger.addHandler(self.text_handler)

    # ------------------------------------------------------------------ helpers
    def _update_listbox(self, listbox: tk.Listbox, items: Iterable[Path]) -> None:
        listbox.delete(0, "end")
        for path in sorted({str(p) for p in items}):
            listbox.insert("end", path)

    def _add_fabrication_files(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[("Excel", "*.xlsx")])
        if not paths:
            return
        self.fabrication_paths.extend(Path(p) for p in paths)
        self._update_listbox(self.fabrication_list, self.fabrication_paths)

    def _add_fabrication_folder(self) -> None:
        folder = filedialog.askdirectory()
        if not folder:
            return
        root = Path(folder)
        pattern = root.rglob("*.xlsx") if self.fabrication_recursive_var.get() else root.glob("*.xlsx")
        self.fabrication_paths.extend(Path(p) for p in pattern)
        self._update_listbox(self.fabrication_list, self.fabrication_paths)

    def _clear_fabrication(self) -> None:
        self.fabrication_paths.clear()
        self.fabrication_list.delete(0, "end")

    def _add_anneal_files(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[("Text", "*.txt")])
        if not paths:
            return
        self.annealing_paths.extend(Path(p) for p in paths)
        self._update_listbox(self.anneal_list, self.annealing_paths)

    def _add_anneal_folder(self) -> None:
        folder = filedialog.askdirectory()
        if not folder:
            return
        root = Path(folder)
        pattern = root.rglob("*.txt") if self.anneal_recursive_var.get() else root.glob("*.txt")
        self.annealing_paths.extend(Path(p) for p in pattern)
        self._update_listbox(self.anneal_list, self.annealing_paths)

    def _clear_anneal(self) -> None:
        self.annealing_paths.clear()
        self.anneal_list.delete(0, "end")

    def _select_output_dir(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self.output_dir_var.set(folder)

    def _set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        self.run_button.configure(state=state)
        if running:
            self.progress_var.set(0.0)
            self.progress_text_var.set("Running…")
        else:
            self.progress_text_var.set("Idle")

    def _progress_callback(self, current: int, total: int) -> None:
        total = max(total, 1)
        value = current / total
        self.root.after(0, self.progress_var.set, value)
        self.root.after(0, self.progress_text_var.set, f"{current}/{total}")

    def run_builder(self) -> None:
        if self._running:
            return
        if not self.annealing_paths:
            messagebox.showwarning("Microwire Data Builder", "Please add at least one annealing file.")
            return
        output_dir = Path(self.output_dir_var.get()).expanduser()
        if not output_dir:
            messagebox.showwarning("Microwire Data Builder", "Please choose an output directory.")
            return
        config = BuilderConfig(
            fabrication_files=list(dict.fromkeys(self.fabrication_paths)),
            annealing_files=list(dict.fromkeys(self.annealing_paths)),
            output_dir=output_dir,
            make_plots=self.make_plots_var.get(),
            export_excel=self.export_excel_var.get(),
            export_parquet=self.export_parquet_var.get(),
        )
        self._set_running(True)
        thread = threading.Thread(target=self._run_worker, args=(config,), daemon=True)
        thread.start()

    def _run_worker(self, config: BuilderConfig) -> None:
        file_handler = None
        try:
            log_path = config.output_dir / config.log_file_name
            config.output_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            self.logger.addHandler(file_handler)

            self.logger.info("Starting build with %s annealing file(s)", len(config.annealing_files))
            result = build_database(
                config,
                logger=self.logger,
                progress_callback=self._progress_callback,
                root_for_relpaths=Path.cwd(),
            )
            self.logger.info("CSV written to %s", result.csv_path)
            if result.excel_path:
                self.logger.info("Excel written to %s", result.excel_path)
            if result.parquet_path:
                self.logger.info("Parquet written to %s", result.parquet_path)
            if config.make_plots:
                self.logger.info("Generated %s plot(s)", len(result.plot_paths))
            stats = result.stats
            self.logger.info(
                "Summary: parsed=%s skipped=%s missing_draw=%s missing_piece=%s R≈V/I failures=%s",
                stats.parsed,
                stats.skipped,
                stats.missing_draw,
                stats.missing_piece,
                stats.resistance_checks_failed,
            )
        except Exception:
            self.logger.exception("Build failed")
            self.root.after(0, lambda: messagebox.showerror("Microwire Data Builder", "Build failed. See log for details."))
        finally:
            if file_handler:
                self.logger.removeHandler(file_handler)
                file_handler.close()
            self.root.after(0, self._set_running, False)


def run_app() -> None:
    root = tk.Tk()
    app = BuilderApp(root)
    root.mainloop()


def main() -> None:
    run_app()


__all__ = ["BuilderApp", "main", "run_app"]
