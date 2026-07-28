set(FC_H755_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")
set(FC_CUBEH7_ROOT
    "${FC_H755_ROOT}/vendor/STM32CubeH7"
    CACHE PATH "Ruta de STM32CubeH7 v1.13.0")

option(FC_H755_CLOCK_480
    "Usar 480/240 MHz; requiere que la placa se haya modificado físicamente a LDO"
    OFF)
option(FC_H755_LDO_MODIFICATION_CONFIRMED
    "Confirmación explícita de que NUCLEO-H755ZI-Q se modificó físicamente a LDO"
    OFF)

if(FC_H755_CLOCK_480 AND NOT FC_H755_LDO_MODIFICATION_CONFIRMED)
    message(FATAL_ERROR
        "El perfil 480/240 MHz exige "
        "-DFC_H755_LDO_MODIFICATION_CONFIRMED=ON. "
        "La NUCLEO-H755ZI-Q de fábrica usa DIRECT_SMPS.")
endif()

set(_fc_h755_device
    "${FC_CUBEH7_ROOT}/Drivers/CMSIS/Device/ST/STM32H7xx")
set(_fc_h755_cmsis
    "${FC_CUBEH7_ROOT}/Drivers/CMSIS")
set(_fc_h755_hal
    "${FC_CUBEH7_ROOT}/Drivers/STM32H7xx_HAL_Driver")
set(_fc_h755_cmsis_core "${_fc_h755_cmsis}/Include")
if(NOT EXISTS "${_fc_h755_cmsis_core}/core_cm7.h")
    message(FATAL_ERROR
        "STM32CubeH7 v1.13.0 no contiene Drivers/CMSIS/Include/core_cm7.h. "
        "Ejecute tools/bootstrap.ps1 antes de compilar H755.")
endif()
set(_fc_h755_startup
    "${_fc_h755_device}/Source/Templates/gcc/startup_stm32h755xx.s")
set(_fc_h755_system
    "${_fc_h755_device}/Source/Templates/system_stm32h7xx_dualcore_boot_cm4_cm7.c")

foreach(_required
        "${_fc_h755_startup}"
        "${_fc_h755_system}"
        "${_fc_h755_device}/Include/stm32h755xx.h"
        "${_fc_h755_hal}/Src/stm32h7xx_hal.c")
    if(NOT EXISTS "${_required}")
        message(FATAL_ERROR
            "STM32CubeH7 v1.13.0 incompleto; falta ${_required}. "
            "Ejecute tools/bootstrap.ps1.")
    endif()
endforeach()

set(_fc_h755_includes
    "${CMAKE_CURRENT_LIST_DIR}/include"
    "${FC_H755_ROOT}/common"
    "${FC_H755_ROOT}/generated"
    "${FC_H755_ROOT}/transport"
    "${_fc_h755_hal}/Inc"
    "${_fc_h755_hal}/Inc/Legacy"
    "${_fc_h755_device}/Include"
    "${_fc_h755_cmsis_core}")

set(_fc_h755_hal_common
    "${_fc_h755_hal}/Src/stm32h7xx_hal.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_cortex.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_hsem.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_pwr.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_pwr_ex.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_rcc.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_rcc_ex.c")

set(_fc_h755_cm7_sources
    "${CMAKE_CURRENT_LIST_DIR}/cm7/src/main.c"
    "${CMAKE_CURRENT_LIST_DIR}/cm7/src/stm32h7xx_it.c"
    "${CMAKE_CURRENT_LIST_DIR}/shared/h755_shared_memory.c"
    "${CMAKE_CURRENT_LIST_DIR}/shared/h755_syscalls.c"
    "${FC_H755_ROOT}/common/fractional_chaos.c"
    "${FC_H755_ROOT}/transport/fc_shared_queue.c"
    "${_fc_h755_startup}"
    "${_fc_h755_system}"
    ${_fc_h755_hal_common})

set(_fc_h755_cm4_sources
    "${CMAKE_CURRENT_LIST_DIR}/cm4/src/main.c"
    "${CMAKE_CURRENT_LIST_DIR}/cm4/src/stm32h7xx_hal_msp.c"
    "${CMAKE_CURRENT_LIST_DIR}/cm4/src/stm32h7xx_it.c"
    "${CMAKE_CURRENT_LIST_DIR}/shared/h755_shared_memory.c"
    "${CMAKE_CURRENT_LIST_DIR}/shared/h755_syscalls.c"
    "${FC_H755_ROOT}/transport/fc_protocol.c"
    "${FC_H755_ROOT}/transport/fc_shared_queue.c"
    "${_fc_h755_startup}"
    "${_fc_h755_system}"
    ${_fc_h755_hal_common}
    "${_fc_h755_hal}/Src/stm32h7xx_hal_dma.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_gpio.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_uart.c"
    "${_fc_h755_hal}/Src/stm32h7xx_hal_uart_ex.c")

function(fc_h755_apply_common target core linker_script)
    target_include_directories(${target} PRIVATE
        ${_fc_h755_includes}
        "${CMAKE_CURRENT_LIST_DIR}/${core}/include")
    target_compile_features(${target} PRIVATE c_std_11)
    target_compile_definitions(${target} PRIVATE
        STM32H755xx
        USE_HAL_DRIVER
        USER_VECT_TAB_ADDRESS)

    if(FC_H755_CLOCK_480)
        target_compile_definitions(${target} PRIVATE
            USE_PWR_LDO_SUPPLY
            FC_H755_CLOCK_480=1
            FC_H755_LDO_MODIFICATION_CONFIRMED=1)
    else()
        target_compile_definitions(${target} PRIVATE
            USE_PWR_DIRECT_SMPS_SUPPLY
            FC_H755_CLOCK_480=0)
    endif()

    if(core STREQUAL "cm7")
        set(_core_options
            -mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard)
        target_compile_definitions(${target} PRIVATE CORE_CM7)
    else()
        set(_core_options
            -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard)
        target_compile_definitions(${target} PRIVATE CORE_CM4)
    endif()

    target_compile_options(${target} PRIVATE
        ${_core_options}
        $<$<COMPILE_LANGUAGE:C>:
            -Wall
            -Wextra
            -Wpedantic
            -fno-fast-math
            -ffp-contract=off>)
    target_link_options(${target} PRIVATE
        ${_core_options}
        "-T${linker_script}"
        "-Wl,-Map=$<TARGET_FILE_DIR:${target}>/${target}.map"
        "-Wl,--cref"
        "-Wl,--print-memory-usage")
    set_target_properties(${target} PROPERTIES
        SUFFIX ".elf"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")

    if(CMAKE_OBJCOPY)
        add_custom_command(TARGET ${target} POST_BUILD
            COMMAND "${CMAKE_OBJCOPY}" -O ihex
                "$<TARGET_FILE:${target}>"
                "$<TARGET_FILE_DIR:${target}>/${target}.hex"
            COMMAND "${CMAKE_OBJCOPY}" -O binary
                "$<TARGET_FILE:${target}>"
                "$<TARGET_FILE_DIR:${target}>/${target}.bin"
            VERBATIM)
    endif()
    if(CMAKE_SIZE)
        add_custom_command(TARGET ${target} POST_BUILD
            COMMAND "${CMAKE_SIZE}" "$<TARGET_FILE:${target}>"
            VERBATIM)
    endif()
endfunction()

function(fc_h755_add_m7_target
        target system_id method_id table_source table_symbol)
    add_executable(${target}
        ${_fc_h755_cm7_sources}
        "${FC_H755_ROOT}/generated/${table_source}")
    target_compile_definitions(${target} PRIVATE
        FC_SYSTEM_ID=${system_id}
        FC_METHOD_ID=${method_id}
        FC_REQUIRE_PRECOMPUTED_TABLES=1
        FC_PRECOMPUTED_TABLES_SYMBOL=${table_symbol})
    target_link_libraries(${target} PRIVATE m)
    fc_h755_apply_common(
        ${target}
        cm7
        "${CMAKE_CURRENT_LIST_DIR}/linker/STM32H755ZITX_FLASH_CM7.ld")
endfunction()

fc_h755_add_m7_target(
    h755_m7_lorenz_efork3 0 0
    fc_lorenz_efork3.c FC_LORENZ_EFORK_TABLES)
fc_h755_add_m7_target(
    h755_m7_lorenz_gl 0 1
    fc_lorenz_gl.c FC_LORENZ_GL_TABLES)
fc_h755_add_m7_target(
    h755_m7_rossler_efork3 1 0
    fc_rossler_efork3.c FC_ROSSLER_EFORK_TABLES)
fc_h755_add_m7_target(
    h755_m7_rossler_gl 1 1
    fc_rossler_gl.c FC_ROSSLER_GL_TABLES)
fc_h755_add_m7_target(
    h755_m7_chen_efork3 2 0
    fc_chen_efork3.c FC_CHEN_EFORK_TABLES)
fc_h755_add_m7_target(
    h755_m7_chen_gl 2 1
    fc_chen_gl.c FC_CHEN_GL_TABLES)

add_executable(h755_m4_uart ${_fc_h755_cm4_sources})
fc_h755_apply_common(
    h755_m4_uart
    cm4
    "${CMAKE_CURRENT_LIST_DIR}/linker/STM32H755ZITX_FLASH_CM4.ld")
