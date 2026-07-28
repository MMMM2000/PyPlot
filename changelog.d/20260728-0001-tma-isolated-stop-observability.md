TMA recipes running in the dedicated controller process now preserve the motor
PSU channel on a confirmed normal stop, restore the return-load/displacement
prompt after ownership is released, stream child run-log updates into the
visible UI, keep setup samples out of recipe plots, and retain the full plot
time range through bounded thinning. Emergency, crash, and application-close
paths continue to disable the motor supply. Prague current-hold corrections
also wait for a complete filtered response to the previous motor command
before another correction can be issued.
