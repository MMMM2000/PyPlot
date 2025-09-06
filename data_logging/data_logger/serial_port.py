from __future__ import annotations

"""Utility helpers for working with serial ports.

This module provides a context manager that configures and opens a
:class:`~PyQt6.QtSerialPort.QSerialPort` instance.  Extracting the port
setup into a separate module makes the data logger easier to test by
allowing the serial backend to be mocked.
"""

from contextlib import contextmanager
from typing import Iterator

from PyQt6 import QtCore, QtSerialPort
import os
import platform


@contextmanager
def serial_connection(port_name: str, baudrate: int) -> Iterator[QtSerialPort.QSerialPort]:
    """Open a configured :class:`QSerialPort` as a context manager.

    Parameters
    ----------
    port_name:
        Name of the serial port to open.
    baudrate:
        Baud rate for the connection.

    Yields
    ------
    QtSerialPort.QSerialPort
        The opened serial port instance.

    Raises
    ------
    OSError
        If the port cannot be opened.
    """

    port = QtSerialPort.QSerialPort()
    # Build candidate names: try the given string (may be a full path), then
    # basename, then a macOS 'cu.' variant if helpful.
    candidates = []
    if port_name:
        candidates.append(port_name)
        base = os.path.basename(port_name)
        if base and base != port_name:
            candidates.append(base)
            if platform.system() == 'Darwin':
                if base.startswith('tty'):
                    candidates.append('cu.' + base[4:])
                elif not base.startswith('cu.'):
                    candidates.append('cu.' + base)
    else:
        candidates.append(port_name)

    opened = False
    for name in candidates:
        try:
            port.setPortName(name)
            port.setBaudRate(baudrate)
            try:
                port.setReadBufferSize(0)
            except Exception:
                pass
            port.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
            port.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
            port.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
            port.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)
            if port.open(QtCore.QIODeviceBase.OpenModeFlag.ReadWrite):
                opened = True
                break
        except Exception:
            pass

    if not opened:
        raise OSError(f"Failed to open serial port {port_name}")

    try:
        port.clear()
        yield port
    finally:
        port.close()
