#include "ble_uart.h"
#include "counter_state.h"

#include <string.h>
#include <stdio.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/services/nus.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(sc_ble, LOG_LEVEL_INF);

K_MSGQ_DEFINE(sc_ble_cmd_q, sizeof(struct sc_ble_cmd), 4, 4);

static struct bt_conn *current_conn;
static bool ble_started_flag;
static bool advertising_flag;

/*
 * Advertising payload. The Arduino NimBLE code set the device name +
 * one 128-bit service UUID. Zephyr expresses that as a
 * struct bt_data array, where each entry is a (type, len, data) triplet
 * matching the Bluetooth Core spec AD format.
 */
static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0,
		      0x93, 0xF3, 0xA3, 0xB5, 0x01, 0x00, 0x40, 0x6E),
};

static const struct bt_data sd[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE,
		CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static void connected(struct bt_conn *conn, uint8_t err)
{
	if (err) {
		LOG_WRN("connect err 0x%02x", err);
		return;
	}
	current_conn = bt_conn_ref(conn);
	LOG_INF("BLE central connected");
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	LOG_INF("BLE disconnected, reason 0x%02x", reason);
	if (current_conn) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
	}
	/* The application decides whether to re-advertise; see main.c. */
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected    = connected,
	.disconnected = disconnected,
};

/*
 * NUS callbacks. `received` is invoked off the BT RX thread; we copy
 * the payload into a message queue so the main thread can do the
 * heavy command parsing without blocking the BLE stack.
 */
static void nus_received(struct bt_conn *conn, const void *data, uint16_t len)
{
	ARG_UNUSED(conn);

	struct sc_ble_cmd cmd = {0};
	size_t n = MIN(len, sizeof(cmd.data) - 1);
	memcpy(cmd.data, data, n);
	cmd.data[n] = '\0';

	(void)k_msgq_put(&sc_ble_cmd_q, &cmd, K_NO_WAIT);
}

static struct bt_nus_cb nus_cb = {
	.received = nus_received,
};

int sc_ble_init(void)
{
	int rc;

	rc = bt_enable(NULL);
	if (rc) {
		LOG_ERR("bt_enable: %d", rc);
		return rc;
	}

	rc = bt_nus_cb_register(&nus_cb, NULL);
	if (rc) {
		LOG_ERR("bt_nus_cb_register: %d", rc);
		return rc;
	}

	/* Push the user-configurable device name into the BLE stack so
	 * the scan-response sd[] uses the right value. */
	rc = bt_set_name(sc_state_get()->device_name);
	if (rc) {
		LOG_WRN("bt_set_name: %d", rc);
	}

	ble_started_flag = true;
	return 0;
}

int sc_ble_start_advertising(void)
{
	if (!ble_started_flag) return -ENODEV;

	int rc = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad),
				 sd, ARRAY_SIZE(sd));
	if (rc && rc != -EALREADY) {
		LOG_ERR("adv start failed: %d", rc);
		return rc;
	}

	advertising_flag = true;
	LOG_INF("advertising started");
	return 0;
}

int sc_ble_stop_advertising(void)
{
	if (!advertising_flag) return 0;
	(void)bt_le_adv_stop();
	advertising_flag = false;
	LOG_INF("advertising stopped");
	return 0;
}

int sc_ble_send_line(const char *msg)
{
	if (!current_conn || !msg) {
		return -ENOTCONN;
	}
	return bt_nus_send(current_conn, msg, strlen(msg));
}

bool sc_ble_is_connected(void)       { return current_conn != NULL; }
bool sc_ble_advertising_active(void) { return advertising_flag; }
bool sc_ble_started(void)            { return ble_started_flag; }
