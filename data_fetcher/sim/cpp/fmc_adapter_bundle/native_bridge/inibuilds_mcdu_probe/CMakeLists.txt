cmake_minimum_required(VERSION 3.20)

project(inibuilds_mcdu_probe)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(inibuilds_mcdu_probe
    inibuilds_mcdu_probe.cpp
)

target_include_directories(inibuilds_mcdu_probe PRIVATE
    "C:/MSFS 2024 SDK/SimConnect SDK/include"
)

target_link_directories(inibuilds_mcdu_probe PRIVATE
    "C:/MSFS 2024 SDK/SimConnect SDK/lib"
)

target_link_libraries(inibuilds_mcdu_probe PRIVATE
    SimConnect
)
