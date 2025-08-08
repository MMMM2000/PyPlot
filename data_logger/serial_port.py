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
    port.setPortName(port_name)
    port.setBaudRate(baudrate)
    port.setFlowControl(QtSerialPort.QSerialPort.FlowControl.NoFlowControl)
    port.setDataBits(QtSerialPort.QSerialPort.DataBits.Data8)
    port.setParity(QtSerialPort.QSerialPort.Parity.NoParity)
    port.setStopBits(QtSerialPort.QSerialPort.StopBits.OneStop)

    if not port.open(QtCore.QIODeviceBase.OpenModeFlag.ReadWrite):
        raise OSError(f"Failed to open serial port {port_name}")

    try:
        port.clear()
        yield port
    finally:
        port.close()
