#include "counter_state.h"

#include <string.h>
#include <stdio.h>

#include <zephyr/bluetooth/addr.h>
#include <zephyr/bluetooth/bluetooth.h>

K_MUTEX_DEFINE(sc_state_lock);

static struct sc_state s_state;

struct sc_state *sc_state_get(void)
{
	return &s_state;
}

void sc_state_init_defaults(struct sc_state *s)
{
	memset(s, 0, sizeof(*s));

	bt_addr_le_t addr;
	size_t count = 1;
	bt_id_get(&addr, &count);

	snprintf(s->device_name, sizeof(s->device_name),
		 "Counter-%02X%02X%02X",
		 addr.a.val[2], addr.a.val[1], addr.a.val[0]);

	strncpy(s->part_name, "PART", sizeof(s->part_name) - 1);
	strncpy(s->part_id,   "0001", sizeof(s->part_id)   - 1);

	s->count_value         = 0;
	s->warn_threshold      = 0;
	s->crit_threshold      = 0;
	s->display_timeout_sec = CONFIG_SMARTCOUNTER_DEFAULT_DISPLAY_TIMEOUT_SEC;
	s->post_interval_sec   = CONFIG_SMARTCOUNTER_DEFAULT_POST_INTERVAL_SEC;

	strncpy(s->wifi_ssid, CONFIG_SMARTCOUNTER_DEFAULT_WIFI_SSID, sizeof(s->wifi_ssid) - 1);
	strncpy(s->wifi_pass, CONFIG_SMARTCOUNTER_DEFAULT_WIFI_PASS, sizeof(s->wifi_pass) - 1);
	strncpy(s->api_url,   CONFIG_SMARTCOUNTER_DEFAULT_API_URL,   sizeof(s->api_url)   - 1);

	s->last_rssi  = 0;
	s->last_temp_c = -999.0f;
}
