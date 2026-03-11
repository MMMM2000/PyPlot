from __future__ import annotations

from pathlib import Path

from PIL import Image

from experiments import paddleocr_vl_pdf as module


class _FakeRenderer:
    def __init__(self) -> None:
        self._image = Image.new("RGB", (24, 24), "white")

    def to_pil(self) -> Image.Image:
        return self._image.copy()

    def close(self) -> None:
        return None


class _FakePage:
    def render(self, *, scale: float) -> _FakeRenderer:
        assert scale > 0
        return _FakeRenderer()

    def get_size(self) -> tuple[float, float]:
        return (120.0, 80.0)


class _FakePdfDocument:
    def __init__(self, _path: str) -> None:
        self._pages = [_FakePage(), _FakePage()]

    def __len__(self) -> int:
        return len(self._pages)

    def __iter__(self):
        return iter(self._pages)

    def close(self) -> None:
        return None


class _FakeTextObject:
    def setTextRenderMode(self, _mode: int) -> None:
        return None

    def setFont(self, _font: str, _size: float) -> None:
        return None

    def setTextOrigin(self, _x: float, _y: float) -> None:
        return None

    def textLine(self, _text: str) -> None:
        return None


class _FakeCanvas:
    instances: list["_FakeCanvas"] = []

    def __init__(self, _path: str) -> None:
        self.page_count = 0
        self.saved = False
        self.images: list[str] = []
        type(self).instances.append(self)

    def setPageSize(self, _size: tuple[float, float]) -> None:
        return None

    def drawInlineImage(self, image_path: str, *_args, **_kwargs) -> None:
        self.images.append(image_path)

    def beginText(self) -> _FakeTextObject:
        return _FakeTextObject()

    def drawText(self, _text_object: _FakeTextObject) -> None:
        return None

    def showPage(self) -> None:
        self.page_count += 1

    def save(self) -> None:
        self.saved = True


def test_convert_pdf_streams_pages_to_canvas(monkeypatch, tmp_path: Path) -> None:
    progress: list[tuple[int, int]] = []
    _FakeCanvas.instances.clear()

    monkeypatch.setattr(module.pdfium, "PdfDocument", _FakePdfDocument)
    monkeypatch.setattr(module.canvas, "Canvas", _FakeCanvas)
    monkeypatch.setattr(module, "_initialise_vl_engine", lambda disable_vl: (object(), "classic"))
    monkeypatch.setattr(module, "_initialise_classic_engine", lambda: object())
    monkeypatch.setattr(
        module,
        "_prepare_analysis_image",
        lambda pil_image, original_path, temp_dir, page_number: (original_path, pil_image),
    )
    monkeypatch.setattr(
        module,
        "_extract_overlays",
        lambda engine, image_path, page_width, page_height, raster_width, raster_height: [
            module.OverlayRegion("test", 1.0, 1.0, 10.0, 12.0)
        ],
    )

    output_path = tmp_path / "searchable.pdf"
    module.convert_pdf(
        tmp_path / "input.pdf",
        output_path,
        dpi=150,
        disable_vl=True,
        prompt="ignored",
        include_images=True,
        summary_font=10.0,
        include_summary_text=False,
        progress_callback=lambda current, total: progress.append((current, total)),
    )

    assert len(_FakeCanvas.instances) == 1
    fake_canvas = _FakeCanvas.instances[0]
    assert fake_canvas.page_count == 2
    assert fake_canvas.saved is True
    assert len(fake_canvas.images) == 2
    assert progress == [(0, 2), (1, 2), (2, 2)]


def test_main_cli_runs_converter(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"
    input_path.write_bytes(b"%PDF-1.4\n%fake\n")
    called: dict[str, object] = {}

    def _fake_convert(input_file: Path, output_file: Path, **kwargs) -> None:
        called["input"] = input_file
        called["output"] = output_file
        called.update(kwargs)

    monkeypatch.setattr(module, "convert_pdf", _fake_convert)

    result = module.main(
        [
            str(input_path),
            str(output_path),
            "--dpi",
            "150",
            "--no-summary-text",
        ]
    )

    assert result == 0
    assert called["input"] == input_path
    assert called["output"] == output_path
    assert called["start_page"] == 1
    assert called["end_page"] is None
    assert called["dpi"] == 150
    assert called["disable_vl"] is True
    assert called["include_images"] is True
    assert called["include_summary_text"] is False
    assert called["min_free_memory_percent"] == 20.0


def test_memory_guard_raises_when_free_memory_too_low(monkeypatch) -> None:
    monkeypatch.setattr(module, "_system_free_memory_percent", lambda: 9.5)

    try:
        module._enforce_memory_guard(20.0)
    except RuntimeError as exc:
        assert "protect system memory" in str(exc)
    else:
        raise AssertionError("Expected memory guard to abort OCR under low free-memory conditions")


def test_iter_classic_ocr_entries_supports_dict_style_results() -> None:
    results = [
        {
            "rec_texts": ["Alpha", "Beta"],
            "rec_polys": [
                [[1, 2], [3, 2], [3, 4], [1, 4]],
                [[5, 6], [7, 6], [7, 8], [5, 8]],
            ],
        }
    ]

    entries = module._iter_classic_ocr_entries(results)

    assert entries == [
        ("Alpha", [[1, 2], [3, 2], [3, 4], [1, 4]]),
        ("Beta", [[5, 6], [7, 6], [7, 8], [5, 8]]),
    ]
