# StockNet — System Architecture Overview

---

## Full System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            WAREHOUSE FLOOR                                      │
│                                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐           │
│  │  SmartCounter    │   │  SmartCounter    │   │  SmartCounter    │  ...       │
│  │  [XIAO ESP32-S3] │   │  [XIAO ESP32-S3] │   │  [XIAO ESP32-S3] │           │
│  │                  │   │                  │   │                  │           │
│  │  ┌────────────┐  │   │  ┌────────────┐  │   │  ┌────────────┐  │           │
│  │  │ OLED 128×64│  │   │  │ OLED 128×64│  │   │  │ OLED 128×64│  │           │
│  │  │ Part: R-10K│  │   │  │ Part: C-22U│  │   │  │ Asm: PCB-01│  │           │
│  │  │ Count: 250 │  │   │  │ Count:  80 │  │   │  │ Count:  12 │  │           │
│  │  └────────────┘  │   │  └────────────┘  │   │  └────────────┘  │           │
│  │  [↑] [↓] [SHOW]  │   │  [↑] [↓] [SHOW]  │   │  [↑] [↓] [SHOW]  │           │
│  │  Battery: 4.1V   │   │  Battery: 3.6V   │   │  Battery: 3.1V ⚠ │           │
│  └────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘           │
│           │ WiFi                 │ WiFi                  │ WiFi                │
└───────────┼──────────────────────┼───────────────────────┼─────────────────────┘
            │                      │                       │
            │       ┌──────────────┘                       │
            │       │       Local 802.11 Network           │
            └───────┴──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        STOCKNET BACKEND SERVER                                  │
│                        (Python Flask + PostgreSQL)                              │
│                                                                                 │
│  ┌────────────────────┐   ┌─────────────────────┐   ┌──────────────────────┐  │
│  │   Device Ingest    │   │  Command Queue      │   │  Alert Engine        │  │
│  │                    │   │                     │   │                      │  │
│  │  POST /api/counter │   │  pending → acked    │   │  low_stock (warn)    │  │
│  │  • Upsert device   │   │  set_count          │   │  low_stock (crit)    │  │
│  │  • Log event       │   │  set_name           │   │  low_battery (warn)  │  │
│  │  • Update inv.     │   │  set_wifi           │   │  low_battery (crit)  │  │
│  │  • BOM deduction   │   │  set_api_url        │   │  auto-clear on OK    │  │
│  │  • Battery health  │   │  display_on/off     │   │                      │  │
│  │  • Alert check     │   │  reset_count        │   └──────────┬───────────┘  │
│  │  • Return cmds     │   │  set_post_interval  │              │               │
│  └────────────────────┘   └─────────────────────┘              │               │
│                                                                 ▼               │
│  ┌────────────────────┐   ┌─────────────────────┐   ┌──────────────────────┐  │
│  │  Inventory Engine  │   │  Production Orders  │   │  Webhook Dispatcher  │  │
│  │                    │   │                     │   │                      │  │
│  │  Components CRUD   │   │  draft              │   │  HMAC-SHA256 signed  │  │
│  │  Assemblies CRUD   │   │  → confirmed        │   │  low_stock event     │  │
│  │  BOM management    │   │  → in_progress      │   │  low_battery event   │  │
│  │  Stock adjustments │   │  → completed        │   │  order events        │  │
│  │  Reservations      │   │  Component reserves │   │  → Slack / webhook   │  │
│  │  CSV export        │   │  Supplier orders    │   │                      │  │
│  └────────────────────┘   └─────────────────────┘   └──────────────────────┘  │
│                                                                                 │
│  ┌────────────────────────────────────────────────────────────────────────┐    │
│  │                        PostgreSQL Database                             │    │
│  │  devices · counter_events · device_commands · device_health_readings  │    │
│  │  components · assemblies · assembly_components · inventory_items       │    │
│  │  alerts · production_orders · production_order_lines                   │    │
│  │  component_reservations · supplier_orders · webhooks                   │    │
│  │  cycle_count_schedules                                                  │    │
│  └────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────┬──────────────────────────────────────────────┘
                                   │  HTTP
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      WEB DASHBOARD (Browser)                                    │
│                      Dark Industrial Theme                                      │
│                                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Live       │  │  BOM        │  │  Production │  │  Procurement        │  │
│  │  Inventory  │  │  Manager    │  │  Orders     │  │  & Supplier Mgmt    │  │
│  │             │  │             │  │             │  │                     │  │
│  │  Device     │  │  Assembly   │  │  PO draft / │  │  Lead times         │  │
│  │  fleet      │  │  component  │  │  confirm /  │  │  Supplier URLs      │  │
│  │  status     │  │  lists      │  │  complete   │  │  Min order qty      │  │
│  │  Count chart│  │  BOM editor │  │  Reservations│  │  CSV export         │  │
│  │  Alerts     │  │  Stock adj. │  │             │  │                     │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼ (webhooks)
                    ┌──────────────────────────────┐
                    │   External Integrations      │
                    │   Slack / Teams / Custom API │
                    │   (HMAC-signed payloads)     │
                    └──────────────────────────────┘
```

---

## BLE Provisioning Flow (Local Setup via Phone)

```
Operator Phone                  SmartCounter Device
(LightBlue / nRF Connect)       (ESP32-S3 BLE)
        │                               │
        │── BLE Scan ──────────────────▶│
        │◀─ Advertise "Counter-A02" ───│
        │── Connect ───────────────────▶│
        │── DEVNAME=Counter-A02 ───────▶│  (stores to NVS flash)
        │── NAME=10k Resistor ─────────▶│
        │── ID=R-10K-0603 ─────────────▶│
        │── WIFI_SSID=WarehouseWiFi ───▶│
        │── WIFI_PASS=secret123 ───────▶│
        │── API_URL=http://192.168... ─▶│
        │── WIFI_TEST ─────────────────▶│
        │◀─ OK WIFI IP=192.168.1.55 ───│
        │── POST_NOW ──────────────────▶│
        │◀─ OK POST 200 ───────────────│
```

---

## Counter Event → Inventory Update Flow

```
SmartCounter                   Flask Backend                    PostgreSQL
     │                              │                               │
     │── POST /api/counter ────────▶│                               │
     │   { device_name, part_name,  │                               │
     │     part_id, count,          │                               │
     │     battery_voltage, rssi }  │                               │
     │                              │── UPSERT devices ────────────▶│
     │                              │── INSERT counter_events ─────▶│
     │                              │── INSERT device_health ──────▶│
     │                              │── CALL upsert_inventory() ───▶│
     │                              │   (if assembly: BOM deduct)   │
     │                              │── CHECK alert thresholds ────▶│
     │                              │── CHECK battery thresholds ──▶│
     │                              │── SELECT pending commands ────▶│
     │◀─ { ok, commands: [...] } ──│◀──────────────────────────────│
     │   (device applies cmds)      │                               │
     │── POST /api/device/          │                               │
     │   commands/ack ─────────────▶│── UPDATE command status ─────▶│
```

---

## Technology Stack

| Layer          | Technology                                      |
|----------------|-------------------------------------------------|
| MCU            | Seeed XIAO ESP32-S3 (Xtensa LX7 dual-core)      |
| Display        | SSD1306 OLED 128×64 via I2C                     |
| Connectivity   | 802.11 WiFi + BLE 5.0 (Nordic UART Service)     |
| Firmware (v1)  | C++ Arduino / ESP-IDF                           |
| Firmware (v2)  | C, Zephyr RTOS (nRF Connect SDK), West build    |
| Backend        | Python 3.10, Flask, psycopg3, psycopg-pool      |
| Database       | PostgreSQL 15, connection pooling               |
| Frontend       | HTML5, CSS3, Vanilla JS, Plotly.js              |
| Webhooks       | HTTP POST, HMAC-SHA256 signing                  |
| Hosting        | Self-hosted LAN (no cloud dependency)           |
