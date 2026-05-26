# Reporting Tooling

PyPlot's normal runtime dependencies live in `pyproject.toml`, `uv.lock`,
`requirements.txt`, and `requirements-win.txt`. Extra tools used only for
Codex/manual report generation are intentionally split out so the application
environment does not grow just because we need to print or inspect a PDF.

## Python report helpers

Install optional Python-only reporting helpers when needed:

```powershell
uv pip install -r requirements-reporting.txt
```

These helpers cover PDF generation, PDF text extraction, and table-oriented PDF
inspection. They are not needed for running the launcher or loggers.

## System PDF tools

Some PDF QA requires external executables rather than Python packages.

Recommended Windows installs:

```powershell
winget install --id oschwartz10612.Poppler -e
```

Poppler provides tools such as `pdftoppm`, `pdftotext`, `pdfinfo`, and
`pdfimages`. Codex uses `pdftoppm` to render generated PDFs to PNG pages for
visual inspection before calling a report finished.

Ghostscript is useful for some PostScript/PDF conversion and repair workflows,
but it is not required for the current Mini DMA report flow because Poppler can
render the generated PDFs for QA. If Ghostscript is needed later, install it from
the official Artifex download page and keep it as a machine-level dependency,
not a PyPlot runtime dependency.

## Dependency boundary

- Application/runtime dependencies: `pyproject.toml` and exported runtime locks.
- Test-only dependencies: the `test` optional dependency group.
- Report-only Python helpers: `requirements-reporting.txt`.
- Global/user tools: Poppler, Ghostscript, LibreOffice, or other command-line
  renderers that cannot be represented as Python packages.
