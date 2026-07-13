cmake_minimum_required(VERSION 3.22)

set(CMAKE_TOOLCHAIN_FILE "${CMAKE_CURRENT_LIST_DIR}/cmake/gcc-arm-none-eabi.cmake")
project(stm32cube_mlx90614_probe C ASM)

include("${CMAKE_CURRENT_LIST_DIR}/../cmake/stm32-arduino-core.cmake")

set(MLX_I2C_TIMING "0x307075B1" CACHE STRING "STM32H7 I2C timing register value for MLX90614 SMBus, default about 100 kHz")
set(MLX_UART_BAUD "2000000" CACHE STRING "USART3 baud rate for the ST-LINK virtual COM port")
set(MLX_SAMPLE_INTERVAL_MS "100" CACHE STRING "Default MLX90614 serial sample interval in milliseconds")

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
    "${STM32_CMSIS_CORE_INCLUDE}"
    "${STM32_CMSIS}/Include"
    "${STM32_DEVICE}/Include"
)

target_compile_definitions(${PROJECT_NAME} PRIVATE
    STM32H753xx
    USE_HAL_DRIVER
    HSE_VALUE=8000000
    MLX_I2C_TIMING=${MLX_I2C_TIMING}
    MLX_UART_BAUD=${MLX_UART_BAUD}
    MLX_SAMPLE_INTERVAL_MS=${MLX_SAMPLE_INTERVAL_MS}
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
