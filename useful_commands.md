### Set part name
```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_name",
    "payload": {"part_name": "10k Resistor"}
  }'
```

### Set count
```bash
curl -X POST http://localhost:5007/api/device/commands/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "device_name": "Counter-A02",
    "command_type": "set_count",
    "payload": {"count": 80}
  }'
```

### Set WiFi
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