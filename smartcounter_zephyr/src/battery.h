/*
 * Battery voltage sampling. The Arduino code did:
 *   - digitalWrite(D8, HIGH)            // enable divider
 *   - delay(3)
 *   - analogRead(A9) * 8/N samples
 *   - digitalWrite(D8, LOW)
 *   - apply DIVIDER_SCALE math
 *
 * In Zephyr the `voltage-divider` devicetree binding (declared in the
 * board overlay) wraps the enable GPIO, the ADC channel, and the
 * resistor ratio behind a single API call. The sample loop here only
 * has to issue N samples and average.
 */

#ifndef SMARTCOUNTER_BATTERY_H_
#define SMARTCOUNTER_BATTERY_H_

#include <stdbool.h>
#include <stdint.h>

struct sc_battery_reading {
	uint32_t raw;
	uint32_t adc_mv;
	float    adc_v;
	float    battery_v;
	bool     valid;
};

int sc_battery_init(void);
int sc_battery_read(struct sc_battery_reading *out);

int sc_die_temp_init(void);
int sc_die_temp_read_c(float *out_c);

#endif /* SMARTCOUNTER_BATTERY_H_ */
