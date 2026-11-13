"""Explicit local physical-piece chooser shared by logger naming controls."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from dataclasses import asdict
from collections.abc import Callable

from PyQt6 import QtCore, QtGui, QtWidgets

from data_logging.sample_identity import PhysicalSample, SampleRegistry, wire_key


class SampleChooser(QtWidgets.QDialog):
    """No allocation until Record new piece is pressed; cancellation is read-only."""

    def __init__(self, registry: SampleRegistry, composition: str, wire: str,
                 parent: QtWidgets.QWidget | None = None, *,
                 accepts_label: Callable[[str], bool] | None = None):
        super().__init__(parent)
        self.registry, self.composition, self.wire = registry, composition, wire
        self.selected_label: str | None = None
        self.selected_record: PhysicalSample | None = None
        self.accepts_label = accepts_label or (lambda _label: True)
        self.setWindowTitle("Physical samples recorded on this computer")
        self.resize(560, 360)
        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel(f"{composition} / {wire}")
        heading.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(heading)
        warning = QtWidgets.QLabel(
            "Local records only, not a complete history. Check labels used on other "
            "computers before recording a new piece. Remeasuring keeps the same ID."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.records = QtWidgets.QListWidget()
        layout.addWidget(self.records)
        self.existing = QtWidgets.QPushButton("Use selected existing piece")
        self.existing.setEnabled(False)
        self.existing.clicked.connect(self._select_existing)
        self.records.currentItemChanged.connect(
            lambda item, _old: self.existing.setEnabled(item is not None and bool(item.flags() & QtCore.Qt.ItemFlag.ItemIsEnabled)))
        layout.addWidget(self.existing)
        row_layout = QtWidgets.QHBoxLayout()
        self.label = QtWidgets.QLineEdit(registry.suggested_label(composition, wire))
        self.label.setAccessibleName("New physical sample ID")
        row_layout.addWidget(self.label)
        self.create_button = QtWidgets.QPushButton("Record new piece locally")
        self.create_button.clicked.connect(self._create)
        row_layout.addWidget(self.create_button)
        layout.addLayout(row_layout)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        exchange = QtWidgets.QHBoxLayout()
        for title, callback in (("Import sample records…", self._import_records),
                                ("Export sample records…", self._export_records)):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(callback)
            exchange.addWidget(button)
        layout.addLayout(exchange)
        self._reload_records()
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def _reload_records(self):
        self.records.clear()
        conflicts = self.registry.conflicts(self.composition, self.wire)
        for row in self.registry.known(self.composition, self.wire):
            item = QtWidgets.QListWidgetItem(f"{row.label}  ({row.source}; {row.uid[:8]})")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, row.label)
            if row.label in conflicts:
                item.setText(f"CONFLICT: {row.label}  ({row.source}; {row.uid[:8]})")
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.records.addItem(item)

    def _import_records(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import explicit sample records", "", "Sample exchange (*.json)")
        if not path:
            return
        try:
            with Path(path).open("r", encoding="utf-8") as stream:
                records = self.registry.parse_snapshot(stream.read(5_000_001))
            answer = QtWidgets.QMessageBox.question(self, "Import physical sample records",
                f"Import {len(records)} records? Conflicting labels will remain separate and unavailable for ambiguous selection. "
                "This does not establish global uniqueness or rename measurements.",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel)
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self.registry.merge(records)
            self._reload_records()
            self.label.setText(self.registry.suggested_label(self.composition, self.wire))
            self.error.setText("Imported records. Review conflicts before selecting a piece.")
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.error.setText(str(exc))

    def _export_records(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export local sample records", "physical-samples.json", "Sample exchange (*.json)")
        if not path:
            return
        try:
            text = self.registry.snapshot()
            # Do not overwrite another machine's exchange file or snapshot.
            with Path(path).open("x", encoding="utf-8") as stream:
                stream.write(text)
            self.error.setText("Exported local records. Transfer and import explicitly on the other computer.")
        except (OSError, sqlite3.Error) as exc:
            self.error.setText(str(exc))

    def _select_existing(self):
        item = self.records.currentItem()
        if item is not None and item.flags() & QtCore.Qt.ItemFlag.ItemIsEnabled:
            label = item.data(QtCore.Qt.ItemDataRole.UserRole)
            try:
                matches = [row for row in self.registry.known(self.composition, self.wire) if row.label == label]
            except (sqlite3.Error, OSError) as exc:
                self.error.setText(str(exc))
                return
            if len(matches) != 1:
                self.error.setText("Sample records changed or conflict. Reopen the chooser after reconciliation.")
                return
            if not self.accepts_label(label):
                self.error.setText("This logger cannot represent this label without changing it.")
                return
            self.selected_label = label
            self.selected_record = matches[0]
            self.accept()

    def _create(self):
        if not self.accepts_label(self.label.text().strip()):
            self.error.setText("This logger cannot represent this label without changing it.")
            return
        try:
            row = self.registry.create(self.composition, self.wire, self.label.text().strip(),
                                       source="Explicit local operator entry")
        except (ValueError, sqlite3.Error, OSError) as exc:
            self.error.setText(str(exc))
            return
        self.selected_label = row.label
        self.selected_record = row
        self.accept()


def selection_metadata(sample, composition: str, wire: str, label: str) -> dict:
    """Return only a deliberately selected identity matching the current fields.

    Typed or restored labels never acquire a guessed UUID. No storage access.
    """
    result = dict(composition=composition.strip(), wire=wire_key(wire), label=label.strip(),
                  status="unregistered", coverage="local_only")
    selected = sample.property("physicalSample") if sample is not None else None
    if isinstance(selected, dict) and isinstance(selected.get("uid"), str) and selected["uid"] and all(selected.get(key) == result[key] for key in ("composition", "wire", "label")):
        result.update(selected, status="explicitly_selected_local", coverage="local_only")
    return result


def attach_sample_chooser(sample, composition: QtWidgets.QLineEdit,
                          wire: QtWidgets.QLineEdit) -> None:
    """Add a small named action without loading storage or altering existing text."""
    sample.setToolTip("Physical piece ID: keep it when remeasuring. Use Samples to see local records; legacy labels are preserved.")
    action = QtGui.QAction("Samples", sample)
    action.setIcon(sample.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView))
    editor = sample.lineEdit() if isinstance(sample, QtWidgets.QSpinBox) else sample
    editor.addAction(action, QtWidgets.QLineEdit.ActionPosition.TrailingPosition)
    action.setToolTip("Choose or record a physical piece (local records only)")
    selecting = False

    def clear_selection(*_args):
        if not selecting:
            sample.setProperty("physicalSample", None)

    # Changes to any component invalidate the deliberate selection permanently,
    # even when the user later returns to the same visible label.
    for field in (editor, composition, wire):
        field.textChanged.connect(clear_selection)
    if editor is not sample:
        sample.textChanged.connect(clear_selection)

    def choose():
        nonlocal selecting
        comp, wire_id = composition.text().strip(), wire.text().strip()
        if not comp or not wire_id:
            QtWidgets.QMessageBox.information(sample, "Physical sample", "Enter composition and source wire first.")
            return
        try:
            # Same workstation location for all logger apps, never a synced project.
            root = Path(QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.GenericDataLocation)) / "PyPlot" / "physical-samples"
            root.mkdir(parents=True, exist_ok=True)
            def accepts(label):
                if not isinstance(sample, QtWidgets.QSpinBox):
                    return True
                parsed = sample._parse(label)
                return parsed is not None and sample.minimum() <= parsed[1] <= sample.maximum()

            dialog = SampleChooser(SampleRegistry(root / "local.sqlite3"), comp, wire_id, sample,
                                   accepts_label=accepts)
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted and dialog.selected_record:
                if composition.text().strip() != comp or wire_key(wire.text()) != wire_key(wire_id):
                    QtWidgets.QMessageBox.warning(sample, "Sample selection changed", "Composition or source wire changed. Select the piece again.")
                    return
                selecting = True
                try:
                    sample.setText(dialog.selected_label)
                    sample.setProperty("physicalSample", asdict(dialog.selected_record))
                    # Notify consumers after metadata is attached, including the
                    # annealing spinbox whose setText intentionally blocks signals.
                    sample.textChanged.emit(sample.text())
                finally:
                    selecting = False
        except (OSError, sqlite3.Error) as exc:
            QtWidgets.QMessageBox.warning(sample, "Sample records unavailable", str(exc))

    action.triggered.connect(choose)
