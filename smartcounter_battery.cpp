#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <NimBLEDevice.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include <esp_mac.h>
#include <esp_sleep.h>
#include <driver/gpio.h>
#include <driver/rtc_io.h>
#include <driver/temperature_sensor.h>

// ============================================================
// CONFIG
// ============================================================

#define DEBUG_SERIAL 1

#if DEBUG_SERIAL
  #define DBG_BEGIN(x)    Serial.begin(x)
  #define DBG_PRINT(x)    Serial.print(x)
  #define DBG_PRINTLN(x)  Serial.println(x)
  #define DBG_PRINTF(...) Serial.printf(__VA_ARGS__)
#else
  #define DBG_BEGIN(x)
  #define DBG_PRINT(x)
  #define DBG_PRINTLN(x)
  #define DBG_PRINTF(...)
#endif

// -------------------------
// OLED
// -------------------------
#define SCREEN_WIDTH    128
#define SCREEN_HEIGHT    64
#define OLED_RESET       -1
#define SCREEN_ADDRESS  0x3C

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// -------------------------
// BUTTONS (XIAO ESP32S3)
// -------------------------
#define BTN_UP    D0
#define BTN_DOWN  D1
#define BTN_SHOW  D2

static const gpio_num_t WAKE_SHOW_GPIO = (gpio_num_t)BTN_SHOW;

// -------------------------
// BATTERY MONITOR (XIAO ESP32S3)
// A9 reads divider midpoint
// D8 enables divider through MOSFET network
// -------------------------
#define BATTERY_ADC_PIN       A9
#define BATTERY_SENSE_EN_PIN  D8

// Flip these if your MOSFET control is active-low
static const uint8_t BATTERY_SENSE_ON  = HIGH;
static const uint8_t BATTERY_SENSE_OFF = LOW;

// Voltage divider: top resistor between P-MOSFET drain and ADC pin,
// bottom resistor between ADC pin and GND.
// Vadc = Vbat * (R_BOTTOM / (R_TOP + R_BOTTOM))
// Vbat = Vadc * (R_TOP + R_BOTTOM) / R_BOTTOM  <- DIVIDER_SCALE
static const float BATTERY_R_TOP_OHMS    = 3300.0f;   // 3.3 kΩ (drain → ADC)
static const float BATTERY_R_BOTTOM_OHMS = 6800.0f;   // 6.8 kΩ (ADC → GND)
static const float BATTERY_DIVIDER_SCALE =
  (BATTERY_R_TOP_OHMS + BATTERY_R_BOTTOM_OHMS) / BATTERY_R_BOTTOM_OHMS;

// Sampling behavior
static const uint8_t  BATTERY_SAMPLE_COUNT     = 8;
static const uint16_t BATTERY_ENABLE_SETTLE_MS = 3;
static const uint16_t BATTERY_SAMPLE_GAP_MS    = 2;

// Optional calibration factor if you compare against a DMM later
static const float BATTERY_CALIBRATION_GAIN = 1.000f;

// -------------------------
// BLE UART UUIDs
// -------------------------
static const char* SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* RX_UUID      = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* TX_UUID      = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E";

// -------------------------
// Network defaults
// -------------------------
const char* DEFAULT_WIFI_SSID = "Optimus";
const char* DEFAULT_WIFI_PASS = "WindWaker56$&";
const char* DEFAULT_API_URL   = "https://stocknet.resensys.cloud/api/counter";

// false = only apply defaults if missing
// true  = overwrite stored values every boot
const bool FORCE_DEFAULT_NETWORK_CONFIG = false;

// -------------------------
// Timing / power behavior
// -------------------------
static const uint32_t DEFAULT_DISPLAY_TIMEOUT_SEC = 15;
static const uint32_t DEFAULT_POST_INTERVAL_SEC   = 3600;

static const uint32_t MIN_POST_INTERVAL_SEC       = 5;
static const uint32_t MAX_POST_INTERVAL_SEC       = 86400;

static const uint32_t SHOW_LONG_PRESS_MS          = 3000;
static const uint32_t SHOW_DOUBLE_PRESS_MS        = 400;
static const uint32_t DEBOUNCE_MS                 = 60;

static const uint32_t BLE_ACTIVE_WINDOW_MS        = 60000;
static const uint32_t LOCAL_AWAKE_WINDOW_MS       = 15000;
static const uint32_t TIMER_AWAKE_WINDOW_MS       = 15000;
static const uint32_t BOOT_AWAKE_WINDOW_MS        = 5000;
static const uint32_t SAVE_DEFER_MS               = 2000;

// BLE command queue sizing
static const uint8_t BLE_CMD_QUEUE_LEN = 4;
static const size_t  BLE_CMD_MAX_LEN   = 192;

// ============================================================
// GLOBAL STATE
// ============================================================

Preferences prefs;

// Stored state
String deviceName  = "SC-01";
String partName    = "PART";
String partID      = "0001";
int    countValue  = 0;
int    warnThreshold = 0;    // 0 = disabled; show LOW indicator when count <= this
int    critThreshold = 0;    // 0 = disabled; show CRIT indicator when count <= this

// Health telemetry — updated each time WiFi connects / temperature is read
int   lastRssi    = 0;       // dBm; 0 = not yet measured
float lastTempC   = -999.0f; // °C; -999 = sensor unavailable

// Internal temperature sensor handle (IDF v5 / Arduino core 3.x)
static temperature_sensor_handle_t s_tsens = nullptr;

uint32_t displayTimeoutSec = DEFAULT_DISPLAY_TIMEOUT_SEC;
uint32_t postIntervalSec   = DEFAULT_POST_INTERVAL_SEC;

String wifiSSID = "";
String wifiPass = "";
String apiUrl   = "";

// Display state
enum DisplayMode { DISP_COUNT, DISP_BATTERY };
DisplayMode displayMode = DISP_COUNT;
bool displayEnabled = false;
unsigned long lastUserActivityMs = 0;

// Small status overlay
String displayStatusLine1 = "";
String displayStatusLine2 = "";
bool statusOverlayActive = false;

// Save management
bool settingsDirty = false;
unsigned long settingsDirtyAtMs = 0;

// BLE state
static NimBLECharacteristic* txCharacteristic = nullptr;
static NimBLECharacteristic* rxCharacteristic = nullptr;
static NimBLEServer* bleServer = nullptr;
static bool bleClientConnected = false;
static bool bleStarted = false;
static bool bleAdvertisingActive = false;
unsigned long bleActiveUntilMs = 0;

// BLE event flags
volatile bool bleConnectedEvent = false;
volatile bool bleDisconnectedEvent = false;

// BLE deferred RX queue
char bleCmdQueue[BLE_CMD_QUEUE_LEN][BLE_CMD_MAX_LEN];
volatile uint8_t bleCmdHead = 0;
volatile uint8_t bleCmdTail = 0;
volatile uint8_t bleCmdCount = 0;

// SHOW button tracking
bool showPressed = false;
bool showLongPressHandled = false;
unsigned long showPressStartMs = 0;
bool showTapPending = false;
unsigned long showTapReleasedMs = 0;
bool manualNetworkWorkRequested = false;

// Button debounce / edge tracking
unsigned long lastUpAcceptedMs   = 0;
unsigned long lastDownAcceptedMs = 0;
unsigned long lastShowAcceptedMs = 0;

bool prevUpState   = HIGH;
bool prevDownState = HIGH;
bool prevShowState = HIGH;

// Session / wake management
enum WakeMode {
  WAKE_COLD_BOOT,
  WAKE_FROM_TIMER,
  WAKE_FROM_BUTTON
};

WakeMode wakeMode = WAKE_COLD_BOOT;
unsigned long awakeUntilMs = 0;
bool periodicWorkDone = false;

// RTC-retained boot counter
RTC_DATA_ATTR uint32_t bootCount = 0;

// ============================================================
// TYPES
// ============================================================

struct BatteryReading {
  uint16_t raw = 0;
  uint32_t adcMv = 0;
  float adcV = 0.0f;
  float batteryV = 0.0f;
  bool valid = false;
};

// ============================================================
// FORWARD DECLARATIONS
// ============================================================

void renderDisplay();
void renderBatteryDisplay();
void turnDisplayOn(bool openBleWindow = false);
void turnDisplayOff();
void toggleDisplay();

void showStatus(const String& line1, const String& line2 = "");
void clearStatus();

void startBleIfNeeded();
void startAdvertising();
void stopAdvertising();
void enableBleWindow(uint32_t windowMs = BLE_ACTIVE_WINDOW_MS);

bool applyServerCommand(const String& commandType, JsonObject payload, String& resultMessage);

bool connectWiFi(uint32_t timeoutMs = 12000);
void wifiOff();

bool postCounterUpdate();
bool fetchAndApplyPendingCommands();
bool ackDeviceCommand(const String& commandId, const String& status, const String& resultMessage);

void scheduleSave();
void flushSaveNow();
void flushSaveIfDue();

void markActivity(bool openBleWindow = false);
void processCommand(String cmd);
void processPendingBleEvents();
void processPendingBleCommands();
void handleButtonsPolled();

void configureDeepSleepWakeSources();
void enterDeepSleepNow();

WakeMode decodeWakeMode();
void handleWakeBootBehavior();
bool readyForDeepSleep();

void runPeriodicNetworkWork();
const char* wakeModeToString(WakeMode mode);

bool httpBeginForUrl(HTTPClient& http, const String& url, WiFiClientSecure& secureClient);
bool isHttpsUrl(const String& url);

void setBatterySenseEnabled(bool enabled);
BatteryReading readBatteryVoltage();

void   initTempSensor();
float  readInternalTempC();

// ============================================================
// HELPERS
// ============================================================

String trimCopy(const String& s) {
  String t = s;
  t.trim();
  return t;
}

String getDefaultDeviceName() {
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);

  char buf[32];
  snprintf(buf, sizeof(buf), "Counter-%02X%02X%02X", mac[3], mac[4], mac[5]);
  return String(buf);
}

String jsonEscape(const String& s) {
  String out;
  out.reserve(s.length() + 8);

  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    switch (c) {
      case '\\': out += "\\\\"; break;
      case '"':  out += "\\\""; break;
      case '\n': out += "\\n";  break;
      case '\r': out += "\\r";  break;
      case '\t': out += "\\t";  break;
      default:   out += c;      break;
    }
  }

  return out;
}

bool isHttpsUrl(const String& url) {
  return url.startsWith("https://");
}

bool httpBeginForUrl(HTTPClient& http, const String& url, WiFiClientSecure& secureClient) {
  if (isHttpsUrl(url)) {
    secureClient.setInsecure();
    return http.begin(secureClient, url);
  }
  return http.begin(url);
}

String buildAckUrl() {
  if (apiUrl.length() == 0) return "";

  String ackUrl = apiUrl;
  int idx = ackUrl.lastIndexOf("/api/counter");
  if (idx >= 0) {
    ackUrl = ackUrl.substring(0, idx) + "/api/device/commands/ack";
  } else {
    ackUrl = "";
  }
  return ackUrl;
}

String buildCmdUrl() {
  if (apiUrl.length() == 0) return "";

  String cmdUrl = apiUrl;
  int idx = cmdUrl.lastIndexOf("/api/counter");
  if (idx >= 0) {
    cmdUrl = cmdUrl.substring(0, idx) + "/api/device/commands?device_name=" + deviceName;
  } else {
    cmdUrl = "";
  }
  return cmdUrl;
}

const char* wakeModeToString(WakeMode mode) {
  switch (mode) {
    case WAKE_FROM_TIMER:  return "TIMER";
    case WAKE_FROM_BUTTON: return "BUTTON";
    case WAKE_COLD_BOOT:
    default:               return "COLD_BOOT";
  }
}

void markActivity(bool openBleWindow) {
  lastUserActivityMs = millis();
  awakeUntilMs = millis() + LOCAL_AWAKE_WINDOW_MS;

  if (openBleWindow) {
    enableBleWindow(BLE_ACTIVE_WINDOW_MS);
  }
}

void scheduleSave() {
  settingsDirty = true;
  settingsDirtyAtMs = millis();
}

void saveDataNow() {
  prefs.begin("counter", false);
  prefs.putString("devname", deviceName);
  prefs.putString("name", partName);
  prefs.putString("id", partID);
  prefs.putInt("count", countValue);
  prefs.putUInt("tout", displayTimeoutSec);
  prefs.putUInt("pint", postIntervalSec);
  prefs.putString("wssid", wifiSSID);
  prefs.putString("wpass", wifiPass);
  prefs.putString("apiurl", apiUrl);
  prefs.putInt("w_thr", warnThreshold);
  prefs.putInt("c_thr", critThreshold);
  prefs.end();

  DBG_PRINTLN("Saved settings to NVS");
}

void flushSaveNow() {
  if (!settingsDirty) return;
  saveDataNow();
  settingsDirty = false;
}

void flushSaveIfDue() {
  if (settingsDirty && (millis() - settingsDirtyAtMs >= SAVE_DEFER_MS)) {
    flushSaveNow();
  }
}

void loadData() {
  prefs.begin("counter", true);

  deviceName        = prefs.getString("devname", "");
  partName          = prefs.getString("name", "PART");
  partID            = prefs.getString("id", "0001");
  countValue        = prefs.getInt("count", 0);
  displayTimeoutSec = prefs.getUInt("tout", DEFAULT_DISPLAY_TIMEOUT_SEC);
  postIntervalSec   = prefs.getUInt("pint", DEFAULT_POST_INTERVAL_SEC);
  wifiSSID          = prefs.getString("wssid", "");
  wifiPass          = prefs.getString("wpass", "");
  apiUrl            = prefs.getString("apiurl", "");
  warnThreshold     = prefs.getInt("w_thr", 0);
  critThreshold     = prefs.getInt("c_thr", 0);

  prefs.end();

  if (deviceName.length() == 0) {
    deviceName = getDefaultDeviceName();
  }

  if (displayTimeoutSec < 3) displayTimeoutSec = 3;
  if (displayTimeoutSec > 600) displayTimeoutSec = 600;

  if (postIntervalSec < MIN_POST_INTERVAL_SEC) postIntervalSec = MIN_POST_INTERVAL_SEC;
  if (postIntervalSec > MAX_POST_INTERVAL_SEC) postIntervalSec = MAX_POST_INTERVAL_SEC;

  DBG_PRINTLN("Loaded settings from NVS");
  DBG_PRINT("deviceName = "); DBG_PRINTLN(deviceName);
  DBG_PRINT("partName = "); DBG_PRINTLN(partName);
  DBG_PRINT("partID = "); DBG_PRINTLN(partID);
  DBG_PRINT("countValue = "); DBG_PRINTLN(countValue);
  DBG_PRINT("displayTimeoutSec = "); DBG_PRINTLN(displayTimeoutSec);
  DBG_PRINT("postIntervalSec = "); DBG_PRINTLN(postIntervalSec);
  DBG_PRINT("wifiSSID = "); DBG_PRINTLN(wifiSSID);
  DBG_PRINT("apiUrl = "); DBG_PRINTLN(apiUrl);
}

void applyDefaultNetworkConfig() {
  bool changed = false;

  if (FORCE_DEFAULT_NETWORK_CONFIG || wifiSSID.length() == 0) {
    wifiSSID = DEFAULT_WIFI_SSID;
    changed = true;
  }

  if (FORCE_DEFAULT_NETWORK_CONFIG || wifiPass.length() == 0) {
    wifiPass = DEFAULT_WIFI_PASS;
    changed = true;
  }

  if (FORCE_DEFAULT_NETWORK_CONFIG || apiUrl.length() == 0) {
    apiUrl = DEFAULT_API_URL;
    changed = true;
  }

  if (postIntervalSec < MIN_POST_INTERVAL_SEC || postIntervalSec > MAX_POST_INTERVAL_SEC) {
    postIntervalSec = DEFAULT_POST_INTERVAL_SEC;
    changed = true;
  }

  if (changed) {
    saveDataNow();
    DBG_PRINTLN("Applied default network config");
  }
}

// ============================================================
// INTERNAL TEMPERATURE SENSOR (ESP32-S3, IDF v5)
// ============================================================

void initTempSensor() {
  temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
  esp_err_t err = temperature_sensor_install(&cfg, &s_tsens);
  if (err != ESP_OK) {
    s_tsens = nullptr;
    DBG_PRINTLN("Temp sensor install failed");
    return;
  }
  temperature_sensor_enable(s_tsens);
  DBG_PRINTLN("Temp sensor enabled");
}

float readInternalTempC() {
  if (!s_tsens) return -999.0f;
  float t = 0.0f;
  if (temperature_sensor_get_celsius(s_tsens, &t) != ESP_OK) return -999.0f;
  return t;
}

// ============================================================
// BATTERY
// ============================================================

void setBatterySenseEnabled(bool enabled) {
  digitalWrite(BATTERY_SENSE_EN_PIN, enabled ? BATTERY_SENSE_ON : BATTERY_SENSE_OFF);
}

BatteryReading readBatteryVoltage() {
  BatteryReading reading;

  setBatterySenseEnabled(true);
  delay(BATTERY_ENABLE_SETTLE_MS);

  // Throw away first sample after enabling
  (void)analogRead(BATTERY_ADC_PIN);
  delay(1);

  uint32_t rawSum = 0;
  uint32_t mvSum  = 0;

  for (uint8_t i = 0; i < BATTERY_SAMPLE_COUNT; i++) {
    rawSum += analogRead(BATTERY_ADC_PIN);
    mvSum  += analogReadMilliVolts(BATTERY_ADC_PIN);
    delay(BATTERY_SAMPLE_GAP_MS);
  }

  setBatterySenseEnabled(false);

  reading.raw   = rawSum / BATTERY_SAMPLE_COUNT;
  reading.adcMv = mvSum / BATTERY_SAMPLE_COUNT;
  reading.adcV  = reading.adcMv / 1000.0f;
  reading.batteryV = reading.adcV * BATTERY_DIVIDER_SCALE * BATTERY_CALIBRATION_GAIN;
  reading.valid = true;

  DBG_PRINT("Battery raw ADC: ");
  DBG_PRINTLN(reading.raw);
  DBG_PRINT("Battery ADC mV: ");
  DBG_PRINTLN(reading.adcMv);
  DBG_PRINT("Battery scaled V: ");
  DBG_PRINTLN(reading.batteryV);

  return reading;
}

String getStateText() {
  BatteryReading batt = readBatteryVoltage();

  String s;
  s.reserve(420);

  s += "DEVNAME=" + deviceName + "\n";
  s += "NAME=" + partName + "\n";
  s += "ID=" + partID + "\n";
  s += "COUNT=" + String(countValue) + "\n";
  s += "TIMEOUT=" + String(displayTimeoutSec) + "\n";
  s += "POSTINT=" + String(postIntervalSec) + "\n";
  s += "DISPLAY=" + String(displayEnabled ? "ON" : "OFF") + "\n";
  s += "BLE=" + String(bleStarted ? (bleAdvertisingActive ? "ADV" : "IDLE") : "OFF") + "\n";
  s += "WIFI_SSID=" + wifiSSID + "\n";
  s += "API_URL=" + apiUrl + "\n";
  s += "BATT_V=" + String(batt.batteryV, 3) + "\n";
  s += "BATT_MV=" + String(batt.adcMv) + "\n";
  s += "BATT_RAW=" + String(batt.raw) + "\n";
  s += "WARN_THR=" + String(warnThreshold) + "\n";
  s += "CRIT_THR=" + String(critThreshold);

  return s;
}

// ============================================================
// DISPLAY
// ============================================================

void displayHardwareOn() {
  display.ssd1306_command(SSD1306_DISPLAYON);
}

void displayHardwareOff() {
  display.ssd1306_command(SSD1306_DISPLAYOFF);
}

void showStatus(const String& line1, const String& line2) {
  statusOverlayActive = true;
  displayStatusLine1 = line1;
  displayStatusLine2 = line2;

  if (!displayEnabled) {
    displayEnabled = true;
  }

  displayHardwareOn();
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);

  display.setCursor(0, 0);
  display.println(deviceName);

  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  display.setCursor(0, 18);
  display.println(line1);

  if (line2.length() > 0) {
    display.setCursor(0, 30);
    display.println(line2);
  }

  display.display();
}

void clearStatus() {
  statusOverlayActive = false;
  displayStatusLine1 = "";
  displayStatusLine2 = "";
  renderDisplay();
}

void renderDisplay() {
  if (!displayEnabled) {
    display.clearDisplay();
    display.display();
    return;
  }

  if (statusOverlayActive) {
    showStatus(displayStatusLine1, displayStatusLine2);
    return;
  }

  if (displayMode == DISP_BATTERY) {
    renderBatteryDisplay();
    return;
  }

  // Default: count display
  displayHardwareOn();
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println(partName);

  display.setCursor(0, 10);
  display.println(partID);

  display.drawLine(0, 20, 127, 20, SSD1306_WHITE);

  display.setTextSize(1);
  display.setCursor(0, 24);
  display.println("COUNT");

  // Threshold status badge — drawn in the top-right corner over the header
  // so it is always visible regardless of count magnitude
  const char* threshLabel = nullptr;
  if (critThreshold > 0 && countValue <= critThreshold) {
    threshLabel = "CRIT";
  } else if (warnThreshold > 0 && countValue <= warnThreshold) {
    threshLabel = "LOW";
  }
  if (threshLabel != nullptr) {
    int16_t tx, ty;
    uint16_t tw, th;
    display.setTextSize(1);
    display.getTextBounds(threshLabel, 0, 0, &tx, &ty, &tw, &th);
    int bx = SCREEN_WIDTH - (int)tw - 3;
    // Filled (inverted) rounded badge in top-right
    display.fillRect(bx - 1, 0, (int)tw + 3, 10, SSD1306_WHITE);
    display.setTextColor(SSD1306_BLACK);
    display.setCursor(bx, 1);
    display.print(threshLabel);
    display.setTextColor(SSD1306_WHITE);
  }

  display.setTextSize(4);
  display.setCursor(0, 34);
  display.println(countValue);

  display.display();
}

void renderBatteryDisplay() {
  BatteryReading batt = readBatteryVoltage();

  displayHardwareOn();
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // Header
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println(deviceName);
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  display.setCursor(0, 14);
  display.println("BATTERY");

  // Battery icon outline (left-justified fill bar)
  // Outer rect: x=0, y=26, w=100, h=18
  // Nub: x=100, y=31, w=4, h=8
  const int bx = 0, by = 26, bw = 100, bh = 18;
  display.drawRect(bx, by, bw, bh, SSD1306_WHITE);
  display.fillRect(bx + bw, by + 4, 4, bh - 8, SSD1306_WHITE);

  // Fill bar (left-justified inside outline, 2px inset)
  float pct = 0.0f;
  if (batt.valid && batt.batteryV > 3.0f) {
    pct = (batt.batteryV - 3.0f) / (4.2f - 3.0f);
    if (pct > 1.0f) pct = 1.0f;
  }
  int fillW = (int)((bw - 4) * pct);
  if (fillW > 0) {
    display.fillRect(bx + 2, by + 2, fillW, bh - 4, SSD1306_WHITE);
  }

  // Percentage text (large, centered below battery icon)
  int pctInt = (int)(pct * 100.0f);
  char pctBuf[8];
  snprintf(pctBuf, sizeof(pctBuf), "%d%%", pctInt);

  display.setTextSize(2);
  display.setCursor(0, 48);
  display.print(pctBuf);

  // Voltage text (right side of percentage row)
  char vBuf[12];
  if (batt.valid) {
    snprintf(vBuf, sizeof(vBuf), "%.2fV", batt.batteryV);
  } else {
    snprintf(vBuf, sizeof(vBuf), "--V");
  }
  display.setTextSize(1);
  // Right-align voltage text
  int16_t tx, ty;
  uint16_t tw, th;
  display.getTextBounds(vBuf, 0, 0, &tx, &ty, &tw, &th);
  display.setCursor(SCREEN_WIDTH - tw - 1, 54);
  display.print(vBuf);

  display.display();
}

void turnDisplayOn(bool openBleWindow) {
  displayEnabled = true;
  displayMode = DISP_COUNT;
  markActivity(openBleWindow);
  clearStatus();
  DBG_PRINTLN("Display ON");
}

void turnDisplayOff() {
  displayEnabled = false;
  statusOverlayActive = false;
  display.clearDisplay();
  display.display();
  displayHardwareOff();
  DBG_PRINTLN("Display OFF");
}

void toggleDisplay() {
  if (!displayEnabled) {
    // Off → Count view
    turnDisplayOn(true);
  } else if (displayMode == DISP_COUNT) {
    // Count view → Battery view
    displayMode = DISP_BATTERY;
    markActivity(false);
    DBG_PRINTLN("Display mode: BATTERY");
    renderDisplay();
  } else {
    // Battery view → Off
    turnDisplayOff();
  }
}

// ============================================================
// BLE
// ============================================================

void sendBleLine(const String& msg) {
  DBG_PRINT("[BLE TX] ");
  DBG_PRINTLN(msg);

  if (txCharacteristic && bleClientConnected) {
    txCharacteristic->setValue(msg.c_str());
    txCharacteristic->notify();
  }
}

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo) override {
    bleClientConnected = true;
    bleConnectedEvent = true;
  }

  void onDisconnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo, int reason) override {
    bleClientConnected = false;
    bleDisconnectedEvent = true;
  }
};

class RxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* pCharacteristic, NimBLEConnInfo& connInfo) override {
    std::string value = pCharacteristic->getValue();
    if (value.empty()) return;

    if (bleCmdCount >= BLE_CMD_QUEUE_LEN) {
      return;
    }

    size_t n = value.size();
    if (n >= BLE_CMD_MAX_LEN) n = BLE_CMD_MAX_LEN - 1;

    memcpy(bleCmdQueue[bleCmdHead], value.data(), n);
    bleCmdQueue[bleCmdHead][n] = '\0';

    bleCmdHead = (bleCmdHead + 1) % BLE_CMD_QUEUE_LEN;
    bleCmdCount++;
  }
};

void startAdvertising() {
  if (!bleStarted) return;

  NimBLEAdvertising* pAdvertising = NimBLEDevice::getAdvertising();
  if (!pAdvertising) return;

  NimBLEAdvertisementData advData;
  advData.setName(deviceName.c_str());
  advData.addServiceUUID(SERVICE_UUID);

  NimBLEAdvertisementData scanResp;
  scanResp.setName(deviceName.c_str());

  pAdvertising->stop();
  delay(10);
  pAdvertising->setAdvertisementData(advData);
  pAdvertising->setScanResponseData(scanResp);
  pAdvertising->setMinInterval(800);
  pAdvertising->setMaxInterval(1600);
  pAdvertising->start();

  bleAdvertisingActive = true;

  DBG_PRINT("Advertising as: ");
  DBG_PRINTLN(deviceName);
}

void stopAdvertising() {
  if (!bleStarted) return;

  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  if (adv) adv->stop();

  bleAdvertisingActive = false;
  DBG_PRINTLN("BLE advertising stopped");
}

void startBleIfNeeded() {
  if (bleStarted) {
    if (!bleAdvertisingActive) startAdvertising();
    return;
  }

  NimBLEDevice::init(deviceName.c_str());
  NimBLEDevice::setPower(ESP_PWR_LVL_N0);

  bleServer = NimBLEDevice::createServer();
  bleServer->setCallbacks(new ServerCallbacks());

  NimBLEService* pService = bleServer->createService(SERVICE_UUID);

  txCharacteristic = pService->createCharacteristic(
    TX_UUID,
    NIMBLE_PROPERTY::NOTIFY
  );

  rxCharacteristic = pService->createCharacteristic(
    RX_UUID,
    NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR
  );

  rxCharacteristic->setCallbacks(new RxCallbacks());

  pService->start();

  bleStarted = true;
  bleClientConnected = false;
  bleAdvertisingActive = false;

  startAdvertising();
  DBG_PRINTLN("BLE started");
}

void enableBleWindow(uint32_t windowMs) {
  bleActiveUntilMs = millis() + windowMs;
  startBleIfNeeded();
}

void processPendingBleEvents() {
  if (bleConnectedEvent) {
    bleConnectedEvent = false;
    markActivity(false);
    DBG_PRINTLN("BLE client connected");
  }

  if (bleDisconnectedEvent) {
    bleDisconnectedEvent = false;
    DBG_PRINTLN("BLE client disconnected");

    if (bleStarted && (millis() < bleActiveUntilMs)) {
      startAdvertising();
    }
  }
}

void processPendingBleCommands() {
  while (bleCmdCount > 0) {
    char localBuf[BLE_CMD_MAX_LEN];

    memcpy(localBuf, bleCmdQueue[bleCmdTail], BLE_CMD_MAX_LEN);
    bleCmdTail = (bleCmdTail + 1) % BLE_CMD_QUEUE_LEN;
    bleCmdCount--;

    String cmd = String(localBuf);
    cmd.trim();

    if (cmd.length() == 0) continue;

    markActivity(true);

    DBG_PRINT("[BLE RX deferred] ");
    DBG_PRINTLN(cmd);

    processCommand(cmd);
  }
}

// ============================================================
// WIFI / HTTP
// ============================================================

void wifiOff() {
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
  delay(50);
  DBG_PRINTLN("WiFi OFF");
}

bool connectWiFi(uint32_t timeoutMs) {
  if (wifiSSID.length() == 0) {
    DBG_PRINTLN("WiFi SSID not set");
    return false;
  }

  if (WiFi.status() == WL_CONNECTED) {
    DBG_PRINT("WiFi already connected. IP = ");
    DBG_PRINTLN(WiFi.localIP());
    return true;
  }

  showStatus("WiFi...", wifiSSID);

  WiFi.persistent(false);
  WiFi.setAutoReconnect(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);
  WiFi.begin(wifiSSID.c_str(), wifiPass.c_str());

  DBG_PRINT("Connecting to WiFi");
  unsigned long startMs = millis();

  while (WiFi.status() != WL_CONNECTED && (millis() - startMs) < timeoutMs) {
    delay(250);
    DBG_PRINT(".");
  }
  DBG_PRINTLN("");

  if (WiFi.status() == WL_CONNECTED) {
    lastRssi = WiFi.RSSI();
    DBG_PRINT("WiFi connected. IP = ");
    DBG_PRINTLN(WiFi.localIP());
    DBG_PRINT("RSSI = ");
    DBG_PRINTLN(lastRssi);
    showStatus("WiFi OK", WiFi.localIP().toString());
    return true;
  }

  DBG_PRINTLN("WiFi connect failed");
  showStatus("WiFi failed", wifiSSID);
  wifiOff();
  return false;
}

bool postCounterUpdate() {
  if (apiUrl.length() == 0) {
    DBG_PRINTLN("API URL not set");
    sendBleLine("ERR API_URL NOT SET");
    return false;
  }

  flushSaveNow();

  BatteryReading batt = readBatteryVoltage();
  lastTempC = readInternalTempC();

  if (!connectWiFi()) {
    sendBleLine("ERR WIFI CONNECT");
    return false;
  }

  showStatus("POSTing...", "");

  HTTPClient http;
  WiFiClientSecure secureClient;
  http.setTimeout(10000);

  if (!httpBeginForUrl(http, apiUrl, secureClient)) {
    DBG_PRINTLN("HTTP begin failed for POST");
    sendBleLine("ERR HTTP BEGIN");
    return false;
  }

  http.addHeader("Content-Type", "application/json");

  String payload;
  payload.reserve(512);
  payload += "{";
  payload += "\"device_name\":\"" + jsonEscape(deviceName) + "\",";
  payload += "\"part_name\":\"" + jsonEscape(partName) + "\",";
  payload += "\"part_id\":\"" + jsonEscape(partID) + "\",";
  payload += "\"count\":" + String(countValue) + ",";
  payload += "\"battery_voltage\":" + String(batt.batteryV, 3) + ",";
  payload += "\"battery_adc_mv\":" + String(batt.adcMv) + ",";
  payload += "\"battery_adc_raw\":" + String(batt.raw) + ",";
  payload += "\"rssi\":" + String(lastRssi) + ",";
  if (lastTempC > -900.0f) {
    payload += "\"temperature_c\":" + String(lastTempC, 2) + ",";
  }
  payload += "\"ip\":\"" + WiFi.localIP().toString() + "\"";
  payload += "}";

  DBG_PRINTLN("POST payload:");
  DBG_PRINTLN(payload);

  int code = http.POST(payload);
  String response = http.getString();

  DBG_PRINT("POST HTTP code: ");
  DBG_PRINTLN(code);
  DBG_PRINT("POST response: ");
  DBG_PRINTLN(response);

  http.end();

  if (code > 0 && code < 300) {
    showStatus("POST OK", String(code));
    sendBleLine("OK POST " + String(code));
    return true;
  }

  showStatus("POST FAIL", String(code));
  sendBleLine("ERR POST " + String(code));
  return false;
}

bool ackDeviceCommand(const String& commandId, const String& status, const String& resultMessage) {
  String ackUrl = buildAckUrl();
  if (ackUrl.length() == 0) return false;

  if (!connectWiFi()) return false;

  HTTPClient http;
  WiFiClientSecure secureClient;
  http.setTimeout(10000);

  if (!httpBeginForUrl(http, ackUrl, secureClient)) {
    DBG_PRINTLN("HTTP begin failed for ACK");
    return false;
  }

  http.addHeader("Content-Type", "application/json");

  String payload;
  payload.reserve(256);
  payload += "{";
  payload += "\"command_id\":\"" + jsonEscape(commandId) + "\",";
  payload += "\"status\":\"" + jsonEscape(status) + "\",";
  payload += "\"result_message\":\"" + jsonEscape(resultMessage) + "\"";
  payload += "}";

  int code = http.POST(payload);
  String response = http.getString();

  DBG_PRINT("ACK HTTP code: ");
  DBG_PRINTLN(code);
  DBG_PRINT("ACK response: ");
  DBG_PRINTLN(response);

  http.end();
  return (code > 0 && code < 300);
}

bool fetchAndApplyPendingCommands() {
  String cmdUrl = buildCmdUrl();
  if (cmdUrl.length() == 0) {
    DBG_PRINTLN("Command URL not available");
    return false;
  }

  flushSaveNow();

  if (!connectWiFi()) {
    DBG_PRINTLN("Cannot fetch commands: WiFi connect failed");
    return false;
  }

  showStatus("Fetching...", "");

  HTTPClient http;
  WiFiClientSecure secureClient;
  http.setTimeout(10000);

  if (!httpBeginForUrl(http, cmdUrl, secureClient)) {
    DBG_PRINTLN("HTTP begin failed for GET");
    return false;
  }

  DBG_PRINT("GET commands URL: ");
  DBG_PRINTLN(cmdUrl);

  int code = http.GET();

  if (code <= 0) {
    DBG_PRINT("GET commands failed, code=");
    DBG_PRINTLN(code);
    http.end();
    showStatus("Fetch FAIL", String(code));
    return false;
  }

  String body = http.getString();
  http.end();

  DBG_PRINT("GET commands HTTP code: ");
  DBG_PRINTLN(code);
  DBG_PRINT("GET commands body: ");
  DBG_PRINTLN(body);

  DynamicJsonDocument doc(4096);
  DeserializationError err = deserializeJson(doc, body);
  if (err) {
    DBG_PRINT("Command JSON parse failed: ");
    DBG_PRINTLN(err.c_str());
    showStatus("JSON FAIL", err.c_str());
    return false;
  }

  JsonArray commands = doc["commands"].as<JsonArray>();

  if (commands.isNull()) {
    DBG_PRINTLN("No commands array in response");
    showStatus("No commands", "");
    return false;
  }

  DBG_PRINT("Pending commands: ");
  DBG_PRINTLN(commands.size());

  showStatus("Commands", String(commands.size()));

  for (JsonObject cmd : commands) {
    String commandId   = cmd["command_id"].as<String>();
    String commandType = cmd["command_type"].as<String>();
    JsonObject payload = cmd["payload"].as<JsonObject>();

    String resultMessage;
    bool ok = applyServerCommand(commandType, payload, resultMessage);

    DBG_PRINT("Applied command ");
    DBG_PRINT(commandType);
    DBG_PRINT(" => ");
    DBG_PRINTLN(ok ? "OK" : "FAIL");

    ackDeviceCommand(commandId, ok ? "acked" : "failed", resultMessage);
  }

  return true;
}

// ============================================================
// COUNTER ACTIONS
// ============================================================

void incrementCount() {
  countValue++;
  scheduleSave();
  turnDisplayOn(false);
  sendBleLine("OK COUNT=" + String(countValue));
  DBG_PRINT("Incremented count: ");
  DBG_PRINTLN(countValue);
}

void decrementCount() {
  if (countValue > 0) countValue--;
  scheduleSave();
  turnDisplayOn(false);
  sendBleLine("OK COUNT=" + String(countValue));
  DBG_PRINT("Decremented count: ");
  DBG_PRINTLN(countValue);
}

void resetCount() {
  countValue = 0;
  scheduleSave();
  turnDisplayOn(true);
  sendBleLine("OK RESET COUNT=0");
  DBG_PRINTLN("Count reset to zero");
}

// ============================================================
// COMMAND PROCESSING
// ============================================================

void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  markActivity(true);

  String upper = cmd;
  upper.toUpperCase();

  if (upper == "HELP") {
    sendBleLine(
      "CMDS: HELP, GET, DEVNAME=..., NAME=..., ID=..., COUNT=..., TIMEOUT=..., "
      "POSTINT=..., WIFI_SSID=..., WIFI_PASS=..., API_URL=..., WIFI_TEST, POST_NOW, "
      "FETCH_NOW, RESET, DISPLAY=ON, DISPLAY=OFF, BLE=ON, BLE=OFF, SLEEP_NOW, BATT_NOW"
    );
    return;
  }

  if (upper == "GET") {
    sendBleLine(getStateText());
    return;
  }

  if (upper == "BATT_NOW") {
    BatteryReading batt = readBatteryVoltage();
    sendBleLine("OK BATT_V=" + String(batt.batteryV, 3) +
                " ADC_MV=" + String(batt.adcMv) +
                " RAW=" + String(batt.raw));
    return;
  }

  if (upper == "RESET") {
    resetCount();
    return;
  }

  if (upper == "DISPLAY=ON") {
    turnDisplayOn(true);
    sendBleLine("OK DISPLAY=ON");
    return;
  }

  if (upper == "DISPLAY=OFF") {
    turnDisplayOff();
    sendBleLine("OK DISPLAY=OFF");
    return;
  }

  if (upper == "BLE=ON") {
    enableBleWindow(BLE_ACTIVE_WINDOW_MS);
    sendBleLine("OK BLE=ON");
    return;
  }

  if (upper == "BLE=OFF") {
    if (bleClientConnected) {
      sendBleLine("ERR BLE CONNECTED");
    } else {
      stopAdvertising();
      sendBleLine("OK BLE=OFF");
    }
    return;
  }

  if (upper == "WIFI_TEST") {
    bool ok = connectWiFi();
    sendBleLine(ok ? ("OK WIFI IP=" + WiFi.localIP().toString()) : "ERR WIFI TEST");
    wifiOff();
    clearStatus();
    return;
  }

  if (upper == "POST_NOW") {
    bool ok = postCounterUpdate();
    wifiOff();
    sendBleLine(ok ? "OK POST_NOW" : "ERR POST_NOW");
    clearStatus();
    return;
  }

  if (upper == "FETCH_NOW") {
    bool ok = fetchAndApplyPendingCommands();
    wifiOff();
    sendBleLine(ok ? "OK FETCH_NOW" : "ERR FETCH_NOW");
    clearStatus();
    return;
  }

  if (upper == "SLEEP_NOW") {
    sendBleLine("OK SLEEPING");
    showStatus("Sleeping...", "");
    delay(50);
    enterDeepSleepNow();
    return;
  }

  if (upper.startsWith("DEVNAME=")) {
    String v = trimCopy(cmd.substring(8));
    if (v.length() == 0) { sendBleLine("ERR DEVNAME EMPTY"); return; }
    if (v.length() > 24) v = v.substring(0, 24);

    deviceName = v;
    scheduleSave();
    if (bleStarted && bleAdvertisingActive) {
      startAdvertising();
    }
    sendBleLine("OK DEVNAME=" + deviceName);
    return;
  }

  if (upper.startsWith("NAME=")) {
    String v = trimCopy(cmd.substring(5));
    if (v.length() == 0) { sendBleLine("ERR NAME EMPTY"); return; }
    if (v.length() > 20) v = v.substring(0, 20);

    partName = v;
    scheduleSave();
    turnDisplayOn(true);
    sendBleLine("OK NAME=" + partName);
    return;
  }

  if (upper.startsWith("ID=")) {
    String v = trimCopy(cmd.substring(3));
    if (v.length() == 0) { sendBleLine("ERR ID EMPTY"); return; }
    if (v.length() > 20) v = v.substring(0, 20);

    partID = v;
    scheduleSave();
    turnDisplayOn(true);
    sendBleLine("OK ID=" + partID);
    return;
  }

  if (upper.startsWith("COUNT=")) {
    String v = trimCopy(cmd.substring(6));
    countValue = v.toInt();
    if (countValue < 0) countValue = 0;
    scheduleSave();
    turnDisplayOn(true);
    sendBleLine("OK COUNT=" + String(countValue));
    return;
  }

  if (upper.startsWith("TIMEOUT=")) {
    String v = trimCopy(cmd.substring(8));
    int t = v.toInt();
    if (t < 3 || t > 600) {
      sendBleLine("ERR TIMEOUT RANGE 3..600");
      return;
    }

    displayTimeoutSec = (uint32_t)t;
    scheduleSave();
    turnDisplayOn(true);
    sendBleLine("OK TIMEOUT=" + String(displayTimeoutSec));
    return;
  }

  if (upper.startsWith("POSTINT=")) {
    String v = trimCopy(cmd.substring(8));
    int t = v.toInt();
    if (t < (int)MIN_POST_INTERVAL_SEC || t > (int)MAX_POST_INTERVAL_SEC) {
      sendBleLine("ERR POSTINT RANGE 5..86400");
      return;
    }

    postIntervalSec = (uint32_t)t;
    scheduleSave();
    sendBleLine("OK POSTINT=" + String(postIntervalSec));
    return;
  }

  if (upper.startsWith("WIFI_SSID=")) {
    wifiSSID = trimCopy(cmd.substring(10));
    scheduleSave();
    sendBleLine("OK WIFI_SSID=" + wifiSSID);
    return;
  }

  if (upper.startsWith("WIFI_PASS=")) {
    wifiPass = trimCopy(cmd.substring(10));
    scheduleSave();
    sendBleLine("OK WIFI_PASS SET");
    return;
  }

  if (upper.startsWith("API_URL=")) {
    apiUrl = trimCopy(cmd.substring(8));
    scheduleSave();
    sendBleLine("OK API_URL=" + apiUrl);
    return;
  }

  sendBleLine("ERR UNKNOWN CMD");
}

// ============================================================
// SERVER COMMANDS
// ============================================================

bool applyServerCommand(const String& commandType, JsonObject payload, String& resultMessage) {
  if (commandType == "set_devname") {
    if (!payload["device_name"].is<const char*>()) {
      resultMessage = "missing device_name";
      return false;
    }
    deviceName = String(payload["device_name"].as<const char*>());
    scheduleSave();
    if (bleStarted && bleAdvertisingActive) startAdvertising();
    resultMessage = "device name updated";
    return true;
  }

  if (commandType == "set_name") {
    if (!payload["part_name"].is<const char*>()) {
      resultMessage = "missing part_name";
      return false;
    }
    partName = String(payload["part_name"].as<const char*>());
    scheduleSave();
    turnDisplayOn(true);
    resultMessage = "part name updated";
    return true;
  }

  if (commandType == "set_id") {
    if (!payload["part_id"].is<const char*>()) {
      resultMessage = "missing part_id";
      return false;
    }
    partID = String(payload["part_id"].as<const char*>());
    scheduleSave();
    turnDisplayOn(true);
    resultMessage = "part id updated";
    return true;
  }

  if (commandType == "set_count") {
    if (!payload["count"].is<int>() && !payload["count"].is<long>() && !payload["count"].is<float>()) {
      resultMessage = "missing count";
      return false;
    }
    countValue = payload["count"].as<int>();
    if (countValue < 0) countValue = 0;
    scheduleSave();
    turnDisplayOn(true);
    resultMessage = "count updated";
    return true;
  }

  if (commandType == "set_timeout") {
    if (!payload["display_timeout_sec"].is<int>()) {
      resultMessage = "missing display_timeout_sec";
      return false;
    }
    int t = payload["display_timeout_sec"].as<int>();
    if (t < 3 || t > 600) {
      resultMessage = "timeout out of range";
      return false;
    }
    displayTimeoutSec = (uint32_t)t;
    scheduleSave();
    resultMessage = "timeout updated";
    return true;
  }

  if (commandType == "set_post_interval") {
    if (!payload["post_interval_sec"].is<int>()) {
      resultMessage = "missing post_interval_sec";
      return false;
    }
    int t = payload["post_interval_sec"].as<int>();
    if (t < (int)MIN_POST_INTERVAL_SEC || t > (int)MAX_POST_INTERVAL_SEC) {
      resultMessage = "post interval out of range";
      return false;
    }
    postIntervalSec = (uint32_t)t;
    scheduleSave();
    resultMessage = "post interval updated";
    return true;
  }

  if (commandType == "set_wifi") {
    if (!payload["wifi_ssid"].is<const char*>()) {
      resultMessage = "missing wifi_ssid";
      return false;
    }

    wifiSSID = String(payload["wifi_ssid"].as<const char*>());

    if (payload["wifi_pass"].is<const char*>()) {
      wifiPass = String(payload["wifi_pass"].as<const char*>());
    }

    scheduleSave();
    resultMessage = "wifi updated";
    return true;
  }

  if (commandType == "set_api_url") {
    if (!payload["api_url"].is<const char*>()) {
      resultMessage = "missing api_url";
      return false;
    }
    apiUrl = String(payload["api_url"].as<const char*>());
    scheduleSave();
    resultMessage = "api url updated";
    return true;
  }

  if (commandType == "display_on") {
    turnDisplayOn(true);
    resultMessage = "display on";
    return true;
  }

  if (commandType == "display_off") {
    turnDisplayOff();
    resultMessage = "display off";
    return true;
  }

  if (commandType == "reset_count") {
    countValue = 0;
    scheduleSave();
    turnDisplayOn(true);
    resultMessage = "count reset";
    return true;
  }

  if (commandType == "set_thresholds") {
    // Both fields are optional; omit or set to 0 to disable that threshold
    if (payload["warn_threshold"].is<int>() || payload["warn_threshold"].is<long>()) {
      int w = payload["warn_threshold"].as<int>();
      warnThreshold = (w > 0) ? w : 0;
    }
    if (payload["critical_threshold"].is<int>() || payload["critical_threshold"].is<long>()) {
      int c = payload["critical_threshold"].as<int>();
      critThreshold = (c > 0) ? c : 0;
    }
    // Ensure critical <= warn when both are set
    if (critThreshold > 0 && warnThreshold > 0 && critThreshold > warnThreshold) {
      critThreshold = warnThreshold;
    }
    scheduleSave();
    renderDisplay();  // refresh badge immediately
    DBG_PRINTF("Thresholds set: warn=%d crit=%d\n", warnThreshold, critThreshold);
    resultMessage = "thresholds updated";
    return true;
  }

  resultMessage = "unknown command";
  return false;
}

// ============================================================
// BUTTON PROCESSING
// ============================================================

void handleButtonsPolled() {
  unsigned long now = millis();

  bool upNow   = digitalRead(BTN_UP);
  bool downNow = digitalRead(BTN_DOWN);
  bool showNow = digitalRead(BTN_SHOW);

  if (prevUpState == HIGH && upNow == LOW && (now - lastUpAcceptedMs > DEBOUNCE_MS)) {
    lastUpAcceptedMs = now;
    markActivity(false);
    incrementCount();
  }

  if (prevDownState == HIGH && downNow == LOW && (now - lastDownAcceptedMs > DEBOUNCE_MS)) {
    lastDownAcceptedMs = now;
    markActivity(false);
    decrementCount();
  }

  if (prevShowState == HIGH && showNow == LOW && (now - lastShowAcceptedMs > DEBOUNCE_MS)) {
    lastShowAcceptedMs = now;
    markActivity(true);
    showPressed = true;
    showLongPressHandled = false;
    showPressStartMs = now;
    DBG_PRINTLN("SHOW pressed");
  }

  if (showPressed) {
    if (showNow == LOW) {
      if (!showLongPressHandled && (now - showPressStartMs >= SHOW_LONG_PRESS_MS)) {
        showLongPressHandled = true;
        showTapPending = false;
        resetCount();
        DBG_PRINTLN("SHOW long press -> reset");
      }
    } else {
      if (!showLongPressHandled) {
        if (showTapPending && (now - showTapReleasedMs <= SHOW_DOUBLE_PRESS_MS)) {
          showTapPending = false;
          manualNetworkWorkRequested = true;
          showStatus("Manual sync", "Fetch + POST");
          DBG_PRINTLN("SHOW double press -> request manual fetch/post");
        } else {
          showTapPending = true;
          showTapReleasedMs = now;
          DBG_PRINTLN("SHOW short press -> waiting for second press");
        }
      }
      showPressed = false;
    }
  }

  prevUpState   = upNow;
  prevDownState = downNow;
  prevShowState = showNow;
}

// ============================================================
// PERIODIC WORK
// ============================================================

void runPeriodicNetworkWork() {
  DBG_PRINTLN("========================================");
  DBG_PRINTLN("Running periodic network work");
  DBG_PRINT("Wake mode: ");
  DBG_PRINTLN(wakeModeToString(wakeMode));
  DBG_PRINT("Configured post interval (sec): ");
  DBG_PRINTLN(postIntervalSec);
  DBG_PRINTLN("========================================");

  flushSaveNow();

  bool fetchOk = fetchAndApplyPendingCommands();
  DBG_PRINT("fetchAndApplyPendingCommands() => ");
  DBG_PRINTLN(fetchOk ? "OK" : "FAIL");

  bool postOk = postCounterUpdate();
  DBG_PRINT("postCounterUpdate() => ");
  DBG_PRINTLN(postOk ? "OK" : "FAIL");

  wifiOff();
  clearStatus();

  DBG_PRINTLN("Periodic network work complete");
}

// ============================================================
// DEEP SLEEP
// ============================================================

WakeMode decodeWakeMode() {
  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();

  switch (cause) {
    case ESP_SLEEP_WAKEUP_TIMER:
      return WAKE_FROM_TIMER;

    case ESP_SLEEP_WAKEUP_EXT0:
    case ESP_SLEEP_WAKEUP_EXT1:
      return WAKE_FROM_BUTTON;

    default:
      return WAKE_COLD_BOOT;
  }
}

void configureDeepSleepWakeSources() {
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);

  esp_sleep_enable_timer_wakeup((uint64_t)postIntervalSec * 1000000ULL);
  esp_sleep_enable_ext0_wakeup(WAKE_SHOW_GPIO, 0);

  rtc_gpio_pullup_en(WAKE_SHOW_GPIO);
  rtc_gpio_pulldown_dis(WAKE_SHOW_GPIO);

  DBG_PRINT("Configured timer wake for ");
  DBG_PRINT(postIntervalSec);
  DBG_PRINTLN(" seconds");
  DBG_PRINTLN("Configured EXT0 wake on SHOW button LOW");
}

bool readyForDeepSleep() {
  unsigned long now = millis();

  if (bleClientConnected) return false;
  if (showPressed) return false;
  if (showTapPending) return false;
  if (manualNetworkWorkRequested) return false;
  if (settingsDirty && (now - settingsDirtyAtMs < SAVE_DEFER_MS)) return false;
  if (displayEnabled) return false;
  if (now < awakeUntilMs) return false;

  return true;
}

void enterDeepSleepNow() {
  flushSaveNow();

  stopAdvertising();
  wifiOff();

  showStatus("Sleeping...", "");
  delay(50);

  // Ensure battery sense is off, then latch the pin LOW through deep sleep.
  // Without gpio_hold_en() the output driver is powered off during sleep and
  // the pin floats, which can partially bias the N-MOSFET on and allow the
  // full divider current (~350 µA) to flow continuously.
  setBatterySenseEnabled(false);
  gpio_hold_en((gpio_num_t)BATTERY_SENSE_EN_PIN);

  turnDisplayOff();
  configureDeepSleepWakeSources();

  DBG_PRINTLN("Entering deep sleep now");
  DBG_PRINT("Next timer wake in ");
  DBG_PRINT(postIntervalSec);
  DBG_PRINTLN(" seconds");

  Serial.flush();
  delay(50);

  esp_deep_sleep_start();
}

void handleWakeBootBehavior() {
  wakeMode = decodeWakeMode();
  periodicWorkDone = false;

  DBG_PRINT("Decoded wake mode: ");
  DBG_PRINTLN(wakeModeToString(wakeMode));

  switch (wakeMode) {
    case WAKE_FROM_BUTTON:
      DBG_PRINTLN("Wake cause: BUTTON");
      turnDisplayOn(true);
      awakeUntilMs = millis() + LOCAL_AWAKE_WINDOW_MS;
      break;

    case WAKE_FROM_TIMER:
      DBG_PRINTLN("Wake cause: TIMER");
      displayEnabled = false;
      turnDisplayOff();
      awakeUntilMs = millis() + TIMER_AWAKE_WINDOW_MS;
      break;

    case WAKE_COLD_BOOT:
    default:
      DBG_PRINTLN("Wake cause: COLD BOOT / RESET");
      turnDisplayOn(false);
      awakeUntilMs = millis() + BOOT_AWAKE_WINDOW_MS;
      break;
  }

  lastUserActivityMs = millis();
}

// ============================================================
// SETUP
// ============================================================

void setup() {
  DBG_BEGIN(115200);
  delay(300);

  bootCount++;

  DBG_PRINTLN();
  DBG_PRINTLN("========================================");
  DBG_PRINTLN("Smart Counter Boot");
  DBG_PRINT("bootCount = ");
  DBG_PRINTLN(bootCount);
  DBG_PRINTLN("========================================");

  pinMode(BTN_UP, INPUT_PULLUP);
  pinMode(BTN_DOWN, INPUT_PULLUP);
  pinMode(BTN_SHOW, INPUT_PULLUP);

  // Release any hold latched from the previous sleep cycle before
  // reconfiguring the pin — otherwise digitalWrite() has no effect.
  gpio_hold_dis((gpio_num_t)BATTERY_SENSE_EN_PIN);
  pinMode(BATTERY_SENSE_EN_PIN, OUTPUT);
  setBatterySenseEnabled(false);

  prevUpState   = digitalRead(BTN_UP);
  prevDownState = digitalRead(BTN_DOWN);
  prevShowState = digitalRead(BTN_SHOW);

  setCpuFrequencyMhz(80);

  WiFi.mode(WIFI_OFF);

  analogReadResolution(12);
  analogSetPinAttenuation(BATTERY_ADC_PIN, ADC_11db);

  Wire.begin();
  delay(30);

  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    DBG_PRINTLN("SSD1306 init failed");
    while (true) {
      delay(1000);
    }
  }

  DBG_PRINTLN("OLED initialized");

  initTempSensor();

  loadData();
  applyDefaultNetworkConfig();
  handleWakeBootBehavior();

  DBG_PRINT("Device name: ");
  DBG_PRINTLN(deviceName);
  DBG_PRINT("Part name: ");
  DBG_PRINTLN(partName);
  DBG_PRINT("Part ID: ");
  DBG_PRINTLN(partID);
  DBG_PRINT("POST interval sec: ");
  DBG_PRINTLN(postIntervalSec);

  DBG_PRINTLN("Setup complete");
}

// ============================================================
// LOOP
// ============================================================

void loop() {
  unsigned long now = millis();

  handleButtonsPolled();
  processPendingBleEvents();
  processPendingBleCommands();
  flushSaveIfDue();

  if (showTapPending && (now - showTapReleasedMs > SHOW_DOUBLE_PRESS_MS)) {
    showTapPending = false;
    toggleDisplay();
    DBG_PRINTLN("SHOW single short press -> toggle display");
  }

  if (manualNetworkWorkRequested) {
    manualNetworkWorkRequested = false;
    awakeUntilMs = millis() + TIMER_AWAKE_WINDOW_MS;
    DBG_PRINTLN("Running manual fetch/post from SHOW double press");
    runPeriodicNetworkWork();
    now = millis();
  }

  if (bleStarted && !bleClientConnected && bleAdvertisingActive && now >= bleActiveUntilMs) {
    stopAdvertising();
  }

  if (displayEnabled && !statusOverlayActive &&
      (now - lastUserActivityMs >= (displayTimeoutSec * 1000UL))) {
    turnDisplayOff();
  }

  if (wakeMode == WAKE_FROM_TIMER && !periodicWorkDone) {
    periodicWorkDone = true;
    runPeriodicNetworkWork();
    awakeUntilMs = millis() + 800;
  }

  if (wakeMode == WAKE_COLD_BOOT && !periodicWorkDone) {
    periodicWorkDone = true;
    DBG_PRINTLN("Cold boot -> performing one verification POST");
    runPeriodicNetworkWork();
    awakeUntilMs = millis() + 1200;
  }

  if (readyForDeepSleep()) {
    enterDeepSleepNow();
    return;
  }

  delay(5);
}