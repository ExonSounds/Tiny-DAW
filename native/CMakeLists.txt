# Tiny DAW's plugin host (VST3, and VST2): a Python module (daw/audio/vst3host.*.pyd) built on Steinberg's
# VST3 SDK (MIT) and pybind11 (BSD). Both are fetched from GitHub when configuring; see build.ps1.
cmake_minimum_required(VERSION 3.25)
project(vst3host LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")  # no separate C++ runtime to ship

include(FetchContent)

# --- the VST3 SDK: only what hosting needs (no VSTGUI, no examples) ---
set(SMTG_ENABLE_VSTGUI_SUPPORT OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VST3_PLUGIN_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VST3_HOSTING_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_ADD_VST3_UTILITIES OFF CACHE BOOL "" FORCE)
set(SMTG_RUN_VST_VALIDATOR OFF CACHE BOOL "" FORCE)
set(SMTG_CREATE_PLUGIN_LINK OFF CACHE BOOL "" FORCE)
set(SMTG_USE_STATIC_CRT ON CACHE BOOL "" FORCE)
FetchContent_Declare(vst3sdk
    GIT_REPOSITORY https://github.com/steinbergmedia/vst3sdk.git
    GIT_TAG v3.8.1_build_84
    GIT_SHALLOW TRUE
    GIT_SUBMODULES base cmake pluginterfaces public.sdk
    EXCLUDE_FROM_ALL)  # (only the libraries the module links are built)
FetchContent_MakeAvailable(vst3sdk)

# --- pybind11 ---
set(PYBIND11_FINDPYTHON ON)
FetchContent_Declare(pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG v2.13.6
    GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(pybind11)

pybind11_add_module(vst3host
    vst3host.cpp
    vst2plugin.cpp  # (VST2 plugins: vst2.h, the interface declared from its public behaviour)
    # (hosting helpers the SDK's sdk_hosting library leaves out)
    ${vst3sdk_SOURCE_DIR}/public.sdk/source/vst/hosting/plugprovider.cpp
    ${vst3sdk_SOURCE_DIR}/public.sdk/source/vst/hosting/module_win32.cpp
    ${vst3sdk_SOURCE_DIR}/public.sdk/source/common/memorystream.cpp)
target_link_libraries(vst3host PRIVATE sdk_hosting sdk_common base pluginterfaces)
target_compile_definitions(vst3host PRIVATE UNICODE _UNICODE NOMINMAX)
if(MSVC)
    target_compile_options(vst3host PRIVATE /W3 /utf-8)
endif()

# the module goes next to the Python code that uses it
add_custom_command(TARGET vst3host POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy $<TARGET_FILE:vst3host> ${CMAKE_CURRENT_SOURCE_DIR}/../daw/audio/)
