Added sample-scoped yes/no/not-assessed Brittle annotation to the microscope app
and CLI, with JSON/CSV measurement provenance and explicit guarded Builder project
updates including verification, backup and receipts. Builder now preserves an
explicit negative annotation when legacy microscope images would infer brittle.
New capture folders include Windows-safe sample labels, UTC time and a unique suffix.
Builder package publication tolerates brief Windows sharing locks while rechecking
source and destination fingerprints before every bounded retry.
