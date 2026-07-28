### Fixed

- Prevented TMA, VSM hysteresis, and VSM temperature thumbnail completion from
  resetting a table's layout while it is being scrolled. Cached graph previews
  now request a decoration-only repaint, avoiding a native Qt crash during
  rapid preview browsing.
- Batched packaged graph-preview requests and stopped retrying completed rows.
  TMA thumbnails now render from the data stored inside the project, with
  display-only downsampling, so scrolling no longer fans out duplicate package
  reads or depends on the original measurement folders.
