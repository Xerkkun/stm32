cmake_minimum_required(VERSION 3.22)

set(
    FC_PLATFORM
    "host"
    CACHE STRING
    "Plataforma que se compila: host, f746 o h755")
set_property(CACHE FC_PLATFORM PROPERTY STRINGS host f746 h755)

set(
    FC_SELECTED_SYSTEM_MANIFEST
    "${CMAKE_CURRENT_SOURCE_DIR}/validation/selected_system_manifests_v1.json")
if(NOT EXISTS "${FC_SELECTED_SYSTEM_MANIFEST}")
    message(FATAL_ERROR
        "Falta el manifiesto seleccionado: ${FC_SELECTED_SYSTEM_MANIFEST}")
endif()
file(
    SHA256
    "${FC_SELECTED_SYSTEM_MANIFEST}"
    FC_SELECTED_SYSTEM_MANIFEST_SHA256)
add_compile_definitions(
    FC_SELECTED_SYSTEM_MANIFEST_SHA256="${FC_SELECTED_SYSTEM_MANIFEST_SHA256}")

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
