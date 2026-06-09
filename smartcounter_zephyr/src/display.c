#include "display.h"
#include "counter_state.h"
#include "battery.h"

#include <stdio.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/display.h>
#include <zephyr/display/cfb.h>
#include <zephyr/pm/device.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(sc_display, LOG_LEVEL_INF);

static const struct device *const oled = DEVICE_DT_GET(DT_CHOSEN(zephyr_display));

static enum sc_display_mode mode = SC_DISP_COUNT;
static bool enabled;
static bool status_active;
static char status_l1[32];
static char status_l2[32];

extern void sc_app_mark_activity(bool open_ble_window);

static int display_power(bool on)
{
	enum pm_device_action action = on ? PM_DEVICE_ACTION_RESUME : PM_DEVICE_ACTION_SUSPEND;
	int rc = pm_device_action_run(oled, action);
	if (rc && rc != -EALREADY) {
		LOG_WRN("display pm action %d failed: %d", action, rc);
	}
	(void)display_blanking_off(oled);
	return rc;
}

static void draw_header(const char *line)
{
	cfb_print(oled, line, 0, 0);
}

static void draw_count_screen(void)
{
	char id_line[40];
	char count_line[16];
	const char *badge = NULL;

	struct sc_state *s = sc_state_get();

	cfb_framebuffer_clear(oled, false);

	cfb_framebuffer_set_font(oled, 0);
	draw_header(s->part_name);
	snprintf(id_line, sizeof(id_line), "%s", s->part_id);
	cfb_print(oled, id_line, 0, 10);

	if (s->crit_threshold > 0 && s->count_value <= s->crit_threshold) {
		badge = "CRIT";
	} else if (s->warn_threshold > 0 && s->count_value <= s->warn_threshold) {
		badge = "LOW";
	}
	if (badge) {
		cfb_print(oled, badge, 100, 0);
	}

	cfb_framebuffer_set_font(oled, 1); /* larger font */
	snprintf(count_line, sizeof(count_line), "%d", s->count_value);
	cfb_print(oled, count_line, 0, 34);

	cfb_framebuffer_finalize(oled);
}

static void draw_battery_screen(void)
{
	struct sc_battery_reading b;
	char l1[24];
	char l2[24];

	(void)sc_battery_read(&b);

	cfb_framebuffer_clear(oled, false);

	cfb_framebuffer_set_font(oled, 0);
	draw_header(sc_state_get()->device_name);
	cfb_print(oled, "BATTERY", 0, 14);

	float pct = 0.0f;
	if (b.valid && b.battery_v > 3.0f) {
		pct = (b.battery_v - 3.0f) / (4.2f - 3.0f);
		if (pct > 1.0f) pct = 1.0f;
	}

	snprintf(l1, sizeof(l1), "%d%%", (int)(pct * 100.0f));
	cfb_framebuffer_set_font(oled, 1);
	cfb_print(oled, l1, 0, 48);

	if (b.valid) {
		snprintf(l2, sizeof(l2), "%.2fV", (double)b.battery_v);
	} else {
		snprintf(l2, sizeof(l2), "--V");
	}
	cfb_framebuffer_set_font(oled, 0);
	cfb_print(oled, l2, 80, 54);

	cfb_framebuffer_finalize(oled);
}

static void draw_status_screen(void)
{
	cfb_framebuffer_clear(oled, false);
	cfb_framebuffer_set_font(oled, 0);
	draw_header(sc_state_get()->device_name);
	cfb_print(oled, status_l1, 0, 18);
	if (status_l2[0]) {
		cfb_print(oled, status_l2, 0, 30);
	}
	cfb_framebuffer_finalize(oled);
}

int sc_display_init(void)
{
	if (!device_is_ready(oled)) {
		LOG_ERR("OLED device not ready");
		return -ENODEV;
	}

	int rc = cfb_framebuffer_init(oled);
	if (rc) {
		LOG_ERR("cfb init: %d", rc);
		return rc;
	}

	display_power(false);
	enabled = false;
	return 0;
}

void sc_display_render(void)
{
	if (!enabled) {
		display_power(false);
		return;
	}
	display_power(true);
	if (status_active) {
		draw_status_screen();
	} else if (mode == SC_DISP_BATTERY) {
		draw_battery_screen();
	} else {
		draw_count_screen();
	}
}

void sc_display_turn_on(bool open_ble_window)
{
	enabled = true;
	mode = SC_DISP_COUNT;
	status_active = false;
	sc_app_mark_activity(open_ble_window);
	sc_display_render();
}

void sc_display_turn_off(void)
{
	enabled = false;
	status_active = false;
	cfb_framebuffer_clear(oled, true);
	display_power(false);
}

void sc_display_toggle(void)
{
	if (!enabled) {
		sc_display_turn_on(true);
	} else if (mode == SC_DISP_COUNT) {
		mode = SC_DISP_BATTERY;
		sc_app_mark_activity(false);
		sc_display_render();
	} else {
		sc_display_turn_off();
	}
}

void sc_display_show_status(const char *line1, const char *line2)
{
	strncpy(status_l1, line1 ? line1 : "", sizeof(status_l1) - 1);
	strncpy(status_l2, line2 ? line2 : "", sizeof(status_l2) - 1);
	status_active = true;
	enabled = true;
	sc_display_render();
}

void sc_display_clear_status(void)
{
	status_active = false;
	status_l1[0] = '\0';
	status_l2[0] = '\0';
	sc_display_render();
}

bool sc_display_enabled(void) { return enabled; }
enum sc_display_mode sc_display_get_mode(void) { return mode; }
