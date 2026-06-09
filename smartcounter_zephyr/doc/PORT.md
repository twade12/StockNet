# Porting `smartcounter_battery.cpp` from Arduino to Zephyr

This document walks the original Arduino sketch
(`StockNet/smartcounter_battery.cpp`) section by section and explains what
each line becomes in the Zephyr translation under `smartcounter_zephyr/`.
At every step it calls out where **nRF Connect SDK (NCS)** — Nordic
Semiconductor's downstream of Zephyr — already ships a higher-level
component you would otherwise have to write yourself.

> **TL;DR** — Arduino's "one big `.ino` with library helpers" becomes
> four artifacts in Zephyr:
>
> 1. A **devicetree overlay** describing every peripheral (pins, I²C
>    bus, ADC channel, OLED, wake-capable button).
> 2. A **Kconfig fragment** (`prj.conf`) declaring which subsystems
>    and drivers to compile in.
> 3. A **set of C modules** that talk to those subsystems via stable
>    Zephyr APIs (GPIO, ADC, display, Bluetooth, networking, settings,
>    PM, retention).
> 4. A **CMakeLists.txt** that wires the modules into the Zephyr build.

---

## 1. Project layout

```
smartcounter_zephyr/
├── CMakeLists.txt                # build script (CMake + west)
├── prj.conf                      # Kconfig fragment (subsystem switches)
├── Kconfig                       # app-defined Kconfig options
├── sample.yaml                   # twister test descriptor (optional)
├── boards/
│   └── xiao_esp32s3_procpu.overlay  # pin & peripheral mapping
├── doc/
│   └── PORT.md                   # this file
└── src/
    ├── main.c                    # setup() + loop() replacement
    ├── counter_state.{c,h}       # persistent app state struct
    ├── settings_store.{c,h}      # NVS-backed save/load (Preferences)
    ├── buttons.{c,h}             # GPIO + debounce + tap/long-press FSM
    ├── battery.{c,h}             # voltage divider sampling + die-temp
    ├── display.{c,h}             # SSD1306 via cfb
    ├── ble_uart.{c,h}            # NUS service (TX/RX characteristics)
    └── network.{c,h}             # WiFi + http_client + TLS + JSON
```

### Why the split?

Arduino sketches put everything in a single translation unit because the
Arduino core wraps the underlying RTOS (FreeRTOS on ESP32) and exposes a
synchronous `setup()/loop()` façade. Zephyr is the RTOS — you call its
primitives directly. Splitting per concern keeps each module testable
with `ztest` and makes it possible to swap (for instance) the SSD1306
backend without recompiling the BLE layer.

---

## 2. Devicetree: replacing the `#define`s and library constructors

In the Arduino sketch peripherals are wired with magic numbers and
constructor calls:

```cpp
#define BTN_UP    D0
#define BTN_DOWN  D1
#define BTN_SHOW  D2
#define BATTERY_ADC_PIN       A9
#define BATTERY_SENSE_EN_PIN  D8
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
Wire.begin();
display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
```

In Zephyr the **devicetree** owns this. The board-specific overlay
`boards/xiao_esp32s3_procpu.overlay` declares:

* A `gpio-keys` parent node with three child buttons. The `wakeup-source`
  property on the SHOW button is what lets the SoC's deep-sleep wake
  controller (RTC-IO on ESP32-S3) keep the line monitored.
* A `voltage-divider` node that bundles the ADC channel, the divider
  ratio, and the `power-gpios` enable line. The Zephyr
  `voltage_divider_dt_spec` API then collapses your three Arduino calls
  (digitalWrite → settle → analogRead → digitalWrite) into a single
  `voltage_divider_sample_to_mv()` invocation that includes the divider
  math.
* The I²C controller (`&i2c0`) gets its pin-control state from
  `pinctrl-0` (a separate concept in Zephyr — pin muxing is **not** done
  in C).
* A child `ssd1306@3c` node under `&i2c0` with all of the panel's
  geometry and SSD1306 register quirks as DT properties.
* A channel under `&adc0` declaring gain, reference, acquisition time
  and resolution at *build* time, not via `analogSetPinAttenuation()`.
* `chosen { zephyr,display = &ssd1306_oled; }` — a *role* binding that
  the display drivers consume so application code says "open the chosen
  display" instead of naming a model.

**Concept name to learn:** *bindings* (`dts/bindings/*.yaml` in the
Zephyr tree) describe what properties each `compatible` value supports.
Always look up the binding file before inventing properties.

> **nRF Connect SDK note**
> NCS ships board overlays for every supported Nordic kit, and Nordic's
> *Visual Studio Code extension* (the "nRF Connect for VS Code" pack)
> provides a graphical devicetree editor that shows binding constraints
> live. On vanilla Zephyr you would edit the overlay by hand.

---

## 3. Kconfig: replacing `#include` directives

The Arduino sketch declares dependencies implicitly through
`#include <…>`:

```cpp
#include <NimBLEDevice.h>
#include <Adafruit_SSD1306.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
```

In Zephyr each subsystem is a Kconfig switch in `prj.conf`. The build
system pulls in the matching driver and source only if the symbol is
`y`. The mapping:

| Arduino `#include`                | Zephyr `CONFIG_*`                                                                                            |
|----------------------------------|--------------------------------------------------------------------------------------------------------------|
| `<NimBLEDevice.h>`               | `BT=y`, `BT_PERIPHERAL=y`, `BT_ZEPHYR_NUS=y`, `BT_GATT_DYNAMIC_DB=y`                                          |
| `<Adafruit_SSD1306.h>`           | `DISPLAY=y`, `SSD1306=y`, `CHARACTER_FRAMEBUFFER=y`                                                           |
| `<Adafruit_GFX.h>`               | covered by `CHARACTER_FRAMEBUFFER=y` (or `LVGL=y` for a richer UI)                                            |
| `<WiFi.h>` / `<WiFiClientSecure>` | `WIFI=y`, `WIFI_ESP32=y`, `NET_L2_WIFI_MGMT=y`, `NETWORKING=y`, `NET_SOCKETS=y`, `MBEDTLS=y`                  |
| `<HTTPClient.h>`                 | `HTTP_CLIENT=y`                                                                                              |
| `<ArduinoJson.h>`                | `JSON_LIBRARY=y` (declarative; descriptor-based)                                                              |
| `<Preferences.h>`                | `NVS=y`, `SETTINGS=y`, `SETTINGS_NVS=y`, `FLASH_MAP=y`                                                        |
| `<esp_sleep.h>`                  | `PM=y`, `POWEROFF=y`, plus `RETAINED_MEM=y` / `RETENTION=y` for boot-counter state                            |
| `<driver/temperature_sensor.h>`  | `SENSOR=y` + the SoC's `die-temp` devicetree node                                                             |

The application's own knobs (post interval defaults, sample counts,
default API URL) live in the project's own `Kconfig`. Choosing them
this way means downstream users / CI configurations can override them
with `west build -- -DCONFIG_SMARTCOUNTER_DEFAULT_POST_INTERVAL_SEC=60`.

> **nRF Connect SDK note**
> NCS exposes additional convenience symbols (e.g. `BT_NUS` instead of
> `BT_ZEPHYR_NUS`; `NCS_BOOT_BANNER` flavour; partition manager). For
> WiFi on a Nordic part you would switch to `WIFI_NRF7000=y` /
> `WIFI_NRF7002=y` against the nRF7002 coprocessor. The Bluetooth bring-
> up code is unchanged.

---

## 4. From `setup()` / `loop()` to an RTOS application

### Arduino model

`setup()` runs once, then `loop()` is called forever on a single thread.
Long operations (`HTTPClient.POST`, `WiFi.begin`, `delay()`) block this
thread. ISR/RX events have to be deferred into volatile flags or
ring buffers consumed by `loop()`.

### Zephyr model

`main()` is just the cold-start function for the boot thread. The
application is free to:

1. Spawn additional **threads** (`k_thread_create()`).
2. Schedule **work items** on the system or a private **work queue**
   (`k_work_init_delayable()` / `k_work_reschedule()`).
3. Wait on **kernel objects** (semaphores, message queues, events,
   `k_poll`).
4. Suspend with `k_sleep()` / `k_msleep()` which is *not* a busy wait —
   the kernel idles the CPU between ticks.

`src/main.c` uses:

* A **worker thread** that blocks on `k_poll()` over two message queues
  (`sc_button_events`, `sc_ble_cmd_q`). This replaces the `loop()`'s
  busy-poll of `handleButtonsPolled()` + `processPendingBleEvents()` +
  `processPendingBleCommands()`.
* A **delayable work item** in `settings_store.c` for the debounced
  flush of dirty preferences (replaces `flushSaveIfDue()`'s `millis()`
  comparison).
* The main thread itself is the supervisor: it ticks every 50 ms,
  checks the OLED timeout, expires BLE advertising, and eventually
  calls `sys_poweroff()` once `ready_for_deep_sleep()` returns true.

**Why three threads instead of one?** Because BLE RX (`bt_nus_cb`) and
GPIO callbacks already run in their own contexts inside Zephyr. The
moment you call `bt_nus_send()` from a callback you risk a priority
inversion or — worse — calling a kernel API that is illegal from ISR
context. The message-queue pattern keeps callbacks short and pushes
all real work onto schedulable threads.

> **nRF Connect SDK note**
> NCS provides a *zephyr,console = &uart0; zephyr,shell-uart = &uart0;*
> chosen pattern plus the `shell` subsystem so you can type `count set
> 5` over UART or RTT for free. Recommended for production: replace the
> ASCII `processCommand()` parser with a shell module.

---

## 5. Persistent storage: `Preferences` → `settings` + NVS

Arduino:

```cpp
prefs.begin("counter", false);
prefs.putInt("count", countValue);
prefs.end();
```

Zephyr (in `settings_store.c`):

```c
SETTINGS_STATIC_HANDLER_DEFINE(sc, "sc", NULL, sc_settings_set, NULL, NULL);
settings_save_one("sc/state", &state, sizeof(state));
settings_load_subtree("sc");
```

Key differences:

* `settings` is a **subsystem**, not a driver. Underneath it can use
  NVS, FCB, or a file system. We pick `SETTINGS_NVS=y`.
* Items are addressed by a **hierarchical key** (`sc/state`,
  `bt/keys/0/...`). The Bluetooth stack uses the same store, so your
  BLE bond keys are automatically persisted by enabling
  `BT_SETTINGS=y`.
* A `static_handler` is registered per subtree; the kernel calls your
  `set` callback during `settings_load_subtree()` to restore each key.

> **nRF Connect SDK note**
> NCS includes a *Settings Configuration Channel* (BLE Settings service)
> that lets a mobile app remotely read and write the entire settings
> tree without you defining custom commands. It pairs well with the
> nRF Connect mobile app for field provisioning of `wifi_ssid`,
> `wifi_pass`, and `api_url` instead of hardcoded defaults.

---

## 6. GPIO + buttons: pull-ups, edges, debounce

Arduino:

```cpp
pinMode(BTN_UP, INPUT_PULLUP);
digitalRead(BTN_UP);
// debounce by millis() in loop()
```

Zephyr (`buttons.c`):

```c
const struct gpio_dt_spec btn_up = GPIO_DT_SPEC_GET(DT_ALIAS(btn_up), gpios);
gpio_pin_configure_dt(&btn_up, GPIO_INPUT);
gpio_pin_interrupt_configure_dt(&btn_up, GPIO_INT_EDGE_TO_ACTIVE);
gpio_init_callback(&cb_data, isr, BIT(btn_up.pin));
gpio_add_callback(btn_up.port, &cb_data);
```

* `GPIO_DT_SPEC_GET()` resolves the devicetree node, picks the correct
  `gpio` controller device, the pin number, and the active-level flag
  (`GPIO_ACTIVE_LOW`) in one struct.
* Interrupts are **first-class**. The Arduino code polled GPIOs every
  5 ms; the Zephyr code wakes only on edges.
* Long-press / double-tap detection lives in a **delayable work item**
  (`show_work`) rather than `millis()` comparisons inside `loop()`.

> **nRF Connect SDK note**
> NCS includes a `dk_buttons_and_leds` helper module that wraps this
> exact pattern. It's optional but lets you call
> `dk_button_handler_add(handler)` instead of writing the ISR yourself.

---

## 7. Battery sampling: voltage divider + ADC

The Arduino code does this manually:

```cpp
digitalWrite(BATTERY_SENSE_EN_PIN, HIGH);
delay(3);
for (...) {
  rawSum += analogRead(BATTERY_ADC_PIN);
  mvSum  += analogReadMilliVolts(BATTERY_ADC_PIN);
}
digitalWrite(BATTERY_SENSE_EN_PIN, LOW);
reading.batteryV = reading.adcV * DIVIDER_SCALE;
```

In Zephyr the **`voltage-divider` devicetree binding** does the math
and the enable line in one helper. The board overlay declares:

```dts
vbatt: vbatt {
    compatible    = "voltage-divider";
    io-channels   = <&adc0 9>;
    output-ohms   = <6800>;
    full-ohms     = <(3300 + 6800)>;
    power-gpios   = <&gpio0 18 GPIO_ACTIVE_HIGH>;
};
```

…and `battery.c` calls `voltage_divider_sample_to_mv()` which already
returns the **reconstructed battery voltage** in millivolts. The
"settle + throwaway first sample" pattern is replicated for parity but
the divider ratio math no longer lives in source.

Crucially, the `gpio_hold_en(BATTERY_SENSE_EN_PIN)` trick used in the
Arduino sketch (to keep the N-MOSFET driven low through deep sleep) is
not needed in Zephyr if you put the divider GPIO behind a pinctrl
sleep state in the overlay — Zephyr's PM framework will keep the GPIO
parked correctly across `sys_poweroff()`.

> **nRF Connect SDK note**
> NCS ships a higher-level *fuel gauge* model (`nrf_fuel_gauge` for the
> nPM1300 PMIC) which not only samples voltage but tracks SoC%, time-
> to-empty, and reports BLE Battery Service updates without app code.
> If you ever migrate from "raw divider on a SoC pin" to "Nordic PMIC",
> the `battery.c` module is the only file you need to touch.

---

## 8. OLED: `Adafruit_GFX` → `cfb` (or LVGL)

Arduino:

```cpp
Adafruit_SSD1306 display(128, 64, &Wire, -1);
display.clearDisplay();
display.setTextSize(4);
display.setCursor(0, 34);
display.println(countValue);
display.display();
```

Zephyr (`display.c`):

```c
const struct device *oled = DEVICE_DT_GET(DT_CHOSEN(zephyr_display));
cfb_framebuffer_init(oled);
cfb_framebuffer_clear(oled, false);
cfb_framebuffer_set_font(oled, 1);
cfb_print(oled, "42", 0, 34);
cfb_framebuffer_finalize(oled);
```

Differences:

* The driver bound to `compatible = "solomon,ssd1306fb"` provides the
  generic `display.h` API (`display_blanking_off`, `display_write`,
  `display_set_pixel_format`, …). Switching the OLED to an SH1106 or a
  larger panel is a devicetree change, not a code change.
* The character framebuffer (`cfb`) helper is the closest match to
  GFX's text helpers. Drawing primitives (lines, rects, the battery-
  icon outline) require either pixel manipulation via `display_write()`
  + a back buffer, or — better — enabling LVGL (`CONFIG_LVGL=y`) which
  provides labels, icons, and event-driven widgets.
* `pm_device_action_run(oled, PM_DEVICE_ACTION_SUSPEND)` replaces
  `display.ssd1306_command(SSD1306_DISPLAYOFF)`; the driver author put
  that command behind the standard PM hook.

> **nRF Connect SDK note**
> NCS includes pre-built LVGL themes for Nordic kits and ships the LVGL
> "online image converter" for embedding bitmaps directly. If you want
> a graphical UI (e.g. a battery icon with a fill bar, an animated
> "syncing" spinner) LVGL on NCS is the fastest path.

---

## 9. Bluetooth Low Energy — the biggest mapping

The Arduino sketch uses Apache NimBLE. The Zephyr **host stack is also
NimBLE-derived but with a different API surface**: `bt_…` functions in
`<zephyr/bluetooth/bluetooth.h>`. Terminology you should know:

| Concept                  | Arduino (NimBLE)                                 | Zephyr                                                          |
|--------------------------|--------------------------------------------------|------------------------------------------------------------------|
| Host stack init          | `NimBLEDevice::init("name")`                     | `bt_enable(NULL)` + `bt_set_name(...)`                          |
| GATT **server**          | `NimBLEDevice::createServer()`                   | implicit; everything in the `BT_GATT_SERVICE_DEFINE` table       |
| **GATT service**         | `server->createService(uuid)`                    | `BT_GATT_SERVICE_DEFINE(nus_svc, BT_GATT_PRIMARY_SERVICE(...), ...)` — declarative, evaluated at link time |
| **GATT characteristic**  | `service->createCharacteristic(uuid, prop)`      | `BT_GATT_CHARACTERISTIC(uuid, props, perm, read_cb, write_cb, value_ptr)` |
| **GATT attribute**       | (hidden inside characteristic)                   | each element of the `struct bt_gatt_attr[]` is an attribute     |
| **CCCD** (notify/indicate enable) | provided automatically by `NIMBLE_PROPERTY::NOTIFY` | `BT_GATT_CCC(ccc_cfg_changed)` macro entry                       |
| **MTU exchange**         | implicit                                         | enabled by `BT_BUF_ACL_*` / `BT_L2CAP_TX_MTU` Kconfig            |
| Advertising payload      | `NimBLEAdvertisementData`                        | array of `struct bt_data` (BT Core spec AD format)              |
| Connection callbacks     | `NimBLEServerCallbacks::onConnect`               | `BT_CONN_CB_DEFINE(name)` with `.connected/.disconnected`       |
| Send a "TX" line         | `txCharacteristic->setValue(); ->notify();`      | `bt_gatt_notify(conn, attr, data, len)` or `bt_nus_send(conn, …)` |
| RX write callback        | `NimBLECharacteristicCallbacks::onWrite`         | `bt_nus_cb.received(conn, data, len)`                            |

For this project we use the **Nordic UART Service (NUS)** which
**already implements the exact three UUIDs** your Arduino code defines
(`6E400001-…`, `6E400002-…`, `6E400003-…`). On vanilla Zephyr it's
`CONFIG_BT_ZEPHYR_NUS=y` and `<zephyr/bluetooth/services/nus.h>`. On
NCS it's `CONFIG_BT_NUS=y` and `<bluetooth/services/nus.h>` — *same
UUIDs, very similar API*, with a few extra helpers (`bt_nus_send`
returns flow-controlled, has an async send queue, etc.).

In `ble_uart.c`:

* `bt_enable(NULL)` boots the host stack on the system work queue.
* `bt_nus_cb_register(...)` hooks the RX path. Inside the callback we
  copy into a `k_msgq` (the analogue of your `bleCmdQueue[]`) so the
  callback returns quickly.
* Advertising payload `ad[]` carries the 128-bit NUS UUID; scan response
  `sd[]` carries the device name (because a 128-bit UUID barely fits
  in a single 31-byte advertisement frame next to a name string).
* `bt_le_adv_start(BT_LE_ADV_CONN, ad, …)` corresponds to
  `pAdvertising->start()`. The min/max interval in your Arduino code
  (`setMinInterval(800)` etc.) is now controlled by the
  `BT_LE_ADV_CONN` parameter struct.

### Where NCS shines for BLE

* **Higher-level services** out of the box: BAS (Battery Service),
  CTS (Current Time), DIS (Device Information), and an MCU manager for
  DFU updates over BLE — perfect for a fleet device.
* **SMP (Simple Management Protocol) over BLE** + MCUboot lets you push
  signed firmware updates from the mobile app without a wired re-flash.
* **`bt_nus`** in NCS supports back-pressure on the send queue, which
  matters when you spam telemetry over BLE.
* **`bt_filter_accept_list`** (whitelist) is exposed with a one-liner
  helper.
* **Pairing UI** — NCS includes the `bt_conn_auth_cb` patterns and the
  `nrf_security` library for OOB / passkey / numeric comparison.

---

## 10. WiFi + HTTP — the most code-heavy translation

Arduino:

```cpp
WiFi.mode(WIFI_STA);
WiFi.begin(ssid, pass);
while (WiFi.status() != WL_CONNECTED) { ... }
HTTPClient http;
http.begin(secureClient, url);
http.addHeader("Content-Type", "application/json");
int code = http.POST(payload);
```

Zephyr (`network.c`):

```c
struct wifi_connect_req_params p = { /* SSID/PSK/sec type */ };
net_mgmt(NET_REQUEST_WIFI_CONNECT, iface, &p, sizeof(p));
/* …wait on net_mgmt event semaphores… */

int sock = zsock_socket(AF_INET, SOCK_STREAM, IPPROTO_TLS_1_2);
zsock_setsockopt(sock, SOL_TLS, TLS_PEER_VERIFY, …);
zsock_connect(sock, addrinfo->ai_addr, …);

struct http_request req = { … };
http_client_req(sock, &req, 10000, &capture_ctx);
```

Concepts to learn:

* **`net_mgmt`** — a publish/subscribe bus for networking events. Your
  app subscribes once (`net_mgmt_add_event_callback`) and gets
  `NET_EVENT_WIFI_CONNECT_RESULT`, `NET_EVENT_IPV4_ADDR_ADD`, etc.
* **BSD-style sockets** (`zsock_*` prefix) — same `socket()/connect()/
  send()/recv()` shape as POSIX, but typed for Zephyr's per-thread
  errno and TLS option set.
* **`IPPROTO_TLS_1_2`** is how you "upgrade" a socket to mbedTLS-backed
  HTTPS. The `setInsecure()` call in your Arduino code maps to
  `TLS_PEER_VERIFY_NONE`. For production install the StockNet CA via
  `tls_credential_add(TAG, TLS_CREDENTIAL_CA_CERTIFICATE, der, len)`
  and reference the tag from a `TLS_SEC_TAG_LIST`.
* **`http_client_req()`** — Zephyr's analogue to HTTPClient. You pass
  a `struct http_request` and a response callback that gets called
  back as bytes arrive, so the parser is streaming-friendly. Look at
  `samples/net/sockets/http_client` in the Zephyr tree for the full
  pattern.

### JSON parsing

Replace `ArduinoJson::DynamicJsonDocument` + `deserializeJson()` with
`<zephyr/data/json.h>` — a *declarative* parser. You describe the
schema once:

```c
struct cmd_entry {
    const char *command_id;
    const char *command_type;
    /* nested payload */
};

static const struct json_obj_descr cmd_descr[] = {
    JSON_OBJ_DESCR_PRIM(struct cmd_entry, command_id,   JSON_TOK_STRING),
    JSON_OBJ_DESCR_PRIM(struct cmd_entry, command_type, JSON_TOK_STRING),
};

json_obj_parse(body, body_len, cmd_descr, ARRAY_SIZE(cmd_descr), &entry);
```

This is faster, allocation-free, and produces compile-time errors if a
field name changes.

### Where NCS shines for WiFi & HTTP

* For Nordic hardware: **`nrf_wifi`** driver against the nRF7002
  coprocessor, with WiFi-6 support and certified power-save modes.
* **`download_client`** (NCS-only) takes a URL, handles TLS, retries,
  and emits chunks to a callback. Reduces the entire `do_http_post()`
  + `connect_to_host()` skeleton to ~30 lines.
* **`net/lib/azure_iot_hub`**, **`net/lib/nrf_cloud`**,
  **`net/lib/aws_iot`** — if you switch from your bespoke REST endpoint
  to a managed IoT broker, NCS already provides MQTT-with-TLS clients
  bonded to those vendors.
* **Modem trace + Modem firmware update** subsystems for cellular
  (nRF9151) variants of the same product.

---

## 11. Deep sleep and the boot counter

Arduino: `esp_deep_sleep_start()`, `RTC_DATA_ATTR uint32_t bootCount`,
`esp_sleep_enable_ext0_wakeup(WAKE_SHOW_GPIO, 0)`.

Zephyr:

* **`sys_poweroff()`** (from `<zephyr/sys/poweroff.h>`) is the portable
  hook. On ESP32-S3 it lowers down to `esp_deep_sleep_start()`; on
  Nordic SoCs it triggers `System OFF`.
* The wake source is declared in devicetree
  (`wakeup-source` property on the SHOW button) plus a runtime call to
  `gpio_pin_interrupt_configure_dt(..., GPIO_INT_LEVEL_ACTIVE)` to arm
  the pin for the wake.
* **Retained data** uses the `retention` subsystem
  (`CONFIG_RETENTION=y`). You point a `chosen { zephyr,retention = … }`
  node at either a `compatible = "zephyr,retained-ram"` region or a
  hardware-backed alternative; the device exposes
  `retention_read()`/`retention_write()`.
* A timed wake (post interval) is set with a wake-capable counter
  device (`<zephyr/drivers/counter.h>` + `counter_set_alarm()`). On
  ESP32-S3 the natural backing is the RTC timer; on nRF parts, the
  `RTC0` peripheral.

> **nRF Connect SDK note**
> On Nordic chips the equivalent of `RTC_DATA_ATTR` is **GPREGRET**
> (general-purpose retention register), accessible via the same
> `retention` Kconfig but with a Nordic-specific driver. NCS also
> bundles `nrf_pwrmgr` examples that wire up timed wake, button wake,
> *and* nPM1300 PMIC wake on the same code path.

---

## 12. Logging and console

Replace `Serial.begin(115200)` + `Serial.printf(...)` with:

* `CONFIG_LOG=y`, `CONFIG_LOG_MODE_DEFERRED=y` — async logging on a
  background thread so timing-sensitive code is not perturbed by UART
  blocking.
* Per-module log instances: `LOG_MODULE_REGISTER(sc_app, LOG_LEVEL_INF)`
  followed by `LOG_INF(...)` / `LOG_WRN(...)` / `LOG_ERR(...)`.
* The UART console is automatically wired by enabling
  `CONFIG_UART_CONSOLE=y` and pointing `chosen { zephyr,console = ... }`
  at the right UART node.

> **nRF Connect SDK note**
> NCS adds **RTT logging** (`LOG_BACKEND_RTT=y`) which streams logs over
> a J-Link probe with zero UART pins. Useful when every GPIO is taken.

---

## 13. Build & flash

```bash
# Vanilla Zephyr (ESP32-S3, Pro CPU is the application target)
west init -m https://github.com/zephyrproject-rtos/zephyr
west update
cd smartcounter_zephyr
west build -b xiao_esp32s3/esp32s3/procpu -p auto
west flash

# NCS (Nordic kit example; would require board overlay & WiFi re-targeting)
nrfutil install toolchain-manager
west init -m https://github.com/nrfconnect/sdk-nrf --mr v2.6.0
west update
west build -b nrf52840dk/nrf52840 -p auto
west flash
```

`-p auto` rebuilds from scratch when the configuration changes — the
Zephyr equivalent of "delete the build folder when in doubt".

---

## 14. Suggested follow-up work

1. **Cert provisioning** — replace `TLS_PEER_VERIFY_NONE` with a real
   CA. Implement `tls_credential_add()` calls fed from a flash slot.
2. **Settings over BLE** — expose a writeable "config" characteristic
   so the iOS/Android app can push SSID/PSK/API_URL without re-flashing.
3. **MCUboot + DFU** — once the device is in the field, OTA firmware is
   table stakes. On NCS, enable `CONFIG_BOOTLOADER_MCUBOOT=y` and the
   SMP server (`CONFIG_MCUMGR_TRANSPORT_BT=y`) — pairs with the
   "nRF Connect Device Manager" mobile app for free updates.
4. **LVGL UI** — switch `display.c` to LVGL for a proper graphical UI
   (reservation badge, sync icons, multi-bin display) that maps to the
   StockNet inventory workflow described in `stocknet_docs.md`.
5. **Pairing & encryption** — turn on `BT_SMP=y`, `BT_BONDABLE=y` so
   bonded mobile clients are remembered across reboots; the BLE keys
   piggyback on the same `settings` store you already configured.
6. **Power profiling** — Zephyr's `pm_state_force()` API lets you
   measure exact current draw per state. NCS adds the
   *Online Power Profiler for Bluetooth LE* tool which can predict
   battery life from your advertising/connection parameters.
