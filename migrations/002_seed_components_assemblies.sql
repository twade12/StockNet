-- Seed data: electronic components and assemblies for testing
-- Run after 001_device_thresholds.sql

-- ═══════════════ COMPONENTS ═══════════════
INSERT INTO components (part_id, part_name, description, min_threshold) VALUES
  ('R-10K',      'Resistor 10kΩ',         '1/4W carbon film resistor, 10kΩ ±5%',              100),
  ('R-4K7',      'Resistor 4.7kΩ',        '1/4W carbon film resistor, 4.7kΩ ±5%',              80),
  ('R-100',      'Resistor 100Ω',         '1/4W carbon film resistor, 100Ω ±5%',              100),
  ('C-100U',     'Capacitor 100µF',       'Electrolytic capacitor 100µF 25V',                   50),
  ('C-10U',      'Capacitor 10µF',        'Ceramic capacitor 10µF 16V',                         60),
  ('C-100N',     'Capacitor 100nF',       'Ceramic capacitor 100nF 50V (decoupling)',          200),
  ('LED-RED',    'LED Red 5mm',           'Standard red LED, 5mm, 20mA, 2V',                  150),
  ('LED-GRN',    'LED Green 5mm',         'Standard green LED, 5mm, 20mA, 2.1V',              150),
  ('IC-7805',    'Voltage Regulator 5V',  'LM7805 TO-220, 5V 1A linear regulator',             25),
  ('IC-ESP32',   'ESP32-WROOM-32',        'ESP32 WiFi+BLE module, 4MB flash',                   20),
  ('IC-ATMEGA',  'ATmega328P',            'ATmega328P-PU DIP-28, 8-bit AVR MCU',               15),
  ('CONN-USB-C', 'USB-C Connector',       'USB Type-C receptacle, SMD, 24-pin',                 40),
  ('CONN-HDR20', 'Pin Header 2×10',       '2.54mm pitch, 2×10 male pin header',                 60),
  ('CONN-JST2',  'JST-PH 2-pin',         'JST PH 2-pin connector, right angle',                80),
  ('PCB-PROTO',  'Protoboard 5×7cm',      'FR4 prototype PCB, 5×7cm, double-sided',            30),
  ('SW-TACT',    'Tactile Switch 6mm',    '6×6mm momentary tactile push button',               200),
  ('DIODE-1N4',  'Diode 1N4007',         '1N4007 rectifier diode, 1A 1000V',                  100),
  ('XTAL-16M',   'Crystal 16MHz',        '16MHz quartz crystal, HC-49S',                       30),
  ('OLED-096',   'OLED Display 0.96"',   '0.96" 128×64 I2C OLED display module, SSD1306',      10),
  ('BATT-CR',    'CR2032 Holder',        'CR2032 coin cell battery holder, THT',                40)
ON CONFLICT (part_id) DO NOTHING;

-- ═══════════════ ASSEMBLIES ═══════════════
INSERT INTO assemblies (assembly_code, assembly_name, description, min_threshold) VALUES
  ('ASM-PSU5V',   '5V Power Supply Module',     'Regulated 5V DC power supply with reverse polarity protection',  5),
  ('ASM-SENSOR',  'WiFi Sensor Board',          'ESP32-based sensor node with OLED display and status LEDs',      3),
  ('ASM-CTRLR',   'Main Controller Unit',        'ATmega328P controller with USB-C interface and expansion headers', 5),
  ('ASM-LED-IND', 'LED Indicator Panel',         '6-LED status indicator panel with current-limiting resistors',  8),
  ('ASM-BATT',    'Battery Backup Module',       'CR2032 battery backup with voltage monitoring',                 10)
ON CONFLICT (assembly_code) DO NOTHING;

-- ═══════════════ BILL OF MATERIALS ═══════════════
-- Link components to assemblies with required quantities

-- 5V Power Supply Module BOM
INSERT INTO assembly_components (assembly_id, component_id, quantity_required)
SELECT a.assembly_id, c.component_id, q.qty
FROM (VALUES
  ('ASM-PSU5V', 'IC-7805',   1),
  ('ASM-PSU5V', 'C-100U',    2),
  ('ASM-PSU5V', 'C-100N',    3),
  ('ASM-PSU5V', 'DIODE-1N4', 2),
  ('ASM-PSU5V', 'R-100',     1),
  ('ASM-PSU5V', 'LED-GRN',   1),
  ('ASM-PSU5V', 'CONN-JST2', 2),
  ('ASM-PSU5V', 'PCB-PROTO', 1)
) AS q(asm_code, part_id, qty)
JOIN assemblies a ON a.assembly_code = q.asm_code
JOIN components c ON c.part_id = q.part_id
ON CONFLICT (assembly_id, component_id) DO NOTHING;

-- WiFi Sensor Board BOM
INSERT INTO assembly_components (assembly_id, component_id, quantity_required)
SELECT a.assembly_id, c.component_id, q.qty
FROM (VALUES
  ('ASM-SENSOR', 'IC-ESP32',   1),
  ('ASM-SENSOR', 'OLED-096',   1),
  ('ASM-SENSOR', 'C-10U',      2),
  ('ASM-SENSOR', 'C-100N',     4),
  ('ASM-SENSOR', 'R-10K',      3),
  ('ASM-SENSOR', 'R-4K7',      2),
  ('ASM-SENSOR', 'LED-RED',    1),
  ('ASM-SENSOR', 'LED-GRN',    1),
  ('ASM-SENSOR', 'CONN-HDR20', 1),
  ('ASM-SENSOR', 'SW-TACT',    2),
  ('ASM-SENSOR', 'PCB-PROTO',  1)
) AS q(asm_code, part_id, qty)
JOIN assemblies a ON a.assembly_code = q.asm_code
JOIN components c ON c.part_id = q.part_id
ON CONFLICT (assembly_id, component_id) DO NOTHING;

-- Main Controller Unit BOM
INSERT INTO assembly_components (assembly_id, component_id, quantity_required)
SELECT a.assembly_id, c.component_id, q.qty
FROM (VALUES
  ('ASM-CTRLR', 'IC-ATMEGA',  1),
  ('ASM-CTRLR', 'XTAL-16M',   1),
  ('ASM-CTRLR', 'C-100N',     3),
  ('ASM-CTRLR', 'C-10U',      1),
  ('ASM-CTRLR', 'R-10K',      2),
  ('ASM-CTRLR', 'R-100',      1),
  ('ASM-CTRLR', 'CONN-USB-C', 1),
  ('ASM-CTRLR', 'CONN-HDR20', 2),
  ('ASM-CTRLR', 'LED-RED',    1),
  ('ASM-CTRLR', 'LED-GRN',    1),
  ('ASM-CTRLR', 'SW-TACT',    1),
  ('ASM-CTRLR', 'PCB-PROTO',  1)
) AS q(asm_code, part_id, qty)
JOIN assemblies a ON a.assembly_code = q.asm_code
JOIN components c ON c.part_id = q.part_id
ON CONFLICT (assembly_id, component_id) DO NOTHING;

-- LED Indicator Panel BOM
INSERT INTO assembly_components (assembly_id, component_id, quantity_required)
SELECT a.assembly_id, c.component_id, q.qty
FROM (VALUES
  ('ASM-LED-IND', 'LED-RED',    3),
  ('ASM-LED-IND', 'LED-GRN',    3),
  ('ASM-LED-IND', 'R-100',      6),
  ('ASM-LED-IND', 'CONN-JST2',  1),
  ('ASM-LED-IND', 'PCB-PROTO',  1)
) AS q(asm_code, part_id, qty)
JOIN assemblies a ON a.assembly_code = q.asm_code
JOIN components c ON c.part_id = q.part_id
ON CONFLICT (assembly_id, component_id) DO NOTHING;

-- Battery Backup Module BOM
INSERT INTO assembly_components (assembly_id, component_id, quantity_required)
SELECT a.assembly_id, c.component_id, q.qty
FROM (VALUES
  ('ASM-BATT', 'BATT-CR',   1),
  ('ASM-BATT', 'DIODE-1N4', 1),
  ('ASM-BATT', 'C-100N',    1),
  ('ASM-BATT', 'R-10K',     2),
  ('ASM-BATT', 'R-4K7',     1),
  ('ASM-BATT', 'CONN-JST2', 1)
) AS q(asm_code, part_id, qty)
JOIN assemblies a ON a.assembly_code = q.asm_code
JOIN components c ON c.part_id = q.part_id
ON CONFLICT (assembly_id, component_id) DO NOTHING;
