## Current-program resistance protection

- Added selectable post-threshold actions: legacy latched current ceiling, immediate drop to the initial/readout current with continued logging, or output shutdown and run stop. Resistance regulation is not implemented.
- Readout mode stays latched until Stop and never increases current at the trip. The initial-current setting supplies the readout value; nonzero readout still heats the specimen.
- Added an independent requested resistance check rate (default 100 Hz), with CSV logging and UI decimated separately. Existing action defaults remain unchanged. Action and rate persist in settings and run metadata; metadata includes check count and maximum observed inter-check gap.
- Invalid protection readbacks fail the run and invoke adapter cleanup. Protection remains software/communication-limited, not an instrument-side watchdog or a verified temperature limit. No hardware timing or safe operating envelope is implied.
- Verified with fake adapters and isolated Qt settings; no hardware accessed.
