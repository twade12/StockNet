-- Migration: Add battery monitoring columns
-- Tracks battery voltage per device from IoT counter POST payloads
-- and enables low-battery alerting (warning < 3.4V, critical < 3.2V)

-- ── Devices: store latest battery reading ──
ALTER TABLE devices ADD COLUMN IF NOT EXISTS battery_voltage numeric NULL;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS battery_adc_mv integer NULL;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS battery_adc_raw integer NULL;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS battery_updated_at timestamptz NULL;

COMMENT ON COLUMN devices.battery_voltage IS 'Latest battery voltage reading (volts)';
COMMENT ON COLUMN devices.battery_adc_mv IS 'Latest ADC millivolt reading from voltage divider';
COMMENT ON COLUMN devices.battery_adc_raw IS 'Latest raw ADC value';
COMMENT ON COLUMN devices.battery_updated_at IS 'Timestamp of last battery reading';

-- ── Counter events: store battery data per event ──
ALTER TABLE counter_events ADD COLUMN IF NOT EXISTS battery_voltage numeric NULL;
ALTER TABLE counter_events ADD COLUMN IF NOT EXISTS battery_adc_mv integer NULL;
ALTER TABLE counter_events ADD COLUMN IF NOT EXISTS battery_adc_raw integer NULL;

COMMENT ON COLUMN counter_events.battery_voltage IS 'Battery voltage at time of POST';
COMMENT ON COLUMN counter_events.battery_adc_mv IS 'ADC millivolt reading at time of POST';
COMMENT ON COLUMN counter_events.battery_adc_raw IS 'Raw ADC value at time of POST';
