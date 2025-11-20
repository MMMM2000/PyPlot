from __future__ import annotations

import threading
import tkinter as tk
from abc import ABC, abstractmethod
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Iterable


class SimpleScriptProcessor(ABC):
    """Abstract base class that each simple script implements."""

    def __init__(self) -> None:
        self._logger: Callable[[str], None] = lambda message: None

    def attach_logger(self, callback: Callable[[str], None]) -> None:
        self._logger = callback

    def log(self, message: str) -> None:
        try:
            self._logger(message)
        except Exception:
            pass

    @abstractmethod
    def load(self, paths: list[Path]) -> Any:
        """Return the parsed representation for the selected files."""

    @abstractmethod
    def plot_matplotlib(self, dataset: Any) -> None:
        """Render one or more Matplotlib figures for ``dataset``."""

    @abstractmethod
    def plot_origin(self, dataset: Any) -> None:
        """Send ``dataset`` to Origin."""

    @abstractmethod
    def export_txt(self, dataset: Any, output_dir: Path) -> None:
        """Write TXT exports for ``dataset`` into ``output_dir``."""


class SimpleScriptApp(tk.Tk):
    """Reusable Tk application hosting file pickers and action buttons."""

    def __init__(self, title: str, processor: SimpleScriptProcessor) -> None:
        super().__init__()
        self.title(title)
        self.minsize(880, 520)
        self.processor = processor
        attach = getattr(self.processor, "attach_logger", None)
        if callable(attach):
            attach(self.log)
        self._selected_paths: list[Path] = []
        self._dataset: Any | None = None
        self._busy = False
        self._closing = False
        self._worker_threads: set[threading.Thread] = set()
        self._init_theme()
        self._build_ui()
        try:
            self.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

    # ------------------------------------------------------------------ UI helpers
    def _init_theme(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        bg = "#0b1120"
        fg = "#e5e7eb"
        accent = "#1f2937"
        self.configure(background=bg)
        style.configure(
            ".",
            background=bg,
            foreground=fg,
            fieldbackground=accent,
        )
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure(
            "TButton",
            background="#111827",
            foreground=fg,
            padding=6,
        )
        style.map(
            "TButton",
            background=[("pressed", "#1d4ed8"), ("active", "#374151")],
            foreground=[("disabled", "#6b7280")],
        )

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=12, pady=10)
        self.top_frame = top

        ttk.Button(top, text="Add Files…", command=self._add_files).pack(side=tk.LEFT)
        ttk.Button(top, text="Add Folder…", command=self._add_folder).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(top, text="Clear", command=self._clear_files).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(top, text="Load Data", command=self._load_data).pack(
            side=tk.LEFT, padx=6
        )

        self.options_frame = ttk.Frame(self)
        self.options_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left, text="Selected files:").pack(anchor="w")
        self.files_list = tk.Listbox(
            left,
            selectmode=tk.EXTENDED,
            activestyle="none",
            height=12,
            background="#0f172a",
            foreground="#f9fafb",
            selectbackground="#1d4ed8",
            highlightthickness=0,
            borderwidth=0,
        )
        self.files_list.pack(fill=tk.BOTH, expand=True, pady=(4, 8))

        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
        ttk.Label(right, text="Log:").pack(anchor="w")
        self.log_text = tk.Text(
            right,
            height=12,
            state=tk.DISABLED,
            background="#0f172a",
            foreground="#f9fafb",
            highlightthickness=0,
            borderwidth=0,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(4, 8))

        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=12, pady=10)

        self.plot_mpl_btn = ttk.Button(
            bottom, text="Plot Matplotlib", command=self._plot_matplotlib, state=tk.DISABLED
        )
        self.plot_mpl_btn.pack(side=tk.LEFT)

        self.plot_origin_btn = ttk.Button(
            bottom, text="Plot Origin", command=self._plot_origin, state=tk.DISABLED
        )
        self.plot_origin_btn.pack(side=tk.LEFT, padx=8)

        self.export_btn = ttk.Button(
            bottom, text="Export TXT", command=self._export_txt, state=tk.DISABLED
        )
        self.export_btn.pack(side=tk.LEFT)

        ttk.Button(bottom, text="Quit", command=self.destroy).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ File and task handling
    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self, title="Select files")
        if paths:
            self._push_paths(Path(p) for p in paths)

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Select folder")
        if not folder:
            return
        path = Path(folder)
        candidates: list[Path] = []
        for child in path.iterdir():
            if child.is_file():
                candidates.append(child)
        self._push_paths(candidates)

    def _push_paths(self, paths: Iterable[Path]) -> None:
        added = 0
        for path in paths:
            if path not in self._selected_paths:
                self._selected_paths.append(path)
                self.files_list.insert(tk.END, str(path))
                added += 1
        if added:
            self.log(f"Added {added} file(s).")
        if added:
            self._dataset = None
            self._update_action_states()

    def _clear_files(self) -> None:
        self._selected_paths.clear()
        self.files_list.delete(0, tk.END)
        self._dataset = None
        self._update_action_states()
        self.log("Cleared selection.")

    def _load_data(self) -> None:
        if not self._selected_paths or self._busy:
            return
        self._run_task(self._load_worker, background=True)

    def _load_worker(self) -> None:
        self.log("Loading data…")
        dataset = self.processor.load(self._selected_paths.copy())
        self.after(0, lambda d=dataset: self._finish_load(d))

    def _finish_load(self, dataset: Any) -> None:
        self._dataset = dataset
        self.log("Data ready.")
        self._update_action_states()

    def _plot_matplotlib(self) -> None:
        if self._busy or self._dataset is None:
            return
        self._run_task(self.processor.plot_matplotlib, self._dataset, background=False)

    def _plot_origin(self) -> None:
        if self._busy or self._dataset is None:
            return
        self._run_task(self.processor.plot_origin, self._dataset, background=False)

    def _export_txt(self) -> None:
        if self._busy or self._dataset is None:
            return
        target = filedialog.askdirectory(parent=self, title="Select export folder")
        if not target:
            return
        self._run_task(
            self.processor.export_txt, self._dataset, Path(target), background=False
        )

    def _run_task(self, func, *args, background: bool = True) -> None:
        def worker() -> None:
            try:
                func(*args)
            except Exception as exc:  # pragma: no cover - UI side-effect
                self.after(0, lambda: self._handle_error(exc))
            finally:
                self.after(0, self._task_complete)
                self.after(
                    0,
                    lambda thr=threading.current_thread(): self._worker_threads.discard(thr),
                )

        if self._busy or self._closing:
            return
        self._busy = True
        self._set_buttons_state(tk.DISABLED)
        if background:
            thread = threading.Thread(target=worker, daemon=True)
            self._worker_threads.add(thread)
            thread.start()
        else:
            try:
                func(*args)
            except Exception as exc:
                self._handle_error(exc)
            finally:
                self._task_complete()

    def _task_complete(self) -> None:
        self._busy = False
        self._update_action_states()

    def _handle_error(self, exc: Exception) -> None:
        self.log(f"ERROR: {exc}")
        messagebox.showerror("Error", str(exc), parent=self)

    def _update_action_states(self) -> None:
        has_data = self._dataset is not None
        state = tk.NORMAL if has_data and not self._busy else tk.DISABLED
        self.plot_mpl_btn.config(state=state)
        self.plot_origin_btn.config(state=state)
        self.export_btn.config(state=state)

    def _set_buttons_state(self, state: str) -> None:
        self.plot_mpl_btn.config(state=state)
        self.plot_origin_btn.config(state=state)
        self.export_btn.config(state=state)

    def log(self, message: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda msg=message: self.log(msg))
            return
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _on_close(self) -> None:
        self._closing = True
        self._set_buttons_state(tk.DISABLED)
        for thread in list(self._worker_threads):
            try:
                thread.join(timeout=1.0)
            except Exception:
                continue
        try:
            super().destroy()
        except Exception:
            pass


__all__ = ["SimpleScriptApp", "SimpleScriptProcessor"]
