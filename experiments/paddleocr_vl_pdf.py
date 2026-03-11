"""GUI utility for converting image PDFs into searchable PDFs with PaddleOCR-VL."""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import threading
import tempfile
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable, List, Optional, Sequence, Tuple

import pypdfium2 as pdfium
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets
from reportlab.pdfgen import canvas

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

try:
    from paddleocr import PaddleOCR, PaddleOCRVL
except ImportError as exc:  # pragma: no cover - dependency missing
    raise RuntimeError(
        "PaddleOCR is required for the PDF converter. Install the project dependencies first."
    ) from exc


LOGGER = logging.getLogger("paddleocr_vl_pdf")


try:
    _RESAMPLE_LANCZOS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - pillow<9.1 fallback
    _RESAMPLE_LANCZOS = Image.LANCZOS  # type: ignore[attr-defined]

_MAX_ANALYSIS_SIDE = 2200
_MIN_ANALYSIS_SIDE = 48
_DEFAULT_TEXT_DETECTION_MODEL = "PP-OCRv5_mobile_det"
_DEFAULT_TEXT_RECOGNITION_MODEL = "latin_PP-OCRv5_mobile_rec"


@dataclass
class OverlayRegion:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 0.0)


@dataclass
class PagePayload:
    width: float
    height: float
    image_path: Optional[Path]
    overlay: List[OverlayRegion]
    summary_lines: List[str]


def _collect_strings(payload: object, *, prompt: str) -> List[str]:
    """Recursively extract textual segments from a PaddleOCR-VL payload."""

    collected: List[str] = []

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"text", "content", "answer", "value"} and isinstance(value, str):
                    candidate = value.strip()
                    if candidate:
                        collected.append(candidate)
                _walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
        elif isinstance(node, str):
            candidate = node.strip()
            if candidate:
                collected.append(candidate)

    _walk(payload)

    filtered: List[str] = []
    seen: set[str] = set()
    prompt_normalised = prompt.strip().lower()
    for entry in collected:
        normalised = entry.lower()
        if not entry or normalised == prompt_normalised:
            continue
        if normalised in seen:
            continue
        seen.add(normalised)
        filtered.append(entry)
    return filtered


def _initialise_vl_engine(disable_vl: bool) -> Tuple[PaddleOCR | PaddleOCRVL, str]:
    """Create the OCR engines used by the converter."""

    if disable_vl:
        LOGGER.info("PaddleOCR-VL disabled; using classic PaddleOCR for summaries.")
        classic = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            text_detection_model_name=_DEFAULT_TEXT_DETECTION_MODEL,
            text_recognition_model_name=_DEFAULT_TEXT_RECOGNITION_MODEL,
            text_det_limit_side_len=4000,
            use_textline_orientation=False,
        )
        return classic, "classic"

    try:
        engine = PaddleOCRVL()
    except Exception as exc:  # pragma: no cover - optional dependency
        missing_msg = (
            "PaddleOCR-VL requires the paddlex[ocr] extra (and a safetensors build with paddle support). "
            "Install with `pip install \"paddlex[ocr]\"` to pull the Python deps and, on macOS, install Rust "
            "then rebuild safetensors from source: `pip install --no-binary safetensors safetensors`. "
            "You can also uncheck \"Use PaddleOCR-VL for page summaries\" to fall back to classic OCR."
        )
        raise RuntimeError(missing_msg) from exc

    LOGGER.info("Using PaddleOCR-VL pipeline for page-level summaries.")
    return engine, "vl"


def _initialise_classic_engine() -> PaddleOCR:
    LOGGER.info(
        "Using PaddleOCR mobile OCR models (%s / %s).",
        _DEFAULT_TEXT_DETECTION_MODEL,
        _DEFAULT_TEXT_RECOGNITION_MODEL,
    )
    return PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        text_detection_model_name=_DEFAULT_TEXT_DETECTION_MODEL,
        text_recognition_model_name=_DEFAULT_TEXT_RECOGNITION_MODEL,
        text_det_limit_side_len=_MAX_ANALYSIS_SIDE,
        use_textline_orientation=False,
        return_word_box=True,
    )


def _summarise_page(
    engine: PaddleOCR | PaddleOCRVL,
    mode: str,
    image_path: Path,
    prompt: str,
) -> List[str]:
    if not prompt.strip():
        return []
    if mode == "vl":
        try:
            result = engine.predict({"image": str(image_path), "prompt": prompt})
        except Exception:
            result = engine.predict(str(image_path))
        return _collect_strings(result, prompt=prompt)

    lines: List[str] = []
    for text, _box in _iter_classic_ocr_entries(engine.ocr(str(image_path))):
        cleaned = text.strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _iter_classic_ocr_entries(results: object) -> List[tuple[str, object]]:
    entries: List[tuple[str, object]] = []
    if not isinstance(results, list):
        return entries

    for entry in results:
        if isinstance(entry, dict):
            texts = entry.get("rec_texts")
            polys = entry.get("rec_polys") or entry.get("dt_polys")
            if isinstance(texts, list) and isinstance(polys, list):
                for text, poly in zip(texts, polys):
                    if isinstance(text, str):
                        entries.append((text, poly))
            continue

        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        box, info = entry[0], entry[1]
        if isinstance(info, (list, tuple)) and info:
            text = info[0]
            if isinstance(text, str):
                entries.append((text, box))
    return entries


def _extract_overlays(
    engine: PaddleOCR,
    image_path: Path,
    page_width: float,
    page_height: float,
    raster_width: int,
    raster_height: int,
) -> List[OverlayRegion]:
    try:
        results = engine.ocr(str(image_path))
    except Exception as exc:
        LOGGER.warning("Classic OCR failed on %s: %s", image_path, exc)
        return []

    overlays: List[OverlayRegion] = []
    scale_x = page_width / float(raster_width or 1)
    scale_y = page_height / float(raster_height or 1)

    for text, box in _iter_classic_ocr_entries(results):
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not text:
            continue

        xs: List[float] = []
        ys: List[float] = []
        try:
            for point in box:
                if len(point) >= 2:
                    xs.append(float(point[0]))
                    ys.append(float(point[1]))
        except Exception:
            continue
        if not xs or not ys:
            continue
        x0 = min(xs) * scale_x
        x1 = max(xs) * scale_x
        y_top = max(ys) * scale_y
        y_bottom = min(ys) * scale_y
        y0 = page_height - y_top
        y1 = page_height - y_bottom
        if x1 <= x0 or y1 <= y0:
            continue
        overlays.append(
            OverlayRegion(
                text=text,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
            )
        )
    return overlays


def _write_page_to_canvas(
    pdf: canvas.Canvas,
    payload: PagePayload,
    *,
    show_images: bool,
    summary_font: float,
) -> None:
    pdf.setPageSize((payload.width, payload.height))

    if show_images and payload.image_path is not None:
        pdf.drawInlineImage(str(payload.image_path), 0, 0, width=payload.width, height=payload.height)

    for region in payload.overlay:
        text_obj = pdf.beginText()
        text_obj.setTextRenderMode(3)  # invisible text for search
        font_height = max(region.height * 0.85, 6.0)
        text_obj.setFont("Helvetica", font_height)
        text_obj.setTextOrigin(region.x0, region.y0)
        text_obj.textLine(region.text)
        pdf.drawText(text_obj)

    if payload.summary_lines:
        text_obj = pdf.beginText()
        text_obj.setTextRenderMode(3)
        text_obj.setFont("Helvetica", summary_font)
        margin = 36
        text_obj.setTextOrigin(margin, payload.height - margin)
        for line in payload.summary_lines:
            text_obj.textLine(line)
        pdf.drawText(text_obj)

    pdf.showPage()


class ConversionCancelled(Exception):
    """Raised when the PDF conversion is cancelled by the user."""


def _prepare_analysis_image(
    pil_image: Image.Image, original_path: Path, temp_dir: Path, page_number: int
) -> tuple[Path, Image.Image]:
    """Return a resized copy of ``pil_image`` suitable for Paddle OCR."""

    analysis_image = pil_image
    analysis_changed = False
    max_dim = max(pil_image.width, pil_image.height)
    if max_dim > _MAX_ANALYSIS_SIDE:
        scale = _MAX_ANALYSIS_SIDE / float(max_dim)
        new_width = max(_MIN_ANALYSIS_SIDE, int(round(pil_image.width * scale)))
        new_height = max(_MIN_ANALYSIS_SIDE, int(round(pil_image.height * scale)))
        analysis_image = pil_image.resize((new_width, new_height), _RESAMPLE_LANCZOS)
        analysis_changed = True
        LOGGER.info(
            "Downscaled page %s for OCR: %sx%s → %sx%s",
            page_number,
            pil_image.width,
            pil_image.height,
            analysis_image.width,
            analysis_image.height,
        )
    if analysis_image.mode not in {"RGB", "L"}:
        analysis_image = analysis_image.convert("RGB")
        analysis_changed = True

    if analysis_changed:
        analysis_path = temp_dir / f"page_{page_number:04d}_analysis.png"
        analysis_image.save(analysis_path, format="PNG")
    else:
        analysis_path = original_path

    return analysis_path, analysis_image


def _system_free_memory_percent() -> Optional[float]:
    try:
        completed = subprocess.run(
            ["memory_pressure", "-Q"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None

    match = re.search(
        r"System-wide memory free percentage:\s*([0-9]+(?:\.[0-9]+)?)%",
        completed.stdout,
    )
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _enforce_memory_guard(min_free_memory_percent: Optional[float]) -> None:
    if min_free_memory_percent is None:
        return
    free_percent = _system_free_memory_percent()
    if free_percent is None:
        return
    if free_percent < float(min_free_memory_percent):
        raise RuntimeError(
            "Stopping OCR to protect system memory: "
            f"free memory fell to {free_percent:.1f}% (threshold {float(min_free_memory_percent):.1f}%)."
        )


def convert_pdf(
    input_path: Path,
    output_path: Path,
    *,
    start_page: int = 1,
    end_page: Optional[int] = None,
    dpi: int,
    disable_vl: bool,
    prompt: str,
    include_images: bool,
    summary_font: float,
    include_summary_text: bool = True,
    min_free_memory_percent: Optional[float] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    if disable_vl:
        classic_engine = _initialise_classic_engine()
        vl_engine, vl_mode = classic_engine, "classic"
    else:
        vl_engine, vl_mode = _initialise_vl_engine(disable_vl)
        classic_engine = _initialise_classic_engine()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output_path))

    with tempfile.TemporaryDirectory(prefix="paddleocr_vl_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        try:
            pdf_doc = pdfium.PdfDocument(str(input_path))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load document (PDFium: {exc}). Try re-saving the PDF without encryption or export a fresh copy."
            ) from exc
        total_pages = len(pdf_doc)
        selected_start = max(1, int(start_page))
        selected_end = total_pages if end_page is None else min(total_pages, int(end_page))
        if selected_end < selected_start:
            raise RuntimeError(
                f"Invalid page range: start page {selected_start} is after end page {selected_end}."
            )
        selected_total = selected_end - selected_start + 1
        if progress_callback is not None:
            progress_callback(0, selected_total)
        scale = dpi / 72.0
        processed_count = 0

        try:
            for page_number, page in enumerate(pdf_doc, start=1):
                if page_number < selected_start:
                    continue
                if page_number > selected_end:
                    break
                _enforce_memory_guard(min_free_memory_percent)
                if cancel_event is not None and cancel_event.is_set():
                    LOGGER.info("Conversion cancelled before processing page %s.", page_number)
                    raise ConversionCancelled()

                renderer = page.render(scale=scale)
                pil_image = renderer.to_pil()
                renderer.close()
                width, height = page.get_size()

                image_path = temp_dir / f"page_{page_number:04d}.png"
                pil_image.save(image_path, format="PNG")

                analysis_path, analysis_image = _prepare_analysis_image(
                    pil_image, image_path, temp_dir, page_number
                )

                if include_summary_text and vl_mode == "vl":
                    summary_lines = _summarise_page(vl_engine, vl_mode, analysis_path, prompt)
                else:
                    summary_lines = []
                overlays = _extract_overlays(
                    classic_engine,
                    analysis_path,
                    width,
                    height,
                    analysis_image.width,
                    analysis_image.height,
                )

                _write_page_to_canvas(
                    pdf,
                    PagePayload(
                        width=width,
                        height=height,
                        image_path=image_path if include_images else None,
                        overlay=overlays,
                        summary_lines=summary_lines,
                    ),
                    show_images=include_images,
                    summary_font=summary_font,
                )

                try:
                    pil_image.close()
                except Exception:
                    pass
                if analysis_path != image_path:
                    try:
                        analysis_image.close()
                    except Exception:
                        pass

                processed_count += 1
                if progress_callback is not None:
                    progress_callback(processed_count, selected_total)
                _enforce_memory_guard(min_free_memory_percent)
                if cancel_event is not None and cancel_event.is_set():
                    LOGGER.info("Conversion cancelled after processing page %s.", page_number)
                    raise ConversionCancelled()
        finally:
            pdf_doc.close()

    if cancel_event is not None and cancel_event.is_set():
        LOGGER.info("Conversion cancelled before saving output.")
        raise ConversionCancelled()
    pdf.save()
    LOGGER.info("Searchable PDF written to %s", output_path)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert an image-based PDF into a searchable PDF using PaddleOCR."
    )
    parser.add_argument("input", nargs="?", help="Input image PDF")
    parser.add_argument("output", nargs="?", help="Output searchable PDF")
    parser.add_argument("--dpi", type=int, default=200, help="Raster DPI used for OCR (default: 200)")
    parser.add_argument("--start-page", type=int, default=1, help="First 1-based page to convert.")
    parser.add_argument("--end-page", type=int, help="Last 1-based page to convert.")
    parser.add_argument(
        "--use-vl",
        action="store_true",
        help="Use PaddleOCR-VL page summaries in addition to invisible OCR overlay text.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Write a text-only PDF without re-embedding the original page images.",
    )
    parser.add_argument(
        "--no-summary-text",
        action="store_true",
        help="Disable page-level summary text injection. Recommended for plain searchable PDF output.",
    )
    parser.add_argument(
        "--prompt",
        default="Transcribe this page into text.",
        help="Prompt used for PaddleOCR-VL page summaries.",
    )
    parser.add_argument(
        "--summary-font",
        type=float,
        default=10.0,
        help="Font size for hidden summary text when VL summaries are enabled.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open the GUI instead of running the CLI conversion flow.",
    )
    parser.add_argument(
        "--min-free-memory-percent",
        type=float,
        default=20.0,
        help="Abort OCR if macOS reports less free memory than this percentage (default: 20).",
    )
    return parser.parse_args(argv)


def _run_cli(args: argparse.Namespace) -> int:
    input_value = (args.input or "").strip()
    output_value = (args.output or "").strip()
    if not input_value or not output_value:
        raise SystemExit("CLI mode requires both input and output PDF paths.")

    input_path = Path(input_value).expanduser()
    output_path = Path(output_value).expanduser()
    if not input_path.exists():
        raise SystemExit(f"Input PDF does not exist: {input_path}")

    LOGGER.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    LOGGER.handlers[:] = [handler]

    def _progress(current: int, total: int) -> None:
        print(f"[{current}/{total}] processed", flush=True)

    convert_pdf(
        input_path,
        output_path,
        start_page=max(1, int(args.start_page)),
        end_page=args.end_page,
        dpi=max(72, int(args.dpi)),
        disable_vl=not args.use_vl,
        prompt=args.prompt,
        include_images=not args.no_images,
        summary_font=float(args.summary_font),
        include_summary_text=not args.no_summary_text,
        min_free_memory_percent=float(args.min_free_memory_percent),
        progress_callback=_progress,
    )
    return 0


class _QtLogHandler(QtCore.QObject, logging.Handler):
    message_emitted = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        QtCore.QObject.__init__(self)
        logging.Handler.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - thin wrapper
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.message_emitted.emit(message)


class _ConversionWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal()
    failed = QtCore.pyqtSignal(str)
    cancelled = QtCore.pyqtSignal()

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        *,
        dpi: int,
        disable_vl: bool,
        prompt: str,
        include_images: bool,
        summary_font: float,
    ) -> None:
        super().__init__()
        self._input_path = input_path
        self._output_path = output_path
        self._dpi = dpi
        self._disable_vl = disable_vl
        self._prompt = prompt
        self._include_images = include_images
        self._summary_font = summary_font
        self._cancel_event = threading.Event()

    @QtCore.pyqtSlot()
    def run(self) -> None:  # pragma: no cover - exercised via GUI
        try:
            convert_pdf(
                self._input_path,
                self._output_path,
                dpi=self._dpi,
                disable_vl=self._disable_vl,
                prompt=self._prompt,
                include_images=self._include_images,
                summary_font=self._summary_font,
                progress_callback=self._emit_progress,
                cancel_event=self._cancel_event,
            )
        except ConversionCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit()

    def _emit_progress(self, current: int, total: int) -> None:
        try:
            self.progress.emit(int(current), int(total))
        except Exception:
            pass

    def cancel(self) -> None:
        self._cancel_event.set()


class PaddleOCRVLPDFWindow(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PaddleOCR-VL PDF Converter")
        self.resize(760, 520)

        self._thread: QtCore.QThread | None = None
        self._worker: _ConversionWorker | None = None
        self._settings = QtCore.QSettings("MicrowireTools", "PaddleOCRVLPDF")
        last_dir_value = self._settings.value("last_dir")
        if isinstance(last_dir_value, str) and last_dir_value:
            self._last_dir = Path(last_dir_value)
        else:
            self._last_dir = Path.cwd()

        self._build_ui()
        self._configure_logging()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)

        self.input_edit = QtWidgets.QLineEdit()
        self.input_edit.setPlaceholderText("Select an image-based PDF…")
        browse_input = QtWidgets.QPushButton("Browse…")
        browse_input.clicked.connect(self._choose_input)
        form.addWidget(QtWidgets.QLabel("Input PDF:"), 0, 0)
        form.addWidget(self.input_edit, 0, 1)
        form.addWidget(browse_input, 0, 2)

        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("Choose where to write the searchable PDF…")
        browse_output = QtWidgets.QPushButton("Browse…")
        browse_output.clicked.connect(self._choose_output)
        form.addWidget(QtWidgets.QLabel("Output PDF:"), 1, 0)
        form.addWidget(self.output_edit, 1, 1)
        form.addWidget(browse_output, 1, 2)

        self.dpi_spin = QtWidgets.QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setValue(300)
        form.addWidget(QtWidgets.QLabel("Render DPI:"), 2, 0)
        form.addWidget(self.dpi_spin, 2, 1)

        self.include_images_check = QtWidgets.QCheckBox("Embed original page images")
        self.include_images_check.setChecked(True)
        form.addWidget(self.include_images_check, 3, 1, 1, 2)

        self.use_vl_check = QtWidgets.QCheckBox("Use PaddleOCR-VL for page summaries")
        self.use_vl_check.setChecked(True)
        form.addWidget(self.use_vl_check, 4, 1, 1, 2)

        self.summary_font_spin = QtWidgets.QDoubleSpinBox()
        self.summary_font_spin.setDecimals(1)
        self.summary_font_spin.setRange(6.0, 24.0)
        self.summary_font_spin.setSingleStep(0.5)
        self.summary_font_spin.setValue(10.0)
        form.addWidget(QtWidgets.QLabel("Summary font size (pt):"), 5, 0)
        form.addWidget(self.summary_font_spin, 5, 1)

        self.prompt_edit = QtWidgets.QLineEdit("Transcribe this page into text.")
        form.addWidget(QtWidgets.QLabel("Prompt:"), 6, 0)
        form.addWidget(self.prompt_edit, 6, 1, 1, 2)

        layout.addLayout(form)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        layout.addWidget(self.log_view, 1)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._cancel_conversion)
        button_row.addWidget(self.stop_button)
        self.convert_button = QtWidgets.QPushButton("Convert")
        self.convert_button.clicked.connect(self._start_conversion)
        button_row.addWidget(self.convert_button)
        layout.addLayout(button_row)

    def _configure_logging(self) -> None:
        self._log_handler = _QtLogHandler()
        self._log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        self._log_handler.message_emitted.connect(self._append_log)
        LOGGER.addHandler(self._log_handler)
        LOGGER.setLevel(logging.INFO)

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        scrollbar = self.log_view.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def _effective_start_dir(self) -> Path:
        try:
            candidate = self._last_dir
        except AttributeError:
            return Path.cwd()
        if isinstance(candidate, Path) and candidate.exists():
            return candidate
        return Path.cwd()

    def _update_last_dir(self, path: Path) -> None:
        if not isinstance(path, Path):
            return
        directory = path if path.is_dir() else path.parent
        if not isinstance(directory, Path):
            return
        self._last_dir = directory
        self._settings.setValue("last_dir", str(directory))

    def _choose_input(self) -> None:
        start_dir = self._effective_start_dir()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select PDF",
            str(start_dir),
            "PDF files (*.pdf)",
        )
        if path:
            self._update_last_dir(Path(path))
            self.input_edit.setText(path)
            if not self.output_edit.text():
                stem = Path(path).stem + "_searchable.pdf"
                self.output_edit.setText(str(Path(path).with_name(stem)))

    def _choose_output(self) -> None:
        current_output = self.output_edit.text().strip()
        if current_output:
            start_path = Path(current_output)
            start_dir = start_path.parent if start_path.parent.exists() else self._effective_start_dir()
        else:
            start_dir = self._effective_start_dir()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Searchable PDF",
            current_output or str(start_dir / "searchable.pdf"),
            "PDF files (*.pdf)",
        )
        if path:
            self._update_last_dir(Path(path))
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.output_edit.setText(path)

    def _start_conversion(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            QtWidgets.QMessageBox.warning(self, "Conversion in progress", "Please wait for the current conversion to finish.")
            return

        input_path = Path(self.input_edit.text().strip())
        output_path = Path(self.output_edit.text().strip()) if self.output_edit.text().strip() else None

        if not input_path.exists():
            QtWidgets.QMessageBox.warning(self, "Invalid input", "Select an existing PDF file to convert.")
            return

        if output_path is None:
            QtWidgets.QMessageBox.warning(self, "Missing output", "Choose a destination for the searchable PDF.")
            return

        if not output_path.parent.exists():
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Unable to create directory",
                    f"Failed to create {output_path.parent}:\n{exc}",
                )
                return

        self.convert_button.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.log_view.clear()
        self._append_log(f"Starting conversion: {input_path} → {output_path}")
        self.stop_button.setEnabled(True)
        self._update_last_dir(input_path.parent)
        self._update_last_dir(output_path.parent)

        self._thread = QtCore.QThread(self)
        self._worker = _ConversionWorker(
            input_path,
            output_path,
            dpi=int(self.dpi_spin.value()),
            disable_vl=not self.use_vl_check.isChecked(),
            prompt=self.prompt_edit.text().strip() or "Transcribe this page into text.",
            include_images=self.include_images_check.isChecked(),
            summary_font=float(self.summary_font_spin.value()),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._handle_progress)
        self._worker.finished.connect(self._handle_finished)
        self._worker.failed.connect(self._handle_failed)
        self._worker.cancelled.connect(self._handle_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)
        self._thread.start()

    def _cancel_conversion(self) -> None:
        if self._worker is None or self._thread is None:
            return
        if not self._thread.isRunning():
            return
        self._append_log("Cancellation requested; stopping after current page.")
        self.stop_button.setEnabled(False)
        try:
            self._worker.cancel()
        except Exception:
            pass

    def _handle_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(max(0, min(current, total)))
        self._append_log(f"Processed page {current} of {total}")

    def _handle_finished(self) -> None:
        self._append_log("Conversion completed successfully.")
        QtWidgets.QMessageBox.information(self, "Conversion complete", "The searchable PDF has been created.")
        self._reset_controls()

    def _handle_failed(self, message: str) -> None:
        self._append_log(f"Conversion failed: {message}")
        details = message
        if "Failed to load document (PDFium" in message:
            details += "\n\nPDFium could not parse the file. Save a local copy without encryption or re-export the PDF, then try again."
        QtWidgets.QMessageBox.critical(self, "Conversion failed", details)
        self._reset_controls()

    def _handle_cancelled(self) -> None:
        self._append_log("Conversion cancelled.")
        QtWidgets.QMessageBox.information(self, "Conversion cancelled", "The PDF conversion was cancelled.")
        self._reset_controls()

    def _cleanup_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    def _reset_controls(self) -> None:
        self.convert_button.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.stop_button.setEnabled(False)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self._thread is not None and self._thread.isRunning():
            self._cancel_conversion()
            self._append_log("Waiting for conversion to finish before closing...")
            self._thread.quit()
            self._thread.wait(2000)
        if hasattr(self, "_log_handler"):
            LOGGER.removeHandler(self._log_handler)
        super().closeEvent(event)


def main(argv: Optional[Sequence[str]] = None) -> QtWidgets.QWidget | int | None:
    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    if not args.gui and (args.input or args.output):
        return _run_cli(args)

    from plotting.shared.utils import ensure_app_theme

    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication([])
        ensure_app_theme(app)
        owns_app = True
    window = PaddleOCRVLPDFWindow()
    window.show()
    if owns_app:
        app.exec()
        return None
    return window


__all__ = ["convert_pdf", "PaddleOCRVLPDFWindow", "main"]


if __name__ == "__main__":  # pragma: no cover - CLI/GUI entry point
    raise SystemExit(main())
