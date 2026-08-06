### Changed

- Added a developer-only press-and-hold TMA current-hold bypass whose state is owned by the isolated controller and is cleared on release, focus loss, pause, stop, completion, emergency, heartbeat loss, or process shutdown.
- Kept the Windows child-process spawn entry point compatible with already-running TMA parents when the optional bypass IPC lane is introduced by a source update.
- Tightened the Prague current-hold cycle-center escape so it resumes only after a mature stationary distribution repeatedly crosses the target with sufficient evidence on both sides; live snapshots expose the classification and override state, control traces retain the crossing evidence, and run metadata records the classifier configuration plus override history.
