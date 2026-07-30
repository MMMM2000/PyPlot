### Fixed

- TMA active-run metadata now uses fast local recovery checkpoints and bounded one-minute canonical `metadata.json` publishes, preserving atomic final metadata while sharply reducing DriveFS/network-folder replace churn and retaining a current checkpoint after interrupted runs.
