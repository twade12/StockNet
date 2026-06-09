-- 009_device_health.sql
-- Add RSSI and temperature snapshot columns to devices (latest reported values)
ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS rssi           INTEGER,
    ADD COLUMN IF NOT EXISTS temperature_c  NUMERIC(5,2);

-- Time-series health readings — one row per counter check-in
CREATE TABLE IF NOT EXISTS device_health_readings (
    reading_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id       UUID        REFERENCES devices(device_id) ON DELETE SET NULL,
    device_name     TEXT        NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    battery_voltage NUMERIC(5,3),
    battery_pct     NUMERIC(5,1),   -- computed: (voltage-3.0)/(4.2-3.0)*100
    rssi            INTEGER,         -- dBm; negative; typically -30 (excellent) to -90 (poor)
    temperature_c   NUMERIC(5,2),    -- ESP32 internal die temperature
    ip_address      TEXT
);

-- Primary query pattern: latest N readings for a named device
CREATE INDEX IF NOT EXISTS idx_health_device_time
    ON device_health_readings (device_name, recorded_at DESC);
