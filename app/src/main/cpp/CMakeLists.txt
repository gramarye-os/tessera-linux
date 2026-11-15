cmake_minimum_required(VERSION 3.22.1)
project(launcher_native C)

add_library(launcher SHARED pty.c)
target_compile_options(launcher PRIVATE -Wall -Wextra -Werror)
target_link_libraries(launcher log)

# The phone's end of a session in a gramarye env (relay.c). A program, not a library: named like
# one and put with them so that the APK carries it and Android unpacks it where it can be run.
add_executable(tessera-relay relay.c)
target_compile_options(tessera-relay PRIVATE -Wall -Wextra -Werror)
set_target_properties(tessera-relay PROPERTIES
    OUTPUT_NAME "libtessera-relay.so"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_LIBRARY_OUTPUT_DIRECTORY}")
