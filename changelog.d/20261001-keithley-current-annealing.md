### Added
- Current Annealing Logger supports Keithley 2636B channel A/B over VISA, with
  separate supply defaults, 50 Hz elapsed-time ramp control and a 100 Hz paired
  I/V acquisition target (0.1 NPLC, local two-wire sensing).
- Existing three-column annealing files retain their format and contain denser
  measurements. Keithley plotting is limited to 10 Hz and the latest 2,000 points
  (20,000 retained for history);
  disk writes are buffered independently of redraws.
- Exclusive VISA ownership, output-OFF takeover checks, bounded acquisition
  buffering, control-stall/contact-loss shutdown and verified output-OFF cleanup.
