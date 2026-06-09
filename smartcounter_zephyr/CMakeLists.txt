#
# Smart Counter — Zephyr build script
#
# Usage (vanilla Zephyr):
#   west build -b xiao_esp32s3/esp32s3/procpu -p auto
#
# Usage (nRF Connect SDK on a Nordic board):
#   west build -b nrf52840dk/nrf52840 -p auto
#   (board overlay + WiFi pieces would need re-targeting, see doc/PORT.md)
#

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(smartcounter)

target_sources(app PRIVATE
    src/main.c
    src/buttons.c
    src/battery.c
    src/display.c
    src/ble_uart.c
    src/network.c
    src/settings_store.c
    src/counter_state.c
)

target_include_directories(app PRIVATE src)
