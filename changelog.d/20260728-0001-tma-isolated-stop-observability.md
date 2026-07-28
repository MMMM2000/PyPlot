TMA recipes running in the dedicated controller process now preserve the motor
PSU channel on a confirmed normal stop, restore the return-load/displacement
prompt after ownership is released, stream child run-log updates into the
visible UI, keep setup samples out of recipe plots, and retain the full plot
time range through bounded thinning. Emergency, crash, and application-close
paths continue to disable the motor supply. Prague current-hold corrections
also wait for a complete filtered response to the previous motor command
before another correction can be issued. The child now also preserves the
visible UI's completed prior-run/next-run filename decision instead of
reapplying stale automatic naming after ownership transfer.
The UI-to-controller handoff now releases shared-HMP leases without switching
the current or motor outputs off and back on, with an explicit all-output
emergency fallback if the child cannot take ownership. Setup snapshots continue
after the visible pre-prompt samples on one display timeline, recipe plots
accept their new elapsed clock immediately, and transient setup graphs refresh
at the faster UI cadence.
