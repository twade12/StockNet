/*
 * Smart Counter — main orchestrator.
 *
 * Replaces the Arduino setup() / loop() pair with:
 *   - main() that initialises every subsystem and falls into the run
 *     loop on the main thread,
 *   - a delayable work item that drives the "idle ⇒ deep sleep"
 *     transition,
 *   - a worker thread that consumes BLE RX commands and button events
 *     off message queues,
 *   - sys_poweroff() for the deep-sleep equivalent of
 *     esp_deep_sleep_start().
 *
 * RTC-retained boot counter from RTC_DATA_ATTR is implemented via
 * Zephyr's retention subsystem (linker-reserved noinit region).
 */

#include <stdio.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <zephyr/logging/log.h>
#include <zephyr/retention/retention.h>
#include <zephyr/sys/poweroff.h>
#include <zephyr/pm/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/devicetree.h>

#include "counter_state.h"
#include "settings_store.h"
#include "buttons.h"
#include "battery.h"
#include "display.h"
#include "ble_uart.h"
#include "network.h"

LOG_MODULE_REGISTER(sc_app, LOG_LEVEL_INF);

/* -------------------------------------------------------------------------
 * Configuration
 * ------------------------------------------------------------------------- */

#define LOCAL_AWAKE_WINDOW_MS   15000
#define TIMER_AWAKE_WINDOW_MS   15000
#define BOOT_AWAKE_WINDOW_MS    5000
#define BLE_WINDOW_MS_DEFAULT   60000

enum wake_mode {
	WAKE_COLD_BOOT,
	WAKE_FROM_TIMER,
	WAKE_FROM_BUTTON,
};

/* -------------------------------------------------------------------------
 * Retained data across deep sleep (replaces RTC_DATA_ATTR)
 * ------------------------------------------------------------------------- */

struct retained_data {
	uint32_t boot_count;
	uint32_t last_wake_mode;
};

/* Retention area must be declared in the board overlay as a `retained_mem`
 * node; this is the read/write handle. */
#if DT_HAS_CHOSEN(zephyr_retention)
static const struct device *retain_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_retention));
#endif

static struct retained_data retain_state;

/* -------------------------------------------------------------------------
 * Run-time state (formerly loose globals in the Arduino sketch)
 * ------------------------------------------------------------------------- */

static enum wake_mode  s_wake_mode = WAKE_COLD_BOOT;
static int64_t         s_awake_until_ms;
static int64_t         s_last_activity_ms;
static int64_t         s_ble_active_until_ms;
static bool            s_periodic_work_done;

void sc_app_mark_activity(bool open_ble_window)
{
	s_last_activity_ms = k_uptime_get();
	s_awake_until_ms   = s_last_activity_ms + LOCAL_AWAKE_WINDOW_MS;

	if (open_ble_window) {
		s_ble_active_until_ms = s_last_activity_ms + BLE_WINDOW_MS_DEFAULT;
		if (!sc_ble_advertising_active()) {
			(void)sc_ble_start_advertising();
		}
	}
}

/* -------------------------------------------------------------------------
 * Counter actions (mirror Arduino incrementCount / decrementCount / reset)
 * ------------------------------------------------------------------------- */

static void increment_count(void)
{
	char buf[32];
	struct sc_state *s = sc_state_get();

	k_mutex_lock(&sc_state_lock, K_FOREVER);
	s->count_value++;
	k_mutex_unlock(&sc_state_lock);

	sc_settings_schedule_save();
	sc_display_turn_on(false);
	snprintf(buf, sizeof(buf), "OK COUNT=%d", s->count_value);
	(void)sc_ble_send_line(buf);
}

static void decrement_count(void)
{
	char buf[32];
	struct sc_state *s = sc_state_get();

	k_mutex_lock(&sc_state_lock, K_FOREVER);
	if (s->count_value > 0) s->count_value--;
	k_mutex_unlock(&sc_state_lock);

	sc_settings_schedule_save();
	sc_display_turn_on(false);
	snprintf(buf, sizeof(buf), "OK COUNT=%d", s->count_value);
	(void)sc_ble_send_line(buf);
}

static void reset_count(void)
{
	struct sc_state *s = sc_state_get();
	k_mutex_lock(&sc_state_lock, K_FOREVER);
	s->count_value = 0;
	k_mutex_unlock(&sc_state_lock);

	sc_settings_schedule_save();
	sc_display_turn_on(true);
	(void)sc_ble_send_line("OK RESET COUNT=0");
}

/* -------------------------------------------------------------------------
 * Command parsing (parity with the Arduino processCommand())
 * ------------------------------------------------------------------------- */

static void send_state_dump(void)
{
	struct sc_state *s = sc_state_get();
	struct sc_battery_reading b;
	(void)sc_battery_read(&b);

	char line[256];
	snprintf(line, sizeof(line),
		 "DEVNAME=%s\nNAME=%s\nID=%s\nCOUNT=%d\nBATT_V=%.3f\nBATT_MV=%u\n",
		 s->device_name, s->part_name, s->part_id, s->count_value,
		 (double)b.battery_v, b.adc_mv);
	(void)sc_ble_send_line(line);
}

static void process_command(char *cmd)
{
	/* Strip CR/LF and surrounding whitespace */
	char *p = cmd;
	while (*p == ' ' || *p == '\t') p++;
	size_t len = strlen(p);
	while (len && (p[len-1] == '\r' || p[len-1] == '\n' || p[len-1] == ' ')) {
		p[--len] = '\0';
	}
	if (len == 0) return;

	sc_app_mark_activity(true);

	if (strcmp(p, "HELP") == 0) {
		(void)sc_ble_send_line("CMDS: HELP, GET, COUNT=..., RESET, POST_NOW, FETCH_NOW, SLEEP_NOW, BATT_NOW");
		return;
	}
	if (strcmp(p, "GET") == 0)        { send_state_dump(); return; }
	if (strcmp(p, "RESET") == 0)      { reset_count();     return; }
	if (strcmp(p, "POST_NOW") == 0)   {
		int rc = sc_http_post_counter_update();
		sc_wifi_disconnect();
		(void)sc_ble_send_line(rc == 0 ? "OK POST_NOW" : "ERR POST_NOW");
		return;
	}
	if (strcmp(p, "FETCH_NOW") == 0)  {
		int rc = sc_http_fetch_and_apply_commands();
		sc_wifi_disconnect();
		(void)sc_ble_send_line(rc == 0 ? "OK FETCH_NOW" : "ERR FETCH_NOW");
		return;
	}
	if (strcmp(p, "SLEEP_NOW") == 0)  {
		(void)sc_ble_send_line("OK SLEEPING");
		s_awake_until_ms = 0;
		return;
	}
	if (strncmp(p, "COUNT=", 6) == 0) {
		int v = atoi(p + 6);
		if (v < 0) v = 0;
		k_mutex_lock(&sc_state_lock, K_FOREVER);
		sc_state_get()->count_value = v;
		k_mutex_unlock(&sc_state_lock);
		sc_settings_schedule_save();
		sc_display_turn_on(true);
		(void)sc_ble_send_line("OK COUNT");
		return;
	}

	(void)sc_ble_send_line("ERR UNKNOWN CMD");
}

/* -------------------------------------------------------------------------
 * Background thread that processes button + BLE events.
 * ------------------------------------------------------------------------- */

static K_THREAD_STACK_DEFINE(worker_stack, 4096);
static struct k_thread worker_thread;

static void worker(void *p1, void *p2, void *p3)
{
	ARG_UNUSED(p1); ARG_UNUSED(p2); ARG_UNUSED(p3);

	struct k_poll_event events[] = {
		K_POLL_EVENT_STATIC_INITIALIZER(K_POLL_TYPE_MSGQ_DATA_AVAILABLE,
						K_POLL_MODE_NOTIFY_ONLY,
						&sc_button_events, 0),
		K_POLL_EVENT_STATIC_INITIALIZER(K_POLL_TYPE_MSGQ_DATA_AVAILABLE,
						K_POLL_MODE_NOTIFY_ONLY,
						&sc_ble_cmd_q, 0),
	};

	while (1) {
		(void)k_poll(events, ARRAY_SIZE(events), K_FOREVER);

		if (events[0].state == K_POLL_STATE_MSGQ_DATA_AVAILABLE) {
			enum sc_button_event ev;
			while (k_msgq_get(&sc_button_events, &ev, K_NO_WAIT) == 0) {
				sc_app_mark_activity(ev == SC_BTN_SHOW_TAP);
				switch (ev) {
				case SC_BTN_UP_PRESSED:        increment_count(); break;
				case SC_BTN_DOWN_PRESSED:      decrement_count(); break;
				case SC_BTN_SHOW_TAP:          sc_display_toggle(); break;
				case SC_BTN_SHOW_DOUBLE_TAP:
					sc_display_show_status("Manual sync", "Fetch + POST");
					(void)sc_http_fetch_and_apply_commands();
					(void)sc_http_post_counter_update();
					sc_wifi_disconnect();
					sc_display_clear_status();
					break;
				case SC_BTN_SHOW_LONG_PRESS:   reset_count(); break;
				}
			}
		}

		if (events[1].state == K_POLL_STATE_MSGQ_DATA_AVAILABLE) {
			struct sc_ble_cmd cmd;
			while (k_msgq_get(&sc_ble_cmd_q, &cmd, K_NO_WAIT) == 0) {
				process_command(cmd.data);
			}
		}

		events[0].state = K_POLL_STATE_NOT_READY;
		events[1].state = K_POLL_STATE_NOT_READY;
	}
}

/* -------------------------------------------------------------------------
 * Deep sleep
 * ------------------------------------------------------------------------- */

#define BTN_SHOW_NODE DT_ALIAS(btn_show)

static void enter_deep_sleep(void)
{
	(void)sc_settings_save();
	(void)sc_ble_stop_advertising();
	sc_wifi_disconnect();
	sc_display_turn_off();

	/* Configure the SHOW button as the wake source. On Zephyr the
	 * portable hook is `gpio_pin_interrupt_configure_dt(..., GPIO_INT_LEVEL_LOW)`
	 * with the pin declared `wakeup-source` in the devicetree. */
	const struct gpio_dt_spec btn_show = GPIO_DT_SPEC_GET(BTN_SHOW_NODE, gpios);
	(void)gpio_pin_interrupt_configure_dt(&btn_show, GPIO_INT_LEVEL_ACTIVE);

#if DT_HAS_CHOSEN(zephyr_retention)
	if (retain_dev && device_is_ready(retain_dev)) {
		retain_state.boot_count++;
		retain_state.last_wake_mode = WAKE_FROM_TIMER;
		(void)retention_write(retain_dev, 0,
				      (uint8_t *)&retain_state, sizeof(retain_state));
	}
#endif

	LOG_INF("entering soft-off");
	k_msleep(50);

	/* sys_poweroff() halts the CPU; wakeup-source GPIOs (and any
	 * RTC timer programmed earlier) trigger a full reboot — same
	 * semantics as the ESP32 deep-sleep cycle. To schedule a timed
	 * wake on ESP32-S3 you would call esp_sleep_enable_timer_wakeup()
	 * prior to this; in Zephyr you'd use a counter device with
	 * counter_set_alarm() + COUNTER_ALARM_CFG_ABSOLUTE on a
	 * wake-capable timer. */
	sys_poweroff();
}

static bool ready_for_deep_sleep(void)
{
	int64_t now = k_uptime_get();
	if (sc_ble_is_connected()) return false;
	if (sc_display_enabled())  return false;
	if (now < s_awake_until_ms) return false;
	return true;
}

/* -------------------------------------------------------------------------
 * Wake handling (parity with handleWakeBootBehavior())
 * ------------------------------------------------------------------------- */

static enum wake_mode decode_wake_mode(void)
{
	/* On Zephyr the post-poweroff reboot looks like a fresh boot;
	 * the application is expected to read its retention region to
	 * tell cold-boot from wake. */
#if DT_HAS_CHOSEN(zephyr_retention)
	if (retain_dev && device_is_ready(retain_dev)) {
		(void)retention_read(retain_dev, 0,
				     (uint8_t *)&retain_state, sizeof(retain_state));
		if (retain_state.boot_count > 0) {
			return (enum wake_mode)retain_state.last_wake_mode;
		}
	}
#endif
	return WAKE_COLD_BOOT;
}

static void apply_wake_mode(enum wake_mode m)
{
	s_wake_mode          = m;
	s_periodic_work_done = false;

	switch (m) {
	case WAKE_FROM_BUTTON:
		sc_display_turn_on(true);
		s_awake_until_ms = k_uptime_get() + LOCAL_AWAKE_WINDOW_MS;
		break;
	case WAKE_FROM_TIMER:
		sc_display_turn_off();
		s_awake_until_ms = k_uptime_get() + TIMER_AWAKE_WINDOW_MS;
		break;
	case WAKE_COLD_BOOT:
	default:
		sc_display_turn_on(false);
		s_awake_until_ms = k_uptime_get() + BOOT_AWAKE_WINDOW_MS;
		break;
	}

	s_last_activity_ms = k_uptime_get();
}

/* -------------------------------------------------------------------------
 * main()
 * ------------------------------------------------------------------------- */

int main(void)
{
	LOG_INF("Smart Counter boot");

	sc_state_init_defaults(sc_state_get());

	if (sc_settings_init() == 0) {
		(void)sc_settings_load();
	}

	(void)sc_battery_init();
	(void)sc_die_temp_init();
	(void)sc_display_init();
	(void)sc_buttons_init();
	(void)sc_ble_init();
	(void)sc_network_init();

	apply_wake_mode(decode_wake_mode());

	(void)sc_ble_start_advertising();
	s_ble_active_until_ms = k_uptime_get() + BLE_WINDOW_MS_DEFAULT;

	k_thread_create(&worker_thread, worker_stack,
			K_THREAD_STACK_SIZEOF(worker_stack),
			worker, NULL, NULL, NULL,
			7, 0, K_NO_WAIT);
	k_thread_name_set(&worker_thread, "sc_worker");

	/* "Cold boot verification POST" — same as Arduino loop()'s
	 * `if (wakeMode == WAKE_COLD_BOOT && !periodicWorkDone) ...` */
	if (s_wake_mode == WAKE_COLD_BOOT || s_wake_mode == WAKE_FROM_TIMER) {
		s_periodic_work_done = true;
		(void)sc_http_fetch_and_apply_commands();
		(void)sc_http_post_counter_update();
		sc_wifi_disconnect();
	}

	while (1) {
		int64_t now = k_uptime_get();

		/* OLED idle timeout */
		struct sc_state *s = sc_state_get();
		if (sc_display_enabled() &&
		    (now - s_last_activity_ms >= (int64_t)s->display_timeout_sec * 1000)) {
			sc_display_turn_off();
		}

		/* Stop advertising once the BLE window expires without a peer */
		if (sc_ble_advertising_active() && !sc_ble_is_connected() &&
		    now >= s_ble_active_until_ms) {
			(void)sc_ble_stop_advertising();
		}

		if (ready_for_deep_sleep()) {
			enter_deep_sleep();
			/* unreachable */
		}

		k_msleep(50);
	}

	return 0;
}
