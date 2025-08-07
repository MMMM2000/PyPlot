import math
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from serial import Serial, SerialException
from serial.tools import list_ports

DEFAULT_CMD = ">2050;1270;1;"
DEFAULT_SAMPLE_COUNT = 2000


class LoggerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Windows Data Logger")
        self.port_var = tk.StringVar()
        self.baud_var = tk.IntVar(value=115200)
        self.cmd_var = tk.StringVar(value=DEFAULT_CMD)
        self.resp_var = tk.StringVar()
        self.sample_count_var = tk.IntVar(value=DEFAULT_SAMPLE_COUNT)
        self.sample_idx = 0
        self.logging = False
        self.serial: Serial | None = None
        self.log_file = None
        self.sample_rate: float | None = None
        self._rate_window: list[float] = []
        self.last_time: float | None = None
        self._build_ui()
        self.refresh_ports()
        self.after(10, self._poll_serial)

    # ------------------------------------------------------------------ UI setup
    def _build_ui(self) -> None:
        row = ttk.Frame(self)
        row.pack(pady=4)
        self.port_combo = ttk.Combobox(row, textvariable=self.port_var, width=20)
        self.port_combo.pack(side=tk.LEFT)
        ttk.Button(row, text="Refresh", command=self.refresh_ports).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Entry(row, textvariable=self.baud_var, width=8).pack(side=tk.LEFT, padx=2)
        self.conn_btn = ttk.Button(row, text="Connect", command=self.toggle_connection)
        self.conn_btn.pack(side=tk.LEFT, padx=2)

        ttk.Label(self, textvariable=self.resp_var).pack(pady=4)
        cmd_row = ttk.Frame(self)
        cmd_row.pack(pady=4)
        ttk.Entry(cmd_row, textvariable=self.cmd_var, width=40).pack(side=tk.LEFT)
        ttk.Button(cmd_row, text="Send", command=self.send_command).pack(
            side=tk.LEFT, padx=2
        )

        log_row = ttk.Frame(self)
        log_row.pack(pady=4)
        ttk.Entry(log_row, textvariable=self.sample_count_var, width=10).pack(
            side=tk.LEFT
        )
        self.rec_btn = ttk.Button(log_row, text="Record", command=self.start_logging)
        self.rec_btn.pack(side=tk.LEFT, padx=2)
        self.cancel_btn = ttk.Button(
            log_row, text="Cancel", command=self.cancel_logging, state=tk.DISABLED
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=2)

        self.progress = ttk.Progressbar(
            self, maximum=self.sample_count_var.get(), length=300
        )
        self.progress.pack(pady=4)
        self.rate_label = ttk.Label(self, text="Sample rate: N/A")
        self.rate_label.pack()
        self.time_label = ttk.Label(self, text="Time remaining: N/A")
        self.time_label.pack()

    # ----------------------------------------------------------------- utilities
    def refresh_ports(self) -> None:
        ports = [p.device for p in list_ports.comports()]
        self.port_combo["values"] = ports
        if ports:
            self.port_var.set(ports[0])

    def toggle_connection(self) -> None:
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.serial = None
            self.conn_btn.config(text="Connect")
            return
        try:
            self.serial = Serial(self.port_var.get(), self.baud_var.get(), timeout=0)
            self.conn_btn.config(text="Disconnect")
        except SerialException as exc:
            messagebox.showerror("Error", str(exc))
            self.serial = None

    def _poll_serial(self) -> None:
        if self.serial and self.serial.is_open:
            try:
                raw = self.serial.readline().decode("ascii")
            except Exception:
                raw = ""
            if raw:
                self.resp_var.set(raw.strip())
                now = time.perf_counter()
                if self.last_time is not None:
                    dt = now - self.last_time
                    if dt > 0:
                        rate = 1.0 / dt
                        self._rate_window.append(rate)
                        if len(self._rate_window) > 1000:
                            self._rate_window.pop(0)
                        self.sample_rate = sum(self._rate_window) / len(self._rate_window)
                        self.rate_label.config(
                            text=f"Sample rate: {self.sample_rate:.1f} Hz"
                        )
                self.last_time = now
                if self.logging and self.log_file:
                    self.log_file.write(raw.lstrip(">"))
                    self.sample_idx += 1
                    self.progress["value"] = self.sample_idx
                    if self.sample_rate:
                        remaining = self.sample_count_var.get() - self.sample_idx
                        secs = math.ceil(remaining / self.sample_rate)
                        self.time_label.config(text=f"Time remaining: {secs}s")
                    if self.sample_idx >= self.sample_count_var.get():
                        self.cancel_logging()
        self.after(10, self._poll_serial)

    def send_command(self) -> None:
        if self.serial and self.serial.is_open:
            cmd = self.cmd_var.get() + "\n"
            self.serial.write(cmd.encode("ascii"))

    def start_logging(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=[("Text", "*.txt")]
        )
        if not path:
            return
        try:
            self.log_file = open(path, "w")
        except OSError as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.sample_idx = 0
        self.logging = True
        self.progress.configure(maximum=self.sample_count_var.get(), value=0)
        self.rec_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)

    def cancel_logging(self) -> None:
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        self.logging = False
        self.rec_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self.time_label.config(text="Time remaining: N/A")


if __name__ == "__main__":
    app = LoggerApp()
    app.mainloop()
