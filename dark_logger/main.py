import threading
import os
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover - only triggered if pyserial missing
    raise SystemExit(
        "Missing pyserial. Install with 'pip install pyserial' and try again'") from exc

import darkdetect
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

# Default configuration
LOG_DIR = os.getenv('LOG_DIR', '.')
DEFAULT_PORT_COMMAND = '>2050;1270;1;'
DEFAULT_LOG_FILE_NAME = 'log.txt'

class LoggerApp(ttk.Window):
    """Serial data logger with a dark mode GUI."""

    def __init__(self):
        theme = 'darkly' if darkdetect.isDark() else ttk.DEFAULT_THEME
        super().__init__(themename=theme)
        self.title('Dark Logger')
        self.serial = None
        self.logging = False
        self.log_file = None
        self.response_lock = threading.Lock()
        self.port_response = ''

        self.port_var = ttk.StringVar()
        self.baud_var = ttk.StringVar(value='115200')
        self.command_var = ttk.StringVar(value=DEFAULT_PORT_COMMAND)
        self.log_file_var = ttk.StringVar(value=DEFAULT_LOG_FILE_NAME)
        self.sample_count_var = ttk.IntVar(value=2000)

        self._create_widgets()
        self._populate_ports()
        self.after(10, self._update_response_label)

    def _create_widgets(self):
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill=BOTH, expand=YES)

        port_row = ttk.Frame(frame)
        port_row.pack(fill=X, pady=5)
        ttk.Label(port_row, text='Port:').pack(side=LEFT)
        self.port_menu = ttk.Combobox(port_row, textvariable=self.port_var, width=10)
        self.port_menu.pack(side=LEFT, padx=5)
        ttk.Label(port_row, text='Baud:').pack(side=LEFT, padx=(10, 0))
        self.baud_menu = ttk.Combobox(
            port_row,
            textvariable=self.baud_var,
            width=10,
            values=['921600', '460800', '115200', '57600', '19200', '9600'],
        )
        self.baud_menu.pack(side=LEFT)
        self.connect_btn = ttk.Button(port_row, text='Connect', command=self._toggle_connection)
        self.connect_btn.pack(side=LEFT, padx=10)

        cmd_row = ttk.Frame(frame)
        cmd_row.pack(fill=X, pady=5)
        self.cmd_entry = ttk.Entry(cmd_row, textvariable=self.command_var, width=40)
        self.cmd_entry.pack(side=LEFT, fill=X, expand=YES)
        ttk.Button(cmd_row, text='Send', command=self._send_command).pack(side=LEFT, padx=5)

        self.response_label = ttk.Label(frame, text='', wraplength=500)
        self.response_label.pack(fill=X, pady=10)

        log_row = ttk.Frame(frame)
        log_row.pack(fill=X, pady=5)
        ttk.Label(log_row, text='Log file:').pack(side=LEFT)
        self.log_entry = ttk.Entry(log_row, textvariable=self.log_file_var, width=30)
        self.log_entry.pack(side=LEFT, padx=5)
        ttk.Label(log_row, text='samples').pack(side=RIGHT)
        self.sample_spin = ttk.Spinbox(
            log_row,
            from_=1,
            to=1_000_000,
            textvariable=self.sample_count_var,
            width=8,
        )
        self.sample_spin.pack(side=RIGHT, padx=5)
        ttk.Button(log_row, text='Record', command=self._start_logging).pack(side=RIGHT, padx=5)

    def _populate_ports(self):
        self.port_menu['values'] = [p.device for p in list_ports.comports()]
        if self.port_menu['values']:
            self.port_var.set(self.port_menu['values'][0])

    def _toggle_connection(self):
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.serial = None
            self.connect_btn.config(text='Connect')
            return
        try:
            self.serial = serial.Serial(self.port_var.get(), int(self.baud_var.get()), timeout=0)
            self.connect_btn.config(text='Disconnect')
        except serial.SerialException as exc:
            ttk.Messagebox.show_error(message=f'Failed to open port: {exc}')
            self.serial = None

    def _send_command(self):
        if not self.serial or not self.serial.is_open:
            return
        self.serial.write((self.command_var.get() + '\n').encode('ascii'))

    def _start_logging(self):
        if not self.serial or not self.serial.is_open or self.logging:
            return
        log_dir = Path(LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / self.log_file_var.get()
        try:
            self.log_file = open(path, 'w')
        except OSError as exc:
            ttk.Messagebox.show_error(message=f'Failed to open {path}: {exc}')
            return
        self.logging = True
        self.sample_idx = 0
        self.after(10, self._read_from_port)

    def _read_from_port(self):
        if not (self.serial and self.serial.is_open and self.logging):
            return
        while self.serial.in_waiting:
            line = self.serial.readline().decode('ascii', errors='ignore').strip()
            with self.response_lock:
                self.port_response = line
            if self.logging:
                self.log_file.write(line + '\n')
                self.sample_idx += 1
                if self.sample_idx >= self.sample_count_var.get():
                    self.log_file.close()
                    self.logging = False
        self.after(10, self._read_from_port)

    def _update_response_label(self):
        with self.response_lock:
            text = self.port_response
        self.response_label.configure(text=text)
        self.after(100, self._update_response_label)

if __name__ == '__main__':
    app = LoggerApp()
    app.mainloop()
