# Smart Counter — Zephyr port

This is the Zephyr RTOS translation of `../smartcounter_battery.cpp`
(the Arduino IDE sketch driving the XIAO ESP32-S3 inventory counter).

* See **[doc/PORT.md](doc/PORT.md)** for the line-by-line mapping
  between the Arduino sketch and this project, plus notes on where the
  nRF Connect SDK has prebuilt equivalents.
* The board overlay (`boards/xiao_esp32s3_procpu.overlay`) is the
  source of truth for every pin, peripheral, and wake source.
* The `prj.conf` is the source of truth for every subsystem and driver
  that gets compiled in.

## Quick build

```bash
west build -b xiao_esp32s3/esp32s3/procpu -p auto smartcounter_zephyr
west flash
```

## Layout

| Path                                | Purpose                                          |
|-------------------------------------|--------------------------------------------------|
| `CMakeLists.txt`                    | West/CMake entry point                           |
| `prj.conf`                          | Kconfig fragment (which subsystems to compile)   |
| `Kconfig`                           | App-defined Kconfig knobs                        |
| `boards/*.overlay`                  | Per-board devicetree overlay                     |
| `src/main.c`                        | Init + supervisor loop + deep-sleep entry        |
| `src/counter_state.{c,h}`           | In-RAM persistent application state              |
| `src/settings_store.{c,h}`          | NVS-backed `settings` save/load                  |
| `src/buttons.{c,h}`                 | Tap / double-tap / long-press FSM                |
| `src/battery.{c,h}`                 | Voltage divider sampling + die-temp sensor       |
| `src/display.{c,h}`                 | SSD1306 OLED via `cfb`                           |
| `src/ble_uart.{c,h}`                | Nordic UART Service wrapper                      |
| `src/network.{c,h}`                 | WiFi (`net_mgmt`) + HTTP client + TLS            |
| `doc/PORT.md`                       | Long-form migration guide                        |
