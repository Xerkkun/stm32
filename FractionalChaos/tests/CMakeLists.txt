cmake_minimum_required(VERSION 3.20)

project(fractional_chaos_host_tests LANGUAGES C)

option(FC_ENABLE_SANITIZERS "Enable AddressSanitizer and UBSan" OFF)

if(FC_ENABLE_SANITIZERS AND
   (CMAKE_C_COMPILER_ID MATCHES "GNU|Clang"))
    include(CheckCSourceCompiles)
    set(_fc_saved_required_flags "${CMAKE_REQUIRED_FLAGS}")
    set(_fc_saved_required_link_options "${CMAKE_REQUIRED_LINK_OPTIONS}")
    set(CMAKE_REQUIRED_FLAGS
        "${CMAKE_REQUIRED_FLAGS} -fsanitize=address,undefined")
    set(CMAKE_REQUIRED_LINK_OPTIONS
        "${CMAKE_REQUIRED_LINK_OPTIONS};-fsanitize=address,undefined")
    check_c_source_compiles(
        "int main(void) { return 0; }"
        FC_SANITIZERS_AVAILABLE)
    set(CMAKE_REQUIRED_FLAGS "${_fc_saved_required_flags}")
    set(CMAKE_REQUIRED_LINK_OPTIONS
        "${_fc_saved_required_link_options}")
    if(NOT FC_SANITIZERS_AVAILABLE)
        message(FATAL_ERROR
            "FC_ENABLE_SANITIZERS=ON, but this compiler lacks "
            "the ASan/UBSan runtime libraries")
    endif()
endif()

add_library(fractional_chaos STATIC
    ../common/fractional_chaos.c
)

target_include_directories(fractional_chaos PUBLIC
    ../common
)

target_compile_features(fractional_chaos PUBLIC c_std_11)

if(MSVC)
    target_compile_options(fractional_chaos PRIVATE /W4)
else()
    target_compile_options(fractional_chaos PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Wconversion
        -Wshadow
        -Wdouble-promotion
        -Werror
        -fno-fast-math
        -ffp-contract=off
    )
endif()

add_executable(fractional_chaos_tests
    test_fractional_chaos.c
)
target_link_libraries(fractional_chaos_tests PRIVATE fractional_chaos)

add_executable(fractional_reference_dump
    reference_dump.c
)
target_link_libraries(fractional_reference_dump PRIVATE fractional_chaos)

add_executable(fractional_transport_tests
    test_transport.c
    ../transport/fc_protocol.c
    ../transport/fc_shared_queue.c
)
target_include_directories(fractional_transport_tests PRIVATE
    ../transport
)
target_compile_features(fractional_transport_tests PRIVATE c_std_11)

if(MSVC)
    target_compile_options(fractional_chaos_tests PRIVATE /W4)
    target_compile_options(fractional_reference_dump PRIVATE /W4)
    target_compile_options(fractional_transport_tests PRIVATE /W4)
else()
    foreach(target
            fractional_chaos_tests
            fractional_reference_dump
            fractional_transport_tests)
        target_compile_options(${target} PRIVATE
            -Wall
            -Wextra
            -Wpedantic
            -Wconversion
            -Wshadow
            -Wdouble-promotion
            -Werror
            -fno-fast-math
            -ffp-contract=off
        )
    endforeach()
endif()

if(NOT MSVC)
    target_link_libraries(fractional_chaos PUBLIC m)
endif()

if(FC_ENABLE_SANITIZERS AND
   (CMAKE_C_COMPILER_ID MATCHES "GNU|Clang"))
    target_compile_options(fractional_chaos PRIVATE
        -fsanitize=address,undefined
        -fno-omit-frame-pointer
    )
    foreach(target fractional_chaos_tests fractional_reference_dump)
        target_compile_options(${target} PRIVATE
            -fsanitize=address,undefined
            -fno-omit-frame-pointer
        )
        target_link_options(${target} PRIVATE
            -fsanitize=address,undefined
        )
    endforeach()
endif()

enable_testing()
add_test(NAME fractional_chaos_c
    COMMAND fractional_chaos_tests
)
add_test(NAME fractional_transport_c
    COMMAND fractional_transport_tests
)

find_package(Python3 COMPONENTS Interpreter)
if(Python3_Interpreter_FOUND)
    add_test(NAME fractional_chaos_python_reference
        COMMAND
            ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/reference_check.py
            $<TARGET_FILE:fractional_reference_dump>
    )
    add_test(NAME fractional_uart_decoder
        COMMAND
            ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/test_decode_uart.py
    )
    add_test(NAME fractional_generated_tables
        COMMAND
            ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/check_generated_tables.py
    )
endif()
