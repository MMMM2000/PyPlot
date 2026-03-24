"""Qt window for Microwire EDA report generation."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

from PyQt6 import QtCore, QtGui, QtWidgets

from .core import (
    INPUT_KIND_AUTO,
    ROW_SCOPE_ALL,
    ROW_SCOPE_FILTERED,
    ROW_SCOPE_SELECTED,
    MicrowireEdaConfig,
    MicrowireEdaResult,
    generate_report,
)

_WINDOW_REFS: list["MicrowireEdaWindow"] = []


class _EdaWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(str)

    def __init__(self, config: MicrowireEdaConfig) -> None:
        super().__init__()
        self._config = config

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            result = generate_report(self._config, progress_callback=self.progress.emit)
        except Exception as exc:
            message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.failed.emit(message)
            return
        self.finished.emit(result)


class MicrowireEdaWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        config: MicrowireEdaConfig | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial_config = config or MicrowireEdaConfig()
        self._last_result: MicrowireEdaResult | None = None
        self._thread: QtCore.QThread | None = None
        self._worker: _EdaWorker | None = None
        self._progress_dialog: QtWidgets.QProgressDialog | None = None
        self.setWindowTitle("Microwire EDA")
        self.resize(860, 620)
        self._build_ui()
        self._apply_initial_config()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)

        form = QtWidgets.QFormLayout()
        self.input_edit = QtWidgets.QLineEdit(self)
        browse_row = QtWidgets.QHBoxLayout()
        browse_row.addWidget(self.input_edit, 1)
        self.input_browse_button = QtWidgets.QPushButton("Browse…", self)
        self.input_browse_button.clicked.connect(self._choose_input)
        browse_row.addWidget(self.input_browse_button)
        form.addRow("Input project/export", self._wrap_layout(browse_row))

        self.output_edit = QtWidgets.QLineEdit(self)
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        self.output_browse_button = QtWidgets.QPushButton("Browse…", self)
        self.output_browse_button.clicked.connect(self._choose_output_dir)
        output_row.addWidget(self.output_browse_button)
        form.addRow("Output directory", self._wrap_layout(output_row))

        self.title_edit = QtWidgets.QLineEdit(self)
        form.addRow("Report title", self.title_edit)

        self.scope_combo = QtWidgets.QComboBox(self)
        form.addRow("Row scope", self.scope_combo)

        self.pdf_checkbox = QtWidgets.QCheckBox("Write PDF figure bundle", self)
        self.pdf_checkbox.setChecked(True)
        form.addRow("", self.pdf_checkbox)

        layout.addLayout(form)

        self.context_label = QtWidgets.QLabel("", self)
        self.context_label.setWordWrap(True)
        layout.addWidget(self.context_label)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.run_button = QtWidgets.QPushButton("Run analysis", self)
        self.run_button.clicked.connect(self._run_analysis)
        button_row.addWidget(self.run_button)
        self.open_report_button = QtWidgets.QPushButton("Open report", self)
        self.open_report_button.setEnabled(False)
        self.open_report_button.clicked.connect(self._open_report)
        button_row.addWidget(self.open_report_button)
        layout.addLayout(button_row)

        self.summary_label = QtWidgets.QLabel("", self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.log_view = QtWidgets.QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        self.setCentralWidget(central)

    @staticmethod
    def _wrap_layout(layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
        wrapper = QtWidgets.QWidget()
        wrapper.setLayout(layout)
        return wrapper

    def _apply_initial_config(self) -> None:
        config = self._initial_config
        if config.input_path is not None:
            self.input_edit.setText(str(config.input_path))
        default_output = config.output_dir or (Path.cwd() / "microwire_eda_output")
        self.output_edit.setText(str(default_output))
        self.title_edit.setText(config.report_title)
        self.pdf_checkbox.setChecked(bool(config.export_pdf_bundle))

        self.scope_combo.clear()
        scopes: list[tuple[str, str]] = [(ROW_SCOPE_ALL, "All rows")]
        if config.filtered_row_indices:
            scopes.insert(0, (ROW_SCOPE_FILTERED, "Filtered rows"))
        if config.selected_row_indices:
            scopes.insert(0, (ROW_SCOPE_SELECTED, "Selected rows"))
        for value, label in scopes:
            self.scope_combo.addItem(label, value)
        current_index = max(0, self.scope_combo.findData(config.row_scope))
        self.scope_combo.setCurrentIndex(current_index)

        if isinstance(config.source_dataframe, object) and config.source_dataframe is not None:
            self.context_label.setText(
                f"Using {len(config.source_dataframe.index)} row(s) supplied directly from Microwire Data Builder Assemble."
            )
        else:
            self.context_label.setText(
                "The report will use only the data already present in the selected Assemble project/export."
            )

    def _choose_input(self) -> None:
        start = self.input_edit.text().strip() or str(Path.cwd())
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Choose Microwire EDA input",
            start,
            "Microwire Builder Project (*.pydpj);;Spreadsheet (*.xlsx *.xls *.xlsm *.csv);;All files (*)",
        )
        if path_str:
            self.input_edit.setText(path_str)

    def _choose_output_dir(self) -> None:
        start = self.output_edit.text().strip() or str(Path.cwd())
        path_str = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose output directory", start)
        if path_str:
            self.output_edit.setText(path_str)

    def _current_config(self) -> MicrowireEdaConfig:
        return MicrowireEdaConfig(
            input_path=Path(self.input_edit.text().strip()).expanduser()
            if self.input_edit.text().strip()
            else self._initial_config.input_path,
            input_kind=INPUT_KIND_AUTO,
            row_scope=str(self.scope_combo.currentData() or ROW_SCOPE_ALL),
            output_dir=Path(self.output_edit.text().strip()).expanduser(),
            report_title=self.title_edit.text().strip() or "Microwire EDA Report",
            source_dataframe=self._initial_config.source_dataframe,
            filtered_row_indices=self._initial_config.filtered_row_indices,
            selected_row_indices=self._initial_config.selected_row_indices,
            export_png_bundle=True,
            export_pdf_bundle=self.pdf_checkbox.isChecked(),
            metadata=dict(self._initial_config.metadata),
        )

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        bar = self.log_view.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _run_analysis(self) -> None:
        config = self._current_config()
        if config.source_dataframe is None and config.input_path is None:
            QtWidgets.QMessageBox.information(
                self,
                "Microwire EDA",
                "Choose a Builder project or assembled spreadsheet first.",
            )
            return
        self.run_button.setEnabled(False)
        self.open_report_button.setEnabled(False)
        self.summary_label.setText("Running analysis...")
        self._log("Starting Microwire EDA...")
        self._progress_dialog = QtWidgets.QProgressDialog(
            "Preparing analysis...",
            "",
            0,
            0,
            self,
        )
        self._progress_dialog.setWindowTitle("Microwire EDA")
        self._progress_dialog.setCancelButton(None)
        self._progress_dialog.setMinimumDuration(0)
        self._progress_dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self._progress_dialog.show()

        self._thread = QtCore.QThread(self)
        self._worker = _EdaWorker(config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._handle_progress)
        self._worker.finished.connect(self._handle_finished)
        self._worker.failed.connect(self._handle_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _handle_progress(self, message: str) -> None:
        self._log(message)
        if self._progress_dialog is not None:
            self._progress_dialog.setLabelText(message)

    def _handle_finished(self, result: object) -> None:
        self.run_button.setEnabled(True)
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        if not isinstance(result, MicrowireEdaResult):
            self._log(f"Unexpected result type: {type(result)}")
            return
        self._last_result = result
        self.open_report_button.setEnabled(True)
        self.summary_label.setText(
            f"Report ready: {result.report_path}\nRows analysed: {result.row_counts.get('all_rows', 0)}"
        )
        self._log(f"HTML report: {result.report_path}")
        self._log(f"Workbook: {result.workbook_path}")
        self._log(f"Manifest: {result.manifest_path}")
        if result.findings_json_path is not None:
            self._log(f"Findings JSON: {result.findings_json_path}")
        if result.findings_md_path is not None:
            self._log(f"Findings Markdown: {result.findings_md_path}")
        if result.copied_project_path is not None:
            self._log(f"Disposable project copy: {result.copied_project_path}")
        if result.skipped_sections:
            for key, message in result.skipped_sections.items():
                self._log(f"Skipped {key}: {message}")

    def _handle_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        self._log("Microwire EDA failed:")
        self._log(message)
        self.summary_label.setText("Analysis failed. See the log for details.")
        QtWidgets.QMessageBox.critical(self, "Microwire EDA", "Analysis failed. See the log for details.")

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None

    def _open_report(self) -> None:
        if self._last_result is None:
            return
        url = QtCore.QUrl.fromLocalFile(str(self._last_result.report_path))
        QtGui.QDesktopServices.openUrl(url)


def launch_eda_window(
    config: MicrowireEdaConfig | None = None,
    parent: QtWidgets.QWidget | None = None,
) -> MicrowireEdaWindow:
    window = MicrowireEdaWindow(config=config, parent=parent)
    window.show()
    _WINDOW_REFS.append(window)
    return window


def main(argv: list[str] | None = None) -> Any:
    args = sys.argv if argv is None else argv
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication([args[0] if args else "microwire_eda"])
    window = launch_eda_window()
    if owns_app:
        return app.exec()
    return window
