"""SPD1305X SCPI adapter: exclusive VISA ownership, local sense, 1 mA steps."""
import math


class SiglentSPD1305XAdapter:
    profile_mode = "Siglent SPD1305X (VISA)"
    # Conservative application policy, not a claimed ADC update rate.
    startup_timeout_s = 1.0
    startup_poll_s = 0.1

    def __init__(self, *, resource_name, current_limit_mA, resource_manager_factory):
        if not math.isfinite(current_limit_mA) or not 0 <= current_limit_mA <= 5000:
            raise ValueError("SPD1305X current limit must be between 0 and 5000 mA.")
        self.resource_name = resource_name.strip()
        self.current_limit_mA = current_limit_mA
        self._factory = resource_manager_factory
        self._manager = self._instrument = None
        self._locked = self._owned = False
        self._last_current = None
        self.description = "Siglent SPD1305X CH1 (VISA, local 2-wire sense, 1 mA steps)"

    def _status(self):
        return int(self._instrument.query("SYST:STAT?").strip(), 16)

    def open(self):
        if not self.resource_name:
            raise ValueError("Select the Siglent VISA resource.")
        try:
            self._manager = self._factory()
            d = self._instrument = self._manager.open_resource(self.resource_name)
            d.timeout = 2000
            d.write_termination = d.read_termination = "\n"
            d.lock_excl(timeout=1000)
            self._locked = True
            identity = d.query("*IDN?").strip()
            fields = [s.strip().upper() for s in identity.split(',')]
            if len(fields) < 2 or 'SIGLENT' not in fields[0] or fields[1] != 'SPD1305X':
                raise RuntimeError(f"Not a Siglent SPD1305X: {identity}")
            if self._status() & (0x10 | 0x40):
                raise RuntimeError("Siglent output/timer is already on; stop it before starting a run.")
            self._owned = True
            self.description += f"; {identity}"
        except Exception:
            self._release()
            raise

    def _release(self):
        d, manager = self._instrument, self._manager
        self._instrument = self._manager = None
        try:
            if d is not None:
                try:
                    if self._locked:
                        d.unlock()
                finally:
                    d.close()
        finally:
            self._locked = self._owned = False
            self._last_current = None
            if manager is not None:
                manager.close()

    def _validate_current(self, current_mA):
        if not math.isfinite(current_mA) or not 0 <= current_mA <= self.current_limit_mA:
            raise ValueError("Requested Siglent current is outside the run limit.")

    def configure(self, *, voltage_v, current_mA):
        if not self._owned:
            raise RuntimeError("Siglent is not owned by this adapter.")
        if not math.isfinite(voltage_v) or not 0 <= voltage_v <= 30:
            raise ValueError("SPD1305X voltage ceiling must be between 0 and 30 V.")
        self._validate_current(current_mA)
        d = self._instrument
        d.write("OUTP CH1,OFF")
        d.write("TIMER CH1,OFF")
        d.write("MODE:SET 2W")
        # Never round a voltage ceiling upwards either.
        voltage_v = math.floor(voltage_v * 1000) / 1000
        d.write(f"CH1:VOLT {voltage_v:.3f}")
        self.set_current(current_mA)
        if self._status() & (0x10 | 0x20 | 0x40):
            raise RuntimeError("Could not establish output-off/local-sense/timer-off state.")
        actual_v = float(d.query("CH1:VOLT?"))
        actual_i = float(d.query("CH1:CURR?")) * 1000
        if not math.isclose(actual_v, voltage_v, abs_tol=0.0001) or not math.isclose(actual_i, self._last_current, abs_tol=0.01):
            raise RuntimeError("Siglent setpoint verification failed; output not enabled.")
        d.write("OUTP CH1,ON")
        if not self._status() & 0x10:
            raise RuntimeError("Siglent output did not enable.")

    def set_current(self, current_mA):
        self._validate_current(current_mA)
        if not self._owned:
            raise RuntimeError("Siglent is not owned by this adapter.")
        quantized = math.floor(current_mA)
        if quantized != self._last_current:
            self._instrument.write(f"CH1:CURR {quantized / 1000:.3f}")
            self._last_current = quantized

    def measure(self):
        # Separate queries, not simultaneous acquisition; zero current has no valid R.
        current = float(self._instrument.query("MEAS:CURR? CH1")) * 1000
        voltage = float(self._instrument.query("MEAS:VOLT? CH1"))
        if not all(math.isfinite(v) and v >= 0 for v in (current, voltage)):
            raise RuntimeError("Invalid Siglent I/V readback.")
        return {"current_mA": current, "voltage_V": voltage}

    def close(self):
        errors = []
        try:
            if self._owned:
                for command in ("OUTP CH1,OFF", "TIMER CH1,OFF", "CH1:CURR 0.000", "CH1:VOLT 0.000"):
                    try:
                        self._instrument.write(command)
                    except Exception as exc:
                        errors.append(str(exc))
                try:
                    if self._status() & (0x10 | 0x40):
                        errors.append("Output or timer remains on")
                    if float(self._instrument.query("CH1:CURR?")) != 0 or float(self._instrument.query("CH1:VOLT?")) != 0:
                        errors.append("Setpoints did not return to zero")
                except Exception as exc:
                    errors.append(str(exc))
        finally:
            self._release()
        if errors:
            raise RuntimeError("Siglent safe shutdown not confirmed: " + '; '.join(errors))
