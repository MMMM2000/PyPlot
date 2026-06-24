set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)

set(TOOLCHAIN_ROOT "C:/ST/STM32CubeCLT_1.21.0/GNU-tools-for-STM32/bin")

set(CMAKE_C_COMPILER "${TOOLCHAIN_ROOT}/arm-none-eabi-gcc.exe")
set(CMAKE_ASM_COMPILER "${TOOLCHAIN_ROOT}/arm-none-eabi-gcc.exe")
set(CMAKE_OBJCOPY "${TOOLCHAIN_ROOT}/arm-none-eabi-objcopy.exe")
set(CMAKE_SIZE "${TOOLCHAIN_ROOT}/arm-none-eabi-size.exe")

set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
