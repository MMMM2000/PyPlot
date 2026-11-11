"""Explicit local physical-piece chooser shared by logger naming controls."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from PyQt6 import QtCore, QtGui, QtWidgets

from data_logging.sample_identity import SampleRegistry


class SampleChooser(QtWidgets.QDialog):
    """No allocation until Record new piece is pressed; cancellation is read-only."""

    def __init__(self, registry: SampleRegistry, composition: str, wire: str,
                 parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.registry, self.composition, self.wire = registry, composition, wire
        self.selected_label: str | None = None
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
        conflicts = registry.conflicts(composition, wire)
        for row in registry.known(composition, wire):
            item = QtWidgets.QListWidgetItem(f"{row.label}  ({row.source})")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, row.label)
            if row.label in conflicts:
                item.setText(f"CONFLICT: {row.label}  ({row.source})")
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.records.addItem(item)
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
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

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
            self.selected_label = label
            self.accept()

    def _create(self):
        try:
            row = self.registry.create(self.composition, self.wire, self.label.text().strip(),
                                       source="Explicit local operator entry")
        except (ValueError, sqlite3.Error, OSError) as exc:
            self.error.setText(str(exc))
            return
        self.selected_label = row.label
        self.accept()


def attach_sample_chooser(sample: QtWidgets.QLineEdit, composition: QtWidgets.QLineEdit,
                          wire: QtWidgets.QLineEdit) -> None:
    """Add a small named action without loading storage or altering existing text."""
    sample.setToolTip("Physical piece ID: keep it when remeasuring. Use Samples to see local records; legacy labels are preserved.")
    action = QtGui.QAction("Samples", sample)
    action.setIcon(sample.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView))
    sample.addAction(action, QtWidgets.QLineEdit.ActionPosition.TrailingPosition)
    action.setToolTip("Choose or record a physical piece (local records only)")

    def choose():
        comp, wire_id = composition.text().strip(), wire.text().strip()
        if not comp or not wire_id:
            QtWidgets.QMessageBox.information(sample, "Physical sample", "Enter composition and source wire first.")
            return
        try:
            # Same workstation location for all logger apps, never a synced project.
            root = Path(QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.GenericDataLocation)) / "PyPlot" / "physical-samples"
            root.mkdir(parents=True, exist_ok=True)
            dialog = SampleChooser(SampleRegistry(root / "local.sqlite3"), comp, wire_id, sample)
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted and dialog.selected_label:
                sample.setText(dialog.selected_label)
        except (OSError, sqlite3.Error) as exc:
            QtWidgets.QMessageBox.warning(sample, "Sample records unavailable", str(exc))

    action.triggered.connect(choose)
