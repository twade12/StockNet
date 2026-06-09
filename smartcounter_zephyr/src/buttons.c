#include "buttons.h"

#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(sc_buttons, LOG_LEVEL_INF);

#define BTN_UP_NODE    DT_ALIAS(btn_up)
#define BTN_DOWN_NODE  DT_ALIAS(btn_down)
#define BTN_SHOW_NODE  DT_ALIAS(btn_show)

#define DEBOUNCE_MS         60
#define SHOW_LONG_PRESS_MS  3000
#define SHOW_DBL_PRESS_MS   400

K_MSGQ_DEFINE(sc_button_events, sizeof(enum sc_button_event), 8, 4);

static const struct gpio_dt_spec btn_up   = GPIO_DT_SPEC_GET(BTN_UP_NODE,   gpios);
static const struct gpio_dt_spec btn_down = GPIO_DT_SPEC_GET(BTN_DOWN_NODE, gpios);
static const struct gpio_dt_spec btn_show = GPIO_DT_SPEC_GET(BTN_SHOW_NODE, gpios);

static struct gpio_callback btn_up_cb_data;
static struct gpio_callback btn_down_cb_data;
static struct gpio_callback btn_show_cb_data;

static int64_t last_up_ms;
static int64_t last_down_ms;
static int64_t last_show_edge_ms;

/* Show-button state machine */
static bool     show_pressed;
static bool     show_long_handled;
static int64_t  show_press_start_ms;
static bool     show_tap_pending;
static int64_t  show_tap_released_ms;

static void post_event(enum sc_button_event ev)
{
	(void)k_msgq_put(&sc_button_events, &ev, K_NO_WAIT);
}

/* Delayed work: long-press detection + pending-tap timeout */
static void show_work_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(show_work, show_work_handler);

static void show_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	int64_t now = k_uptime_get();

	if (show_pressed && !show_long_handled &&
	    (now - show_press_start_ms >= SHOW_LONG_PRESS_MS)) {
		show_long_handled = true;
		show_tap_pending  = false;
		post_event(SC_BTN_SHOW_LONG_PRESS);
		return;
	}

	if (show_tap_pending && (now - show_tap_released_ms > SHOW_DBL_PRESS_MS)) {
		show_tap_pending = false;
		post_event(SC_BTN_SHOW_TAP);
	}
}

static void btn_up_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
	ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins);
	int64_t now = k_uptime_get();
	if (now - last_up_ms < DEBOUNCE_MS) return;
	last_up_ms = now;
	post_event(SC_BTN_UP_PRESSED);
}

static void btn_down_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
	ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins);
	int64_t now = k_uptime_get();
	if (now - last_down_ms < DEBOUNCE_MS) return;
	last_down_ms = now;
	post_event(SC_BTN_DOWN_PRESSED);
}

static void btn_show_isr(const struct device *dev, struct gpio_callback *cb, uint32_t pins)
{
	ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins);

	int64_t now   = k_uptime_get();
	int level     = gpio_pin_get_dt(&btn_show);

	if (now - last_show_edge_ms < DEBOUNCE_MS) return;
	last_show_edge_ms = now;

	if (level == 1) {
		/* Press edge (logical active=1 because of GPIO_ACTIVE_LOW) */
		show_pressed       = true;
		show_long_handled  = false;
		show_press_start_ms = now;
		k_work_reschedule(&show_work, K_MSEC(SHOW_LONG_PRESS_MS));
	} else {
		/* Release edge */
		if (show_pressed && !show_long_handled) {
			if (show_tap_pending &&
			    (now - show_tap_released_ms <= SHOW_DBL_PRESS_MS)) {
				show_tap_pending = false;
				post_event(SC_BTN_SHOW_DOUBLE_TAP);
			} else {
				show_tap_pending = true;
				show_tap_released_ms = now;
				k_work_reschedule(&show_work, K_MSEC(SHOW_DBL_PRESS_MS + 10));
			}
		}
		show_pressed = false;
	}
}

static int configure_button(const struct gpio_dt_spec *spec,
			    struct gpio_callback *cb_data,
			    gpio_callback_handler_t handler,
			    gpio_flags_t edge)
{
	int rc;

	if (!gpio_is_ready_dt(spec)) {
		LOG_ERR("button GPIO not ready");
		return -ENODEV;
	}

	rc = gpio_pin_configure_dt(spec, GPIO_INPUT);
	if (rc) return rc;

	rc = gpio_pin_interrupt_configure_dt(spec, edge);
	if (rc) return rc;

	gpio_init_callback(cb_data, handler, BIT(spec->pin));
	return gpio_add_callback(spec->port, cb_data);
}

int sc_buttons_init(void)
{
	int rc;

	rc = configure_button(&btn_up,   &btn_up_cb_data,   btn_up_isr,   GPIO_INT_EDGE_TO_ACTIVE);
	if (rc) return rc;

	rc = configure_button(&btn_down, &btn_down_cb_data, btn_down_isr, GPIO_INT_EDGE_TO_ACTIVE);
	if (rc) return rc;

	rc = configure_button(&btn_show, &btn_show_cb_data, btn_show_isr, GPIO_INT_EDGE_BOTH);
	if (rc) return rc;

	LOG_INF("buttons ready");
	return 0;
}
