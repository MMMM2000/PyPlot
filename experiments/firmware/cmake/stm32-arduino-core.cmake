if(NOT DEFINED STM32_CORE OR STM32_CORE STREQUAL "")
    if(DEFINED ENV{STM32_CORE})
        file(TO_CMAKE_PATH "$ENV{STM32_CORE}" STM32_CORE)
    elseif(DEFINED ENV{LOCALAPPDATA})
        file(TO_CMAKE_PATH "$ENV{LOCALAPPDATA}" _stm32_local_appdata)
        file(GLOB _stm32_core_versions LIST_DIRECTORIES true "${_stm32_local_appdata}/Arduino15/packages/STMicroelectronics/hardware/stm32/*")
        if(_stm32_core_versions)
            list(SORT _stm32_core_versions COMPARE NATURAL ORDER DESCENDING)
            list(GET _stm32_core_versions 0 STM32_CORE)
        endif()
    endif()
endif()
set(STM32_CORE "${STM32_CORE}" CACHE PATH "Path to Arduino STM32 core package")

if(NOT STM32_CORE OR NOT EXISTS "${STM32_CORE}/system/Drivers/STM32H7xx_HAL_Driver")
    message(FATAL_ERROR
        "STM32 Arduino core was not found. Install the STMicroelectronics STM32 board package "
        "with Arduino IDE or arduino-cli, or configure with -DSTM32_CORE=<path to .../hardware/stm32/<version>>."
    )
endif()

if(NOT DEFINED STM32_CMSIS_CORE_INCLUDE OR STM32_CMSIS_CORE_INCLUDE STREQUAL "")
    if(DEFINED ENV{STM32_CMSIS_CORE_INCLUDE})
        file(TO_CMAKE_PATH "$ENV{STM32_CMSIS_CORE_INCLUDE}" STM32_CMSIS_CORE_INCLUDE)
    elseif(DEFINED ENV{LOCALAPPDATA})
        file(TO_CMAKE_PATH "$ENV{LOCALAPPDATA}" _stm32_local_appdata)
        file(GLOB _stm32_cmsis_versions LIST_DIRECTORIES true "${_stm32_local_appdata}/Arduino15/packages/STMicroelectronics/tools/CMSIS/*/CMSIS/Core/Include")
        if(_stm32_cmsis_versions)
            list(SORT _stm32_cmsis_versions COMPARE NATURAL ORDER DESCENDING)
            list(GET _stm32_cmsis_versions 0 STM32_CMSIS_CORE_INCLUDE)
        endif()
    endif()
endif()
set(STM32_CMSIS_CORE_INCLUDE "${STM32_CMSIS_CORE_INCLUDE}" CACHE PATH "Path to CMSIS/Core/Include from the Arduino STM32 tools package")

if(NOT STM32_CMSIS_CORE_INCLUDE OR NOT EXISTS "${STM32_CMSIS_CORE_INCLUDE}/core_cm7.h")
    message(FATAL_ERROR
        "CMSIS Core include directory was not found. Install the STMicroelectronics STM32 board package "
        "with Arduino IDE or arduino-cli, or configure with -DSTM32_CMSIS_CORE_INCLUDE=<path to CMSIS/Core/Include>."
    )
endif()

set(STM32_SYSTEM "${STM32_CORE}/system")
set(STM32_HAL "${STM32_SYSTEM}/Drivers/STM32H7xx_HAL_Driver")
set(STM32_CMSIS "${STM32_SYSTEM}/Drivers/CMSIS")
set(STM32_DEVICE "${STM32_CMSIS}/Device/ST/STM32H7xx")
set(STM32_VARIANT "${STM32_CORE}/variants/STM32H7xx/H742Z(G-I)T_H743Z(G-I)T_H747A(G-I)I_H747I(G-I)T_H750ZBT_H753ZIT_H757AII_H757IIT")
