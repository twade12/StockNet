# StockNet — Fiverr Project Listing

---

## Project Name

**StockNet: End-to-End IoT Inventory Management System with Custom ESP32 Hardware Counter & Fleet Management Backend**

---

## Industry

1. Electronics & Embedded Systems
2. Manufacturing & Production
3. Inventory & Supply Chain Management
4. Internet of Things (IoT)
5. Industrial Automation
6. Software Development & APIs

---

## Project Duration

**3 months** (design through production-ready delivery)

---

## Project Description

My client runs a small-batch electronics manufacturing operation and was drowning in manual inventory processes — spreadsheets updated by hand, no visibility into real-time stock levels across the warehouse floor, and no early warning when components ran low before a production run. They needed something better, but off-the-shelf RFID or barcode solutions were either too expensive, too complicated to retrofit onto their existing shelving, or required monthly SaaS subscriptions they didn't want.

The goal was a fully custom, end-to-end solution: purpose-built physical counting devices that live at each component bin, paired with a centralized backend for fleet management, alerting, and procurement planning — all self-hosted, zero ongoing licensing fees.

---

### What I Built

#### Hardware — SmartCounter Device (ESP32-S3)

The SmartCounter is a compact IoT counter built on the Seeed XIAO ESP32-S3. Each unit sits at a shelf bin and tracks its component count in real time.

**Hardware features:**
- OLED display (SSD1306) showing part name, part ID, and live count
- Three physical buttons: UP (increment), DOWN (decrement), SHOW (toggle display / long-press to reset)
- Tap, double-tap, and long-press detection FSM for intuitive operation
- Battery monitoring via a hardware voltage divider with ADC sampling — reports raw ADC value, millivolts, and computed battery percentage (3.0 V = 0%, 4.2 V = 100%) to the server on every POST
- WiFi (802.11 b/g/n) for automatic count reporting to the backend at configurable intervals
- BLE (Nordic UART Service) for zero-touch local provisioning via a phone app — operators scan, connect, and configure a new device in under a minute
- Deep-sleep power management to extend battery life between charges

**Firmware delivered in two variants:**
- **Arduino/ESP-IDF** (for rapid prototyping and immediate deployment)
- **Zephyr RTOS port** (full Zephyr/nRF Connect SDK translation targeting the same XIAO ESP32-S3 — production-grade, OTA-upgrade-ready, documented with a line-by-line mapping guide between both implementations)

---

#### Backend — StockNet Flask + PostgreSQL

A Python Flask REST API backed by PostgreSQL with a connection pool, served on the client's local network.

**Device fleet management:**
- Automatic device registration on first contact — devices self-enroll by name
- BLE-to-backend command queue: the server enqueues configuration changes (set count, update WiFi credentials, toggle display, set thresholds, adjust POST interval, rename device) which devices pick up and acknowledge on their next poll cycle
- Device health time-series: every counter POST stores a health snapshot (battery voltage, battery %, RSSI, temperature, IP) to a dedicated readings table, enabling trend charts and predictive maintenance

**Inventory engine:**
- Component and assembly catalog with full CRUD
- Bill of Materials (BOM): assemblies reference components with per-unit quantity requirements
- When an assembly counter increments, component stock is automatically deducted proportionally via a transactionally-safe PostgreSQL upsert function
- Manual stock adjustments (positive for received shipments, negative for scrap/usage) with automatic BOM deduction when building assemblies

**Alerting system:**
- Tiered low-stock alerts (warning / critical thresholds) configurable per device; alerts auto-escalate and auto-clear as counts change
- Tiered battery alerts (warning at 3.4 V, critical at 3.2 V) with the same open/clear lifecycle
- Webhook system with optional HMAC-SHA256 signing — fires non-blocking HTTP POSTs to Slack or any external endpoint on `low_stock`, `low_battery`, `order_confirmed`, `order_completed`, and `supplier_received` events

**Production planning:**
- Production orders module with a full lifecycle (draft → confirmed → in-progress → completed / cancelled)
- Component reservations: on order confirmation, required component quantities are locked against inventory so available stock reflects real availability
- Supplier orders module for tracking inbound shipment ETAs
- Cycle count scheduling: schedule periodic physical counts, capture confirmed vs. system counts, and persist discrepancies

**Procurement & reporting:**
- Procurement dashboard showing supplier URLs, lead times, and minimum order quantities
- Inventory CSV export with computed deficit and suggested reorder quantities for every component and assembly

**Frontend:**
- Dark industrial-themed single-page web dashboard (plain HTML/CSS/JS, no framework)
- Tabs: Live Inventory, BOM Manager, Production Orders, Procurement
- Plotly.js time-series charts for count history per device with auto-bucketing based on the selected time range
- Device fleet status panel with last-seen timestamps, battery indicators, and threshold status

---

### Challenges & How I Solved Them

**1. ESP32-S3 ADC non-linearity for battery readings**
The ESP32-S3's ADC is notoriously non-linear near the rails. Early firmware was generating false low-battery alerts from noise spikes. I implemented a rolling average across multiple ADC samples and applied the manufacturer's recommended attenuation settings, which brought voltage readings to within ±50 mV of actual — accurate enough for reliable threshold alerts without false positives.

**2. BLE command queue with graceful replay**
Devices can lose WiFi connection for extended periods. When they reconnect, they needed to pick up only the commands they hadn't yet acknowledged — not replay already-applied ones. I designed the command queue with explicit `pending → acked / failed / cancelled` state transitions and a per-device poll endpoint that returns only pending commands in insertion order, so reconnecting devices always resume cleanly.

**3. Concurrent counter POSTs and BOM deduction correctness**
When multiple assembly counters post simultaneously, the BOM deduction logic could race against itself and produce incorrect component stock levels. I moved the upsert and deduction logic into a PostgreSQL function invoked inside a single transaction per POST, eliminating the race condition entirely without needing application-level locking.

**4. Zephyr RTOS port from Arduino**
The client wanted the option to ship a production firmware build with OTA upgrade support, which the Arduino toolchain couldn't provide. I ported the entire sketch to Zephyr RTOS — remapping every peripheral (SSD1306 via CFB, WiFi via `net_mgmt`, NVS settings via Zephyr settings subsystem, BLE via Nordic UART Service) to Zephyr device tree and Kconfig equivalents. A full port guide (`doc/PORT.md`) documents every mapping decision so future firmware engineers can maintain both variants in sync.

---

### Result

A fully self-contained IoT inventory system — from custom silicon to web dashboard — deployed on the client's local network. Real-time visibility across every shelf bin, automated low-stock and battery alerts via Slack webhooks, production order planning with component reservations, and zero monthly SaaS costs.

---

## Attachments

*See the following generated marketing materials in the `marketing/` folder:*

- **[marketing/architecture_diagram.md](marketing/architecture_diagram.md)** — Full system architecture overview with component diagram (suitable for client-facing presentation)
- **[marketing/feature_one_pager.md](marketing/feature_one_pager.md)** — Marketing one-pager / feature highlights document

---
