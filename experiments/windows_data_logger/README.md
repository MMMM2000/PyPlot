# Windows Native Data Logger

This experiment provides a simple data logger using Tkinter and PySerial to
offer a Windows-native look and feel. The interface mirrors the PyQt6 logger:

- Select and refresh available serial ports
- Connect and send commands
- Record a fixed number of samples to a text file
- Display live sample rate and remaining time
- Cancel an active recording

Run the logger on Windows with:

```bash
python logger.py
```

The script depends on the `pyserial` package which is listed in the project
requirements.
