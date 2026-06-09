/*
 * Networking glue: WiFi STA bring-up + HTTP client.
 *
 * This file shows the *shape* of the port. A production build would
 * need:
 *   - a CA certificate provisioned with tls_credential_add() and
 *     referenced via TLS_SEC_TAG_LIST,
 *   - the URL split into scheme / host / port / path (use
 *     http_parser_url),
 *   - proper retry/backoff,
 *   - and a JSON parser invocation for the command list.
 *
 * The goal here is a faithful 1:1 mapping of the Arduino routines:
 *   connectWiFi()                     → sc_wifi_connect()
 *   wifiOff()                         → sc_wifi_disconnect()
 *   postCounterUpdate()               → sc_http_post_counter_update()
 *   fetchAndApplyPendingCommands()    → sc_http_fetch_and_apply_commands()
 *   ackDeviceCommand()                → sc_http_ack_command()
 */

#include "network.h"
#include "counter_state.h"
#include "battery.h"
#include "display.h"
#include "ble_uart.h"
#include "settings_store.h"

#include <stdio.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/net_mgmt.h>
#include <zephyr/net/wifi.h>
#include <zephyr/net/wifi_mgmt.h>
#include <zephyr/net/socket.h>
#include <zephyr/net/http/client.h>
#include <zephyr/data/json.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(sc_net, LOG_LEVEL_INF);

#define WIFI_EVENTS                                                          \
	(NET_EVENT_WIFI_CONNECT_RESULT | NET_EVENT_WIFI_DISCONNECT_RESULT |  \
	 NET_EVENT_IPV4_ADDR_ADD)

static struct net_mgmt_event_callback wifi_cb;
static K_SEM_DEFINE(wifi_connected, 0, 1);
static K_SEM_DEFINE(ip_obtained, 0, 1);
static bool wifi_link_up;

static void wifi_event_handler(struct net_mgmt_event_callback *cb,
			       uint32_t mgmt_event, struct net_if *iface)
{
	ARG_UNUSED(iface);

	switch (mgmt_event) {
	case NET_EVENT_WIFI_CONNECT_RESULT: {
		const struct wifi_status *st = (const struct wifi_status *)cb->info;
		if (st && st->status == 0) {
			wifi_link_up = true;
			k_sem_give(&wifi_connected);
		} else {
			LOG_WRN("wifi connect failed: %d", st ? st->status : -1);
		}
		break;
	}
	case NET_EVENT_WIFI_DISCONNECT_RESULT:
		wifi_link_up = false;
		break;
	case NET_EVENT_IPV4_ADDR_ADD:
		k_sem_give(&ip_obtained);
		break;
	default:
		break;
	}
}

int sc_network_init(void)
{
	net_mgmt_init_event_callback(&wifi_cb, wifi_event_handler, WIFI_EVENTS);
	net_mgmt_add_event_callback(&wifi_cb);
	return 0;
}

bool sc_wifi_connect(uint32_t timeout_ms)
{
	struct net_if *iface = net_if_get_first_wifi();
	if (!iface) {
		LOG_ERR("no wifi iface");
		return false;
	}

	struct sc_state *s = sc_state_get();
	if (s->wifi_ssid[0] == '\0') {
		LOG_WRN("wifi ssid not configured");
		return false;
	}

	if (wifi_link_up) {
		return true;
	}

	struct wifi_connect_req_params params = {
		.ssid        = (const uint8_t *)s->wifi_ssid,
		.ssid_length = strlen(s->wifi_ssid),
		.psk         = (const uint8_t *)s->wifi_pass,
		.psk_length  = strlen(s->wifi_pass),
		.channel     = WIFI_CHANNEL_ANY,
		.security    = WIFI_SECURITY_TYPE_PSK,
		.band        = WIFI_FREQ_BAND_2_4_GHZ,
		.mfp         = WIFI_MFP_OPTIONAL,
	};

	int rc = net_mgmt(NET_REQUEST_WIFI_CONNECT, iface, &params, sizeof(params));
	if (rc) {
		LOG_ERR("NET_REQUEST_WIFI_CONNECT: %d", rc);
		return false;
	}

	if (k_sem_take(&wifi_connected, K_MSEC(timeout_ms)) != 0) {
		LOG_WRN("wifi connect timeout");
		return false;
	}
	if (k_sem_take(&ip_obtained, K_MSEC(timeout_ms)) != 0) {
		LOG_WRN("ip acquisition timeout");
		return false;
	}

	LOG_INF("wifi up");
	return true;
}

void sc_wifi_disconnect(void)
{
	struct net_if *iface = net_if_get_first_wifi();
	if (!iface) return;
	(void)net_mgmt(NET_REQUEST_WIFI_DISCONNECT, iface, NULL, 0);
	wifi_link_up = false;
}

/* ----- HTTP helpers ----- */

struct http_rsp_capture {
	int     code;
	size_t  body_len;
	char    body[1024];
};

static void http_response_cb(struct http_response *rsp,
			     enum http_final_call final, void *user_data)
{
	struct http_rsp_capture *cap = user_data;
	if (!cap) return;

	cap->code = rsp->http_status_code;

	size_t take = MIN(rsp->body_frag_len, sizeof(cap->body) - 1 - cap->body_len);
	if (take && rsp->body_frag_start) {
		memcpy(&cap->body[cap->body_len], rsp->body_frag_start, take);
		cap->body_len += take;
		cap->body[cap->body_len] = '\0';
	}

	if (final == HTTP_DATA_FINAL) {
		LOG_INF("http done, code=%d, body_len=%zu", cap->code, cap->body_len);
	}
}

/*
 * Minimal "https POST a JSON body" path. The Arduino sketch built the
 * payload by string concatenation; here we use snprintf for the same
 * effect. For a structured approach use <zephyr/data/json.h>.
 *
 * NOTE: connect_to_host() and the TLS context setup are stubbed below
 * — see doc/PORT.md "HTTPS" section for the full wiring including the
 * trusted CA install via tls_credential_add().
 */
static int connect_to_host(const char *host, uint16_t port, bool tls, int *out_sock);

static int do_http_post(const char *url, const char *json_body,
			struct http_rsp_capture *cap)
{
	/* The Arduino code passed apiUrl straight to HTTPClient. Here we
	 * parse it manually. For brevity we hard-fail unknown schemes. */
	bool tls = strncmp(url, "https://", 8) == 0;
	const char *after_scheme = tls ? url + 8 : url + 7;
	const char *path_start   = strchr(after_scheme, '/');
	if (!path_start) path_start = "/";

	char host[64];
	size_t host_len = path_start - after_scheme;
	if (host_len >= sizeof(host)) return -EINVAL;
	memcpy(host, after_scheme, host_len);
	host[host_len] = '\0';

	int sock;
	int rc = connect_to_host(host, tls ? 443 : 80, tls, &sock);
	if (rc) {
		return rc;
	}

	struct http_request req = {0};
	req.method       = HTTP_POST;
	req.url          = path_start;
	req.host         = host;
	req.protocol     = "HTTP/1.1";
	req.payload      = json_body;
	req.payload_len  = strlen(json_body);
	req.response     = http_response_cb;
	req.recv_buf     = cap->body;
	req.recv_buf_len = sizeof(cap->body);

	static const char *const hdrs[] = {
		"Content-Type: application/json\r\n",
		NULL,
	};
	req.header_fields = hdrs;

	rc = http_client_req(sock, &req, 10000, cap);

	zsock_close(sock);
	return rc;
}

/* See doc/PORT.md — this is the piece that benefits most from NCS,
 * which ships a higher-level "downloader" + cert provisioning helper. */
static int connect_to_host(const char *host, uint16_t port, bool tls, int *out_sock)
{
	struct zsock_addrinfo hints = {
		.ai_family = AF_INET,
		.ai_socktype = SOCK_STREAM,
	};
	struct zsock_addrinfo *res = NULL;
	char port_str[8];

	snprintf(port_str, sizeof(port_str), "%u", port);
	int rc = zsock_getaddrinfo(host, port_str, &hints, &res);
	if (rc) {
		LOG_ERR("getaddrinfo(%s): %d", host, rc);
		return rc;
	}

	int sock = zsock_socket(res->ai_family,
				tls ? SOCK_STREAM : SOCK_STREAM,
				tls ? IPPROTO_TLS_1_2 : IPPROTO_TCP);
	if (sock < 0) {
		zsock_freeaddrinfo(res);
		return sock;
	}

	if (tls) {
		/*
		 * In a real build, install the StockNet root CA once at
		 * boot via tls_credential_add(TLS_CREDENTIAL_CA_CERTIFICATE)
		 * and reference its tag here. We use TLS_PEER_VERIFY_NONE
		 * for parity with the Arduino setInsecure() default.
		 */
		int verify = TLS_PEER_VERIFY_NONE;
		(void)zsock_setsockopt(sock, SOL_TLS, TLS_PEER_VERIFY,
				       &verify, sizeof(verify));
		(void)zsock_setsockopt(sock, SOL_TLS, TLS_HOSTNAME,
				       host, strlen(host));
	}

	rc = zsock_connect(sock, res->ai_addr, res->ai_addrlen);
	zsock_freeaddrinfo(res);
	if (rc) {
		LOG_ERR("connect: %d", rc);
		zsock_close(sock);
		return rc;
	}

	*out_sock = sock;
	return 0;
}

int sc_http_post_counter_update(void)
{
	struct sc_state *s = sc_state_get();
	if (s->api_url[0] == '\0') {
		LOG_WRN("api url empty");
		return -EINVAL;
	}

	(void)sc_settings_save();

	struct sc_battery_reading b;
	(void)sc_battery_read(&b);
	(void)sc_die_temp_read_c(&s->last_temp_c);

	if (!sc_wifi_connect(12000)) {
		(void)sc_ble_send_line("ERR WIFI CONNECT");
		return -ENETDOWN;
	}

	sc_display_show_status("POSTing...", "");

	char body[512];
	snprintf(body, sizeof(body),
		 "{"
		 "\"device_name\":\"%s\","
		 "\"part_name\":\"%s\","
		 "\"part_id\":\"%s\","
		 "\"count\":%d,"
		 "\"battery_voltage\":%.3f,"
		 "\"battery_adc_mv\":%u,"
		 "\"battery_adc_raw\":%u,"
		 "\"rssi\":%d,"
		 "\"temperature_c\":%.2f"
		 "}",
		 s->device_name, s->part_name, s->part_id, s->count_value,
		 (double)b.battery_v, b.adc_mv, b.raw,
		 s->last_rssi, (double)s->last_temp_c);

	struct http_rsp_capture cap = {0};
	int rc = do_http_post(s->api_url, body, &cap);

	if (rc == 0 && cap.code >= 200 && cap.code < 300) {
		sc_display_show_status("POST OK", "");
		return 0;
	}

	sc_display_show_status("POST FAIL", "");
	return -EIO;
}

int sc_http_ack_command(const char *command_id, const char *status,
			const char *result_message)
{
	struct sc_state *s = sc_state_get();
	if (s->api_url[0] == '\0') return -EINVAL;

	/* Reuse the same trick as the Arduino code: replace /api/counter
	 * with /api/device/commands/ack. */
	char ack_url[SC_API_URL_MAX + 32];
	const char *suffix = "/api/counter";
	const char *p = strstr(s->api_url, suffix);
	if (!p) return -EINVAL;
	size_t prefix_len = p - s->api_url;
	snprintf(ack_url, sizeof(ack_url), "%.*s/api/device/commands/ack",
		 (int)prefix_len, s->api_url);

	char body[256];
	snprintf(body, sizeof(body),
		 "{\"command_id\":\"%s\",\"status\":\"%s\",\"result_message\":\"%s\"}",
		 command_id, status, result_message);

	struct http_rsp_capture cap = {0};
	int rc = do_http_post(ack_url, body, &cap);
	return rc;
}

int sc_http_fetch_and_apply_commands(void)
{
	/*
	 * In the Arduino code we GET, ArduinoJson-parse the body, then
	 * loop applyServerCommand(). The shape is identical in Zephyr:
	 *
	 *   - call do_http_get() (analogous to do_http_post())
	 *   - parse with <zephyr/data/json.h>:
	 *
	 *       static const struct json_obj_descr cmd_descr[] = { ... };
	 *       struct cmd_msg msg; json_obj_parse(buf, len, descr, n, &msg);
	 *
	 *   - dispatch on commandType, ack each one.
	 *
	 * Kept as a stub here to keep this file readable; see
	 * doc/PORT.md "JSON" for the descriptor pattern.
	 */
	LOG_WRN("fetch+apply not yet implemented in this port skeleton");
	return -ENOTSUP;
}
