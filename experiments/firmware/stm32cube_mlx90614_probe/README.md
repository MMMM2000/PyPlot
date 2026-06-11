# STM32Cube MLX90614 probe

Experimental STM32Cube/HAL firmware for the NUCLEO-H753ZI and one MLX90614
single-point IR thermometer module.

Default hardware assumptions:

- Board: NUCLEO-H753ZI
- Sensor I2C: I2C1 on PB9 SDA / PB8 SCL, matching Arduino D14 / D15
- Serial stream: USART3 on PD8 TX / PD9 RX, the ST-LINK virtual COM port
- Sensor address: `0x5A`
- I2C timing: SMBus-safe 100 kHz timing (`0x307075B1`)
- UART baud: `2000000`
- Default sample interval: `100 ms`

The firmware emits one text line per read:

```text
MLX90614,<seq>,<elapsed_ms>,<read_us>,<ambient_c>,<object_c>,<raw_ta>,<raw_to>,<flags>
```

`ambient_c` is the sensor package temperature and `object_c` is the spot
temperature averaged over the sensor field of view. This is not a thermal image.

Runtime interval commands over the same serial port:

- `1` selects 1000 ms
- `2` selects 200 ms
- `3` selects 100 ms
- `4` selects 50 ms
- `5` selects 20 ms
- `6` selects 10 ms
- `7` selects no deliberate delay

Experimental config commands:

- `C` reports EEPROM Config Register1 and decoded IIR/FIR filter codes
- `F` reports that the fast-filter experiment is disabled; it does not write
  EEPROM
- `R` restores the observed original DCI Config Register1 value `0xB7F5`
- `W` puts the sensor to sleep and wakes it by holding SDA low so EEPROM
  filter settings are reloaded

The first tested DCI module reported Config Register1 `0xB7F5` before the
failed fast-filter experiment and `0x9795` after the removed write path. The
module stopped responding usefully to warm targets in that state, so restore
`0xB7F5` with `R` and `W`. Avoid repeated EEPROM writes.

Build from this folder with STM32CubeCLT:

```powershell
C:\ST\STM32CubeCLT_1.21.0\CMake\bin\cmake.exe -S . -B build -G Ninja
C:\ST\STM32CubeCLT_1.21.0\CMake\bin\cmake.exe --build build
```

Flash with STM32CubeProgrammer:

```powershell
C:\ST\STM32CubeCLT_1.21.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe -c port=SWD -w build\stm32cube_mlx90614_probe.bin 0x08000000 -v -rst
```

Capture from the project root:

```powershell
.\.venv\Scripts\python.exe -m serial.tools.miniterm COM10 2000000
```
