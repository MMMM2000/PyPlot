## End sequence and monitor cooldown

- Added End sequence → Readout: cancels remaining steps/repeats and applies the initial/readout current while acquisition and the current CSV continue.
- Start next sequence is available after readout is acknowledged; the step table can be edited, while connection and safety settings stay locked. No reconnect or output-off transition occurs on restart. Stop retains its shutdown behavior.
- Added operating_mode, sequence_id, and sequence_elapsed_s CSV fields and sequence/readout events with recipe snapshots in metadata.
- Resistance trips remain latched and prevent sequence restart until Stop and a new run. Readout is not resistance regulation or automatic certification of mechanical baseline recovery.
- Software-only fake-driver tests for this change; no hardware operated. Resistance-based step endings are described in the subsequent resistance-step fragment.
