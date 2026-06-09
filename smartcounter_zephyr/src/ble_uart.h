/*
 * Nordic UART Service (NUS) wrapper.
 *
 *   Service UUID : 6E400001-B5A3-F393-E0A9-E50E24DCCA9E
 *   RX char UUID : 6E400002-...  (peer writes, device receives)
 *   TX char UUID : 6E400003-...  (device notifies, peer reads)
 *
 * In Zephyr this service is shipped as `BT_ZEPHYR_NUS` (struct
 * bt_nus_cb, bt_nus_send, etc.). In nRF Connect SDK the equivalent
 * lives at <bluetooth/services/nus.h> and exposes essentially the
 * same API; the prj.conf swap is `CONFIG_BT_NUS=y`.
 *
 * RX bytes are queued and consumed off the BLE callback context, just
 * like the Arduino bleCmdQueue[] ring did.
 */

#ifndef SMARTCOUNTER_BLE_UART_H_
#define SMARTCOUNTER_BLE_UART_H_

#include <stdbool.h>
#include <stddef.h>
#include <zephyr/kernel.h>

#define SC_BLE_CMD_MAX_LEN 192

struct sc_ble_cmd {
	char data[SC_BLE_CMD_MAX_LEN];
};

extern struct k_msgq sc_ble_cmd_q;

int  sc_ble_init(void);

int  sc_ble_start_advertising(void);
int  sc_ble_stop_advertising(void);

int  sc_ble_send_line(const char *msg);

bool sc_ble_is_connected(void);
bool sc_ble_advertising_active(void);
bool sc_ble_started(void);

#endif /* SMARTCOUNTER_BLE_UART_H_ */
