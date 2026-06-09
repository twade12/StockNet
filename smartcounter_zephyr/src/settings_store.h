/*
 * Persistent storage of `struct sc_state` to flash using Zephyr's
 * settings subsystem (NVS backend). Replaces the Arduino `Preferences`
 * calls in saveDataNow() / loadData() / applyDefaultNetworkConfig().
 */

#ifndef SMARTCOUNTER_SETTINGS_STORE_H_
#define SMARTCOUNTER_SETTINGS_STORE_H_

#include <stdbool.h>

int  sc_settings_init(void);
int  sc_settings_load(void);
int  sc_settings_save(void);

void sc_settings_schedule_save(void);

#endif /* SMARTCOUNTER_SETTINGS_STORE_H_ */
