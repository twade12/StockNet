-- Migration: Add warning/critical threshold columns to devices
-- Run this against the StockNet database before deploying the updated app.py

ALTER TABLE devices ADD COLUMN IF NOT EXISTS warning_threshold int4 NULL;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS critical_threshold int4 NULL;

COMMENT ON COLUMN devices.warning_threshold IS 'Count at or below which a warning (yellow) alert is raised';
COMMENT ON COLUMN devices.critical_threshold IS 'Count at or below which a critical (red) alert is raised';
