/*
 * OLED. The Arduino code used Adafruit_GFX + Adafruit_SSD1306. In
 * Zephyr we use the generic `display` API plus the character
 * framebuffer (cfb) helpers. For a richer UI (icons, anti-aliased
 * text, custom widgets) swap cfb for LVGL (`CONFIG_LVGL=y`).
 */

#ifndef SMARTCOUNTER_DISPLAY_H_
#define SMARTCOUNTER_DISPLAY_H_

#include <stdbool.h>

enum sc_display_mode {
	SC_DISP_COUNT,
	SC_DISP_BATTERY,
};

int  sc_display_init(void);

void sc_display_turn_on(bool open_ble_window);
void sc_display_turn_off(void);
void sc_display_toggle(void);

void sc_display_show_status(const char *line1, const char *line2);
void sc_display_clear_status(void);
void sc_display_render(void);

bool sc_display_enabled(void);
enum sc_display_mode sc_display_get_mode(void);

#endif /* SMARTCOUNTER_DISPLAY_H_ */
