cmake_minimum_required(VERSION 3.20)

project(pmdg777_cdu_listener)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(pmdg777_cdu_listener
    cdu_broadcast_777.cpp
)

target_include_directories(pmdg777_cdu_listener PRIVATE
    "C:/MSFS 2024 SDK/SimConnect SDK/include"
    "D:/MSFS2024/Community/pmdg-aircraft-77w/Documentation/SDK"
)

target_link_directories(pmdg777_cdu_listener PRIVATE
    "C:/MSFS 2024 SDK/SimConnect SDK/lib"
)

target_link_libraries(pmdg777_cdu_listener PRIVATE
    SimConnect
)