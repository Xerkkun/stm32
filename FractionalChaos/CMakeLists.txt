cmake_minimum_required(VERSION 3.22)

set(
    FC_PLATFORM
    "host"
    CACHE STRING
    "Plataforma que se compila: host, f746 o h755")
set_property(CACHE FC_PLATFORM PROPERTY STRINGS host f746 h755)

if(FC_PLATFORM STREQUAL "host")
    project(fractional_chaos LANGUAGES C)
    enable_testing()
    add_subdirectory(tests)
elseif(FC_PLATFORM STREQUAL "f746")
    project(fractional_chaos LANGUAGES C ASM)
    add_subdirectory(platform/f746)
elseif(FC_PLATFORM STREQUAL "h755")
    project(fractional_chaos LANGUAGES C ASM)
    add_subdirectory(platform/h755)
else()
    message(FATAL_ERROR
        "FC_PLATFORM='${FC_PLATFORM}' no es válido; use host, f746 o h755.")
endif()
