/*
 * Button input. The Arduino sketch debounced via timestamps inside
 * loop(); in Zephyr we use the GPIO subsystem with edge-triggered
 * interrupts and a delayed work item that performs the debounce +
 * tap/double-tap/long-press detection.
 *
 * Three semantic events are emitted via a k_msgq:
 *   SC_BTN_UP_PRESSED      → incrementCount()
 *   SC_BTN_DOWN_PRESSED    → decrementCount()
 *   SC_BTN_SHOW_TAP        → toggleDisplay()
 *   SC_BTN_SHOW_DOUBLE_TAP → manual fetch + POST
 *   SC_BTN_SHOW_LONG_PRESS → resetCount()
 */

#ifndef SMARTCOUNTER_BUTTONS_H_
#define SMARTCOUNTER_BUTTONS_H_

#include <zephyr/kernel.h>

enum sc_button_event {
	SC_BTN_UP_PRESSED,
	SC_BTN_DOWN_PRESSED,
	SC_BTN_SHOW_TAP,
	SC_BTN_SHOW_DOUBLE_TAP,
	SC_BTN_SHOW_LONG_PRESS,
};

extern struct k_msgq sc_button_events;

int sc_buttons_init(void);

#endif /* SMARTCOUNTER_BUTTONS_H_ */
