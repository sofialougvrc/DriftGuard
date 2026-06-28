cmake_minimum_required(VERSION 3.18)
project(driftguard_perf LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(driftguard_perf_lib
    src/perf_collector.cpp
)
target_include_directories(driftguard_perf_lib PUBLIC include)

add_executable(driftguard-perf src/main.cpp)
target_link_libraries(driftguard-perf PRIVATE driftguard_perf_lib)

add_executable(driftguard-scoped-example examples/scoped_example.cpp)
target_link_libraries(driftguard-scoped-example PRIVATE driftguard_perf_lib)

add_executable(order-service-bench examples/order_service_bench.cpp)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_compile_definitions(driftguard_perf_lib PRIVATE DRIFTGUARD_LINUX_PERF=1)
endif()
