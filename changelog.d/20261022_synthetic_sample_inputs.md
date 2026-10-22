## Research data and validation

- Replace repository research-data test dependencies with small deterministic
  synthetic inputs and explicit numeric expectations; routine checks stay offline.
- Remove tracked `sample_data/` copies after recording their source commit and
  recovery manifest. History and raw/shared sources are preserved.
- Use remembered/Downloads input defaults and require selected disposable inputs
  for manual validation helpers.
