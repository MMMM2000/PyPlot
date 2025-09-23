"""PyQt6 user interface for the microwire database builder."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.utils import ensure_app_theme, install_standard_menu

from .core import (
    LOGGER_NAME,
    BuildResult,
    BuilderConfig,
    build_database,
)


class QtLogHandler(logging.Handler):
    """Logging handler that forwards records to a Qt slot."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        super().__init__()
        self._emit = emit

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - thin wrapper
        message = self.format(record)
        self._emit(message)


class BuildWorker(QtCore.QObject):
    """Background worker that runs the database builder."""

    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)

    def __init__(self, config: BuilderConfig, logger: logging.Logger) -> None:
        super().__init__()
        self.config = config
        self.logger = logger

    @QtCore.pyqtSlot()
    def run(self) -> None:  # pragma: no cover - exercised via integration test
        file_handler: logging.Handler | None = None
        try:
            config = self.config
            config.output_dir.mkdir(parents=True, exist_ok=True)
            log_path = config.output_dir / config.log_file_name
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            self.logger.addHandler(file_handler)

            self.logger.info("Starting build with %s annealing file(s)", len(config.annealing_files))
            result = build_database(
                config,
                logger=self.logger,
                progress_callback=self.progress.emit,
                root_for_relpaths=Path.cwd(),
            )
            self.logger.info("CSV written to %s", result.csv_path)
            if result.excel_path:
                self.logger.info("Excel written to %s", result.excel_path)
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
            self.finished.emit(result)
        except Exception as exc:  # pragma: no cover - safety net
            self.logger.exception("Build failed")
            message = str(exc) if str(exc) else exc.__class__.__name__
            self.error.emit(message)
        finally:
            if file_handler is not None:
                self.logger.removeHandler(file_handler)
                file_handler.close()


class BuilderWindow(QtWidgets.QMainWindow):
    """Main window that orchestrates the microwire database build."""

    log_message = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Microwire Data Builder")
        self.resize(960, 720)

        self.fabrication_paths: list[Path] = []
        self.annealing_paths: list[Path] = []
        self._thread: QtCore.QThread | None = None
        self._worker: BuildWorker | None = None
        self._running = False

        cwd = Path.cwd()
        self._last_fabrication_dir = str(cwd)
        self._last_anneal_dir = str(cwd)
        self._last_output_dir = str(cwd)

        self.log_message.connect(self._append_log)

        self._build_ui()
        self._configure_logging()
        install_standard_menu(self)

    # ------------------------------------------------------------------ setup
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Fabrication inputs
        self.fabrication_group = QtWidgets.QGroupBox("Fabrication spreadsheets (.xlsx)")
        fab_layout = QtWidgets.QVBoxLayout(self.fabrication_group)
        self.fabrication_list = QtWidgets.QListWidget()
        self.fabrication_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        fab_layout.addWidget(self.fabrication_list)

        fab_buttons = QtWidgets.QHBoxLayout()
        fab_add_files = QtWidgets.QPushButton("Add files…")
        fab_add_files.clicked.connect(self._add_fabrication_files)
        fab_buttons.addWidget(fab_add_files)
        fab_add_folder = QtWidgets.QPushButton("Add folder…")
        fab_add_folder.clicked.connect(self._add_fabrication_folder)
        fab_buttons.addWidget(fab_add_folder)
        fab_clear = QtWidgets.QPushButton("Clear")
        fab_clear.clicked.connect(self._clear_fabrication)
        fab_buttons.addWidget(fab_clear)
        fab_buttons.addStretch(1)
        fab_layout.addLayout(fab_buttons)

        self.fabrication_recursive = QtWidgets.QCheckBox("Recursive scan")
        self.fabrication_recursive.setChecked(True)
        fab_layout.addWidget(self.fabrication_recursive)
        layout.addWidget(self.fabrication_group)

        # Annealing inputs
        self.anneal_group = QtWidgets.QGroupBox("Current-annealing files (.txt)")
        anneal_layout = QtWidgets.QVBoxLayout(self.anneal_group)
        self.anneal_list = QtWidgets.QListWidget()
        self.anneal_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        anneal_layout.addWidget(self.anneal_list)

        anneal_buttons = QtWidgets.QHBoxLayout()
        anneal_add_files = QtWidgets.QPushButton("Add files…")
        anneal_add_files.clicked.connect(self._add_anneal_files)
        anneal_buttons.addWidget(anneal_add_files)
        anneal_add_folder = QtWidgets.QPushButton("Add folder…")
        anneal_add_folder.clicked.connect(self._add_anneal_folder)
        anneal_buttons.addWidget(anneal_add_folder)
        anneal_clear = QtWidgets.QPushButton("Clear")
        anneal_clear.clicked.connect(self._clear_anneal)
        anneal_buttons.addWidget(anneal_clear)
        anneal_buttons.addStretch(1)
        anneal_layout.addLayout(anneal_buttons)

        self.anneal_recursive = QtWidgets.QCheckBox("Recursive scan")
        self.anneal_recursive.setChecked(True)
        anneal_layout.addWidget(self.anneal_recursive)
        layout.addWidget(self.anneal_group)

        # Options
        self.options_group = QtWidgets.QGroupBox("Options")
        options_layout = QtWidgets.QVBoxLayout(self.options_group)
        self.make_plots_check = QtWidgets.QCheckBox("Generate plots (PNG)")
        options_layout.addWidget(self.make_plots_check)
        self.export_excel_check = QtWidgets.QCheckBox("Also export Excel")
        options_layout.addWidget(self.export_excel_check)
        layout.addWidget(self.options_group)

        # Output directory
        self.output_group = QtWidgets.QGroupBox("Output")
        output_layout = QtWidgets.QGridLayout(self.output_group)
        output_label = QtWidgets.QLabel("Directory:")
        output_layout.addWidget(output_label, 0, 0)
        default_output = Path.cwd() / "builder_output"
        self.output_edit = QtWidgets.QLineEdit(str(default_output))
        output_layout.addWidget(self.output_edit, 0, 1)
        self.output_button = QtWidgets.QPushButton("Browse…")
        self.output_button.clicked.connect(self._select_output_dir)
        output_layout.addWidget(self.output_button, 0, 2)
        output_layout.setColumnStretch(1, 1)
        layout.addWidget(self.output_group)

        # Progress row
        progress_row = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_bar, stretch=1)
        self.progress_label = QtWidgets.QLabel("Idle")
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        # Log view
        self.log_group = QtWidgets.QGroupBox("Log")
        log_layout = QtWidgets.QVBoxLayout(self.log_group)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_layout.addWidget(self.log_view)
        layout.addWidget(self.log_group, stretch=1)

        # Run button
        run_row = QtWidgets.QHBoxLayout()
        run_row.addStretch(1)
        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.start_build)
        run_row.addWidget(self.run_button)
        layout.addLayout(run_row)

    def _configure_logging(self) -> None:
        self.logger = logging.getLogger(LOGGER_NAME)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self._log_handler = QtLogHandler(self.log_message.emit)
        self._log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        if self._log_handler not in self.logger.handlers:
            self.logger.addHandler(self._log_handler)

    # ------------------------------------------------------------------ helpers
    def _extend_paths(self, attr: str, paths: Iterable[Path]) -> None:
        current: list[Path] = getattr(self, attr)
        combined = list(dict.fromkeys(current + [Path(p) for p in paths]))
        setattr(self, attr, combined)

    def _update_list_widget(self, widget: QtWidgets.QListWidget, items: Iterable[Path]) -> None:
        widget.clear()
        for text in sorted({str(Path(p)) for p in items}):
            widget.addItem(text)

    def _add_fabrication_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select fabrication spreadsheets",
            self._last_fabrication_dir,
            "Excel files (*.xlsx)",
        )
        if not files:
            return
        self._last_fabrication_dir = str(Path(files[0]).parent)
        self._extend_paths("fabrication_paths", (Path(f) for f in files))
        self._update_list_widget(self.fabrication_list, self.fabrication_paths)

    def _add_fabrication_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select folder with fabrication spreadsheets",
            self._last_fabrication_dir,
        )
        if not folder:
            return
        root = Path(folder)
        iterator = root.rglob("*.xlsx") if self.fabrication_recursive.isChecked() else root.glob("*.xlsx")
        files = [p for p in iterator if p.is_file()]
        if not files:
            QtWidgets.QMessageBox.information(self, "Microwire Data Builder", "No Excel files were found in that folder.")
            return
        self._last_fabrication_dir = folder
        self._extend_paths("fabrication_paths", files)
        self._update_list_widget(self.fabrication_list, self.fabrication_paths)

    def _clear_fabrication(self) -> None:
        self.fabrication_paths = []
        self.fabrication_list.clear()

    def _add_anneal_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select current-annealing files",
            self._last_anneal_dir,
            "Text files (*.txt)",
        )
        if not files:
            return
        self._last_anneal_dir = str(Path(files[0]).parent)
        self._extend_paths("annealing_paths", (Path(f) for f in files))
        self._update_list_widget(self.anneal_list, self.annealing_paths)

    def _add_anneal_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select folder with current-annealing files",
            self._last_anneal_dir,
        )
        if not folder:
            return
        root = Path(folder)
        iterator = root.rglob("*.txt") if self.anneal_recursive.isChecked() else root.glob("*.txt")
        files = [p for p in iterator if p.is_file()]
        if not files:
            QtWidgets.QMessageBox.information(self, "Microwire Data Builder", "No text files were found in that folder.")
            return
        self._last_anneal_dir = folder
        self._extend_paths("annealing_paths", files)
        self._update_list_widget(self.anneal_list, self.annealing_paths)

    def _clear_anneal(self) -> None:
        self.annealing_paths = []
        self.anneal_list.clear()

    def _select_output_dir(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select output directory",
            self.output_edit.text() or self._last_output_dir,
        )
        if directory:
            self._last_output_dir = directory
            self.output_edit.setText(directory)

    def _set_running(self, running: bool) -> None:
        self._running = running
        for widget in (
            self.fabrication_group,
            self.anneal_group,
            self.options_group,
            self.output_group,
        ):
            widget.setEnabled(not running)
        self.run_button.setEnabled(not running)
        if running:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Running…")
        else:
            if self.progress_label.text() not in {"Complete", "Failed"}:
                self.progress_label.setText("Idle")

    # ------------------------------------------------------------------ build orchestration
    def start_build(self) -> None:
        if self._running:
            return
        if not self.annealing_paths:
            QtWidgets.QMessageBox.warning(self, "Microwire Data Builder", "Please add at least one annealing file.")
            return
        output_dir_text = self.output_edit.text().strip()
        if not output_dir_text:
            QtWidgets.QMessageBox.warning(self, "Microwire Data Builder", "Please choose an output directory.")
            return
        output_dir = Path(output_dir_text).expanduser()
        config = BuilderConfig(
            fabrication_files=list(dict.fromkeys(self.fabrication_paths)),
            annealing_files=list(dict.fromkeys(self.annealing_paths)),
            output_dir=output_dir,
            make_plots=self.make_plots_check.isChecked(),
            export_excel=self.export_excel_check.isChecked(),
        )
        self._set_running(True)
        self.log_view.clear()
        self.logger.info(
            "Queued build for %s annealing measurement(s)",
            len(config.annealing_files),
        )
        self._start_worker(config)

    def _start_worker(self, config: BuilderConfig) -> None:
        self._thread = QtCore.QThread(self)
        self._worker = BuildWorker(config, self.logger)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._update_progress)
        self._worker.finished.connect(self._handle_finished)
        self._worker.error.connect(self._handle_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _update_progress(self, current: int, total: int) -> None:
        total = max(total, 1)
        percent = int(round(100 * current / total))
        self.progress_bar.setValue(max(0, min(100, percent)))
        self.progress_label.setText(f"{current}/{total}")

    def _handle_finished(self, result: BuildResult) -> None:
        self._set_running(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Complete")
        QtWidgets.QMessageBox.information(
            self,
            "Microwire Data Builder",
            "Build finished successfully.\n\n"
            f"CSV file: {result.csv_path}",
        )

    def _handle_failed(self, message: str) -> None:
        self._set_running(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Failed")
        QtWidgets.QMessageBox.critical(
            self,
            "Microwire Data Builder",
            "Build failed.\n\n" + message,
        )

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    # ------------------------------------------------------------------ Qt hooks
    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self._running:
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                "A build is currently running. Please wait for it to finish before closing.",
            )
            event.ignore()
            return
        if hasattr(self, "_log_handler") and self._log_handler in self.logger.handlers:
            self.logger.removeHandler(self._log_handler)
        super().closeEvent(event)


def run_app() -> None:
    main()


def main() -> QtWidgets.QWidget | None:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        ensure_app_theme(app)
        owns_app = True
    window = BuilderWindow()
    window.show()
    if owns_app:
        app.exec()
    return window


__all__ = ["BuilderWindow", "main", "run_app"]
