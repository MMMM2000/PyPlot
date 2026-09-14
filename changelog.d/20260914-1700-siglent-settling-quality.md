### Changed
- Siglent fixed-ramp timing is unchanged. A configurable resistance settling interval (provisional default 1 s, 0 disables) starts only after actual quantized current changes or output activation.
- Raw CSV resistance/I/V and all protection checks remain active. Separate qualified resistance and quality columns preserve transition evidence; live/history plots leave gaps during settling, and the display labels the last qualified value.
- Software regression tests and a bounded 10-19 mA hardware ramp verified flagging and safe shutdown; elapsed-time qualification is not a guarantee of ADC synchronization or absolute accuracy.
