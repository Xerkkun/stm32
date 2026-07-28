cmake_minimum_required(VERSION 3.22)

set(FC_F746_STANDALONE_BUILD OFF)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    set(FC_F746_STANDALONE_BUILD ON)
    set(CMAKE_SYSTEM_NAME Generic)
    set(CMAKE_SYSTEM_PROCESSOR arm)
    set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
    project(fractional_chaos_f746 LANGUAGES C ASM)
elseif(NOT CMAKE_ASM_COMPILER)
    enable_language(ASM)
endif()

get_filename_component(
    FC_PROJECT_ROOT
    "${CMAKE_CURRENT_LIST_DIR}/../.."
    ABSOLUTE)

set(
    FC_F746_DONOR_ROOT
    "${FC_PROJECT_ROOT}/../Lorenz_efork"
    CACHE PATH
    "STM32CubeF7 donor containing Drivers and the F746 startup/system files")
set(
    FC_F746_OUTPUT_DECIMATION
    "512"
    CACHE STRING
    "Transmit one state frame every N solver steps")
option(
    FC_F746_BENCHMARK_MODE
    "Store 10000 solver-only DWT counts, then emit timing blocks"
    OFF)

if(NOT FC_F746_OUTPUT_DECIMATION MATCHES "^[1-9][0-9]*$")
    message(FATAL_ERROR "FC_F746_OUTPUT_DECIMATION must be a positive integer")
endif()

set(FC_F7_HAL "${FC_F746_DONOR_ROOT}/Drivers/STM32F7xx_HAL_Driver")
set(FC_F7_CMSIS "${FC_F746_DONOR_ROOT}/Drivers/CMSIS")

if(EXISTS
        "${FC_F746_DONOR_ROOT}/Core/Startup/startup_stm32f746zgtx.s")
    set(FC_F7_APP_ROOT "${FC_F746_DONOR_ROOT}/Core")
elseif(EXISTS
        "${FC_F746_DONOR_ROOT}/Startup/startup_stm32f746zgtx.s")
    set(FC_F7_APP_ROOT "${FC_F746_DONOR_ROOT}")
else()
    message(FATAL_ERROR
        "FC_F746_DONOR_ROOT does not contain the F746 GCC startup file")
endif()

set(FC_F7_STARTUP
    "${FC_F7_APP_ROOT}/Startup/startup_stm32f746zgtx.s")
set(FC_F7_SYSTEM
    "${FC_F7_APP_ROOT}/Src/system_stm32f7xx.c")

foreach(required_path
        "${FC_F7_HAL}/Inc/stm32f7xx_hal.h"
        "${FC_F7_CMSIS}/Device/ST/STM32F7xx/Include/stm32f746xx.h"
        "${FC_F7_STARTUP}"
        "${FC_F7_SYSTEM}")
    if(NOT EXISTS "${required_path}")
        message(FATAL_ERROR
            "Missing STM32CubeF7 donor file: ${required_path}. "
            "Set FC_F746_DONOR_ROOT to a complete F746 Cube project.")
    endif()
endforeach()

if(NOT CMAKE_OBJCOPY)
    get_filename_component(FC_COMPILER_DIRECTORY "${CMAKE_C_COMPILER}" DIRECTORY)
    find_program(
        CMAKE_OBJCOPY
        NAMES arm-none-eabi-objcopy
        HINTS "${FC_COMPILER_DIRECTORY}")
endif()

set(FC_F746_CPU_OPTIONS
    -mcpu=cortex-m7
    -mfpu=fpv5-sp-d16
    -mfloat-abi=hard
    -mthumb)

set(FC_F746_RUNTIME_LINK_OPTIONS)
if(FC_F746_STANDALONE_BUILD)
    list(APPEND FC_F746_RUNTIME_LINK_OPTIONS
        --specs=nano.specs
        --specs=nosys.specs)
endif()

function(
    fc_add_f746_firmware
    target_name
    system_id
    method_id
    table_symbol
    table_source
    fixed_point)

    if(TARGET "${target_name}")
        message(FATAL_ERROR "Duplicate F746 firmware target: ${target_name}")
    endif()
    if(NOT EXISTS "${table_source}")
        message(FATAL_ERROR
            "Generated table source does not exist: ${table_source}")
    endif()

    add_executable(
        "${target_name}"
        src/main.c
        src/stm32f7xx_hal_msp.c
        src/stm32f7xx_it.c
        src/syscalls.c
        "${FC_PROJECT_ROOT}/common/fractional_chaos.c"
        "${FC_PROJECT_ROOT}/common/fractional_chaos_fixed.c"
        "${FC_PROJECT_ROOT}/transport/fc_protocol.c"
        "${table_source}"
        "${FC_F7_SYSTEM}"
        "${FC_F7_STARTUP}"
        "${FC_F7_HAL}/Src/stm32f7xx_hal.c"
        "${FC_F7_HAL}/Src/stm32f7xx_hal_cortex.c"
        "${FC_F7_HAL}/Src/stm32f7xx_hal_dma.c"
        "${FC_F7_HAL}/Src/stm32f7xx_hal_gpio.c"
        "${FC_F7_HAL}/Src/stm32f7xx_hal_pwr_ex.c"
        "${FC_F7_HAL}/Src/stm32f7xx_hal_rcc.c"
        "${FC_F7_HAL}/Src/stm32f7xx_hal_rcc_ex.c"
        "${FC_F7_HAL}/Src/stm32f7xx_hal_uart.c")

    set_target_properties(
        "${target_name}"
        PROPERTIES
            C_STANDARD 11
            C_STANDARD_REQUIRED YES
            C_EXTENSIONS NO
            LINK_DEPENDS
                "${CMAKE_CURRENT_LIST_DIR}/STM32F746ZGTX_FLASH.ld"
            OUTPUT_NAME "${target_name}"
            SUFFIX ".elf"
            RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")

    target_include_directories(
        "${target_name}"
        PRIVATE
            "${CMAKE_CURRENT_LIST_DIR}/include"
            "${FC_PROJECT_ROOT}/common"
            "${FC_PROJECT_ROOT}/transport"
            "${FC_F7_HAL}/Inc"
            "${FC_F7_HAL}/Inc/Legacy"
            "${FC_F7_CMSIS}/Device/ST/STM32F7xx/Include"
            "${FC_F7_CMSIS}/Include")

    target_compile_definitions(
        "${target_name}"
        PRIVATE
            STM32F746xx
            USE_HAL_DRIVER
            FC_REQUIRE_PRECOMPUTED_TABLES=1
            FC_F746_SYSTEM=${system_id}
            FC_F746_METHOD=${method_id}
            FC_F746_FIXED_POINT=${fixed_point}
            FC_F746_PRECOMPUTED_TABLES=${table_symbol}
            FC_F746_OUTPUT_DECIMATION=${FC_F746_OUTPUT_DECIMATION}
            FC_F746_BENCHMARK_MODE=$<BOOL:${FC_F746_BENCHMARK_MODE}>)

    target_compile_options(
        "${target_name}"
        PRIVATE
            ${FC_F746_CPU_OPTIONS}
            -fdata-sections
            -ffunction-sections
            -fno-common
            -fno-fast-math
            -ffp-contract=off
            -Wall
            -Wextra
            $<$<CONFIG:Debug>:-Og>
            $<$<CONFIG:Debug>:-g3>
            $<$<NOT:$<CONFIG:Debug>>:-O3>)

    target_link_libraries("${target_name}" PRIVATE m)

    target_link_options(
        "${target_name}"
        PRIVATE
            ${FC_F746_CPU_OPTIONS}
            ${FC_F746_RUNTIME_LINK_OPTIONS}
            "-T${CMAKE_CURRENT_LIST_DIR}/STM32F746ZGTX_FLASH.ld"
            -Wl,--gc-sections
            -Wl,--cref
            "-Wl,-Map=${CMAKE_BINARY_DIR}/${target_name}.map"
            -Wl,--print-memory-usage)

    if(CMAKE_OBJCOPY)
        add_custom_command(
            TARGET "${target_name}"
            POST_BUILD
            COMMAND "${CMAKE_OBJCOPY}" -O ihex
                    "$<TARGET_FILE:${target_name}>"
                    "$<TARGET_FILE_DIR:${target_name}>/${target_name}.hex"
            COMMAND "${CMAKE_OBJCOPY}" -O binary
                    "$<TARGET_FILE:${target_name}>"
                    "$<TARGET_FILE_DIR:${target_name}>/${target_name}.bin"
            COMMENT "Generating ${target_name} HEX and BIN images")
    endif()
endfunction()

set(FC_LF_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_lorenz_efork3.c")
set(FC_LG_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_lorenz_gl.c")
set(FC_LM_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_lorenz_m2sfrk.c")
set(FC_RF_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_rossler_efork3.c")
set(FC_RG_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_rossler_gl.c")
set(FC_RM_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_rossler_m2sfrk.c")
set(FC_CF_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_chen_efork3.c")
set(FC_CG_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_chen_gl.c")
set(FC_CM_TABLE_SOURCE
    "${FC_PROJECT_ROOT}/generated/fc_chen_m2sfrk.c")

fc_add_f746_firmware(
    f746_lorenz_efork3 0 0
    FC_LORENZ_EFORK_TABLES "${FC_LF_TABLE_SOURCE}" 0)
fc_add_f746_firmware(
    f746_lorenz_gl 0 1
    FC_LORENZ_GL_TABLES "${FC_LG_TABLE_SOURCE}" 0)
fc_add_f746_firmware(
    f746_lorenz_m2sfrk 0 2
    FC_LORENZ_M2SFRK_TABLES "${FC_LM_TABLE_SOURCE}" 0)
fc_add_f746_firmware(
    f746_rossler_efork3 1 0
    FC_ROSSLER_EFORK_TABLES "${FC_RF_TABLE_SOURCE}" 0)
fc_add_f746_firmware(
    f746_rossler_gl 1 1
    FC_ROSSLER_GL_TABLES "${FC_RG_TABLE_SOURCE}" 0)
fc_add_f746_firmware(
    f746_rossler_m2sfrk 1 2
    FC_ROSSLER_M2SFRK_TABLES "${FC_RM_TABLE_SOURCE}" 0)
fc_add_f746_firmware(
    f746_chen_efork3 2 0
    FC_CHEN_EFORK_TABLES "${FC_CF_TABLE_SOURCE}" 0)
fc_add_f746_firmware(
    f746_chen_gl 2 1
    FC_CHEN_GL_TABLES "${FC_CG_TABLE_SOURCE}" 0)
fc_add_f746_firmware(
    f746_chen_m2sfrk 2 2
    FC_CHEN_M2SFRK_TABLES "${FC_CM_TABLE_SOURCE}" 0)

fc_add_f746_firmware(
    f746_lorenz_efork3_fixed 0 0
    FC_LORENZ_EFORK_TABLES "${FC_LF_TABLE_SOURCE}" 1)
fc_add_f746_firmware(
    f746_lorenz_gl_fixed 0 1
    FC_LORENZ_GL_TABLES "${FC_LG_TABLE_SOURCE}" 1)
fc_add_f746_firmware(
    f746_lorenz_m2sfrk_fixed 0 2
    FC_LORENZ_M2SFRK_TABLES "${FC_LM_TABLE_SOURCE}" 1)
fc_add_f746_firmware(
    f746_rossler_efork3_fixed 1 0
    FC_ROSSLER_EFORK_TABLES "${FC_RF_TABLE_SOURCE}" 1)
fc_add_f746_firmware(
    f746_rossler_gl_fixed 1 1
    FC_ROSSLER_GL_TABLES "${FC_RG_TABLE_SOURCE}" 1)
fc_add_f746_firmware(
    f746_rossler_m2sfrk_fixed 1 2
    FC_ROSSLER_M2SFRK_TABLES "${FC_RM_TABLE_SOURCE}" 1)
fc_add_f746_firmware(
    f746_chen_efork3_fixed 2 0
    FC_CHEN_EFORK_TABLES "${FC_CF_TABLE_SOURCE}" 1)
fc_add_f746_firmware(
    f746_chen_gl_fixed 2 1
    FC_CHEN_GL_TABLES "${FC_CG_TABLE_SOURCE}" 1)
fc_add_f746_firmware(
    f746_chen_m2sfrk_fixed 2 2
    FC_CHEN_M2SFRK_TABLES "${FC_CM_TABLE_SOURCE}" 1)
