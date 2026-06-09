/*
 * Persistent application state (the Arduino globals: deviceName, partName,
 * partID, countValue, thresholds, network config, etc.) consolidated into
 * one struct guarded by a mutex.
 *
 * Mirrors what Preferences (NVS) stored on the Arduino side; the actual
 * load/save path lives in settings_store.c using Zephyr's `settings`
 * subsystem.
 */

#ifndef SMARTCOUNTER_COUNTER_STATE_H_
#define SMARTCOUNTER_COUNTER_STATE_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <zephyr/kernel.h>

#define SC_DEVICE_NAME_MAX 24
#define SC_PART_NAME_MAX   20
#define SC_PART_ID_MAX     20
#define SC_WIFI_SSID_MAX   32
#define SC_WIFI_PASS_MAX   64
#define SC_API_URL_MAX     128

struct sc_state {
	char     device_name[SC_DEVICE_NAME_MAX + 1];
	char     part_name[SC_PART_NAME_MAX + 1];
	char     part_id[SC_PART_ID_MAX + 1];
	int32_t  count_value;

	int32_t  warn_threshold;
	int32_t  crit_threshold;

	uint32_t display_timeout_sec;
	uint32_t post_interval_sec;

	char     wifi_ssid[SC_WIFI_SSID_MAX + 1];
	char     wifi_pass[SC_WIFI_PASS_MAX + 1];
	char     api_url[SC_API_URL_MAX + 1];

	int8_t   last_rssi;
	float    last_temp_c;
};

extern struct k_mutex sc_state_lock;

struct sc_state *sc_state_get(void);

void sc_state_init_defaults(struct sc_state *s);

#endif /* SMARTCOUNTER_COUNTER_STATE_H_ */
