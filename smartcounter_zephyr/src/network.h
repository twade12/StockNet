/*
 * WiFi + HTTP layer. Replaces:
 *   - WiFi.begin / WiFi.status              (Arduino WiFi.h)
 *   - HTTPClient + WiFiClientSecure         (HTTPClient.h)
 *   - ArduinoJson DynamicJsonDocument       (ArduinoJson.h)
 *
 * Zephyr equivalents:
 *   - net_mgmt + struct wifi_connect_req_params
 *   - http_client_req() over BSD-style sockets
 *   - mbedTLS via TLS_SEC_TAG (HTTPS)
 *   - <zephyr/data/json.h> (declarative parser)
 */

#ifndef SMARTCOUNTER_NETWORK_H_
#define SMARTCOUNTER_NETWORK_H_

#include <stdbool.h>

int  sc_network_init(void);

bool sc_wifi_connect(uint32_t timeout_ms);
void sc_wifi_disconnect(void);

int  sc_http_post_counter_update(void);
int  sc_http_fetch_and_apply_commands(void);
int  sc_http_ack_command(const char *command_id, const char *status,
			 const char *result_message);

#endif /* SMARTCOUNTER_NETWORK_H_ */
