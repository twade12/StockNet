#include "battery.h"

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/adc/voltage_divider.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(sc_battery, LOG_LEVEL_INF);

#define VBATT_NODE     DT_NODELABEL(vbatt)
#define DIE_TEMP_NODE  DT_ALIAS(die_temp)

#define ENABLE_SETTLE_MS 3
#define SAMPLE_GAP_MS    2

static const struct voltage_divider_dt_spec vbatt =
	VOLTAGE_DIVIDER_DT_SPEC_GET(VBATT_NODE);

static const struct device *const die_temp = DEVICE_DT_GET(DIE_TEMP_NODE);

int sc_battery_init(void)
{
	int rc;

	if (!adc_is_ready_dt(&vbatt.port)) {
		LOG_ERR("ADC channel not ready");
		return -ENODEV;
	}

	rc = adc_channel_setup_dt(&vbatt.port);
	if (rc) {
		LOG_ERR("adc_channel_setup_dt: %d", rc);
		return rc;
	}

	if (vbatt.power_gpios.port) {
		rc = gpio_pin_configure_dt(&vbatt.power_gpios, GPIO_OUTPUT_INACTIVE);
		if (rc) {
			LOG_ERR("battery enable GPIO configure: %d", rc);
			return rc;
		}
	}

	return 0;
}

int sc_battery_read(struct sc_battery_reading *out)
{
	int rc;
	int32_t sum_mv = 0;
	int32_t sum_raw = 0;
	uint32_t samples = CONFIG_SMARTCOUNTER_BATTERY_SAMPLES;

	if (vbatt.power_gpios.port) {
		gpio_pin_set_dt(&vbatt.power_gpios, 1);
		k_msleep(ENABLE_SETTLE_MS);
	}

	/* Discard first sample after enabling the divider */
	int32_t throwaway_mv = 0;
	(void)voltage_divider_sample_to_mv(&vbatt, &throwaway_mv);
	k_msleep(1);

	for (uint32_t i = 0; i < samples; i++) {
		int32_t mv = 0;
		rc = voltage_divider_sample_to_mv(&vbatt, &mv);
		if (rc) {
			break;
		}
		sum_mv  += mv;
		sum_raw += mv; /* raw ADC count not surfaced by the helper */
		k_msleep(SAMPLE_GAP_MS);
	}

	if (vbatt.power_gpios.port) {
		gpio_pin_set_dt(&vbatt.power_gpios, 0);
	}

	if (rc) {
		out->valid = false;
		return rc;
	}

	out->raw       = (uint32_t)(sum_raw / (int32_t)samples);
	out->adc_mv    = (uint32_t)(sum_mv  / (int32_t)samples);
	out->adc_v     = out->adc_mv / 1000.0f;
	out->battery_v = out->adc_v *
			 ((float)vbatt.full_ohms / (float)vbatt.output_ohms);
	out->valid     = true;

	return 0;
}

int sc_die_temp_init(void)
{
	if (!device_is_ready(die_temp)) {
		LOG_WRN("die_temp device not ready");
		return -ENODEV;
	}
	return 0;
}

int sc_die_temp_read_c(float *out_c)
{
	struct sensor_value v;
	int rc;

	if (!device_is_ready(die_temp)) {
		*out_c = -999.0f;
		return -ENODEV;
	}

	rc = sensor_sample_fetch(die_temp);
	if (rc) {
		*out_c = -999.0f;
		return rc;
	}
	rc = sensor_channel_get(die_temp, SENSOR_CHAN_DIE_TEMP, &v);
	if (rc) {
		*out_c = -999.0f;
		return rc;
	}

	*out_c = (float)sensor_value_to_double(&v);
	return 0;
}
