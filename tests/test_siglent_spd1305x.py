import math
import pytest
from experiments.siglent_spd1305x import SiglentSPD1305XAdapter


class Device:
    def __init__(self):
        self.status = 0
        self.current = 0.030
        self.voltage = 1.0
        self.commands = []
        self.closed = self.unlocked = False
        self.identity = 'Siglent Technologies,SPD1305X,TEST,2.1'
        self.fail = None

    def lock_excl(self, **kwargs): pass
    def unlock(self): self.unlocked = True
    def close(self): self.closed = True
    def write(self, command):
        self.commands.append(command)
        if command == self.fail: raise RuntimeError('write failure')
        if command == 'OUTP CH1,ON': self.status |= 0x10
        if command == 'OUTP CH1,OFF': self.status &= ~0x10
        if command == 'TIMER CH1,OFF': self.status &= ~0x40
        if command == 'MODE:SET 2W': self.status &= ~0x20
        if command.startswith('CH1:CURR '): self.current = float(command.split()[1])
        if command.startswith('CH1:VOLT '): self.voltage = float(command.split()[1])
    def query(self, command):
        return {'*IDN?': self.identity, 'SYST:STAT?': hex(self.status),
                'CH1:VOLT?': str(self.voltage), 'CH1:CURR?': str(self.current),
                'MEAS:CURR? CH1': '0.010', 'MEAS:VOLT? CH1': '1.750'}[command]


class Manager:
    def __init__(self, device): self.device = device; self.closed = False
    def open_resource(self, name): return self.device
    def close(self): self.closed = True


def make():
    d = Device(); rm = Manager(d)
    return SiglentSPD1305XAdapter(resource_name='FAKE', current_limit_mA=20,
                                resource_manager_factory=lambda: rm), d, rm


def test_settling_only_restarts_for_real_steps(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('experiments.siglent_spd1305x.time.monotonic', lambda: clock[0])
    a, d, rm = make()
    a.open()
    a.configure(voltage_v=4, current_mA=10)
    assert a.resistance_quality() == 'settling'
    clock[0] = 1.1
    a.set_current(10.9)
    assert a.resistance_quality() == 'settled_interval'
    a.set_current(11)
    assert a.resistance_quality() == 'settling'
    assert a.measure()['current_mA'] == 10  # Raw readback is never suppressed.
    clock[0] = 2.2
    assert a.resistance_quality() == 'settled_interval'
    a.close()


@pytest.mark.parametrize('delay', [-1, math.nan, math.inf])
def test_invalid_settling_interval(delay):
    with pytest.raises(ValueError):
        SiglentSPD1305XAdapter(resource_name='FAKE', current_limit_mA=20,
                              resource_manager_factory=lambda: None, settling_s=delay)


def test_siglent_control_measure_and_verified_shutdown():
    a,d,rm=make(); a.open(); a.configure(voltage_v=2,current_mA=10.9)
    assert d.current == .010
    assert d.status & 0x10
    assert a.measure() == {'current_mA':10,'voltage_V':1.75}
    a.set_current(11.8); assert d.current == .011
    a.set_current(.9); assert d.current == 0
    for v in [-1,21,math.nan,math.inf]:
        with pytest.raises(ValueError): a.set_current(v)
    a.close(); a.close()
    assert d.status == 0 and d.current == 0 and d.voltage == 0
    assert d.closed and d.unlocked and rm.closed


@pytest.mark.parametrize('status,identity',[(0x10,None),(0x40,None),(0,'Other,SPD1305X'),(0,'Siglent,SPD1168X')])
def test_siglent_refuses_takeover_or_wrong_device(status,identity):
    a,d,rm=make(); d.status=status
    if identity: d.identity=identity
    with pytest.raises(RuntimeError): a.open()
    assert not d.commands
    assert d.closed and d.unlocked and rm.closed


def test_siglent_shutdown_attempts_all_commands_on_failure():
    a,d,rm=make(); a.open(); a.configure(voltage_v=1,current_mA=1)
    d.fail='OUTP CH1,OFF'
    with pytest.raises(RuntimeError,match='not confirmed'): a.close()
    assert d.current == 0 and d.voltage == 0
    assert d.closed and rm.closed


def test_siglent_verifies_configuration_before_enable():
    a,d,rm=make(); a.open()
    original=d.query
    d.query=lambda q: '4.000' if q=='CH1:VOLT?' else original(q)
    with pytest.raises(RuntimeError,match='setpoint'): a.configure(voltage_v=1,current_mA=1)
    assert 'OUTP CH1,ON' not in d.commands
    d.query=original; a.close()


def test_siglent_lock_failure_does_not_change_output():
    a,d,rm=make()
    def fail(**kwargs): raise RuntimeError('busy')
    d.lock_excl=fail
    with pytest.raises(RuntimeError,match='busy'): a.open()
    assert d.closed and rm.closed and not d.commands and not d.unlocked


def test_siglent_rejects_invalid_measurement_and_voltage():
    a,d,rm=make(); a.open()
    for voltage in [31,-1,math.nan]:
        with pytest.raises(ValueError): a.configure(voltage_v=voltage,current_mA=1)
    original=d.query
    d.query=lambda q: 'nan' if q=='MEAS:CURR? CH1' else original(q)
    with pytest.raises(RuntimeError,match='Invalid'): a.measure()
    a.close()
