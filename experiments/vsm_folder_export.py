#!/usr/bin/env python3
"""Convert VSM hysteresis/temp scan files into simple TXT tables."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

import pandas as pd
import re
try:
    from PyQt6 import QtCore, QtWidgets
except Exception:  # pragma: no cover - optional dependency
    QtCore = None  # type: ignore[assignment]
    QtWidgets = None  # type: ignore[assignment]

try:
    from plotting.plugins.vsm_hysteresis.vsm_hysteresis_loops import _read_vsm_file
except ModuleNotFoundError:
    root = Path(__file__).resolve().parents[1]
    sys.path.append(str(root))
    from plotting.plugins.vsm_hysteresis.vsm_hysteresis_loops import (  # type: ignore
        _read_vsm_file,
    )


_COLUMN_LINE_RE = re.compile(r"^Column\s+\d+\s*:\s*(.+)$")
_VSM_FILENAME_RE = re.compile(
    r"(?:-hys-|-tscn-|\.vsm-(?:hys|tscn)-data$)",
    re.IGNORECASE,
)


def _looks_like_vsm_data_file(path: Path) -> bool:
    return bool(_VSM_FILENAME_RE.search(path.name))


def _collect_files(root: Path, recursive: bool) -> list[Path]:
    if recursive:
        candidates = [path for path in root.rglob("*") if path.is_file()]
    else:
        candidates = [path for path in root.iterdir() if path.is_file()]
    return [path for path in candidates if _looks_like_vsm_data_file(path)]


def _write_frame(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, sep="\t", index=False)


def _extract_column_names(path: Path) -> list[str]:
    columns: list[str] = []
    in_columns = False
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("@@Columns"):
                    in_columns = True
                    continue
                if in_columns and stripped.startswith("@@END Columns"):
                    break
                if not in_columns:
                    continue
                match = _COLUMN_LINE_RE.match(stripped)
                if match:
                    name = match.group(1).strip()
                    if name:
                        columns.append(name)
    except Exception:
        return []
    return columns


def _convert_file(
    path: Path,
    root: Path,
    output_root: Path,
) -> Tuple[bool, str]:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except Exception:
        rel = path.name
    output_path = output_root / rel
    output_path = output_path.with_suffix(".txt")
    try:
        frame = _read_vsm_file(path)
    except Exception as exc:
        return False, f"parse failed ({exc})"
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return False, "no tabular rows"
    raw_columns = _extract_column_names(path)
    if raw_columns and len(raw_columns) == frame.shape[1]:
        frame = frame.copy()
        frame.columns = raw_columns
    _write_frame(frame, output_path)
    return True, "ok"


def convert_folder(
    input_root: Path,
    output_root: Path,
    *,
    recursive: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[int, int]:
    files = _collect_files(input_root, recursive)
    if not files:
        raise RuntimeError("No VSM hysteresis/temperature files were found in the input folder.")
    converted = 0
    skipped = 0
    for path in files:
        if path.name.startswith("._"):
            continue
        ok, reason = _convert_file(path, input_root, output_root)
        if ok:
            converted += 1
            if log:
                log(f"Converted: {path.name}")
        else:
            skipped += 1
            if log:
                log(f"Skipped: {path.name} ({reason})")
    return converted, skipped


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert VSM hysteresis/temp scan files to simple TXT tables."
    )
    parser.add_argument("input", type=Path, help="Folder containing VSM data files.")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help="Output folder for formatted TXT files (default: <input>_formatted).",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scan input folder recursively.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _parse_args(argv)
    input_root = args.input
    if not input_root.exists() or not input_root.is_dir():
        print(f"Input folder not found: {input_root}")
        return 1
    output_root = args.output or input_root.with_name(f"{input_root.name}_formatted")

    try:
        converted, skipped = convert_folder(
            input_root,
            output_root,
            recursive=bool(args.recursive),
        )
    except Exception as exc:
        print(str(exc))
        return 1
    print(f"Converted {converted} file(s). Skipped {skipped} file(s).")
    print(f"Output folder: {output_root}")
    return 0


if QtCore is not None:

    class _ExportWorker(QtCore.QObject):
        finished = QtCore.pyqtSignal(int, int, object)
        failed = QtCore.pyqtSignal(str)
        log = QtCore.pyqtSignal(str)

        def __init__(self, input_root: Path, output_root: Path, recursive: bool) -> None:
            super().__init__()
            self._input_root = input_root
            self._output_root = output_root
            self._recursive = recursive

        @QtCore.pyqtSlot()
        def run(self) -> None:
            try:
                converted, skipped = convert_folder(
                    self._input_root,
                    self._output_root,
                    recursive=self._recursive,
                    log=self.log.emit,
                )
            except Exception as exc:
                self.failed.emit(str(exc))
                return
            self.finished.emit(converted, skipped, self._output_root)


def launch_gui() -> QtWidgets.QWidget | None:
    if QtWidgets is None or QtCore is None:  # pragma: no cover - optional dependency
        print("PyQt6 is required to launch this tool.")
        return None

    dialog = QtWidgets.QDialog(QtWidgets.QApplication.activeWindow())
    dialog.setWindowTitle("VSM Folder Export")
    dialog.resize(700, 420)
    dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
    layout = QtWidgets.QVBoxLayout(dialog)

    form = QtWidgets.QFormLayout()
    input_edit = QtWidgets.QLineEdit()
    output_edit = QtWidgets.QLineEdit()
    input_btn = QtWidgets.QPushButton("Browse...")
    output_btn = QtWidgets.QPushButton("Browse...")

    def _pick_input() -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Select input folder")
        if folder:
            input_edit.setText(folder)
            if not output_edit.text().strip():
                output_edit.setText(f"{folder}_formatted")

    def _pick_output() -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Select output folder")
        if folder:
            output_edit.setText(folder)

    input_btn.clicked.connect(_pick_input)
    output_btn.clicked.connect(_pick_output)
    input_row = QtWidgets.QHBoxLayout()
    input_row.addWidget(input_edit, 1)
    input_row.addWidget(input_btn)
    output_row = QtWidgets.QHBoxLayout()
    output_row.addWidget(output_edit, 1)
    output_row.addWidget(output_btn)
    form.addRow("Input folder:", input_row)
    form.addRow("Output folder:", output_row)
    layout.addLayout(form)

    recursive_cb = QtWidgets.QCheckBox("Scan folders recursively")
    recursive_cb.setChecked(True)
    layout.addWidget(recursive_cb)

    log_box = QtWidgets.QPlainTextEdit()
    log_box.setReadOnly(True)
    layout.addWidget(log_box, 1)

    button_row = QtWidgets.QHBoxLayout()
    run_button = QtWidgets.QPushButton("Convert")
    close_button = QtWidgets.QPushButton("Close")
    button_row.addStretch(1)
    button_row.addWidget(run_button)
    button_row.addWidget(close_button)
    layout.addLayout(button_row)

    thread_holder: dict[str, object] = {"thread": None, "worker": None}

    def _log(message: str) -> None:
        log_box.appendPlainText(message)

    def _set_busy(busy: bool) -> None:
        run_button.setEnabled(not busy)
        input_edit.setEnabled(not busy)
        output_edit.setEnabled(not busy)
        input_btn.setEnabled(not busy)
        output_btn.setEnabled(not busy)
        recursive_cb.setEnabled(not busy)

    def _start() -> None:
        input_path = Path(input_edit.text().strip())
        output_path = Path(output_edit.text().strip())
        if not input_path.exists() or not input_path.is_dir():
            QtWidgets.QMessageBox.critical(dialog, "VSM Folder Export", "Select a valid input folder.")
            return
        if not output_path:
            QtWidgets.QMessageBox.critical(dialog, "VSM Folder Export", "Select an output folder.")
            return
        _set_busy(True)
        log_box.clear()
        worker = _ExportWorker(input_path, output_path, recursive_cb.isChecked())
        thread = QtCore.QThread(dialog)
        worker.moveToThread(thread)
        worker.log.connect(_log)
        worker.finished.connect(lambda conv, skip, out: _on_finish(conv, skip, out))
        worker.failed.connect(_on_fail)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: _cleanup(thread, worker))
        thread.started.connect(worker.run)
        thread_holder["thread"] = thread
        thread_holder["worker"] = worker
        thread.start()

    def _cleanup(thread: QtCore.QThread, worker: _ExportWorker) -> None:
        try:
            worker.deleteLater()
        except Exception:
            pass
        try:
            thread.deleteLater()
        except Exception:
            pass
        thread_holder["thread"] = None
        thread_holder["worker"] = None
        _set_busy(False)

    def _on_finish(converted: int, skipped: int, out: object) -> None:
        _log(f"Converted {converted} file(s). Skipped {skipped} file(s).")
        _log(f"Output folder: {out}")
        QtWidgets.QMessageBox.information(
            dialog,
            "VSM Folder Export",
            f"Converted {converted} file(s).\nOutput folder:\n{out}",
        )

    def _on_fail(message: str) -> None:
        QtWidgets.QMessageBox.critical(dialog, "VSM Folder Export", message)
        _log(message)

    run_button.clicked.connect(_start)
    close_button.clicked.connect(dialog.close)

    dialog.show()
    try:
        dialog.raise_()
        dialog.activateWindow()
    except Exception:
        pass
    return dialog


if __name__ == "__main__":
    raise SystemExit(main())
