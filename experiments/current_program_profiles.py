"""Application operating envelopes, not manufacturer-guaranteed sample rates."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SupplyProfile:
    key: str
    vendor: str = ""
    max_voltage: float = 32.05
    max_current: float = 5000
    max_control_hz: float = 500
    max_acquisition_hz: float = 1000
    control_hz: float = 100
    acquisition_hz: float = 10
    protection_hz: float = 100
    ui_hz: float = 10
    current_step: float = .01
    conditional_steps: bool = True
    rate_basis: str = "Simulation scheduling limits."


PROFILES = {
    "Simulation": SupplyProfile("simulation"),
    "Keithley 2636B (VISA)": SupplyProfile("keithley2636b", "0X05E6", 20, 1500,
        rate_basis="0.01 NPLC configured; manufacturer high-rate figures use specific GPIB/script conditions, not a guarantee for this USB loop."),
    "Shared HMP broker": SupplyProfile("hmp", max_control_hz=5, max_acquisition_hz=2,
        control_hz=2, acquisition_hz=2, protection_hz=2, ui_hz=2, current_step=.2,
        conditional_steps=False,
        rate_basis="Provisional 2 Hz read/protection cap from operator experience. HMP datasheet: <50 ms nominal per command, not per I/V pair. This broker path is not benchmarked."),
    "Siglent SPD1305X (VISA)": SupplyProfile("spd1305x", "0XF4EC", 30, 5000,
        max_control_hz=10, max_acquisition_hz=20, control_hz=10, acquisition_hz=10,
        protection_hz=10, ui_hz=5, current_step=1, conditional_steps=False,
        rate_basis="2026-09-14 USB/output-OFF benchmark: set+read+verify up to 68.1 ms. Polling ceilings include margin; ADC freshness and loaded response remain unverified."),
}
