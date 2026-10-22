2026-10-22 09:30 UTC

- Default Windows verification temp files to private workspace artifact paths, with an explicit short-parent override and a writable-directory preflight and limited-path-budget warning instead of the hard-coded C:/tmp/pyt root.
- Preserve parallel/serial isolation, report skip reasons, and write separate lane JUnit reports; add runner failure regressions and matching PowerShell options.
- Add a bounded Windows PR software gate using Python from pyproject.toml and locked uv test dependencies, with seven-day diagnostic retention.
