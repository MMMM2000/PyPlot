from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from scripts.saia_final_report.build_baseline import DOCUMENT_XML, build, patch_title


TITLE_XML = (
    b'<w:p xmlns:w="urn:w"><w:r><w:t>POLRO\xc4\x8cn\xc3\xa1</w:t></w:r>'
    b'<w:r><w:t xml:space="preserve"> spr\xc3\xa1v</w:t></w:r>'
    b'<w:r><w:t>a</w:t></w:r>'
    b'<w:r><w:t xml:space="preserve"> z\xc2\xa0pobytu</w:t></w:r></w:p>'
)


def test_patch_title_preserves_paragraph_markup() -> None:
    result = patch_title(TITLE_XML + b"<w:p><w:r><w:t>body</w:t></w:r></w:p>")

    assert b">Z\xc3\x81VERE\xc4\x8cN\xc3\x81<" in result
    assert b"> SPR\xc3\x81V<" in result
    assert b">A<" in result
    assert b"> Z\xc2\xa0POBYTU<" in result
    assert b"<w:p><w:r><w:t>body</w:t></w:r></w:p>" in result


def test_patch_title_rejects_missing_expected_run() -> None:
    with pytest.raises(ValueError, match="Expected exactly one"):
        patch_title(TITLE_XML.replace(b">a<", b">x<"))


def test_build_preserves_all_other_package_parts(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    document_xml = TITLE_XML + b"<w:p><w:r><w:t>body</w:t></w:r></w:p>"
    with zipfile.ZipFile(source, "w") as package:
        package.writestr(DOCUMENT_XML, document_xml)
        package.writestr("word/styles.xml", b"styles")
        package.writestr("word/media/image1.png", b"image")

    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    build(source, output, expected_hash)

    with zipfile.ZipFile(source) as source_zip, zipfile.ZipFile(output) as output_zip:
        assert source_zip.namelist() == output_zip.namelist()
        assert output_zip.read("word/styles.xml") == b"styles"
        assert output_zip.read("word/media/image1.png") == b"image"
        assert output_zip.read(DOCUMENT_XML) != document_xml
