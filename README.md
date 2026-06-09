<p align="center">
  <img src="logo.svg" alt="StockNet" width="400"/>
</p>

<p align="center">
  A fully self-hosted, end-to-end IoT inventory management system — custom ESP32-S3 hardware counters paired with a Flask/PostgreSQL fleet management backend.
</p>

---

## Overview

Each **SmartCounter** device lives at a component bin and reports its live count over WiFi. The backend handles device self-registration, real-time inventory tracking, Bill-of-Materials deduction, tiered alerting (low-stock and low-battery), webhook fanout to Slack, production order planning, and cycle count scheduling — all on your own hardware with zero ongoing SaaS costs.

## Architecture

```
SmartCounters (ESP32-S3)
  │  WiFi  POST /api/counter  (count + battery telemetry)
  │  BLE   Nordic UART Service (zero-touch provisioning)
  ▼
Flask REST API  ──────────────►  PostgreSQL
  │
  ├── Device fleet & BLE command queue
  ├── Inventory engine  (components + assemblies)
  ├── BOM deduction  (auto-decrement on assembly build)
  ├── Tiered alerts  (low-stock · low-battery)
  ├── Webhook fanout  (HMAC-SHA256 signed)
  ├── Production orders & component reservations
  ├── Supplier orders & cycle counts
  └── Web dashboard  (Plotly.js time-series · dark industrial theme)
```

## Tech Stack

| Layer | Technology |
|---|---|
| Firmware (Arduino) | C++ · ESP-IDF · SSD1306 · NVS |
| Firmware (production) | C · Zephyr RTOS · nRF Connect SDK |
| Backend | Python 3.10 · Flask 3.x · psycopg3 · connection pool |
| Database | PostgreSQL 14+ |
| Frontend | Plain HTML/CSS/JS · Plotly.js |
| Server | Gunicorn |

## Hardware — SmartCounter (ESP32-S3)

Each device is built on the **Seeed XIAO ESP32-S3**:

- **OLED display** (SSD1306) — part name, part ID, and live count
- **Three buttons** — UP, DOWN, SHOW with tap / double-tap / long-press FSM
- **Battery monitoring** — hardware voltage divider + ADC sampling; reports raw ADC, millivolts, and computed percentage (3.0 V = 0 %, 4.2 V = 100 %)
- **WiFi 802.11 b/g/n** — periodic count POST with configurable interval
- **BLE** (Nordic UART Service) — phone-app provisioning in under a minute
- **Deep-sleep** power management

Firmware ships in two variants:

| Variant | Source | Notes |
|---|---|---|
| Arduino / ESP-IDF | `smartcounter.cpp` | Rapid prototyping and immediate deployment |
| Zephyr RTOS | `smartcounter_zephyr/` | OTA-upgrade-ready, production-grade |

See [`smartcounter_zephyr/doc/PORT.md`](smartcounter_zephyr/doc/PORT.md) for a line-by-line mapping between both implementations.

## Prerequisites

- **PostgreSQL 14+**
- **Python 3.10+**

## Setup

**1. Clone**

```bash
git clone <repo-url>
cd StockNet
```

**2. Create a virtual environment and install dependencies**

```bash
python -m venv .venv
source .venv/bin/activate
pip install flask "psycopg[binary]" psycopg-pool gunicorn
```

> `requirements.txt` is a full system pip-freeze. Use the command above for a minimal install.

**3. Create the database**

```bash
createdb StockNet
```

**4. Run migrations in order**

```bash
for f in migrations/*.sql; do psql -d StockNet -f "$f"; done
```

**5. Configure the connection string**

Edit `DATABASE_URL` at the top of [`app.py`](app.py):

```python
DATABASE_URL = "postgresql://user:password@localhost:5432/StockNet"
```

**6. Run**

Development:
```bash
flask --app app run --port 5007
```

Production:
```bash
gunicorn -w 4 -b 0.0.0.0:5007 app:app
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `FLASK_PORT` | `5007` | Port the server listens on |
| `FLASK_HOST` | `0.0.0.0` | Interface to bind |

## API Reference

### Core

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web dashboard |
| `GET` | `/api/health` | Database connectivity check |

### Devices & Command Queue

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/counter` | Ingest a count event; auto-registers new devices |
| `GET` | `/api/devices` | List active devices with latest count and battery |
| `POST` | `/api/device/thresholds` | Set per-device warning / critical thresholds |
| `POST` | `/api/device/commands/enqueue` | Push a command to a device |
| `POST` | `/api/device/commands/ack` | Firmware acknowledges a command |
| `GET` | `/api/device/commands?device_name=X` | Poll pending commands |
| `GET` | `/api/commands/pending` | All pending commands across the fleet |

### Inventory

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/inventory` | Live stock across all devices |
| `POST` | `/api/inventory/adjust` | Manual adjustment with BOM deduction |
| `GET` | `/api/inventory/export` | CSV with deficit and reorder suggestions |
| `GET` | `/api/timeseries` | Bucketed count history (Plotly feed) |
| `GET` | `/api/devices/<name>/latest-count` | Latest reading for one device |

### Components

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/components` | List components (supports `?search=`) |
| `POST` | `/api/components` | Create a component |
| `PUT` | `/api/components/<id>` | Update (pushes threshold command to assigned devices) |
| `DELETE` | `/api/components/<id>` | Soft-delete |

### Assemblies & BOM

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/assemblies` | List assemblies with full BOM and stock |
| `POST` | `/api/assemblies` | Create an assembly |
| `PUT` | `/api/assemblies/<id>` | Update |
| `DELETE` | `/api/assemblies/<id>` | Soft-delete |

### Alerts

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/alerts` | Open / acknowledged alerts (supports `?status=`) |
| `POST` | `/api/alerts/acknowledge` | Acknowledge an alert |
| `POST` | `/api/alerts/clear` | Clear an alert |

## Command Types

Commands enqueued via `POST /api/device/commands/enqueue` are picked up by devices on their next POST cycle:

| `command_type` | Payload | Effect |
|---|---|---|
| `set_count` | `{"count": N}` | Override the displayed count |
| `set_name` | `{"name": "..."}` | Update part name on the OLED |
| `set_id` | `{"id": "..."}` | Update part ID |
| `set_wifi` | `{"ssid": "...", "password": "..."}` | Reconfigure WiFi credentials |
| `set_post_interval` | `{"interval_ms": N}` | Change POST frequency |
| `set_timeout` | `{"timeout_s": N}` | OLED sleep timeout |
| `display_on` / `display_off` | — | Toggle the display |
| `reset_count` | — | Zero the counter |
| `set_devname` | `{"name": "..."}` | Rename device in fleet |
| `set_api_url` | `{"url": "..."}` | Update backend endpoint on device |

## Project Structure

```
StockNet/
├── app.py                        # Flask application (REST API)
├── templates/
│   ├── index.html                # Main dashboard
│   ├── bom.html                  # BOM manager
│   ├── orders.html               # Production orders
│   └── procurement.html          # Procurement dashboard
├── migrations/                   # Ordered PostgreSQL DDL (001 → 009)
├── smartcounter.cpp              # Firmware — Arduino/ESP-IDF variant
├── smartcounter_battery.cpp      # Battery-monitoring firmware variant
├── smartcounter_zephyr/
│   ├── src/                      # C sources (main, BLE, display, network, …)
│   ├── boards/                   # Device tree overlay (XIAO ESP32-S3)
│   ├── prj.conf                  # Kconfig
│   └── doc/PORT.md               # Arduino → Zephyr migration guide
└── marketing/                    # Architecture diagram and feature docs
```

## License

MIT
