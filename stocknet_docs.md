# SmartCounter User/API Command Reference

## Overview

This document summarizes the current ways to configure and interact with a SmartCounter device:

- **BLE commands** for local setup from a phone app such as LightBlue or nRF Connect
- **HTTP API calls** for backend/device management through the Flask server

The SmartCounter currently supports:
- device naming
- part name / part ID
- count updates
- display timeout
- display on/off
- reset count
- WiFi SSID / password
- API URL
- WiFi test
- manual POST to server
- backend command queue and acknowledgements

---

## 1. BLE command reference

### How to connect

1. Power on the SmartCounter.
2. Open a BLE app such as **LightBlue** or **nRF Connect**.
3. Scan for the device.
4. Connect to the SmartCounter.
5. Open the Nordic UART / UART-style writable characteristic.
6. Send a command as plain text.
7. Read response notifications from the TX/notify characteristic.

### Command format

Commands are sent as simple text lines like:

```text
NAME=10k Resistor
COUNT=42
DISPLAY=ON
GET
```

Commands are case-insensitive in the current firmware logic, but using uppercase command names is recommended for consistency.

---

### Core BLE commands

#### `HELP`

Returns a summary of supported BLE commands.

Example:

```text
HELP
```

---

#### `GET`

Returns the current in-device configuration/state.

Example:

```text
GET
```

Typical response:

```text
DEVNAME=Counter-A02
NAME=10k Resistor
ID=R-10K-0603
COUNT=80
TIMEOUT=15
DISPLAY=ON
WIFI_SSID=WarehouseWiFi
API_URL=http://192.168.1.100:5007/api/counter
```

---

#### `DEVNAME=<value>`

Sets the SmartCounter device name used for BLE visibility and backend identity.

Example:

```text
DEVNAME=Counter-A02
```

Notes:
- Keep names unique across the fleet.
- Recommended naming examples:
  - `Counter-A01`
  - `Counter-Shelf03-Bin02`
  - `INV-0007`

---

#### `NAME=<value>`

Sets the part name shown on the display and reported to the backend.

Example:

```text
NAME=10k Resistor
```

---

#### `ID=<value>`

Sets the part ID shown on the display and reported to the backend.

Example:

```text
ID=R-10K-0603
```

---

#### `COUNT=<number>`

Sets the current count directly.

Example:

```text
COUNT=125
```

---

#### `TIMEOUT=<seconds>`

Sets the display auto-off timeout in seconds.

Example:

```text
TIMEOUT=20
```

Range currently enforced in firmware:

- minimum: `3`
- maximum: `600`

---

#### `DISPLAY=ON`

Turns the OLED display on.

Example:

```text
DISPLAY=ON
```

---

#### `DISPLAY=OFF`

Turns the OLED display off.

Example:

```text
DISPLAY=OFF
```

---

#### `RESET`

Resets the count to zero.

Example:

```text
RESET
```

---

### WiFi / backend BLE commands

#### `WIFI_SSID=<ssid>`

Sets the WiFi SSID stored on the device.

Example:

```text
WIFI_SSID=WarehouseWiFi
```

---

#### `WIFI_PASS=<password>`

Sets the WiFi password stored on the device.

Example:

```text
WIFI_PASS=MySecretPassword
```

Note:
- This is convenient for setup but not secure against local BLE access.
- Suitable for development or trusted local provisioning.

---

#### `API_URL=<url>`

Sets the backend API endpoint used by the SmartCounter for POSTs.

Example:

```text
API_URL=http://192.168.1.100:5007/api/counter
```

Typical format:

```text
http://<server-ip>:5007/api/counter
```

---

#### `WIFI_TEST`

Attempts to connect to the configured WiFi network and reports the IP if successful.

Example:

```text
WIFI_TEST
```

Typical success response:

```text
OK WIFI IP=192.168.1.55
```

---

#### `POST_NOW`

Immediately sends the current device state to the backend API.

Example:

```text
POST_NOW
```

Typical success response:

```text
OK POST 200
```

---

### Physical button behavior summary

These are not BLE commands, but users should know them:

- **UP button**: increment count
- **DOWN button**: decrement count
- **SHOW short press**: toggle display on/off
- **SHOW long press**: reset count to zero
- display auto-off occurs after the configured timeout

---

## 2. HTTP API reference

Base URL examples:

```text
http://localhost:5007
http://192.168.1.100:5007
```

---

### 2.1 Health check

#### `GET /api/health`

Checks whether the Flask server is alive.

Example:

```bash
curl http://localhost:5007/api/health
```

Example response:

```json
{"ok": true}
```

---

### 2.2 Counter ingest endpoint

#### `POST /api/counter`

Used by the SmartCounter to POST its current state to the backend.

Example:

```bash
curl -X POST http://localhost:5007/api/counter \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "part_name": "10k Resistor",
    "part_id": "R-10K-0603",
    "count": 80,
    "ip": "10.0.0.60",
    "firmware_version": "1.0.0",
    "display_timeout_sec": 15,
    "wifi_ssid": "WarehouseWiFi"
  }'
```

Required fields:
- `device_name`
- `part_name`
- `part_id`
- `count`

Optional fields:
- `ip`
- `firmware_version`
- `display_timeout_sec`
- `wifi_ssid`

Typical behavior:
- upserts the device
- inserts a row into `counter_events`
- updates inventory if the device is assigned
- returns any queued pending commands

---

### 2.3 Get pending device commands

#### `GET /api/device/commands?device_name=<device_name>`

Returns currently pending backend commands for a device.

Example:

```bash
curl "http://localhost:5007/api/device/commands?device_name=Counter-A02"
```

Example response:

```json
{
  "ok": true,
  "device_name": "Counter-A02",
  "commands": [
    {
      "command_id": "11111111-2222-3333-4444-555555555555",
      "command_type": "set_count",
      "payload": {"count": 80},
      "created_at": "2026-03-08T23:10:00+00:00"
    }
  ]
}
```

---

### 2.4 Acknowledge a device command

#### `POST /api/device/commands/ack`

Used by the SmartCounter after applying a backend command.

Example success ack:

```bash
curl -X POST http://localhost:5007/api/device/commands/ack \
  -H "Content-Type: application/json" \
  -d '{
    "command_id": "11111111-2222-3333-4444-555555555555",
    "status": "acked",
    "result_message": "count updated"
  }'
```

Example failure ack:

```bash
curl -X POST http://localhost:5007/api/device/commands/ack \
  -H "Content-Type: application/json" \
  -d '{
    "command_id": "11111111-2222-3333-4444-555555555555",
    "status": "failed",
    "result_message": "missing count"
  }'
```

Supported statuses:
- `acked`
- `failed`
- `cancelled`

---

### 2.5 Enqueue a command for a device

#### `POST /api/device/commands/enqueue`

Creates a pending command in Postgres for a SmartCounter to fetch and apply.

General example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_count",
    "payload": {"count": 80}
  }'
```

---

## 3. Backend command queue reference

These are the recommended `command_type` values for backend-to-device configuration.

### `set_devname`

Changes the SmartCounter device name.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_devname",
    "payload": {"device_name": "Counter-A22"}
  }'
```

Payload fields:
- `device_name`

---

### `set_name`

Sets the part name.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_name",
    "payload": {"part_name": "10k Resistor"}
  }'
```

Payload fields:
- `part_name`

---

### `set_id`

Sets the part ID.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_id",
    "payload": {"part_id": "R-10K-0603"}
  }'
```

Payload fields:
- `part_id`

---

### `set_count`

Sets the count.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_count",
    "payload": {"count": 80}
  }'
```

Payload fields:
- `count`

---

### `set_timeout`

Sets the display timeout.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_timeout",
    "payload": {"display_timeout_sec": 20}
  }'
```

Payload fields:
- `display_timeout_sec`

---

### `set_post_interval`

Sets the POST interval in seconds.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_post_interval",
    "payload": {"post_interval_sec": 600}
  }'
```

Payload fields:
- `post_interval_sec`

---

### `set_wifi`

Sets WiFi SSID and optionally password.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_wifi",
    "payload": {
      "wifi_ssid": "WarehouseWiFi",
      "wifi_pass": "secret123"
    }
  }'
```

Payload fields:
- `wifi_ssid`
- `wifi_pass` (optional in code path, but usually provided)

---

### `set_api_url`

Sets the SmartCounter API URL.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_api_url",
    "payload": {
      "api_url": "http://192.168.1.100:5007/api/counter"
    }
  }'
```

Payload fields:
- `api_url`

---

### `display_on`

Turns the display on.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "display_on",
    "payload": {}
  }'
```

---

### `display_off`

Turns the display off.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "display_off",
    "payload": {}
  }'
```

---

### `reset_count`

Resets the count to zero.

Example:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "reset_count",
    "payload": {}
  }'
```

---

## 4. Typical user workflows

### Local setup from phone

1. Connect to device over BLE with LightBlue.
2. Send:
   - `DEVNAME=Counter-A02`
   - `NAME=10k Resistor`
   - `ID=R-10K-0603`
   - `WIFI_SSID=WarehouseWiFi`
   - `WIFI_PASS=secret123`
   - `API_URL=http://192.168.1.100:5007/api/counter`
3. Test with:
   - `WIFI_TEST`
   - `POST_NOW`
4. Confirm with:
   - `GET`

---

### Backend-driven fleet update

Example: change part name from the server

1. Enqueue command:

```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_name",
    "payload": {"part_name": "22uF Capacitor"}
  }'
```

2. Device polls `/api/device/commands`
3. Device applies update
4. Device sends `/api/device/commands/ack`
5. Device continues normal POSTing to `/api/counter`

---

## 5. Recommended operational notes

- Keep `device_name` unique across all counters.
- Use backend command queue for production fleet management.
- Use BLE mainly for local provisioning and recovery.
- Treat backend assignment in Postgres as the source of truth for what a device represents.
- Protect the Flask API before production rollout if used outside a trusted local network.

---

## 6. Quick command summary

### BLE commands

- `HELP`
- `GET`
- `DEVNAME=<value>`
- `NAME=<value>`
- `ID=<value>`
- `COUNT=<number>`
- `TIMEOUT=<seconds>`
- `DISPLAY=ON`
- `DISPLAY=OFF`
- `RESET`
- `WIFI_SSID=<ssid>`
- `WIFI_PASS=<password>`
- `API_URL=<url>`
- `WIFI_TEST`
- `POST_NOW`

### HTTP API endpoints

- `GET /api/health`
- `POST /api/counter`
- `GET /api/device/commands?device_name=...`
- `POST /api/device/commands/ack`
- `POST /api/device/commands/enqueue`

### Backend command types

- `set_devname`
- `set_name`
- `set_id`
- `set_count`
- `set_timeout`
- `set_wifi`
- `set_api_url`
- `display_on`
- `display_off`
- `reset_count`
