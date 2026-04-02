from __future__ import annotations

import json
import logging
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd
from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.shared.utils import ensure_app_theme, install_standard_menu

from .core import (
    LOGGER_NAME,
    FabricationIndex,
    VideoMetricsSummary,
    _collect_video_sources,
    build_fabrication_index,
)
from .ui import (
    CORE_TEMPERATURE_COLUMN,
    ESTIMATED_TRANSITION_COLUMN,
    GLASS_PULL_COLUMN,
    GLASS_TEMPERATURE_COLUMN,
    MICROSCOPE_CAP_D_COLUMN,
    MICROSCOPE_D_COLUMN,
    VIDEO_END_LENGTH_COLUMN,
    VIDEO_EXTENSIONS,
    VIDEO_MW_LENGTH_COLUMN,
    MiniDatabaseData,
    QtLogHandler,
    SectionProcessResult,
    VideoSection,
    _builder_settings,
    _dialog_start_directory,
    _fabrication_frame_columns,
    _fabrication_index_to_frame,
    _json_safe,
    _microwire_key_to_str,
    _microwire_label,
    _microwire_parts_from_label_safe,
    _video_index_to_frame,
)


EXCEL_EXTENSIONS = (".xlsx", ".xls", ".xlsm")
ALL_SUPPORTED_EXTENSIONS = EXCEL_EXTENSIONS + VIDEO_EXTENSIONS

MISSING_VIDEO_BACKGROUND = "#3a0a0a"
MISSING_VIDEO_FOREGROUND = "#ffd6d6"
REVIEW_COMPLETE_BACKGROUND = "#0f3b26"
REVIEW_COMPLETE_FOREGROUND = "#4ade80"
REVIEW_OVERWRITE_BACKGROUND = "#4a3806"
REVIEW_OVERWRITE_FOREGROUND = "#facc15"


def _serialize_frame(frame: pd.DataFrame) -> Dict[str, Any]:
    columns = [str(column) for column in frame.columns]
    rows: List[Dict[str, Any]] = []
    if not frame.empty:
        for record in frame.to_dict(orient="records"):
            rows.append({column: _json_safe(record.get(column)) for column in columns})
    index_payload = [_json_safe(value) for value in frame.index.tolist()]
    return {
        "columns": columns,
        "rows": rows,
        "index": index_payload,
    }


def _deserialize_frame(payload: Any, *, default_columns: Optional[Sequence[str]] = None) -> pd.DataFrame:
    if not isinstance(payload, Mapping):
        return pd.DataFrame(columns=list(default_columns or ()))
    columns_payload = payload.get("columns")
    columns = [str(column) for column in columns_payload] if isinstance(columns_payload, (list, tuple)) else list(default_columns or ())
    rows_payload = payload.get("rows")
    frame = pd.DataFrame(list(rows_payload), columns=columns or None) if isinstance(rows_payload, (list, tuple)) else pd.DataFrame(columns=columns)
    index_payload = payload.get("index")
    if isinstance(index_payload, list) and len(index_payload) == len(frame.index):
        try:
            frame.index = pd.Index(index_payload)
        except Exception:
            pass
    return frame


def scan_universal_video_inputs(roots: Sequence[Path]) -> List[Path]:
    candidates: Dict[str, Path] = {}
    for root in roots:
        candidate_root = Path(root).expanduser()
        if not candidate_root.exists():
            continue
        try:
            iterator: Iterable[Path] = candidate_root.rglob("*")
        except Exception:
            continue
        for path in iterator:
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in ALL_SUPPORTED_EXTENSIONS:
                continue
            try:
                resolved = str(path.resolve())
            except Exception:
                resolved = str(path)
            candidates.setdefault(resolved, path)
    return sorted(candidates.values())


def _split_candidate_paths(paths: Sequence[Path]) -> Tuple[List[Path], List[Path]]:
    fabrication_files: List[Path] = []
    video_files: List[Path] = []
    for path in dict.fromkeys(Path(p) for p in paths):
        suffix = path.suffix.lower()
        if suffix in EXCEL_EXTENSIONS:
            fabrication_files.append(path)
        elif suffix in VIDEO_EXTENSIONS:
            video_files.append(path)
    return fabrication_files, video_files


class MultiSelectMenuButton(QtWidgets.QWidget):
    selection_changed = QtCore.pyqtSignal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._actions: List[QtGui.QAction] = []
        self._placeholder = "Select draws"
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.button = QtWidgets.QToolButton(self)
        self.button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.button.setText(self._placeholder)
        self.button.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.menu = QtWidgets.QMenu(self.button)
        self.menu.setSeparatorsCollapsible(False)
        self.menu.aboutToHide.connect(self._update_button_text)
        self.button.setMenu(self.menu)
        layout.addWidget(self.button)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

    def set_placeholder_text(self, text: str) -> None:
        self._placeholder = str(text)
        self._update_button_text()

    def clear(self) -> None:
        self.set_options(())

    def set_options(self, options: Sequence[Tuple[str, Any]]) -> None:
        self.menu.clear()
        self._actions = []
        for label, value in options:
            action = self.menu.addAction(str(label))
            action.setCheckable(True)
            action.setData(value)
            action.toggled.connect(self.selection_changed.emit)
            self._actions.append(action)
        self.setEnabled(bool(self._actions))
        self._update_button_text()

    def selected_values(self) -> List[Any]:
        values: List[Any] = []
        for action in self._actions:
            if action.isChecked():
                values.append(action.data())
        return values

    def set_selected_values(self, values: Sequence[Any]) -> None:
        expected = {str(value) for value in values}
        for action in self._actions:
            action.setChecked(str(action.data()) in expected)
        self._update_button_text()

    def _update_button_text(self) -> None:
        checked = [action.text() for action in self._actions if action.isChecked()]
        if not checked:
            self.button.setText(self._placeholder)
            return
        if len(checked) <= 2:
            self.button.setText(", ".join(checked))
            return
        self.button.setText(f"{len(checked)} draws selected")


class UniversalVideoSection(VideoSection):
    section_key = "universal_video"
    section_title = "Universal video builder"
    supported_suffixes = ALL_SUPPORTED_EXTENSIONS

    def __init__(
        self,
        logger: logging.Logger,
        log_callback: Callable[[int, str], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        self._available_fabrication_frame = pd.DataFrame(columns=_fabrication_frame_columns())
        self._available_table = pd.DataFrame()
        super().__init__(logger, log_callback, parent)
        self.source_button.show()
        self.source_button.setText("Connect fabrication folder…")
        self.source_button.setToolTip("Select a fabrication root folder containing spreadsheets and videos.")
        self.refresh_button.setText("Refresh fabrication data")
        self._insert_add_microwire_controls()
        self._build_visual_shell()
        self._apply_layout_polish()
        self._refresh_available_frames_from_state()
        self._refresh_selector_catalog()
        self._refresh_dashboard()

    def _insert_add_microwire_controls(self) -> None:
        container = QtWidgets.QWidget(self)
        container.setObjectName("universalVideoSelectorRow")
        grid = QtWidgets.QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        composition_label = QtWidgets.QLabel("Composition")
        composition_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom
        )
        grid.addWidget(composition_label, 0, 0)
        self.composition_combo = QtWidgets.QComboBox(self)
        self.composition_combo.setEditable(True)
        self.composition_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.composition_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.composition_combo.setMinimumWidth(240)
        self.composition_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.composition_combo.setMinimumHeight(34)
        line_edit = self.composition_combo.lineEdit()
        if line_edit is not None:
            line_edit.textEdited.connect(self._handle_composition_changed)
            line_edit.setPlaceholderText("Start typing a composition")
        self.composition_combo.currentTextChanged.connect(self._handle_composition_changed)
        grid.addWidget(self.composition_combo, 1, 0)

        draws_label = QtWidgets.QLabel("Draws")
        draws_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom
        )
        grid.addWidget(draws_label, 0, 1)
        self.draw_menu = MultiSelectMenuButton(self)
        self.draw_menu.set_placeholder_text("Select draw(s)")
        self.draw_menu.selection_changed.connect(self._refresh_piece_options)
        self.draw_menu.setMinimumWidth(210)
        self.draw_menu.button.setMinimumHeight(34)
        grid.addWidget(self.draw_menu, 1, 1)

        piece_label = QtWidgets.QLabel("Piece")
        piece_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom
        )
        grid.addWidget(piece_label, 0, 2)
        self.piece_combo = QtWidgets.QComboBox(self)
        self.piece_combo.setMinimumWidth(140)
        self.piece_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.piece_combo.setMinimumHeight(34)
        self.piece_combo.addItem("All pieces", None)
        grid.addWidget(self.piece_combo, 1, 2)

        self.add_rows_button = QtWidgets.QPushButton("Add/select microwire(s)", self)
        self.add_rows_button.clicked.connect(self._add_selected_microwires)
        self.add_rows_button.setMinimumWidth(180)
        self.add_rows_button.setMinimumHeight(34)
        self.add_rows_button.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        grid.addWidget(self.add_rows_button, 0, 3, 2, 1, QtCore.Qt.AlignmentFlag.AlignBottom)

        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 0)
        self.main_layout.insertWidget(1, container)

    def _build_visual_shell(self) -> None:
        guidance = QtWidgets.QLabel(
            "State colors: red = missing required data or no video, green = first fill, dark amber = overwritten value.",
            self,
        )
        guidance.setWordWrap(True)
        self.main_layout.insertWidget(2, guidance)
        self.guidance_label = guidance

        if isinstance(self.search_edit, QtWidgets.QLineEdit):
            self.search_edit.setPlaceholderText("Filter the loaded rows by composition, draw, piece, notes, or any visible column")
        self.status_label.setWordWrap(True)
        if isinstance(self.table_view, QtWidgets.QTableView):
            self.table_view.verticalHeader().setDefaultSectionSize(28)
            self.table_view.setShowGrid(False)

    def _apply_layout_polish(self) -> None:
        self.main_layout.setContentsMargins(14, 14, 14, 12)
        self.main_layout.setSpacing(12)
        if isinstance(self.controls_layout, QtWidgets.QHBoxLayout):
            self.controls_layout.setSpacing(8)
        for button in (self.source_button, self.open_sources_button, self.refresh_button, self.stop_button):
            if isinstance(button, QtWidgets.QPushButton):
                button.setMinimumHeight(34)
        if isinstance(self.status_label, QtWidgets.QLabel):
            self.status_label.setContentsMargins(2, 2, 2, 0)
        guidance = getattr(self, "guidance_label", None)
        if isinstance(guidance, QtWidgets.QLabel):
            guidance.setContentsMargins(2, 2, 2, 2)
        if isinstance(self.search_edit, QtWidgets.QLineEdit):
            self.search_edit.setMinimumHeight(34)
        if isinstance(self.search_clear_button, QtWidgets.QPushButton):
            self.search_clear_button.setMinimumHeight(34)
        self._wrap_header_rows()

    def _wrap_header_rows(self) -> None:
        layout = self.main_layout
        if not isinstance(layout, QtWidgets.QVBoxLayout):
            return
        if getattr(self, "_header_frame", None) is not None:
            return
        if layout.count() < 2:
            return

        panel_item = layout.takeAt(layout.count() - 1)
        panel_widget = panel_item.widget() if panel_item is not None else None
        panel_layout = panel_item.layout() if panel_item is not None else None

        header_items: List[QtWidgets.QLayoutItem] = []
        while layout.count():
            item = layout.takeAt(0)
            if item is not None:
                header_items.append(item)

        header_frame = QtWidgets.QFrame(self)
        header_frame.setObjectName("universalVideoHeader")
        header_layout = QtWidgets.QVBoxLayout(header_frame)
        header_layout.setContentsMargins(14, 14, 14, 12)
        header_layout.setSpacing(10)

        for item in header_items:
            widget = item.widget()
            nested_layout = item.layout()
            spacer = item.spacerItem()
            if widget is not None:
                header_layout.addWidget(widget)
            elif nested_layout is not None:
                header_layout.addLayout(nested_layout)
            elif spacer is not None:
                header_layout.addItem(spacer)

        header_frame.setStyleSheet(
            """
            QFrame#universalVideoHeader {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }
            QFrame#universalVideoHeader QLabel {
                padding-left: 0px;
            }
            QFrame#universalVideoHeader QLabel#universalVideoGuidance {
                color: rgba(255, 255, 255, 0.72);
            }
            QFrame#universalVideoHeader QLabel#universalVideoStatus {
                color: rgba(255, 255, 255, 0.82);
            }
            QWidget#universalVideoSelectorRow QLabel {
                color: rgba(255, 255, 255, 0.74);
            }
            """
        )

        guidance = getattr(self, "guidance_label", None)
        if isinstance(guidance, QtWidgets.QLabel):
            guidance.setObjectName("universalVideoGuidance")
        if isinstance(self.status_label, QtWidgets.QLabel):
            self.status_label.setObjectName("universalVideoStatus")

        layout.addWidget(header_frame, 0)
        if panel_widget is not None:
            layout.addWidget(panel_widget, 1)
        elif panel_layout is not None:
            layout.addLayout(panel_layout, 1)
        self._header_frame = header_frame

    def _refresh_dashboard(self) -> None:
        return

    def _current_column_order(self) -> List[str]:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return []
        header = self.table_view.horizontalHeader()
        frame = self.model.frame()
        order: List[str] = []
        if header is None or not isinstance(frame, pd.DataFrame):
            return order
        for visual_index in range(header.count()):
            logical = header.logicalIndex(visual_index)
            try:
                order.append(str(frame.columns[logical]))
            except Exception:
                continue
        return order

    def _apply_column_order(self, order: Sequence[str]) -> None:
        if not order or not isinstance(self.table_view, QtWidgets.QTableView):
            return
        header = self.table_view.horizontalHeader()
        frame = self.model.frame()
        if header is None or not isinstance(frame, pd.DataFrame):
            return
        mapping = {str(column): idx for idx, column in enumerate(frame.columns)}
        for target_visual, column_name in enumerate(order):
            logical = mapping.get(column_name)
            if logical is None:
                continue
            current_visual = header.visualIndex(logical)
            if current_visual != target_visual:
                header.moveSection(current_visual, target_visual)

    def export_project_payload(self) -> Dict[str, Any]:
        payload = super().export_project_payload()
        payload["available_fabrication"] = _serialize_frame(self._available_fabrication_frame)
        payload["available_table"] = _serialize_frame(self._available_table)
        payload["search_text"] = (
            self.search_edit.text()
            if isinstance(self.search_edit, QtWidgets.QLineEdit)
            else ""
        )
        payload["column_order"] = self._current_column_order()
        return payload

    def import_project_payload(self, payload: Mapping[str, Any]) -> None:  # type: ignore[override]
        self._available_fabrication_frame = _deserialize_frame(
            payload.get("available_fabrication"),
            default_columns=_fabrication_frame_columns(),
        )
        self._available_table = _deserialize_frame(payload.get("available_table"))
        super().import_project_payload(payload)
        search_text = str(payload.get("search_text") or "")
        if isinstance(self.search_edit, QtWidgets.QLineEdit):
            self.search_edit.setText(search_text)
        column_order = payload.get("column_order")
        if isinstance(column_order, (list, tuple)):
            self._apply_column_order([str(value) for value in column_order])
        self._refresh_selector_catalog()
        self._refresh_dashboard()

    def reset_to_blank(self) -> None:  # type: ignore[override]
        self._available_fabrication_frame = pd.DataFrame(columns=_fabrication_frame_columns())
        self._available_table = pd.DataFrame()
        super().reset_to_blank()
        self._refresh_selector_catalog()
        self._refresh_dashboard()

    def _refresh_available_frames_from_state(self) -> None:
        if self._available_fabrication_frame.empty:
            self._available_fabrication_frame = _deserialize_frame(
                self.data.extra.get("available_fabrication"),
                default_columns=_fabrication_frame_columns(),
            )
        if self._available_table.empty:
            self._available_table = _deserialize_frame(
                self.data.extra.get("available_table")
            )

    def _fabrication_table(self) -> Optional[pd.DataFrame]:
        self._refresh_available_frames_from_state()
        if isinstance(self._available_fabrication_frame, pd.DataFrame) and not self._available_fabrication_frame.empty:
            return self._available_fabrication_frame.copy()
        return None

    def _build_cumulative_lengths(
        self,
        base_frame: pd.DataFrame,
        fabrication_frame: Optional[pd.DataFrame],
    ) -> Dict[Tuple[str, int, int], Optional[float]]:
        def _rows(frame: Optional[pd.DataFrame]) -> Iterable[pd.Series]:
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                return
            for row_idx in range(len(frame.index)):
                try:
                    yield frame.iloc[row_idx]
                except Exception:
                    continue

        lengths: Dict[Tuple[str, int, int], Optional[float]] = {}
        preferred_frame = fabrication_frame
        if not isinstance(preferred_frame, pd.DataFrame) or preferred_frame.empty:
            preferred_frame = self._fabrication_table()

        for row in _rows(preferred_frame):
            composition = str(row.get("Composition") or "").strip()
            if not composition or composition == "Imported data:":
                continue
            try:
                draw = int(row.get("Draw"))
                piece = int(row.get("Piece"))
            except (TypeError, ValueError):
                continue
            lengths[(composition, draw, piece)] = self._coerce_float(row.get("Length (m)"))

        for row in _rows(base_frame):
            composition = str(row.get("Composition") or "").strip()
            if not composition or composition == "Imported data:":
                continue
            try:
                draw = int(row.get("Draw"))
                piece = int(row.get("Piece"))
            except (TypeError, ValueError):
                continue
            lengths[(composition, draw, piece)] = self._coerce_float(row.get("Length (m)"))

        cumulative_map: Dict[Tuple[str, int, int], Optional[float]] = {}
        grouped: Dict[Tuple[str, int], List[Tuple[int, Optional[float]]]] = {}
        for (composition, draw, piece), length_val in lengths.items():
            grouped.setdefault((composition, draw), []).append((piece, length_val))
        for (composition, draw), entries in grouped.items():
            running: Optional[float] = 0.0
            for piece, length_val in sorted(entries, key=lambda item: item[0]):
                if running is None or length_val is None:
                    running = None
                    cumulative_map[(composition, draw, piece)] = None
                else:
                    running += length_val
                    cumulative_map[(composition, draw, piece)] = running
        return cumulative_map

    def process(
        self,
        paths: List[Path],
        progress: Optional[Callable[[int, int, Optional[str]], None]] = None,
    ) -> SectionProcessResult:
        unique_paths = list(dict.fromkeys(Path(path) for path in paths))
        fabrication_files, video_files = _split_candidate_paths(unique_paths)
        total = len(fabrication_files) + len(video_files)
        progress_done = 0

        def _report(message: str) -> None:
            if progress is None:
                return
            try:
                progress(progress_done, total, message)
            except Exception:
                pass

        def _fabrication_progress(current: int, total_local: int) -> None:
            nonlocal progress_done
            progress_done = min(current, len(fabrication_files))
            if progress is None:
                return
            message = None
            if 0 < current <= len(fabrication_files):
                message = f"Parsing {fabrication_files[current - 1].name}"
            try:
                progress(progress_done, total, message)
            except Exception:
                pass

        def _video_progress(current: int, total_local: int) -> None:
            nonlocal progress_done
            progress_done = len(fabrication_files) + min(current, len(video_files))
            if progress is None:
                return
            message = None
            if 0 < current <= len(video_files):
                message = f"Linking {video_files[current - 1].name}"
            try:
                progress(progress_done, total, message)
            except Exception:
                pass

        if total == 0:
            return SectionProcessResult(
                table=pd.DataFrame(),
                processed={},
                payloads={"video_index": {}},
                extra={
                    "available_fabrication": _serialize_frame(pd.DataFrame(columns=_fabrication_frame_columns())),
                    "available_table": _serialize_frame(pd.DataFrame()),
                },
            )

        raw_index = build_fabrication_index(
            fabrication_files,
            self.logger,
            progress_callback=_fabrication_progress if fabrication_files else None,
            cancel_callback=self.is_cancelled,
        )
        fabrication_frame = _fabrication_index_to_frame(raw_index)
        if not fabrication_files:
            _report("No fabrication spreadsheets found.")

        video_index = _collect_video_sources(
            video_files,
            self.logger,
            progress_callback=_video_progress if video_files else None,
        )
        if not video_files and progress is not None:
            progress(total, total, "No videos found.")

        table = _video_index_to_frame(video_index, fabrication_frame)
        processed: Dict[str, float] = {}
        for path in unique_paths:
            try:
                processed[str(path)] = float(path.stat().st_mtime)
            except OSError:
                continue
        return SectionProcessResult(
            table=table,
            processed=processed,
            payloads={"video_index": video_index},
            extra={
                "available_fabrication": _serialize_frame(fabrication_frame),
                "available_table": _serialize_frame(table),
            },
        )

    def _handle_worker_finished(self, result: SectionProcessResult) -> None:
        self._finish_progress()
        existing_payloads = set(self.data.extra.get("payloads", {}).keys())
        new_payloads = set(result.payloads.keys())
        for name in existing_payloads - new_payloads:
            self.store.clear_payload(name)

        payload_map: Dict[str, str] = {}
        for name, payload in result.payloads.items():
            self.store.save_payload(name, payload)
            payload_map[name] = name
        if payload_map:
            self.data.extra["payloads"] = payload_map
        if result.extra:
            self.data.extra.update(result.extra)
        self.data.processed = result.processed

        previous_frame = self.model.frame()
        previous_keys: List[str] = []
        if isinstance(previous_frame, pd.DataFrame) and not previous_frame.empty:
            for _, row in previous_frame.iterrows():
                key = str(row.get("_group_key") or "").strip()
                if key:
                    previous_keys.append(key)

        available_table = _deserialize_frame(
            result.extra.get("available_table") if isinstance(result.extra, Mapping) else None
        )
        visible_table = self._table_for_group_keys(previous_keys, available_table)
        visible_table = self._apply_overrides_to_table(self._ensure_core_columns(visible_table))
        self.data.table = visible_table
        self.store.save(self.data)
        self._close_active_editor()
        self.model.set_frame(visible_table)
        self._hide_columns(self._HIDDEN_VIDEO_COLUMNS)
        if not visible_table.empty:
            self._auto_fit_columns()
        self._update_status()
        processed_count = len(self._active_candidates)
        self.log(f"{self.section_title}: processed {processed_count} file(s).")
        self._active_candidates = []
        self._update_open_sources_enabled()
        try:
            self.sources_changed.emit(list(self.data.sources))
        except Exception:
            pass
        try:
            self.data_updated.emit()
        except Exception:
            pass
        self._refresh_available_frames_from_state()
        self._refresh_selector_catalog()
        self._refresh_dashboard()

    def _table_for_group_keys(
        self,
        group_keys: Sequence[str],
        available: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        frame = available.copy() if isinstance(available, pd.DataFrame) else self._available_rows_frame()
        if frame.empty:
            return pd.DataFrame(columns=list(frame.columns))
        wanted = {str(key).strip() for key in group_keys if str(key).strip()}
        if not wanted:
            return frame.iloc[0:0].copy()
        rows = frame[frame["_group_key"].astype(str).isin(wanted)].copy()
        return rows.reset_index(drop=True)

    def _update_status(self) -> None:  # type: ignore[override]
        super()._update_status()
        self._refresh_dashboard()

    def _background_brush_for_cell(self, row: pd.Series, column: str) -> Optional[QtGui.QBrush]:  # type: ignore[override]
        if self._row_missing_video_files(row):
            return QtGui.QBrush(QtGui.QColor(MISSING_VIDEO_BACKGROUND))
        if self._cell_is_overwritten(row, column):
            return QtGui.QBrush(QtGui.QColor(REVIEW_OVERWRITE_BACKGROUND))
        state = self._completion_state(row, column)
        if state is None:
            return None
        return QtGui.QBrush(
            QtGui.QColor(REVIEW_COMPLETE_BACKGROUND if state else MISSING_VIDEO_BACKGROUND)
        )

    def _foreground_brush_for_cell(self, row: pd.Series, column: str) -> Optional[QtGui.QBrush]:  # type: ignore[override]
        if self._row_missing_video_files(row):
            return QtGui.QBrush(QtGui.QColor(MISSING_VIDEO_FOREGROUND))
        if self._cell_is_overwritten(row, column):
            return QtGui.QBrush(QtGui.QColor(REVIEW_OVERWRITE_FOREGROUND))
        state = self._completion_state(row, column)
        if state is None:
            return None
        return QtGui.QBrush(
            QtGui.QColor(REVIEW_COMPLETE_FOREGROUND if state else MISSING_VIDEO_FOREGROUND)
        )

    def _ensure_review_dialog(self):  # type: ignore[override]
        dialog = super()._ensure_review_dialog()
        dialog.resize(1700, 240)
        return dialog

    def _available_rows_frame(self) -> pd.DataFrame:
        self._refresh_available_frames_from_state()
        if isinstance(self._available_table, pd.DataFrame) and not self._available_table.empty:
            return self._available_table.copy()
        frame = self.model.frame()
        if isinstance(frame, pd.DataFrame):
            return frame.copy()
        return pd.DataFrame()

    def _refresh_selector_catalog(self) -> None:
        available = self._available_rows_frame()
        catalog: Dict[str, Dict[int, List[int]]] = {}
        for _, row in available.iterrows():
            composition = str(row.get("Composition") or "").strip()
            if not composition or composition == "Imported data:":
                continue
            try:
                draw = int(row.get("Draw"))
                piece = int(row.get("Piece"))
            except (TypeError, ValueError):
                continue
            catalog.setdefault(composition, {}).setdefault(draw, [])
            if piece not in catalog[composition][draw]:
                catalog[composition][draw].append(piece)

        current_text = self.composition_combo.currentText().strip()
        compositions = sorted(catalog.keys(), key=str.lower)
        was_blocked = self.composition_combo.blockSignals(True)
        self.composition_combo.clear()
        for composition in compositions:
            self.composition_combo.addItem(composition)
        self.composition_combo.setEditText(current_text)
        self.composition_combo.blockSignals(was_blocked)
        completer = self.composition_combo.completer()
        if completer is not None:
            completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
            completer.setModel(QtCore.QStringListModel(compositions, completer))
        self._selector_catalog = catalog
        self._refresh_draw_options()

    def _handle_composition_changed(self, _: str) -> None:
        self._refresh_draw_options()

    def _refresh_draw_options(self) -> None:
        composition = self.composition_combo.currentText().strip()
        draws = sorted(self._selector_catalog.get(composition, {}).keys())
        self.draw_menu.set_options([(str(draw), draw) for draw in draws])
        self._refresh_piece_options()

    def _refresh_piece_options(self) -> None:
        composition = self.composition_combo.currentText().strip()
        selected_draws = [int(value) for value in self.draw_menu.selected_values()]
        pieces: Set[int] = set()
        for draw in selected_draws:
            pieces.update(self._selector_catalog.get(composition, {}).get(draw, []))
        current_piece = self.piece_combo.currentData()
        self.piece_combo.blockSignals(True)
        self.piece_combo.clear()
        self.piece_combo.addItem("All pieces", None)
        for piece in sorted(pieces):
            self.piece_combo.addItem(str(piece), piece)
        if current_piece is not None:
            index = self.piece_combo.findData(current_piece)
            if index >= 0:
                self.piece_combo.setCurrentIndex(index)
        self.piece_combo.blockSignals(False)

    def _existing_group_keys(self) -> Set[str]:
        frame = self.model.frame()
        existing: Set[str] = set()
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return existing
        for _, row in frame.iterrows():
            key = str(row.get("_group_key") or "").strip()
            if key:
                existing.add(key)
        return existing

    def _select_group_keys(self, group_keys: Sequence[str]) -> None:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return
        selection = self.table_view.selectionModel()
        if selection is None:
            return
        selection.clearSelection()
        frame = self.model.frame()
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return
        wanted = {str(key) for key in group_keys if str(key).strip()}
        if not wanted:
            return
        for source_row in range(len(frame.index)):
            try:
                row = frame.iloc[source_row]
            except Exception:
                continue
            key = str(row.get("_group_key") or "").strip()
            if key not in wanted:
                continue
            source_index = self.model.index(source_row, 0)
            if not source_index.isValid():
                continue
            proxy_index = self._search_proxy.mapFromSource(source_index)
            if not proxy_index.isValid():
                continue
            selection.select(
                proxy_index,
                QtCore.QItemSelectionModel.SelectionFlag.Select
                | QtCore.QItemSelectionModel.SelectionFlag.Rows,
            )
            self.table_view.scrollTo(
                proxy_index,
                QtWidgets.QAbstractItemView.ScrollHint.EnsureVisible,
            )

    def _add_selected_microwires(self) -> None:
        composition = self.composition_combo.currentText().strip()
        if not composition:
            QtWidgets.QMessageBox.warning(self, self.section_title, "Select a composition first.")
            return
        selected_draws = [int(value) for value in self.draw_menu.selected_values()]
        if not selected_draws:
            QtWidgets.QMessageBox.warning(self, self.section_title, "Select at least one draw.")
            return
        target_piece = self.piece_combo.currentData()
        available = self._available_rows_frame()
        if available.empty:
            QtWidgets.QMessageBox.information(
                self,
                self.section_title,
                "No fabrication rows are available yet. Connect a folder and refresh first.",
            )
            return
        matches: List[Dict[str, Any]] = []
        selected_keys: List[str] = []
        for _, row in available.iterrows():
            row_composition = str(row.get("Composition") or "").strip()
            if row_composition != composition:
                continue
            try:
                draw = int(row.get("Draw"))
                piece = int(row.get("Piece"))
            except (TypeError, ValueError):
                continue
            if draw not in selected_draws:
                continue
            if target_piece is not None and piece != int(target_piece):
                continue
            key = str(row.get("_group_key") or "").strip()
            if not key:
                key = _microwire_key_to_str((composition, draw, piece, None))
            if key:
                selected_keys.append(key)
            matches.append({column: row.get(column) for column in available.columns})
        if not matches:
            QtWidgets.QMessageBox.information(
                self,
                self.section_title,
                "No rows matched the selected composition/draw/piece combination.",
            )
            return

        frame = self.model.frame()
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame()
        column_order = self._current_column_order()
        existing_keys = self._existing_group_keys()
        new_rows = [row for row in matches if str(row.get("_group_key") or "") not in existing_keys]
        if new_rows:
            addition = pd.DataFrame(new_rows, columns=list(available.columns))
            if frame.empty:
                updated = addition
            else:
                updated = pd.concat([frame, addition], ignore_index=True)
            updated = self._apply_overrides_to_table(self._ensure_core_columns(updated))
            self.data.table = updated
            self.store.save(self.data)
            self.model.set_frame(updated)
            if column_order:
                self._apply_column_order(column_order)
            self._hide_columns(self._HIDDEN_VIDEO_COLUMNS)
            self._auto_fit_columns()
            self._update_status()
            self.data_updated.emit()
            self.log(
                f"{self.section_title}: added {len(new_rows)} microwire row(s)."
            )
        else:
            self.log(
                f"{self.section_title}: selected microwires are already present; focusing existing rows."
            )
        self._select_group_keys(selected_keys)
        self._refresh_dashboard()


class UniversalVideoBuilderWindow(QtWidgets.QMainWindow):
    PROJECT_EXTENSION = ".pydpj"
    PROJECT_VERSION = 1
    PROJECT_KIND = "MicrowireVideoBuilder"

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(LOGGER_NAME)
        self._base_title = "Universal Video Builder"
        self.setWindowTitle(self._base_title)
        self.resize(1180, 760)
        self.settings = QtCore.QSettings("MicrowireData", "UniversalVideoBuilder")
        self._project_path: Optional[Path] = None
        self._dirty = False
        self._suppress_dirty = False
        self._save_project_action: QtGui.QAction | None = None
        self._save_project_as_action: QtGui.QAction | None = None
        self._recent_projects: List[str] = []
        self._recent_projects_menu: QtWidgets.QMenu | None = None
        self._load_recent_projects_setting()

        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.log_view = QtWidgets.QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)

        def _append_log(level: int, message: str) -> None:
            try:
                self.log_view.appendPlainText(message)
                scrollbar = self.log_view.verticalScrollBar()
                if scrollbar is not None:
                    scrollbar.setValue(scrollbar.maximum())
            except RuntimeError:
                return
            if level >= logging.ERROR:
                self._dirty = True

        self._log_handler = QtLogHandler(_append_log)
        self._log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        if self._log_handler not in self.logger.handlers:
            self.logger.addHandler(self._log_handler)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical, self)
        self.section = UniversalVideoSection(self.logger, _append_log, splitter)
        splitter.addWidget(self.section)
        splitter.addWidget(self.log_view)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([760, 80])
        splitter.setHandleWidth(8)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self._suppress_dirty = True
        try:
            self.section._shutdown_background_threads()
            self.section.reset_to_blank()
        finally:
            self._suppress_dirty = False

        menu_bar = install_standard_menu(self, help_topic="builder_database", console=self.log_view)
        self._setup_project_actions(menu_bar)
        self._update_project_actions()
        self._update_project_title()

        self.section.data_updated.connect(self._mark_dirty)
        self.section.sources_changed.connect(self._mark_dirty)
        if isinstance(self.section.search_edit, QtWidgets.QLineEdit):
            self.section.search_edit.textChanged.connect(self._mark_dirty)
        if isinstance(self.section.table_view, QtWidgets.QTableView):
            header = self.section.table_view.horizontalHeader()
            if header is not None:
                header.sectionMoved.connect(self._mark_dirty)

    def _project_settings_key(self, name: str) -> str:
        return f"project/{name}"

    def _mark_dirty(self, *_: object) -> None:
        if self._suppress_dirty:
            return
        self._dirty = True
        self._update_project_title()
        self._update_project_actions()

    def _has_project_data_to_save(self) -> bool:
        return self.section.has_project_data()

    def _project_dialog_start_directory(self) -> Path:
        raw = str(self.settings.value(self._project_settings_key("last_dir"), "") or "").strip()
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.exists():
                return candidate
        if isinstance(self._project_path, Path):
            return self._project_path.parent
        return _dialog_start_directory()

    def _default_project_filename(self) -> str:
        return f"universal_video_builder{self.PROJECT_EXTENSION}"

    def _build_project_payload(self) -> Dict[str, Any]:
        return {
            "version": self.PROJECT_VERSION,
            "kind": self.PROJECT_KIND,
            "saved_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
            "section": self.section.export_project_payload(),
        }

    def _update_project_title(self) -> None:
        title = self._base_title
        if isinstance(self._project_path, Path):
            title = f"{title} - {self._project_path.name}"
        if self._dirty:
            title = f"{title} *"
        self.setWindowTitle(title)

    def _update_project_actions(self) -> None:
        has_data = self._has_project_data_to_save()
        if self._save_project_action is not None:
            self._save_project_action.setEnabled(has_data)
        if self._save_project_as_action is not None:
            self._save_project_as_action.setEnabled(has_data)

    def _setup_project_actions(self, menu_bar: QtWidgets.QMenuBar) -> None:
        file_menu = menu_bar.addMenu("&Project")

        new_action = QtGui.QAction("New Project", self)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        open_action = QtGui.QAction("Open Project…", self)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)

        recent_menu = file_menu.addMenu("Open Recent")
        recent_menu.setObjectName("universal_video_builder_recent_projects")
        self._recent_projects_menu = recent_menu
        self._update_recent_projects_menu()

        file_menu.addSeparator()

        save_action = QtGui.QAction("Save Project", self)
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)
        self._save_project_action = save_action

        save_as_action = QtGui.QAction("Save Project As…", self)
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)
        self._save_project_as_action = save_as_action

    def _remember_project_directory(self, directory: Path) -> None:
        try:
            resolved = directory.resolve()
        except Exception:
            resolved = directory
        self.settings.setValue(self._project_settings_key("last_dir"), str(resolved))

    def _remember_recent_project(self, path: Path) -> None:
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        self._recent_projects = [entry for entry in self._recent_projects if entry != resolved]
        self._recent_projects.insert(0, resolved)
        self._recent_projects = self._recent_projects[:8]
        self._save_recent_projects_setting()
        self._update_recent_projects_menu()

    def _load_recent_projects_setting(self) -> None:
        raw = self.settings.value(self._project_settings_key("recent"), "[]")
        entries: List[str]
        if isinstance(raw, str):
            try:
                entries = [str(item) for item in json.loads(raw) if isinstance(item, str)]
            except json.JSONDecodeError:
                entries = []
        elif isinstance(raw, (list, tuple)):
            entries = [str(item) for item in raw if isinstance(item, str)]
        else:
            entries = []
        seen: Set[str] = set()
        ordered: List[str] = []
        for entry in entries:
            candidate = str(entry).strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
        self._recent_projects = ordered[:8]

    def _save_recent_projects_setting(self) -> None:
        self.settings.setValue(
            self._project_settings_key("recent"),
            json.dumps(self._recent_projects[:8], ensure_ascii=False),
        )

    def _update_recent_projects_menu(self) -> None:
        menu = self._recent_projects_menu
        if not isinstance(menu, QtWidgets.QMenu):
            return
        menu.clear()
        if not self._recent_projects:
            placeholder = menu.addAction("No recent projects")
            placeholder.setEnabled(False)
            return
        for entry in self._recent_projects:
            display = Path(entry).name or entry
            action = menu.addAction(display)
            action.setToolTip(entry)
            action.triggered.connect(lambda _checked=False, value=entry: self._open_recent_project(value))

    def _open_recent_project(self, entry: str) -> None:
        if not self._confirm_discard_changes("opening another project"):
            return
        path = Path(entry)
        if not path.exists():
            QtWidgets.QMessageBox.warning(
                self,
                "Open Project",
                f"The project file {entry} could not be found.",
            )
            self._recent_projects = [item for item in self._recent_projects if item != entry]
            self._save_recent_projects_setting()
            self._update_recent_projects_menu()
            return
        self._load_project_from_path(path)

    def _save_project(self) -> None:
        if not self._has_project_data_to_save():
            QtWidgets.QMessageBox.information(
                self,
                "Save Project",
                "There is no data to save yet.",
            )
            return
        if self._project_path is None:
            self._save_project_as()
            return
        self._write_project_file(self._project_path)

    def _save_project_as(self) -> None:
        if not self._has_project_data_to_save():
            QtWidgets.QMessageBox.information(
                self,
                "Save Project As",
                "Connect a folder and load data before saving a project.",
            )
            return
        start_dir = self._project_dialog_start_directory()
        suggested = start_dir / self._default_project_filename()
        filters = f"Microwire Project (*{self.PROJECT_EXTENSION});;All files (*)"
        path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Project As",
            str(suggested),
            filters,
        )
        if not path_str:
            return
        target = Path(path_str)
        if target.suffix.lower() != self.PROJECT_EXTENSION:
            target = target.with_suffix(self.PROJECT_EXTENSION)
        self._write_project_file(target)

    def _write_project_file(self, target: Path) -> None:
        payload = self._build_project_payload()
        try:
            target.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Save Project",
                f"Failed to write project file:\n{exc}",
            )
            return
        self._project_path = target
        self._remember_project_directory(target.parent)
        self._remember_recent_project(target)
        self._dirty = False
        self._update_project_title()
        self._update_project_actions()
        self.logger.info("Project saved to %s", target)

    def _open_project(self) -> None:
        if not self._confirm_discard_changes("opening another project"):
            return
        start_dir = self._project_dialog_start_directory()
        filters = f"Microwire Project (*{self.PROJECT_EXTENSION});;All files (*)"
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Project",
            str(start_dir),
            filters,
        )
        if not path_str:
            return
        self._load_project_from_path(Path(path_str))

    def _load_project_from_path(self, target: Path) -> None:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Open Project",
                f"Failed to load project file:\n{exc}",
            )
            return
        if payload.get("kind") != self.PROJECT_KIND:
            QtWidgets.QMessageBox.critical(
                self,
                "Open Project",
                "The selected file is not a Universal Video Builder project.",
            )
            return
        section_payload = payload.get("section", {})
        self._suppress_dirty = True
        try:
            self.section.import_project_payload(section_payload if isinstance(section_payload, Mapping) else {})
        finally:
            self._suppress_dirty = False
        self._project_path = target
        self._remember_project_directory(target.parent)
        self._remember_recent_project(target)
        self._dirty = False
        self._update_project_title()
        self._update_project_actions()
        self.logger.info("Project loaded from %s", target)

    def _new_project(self) -> None:
        if not self._confirm_discard_changes("starting a new project"):
            return
        self._suppress_dirty = True
        try:
            self.section.reset_to_blank()
        finally:
            self._suppress_dirty = False
        self._project_path = None
        self._dirty = False
        self._update_project_title()
        self._update_project_actions()

    def _confirm_discard_changes(self, action_label: str) -> bool:
        if not self._dirty:
            return True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Unsaved project")
        box.setText(
            f"Save changes to this Universal Video Builder project before {action_label}?"
        )
        save_btn = box.addButton(QtWidgets.QMessageBox.StandardButton.Save)
        box.addButton("Discard", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_btn:
            return False
        if clicked is save_btn:
            self._save_project()
            return not self._dirty
        return True

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if not self._confirm_discard_changes("closing the window"):
            event.ignore()
            return
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
    window = UniversalVideoBuilderWindow()
    window.show()
    if owns_app:
        app.exec()
    return window


__all__ = [
    "UniversalVideoBuilderWindow",
    "UniversalVideoSection",
    "scan_universal_video_inputs",
    "main",
    "run_app",
]
