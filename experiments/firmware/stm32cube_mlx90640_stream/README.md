# STM32Cube MLX90640 raw stream

Experimental STM32Cube/HAL firmware for the NUCLEO-H753ZI and MLX90640.

This is a lower-level alternative to the Arduino sketches next to this folder.
It uses STM32 HAL directly so the MLX90640 frame RAM can be read in explicit
16-bit addressed I2C chunks instead of going through
`Adafruit_MLX90640.getFrame()`.

Default hardware assumptions:

- Board: NUCLEO-H753ZI
- Camera I2C: I2C1 on PB9 SDA / PB8 SCL, matching Arduino D14 / D15
- Serial stream: USART3 on PD8 TX / PD9 RX, the ST-LINK virtual COM port
- Sensor address: `0x33`
- Sensor mode: interleaved mode, 16 Hz refresh, 17-bit ADC resolution
- I2C timing: 400 kHz Fast Mode (`0x20D01132`)
- UART baud: `2000000`
- Raw ROI: centered 16-column crop by default, columns 8 through 23

The stream sends `MLXE` EEPROM packets at boot and after refresh-rate changes
for host-side calibration, plus compact `MLXR` raw frame-RAM packets for live
frames. It is still a speed probe first: the host can count packets, inspect the
subpage bit, and verify whether the HAL path can sustain the selected data-ready
cadence.

The firmware also accepts single-byte refresh-rate commands over the same serial
port while streaming:

- `5` selects 16 Hz
- `6` selects 32 Hz
- `7` selects 64 Hz

The viewer's rate dropdown sends those commands for Cube raw mode. The firmware
uses interleaved mode and reads only a centered raw-pixel ROI from the current
subpage rows plus auxiliary registers needed by the host Celsius conversion,
then sends compact `MLXR` packets with interrupt-driven UART transmit. The
refresh command also clears the MLX90640 chess-mode bit so the sensor pattern
matches the row-subpage read. The firmware pulses SCL/SDA as GPIO before I2C
startup to recover the bus if the MLX90640 was reset mid-transaction. If the
camera is not detected at boot, the firmware prints line-level and I2C scan
diagnostics and keeps retrying instead of entering the error handler, so wiring
or power can be fixed without reflashing.

Measure the stream from the project root after flashing:

```powershell
.\.venv\Scripts\python.exe experiments\firmware\stm32cube_mlx90640_stream\tools\capture_mlxr.py COM10 --baud 2000000 --seconds 5
```

When the camera is electrically visible, the firmware reports
`MLX90640_CUBE_I2C_FOUND_0x33`, sends an `MLXE` EEPROM packet, and then streams
`MLXR` frame packets. If `capture_mlxr.py` prints `packets=0` followed by
`status=MLX90640_CUBE_I2C_SCAN_NONE`, the NUCLEO firmware and serial port are
alive but no I2C device is acknowledging on the selected pin pair. Check:

- camera `SDA` to Nucleo `D14 / PB9`
- camera `SCL` to Nucleo `D15 / PB8`
- camera `GND` to Nucleo `GND`
- module power on the breakout's input pin, often `VIN`; do not assume a pin
  labelled `3Vo` is a power input, because on many breakouts it is the regulator
  output

Before each scan, the firmware also drives and releases SCL and SDA independently.
`MLX90640_CUBE_I2C_DRIVE_RESULT_PASS` means the selected NUCLEO pins can pull the
bus low and release it high; a subsequent empty scan then points primarily to
camera power, wiring, continuity, or the camera module. A
`MLX90640_CUBE_I2C_DRIVE_RESULT_FAIL` result points instead to a stuck or shorted
line, the wrong physical header pin, or possible NUCLEO pin damage.

Known-good bench result with interleaved subpage reads, centered 16-column ROI,
minimal auxiliary reads, and interrupt-driven UART packets:

- 16 Hz setting: 75 packets in 5 seconds, 15.77 packets/s, 0 drops, 0 overruns
- 32 Hz setting: 150 packets in 5 seconds, 31.46 packets/s, 0 drops, 0 overruns
- 64 Hz setting: 607 packets in 10 seconds, 62.67 packets/s, 0 drops, 0 overruns
- subpages alternate evenly
- subpage ROI plus required auxiliary read time: about 10.9 ms at 400 kHz I2C
- host Celsius sanity at 64 Hz: 384 finite ROI pixels, ambient about 31.9 C

For comparison, the previous full-width compact packet read took about 18.7 ms
and could not keep up with 64 Hz at valid 400 kHz I2C. A wider centered
24-column ROI reduced read time to about 14.9 ms, but still produced occasional
64 Hz overrun flags in longer captures.

Experimental 1 MHz I2C builds reached about 62.7 packets/s at the 64 Hz setting
with 0 overrun packets, but the EEPROM/ambient data was corrupted on the current
bench wiring or timing setup (ambient around `-230 C`), so the checked-in and
flashed known-good build uses 400 kHz.

Build from this folder with STM32CubeCLT:

```powershell
C:\ST\STM32CubeCLT_1.22.0\CMake\bin\cmake.exe -S . -B build -G Ninja
C:\ST\STM32CubeCLT_1.22.0\CMake\bin\cmake.exe --build build
```

Useful CMake knobs:

```powershell
C:\ST\STM32CubeCLT_1.22.0\CMake\bin\cmake.exe -S . -B build -G Ninja -DMLX_REFRESH_RATE=5 -DMLX_ADC_RESOLUTION=1 -DMLX_I2C_TIMING=0x20D01132 -DMLX_I2C_ANALOG_FILTER=1 -DMLX_I2C_DIGITAL_FILTER=0
```

Refresh-rate codes follow the MLX90640 control register (`5=16 Hz`, `6=32 Hz`,
`7=64 Hz`). ADC-resolution codes are `0=16 bit`, `1=17 bit`, `2=18 bit`,
`3=19 bit`. Known STM32H7 I2C timing values from the STM32 Arduino H7 variant
are `0x1080091A` for 1 MHz Fast Mode Plus and `0x20D01132` for 400 kHz Fast
Mode. `MLX_I2C_ANALOG_FILTER` and `MLX_I2C_DIGITAL_FILTER` can be used for
repeatable signal-integrity experiments. `MLX90640_ROI_WIDTH` and
`MLX90640_ROI_START_COL` can be used to rebuild with a different raw-pixel crop;
the PyPlot viewer infers centered compact ROI widths from the `MLXR` packet
word count. `MLX_I2C_PINSET` can be used for bring-up diagnostics:
`0=I2C1 PB9/PB8 Arduino D14/D15`, `1=I2C1 PB7/PB6`, and `2=I2C2 PF0/PF1`.

Flash with STM32CubeProgrammer:

```powershell
C:\ST\STM32CubeCLT_1.22.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe -c port=SWD -w build\stm32cube_mlx90640_stream.bin 0x08000000 -v -rst
```
