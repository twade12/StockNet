# StockNet
## IoT Inventory Management — From Custom Hardware to Live Dashboard

---

> **The problem:** Manual bin counts. Spreadsheets updated after the fact.
> No warning before a production run runs out of parts.
>
> **The solution:** Custom IoT counter devices on every shelf bin, a
> self-hosted backend that knows your stock in real time, and automatic
> alerts before you're ever caught short.

---

## The SmartCounter Device

A compact, purpose-built counter that lives at each shelf bin.

```
┌─────────────────────────┐
│  STOCKNET SmartCounter  │
│  ┌─────────────────┐    │
│  │  10k Resistor   │    │  • Tap [↑] or [↓] to adjust count
│  │  R-10K-0603     │    │  • OLED display auto-dims to save power
│  │  Count: 250     │    │  • WiFi reports to your server automatically
│  │  Batt: ████▒ 87%│    │  • BLE setup from any phone — no app install
│  └─────────────────┘    │  • Battery lasts weeks on a charge
│   [↑]   [↓]   [SHOW]   │
└─────────────────────────┘
```

**Seeed XIAO ESP32-S3** · OLED Display · Physical Buttons · Battery Monitor · WiFi + BLE

---

## The StockNet Backend

Your server. Your data. No subscriptions.

### Real-Time Inventory
Every button press is recorded. The dashboard reflects actual stock the moment it changes — not the last time someone updated a spreadsheet.

### Bill of Materials Engine
Define your assemblies and the components they consume. When an assembly counter goes up, component stock is automatically deducted. No manual reconciliation.

### Smart Alerting
Set warning and critical thresholds per device. Get notified on **Slack** (or any webhook endpoint) before you run out — not after.

| Trigger            | Alert Level | Action                        |
|--------------------|-------------|-------------------------------|
| Count ≤ Warning    | ⚠ Warning   | Webhook fired, alert opened   |
| Count ≤ Critical   | 🔴 Critical  | Webhook fired, alert escalated|
| Count recovers     | ✅ Clear     | Alert auto-closed             |
| Battery ≤ 3.4 V   | ⚠ Warning   | Battery alert opened          |
| Battery ≤ 3.2 V   | 🔴 Critical  | Battery alert escalated       |

### Fleet Management
Reconfigure any counter from the server — no physical access needed. Update part names, WiFi credentials, count values, and display settings. Commands queue up and devices acknowledge receipt.

### Production Planning
Create production orders tied to your assembly catalog. Confirm an order and the system automatically reserves the required components so you never double-book stock.

### Procurement Dashboard
Track supplier URLs, lead times, and minimum order quantities. Export a ready-to-send reorder report — CSV with every low-stock component, its deficit, and a suggested order quantity.

---

## What's Included

| Deliverable                          | Description                                              |
|--------------------------------------|----------------------------------------------------------|
| SmartCounter firmware (Arduino)      | Production-ready ESP32-S3 sketch, documented             |
| SmartCounter firmware (Zephyr RTOS)  | Full Zephyr port, OTA-upgrade-ready, ported guide        |
| Flask REST API (Python)              | Full source, all endpoints, PostgreSQL migrations        |
| Web dashboard                        | 4-tab interface, dark industrial theme, Plotly charts    |
| BLE provisioning docs                | Step-by-step setup guide for field operators             |
| API reference docs                   | All endpoints, BLE commands, and command queue types     |

---

## Feature Summary

**Hardware**
- XIAO ESP32-S3 microcontroller (dual-core LX7, 8 MB flash)
- 128×64 OLED display (SSD1306 I2C)
- 3 physical buttons with tap / long-press detection
- Battery voltage monitoring with percentage display
- WiFi 802.11 b/g/n auto-reconnect
- BLE 5.0 Nordic UART Service for zero-friction provisioning
- Deep sleep power management

**Backend & API**
- Python Flask + PostgreSQL (self-hosted, LAN or cloud)
- Device auto-registration and fleet tracking
- Backend-to-device command queue with acknowledgements
- Time-series device health readings (battery, RSSI, temperature)
- Component and assembly catalog with full CRUD
- BOM-aware inventory engine with automatic deduction
- Tiered alert system (low stock + low battery)
- HMAC-signed outbound webhooks
- Production orders with component reservations
- Supplier orders and procurement tracking
- Cycle count scheduling with discrepancy tracking
- CSV inventory export with suggested reorder quantities

**Dashboard**
- Live inventory view with device fleet status
- Count history charts (Plotly.js, auto-bucketed)
- BOM manager and stock adjustment interface
- Production orders lifecycle management
- Procurement and supplier management page

---

## Why Self-Hosted?

- **No monthly fees.** Runs on any local machine or inexpensive VPS.
- **Your data stays yours.** Nothing leaves your network unless you set up a webhook.
- **No vendor lock-in.** Standard PostgreSQL + Flask — any developer can maintain it.
- **Scales naturally.** Add counters by powering them on. The system self-enrolls them.

---

*Delivered complete with source code, database migrations, documentation, and firmware for both Arduino and Zephyr RTOS toolchains.*
