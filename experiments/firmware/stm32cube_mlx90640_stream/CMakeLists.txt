cmake_minimum_required(VERSION 3.22)

set(CMAKE_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/cmake/gcc-arm-none-eabi.cmake")
project(stm32cube_mlx90640_stream C ASM)

set(STM32_CORE "C:/Users/Martin Eliáš/AppData/Local/Arduino15/packages/STMicroelectronics/hardware/stm32/2.12.0")
set(STM32_SYSTEM "${STM32_CORE}/system")
set(STM32_HAL "${STM32_SYSTEM}/Drivers/STM32H7xx_HAL_Driver")
set(STM32_CMSIS "${STM32_SYSTEM}/Drivers/CMSIS")
set(STM32_DEVICE "${STM32_CMSIS}/Device/ST/STM32H7xx")
set(STM32_VARIANT "${STM32_CORE}/variants/STM32H7xx/H742Z(G-I)T_H743Z(G-I)T_H747A(G-I)I_H747I(G-I)T_H750ZBT_H753ZIT_H757AII_H757IIT")
set(MLX_REFRESH_RATE "5" CACHE STRING "MLX90640 refresh-rate code: 5=16 Hz, 6=32 Hz, 7=64 Hz")
set(MLX_ADC_RESOLUTION "1" CACHE STRING "MLX90640 ADC resolution code: 0=16 bit, 1=17 bit, 2=18 bit, 3=19 bit")
set(MLX_I2C_TIMING "0x20D01132" CACHE STRING "STM32H7 I2C timing register value, e.g. 0x20D01132=400 kHz, 0x1080091A=1 MHz")
set(MLX_UART_BAUD "2000000" CACHE STRING "USART3 baud rate for the ST-LINK virtual COM port")

add_executable(${PROJECT_NAME}
    src/main.c
    src/stm32h7xx_it.c
    "${STM32_DEVICE}/Source/Templates/gcc/startup_stm32h753xx.s"
    "${STM32_DEVICE}/Source/Templates/system_stm32h7xx.c"
    "${STM32_HAL}/Src/stm32h7xx_hal.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_cortex.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_dma.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_dma_ex.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_flash.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_flash_ex.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_gpio.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_i2c.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_i2c_ex.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_pwr.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_pwr_ex.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_rcc.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_rcc_ex.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_uart.c"
    "${STM32_HAL}/Src/stm32h7xx_hal_uart_ex.c"
)
set_target_properties(${PROJECT_NAME} PROPERTIES SUFFIX ".elf")

target_include_directories(${PROJECT_NAME} PRIVATE
    src
    "${STM32_SYSTEM}/STM32H7xx"
    "${STM32_HAL}/Inc"
    "C:/Users/Martin Eliáš/AppData/Local/Arduino15/packages/STMicroelectronics/tools/CMSIS/6.2.0/CMSIS/Core/Include"
    "${STM32_CMSIS}/Include"
    "${STM32_DEVICE}/Include"
)

target_compile_definitions(${PROJECT_NAME} PRIVATE
    STM32H753xx
    USE_HAL_DRIVER
    HSE_VALUE=8000000
    MLX_REFRESH_RATE=${MLX_REFRESH_RATE}
    MLX_ADC_RESOLUTION=${MLX_ADC_RESOLUTION}
    MLX_I2C_TIMING=${MLX_I2C_TIMING}
    MLX_UART_BAUD=${MLX_UART_BAUD}
)

target_compile_options(${PROJECT_NAME} PRIVATE
    -mcpu=cortex-m7
    -mthumb
    -mfpu=fpv5-d16
    -mfloat-abi=hard
    -ffunction-sections
    -fdata-sections
    -Wall
    -Wextra
)

target_link_options(${PROJECT_NAME} PRIVATE
    -mcpu=cortex-m7
    -mthumb
    -mfpu=fpv5-d16
    -mfloat-abi=hard
    -T${CMAKE_CURRENT_LIST_DIR}/STM32H753ZITX_FLASH.ld
    -Wl,--gc-sections
    -Wl,-Map=${PROJECT_NAME}.map
    --specs=nano.specs
    --specs=nosys.specs
)

add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${PROJECT_NAME}> ${PROJECT_NAME}.bin
    COMMAND ${CMAKE_SIZE} $<TARGET_FILE:${PROJECT_NAME}>
)
