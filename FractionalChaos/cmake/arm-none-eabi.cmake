set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

if(DEFINED ENV{ARM_GCC_ROOT})
    set(_fc_gcc_hints "$ENV{ARM_GCC_ROOT}" "$ENV{ARM_GCC_ROOT}/bin")
else()
    file(GLOB _fc_cube_gcc_dirs
        "$ENV{LOCALAPPDATA}/STM32Cube/bundles/gnu-tools-for-stm32/*/bin")
    list(SORT _fc_cube_gcc_dirs
        COMPARE NATURAL
        ORDER DESCENDING)
    set(_fc_gcc_hints ${_fc_cube_gcc_dirs})
endif()

find_program(CMAKE_C_COMPILER
    NAMES arm-none-eabi-gcc
    HINTS ${_fc_gcc_hints}
    REQUIRED)
find_program(CMAKE_ASM_COMPILER
    NAMES arm-none-eabi-gcc
    HINTS ${_fc_gcc_hints}
    REQUIRED)
find_program(CMAKE_OBJCOPY
    NAMES arm-none-eabi-objcopy
    HINTS ${_fc_gcc_hints}
    REQUIRED)
find_program(CMAKE_SIZE
    NAMES arm-none-eabi-size
    HINTS ${_fc_gcc_hints}
    REQUIRED)
find_program(CMAKE_NM
    NAMES arm-none-eabi-nm
    HINTS ${_fc_gcc_hints}
    REQUIRED)
find_program(CMAKE_OBJDUMP
    NAMES arm-none-eabi-objdump
    HINTS ${_fc_gcc_hints}
    REQUIRED)

set(CMAKE_EXECUTABLE_SUFFIX ".elf")

set(CMAKE_C_FLAGS_INIT
    "-ffunction-sections -fdata-sections -fno-common")
set(CMAKE_ASM_FLAGS_INIT
    "-ffunction-sections -fdata-sections")
set(CMAKE_EXE_LINKER_FLAGS_INIT
    "--specs=nano.specs --specs=nosys.specs -Wl,--gc-sections")

set(CMAKE_C_FLAGS_DEBUG_INIT "-Og -g3")
set(CMAKE_C_FLAGS_RELEASE_INIT "-O3 -g1 -DNDEBUG -flto")
set(CMAKE_EXE_LINKER_FLAGS_RELEASE_INIT "-flto")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
