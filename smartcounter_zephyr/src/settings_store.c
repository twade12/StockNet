#include "settings_store.h"
#include "counter_state.h"

#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(sc_settings, LOG_LEVEL_INF);

#define SAVE_DEFER_MS 2000

static struct k_work_delayable save_work;

static int sc_settings_set(const char *name, size_t len,
			    settings_read_cb read_cb, void *cb_arg)
{
	struct sc_state *s = sc_state_get();
	const char *next;
	int rc;

	if (settings_name_steq(name, "state", &next) && !next) {
		if (len != sizeof(*s)) {
			return -EINVAL;
		}
		k_mutex_lock(&sc_state_lock, K_FOREVER);
		rc = read_cb(cb_arg, s, sizeof(*s));
		k_mutex_unlock(&sc_state_lock);
		return (rc >= 0) ? 0 : rc;
	}

	return -ENOENT;
}

SETTINGS_STATIC_HANDLER_DEFINE(sc, "sc", NULL, sc_settings_set, NULL, NULL);

int sc_settings_init(void)
{
	int rc = settings_subsys_init();
	if (rc) {
		LOG_ERR("settings_subsys_init failed: %d", rc);
		return rc;
	}
	return 0;
}

int sc_settings_load(void)
{
	return settings_load_subtree("sc");
}

int sc_settings_save(void)
{
	struct sc_state *s = sc_state_get();
	int rc;

	k_mutex_lock(&sc_state_lock, K_FOREVER);
	rc = settings_save_one("sc/state", s, sizeof(*s));
	k_mutex_unlock(&sc_state_lock);

	if (rc) {
		LOG_ERR("settings save failed: %d", rc);
	} else {
		LOG_INF("settings saved");
	}
	return rc;
}

static void save_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	(void)sc_settings_save();
}

void sc_settings_schedule_save(void)
{
	if (!save_work.work.handler) {
		k_work_init_delayable(&save_work, save_work_handler);
	}
	(void)k_work_reschedule(&save_work, K_MSEC(SAVE_DEFER_MS));
}
