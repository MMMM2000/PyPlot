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
- Sensor mode: chess mode, 16 Hz refresh, 17-bit ADC resolution
- I2C timing: 400 kHz Fast Mode (`0x20D01132`)
- UART baud: `2000000`

The stream sends repeated `MLXE` EEPROM packets for host-side calibration and
`MLXR` raw frame-RAM packets for live frames. It is still a speed probe first:
the host can count packets, inspect the subpage bit, and verify whether the HAL
path can sustain the sensor's 16 Hz data-ready cadence.

The firmware also accepts single-byte refresh-rate commands over the same serial
port while streaming:

- `5` selects 16 Hz
- `6` selects 32 Hz
- `7` selects 64 Hz

The viewer's rate dropdown sends those commands for Cube raw mode. Bench testing
with valid 400 kHz I2C calibration showed 16 Hz is clean, while 32 Hz and 64 Hz
both top out around 22 packets/s and report sensor overrun because each full
frame-RAM read takes about 34.8 ms.

Measure the stream from the project root after flashing:

```powershell
.\.venv\Scripts\python.exe experiments\firmware\stm32cube_mlx90640_stream\tools\capture_mlxr.py COM10 --baud 2000000 --seconds 5
```

Known-good bench result from the first Cube/HAL bring-up:

- 79 packets in 5 seconds at 16 Hz
- 15.77 to 15.79 packets/s
- 0 dropped sequence numbers
- 0 overrun packets
- subpages alternate evenly
- frame RAM read time: about 34.8 ms at 400 kHz I2C, while still sustaining
  15.77 packets/s on the bench

Experimental faster refresh results at 400 kHz I2C:

- 32 Hz setting: about 22.45 packets/s, all packets flagged overrun
- 64 Hz setting: about 22.24 packets/s, all packets flagged overrun

Build from this folder with STM32CubeCLT:

```powershell
C:\ST\STM32CubeCLT_1.21.0\CMake\bin\cmake.exe -S . -B build -G Ninja
C:\ST\STM32CubeCLT_1.21.0\CMake\bin\cmake.exe --build build
```

Useful CMake knobs:

```powershell
C:\ST\STM32CubeCLT_1.21.0\CMake\bin\cmake.exe -S . -B build -G Ninja -DMLX_REFRESH_RATE=5 -DMLX_ADC_RESOLUTION=1 -DMLX_I2C_TIMING=0x20D01132
```

Refresh-rate codes follow the MLX90640 control register (`5=16 Hz`, `6=32 Hz`,
`7=64 Hz`). ADC-resolution codes are `0=16 bit`, `1=17 bit`, `2=18 bit`,
`3=19 bit`. Known STM32H7 I2C timing values from the STM32 Arduino H7 variant
are `0x1080091A` for 1 MHz Fast Mode Plus and `0x20D01132` for 400 kHz Fast
Mode.

Flash with STM32CubeProgrammer:

```powershell
C:\ST\STM32CubeCLT_1.21.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe -c port=SWD -w build\stm32cube_mlx90640_stream.bin 0x08000000 -v -rst
```
