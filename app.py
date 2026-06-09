from flask import Flask, request, jsonify, render_template, Response
from datetime import datetime, timezone
from decimal import Decimal
import os
import math
import csv
import io
import json as json_lib
import threading
import urllib.request
import urllib.error
import hashlib
import hmac as hmac_lib
import atexit
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

app = Flask(__name__)

DATABASE_URL = "postgresql://postgres:password@127.0.0.1:5433/StockNet"
PORT = int(os.environ.get("FLASK_PORT", "5007"))
HOST = os.environ.get("FLASK_HOST", "0.0.0.0")

pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=2,
    max_size=10,
    kwargs={"row_factory": dict_row},
)
atexit.register(pool.close)



def get_db():
    return pool.connection()


def choose_bucket_seconds(total_seconds: float, max_points: int) -> int:
    if total_seconds <= 0:
        return 1
    rough = max(1, math.ceil(total_seconds / max_points))
    candidates = [1, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400]
    for c in candidates:
        if c >= rough:
            return c
    return candidates[-1]


def fire_webhooks(event_type: str, payload: dict):
    """Fire all active webhooks for event_type in a background thread (non-blocking)."""
    def _send():
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT url, secret FROM webhooks "
                        "WHERE is_active = TRUE AND %(et)s = ANY(event_types)",
                        {'et': event_type}
                    )
                    hooks = cur.fetchall()
            body = json_lib.dumps({'event': event_type, 'data': payload}).encode()
            for hook in hooks:
                try:
                    req = urllib.request.Request(
                        hook['url'], data=body,
                        headers={'Content-Type': 'application/json'}
                    )
                    if hook['secret']:
                        sig = hmac_lib.new(
                            hook['secret'].encode(), body, hashlib.sha256
                        ).hexdigest()
                        req.add_header('X-StockNet-Signature', f'sha256={sig}')
                    urllib.request.urlopen(req, timeout=5)
                except Exception:
                    pass
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/health')
def health():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT now() AS now_ts')
            row = cur.fetchone()
    return jsonify({'ok': True, 'db_time': row['now_ts'].isoformat()})


@app.route("/api/counter", methods=["POST"])
def counter():
    payload = request.get_json(silent=True)

    print("COUNTER PAYLOAD: ",payload)

    if not payload:
        return jsonify({"ok": False, "error": "invalid or missing JSON"}), 400

    required = ["device_name", "part_name", "part_id", "count"]
    missing = [k for k in required if k not in payload]
    if missing:
        return jsonify(
            {"ok": False, "error": f"missing fields: {', '.join(missing)}"}
        ), 400

    received_at = datetime.now(timezone.utc)
    device_name = str(payload["device_name"]).strip()
    part_name = str(payload["part_name"]).strip()
    part_id = str(payload["part_id"]).strip()

    try:
        count_value = float(payload["count"])
    except Exception:
        return jsonify({"ok": False, "error": "count must be numeric"}), 400

    ip_address = payload.get("ip") or request.remote_addr
    firmware_version = payload.get("firmware_version")
    wifi_ssid = payload.get("wifi_ssid")
    display_timeout_sec = payload.get("display_timeout_sec")

    # Battery monitoring (optional fields from hardware voltage divider)
    battery_voltage = None
    battery_adc_mv = None
    battery_adc_raw = None
    rssi = None
    temperature_c = None
    try:
        if "battery_voltage" in payload:
            battery_voltage = float(payload["battery_voltage"])
        if "battery_adc_mv" in payload:
            battery_adc_mv = int(payload["battery_adc_mv"])
        if "battery_adc_raw" in payload:
            battery_adc_raw = int(payload["battery_adc_raw"])
        if "rssi" in payload:
            rssi = int(payload["rssi"])
        if "temperature_c" in payload:
            temperature_c = float(payload["temperature_c"])
    except (ValueError, TypeError):
        pass  # ignore malformed fields

    # Compute battery percentage (3.0V=0%, 4.2V=100%)
    battery_pct = None
    if battery_voltage is not None:
        battery_pct = round(max(0.0, min(100.0, (battery_voltage - 3.0) / (4.2 - 3.0) * 100.0)), 1)

    raw_payload = payload
    _wh_stock_event = None   # populated if a new stock alert fires
    _wh_batt_event  = None   # populated if a new battery alert fires

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 1) Upsert device
                cur.execute(
                    """
                    INSERT INTO devices (
                        device_name,
                        reported_part_id,
                        reported_part_name,
                        firmware_version,
                        ip_address,
                        wifi_ssid,
                        display_timeout_sec,
                        battery_voltage,
                        battery_adc_mv,
                        battery_adc_raw,
                        battery_updated_at,
                        rssi,
                        temperature_c,
                        last_seen_at,
                        last_event_at
                    )
                    VALUES (
                        %(device_name)s,
                        %(reported_part_id)s,
                        %(reported_part_name)s,
                        %(firmware_version)s,
                        %(ip_address)s,
                        %(wifi_ssid)s,
                        %(display_timeout_sec)s,
                        %(battery_voltage)s,
                        %(battery_adc_mv)s,
                        %(battery_adc_raw)s,
                        CASE WHEN %(battery_voltage)s IS NOT NULL THEN now() END,
                        %(rssi)s,
                        %(temperature_c)s,
                        now(),
                        %(received_at)s
                    )
                    ON CONFLICT (device_name)
                    DO UPDATE SET
                        reported_part_id = EXCLUDED.reported_part_id,
                        reported_part_name = EXCLUDED.reported_part_name,
                        firmware_version = EXCLUDED.firmware_version,
                        ip_address = EXCLUDED.ip_address,
                        wifi_ssid = EXCLUDED.wifi_ssid,
                        display_timeout_sec = EXCLUDED.display_timeout_sec,
                        battery_voltage = COALESCE(EXCLUDED.battery_voltage, devices.battery_voltage),
                        battery_adc_mv = COALESCE(EXCLUDED.battery_adc_mv, devices.battery_adc_mv),
                        battery_adc_raw = COALESCE(EXCLUDED.battery_adc_raw, devices.battery_adc_raw),
                        battery_updated_at = CASE WHEN EXCLUDED.battery_voltage IS NOT NULL THEN now() ELSE devices.battery_updated_at END,
                        rssi = COALESCE(EXCLUDED.rssi, devices.rssi),
                        temperature_c = COALESCE(EXCLUDED.temperature_c, devices.temperature_c),
                        last_seen_at = now(),
                        last_event_at = EXCLUDED.last_event_at
                    RETURNING
                        device_id,
                        device_name,
                        assignment_type,
                        assigned_component_id,
                        assigned_assembly_id,
                        warning_threshold,
                        critical_threshold,
                        battery_voltage;
                    """,
                    {
                        "device_name": device_name,
                        "reported_part_id": part_id,
                        "reported_part_name": part_name,
                        "firmware_version": firmware_version,
                        "ip_address": ip_address,
                        "wifi_ssid": wifi_ssid,
                        "display_timeout_sec": display_timeout_sec,
                        "battery_voltage": battery_voltage,
                        "battery_adc_mv": battery_adc_mv,
                        "battery_adc_raw": battery_adc_raw,
                        "rssi": rssi,
                        "temperature_c": temperature_c,
                        "received_at": received_at,
                    },
                )
                device_row = cur.fetchone()

                # 1.5) Insert time-series health reading
                cur.execute(
                    """
                    INSERT INTO device_health_readings (
                        device_id, device_name, recorded_at,
                        battery_voltage, battery_pct,
                        rssi, temperature_c, ip_address
                    ) VALUES (
                        %(device_id)s, %(device_name)s, %(recorded_at)s,
                        %(battery_voltage)s, %(battery_pct)s,
                        %(rssi)s, %(temperature_c)s, %(ip_address)s
                    )
                    """,
                    {
                        "device_id": device_row["device_id"],
                        "device_name": device_name,
                        "recorded_at": received_at,
                        "battery_voltage": battery_voltage,
                        "battery_pct": battery_pct,
                        "rssi": rssi,
                        "temperature_c": temperature_c,
                        "ip_address": ip_address,
                    },
                )

                # 2) Insert raw counter event
                cur.execute(
                    """
                    INSERT INTO counter_events (
                        received_at,
                        device_id,
                        device_name,
                        assignment_type,
                        component_id,
                        assembly_id,
                        reported_part_id,
                        reported_part_name,
                        count_value,
                        battery_voltage,
                        battery_adc_mv,
                        battery_adc_raw,
                        ip_address,
                        firmware_version,
                        wifi_ssid,
                        raw_payload
                    )
                    VALUES (
                        %(received_at)s,
                        %(device_id)s,
                        %(device_name)s,
                        %(assignment_type)s,
                        %(component_id)s,
                        %(assembly_id)s,
                        %(reported_part_id)s,
                        %(reported_part_name)s,
                        %(count_value)s,
                        %(battery_voltage)s,
                        %(battery_adc_mv)s,
                        %(battery_adc_raw)s,
                        %(ip_address)s,
                        %(firmware_version)s,
                        %(wifi_ssid)s,
                        %(raw_payload)s::jsonb
                    )
                    RETURNING event_id;
                    """,
                    {
                        "received_at": received_at,
                        "device_id": device_row["device_id"],
                        "device_name": device_name,
                        "assignment_type": device_row["assignment_type"],
                        "component_id": device_row["assigned_component_id"],
                        "assembly_id": device_row["assigned_assembly_id"],
                        "reported_part_id": part_id,
                        "reported_part_name": part_name,
                        "count_value": count_value,
                        "battery_voltage": battery_voltage,
                        "battery_adc_mv": battery_adc_mv,
                        "battery_adc_raw": battery_adc_raw,
                        "ip_address": ip_address,
                        "firmware_version": firmware_version,
                        "wifi_ssid": wifi_ssid,
                        "raw_payload": psycopg.types.json.Jsonb(raw_payload),
                    },
                )
                event_row = cur.fetchone()

                # 3) Update current inventory from assignment
                #    Capture old count first for BOM deduction calculation
                old_inv_count = None
                atype = device_row["assignment_type"]
                if atype == "component" and device_row["assigned_component_id"]:
                    cur.execute(
                        "SELECT current_count FROM inventory_items "
                        "WHERE item_type = 'component' AND component_id = %(cid)s",
                        {"cid": device_row["assigned_component_id"]},
                    )
                    _r = cur.fetchone()
                    old_inv_count = float(_r["current_count"]) if _r else 0
                elif atype == "assembly" and device_row["assigned_assembly_id"]:
                    cur.execute(
                        "SELECT current_count FROM inventory_items "
                        "WHERE item_type = 'assembly' AND assembly_id = %(aid)s",
                        {"aid": device_row["assigned_assembly_id"]},
                    )
                    _r = cur.fetchone()
                    old_inv_count = float(_r["current_count"]) if _r else 0

                # Upsert inventory item via DB function
                cur.execute(
                    """
                    SELECT upsert_inventory_item_from_counter_event(
                        %(device_id)s::uuid,
                        %(assignment_type)s::text,
                        %(component_id)s::uuid,
                        %(assembly_id)s::uuid,
                        %(count_value)s::numeric
                    );
                    """,
                    {
                        "device_id": device_row["device_id"],
                        "assignment_type": device_row["assignment_type"],
                        "component_id": device_row["assigned_component_id"],
                        "assembly_id": device_row["assigned_assembly_id"],
                        "count_value": count_value,
                    },
                )

                # 3.5) BOM deduction: when an assembly counter increases,
                #       deduct components proportionally
                if (atype == "assembly"
                        and device_row["assigned_assembly_id"]
                        and old_inv_count is not None):
                    delta = count_value - old_inv_count
                    if delta > 0:
                        cur.execute(
                            "SELECT component_id, quantity_required "
                            "FROM assembly_components WHERE assembly_id = %(aid)s",
                            {"aid": device_row["assigned_assembly_id"]},
                        )
                        for bom in cur.fetchall():
                            deduction = delta * float(bom["quantity_required"])
                            cur.execute(
                                """
                                UPDATE inventory_items
                                SET current_count = GREATEST(0, current_count - %(ded)s),
                                    updated_at = now()
                                WHERE item_type = 'component'
                                  AND component_id = %(cid)s
                                """,
                                {"cid": bom["component_id"], "ded": deduction},
                            )

                # 4) Check component low-inventory alerts if component-assigned
                if device_row["assignment_type"] == "component" and device_row["assigned_component_id"]:
                    cur.execute(
                        """
                        SELECT create_low_inventory_alert_if_needed(%(component_id)s);
                        """,
                        {"component_id": device_row["assigned_component_id"]},
                    )

                # 4.5) Check device-level thresholds and manage alerts
                warn_thresh = device_row.get("warning_threshold")
                crit_thresh = device_row.get("critical_threshold")

                if warn_thresh is not None or crit_thresh is not None:
                    alert_level = None
                    if crit_thresh is not None and count_value <= crit_thresh:
                        alert_level = "critical"
                    elif warn_thresh is not None and count_value <= warn_thresh:
                        alert_level = "warning"

                    if alert_level:
                        # Check for existing open alert at this level for this device
                        cur.execute(
                            """
                            SELECT alert_id FROM alerts
                            WHERE device_id = %(device_id)s
                              AND alert_type = 'low_stock'
                              AND alert_level = %(alert_level)s
                              AND status IN ('open', 'acknowledged')
                            LIMIT 1
                            """,
                            {
                                "device_id": device_row["device_id"],
                                "alert_level": alert_level,
                            },
                        )
                        existing = cur.fetchone()

                        if not existing:
                            thresh_val = crit_thresh if alert_level == "critical" else warn_thresh
                            cur.execute(
                                """
                                INSERT INTO alerts (
                                    alert_type, alert_level, device_id,
                                    component_id, assembly_id,
                                    message, details
                                ) VALUES (
                                    'low_stock', %(alert_level)s, %(device_id)s,
                                    %(component_id)s, %(assembly_id)s,
                                    %(message)s, %(details)s::jsonb
                                )
                                """,
                                {
                                    "alert_level": alert_level,
                                    "device_id": device_row["device_id"],
                                    "component_id": device_row["assigned_component_id"],
                                    "assembly_id": device_row["assigned_assembly_id"],
                                    "message": f"{device_name}: count {int(count_value)} at or below {alert_level} threshold ({thresh_val})",
                                    "details": psycopg.types.json.Jsonb({
                                        "count": count_value,
                                        "threshold": thresh_val,
                                        "part_name": part_name,
                                        "part_id": part_id,
                                    }),
                                },
                            )
                            _wh_stock_event = {
                                "level": alert_level, "device": device_name,
                                "part_name": part_name, "part_id": part_id,
                                "count": count_value, "threshold": thresh_val,
                            }

                        # Clear alerts of the other severity level
                        other_level = "warning" if alert_level == "critical" else "critical"
                        cur.execute(
                            """
                            UPDATE alerts SET status = 'cleared', cleared_at = now()
                            WHERE device_id = %(device_id)s
                              AND alert_type = 'low_stock'
                              AND alert_level = %(other_level)s
                              AND status IN ('open', 'acknowledged')
                            """,
                            {
                                "device_id": device_row["device_id"],
                                "other_level": other_level,
                            },
                        )
                    else:
                        # Count is above all thresholds — clear all open alerts for this device
                        cur.execute(
                            """
                            UPDATE alerts SET status = 'cleared', cleared_at = now()
                            WHERE device_id = %(device_id)s
                              AND alert_type = 'low_stock'
                              AND status IN ('open', 'acknowledged')
                            """,
                            {"device_id": device_row["device_id"]},
                        )

                # 4.7) Battery voltage alert management
                BATT_WARN = 3.4
                BATT_CRIT = 3.2
                if battery_voltage is not None:
                    batt_level = None
                    if battery_voltage <= BATT_CRIT:
                        batt_level = "critical"
                    elif battery_voltage <= BATT_WARN:
                        batt_level = "warning"

                    if batt_level:
                        # Check for existing open battery alert at this level
                        cur.execute(
                            """
                            SELECT alert_id FROM alerts
                            WHERE device_id = %(device_id)s
                              AND alert_type = 'low_battery'
                              AND alert_level = %(level)s
                              AND status IN ('open', 'acknowledged')
                            LIMIT 1
                            """,
                            {"device_id": device_row["device_id"], "level": batt_level},
                        )
                        if not cur.fetchone():
                            cur.execute(
                                """
                                INSERT INTO alerts (
                                    alert_type, alert_level, device_id,
                                    component_id, assembly_id,
                                    message, details
                                ) VALUES (
                                    'low_battery', %(level)s, %(device_id)s,
                                    %(component_id)s, %(assembly_id)s,
                                    %(message)s, %(details)s::jsonb
                                )
                                """,
                                {
                                    "level": batt_level,
                                    "device_id": device_row["device_id"],
                                    "component_id": device_row["assigned_component_id"],
                                    "assembly_id": device_row["assigned_assembly_id"],
                                    "message": f"{device_name}: battery {battery_voltage:.2f}V ({batt_level})",
                                    "details": psycopg.types.json.Jsonb({
                                        "battery_voltage": battery_voltage,
                                        "battery_adc_mv": battery_adc_mv,
                                        "battery_adc_raw": battery_adc_raw,
                                        "threshold": BATT_CRIT if batt_level == "critical" else BATT_WARN,
                                    }),
                                },
                            )
                            _wh_batt_event = {
                                "level": batt_level, "device": device_name,
                                "battery_voltage": battery_voltage,
                            }

                        # Clear the other severity level
                        other_batt = "warning" if batt_level == "critical" else "critical"
                        cur.execute(
                            """
                            UPDATE alerts SET status = 'cleared', cleared_at = now()
                            WHERE device_id = %(device_id)s
                              AND alert_type = 'low_battery'
                              AND alert_level = %(other)s
                              AND status IN ('open', 'acknowledged')
                            """,
                            {"device_id": device_row["device_id"], "other": other_batt},
                        )
                    else:
                        # Voltage OK — clear all battery alerts for this device
                        cur.execute(
                            """
                            UPDATE alerts SET status = 'cleared', cleared_at = now()
                            WHERE device_id = %(device_id)s
                              AND alert_type = 'low_battery'
                              AND status IN ('open', 'acknowledged')
                            """,
                            {"device_id": device_row["device_id"]},
                        )

                # 5) Return pending commands for device
                cur.execute(
                    """
                    SELECT
                        command_id,
                        command_type,
                        payload,
                        created_at
                    FROM device_commands
                    WHERE device_name = %(device_name)s
                      AND status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 25;
                    """,
                    {"device_name": device_name},
                )
                commands = cur.fetchall()

                conn.commit()

        # Fire webhooks outside the transaction (non-blocking)
        if _wh_stock_event:
            fire_webhooks('low_stock', _wh_stock_event)
        if _wh_batt_event:
            fire_webhooks('low_battery', _wh_batt_event)

        return jsonify(
            {
                "ok": True,
                "received_at": received_at.isoformat(),
                "event_id": event_row["event_id"],
                "device": {
                    "device_id": str(device_row["device_id"]),
                    "device_name": device_row["device_name"],
                    "assignment_type": device_row["assignment_type"],
                    "assigned_component_id": (
                        str(device_row["assigned_component_id"])
                        if device_row["assigned_component_id"]
                        else None
                    ),
                    "assigned_assembly_id": (
                        str(device_row["assigned_assembly_id"])
                        if device_row["assigned_assembly_id"]
                        else None
                    ),
                },
                "commands": [
                    {
                        "command_id": str(c["command_id"]),
                        "command_type": c["command_type"],
                        "payload": c["payload"],
                        "created_at": c["created_at"].isoformat(),
                    }
                    for c in commands
                ],
            }
        ), 200

    except Exception as e:
        print("ERROR ENCOUNTERED: ",e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/device/commands", methods=["GET"])
def get_device_commands():
    device_name = (request.args.get("device_name") or "").strip()
    if not device_name:
        return jsonify({"ok": False, "error": "device_name is required"}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        command_id,
                        command_type,
                        payload,
                        created_at
                    FROM device_commands
                    WHERE device_name = %(device_name)s
                      AND status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT 25;
                    """,
                    {"device_name": device_name},
                )
                commands = cur.fetchall()

        return jsonify(
            {
                "ok": True,
                "device_name": device_name,
                "commands": [
                    {
                        "command_id": str(c["command_id"]),
                        "command_type": c["command_type"],
                        "payload": c["payload"],
                        "created_at": c["created_at"].isoformat(),
                    }
                    for c in commands
                ],
            }
        ), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/device/commands/ack", methods=["POST"])
def ack_device_command():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"ok": False, "error": "invalid or missing JSON"}), 400

    command_id = payload.get("command_id")
    status = payload.get("status", "acked")
    result_message = payload.get("result_message")

    if not command_id:
        return jsonify({"ok": False, "error": "command_id is required"}), 400

    if status not in ("acked", "failed", "cancelled"):
        return jsonify(
            {"ok": False, "error": "status must be acked, failed, or cancelled"}
        ), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if status == "acked":
                    cur.execute(
                        """
                        UPDATE device_commands
                        SET
                            status = 'acked',
                            acked_at = now(),
                            result_message = %(result_message)s
                        WHERE command_id = %(command_id)s::uuid
                        RETURNING command_id, status;
                        """,
                        {
                            "command_id": command_id,
                            "result_message": result_message,
                        },
                    )
                elif status == "failed":
                    cur.execute(
                        """
                        UPDATE device_commands
                        SET
                            status = 'failed',
                            failed_at = now(),
                            result_message = %(result_message)s
                        WHERE command_id = %(command_id)s::uuid
                        RETURNING command_id, status;
                        """,
                        {
                            "command_id": command_id,
                            "result_message": result_message,
                        },
                    )
                else:
                    cur.execute(
                        """
                        UPDATE device_commands
                        SET
                            status = 'cancelled',
                            result_message = %(result_message)s
                        WHERE command_id = %(command_id)s::uuid
                        RETURNING command_id, status;
                        """,
                        {
                            "command_id": command_id,
                            "result_message": result_message,
                        },
                    )

                row = cur.fetchone()
                conn.commit()

                if not row:
                    return jsonify({"ok": False, "error": "command not found"}), 404

        return jsonify(
            {
                "ok": True,
                "command_id": str(row["command_id"]),
                "status": row["status"],
            }
        ), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/devices')
def api_devices():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''
                SELECT
                    d.device_name,
                    d.assignment_type,
                    d.last_seen_at,
                    ce.count_value AS latest_count,
                    d.reported_part_id,
                    d.reported_part_name,
                    d.warning_threshold,
                    d.critical_threshold,
                    d.battery_voltage
                FROM devices d
                LEFT JOIN LATERAL (
                    SELECT ce2.count_value
                    FROM counter_events ce2
                    WHERE ce2.device_name = d.device_name
                    ORDER BY ce2.received_at DESC
                    LIMIT 1
                ) ce ON TRUE
                WHERE d.is_active = TRUE
                ORDER BY d.device_name ASC
                '''
            )
            rows = cur.fetchall()

    devices = []
    for r in rows:
        devices.append({
            'device_name': r['device_name'],
            'assignment_type': r['assignment_type'],
            'last_seen_at': r['last_seen_at'].isoformat() if r['last_seen_at'] else None,
            'latest_count': float(r['latest_count']) if r['latest_count'] is not None else None,
            'reported_part_id': r['reported_part_id'],
            'reported_part_name': r['reported_part_name'],
            'warning_threshold': r['warning_threshold'],
            'critical_threshold': r['critical_threshold'],
            'battery_voltage': float(r['battery_voltage']) if r['battery_voltage'] is not None else None,
        })
    return jsonify({'ok': True, 'devices': devices})


@app.route('/api/commands/pending')
def api_commands_pending():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''
                SELECT
                    dc.command_id,
                    dc.device_name,
                    dc.command_type,
                    dc.payload,
                    dc.status,
                    dc.created_at
                FROM device_commands dc
                WHERE dc.status = 'pending'
                ORDER BY dc.created_at DESC
                LIMIT 200
                '''
            )
            rows = cur.fetchall()

    commands = []
    for r in rows:
        commands.append({
            'command_id': str(r['command_id']),
            'device_name': r['device_name'],
            'command_type': r['command_type'],
            'payload': r['payload'],
            'status': r['status'],
            'created_at': r['created_at'].isoformat() if r['created_at'] else None,
        })
    return jsonify({'ok': True, 'commands': commands})


@app.route('/api/inventory')
def api_inventory():
    search = (request.args.get('search') or '').strip()

    with get_db() as conn:
        with conn.cursor() as cur:
            query = '''
                SELECT
                    d.device_name,
                    d.reported_part_id,
                    d.reported_part_name,
                    d.assignment_type,
                    d.assigned_component_id,
                    d.assigned_assembly_id,
                    d.last_seen_at,
                    d.warning_threshold,
                    d.critical_threshold,
                    d.battery_voltage,
                    ce.count_value AS latest_count,
                    comp.part_id   AS assigned_part_id,
                    comp.part_name AS assigned_part_name,
                    asm.assembly_code  AS assigned_asm_code,
                    asm.assembly_name  AS assigned_asm_name
                FROM devices d
                LEFT JOIN LATERAL (
                    SELECT ce2.count_value
                    FROM counter_events ce2
                    WHERE ce2.device_name = d.device_name
                    ORDER BY ce2.received_at DESC
                    LIMIT 1
                ) ce ON TRUE
                LEFT JOIN components comp ON comp.component_id = d.assigned_component_id
                LEFT JOIN assemblies  asm  ON asm.assembly_id  = d.assigned_assembly_id
                WHERE d.is_active = TRUE
            '''
            params = {}
            if search:
                query += '''
                    AND (
                        d.device_name ILIKE %(search)s
                        OR d.reported_part_name ILIKE %(search)s
                        OR d.reported_part_id ILIKE %(search)s
                    )
                '''
                params['search'] = f'%{search}%'
            query += ' ORDER BY d.reported_part_name ASC, d.device_name ASC'
            cur.execute(query, params)
            rows = cur.fetchall()

    items = []
    for r in rows:
        # Prefer reported values, fall back to DB assignment info
        part_id = r['reported_part_id']
        part_name = r['reported_part_name']
        if not part_name:
            if r['assignment_type'] == 'component' and r['assigned_part_name']:
                part_id = r['assigned_part_id']
                part_name = r['assigned_part_name']
            elif r['assignment_type'] == 'assembly' and r['assigned_asm_name']:
                part_id = r['assigned_asm_code']
                part_name = r['assigned_asm_name']
        items.append({
            'device_name': r['device_name'],
            'part_id': part_id,
            'part_name': part_name,
            'assignment_type': r['assignment_type'],
            'last_seen_at': r['last_seen_at'].isoformat() if r['last_seen_at'] else None,
            'count': float(r['latest_count']) if r['latest_count'] is not None else None,
            'warning_threshold': r['warning_threshold'],
            'critical_threshold': r['critical_threshold'],
            'battery_voltage': float(r['battery_voltage']) if r['battery_voltage'] is not None else None,
        })
    return jsonify({'ok': True, 'items': items})


@app.route("/api/device/thresholds", methods=["POST"])
def set_device_thresholds():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"ok": False, "error": "invalid or missing JSON"}), 400

    device_name = (payload.get("device_name") or "").strip()
    if not device_name:
        return jsonify({"ok": False, "error": "device_name is required"}), 400

    warning_threshold = payload.get("warning_threshold")
    critical_threshold = payload.get("critical_threshold")

    # Validate: if both set, warning must be >= critical
    if warning_threshold is not None and critical_threshold is not None:
        if int(critical_threshold) > int(warning_threshold):
            return jsonify({"ok": False, "error": "critical_threshold must be <= warning_threshold"}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE devices
                    SET warning_threshold = %(warning_threshold)s,
                        critical_threshold = %(critical_threshold)s
                    WHERE device_name = %(device_name)s
                    RETURNING device_name, warning_threshold, critical_threshold
                    """,
                    {
                        "device_name": device_name,
                        "warning_threshold": int(warning_threshold) if warning_threshold is not None else None,
                        "critical_threshold": int(critical_threshold) if critical_threshold is not None else None,
                    },
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "device not found"}), 404
                conn.commit()

        return jsonify({
            "ok": True,
            "device_name": row["device_name"],
            "warning_threshold": row["warning_threshold"],
            "critical_threshold": row["critical_threshold"],
        }), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/alerts")
def api_alerts():
    status_filter = request.args.get("status", "open,acknowledged")
    statuses = [s.strip() for s in status_filter.split(",") if s.strip()]

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        a.alert_id,
                        a.alert_type,
                        a.alert_level,
                        a.status,
                        a.device_id,
                        d.device_name,
                        a.component_id,
                        a.assembly_id,
                        a.message,
                        a.details,
                        a.created_at,
                        a.acknowledged_at,
                        a.cleared_at
                    FROM alerts a
                    LEFT JOIN devices d ON d.device_id = a.device_id
                    WHERE a.status = ANY(%(statuses)s)
                    ORDER BY
                        CASE a.alert_level WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                        a.created_at DESC
                    LIMIT 200
                    """,
                    {"statuses": statuses},
                )
                rows = cur.fetchall()

        alerts = []
        for r in rows:
            alerts.append({
                "alert_id": str(r["alert_id"]),
                "alert_type": r["alert_type"],
                "alert_level": r["alert_level"],
                "status": r["status"],
                "device_name": r["device_name"],
                "message": r["message"],
                "details": r["details"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "acknowledged_at": r["acknowledged_at"].isoformat() if r["acknowledged_at"] else None,
            })
        return jsonify({"ok": True, "alerts": alerts})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/alerts/acknowledge", methods=["POST"])
def acknowledge_alert():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"ok": False, "error": "invalid or missing JSON"}), 400

    alert_id = payload.get("alert_id")
    if not alert_id:
        return jsonify({"ok": False, "error": "alert_id is required"}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE alerts
                    SET status = 'acknowledged', acknowledged_at = now()
                    WHERE alert_id = %(alert_id)s::uuid
                      AND status = 'open'
                    RETURNING alert_id, status
                    """,
                    {"alert_id": alert_id},
                )
                row = cur.fetchone()
                conn.commit()

        if not row:
            return jsonify({"ok": False, "error": "alert not found or not open"}), 404
        return jsonify({"ok": True, "alert_id": str(row["alert_id"]), "status": row["status"]})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/alerts/clear", methods=["POST"])
def clear_alert():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"ok": False, "error": "invalid or missing JSON"}), 400

    alert_id = payload.get("alert_id")
    if not alert_id:
        return jsonify({"ok": False, "error": "alert_id is required"}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE alerts
                    SET status = 'cleared', cleared_at = now()
                    WHERE alert_id = %(alert_id)s::uuid
                      AND status IN ('open', 'acknowledged')
                    RETURNING alert_id, status
                    """,
                    {"alert_id": alert_id},
                )
                row = cur.fetchone()
                conn.commit()

        if not row:
            return jsonify({"ok": False, "error": "alert not found or already cleared"}), 404
        return jsonify({"ok": True, "alert_id": str(row["alert_id"]), "status": row["status"]})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/device/commands/enqueue", methods=["POST"])
def enqueue_device_command():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"ok": False, "error": "invalid or missing JSON"}), 400

    device_name = (payload.get("device_name") or "").strip()
    command_type = (payload.get("command_type") or "").strip()
    command_payload = payload.get("payload", {})

    if not device_name:
        return jsonify({"ok": False, "error": "device_name is required"}), 400

    if not command_type:
        return jsonify({"ok": False, "error": "command_type is required"}), 400

    allowed = {
        "set_devname",
        "set_name",
        "set_id",
        "set_count",
        "set_timeout",
        "set_wifi",
        "set_api_url",
        "display_on",
        "display_off",
        "reset_count",
        "set_post_interval",
    }

    if command_type not in allowed:
        return jsonify({"ok": False, "error": "unsupported command_type"}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT device_id
                    FROM devices
                    WHERE device_name = %(device_name)s
                    """,
                    {"device_name": device_name},
                )
                dev = cur.fetchone()
                if not dev:
                    return jsonify({"ok": False, "error": "device not found"}), 404

                cur.execute(
                    """
                    INSERT INTO device_commands (
                        device_id,
                        device_name,
                        command_type,
                        payload,
                        status
                    )
                    VALUES (
                        %(device_id)s,
                        %(device_name)s,
                        %(command_type)s,
                        %(payload)s::jsonb,
                        'pending'
                    )
                    RETURNING command_id, created_at
                    """,
                    {
                        "device_id": dev["device_id"],
                        "device_name": device_name,
                        "command_type": command_type,
                        "payload": psycopg.types.json.Jsonb(command_payload),
                    },
                )
                row = cur.fetchone()
                conn.commit()

        return jsonify(
            {
                "ok": True,
                "command_id": str(row["command_id"]),
                "device_name": device_name,
                "command_type": command_type,
                "created_at": row["created_at"].isoformat(),
            }
        ), 201

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/inventory/adjust', methods=['POST'])
def api_inventory_adjust():
    """Manually adjust stock for a component or assembly.

    For assemblies with positive delta (building new assemblies):
      - Increase assembly inventory count
      - Deduct BOM components proportionally
    For assemblies with negative delta:
      - Decrease assembly count only (do NOT restore components)
    For components:
      - Directly adjust count (positive = new shipment, negative = usage/scrap)
    """
    data = request.get_json(silent=True) or {}
    item_type = (data.get('item_type') or '').strip()
    item_id = (data.get('item_id') or '').strip()
    delta = data.get('delta')

    if item_type not in ('component', 'assembly'):
        return jsonify({'ok': False, 'error': 'item_type must be component or assembly'}), 400
    if not item_id:
        return jsonify({'ok': False, 'error': 'item_id is required'}), 400
    if delta is None:
        return jsonify({'ok': False, 'error': 'delta is required'}), 400

    try:
        delta = float(delta)
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'delta must be numeric'}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                new_count = None
                deductions = []

                if item_type == 'component':
                    cur.execute("""
                        INSERT INTO inventory_items (item_type, component_id, current_count)
                        VALUES ('component', %(cid)s::uuid, GREATEST(0, %(delta)s))
                        ON CONFLICT (component_id) WHERE component_id IS NOT NULL
                        DO UPDATE SET
                            current_count = GREATEST(0, inventory_items.current_count + %(delta)s),
                            updated_at = now()
                        RETURNING current_count
                    """, {'cid': item_id, 'delta': delta})
                    row = cur.fetchone()
                    new_count = float(row['current_count']) if row else 0

                elif item_type == 'assembly':
                    if delta > 0:
                        # Building assemblies: increase count and deduct BOM components
                        cur.execute("""
                            INSERT INTO inventory_items (item_type, assembly_id, current_count)
                            VALUES ('assembly', %(aid)s::uuid, %(delta)s)
                            ON CONFLICT (assembly_id) WHERE assembly_id IS NOT NULL
                            DO UPDATE SET
                                current_count = inventory_items.current_count + %(delta)s,
                                updated_at = now()
                            RETURNING current_count
                        """, {'aid': item_id, 'delta': delta})
                        row = cur.fetchone()
                        new_count = float(row['current_count']) if row else 0

                        # Deduct BOM components proportionally
                        cur.execute("""
                            SELECT ac.component_id, ac.quantity_required,
                                   c.part_id, c.part_name
                            FROM assembly_components ac
                            JOIN components c ON c.component_id = ac.component_id
                            WHERE ac.assembly_id = %(aid)s::uuid
                        """, {'aid': item_id})
                        bom_entries = cur.fetchall()
                        for entry in bom_entries:
                            ded = delta * float(entry['quantity_required'])
                            cur.execute("""
                                UPDATE inventory_items
                                SET current_count = GREATEST(0, current_count - %(ded)s),
                                    updated_at = now()
                                WHERE item_type = 'component'
                                  AND component_id = %(cid)s
                                RETURNING current_count
                            """, {'cid': entry['component_id'], 'ded': ded})
                            ded_row = cur.fetchone()
                            deductions.append({
                                'part_id': entry['part_id'],
                                'part_name': entry['part_name'],
                                'deducted': ded,
                                'remaining': float(ded_row['current_count']) if ded_row else 0,
                            })
                    else:
                        # Decreasing assembly count — do NOT restore components
                        cur.execute("""
                            UPDATE inventory_items
                            SET current_count = GREATEST(0, current_count + %(delta)s),
                                updated_at = now()
                            WHERE item_type = 'assembly'
                              AND assembly_id = %(aid)s::uuid
                            RETURNING current_count
                        """, {'aid': item_id, 'delta': delta})
                        row = cur.fetchone()
                        new_count = float(row['current_count']) if row else 0

                # Enqueue set_count to any counter device assigned to this item
                commands_enqueued = []
                if new_count is not None:
                    if item_type == 'component':
                        cur.execute("""
                            SELECT device_id, device_name
                            FROM devices
                            WHERE assignment_type = 'component'
                              AND assigned_component_id = %(cid)s::uuid
                              AND is_active = TRUE
                        """, {'cid': item_id})
                    else:
                        cur.execute("""
                            SELECT device_id, device_name
                            FROM devices
                            WHERE assignment_type = 'assembly'
                              AND assigned_assembly_id = %(aid)s::uuid
                              AND is_active = TRUE
                        """, {'aid': item_id})
                    assigned_devices = cur.fetchall()
                    for adev in assigned_devices:
                        cur.execute("""
                            INSERT INTO device_commands
                                (device_id, device_name, command_type, payload, status)
                            VALUES (%(did)s, %(dn)s, 'set_count', %(pl)s::jsonb, 'pending')
                            RETURNING command_id
                        """, {
                            'did': adev['device_id'],
                            'dn': adev['device_name'],
                            'pl': psycopg.types.json.Jsonb({'count': int(new_count)}),
                        })
                        commands_enqueued.append(adev['device_name'])

                # Also enqueue set_count for BOM component deductions
                for ded in deductions:
                    cur.execute("""
                        SELECT d.device_id, d.device_name
                        FROM devices d
                        JOIN components c ON c.component_id = d.assigned_component_id
                        WHERE d.assignment_type = 'component'
                          AND c.part_id = %(pid)s
                          AND d.is_active = TRUE
                    """, {'pid': ded['part_id']})
                    comp_devs = cur.fetchall()
                    for cdev in comp_devs:
                        cur.execute("""
                            INSERT INTO device_commands
                                (device_id, device_name, command_type, payload, status)
                            VALUES (%(did)s, %(dn)s, 'set_count', %(pl)s::jsonb, 'pending')
                            RETURNING command_id
                        """, {
                            'did': cdev['device_id'],
                            'dn': cdev['device_name'],
                            'pl': psycopg.types.json.Jsonb({'count': int(ded['remaining'])}),
                        })
                        commands_enqueued.append(cdev['device_name'])

                conn.commit()

        return jsonify({
            'ok': True,
            'item_type': item_type,
            'item_id': item_id,
            'delta': delta,
            'new_count': new_count,
            'deductions': deductions,
            'commands_enqueued': commands_enqueued,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/timeseries')
def api_timeseries():
    device_names = request.args.getlist('device_name')
    max_points = int(request.args.get('max_points', '600'))
    max_points = max(50, min(max_points, 5000))

    end_raw = request.args.get('end')
    start_raw = request.args.get('start')

    end_dt = datetime.fromisoformat(end_raw.replace('Z', '+00:00')) if end_raw else datetime.now(timezone.utc)
    start_dt = datetime.fromisoformat(start_raw.replace('Z', '+00:00')) if start_raw else (end_dt.replace(microsecond=0) - (end_dt - end_dt.replace(microsecond=0)))
    if not start_raw:
        from datetime import timedelta
        start_dt = end_dt - timedelta(hours=1)

    if start_dt >= end_dt:
        return jsonify({'ok': False, 'error': 'start must be before end'}), 400

    total_seconds = (end_dt - start_dt).total_seconds()
    bucket_seconds = choose_bucket_seconds(total_seconds, max_points)

    with get_db() as conn:
        with conn.cursor() as cur:
            params = {
                'start_dt': start_dt,
                'end_dt': end_dt,
                'bucket_seconds': bucket_seconds,
            }

            where_device = ''
            if device_names:
                where_device = 'AND ce.device_name = ANY(%(device_names)s)'
                params['device_names'] = device_names

            cur.execute(
                f'''
                WITH bucketed AS (
                    SELECT
                        ce.device_name,
                        to_timestamp(
                            floor(extract(epoch FROM ce.received_at) / %(bucket_seconds)s) * %(bucket_seconds)s
                        ) AT TIME ZONE 'UTC' AS bucket_ts,
                        avg(ce.count_value)::numeric AS avg_count
                    FROM counter_events ce
                    WHERE ce.received_at >= %(start_dt)s
                      AND ce.received_at <= %(end_dt)s
                      {where_device}
                    GROUP BY ce.device_name, bucket_ts
                )
                SELECT
                    device_name,
                    bucket_ts,
                    avg_count
                FROM bucketed
                ORDER BY device_name, bucket_ts
                ''' ,
                params,
            )
            rows = cur.fetchall()

    series_map = {}
    for r in rows:
        name = r['device_name']
        series_map.setdefault(name, []).append({
            't': r['bucket_ts'].replace(tzinfo=timezone.utc).isoformat(),
            'v': float(r['avg_count']) if isinstance(r['avg_count'], Decimal) else float(r['avg_count']),
        })

    from datetime import timedelta
    range_text = f"{start_dt.isoformat()} to {end_dt.isoformat()} · bucket {bucket_seconds}s"
    return jsonify({
        'ok': True,
        'range_text': range_text,
        'series': [
            {'device_name': name, 'points': points}
            for name, points in sorted(series_map.items(), key=lambda x: x[0])
        ],
    })

@app.route("/api/devices/<device_name>/latest-count", methods=["GET"])
def api_device_latest_count(device_name):
    device_name = device_name.strip()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ce.received_at,
                        ce.count_value
                    FROM counter_events ce
                    WHERE ce.device_name = %(device_name)s
                    ORDER BY ce.received_at DESC
                    LIMIT 1
                    """,
                    {"device_name": device_name},
                )
                row = cur.fetchone()

        if not row:
            return jsonify({"ok": False, "error": "no readings found"}), 404

        return jsonify(
            {
                "ok": True,
                "device_name": device_name,
                "received_at": row["received_at"].isoformat(),
                "count": float(row["count_value"]),
            }
        ), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/bom')
def bom_page():
    return render_template('bom.html')


# ═══════════════════  COMPONENTS CRUD  ═══════════════════════════
@app.route('/api/components', methods=['GET'])
def api_list_components():
    search = (request.args.get('search') or '').strip()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                q = """
                    SELECT c.component_id, c.part_id, c.part_name, c.description,
                           c.min_threshold, c.supplier_url, c.lead_time_days, c.min_order_qty,
                           c.is_active, c.created_at, c.updated_at,
                           COALESCE(ii.current_count, 0) AS current_count,
                           COALESCE(ii.reserved_count, 0) AS reserved_count
                    FROM components c
                    LEFT JOIN inventory_items ii
                      ON ii.component_id = c.component_id AND ii.item_type = 'component'
                    WHERE c.is_active = TRUE
                """
                params = {}
                if search:
                    q += " AND (c.part_id ILIKE %(s)s OR c.part_name ILIKE %(s)s OR c.description ILIKE %(s)s)"
                    params['s'] = f'%{search}%'
                q += " ORDER BY c.part_name ASC"
                cur.execute(q, params)
                rows = cur.fetchall()
        return jsonify({'ok': True, 'components': [
            {**r, 'component_id': str(r['component_id']),
             'current_count': float(r['current_count']),
             'reserved_count': float(r['reserved_count']),
             'created_at': r['created_at'].isoformat(),
             'updated_at': r['updated_at'].isoformat()}
            for r in rows
        ]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/components', methods=['POST'])
def api_create_component():
    data = request.get_json(silent=True) or {}
    part_id = (data.get('part_id') or '').strip()
    part_name = (data.get('part_name') or '').strip()
    if not part_id or not part_name:
        return jsonify({'ok': False, 'error': 'part_id and part_name are required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO components (part_id, part_name, description, min_threshold,
                                           supplier_url, lead_time_days, min_order_qty)
                    VALUES (%(part_id)s, %(part_name)s, %(description)s, %(min_threshold)s,
                            %(supplier_url)s, %(lead_time_days)s, %(min_order_qty)s)
                    RETURNING component_id, part_id, part_name, description, min_threshold,
                              supplier_url, lead_time_days, min_order_qty
                """, {
                    'part_id': part_id,
                    'part_name': part_name,
                    'description': (data.get('description') or '').strip() or None,
                    'min_threshold': int(data.get('min_threshold', 0)),
                    'supplier_url': (data.get('supplier_url') or '').strip() or None,
                    'lead_time_days': int(data.get('lead_time_days', 7)),
                    'min_order_qty': int(data.get('min_order_qty', 1)),
                })
                row = cur.fetchone()
                conn.commit()
        return jsonify({'ok': True, 'component': {**row, 'component_id': str(row['component_id'])}}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/components/<component_id>', methods=['PUT'])
def api_update_component(component_id):
    data = request.get_json(silent=True) or {}
    sets, params = [], {'cid': component_id}
    for col, key in [('part_name', 'part_name'), ('description', 'description'),
                     ('min_threshold', 'min_threshold'), ('supplier_url', 'supplier_url'),
                     ('lead_time_days', 'lead_time_days'), ('min_order_qty', 'min_order_qty')]:
        if key in data:
            sets.append(f"{col} = %({key})s")
            params[key] = data[key]
    if not sets:
        return jsonify({'ok': False, 'error': 'nothing to update'}), 400
    try:
        cmds_enqueued = 0
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE components SET {', '.join(sets)} WHERE component_id = %(cid)s::uuid "
                    "RETURNING component_id, part_id, part_name, description, min_threshold, supplier_url, lead_time_days, min_order_qty",
                    params,
                )
                row = cur.fetchone()
                if row and 'min_threshold' in data:
                    # Push set_thresholds command to assigned devices
                    cur.execute("""
                        SELECT device_id, device_name FROM devices
                        WHERE assignment_type = 'component'
                          AND assigned_component_id = %(cid)s::uuid AND is_active = TRUE
                    """, {'cid': component_id})
                    devs = cur.fetchall()
                    thr = row['min_threshold'] or 0
                    # Warn = min_threshold, critical = half of warn (floor), or 0 if warn <= 1
                    crit = max(0, thr // 2) if thr > 1 else 0
                    thr_pl = json_lib.dumps({'warn_threshold': thr, 'critical_threshold': crit})
                    for dev in devs:
                        cur.execute("""
                            INSERT INTO device_commands
                                (device_id, device_name, command_type, payload, status)
                            VALUES (%(did)s, %(dn)s, 'set_thresholds', %(pl)s::jsonb, 'pending')
                        """, {'did': dev['device_id'], 'dn': dev['device_name'], 'pl': thr_pl})
                        cmds_enqueued += 1
                conn.commit()
        if not row:
            return jsonify({'ok': False, 'error': 'component not found'}), 404
        return jsonify({'ok': True, 'component': {**row, 'component_id': str(row['component_id'])},
                        'threshold_cmds_enqueued': cmds_enqueued})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/components/<component_id>', methods=['DELETE'])
def api_delete_component(component_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE components SET is_active = FALSE WHERE component_id = %(cid)s::uuid RETURNING component_id",
                    {'cid': component_id},
                )
                row = cur.fetchone()
                conn.commit()
        if not row:
            return jsonify({'ok': False, 'error': 'component not found'}), 404
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════  INVENTORY EXPORT  ═══════════════════════════
@app.route('/api/inventory/export', methods=['GET'])
def api_inventory_export():
    """Export a CSV report of low-stock / out-of-stock items with order suggestions."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Components
                cur.execute("""
                    SELECT c.part_id, c.part_name, c.description, c.min_threshold,
                           c.supplier_url, COALESCE(ii.current_count, 0) AS current_count
                    FROM components c
                    LEFT JOIN inventory_items ii
                      ON ii.component_id = c.component_id AND ii.item_type = 'component'
                    WHERE c.is_active = TRUE
                    ORDER BY c.part_name ASC
                """)
                comps = cur.fetchall()

                # Assemblies
                cur.execute("""
                    SELECT a.assembly_code, a.assembly_name, a.description, a.min_threshold,
                           COALESCE(ii.current_count, 0) AS current_count
                    FROM assemblies a
                    LEFT JOIN inventory_items ii
                      ON ii.assembly_id = a.assembly_id AND ii.item_type = 'assembly'
                    WHERE a.is_active = TRUE
                    ORDER BY a.assembly_name ASC
                """)
                asms = cur.fetchall()

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['Type', 'Part ID / Code', 'Name', 'Description',
                     'Current Stock', 'Min Threshold', 'Deficit',
                     'Suggested Order Qty', 'Supplier URL', 'Status'])

        for c in comps:
            stock = int(c['current_count'])
            thresh = int(c['min_threshold'] or 0)
            deficit = max(0, thresh - stock)
            # Suggest ordering at least up to 2× threshold or at least the deficit
            suggested = max(deficit, thresh) if deficit > 0 else 0
            status = 'OUT OF STOCK' if stock == 0 else ('LOW STOCK' if deficit > 0 else 'OK')
            if stock == 0 and thresh > 0:
                suggested = thresh * 2
            w.writerow(['Component', c['part_id'], c['part_name'],
                        c['description'] or '', stock, thresh, deficit,
                        suggested, c['supplier_url'] or '', status])

        for a in asms:
            stock = int(a['current_count'])
            thresh = int(a['min_threshold'] or 0)
            deficit = max(0, thresh - stock)
            suggested = max(deficit, thresh) if deficit > 0 else 0
            status = 'OUT OF STOCK' if stock == 0 else ('LOW STOCK' if deficit > 0 else 'OK')
            if stock == 0 and thresh > 0:
                suggested = thresh * 2
            w.writerow(['Assembly', a['assembly_code'], a['assembly_name'],
                        a['description'] or '', stock, thresh, deficit,
                        suggested, '', status])

        output = buf.getvalue()
        return Response(
            output,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=stocknet_inventory_report.csv'}
        )
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════  ASSEMBLIES CRUD  ════════════════════════════
@app.route('/api/assemblies', methods=['GET'])
def api_list_assemblies():
    search = (request.args.get('search') or '').strip()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                q = """
                    SELECT a.assembly_id, a.assembly_code, a.assembly_name,
                           a.description, a.min_threshold, a.is_active,
                           COALESCE(ii.current_count, 0) AS current_count,
                           COALESCE(ii.reserved_count, 0) AS reserved_count
                    FROM assemblies a
                    LEFT JOIN inventory_items ii
                      ON ii.assembly_id = a.assembly_id AND ii.item_type = 'assembly'
                    WHERE a.is_active = TRUE
                """
                params = {}
                if search:
                    q += " AND (a.assembly_code ILIKE %(s)s OR a.assembly_name ILIKE %(s)s)"
                    params['s'] = f'%{search}%'
                q += " ORDER BY a.assembly_name ASC"
                cur.execute(q, params)
                assemblies = cur.fetchall()

                # Fetch BOM for all active assemblies (include component stock)
                cur.execute("""
                    SELECT ac.assembly_id, ac.assembly_component_id,
                           ac.component_id, ac.quantity_required,
                           c.part_id, c.part_name,
                           COALESCE(ii.current_count, 0) AS comp_stock
                    FROM assembly_components ac
                    JOIN components c ON c.component_id = ac.component_id
                    JOIN assemblies a ON a.assembly_id = ac.assembly_id
                    LEFT JOIN inventory_items ii
                      ON ii.component_id = ac.component_id AND ii.item_type = 'component'
                    WHERE a.is_active = TRUE AND c.is_active = TRUE
                    ORDER BY c.part_name ASC
                """)
                bom_rows = cur.fetchall()

        bom_map = {}
        for b in bom_rows:
            aid = str(b['assembly_id'])
            bom_map.setdefault(aid, []).append({
                'assembly_component_id': str(b['assembly_component_id']),
                'component_id': str(b['component_id']),
                'part_id': b['part_id'],
                'part_name': b['part_name'],
                'quantity_required': float(b['quantity_required']),
                'comp_stock': float(b['comp_stock']),
            })

        result = []
        for a in assemblies:
            aid = str(a['assembly_id'])
            result.append({
                'assembly_id': aid,
                'assembly_code': a['assembly_code'],
                'assembly_name': a['assembly_name'],
                'description': a['description'],
                'min_threshold': a['min_threshold'],
                'current_count': float(a['current_count']),
                'reserved_count': float(a['reserved_count']),
                'components': bom_map.get(aid, []),
            })
        return jsonify({'ok': True, 'assemblies': result})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/assemblies', methods=['POST'])
def api_create_assembly():
    data = request.get_json(silent=True) or {}
    code = (data.get('assembly_code') or '').strip()
    name = (data.get('assembly_name') or '').strip()
    if not code or not name:
        return jsonify({'ok': False, 'error': 'assembly_code and assembly_name are required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO assemblies (assembly_code, assembly_name, description, min_threshold)
                    VALUES (%(code)s, %(name)s, %(desc)s, %(thresh)s)
                    RETURNING assembly_id, assembly_code, assembly_name, description, min_threshold
                """, {
                    'code': code, 'name': name,
                    'desc': (data.get('description') or '').strip() or None,
                    'thresh': int(data.get('min_threshold', 0)),
                })
                row = cur.fetchone()
                conn.commit()
        return jsonify({'ok': True, 'assembly': {**row, 'assembly_id': str(row['assembly_id']), 'components': []}}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/assemblies/<assembly_id>', methods=['PUT'])
def api_update_assembly(assembly_id):
    data = request.get_json(silent=True) or {}
    sets, params = [], {'aid': assembly_id}
    for col, key in [('assembly_name', 'assembly_name'), ('description', 'description'),
                     ('min_threshold', 'min_threshold')]:
        if key in data:
            sets.append(f"{col} = %({key})s")
            params[key] = data[key]
    if not sets:
        return jsonify({'ok': False, 'error': 'nothing to update'}), 400
    try:
        cmds_enqueued = 0
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE assemblies SET {', '.join(sets)} WHERE assembly_id = %(aid)s::uuid "
                    "RETURNING assembly_id, assembly_code, assembly_name, description, min_threshold",
                    params,
                )
                row = cur.fetchone()
                if row and 'min_threshold' in data:
                    cur.execute("""
                        SELECT device_id, device_name FROM devices
                        WHERE assignment_type = 'assembly'
                          AND assigned_assembly_id = %(aid)s::uuid AND is_active = TRUE
                    """, {'aid': assembly_id})
                    devs = cur.fetchall()
                    thr  = row['min_threshold'] or 0
                    crit = max(0, thr // 2) if thr > 1 else 0
                    thr_pl = json_lib.dumps({'warn_threshold': thr, 'critical_threshold': crit})
                    for dev in devs:
                        cur.execute("""
                            INSERT INTO device_commands
                                (device_id, device_name, command_type, payload, status)
                            VALUES (%(did)s, %(dn)s, 'set_thresholds', %(pl)s::jsonb, 'pending')
                        """, {'did': dev['device_id'], 'dn': dev['device_name'], 'pl': thr_pl})
                        cmds_enqueued += 1
                conn.commit()
        if not row:
            return jsonify({'ok': False, 'error': 'assembly not found'}), 404
        return jsonify({'ok': True, 'assembly': {**row, 'assembly_id': str(row['assembly_id'])},
                        'threshold_cmds_enqueued': cmds_enqueued})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/assemblies/<assembly_id>', methods=['DELETE'])
def api_delete_assembly(assembly_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE assemblies SET is_active = FALSE WHERE assembly_id = %(aid)s::uuid RETURNING assembly_id",
                    {'aid': assembly_id},
                )
                row = cur.fetchone()
                conn.commit()
        if not row:
            return jsonify({'ok': False, 'error': 'assembly not found'}), 404
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════  BOM (assembly ↔ component) ═════════════════════
@app.route('/api/assemblies/<assembly_id>/components', methods=['POST'])
def api_add_bom_entry(assembly_id):
    data = request.get_json(silent=True) or {}
    component_id = (data.get('component_id') or '').strip()
    qty = data.get('quantity_required')
    if not component_id or qty is None:
        return jsonify({'ok': False, 'error': 'component_id and quantity_required are required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO assembly_components (assembly_id, component_id, quantity_required)
                    VALUES (%(aid)s::uuid, %(cid)s::uuid, %(qty)s)
                    ON CONFLICT (assembly_id, component_id)
                    DO UPDATE SET quantity_required = EXCLUDED.quantity_required
                    RETURNING assembly_component_id, assembly_id, component_id, quantity_required
                """, {'aid': assembly_id, 'cid': component_id, 'qty': float(qty)})
                row = cur.fetchone()
                conn.commit()
        return jsonify({'ok': True, 'entry': {
            'assembly_component_id': str(row['assembly_component_id']),
            'assembly_id': str(row['assembly_id']),
            'component_id': str(row['component_id']),
            'quantity_required': float(row['quantity_required']),
        }}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/assemblies/<assembly_id>/components/<component_id>', methods=['PUT'])
def api_update_bom_entry(assembly_id, component_id):
    data = request.get_json(silent=True) or {}
    qty = data.get('quantity_required')
    if qty is None:
        return jsonify({'ok': False, 'error': 'quantity_required is required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE assembly_components
                    SET quantity_required = %(qty)s
                    WHERE assembly_id = %(aid)s::uuid AND component_id = %(cid)s::uuid
                    RETURNING assembly_component_id
                """, {'aid': assembly_id, 'cid': component_id, 'qty': float(qty)})
                row = cur.fetchone()
                conn.commit()
        if not row:
            return jsonify({'ok': False, 'error': 'BOM entry not found'}), 404
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/assemblies/<assembly_id>/components/<component_id>', methods=['DELETE'])
def api_remove_bom_entry(assembly_id, component_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM assembly_components
                    WHERE assembly_id = %(aid)s::uuid AND component_id = %(cid)s::uuid
                    RETURNING assembly_component_id
                """, {'aid': assembly_id, 'cid': component_id})
                row = cur.fetchone()
                conn.commit()
        if not row:
            return jsonify({'ok': False, 'error': 'BOM entry not found'}), 404
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════  DEVICE ASSIGNMENT  ═════════════════════════════
@app.route('/api/devices/assign', methods=['POST'])
def api_assign_device():
    data = request.get_json(silent=True) or {}
    device_name = (data.get('device_name') or '').strip()
    assignment_type = (data.get('assignment_type') or '').strip()
    if not device_name:
        return jsonify({'ok': False, 'error': 'device_name is required'}), 400
    if assignment_type not in ('component', 'assembly'):
        return jsonify({'ok': False, 'error': 'assignment_type must be component or assembly'}), 400

    component_id = data.get('component_id')
    assembly_id = data.get('assembly_id')

    if assignment_type == 'component' and not component_id:
        return jsonify({'ok': False, 'error': 'component_id required for component assignment'}), 400
    if assignment_type == 'assembly' and not assembly_id:
        return jsonify({'ok': False, 'error': 'assembly_id required for assembly assignment'}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE devices SET
                        assignment_type = %(at)s,
                        assigned_component_id = %(cid)s::uuid,
                        assigned_assembly_id = %(aid)s::uuid
                    WHERE device_name = %(dn)s
                    RETURNING device_id, device_name, assignment_type
                """, {
                    'dn': device_name,
                    'at': assignment_type,
                    'cid': component_id if assignment_type == 'component' else None,
                    'aid': assembly_id if assignment_type == 'assembly' else None,
                })
                row = cur.fetchone()
                if not row:
                    return jsonify({'ok': False, 'error': 'device not found'}), 404

                # Auto-enqueue set_name and set_id commands so the physical
                # counter reflects the assigned part
                part_name_val = None
                part_id_val = None
                if assignment_type == 'component':
                    cur.execute(
                        "SELECT part_id, part_name FROM components WHERE component_id = %(cid)s::uuid",
                        {'cid': component_id},
                    )
                    part = cur.fetchone()
                    if part:
                        part_name_val = part['part_name']
                        part_id_val = part['part_id']
                elif assignment_type == 'assembly':
                    cur.execute(
                        "SELECT assembly_code, assembly_name FROM assemblies WHERE assembly_id = %(aid)s::uuid",
                        {'aid': assembly_id},
                    )
                    asm = cur.fetchone()
                    if asm:
                        part_name_val = asm['assembly_name']
                        part_id_val = asm['assembly_code']

                enqueued_cmds = []
                if part_name_val and part_id_val:
                    for cmd_type, payload in [
                        ('set_name', {'part_name': part_name_val}),
                        ('set_id',   {'part_id':   part_id_val}),
                    ]:
                        cur.execute("""
                            INSERT INTO device_commands
                                (device_id, device_name, command_type, payload, status)
                            VALUES (%(did)s, %(dn)s, %(ct)s, %(pl)s::jsonb, 'pending')
                        """, {
                            'did': row['device_id'], 'dn': device_name,
                            'ct': cmd_type,
                            'pl': json_lib.dumps(payload),
                        })
                    enqueued_cmds += ['set_name', 'set_id']

                # Sync current stock count
                if assignment_type == 'component':
                    cur.execute("""
                        SELECT COALESCE(current_count, 0) AS cnt FROM inventory_items
                        WHERE item_type = 'component' AND component_id = %(cid)s::uuid
                    """, {'cid': component_id})
                else:
                    cur.execute("""
                        SELECT COALESCE(current_count, 0) AS cnt FROM inventory_items
                        WHERE item_type = 'assembly' AND assembly_id = %(aid)s::uuid
                    """, {'aid': assembly_id})
                inv = cur.fetchone()
                current_count = int(inv['cnt']) if inv else 0
                cur.execute("""
                    INSERT INTO device_commands
                        (device_id, device_name, command_type, payload, status)
                    VALUES (%(did)s, %(dn)s, 'set_count', %(pl)s::jsonb, 'pending')
                """, {'did': row['device_id'], 'dn': device_name,
                      'pl': json_lib.dumps({'count': current_count})})
                enqueued_cmds.append('set_count')

                # Sync thresholds
                if assignment_type == 'component':
                    cur.execute("SELECT min_threshold FROM components WHERE component_id = %(cid)s::uuid",
                                {'cid': component_id})
                else:
                    cur.execute("SELECT min_threshold FROM assemblies WHERE assembly_id = %(aid)s::uuid",
                                {'aid': assembly_id})
                thr_row = cur.fetchone()
                min_thr  = (thr_row['min_threshold'] or 0) if thr_row else 0
                crit_thr = max(0, min_thr // 2) if min_thr > 1 else 0
                cur.execute("""
                    INSERT INTO device_commands
                        (device_id, device_name, command_type, payload, status)
                    VALUES (%(did)s, %(dn)s, 'set_thresholds', %(pl)s::jsonb, 'pending')
                """, {'did': row['device_id'], 'dn': device_name,
                      'pl': json_lib.dumps({'warn_threshold': min_thr, 'critical_threshold': crit_thr})})
                enqueued_cmds.append('set_thresholds')

                conn.commit()
        return jsonify({'ok': True, 'device': {
            'device_id': str(row['device_id']),
            'device_name': row['device_name'],
            'assignment_type': row['assignment_type'],
        }, 'commands_enqueued': enqueued_cmds})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/devices/unassign', methods=['POST'])
def api_unassign_device():
    data = request.get_json(silent=True) or {}
    device_name = (data.get('device_name') or '').strip()
    if not device_name:
        return jsonify({'ok': False, 'error': 'device_name is required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE devices SET
                        assignment_type = 'unassigned',
                        assigned_component_id = NULL,
                        assigned_assembly_id = NULL
                    WHERE device_name = %(dn)s
                    RETURNING device_id, device_name
                """, {'dn': device_name})
                row = cur.fetchone()
                conn.commit()
        if not row:
            return jsonify({'ok': False, 'error': 'device not found'}), 404
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/devices/<device_name>/health', methods=['GET'])
def api_device_health(device_name):
    """Return time-series health readings for a device.
    Query params: hours (default 24), limit (default 500)
    """
    try:
        hours = float(request.args.get('hours', 24))
        limit = int(request.args.get('limit', 500))
        hours = max(1, min(hours, 8760))   # 1h–1yr
        limit = max(10, min(limit, 2000))
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'invalid query params'}), 400

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT recorded_at, battery_voltage, battery_pct,
                           rssi, temperature_c
                    FROM device_health_readings
                    WHERE device_name = %(name)s
                      AND recorded_at >= now() - (%(hours)s || ' hours')::interval
                    ORDER BY recorded_at ASC
                    LIMIT %(limit)s
                    """,
                    {'name': device_name, 'hours': hours, 'limit': limit},
                )
                rows = cur.fetchall()
        return jsonify({'ok': True, 'device_name': device_name, 'hours': hours,
                        'readings': [
            {
                'ts': r['recorded_at'].isoformat(),
                'battery_voltage': float(r['battery_voltage']) if r['battery_voltage'] is not None else None,
                'battery_pct': float(r['battery_pct']) if r['battery_pct'] is not None else None,
                'rssi': r['rssi'],
                'temperature_c': float(r['temperature_c']) if r['temperature_c'] is not None else None,
            }
            for r in rows
        ]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/devices/<device_name>/sync', methods=['POST'])
def api_device_sync(device_name):
    """Push one or more sync commands to a counter device on demand.

    Body (all optional, at least one required):
      {"count": true, "name": true, "thresholds": true}
    """
    data = request.get_json(silent=True) or {}
    do_count      = bool(data.get('count',      False))
    do_name       = bool(data.get('name',        False))
    do_thresholds = bool(data.get('thresholds',  False))
    if not any([do_count, do_name, do_thresholds]):
        return jsonify({'ok': False, 'error': 'specify at least one of: count, name, thresholds'}), 400

    def _enqueue(cur, did, dn, cmd_type, payload_dict):
        cur.execute("""
            INSERT INTO device_commands
                (device_id, device_name, command_type, payload, status)
            VALUES (%(did)s, %(dn)s, %(ct)s, %(pl)s::jsonb, 'pending')
        """, {'did': did, 'dn': dn, 'ct': cmd_type, 'pl': json_lib.dumps(payload_dict)})

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT device_id, device_name, assignment_type,
                           assigned_component_id, assigned_assembly_id
                    FROM devices
                    WHERE device_name = %(dn)s AND is_active = TRUE
                """, {'dn': device_name})
                dev = cur.fetchone()
                if not dev:
                    return jsonify({'ok': False, 'error': 'device not found'}), 404

                atype = dev['assignment_type']
                cid   = dev['assigned_component_id']
                aid   = dev['assigned_assembly_id']
                did   = dev['device_id']
                dn    = dev['device_name']

                if atype == 'component' and not cid:
                    return jsonify({'ok': False, 'error': 'device has no component assigned'}), 400
                if atype == 'assembly' and not aid:
                    return jsonify({'ok': False, 'error': 'device has no assembly assigned'}), 400
                if atype not in ('component', 'assembly'):
                    return jsonify({'ok': False, 'error': 'device is unassigned'}), 400

                enqueued = []

                if do_name:
                    if atype == 'component':
                        cur.execute("SELECT part_id, part_name FROM components WHERE component_id = %(cid)s",
                                    {'cid': cid})
                        part = cur.fetchone()
                        if part:
                            _enqueue(cur, did, dn, 'set_name', {'part_name': part['part_name']})
                            _enqueue(cur, did, dn, 'set_id',   {'part_id':   part['part_id']})
                            enqueued += ['set_name', 'set_id']
                    else:
                        cur.execute("SELECT assembly_code, assembly_name FROM assemblies WHERE assembly_id = %(aid)s",
                                    {'aid': aid})
                        asm = cur.fetchone()
                        if asm:
                            _enqueue(cur, did, dn, 'set_name', {'part_name': asm['assembly_name']})
                            _enqueue(cur, did, dn, 'set_id',   {'part_id':   asm['assembly_code']})
                            enqueued += ['set_name', 'set_id']

                if do_count:
                    if atype == 'component':
                        cur.execute("""
                            SELECT COALESCE(current_count, 0) AS cnt FROM inventory_items
                            WHERE item_type = 'component' AND component_id = %(cid)s
                        """, {'cid': cid})
                    else:
                        cur.execute("""
                            SELECT COALESCE(current_count, 0) AS cnt FROM inventory_items
                            WHERE item_type = 'assembly' AND assembly_id = %(aid)s
                        """, {'aid': aid})
                    inv = cur.fetchone()
                    _enqueue(cur, did, dn, 'set_count', {'count': int(inv['cnt']) if inv else 0})
                    enqueued.append('set_count')

                if do_thresholds:
                    if atype == 'component':
                        cur.execute("SELECT min_threshold FROM components WHERE component_id = %(cid)s",
                                    {'cid': cid})
                    else:
                        cur.execute("SELECT min_threshold FROM assemblies WHERE assembly_id = %(aid)s",
                                    {'aid': aid})
                    thr_row  = cur.fetchone()
                    min_thr  = (thr_row['min_threshold'] or 0) if thr_row else 0
                    crit_thr = max(0, min_thr // 2) if min_thr > 1 else 0
                    _enqueue(cur, did, dn, 'set_thresholds',
                             {'warn_threshold': min_thr, 'critical_threshold': crit_thr})
                    enqueued.append('set_thresholds')

                conn.commit()
        return jsonify({'ok': True, 'commands_enqueued': enqueued})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/devices/assignments', methods=['GET'])
def api_device_assignments():
    """List all active devices with their current assignments."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT d.device_id, d.device_name, d.assignment_type,
                           d.assigned_component_id, d.assigned_assembly_id,
                           d.last_seen_at, d.battery_voltage,
                           d.rssi, d.temperature_c,
                           c.part_id, c.part_name AS component_name,
                           a.assembly_code, a.assembly_name
                    FROM devices d
                    LEFT JOIN components c ON c.component_id = d.assigned_component_id
                    LEFT JOIN assemblies a ON a.assembly_id = d.assigned_assembly_id
                    WHERE d.is_active = TRUE
                    ORDER BY d.device_name ASC
                """)
                rows = cur.fetchall()
        return jsonify({'ok': True, 'devices': [
            {
                'device_id': str(r['device_id']),
                'device_name': r['device_name'],
                'assignment_type': r['assignment_type'],
                'assigned_component_id': str(r['assigned_component_id']) if r['assigned_component_id'] else None,
                'assigned_assembly_id': str(r['assigned_assembly_id']) if r['assigned_assembly_id'] else None,
                'last_seen_at': r['last_seen_at'].isoformat() if r['last_seen_at'] else None,
                'part_id': r['part_id'],
                'component_name': r['component_name'],
                'assembly_code': r['assembly_code'],
                'assembly_name': r['assembly_name'],
                'battery_voltage': float(r['battery_voltage']) if r['battery_voltage'] is not None else None,
                'rssi': r['rssi'],
                'temperature_c': float(r['temperature_c']) if r['temperature_c'] is not None else None,
            }
            for r in rows
        ]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════  PAGE ROUTES  ═══════════════════════════════════
@app.route('/orders')
def page_orders():
    return render_template('orders.html')


@app.route('/procurement')
def page_procurement():
    return render_template('procurement.html')


# ═══════════════════  PRODUCTION ORDERS  ═════════════════════════════
@app.route('/api/orders', methods=['GET'])
def api_list_orders():
    status_filter = request.args.get('status') or None
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                q = """
                    SELECT po.order_id, po.order_number, po.order_type, po.status,
                           po.customer_ref, po.notes, po.due_date,
                           po.created_at, po.confirmed_at, po.completed_at,
                           COUNT(pol.line_id)       AS line_count,
                           COALESCE(SUM(pol.qty_ordered), 0) AS total_qty
                    FROM production_orders po
                    LEFT JOIN production_order_lines pol ON pol.order_id = po.order_id
                """
                params = {}
                if status_filter:
                    q += " WHERE po.status = %(status)s"
                    params['status'] = status_filter
                q += " GROUP BY po.order_id ORDER BY po.created_at DESC"
                cur.execute(q, params)
                rows = cur.fetchall()
        return jsonify({'ok': True, 'orders': [
            {**r,
             'order_id': str(r['order_id']),
             'due_date': r['due_date'].isoformat() if r['due_date'] else None,
             'created_at': r['created_at'].isoformat(),
             'confirmed_at': r['confirmed_at'].isoformat() if r['confirmed_at'] else None,
             'completed_at': r['completed_at'].isoformat() if r['completed_at'] else None,
             'line_count': int(r['line_count']),
             'total_qty': float(r['total_qty']),
            } for r in rows
        ]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders', methods=['POST'])
def api_create_order():
    data = request.get_json(silent=True) or {}
    order_number = (data.get('order_number') or '').strip()
    if not order_number:
        return jsonify({'ok': False, 'error': 'order_number is required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO production_orders
                        (order_number, order_type, customer_ref, notes, due_date)
                    VALUES (%(num)s, %(type)s, %(cref)s, %(notes)s, %(due)s)
                    RETURNING order_id, order_number, order_type, status,
                              customer_ref, notes, due_date, created_at
                """, {
                    'num':   order_number,
                    'type':  data.get('order_type', 'production'),
                    'cref':  (data.get('customer_ref') or '').strip() or None,
                    'notes': (data.get('notes') or '').strip() or None,
                    'due':   data.get('due_date') or None,
                })
                row = cur.fetchone()
                conn.commit()
        return jsonify({'ok': True, 'order': {
            **row, 'order_id': str(row['order_id']),
            'due_date': row['due_date'].isoformat() if row['due_date'] else None,
            'created_at': row['created_at'].isoformat(),
        }}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders/<order_id>', methods=['GET'])
def api_get_order(order_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT order_id, order_number, order_type, status,
                           customer_ref, notes, due_date,
                           created_at, confirmed_at, completed_at
                    FROM production_orders WHERE order_id = %(oid)s::uuid
                """, {'oid': order_id})
                order = cur.fetchone()
                if not order:
                    return jsonify({'ok': False, 'error': 'Order not found'}), 404

                cur.execute("""
                    SELECT pol.line_id, pol.assembly_id, pol.qty_ordered, pol.qty_completed,
                           a.assembly_code, a.assembly_name,
                           COALESCE(ii.current_count, 0) AS asm_stock
                    FROM production_order_lines pol
                    JOIN assemblies a ON a.assembly_id = pol.assembly_id
                    LEFT JOIN inventory_items ii
                      ON ii.assembly_id = pol.assembly_id AND ii.item_type = 'assembly'
                    WHERE pol.order_id = %(oid)s::uuid
                    ORDER BY a.assembly_name
                """, {'oid': order_id})
                lines = cur.fetchall()

                # Material availability — aggregate BOM requirements across all lines
                cur.execute("""
                    SELECT ac.component_id, c.part_id, c.part_name, c.lead_time_days,
                           SUM(pol.qty_ordered * ac.quantity_required) AS qty_needed,
                           COALESCE(ii.current_count, 0)  AS current_count,
                           COALESCE(ii.reserved_count, 0) AS reserved_count
                    FROM production_order_lines pol
                    JOIN assembly_components ac ON ac.assembly_id = pol.assembly_id
                    JOIN components c ON c.component_id = ac.component_id
                    LEFT JOIN inventory_items ii
                      ON ii.component_id = ac.component_id AND ii.item_type = 'component'
                    WHERE pol.order_id = %(oid)s::uuid
                    GROUP BY ac.component_id, c.part_id, c.part_name, c.lead_time_days,
                             ii.current_count, ii.reserved_count
                    ORDER BY c.part_name
                """, {'oid': order_id})
                materials = cur.fetchall()

        def mat_status(current, reserved, qty_needed):
            available = current - reserved
            if available >= qty_needed:
                return 'ok'
            if available > 0:
                return 'partial'
            return 'shortage'

        return jsonify({'ok': True,
            'order': {
                **order,
                'order_id': str(order['order_id']),
                'due_date': order['due_date'].isoformat() if order['due_date'] else None,
                'created_at': order['created_at'].isoformat(),
                'confirmed_at': order['confirmed_at'].isoformat() if order['confirmed_at'] else None,
                'completed_at': order['completed_at'].isoformat() if order['completed_at'] else None,
            },
            'lines': [{
                **l, 'line_id': str(l['line_id']),
                'assembly_id': str(l['assembly_id']),
                'qty_ordered': float(l['qty_ordered']),
                'qty_completed': float(l['qty_completed']),
                'asm_stock': float(l['asm_stock']),
            } for l in lines],
            'materials': [{
                'component_id': str(m['component_id']),
                'part_id': m['part_id'],
                'part_name': m['part_name'],
                'lead_time_days': m['lead_time_days'],
                'qty_needed': float(m['qty_needed']),
                'current_count': float(m['current_count']),
                'reserved_count': float(m['reserved_count']),
                'available': float(m['current_count']) - float(m['reserved_count']),
                'status': mat_status(
                    float(m['current_count']),
                    float(m['reserved_count']),
                    float(m['qty_needed'])
                ),
            } for m in materials],
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders/<order_id>', methods=['PUT'])
def api_update_order(order_id):
    data = request.get_json(silent=True) or {}
    sets, params = [], {'oid': order_id}
    for col in ('order_number', 'customer_ref', 'notes', 'due_date', 'order_type'):
        if col in data:
            sets.append(f"{col} = %({col})s")
            params[col] = data[col] or None
    if not sets:
        return jsonify({'ok': False, 'error': 'nothing to update'}), 400
    sets.append("updated_at = now()")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE production_orders SET {', '.join(sets)} "
                    "WHERE order_id = %(oid)s::uuid AND status = 'draft' "
                    "RETURNING order_id",
                    params
                )
                if not cur.fetchone():
                    return jsonify({'ok': False, 'error': 'Order not found or not in draft status'}), 404
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders/<order_id>/lines', methods=['POST'])
def api_add_order_line(order_id):
    data = request.get_json(silent=True) or {}
    assembly_id = (data.get('assembly_id') or '').strip()
    qty = data.get('qty_ordered')
    if not assembly_id or qty is None:
        return jsonify({'ok': False, 'error': 'assembly_id and qty_ordered are required'}), 400
    try:
        qty = float(qty)
        if qty <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'qty_ordered must be a positive number'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM production_orders WHERE order_id = %(oid)s::uuid",
                    {'oid': order_id}
                )
                order = cur.fetchone()
                if not order:
                    return jsonify({'ok': False, 'error': 'Order not found'}), 404
                if order['status'] != 'draft':
                    return jsonify({'ok': False, 'error': 'Lines can only be added to draft orders'}), 400
                cur.execute("""
                    INSERT INTO production_order_lines (order_id, assembly_id, qty_ordered)
                    VALUES (%(oid)s::uuid, %(aid)s::uuid, %(qty)s)
                    ON CONFLICT (order_id, assembly_id)
                    DO UPDATE SET qty_ordered = EXCLUDED.qty_ordered
                    RETURNING line_id
                """, {'oid': order_id, 'aid': assembly_id, 'qty': qty})
                row = cur.fetchone()
                conn.commit()
        return jsonify({'ok': True, 'line_id': str(row['line_id'])}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders/<order_id>/lines/<line_id>', methods=['DELETE'])
def api_delete_order_line(order_id, line_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT po.status FROM production_orders po "
                    "JOIN production_order_lines pol ON pol.order_id = po.order_id "
                    "WHERE po.order_id = %(oid)s::uuid AND pol.line_id = %(lid)s::uuid",
                    {'oid': order_id, 'lid': line_id}
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({'ok': False, 'error': 'Line not found'}), 404
                if row['status'] != 'draft':
                    return jsonify({'ok': False, 'error': 'Lines can only be removed from draft orders'}), 400
                cur.execute(
                    "DELETE FROM production_order_lines WHERE line_id = %(lid)s::uuid",
                    {'lid': line_id}
                )
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders/<order_id>/confirm', methods=['POST'])
def api_confirm_order(order_id):
    """Confirm order: lock materials by creating component reservations."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT order_id, order_number, status FROM production_orders "
                    "WHERE order_id = %(oid)s::uuid",
                    {'oid': order_id}
                )
                order = cur.fetchone()
                if not order:
                    return jsonify({'ok': False, 'error': 'Order not found'}), 404
                if order['status'] != 'draft':
                    return jsonify({'ok': False, 'error': f'Cannot confirm order with status: {order["status"]}'}), 400

                # Aggregate BOM requirements across all lines
                cur.execute("""
                    SELECT ac.component_id, c.part_id, c.part_name,
                           SUM(pol.qty_ordered * ac.quantity_required) AS qty_needed
                    FROM production_order_lines pol
                    JOIN assembly_components ac ON ac.assembly_id = pol.assembly_id
                    JOIN components c ON c.component_id = ac.component_id
                    WHERE pol.order_id = %(oid)s::uuid
                    GROUP BY ac.component_id, c.part_id, c.part_name
                """, {'oid': order_id})
                reqs = cur.fetchall()
                if not reqs:
                    return jsonify({'ok': False, 'error': 'Order has no lines or assemblies have empty BOMs'}), 400

                shortfalls = []
                for req in reqs:
                    cid = req['component_id']
                    qty_needed = float(req['qty_needed'])
                    cur.execute("""
                        SELECT COALESCE(current_count, 0) AS current,
                               COALESCE(reserved_count, 0) AS reserved
                        FROM inventory_items
                        WHERE item_type = 'component' AND component_id = %(cid)s
                    """, {'cid': cid})
                    inv = cur.fetchone()
                    current  = float(inv['current'])  if inv else 0.0
                    reserved = float(inv['reserved']) if inv else 0.0
                    available = current - reserved
                    if available < qty_needed:
                        shortfalls.append({
                            'part_id': req['part_id'], 'part_name': req['part_name'],
                            'needed': qty_needed, 'available': available,
                            'shortfall': qty_needed - available,
                        })

                # Create reservations regardless of shortfalls
                for req in reqs:
                    cid = req['component_id']
                    qty_needed = float(req['qty_needed'])
                    cur.execute("""
                        INSERT INTO component_reservations (order_id, component_id, qty_reserved)
                        VALUES (%(oid)s::uuid, %(cid)s, %(qty)s)
                        ON CONFLICT (order_id, component_id)
                        DO UPDATE SET qty_reserved = EXCLUDED.qty_reserved
                    """, {'oid': order_id, 'cid': cid, 'qty': qty_needed})
                    cur.execute("""
                        INSERT INTO inventory_items (item_type, component_id, current_count, reserved_count)
                        VALUES ('component', %(cid)s, 0, %(qty)s)
                        ON CONFLICT (component_id) WHERE component_id IS NOT NULL
                        DO UPDATE SET
                            reserved_count = inventory_items.reserved_count + %(qty)s,
                            updated_at = now()
                    """, {'cid': cid, 'qty': qty_needed})

                cur.execute("""
                    UPDATE production_orders
                    SET status = 'confirmed', confirmed_at = now(), updated_at = now()
                    WHERE order_id = %(oid)s::uuid
                """, {'oid': order_id})
                conn.commit()

        fire_webhooks('order_confirmed', {
            'order_id': order_id, 'order_number': order['order_number'],
        })
        return jsonify({'ok': True, 'shortfalls': shortfalls})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders/<order_id>/start', methods=['POST'])
def api_start_order(order_id):
    """Move confirmed order to in_progress."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE production_orders SET status = 'in_progress', updated_at = now()
                    WHERE order_id = %(oid)s::uuid AND status = 'confirmed'
                    RETURNING order_id
                """, {'oid': order_id})
                if not cur.fetchone():
                    return jsonify({'ok': False, 'error': 'Order not found or not confirmed'}), 404
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders/<order_id>/complete', methods=['POST'])
def api_complete_order(order_id):
    """Complete order: consume reserved component stock, add produced assemblies, sync counters."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT order_id, order_number, status FROM production_orders "
                    "WHERE order_id = %(oid)s::uuid",
                    {'oid': order_id}
                )
                order = cur.fetchone()
                if not order:
                    return jsonify({'ok': False, 'error': 'Order not found'}), 404
                if order['status'] not in ('confirmed', 'in_progress'):
                    return jsonify({'ok': False, 'error': f'Cannot complete order with status: {order["status"]}'}), 400

                # Get component reservations
                cur.execute("""
                    SELECT component_id, qty_reserved
                    FROM component_reservations
                    WHERE order_id = %(oid)s::uuid
                """, {'oid': order_id})
                reservations = cur.fetchall()

                # Get order lines
                cur.execute("""
                    SELECT pol.assembly_id, pol.qty_ordered,
                           a.assembly_code, a.assembly_name
                    FROM production_order_lines pol
                    JOIN assemblies a ON a.assembly_id = pol.assembly_id
                    WHERE pol.order_id = %(oid)s::uuid
                """, {'oid': order_id})
                lines = cur.fetchall()

                commands_enqueued = []

                # Consume reserved component stock
                for res in reservations:
                    cid = res['component_id']
                    qty = float(res['qty_reserved'])
                    cur.execute("""
                        UPDATE inventory_items
                        SET current_count  = GREATEST(0, current_count  - %(qty)s),
                            reserved_count = GREATEST(0, reserved_count - %(qty)s),
                            updated_at = now()
                        WHERE item_type = 'component' AND component_id = %(cid)s
                        RETURNING current_count
                    """, {'qty': qty, 'cid': cid})
                    inv_row = cur.fetchone()
                    new_comp_count = int(inv_row['current_count']) if inv_row else 0

                    cur.execute("""
                        UPDATE component_reservations SET qty_consumed = qty_reserved
                        WHERE order_id = %(oid)s::uuid AND component_id = %(cid)s
                    """, {'oid': order_id, 'cid': cid})

                    # Enqueue set_count to assigned devices for this component
                    cur.execute("""
                        SELECT device_id, device_name FROM devices
                        WHERE assignment_type = 'component'
                          AND assigned_component_id = %(cid)s AND is_active = TRUE
                    """, {'cid': cid})
                    for dev in cur.fetchall():
                        cur.execute("""
                            INSERT INTO device_commands
                                (device_id, device_name, command_type, payload, status)
                            VALUES (%(did)s, %(dn)s, 'set_count', %(pl)s::jsonb, 'pending')
                        """, {
                            'did': dev['device_id'], 'dn': dev['device_name'],
                            'pl': psycopg.types.json.Jsonb({'count': new_comp_count}),
                        })
                        commands_enqueued.append(dev['device_name'])

                # Add produced assemblies to inventory
                for line in lines:
                    aid = line['assembly_id']
                    qty = float(line['qty_ordered'])
                    cur.execute("""
                        INSERT INTO inventory_items (item_type, assembly_id, current_count)
                        VALUES ('assembly', %(aid)s, %(qty)s)
                        ON CONFLICT (assembly_id) WHERE assembly_id IS NOT NULL
                        DO UPDATE SET
                            current_count = inventory_items.current_count + %(qty)s,
                            updated_at = now()
                        RETURNING current_count
                    """, {'aid': aid, 'qty': qty})
                    asm_row = cur.fetchone()
                    new_asm_count = int(asm_row['current_count']) if asm_row else int(qty)

                    cur.execute("""
                        UPDATE production_order_lines
                        SET qty_completed = qty_ordered
                        WHERE order_id = %(oid)s::uuid AND assembly_id = %(aid)s
                    """, {'oid': order_id, 'aid': aid})

                    # Enqueue set_count to assigned assembly devices
                    cur.execute("""
                        SELECT device_id, device_name FROM devices
                        WHERE assignment_type = 'assembly'
                          AND assigned_assembly_id = %(aid)s AND is_active = TRUE
                    """, {'aid': aid})
                    for dev in cur.fetchall():
                        cur.execute("""
                            INSERT INTO device_commands
                                (device_id, device_name, command_type, payload, status)
                            VALUES (%(did)s, %(dn)s, 'set_count', %(pl)s::jsonb, 'pending')
                        """, {
                            'did': dev['device_id'], 'dn': dev['device_name'],
                            'pl': psycopg.types.json.Jsonb({'count': new_asm_count}),
                        })
                        commands_enqueued.append(dev['device_name'])

                cur.execute("""
                    UPDATE production_orders
                    SET status = 'completed', completed_at = now(), updated_at = now()
                    WHERE order_id = %(oid)s::uuid
                """, {'oid': order_id})
                conn.commit()

        fire_webhooks('order_completed', {
            'order_id': order_id, 'order_number': order['order_number'],
        })
        return jsonify({'ok': True, 'commands_enqueued': commands_enqueued})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/orders/<order_id>/cancel', methods=['POST'])
def api_cancel_order(order_id):
    """Cancel order and release all component reservations."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM production_orders WHERE order_id = %(oid)s::uuid",
                    {'oid': order_id}
                )
                order = cur.fetchone()
                if not order:
                    return jsonify({'ok': False, 'error': 'Order not found'}), 404
                if order['status'] == 'completed':
                    return jsonify({'ok': False, 'error': 'Cannot cancel a completed order'}), 400

                # Release reservations if any exist
                if order['status'] in ('confirmed', 'in_progress'):
                    cur.execute("""
                        SELECT component_id, qty_reserved - qty_consumed AS qty_to_release
                        FROM component_reservations WHERE order_id = %(oid)s::uuid
                    """, {'oid': order_id})
                    for res in cur.fetchall():
                        to_release = float(res['qty_to_release'])
                        if to_release > 0:
                            cur.execute("""
                                UPDATE inventory_items
                                SET reserved_count = GREATEST(0, reserved_count - %(qty)s),
                                    updated_at = now()
                                WHERE item_type = 'component' AND component_id = %(cid)s
                            """, {'qty': to_release, 'cid': res['component_id']})

                cur.execute("""
                    UPDATE production_orders
                    SET status = 'cancelled', updated_at = now()
                    WHERE order_id = %(oid)s::uuid
                """, {'oid': order_id})
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════  SUPPLIER ORDERS  ═══════════════════════════════
@app.route('/api/supplier-orders', methods=['GET'])
def api_list_supplier_orders():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT so.supplier_order_id, so.order_number, so.supplier_name,
                           so.status, so.notes, so.ordered_at, so.expected_delivery,
                           so.received_at, so.created_at,
                           COUNT(sol.line_id)              AS line_count,
                           COALESCE(SUM(sol.qty_ordered),  0) AS total_ordered,
                           COALESCE(SUM(sol.qty_received), 0) AS total_received
                    FROM supplier_orders so
                    LEFT JOIN supplier_order_lines sol
                      ON sol.supplier_order_id = so.supplier_order_id
                    GROUP BY so.supplier_order_id
                    ORDER BY so.created_at DESC
                """)
                rows = cur.fetchall()
        return jsonify({'ok': True, 'orders': [
            {**r,
             'supplier_order_id': str(r['supplier_order_id']),
             'ordered_at': r['ordered_at'].isoformat() if r['ordered_at'] else None,
             'expected_delivery': r['expected_delivery'].isoformat() if r['expected_delivery'] else None,
             'received_at': r['received_at'].isoformat() if r['received_at'] else None,
             'created_at': r['created_at'].isoformat(),
             'line_count': int(r['line_count']),
             'total_ordered': float(r['total_ordered']),
             'total_received': float(r['total_received']),
            } for r in rows
        ]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/supplier-orders', methods=['POST'])
def api_create_supplier_order():
    data = request.get_json(silent=True) or {}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO supplier_orders
                        (order_number, supplier_name, notes, ordered_at, expected_delivery)
                    VALUES (%(num)s, %(sup)s, %(notes)s, %(ordered)s, %(expected)s)
                    RETURNING supplier_order_id, order_number, supplier_name, status, created_at
                """, {
                    'num':      (data.get('order_number') or '').strip() or None,
                    'sup':      (data.get('supplier_name') or '').strip() or None,
                    'notes':    (data.get('notes') or '').strip() or None,
                    'ordered':  data.get('ordered_at') or None,
                    'expected': data.get('expected_delivery') or None,
                })
                row = cur.fetchone()
                soid = row['supplier_order_id']

                # Add initial line items if provided
                lines_in = data.get('lines', [])
                for li in lines_in:
                    comp_id = li.get('component_id')
                    qty = li.get('qty_ordered')
                    if comp_id and qty and float(qty) > 0:
                        cur.execute("""
                            INSERT INTO supplier_order_lines
                                (supplier_order_id, component_id, qty_ordered, unit_cost, notes)
                            VALUES (%(soid)s, %(cid)s::uuid, %(qty)s, %(cost)s, %(notes)s)
                        """, {
                            'soid': soid, 'cid': comp_id, 'qty': float(qty),
                            'cost': li.get('unit_cost') or None,
                            'notes': (li.get('notes') or '').strip() or None,
                        })
                conn.commit()
        return jsonify({'ok': True, 'supplier_order_id': str(soid)}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/supplier-orders/<soid>', methods=['GET'])
def api_get_supplier_order(soid):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM supplier_orders WHERE supplier_order_id = %(soid)s::uuid
                """, {'soid': soid})
                order = cur.fetchone()
                if not order:
                    return jsonify({'ok': False, 'error': 'Supplier order not found'}), 404
                cur.execute("""
                    SELECT sol.line_id, sol.component_id, sol.qty_ordered, sol.qty_received,
                           sol.unit_cost, sol.notes,
                           c.part_id, c.part_name, c.supplier_url, c.lead_time_days,
                           COALESCE(ii.current_count, 0) AS current_stock
                    FROM supplier_order_lines sol
                    JOIN components c ON c.component_id = sol.component_id
                    LEFT JOIN inventory_items ii
                      ON ii.component_id = sol.component_id AND ii.item_type = 'component'
                    WHERE sol.supplier_order_id = %(soid)s::uuid
                    ORDER BY c.part_name
                """, {'soid': soid})
                lines = cur.fetchall()
        return jsonify({'ok': True,
            'order': {
                **order,
                'supplier_order_id': str(order['supplier_order_id']),
                'ordered_at': order['ordered_at'].isoformat() if order['ordered_at'] else None,
                'expected_delivery': order['expected_delivery'].isoformat() if order['expected_delivery'] else None,
                'received_at': order['received_at'].isoformat() if order['received_at'] else None,
                'created_at': order['created_at'].isoformat(),
                'updated_at': order['updated_at'].isoformat(),
            },
            'lines': [{
                **l, 'line_id': str(l['line_id']),
                'component_id': str(l['component_id']),
                'qty_ordered': float(l['qty_ordered']),
                'qty_received': float(l['qty_received']),
                'current_stock': float(l['current_stock']),
            } for l in lines],
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/supplier-orders/<soid>/lines', methods=['POST'])
def api_add_supplier_order_line(soid):
    data = request.get_json(silent=True) or {}
    comp_id = (data.get('component_id') or '').strip()
    qty = data.get('qty_ordered')
    if not comp_id or qty is None:
        return jsonify({'ok': False, 'error': 'component_id and qty_ordered are required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM supplier_orders WHERE supplier_order_id = %(soid)s::uuid",
                    {'soid': soid}
                )
                so = cur.fetchone()
                if not so:
                    return jsonify({'ok': False, 'error': 'Supplier order not found'}), 404
                if so['status'] not in ('draft', 'ordered'):
                    return jsonify({'ok': False, 'error': 'Cannot add lines to this order'}), 400
                cur.execute("""
                    INSERT INTO supplier_order_lines
                        (supplier_order_id, component_id, qty_ordered, unit_cost, notes)
                    VALUES (%(soid)s::uuid, %(cid)s::uuid, %(qty)s, %(cost)s, %(notes)s)
                    RETURNING line_id
                """, {
                    'soid': soid, 'cid': comp_id, 'qty': float(qty),
                    'cost': data.get('unit_cost') or None,
                    'notes': (data.get('notes') or '').strip() or None,
                })
                row = cur.fetchone()
                conn.commit()
        return jsonify({'ok': True, 'line_id': str(row['line_id'])}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/supplier-orders/<soid>/place', methods=['POST'])
def api_place_supplier_order(soid):
    """Mark order as placed (ordered)."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE supplier_orders
                    SET status = 'ordered', ordered_at = COALESCE(ordered_at, now()), updated_at = now()
                    WHERE supplier_order_id = %(soid)s::uuid AND status = 'draft'
                    RETURNING supplier_order_id
                """, {'soid': soid})
                if not cur.fetchone():
                    return jsonify({'ok': False, 'error': 'Order not found or not in draft'}), 404
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/supplier-orders/<soid>/receive', methods=['POST'])
def api_receive_supplier_order(soid):
    """Record receipt of items: update inventory and sync counter devices."""
    data = request.get_json(silent=True) or {}
    receipts = data.get('line_receipts', [])
    if not receipts:
        return jsonify({'ok': False, 'error': 'line_receipts is required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM supplier_orders WHERE supplier_order_id = %(soid)s::uuid",
                    {'soid': soid}
                )
                so = cur.fetchone()
                if not so:
                    return jsonify({'ok': False, 'error': 'Supplier order not found'}), 404
                if so['status'] == 'cancelled':
                    return jsonify({'ok': False, 'error': 'Cannot receive into a cancelled order'}), 400

                commands_enqueued = []
                for receipt in receipts:
                    lid = receipt.get('line_id')
                    qty_recv = receipt.get('qty_received', 0)
                    if not lid or float(qty_recv) <= 0:
                        continue
                    cur.execute("""
                        SELECT component_id, qty_ordered, qty_received
                        FROM supplier_order_lines
                        WHERE line_id = %(lid)s::uuid AND supplier_order_id = %(soid)s::uuid
                    """, {'lid': lid, 'soid': soid})
                    line = cur.fetchone()
                    if not line:
                        continue

                    new_received = float(line['qty_received']) + float(qty_recv)
                    cur.execute("""
                        UPDATE supplier_order_lines SET qty_received = %(nr)s
                        WHERE line_id = %(lid)s::uuid
                    """, {'nr': new_received, 'lid': lid})

                    # Add to inventory
                    cur.execute("""
                        INSERT INTO inventory_items (item_type, component_id, current_count)
                        VALUES ('component', %(cid)s, %(qty)s)
                        ON CONFLICT (component_id) WHERE component_id IS NOT NULL
                        DO UPDATE SET
                            current_count = inventory_items.current_count + %(qty)s,
                            updated_at = now()
                        RETURNING current_count
                    """, {'cid': line['component_id'], 'qty': float(qty_recv)})
                    inv_row = cur.fetchone()
                    new_count = int(inv_row['current_count']) if inv_row else int(qty_recv)

                    # Sync counters
                    cur.execute("""
                        SELECT device_id, device_name FROM devices
                        WHERE assignment_type = 'component'
                          AND assigned_component_id = %(cid)s AND is_active = TRUE
                    """, {'cid': line['component_id']})
                    for dev in cur.fetchall():
                        cur.execute("""
                            INSERT INTO device_commands
                                (device_id, device_name, command_type, payload, status)
                            VALUES (%(did)s, %(dn)s, 'set_count', %(pl)s::jsonb, 'pending')
                        """, {
                            'did': dev['device_id'], 'dn': dev['device_name'],
                            'pl': psycopg.types.json.Jsonb({'count': new_count}),
                        })
                        commands_enqueued.append(dev['device_name'])

                # Update overall status
                cur.execute("""
                    SELECT COALESCE(SUM(qty_ordered), 0)  AS tot_ord,
                           COALESCE(SUM(qty_received), 0) AS tot_recv
                    FROM supplier_order_lines
                    WHERE supplier_order_id = %(soid)s::uuid
                """, {'soid': soid})
                totals = cur.fetchone()
                if float(totals['tot_recv']) >= float(totals['tot_ord']):
                    cur.execute("""
                        UPDATE supplier_orders
                        SET status = 'received', received_at = now(), updated_at = now()
                        WHERE supplier_order_id = %(soid)s::uuid
                    """, {'soid': soid})
                else:
                    cur.execute("""
                        UPDATE supplier_orders SET status = 'partial', updated_at = now()
                        WHERE supplier_order_id = %(soid)s::uuid
                    """, {'soid': soid})
                conn.commit()

        fire_webhooks('supplier_received', {'supplier_order_id': soid})
        return jsonify({'ok': True, 'commands_enqueued': commands_enqueued})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════  INVENTORY VELOCITY  ════════════════════════════
@app.route('/api/inventory/velocity', methods=['GET'])
def api_inventory_velocity():
    """Compute component consumption velocity from completed production orders (last 90 days)."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    WITH consumed AS (
                        SELECT cr.component_id, SUM(cr.qty_consumed) AS total_consumed
                        FROM component_reservations cr
                        JOIN production_orders po ON po.order_id = cr.order_id
                        WHERE po.status = 'completed'
                          AND po.completed_at > now() - interval '90 days'
                          AND cr.qty_consumed > 0
                        GROUP BY cr.component_id
                    )
                    SELECT c.component_id, c.part_id, c.part_name,
                           c.lead_time_days, c.min_order_qty, c.min_threshold,
                           COALESCE(ii.current_count,  0) AS current_count,
                           COALESCE(ii.reserved_count, 0) AS reserved_count,
                           COALESCE(con.total_consumed, 0) AS consumed_90d,
                           COALESCE(con.total_consumed, 0) / 90.0 AS daily_rate
                    FROM components c
                    LEFT JOIN inventory_items ii
                      ON ii.component_id = c.component_id AND ii.item_type = 'component'
                    LEFT JOIN consumed con ON con.component_id = c.component_id
                    WHERE c.is_active = TRUE
                    ORDER BY
                        CASE WHEN COALESCE(con.total_consumed, 0) > 0 THEN 0 ELSE 1 END,
                        c.part_name ASC
                """)
                rows = cur.fetchall()

        result = []
        for r in rows:
            daily_rate = float(r['daily_rate']) if r['daily_rate'] else 0.0
            available  = max(0.0, float(r['current_count']) - float(r['reserved_count']))
            days_remaining = round(available / daily_rate, 1) if daily_rate > 0 else None
            lead = int(r['lead_time_days'] or 7)
            result.append({
                'component_id':  str(r['component_id']),
                'part_id':       r['part_id'],
                'part_name':     r['part_name'],
                'lead_time_days': lead,
                'min_order_qty': int(r['min_order_qty'] or 1),
                'min_threshold': int(r['min_threshold'] or 0),
                'current_count': float(r['current_count']),
                'reserved_count': float(r['reserved_count']),
                'available':     available,
                'consumed_90d':  float(r['consumed_90d']),
                'daily_rate':    round(daily_rate, 3),
                'days_remaining': days_remaining,
                'reorder_alert': (
                    days_remaining is not None and days_remaining <= lead
                ),
            })
        return jsonify({'ok': True, 'velocity': result})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════  WEBHOOKS  ══════════════════════════════════════
@app.route('/api/webhooks', methods=['GET'])
def api_list_webhooks():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT webhook_id, name, url, event_types, is_active, created_at
                    FROM webhooks ORDER BY created_at DESC
                """)
                rows = cur.fetchall()
        return jsonify({'ok': True, 'webhooks': [
            {**r, 'webhook_id': str(r['webhook_id']),
             'created_at': r['created_at'].isoformat()}
            for r in rows
        ]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/webhooks', methods=['POST'])
def api_create_webhook():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    url  = (data.get('url')  or '').strip()
    if not name or not url:
        return jsonify({'ok': False, 'error': 'name and url are required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO webhooks (name, url, event_types, secret)
                    VALUES (%(name)s, %(url)s, %(et)s, %(secret)s)
                    RETURNING webhook_id, name, url, event_types, is_active, created_at
                """, {
                    'name': name, 'url': url,
                    'et':     data.get('event_types', []),
                    'secret': (data.get('secret') or '').strip() or None,
                })
                row = cur.fetchone()
                conn.commit()
        return jsonify({'ok': True, 'webhook': {
            **row, 'webhook_id': str(row['webhook_id']),
            'created_at': row['created_at'].isoformat()
        }}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/webhooks/<webhook_id>', methods=['PUT'])
def api_update_webhook(webhook_id):
    data = request.get_json(silent=True) or {}
    sets, params = [], {'wid': webhook_id}
    for col in ('name', 'url', 'secret', 'is_active'):
        if col in data:
            sets.append(f"{col} = %({col})s")
            params[col] = data[col]
    if 'event_types' in data:
        sets.append("event_types = %(event_types)s")
        params['event_types'] = data['event_types']
    if not sets:
        return jsonify({'ok': False, 'error': 'nothing to update'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE webhooks SET {', '.join(sets)} "
                    "WHERE webhook_id = %(wid)s::uuid RETURNING webhook_id",
                    params
                )
                if not cur.fetchone():
                    return jsonify({'ok': False, 'error': 'Webhook not found'}), 404
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/webhooks/<webhook_id>', methods=['DELETE'])
def api_delete_webhook(webhook_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM webhooks WHERE webhook_id = %(wid)s::uuid RETURNING webhook_id",
                    {'wid': webhook_id}
                )
                if not cur.fetchone():
                    return jsonify({'ok': False, 'error': 'Webhook not found'}), 404
                conn.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════  CYCLE COUNTS  ══════════════════════════════════
@app.route('/api/cycle-counts', methods=['GET'])
def api_list_cycle_counts():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT cc.schedule_id, cc.component_id, cc.assembly_id,
                           cc.scheduled_date, cc.status, cc.system_count,
                           cc.confirmed_count, cc.discrepancy, cc.notes, cc.completed_at,
                           c.part_id, c.part_name,
                           a.assembly_code, a.assembly_name
                    FROM cycle_count_schedules cc
                    LEFT JOIN components c ON c.component_id = cc.component_id
                    LEFT JOIN assemblies  a ON a.assembly_id  = cc.assembly_id
                    ORDER BY cc.scheduled_date DESC, cc.created_at DESC
                    LIMIT 200
                """)
                rows = cur.fetchall()
        return jsonify({'ok': True, 'cycle_counts': [
            {**r,
             'schedule_id':  str(r['schedule_id']),
             'component_id': str(r['component_id']) if r['component_id'] else None,
             'assembly_id':  str(r['assembly_id'])  if r['assembly_id']  else None,
             'scheduled_date': r['scheduled_date'].isoformat() if r['scheduled_date'] else None,
             'completed_at':   r['completed_at'].isoformat()   if r['completed_at']   else None,
             'system_count':    float(r['system_count'])    if r['system_count']    is not None else None,
             'confirmed_count': float(r['confirmed_count']) if r['confirmed_count'] is not None else None,
             'discrepancy':     float(r['discrepancy'])     if r['discrepancy']     is not None else None,
            } for r in rows
        ]})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/cycle-counts', methods=['POST'])
def api_create_cycle_count():
    data = request.get_json(silent=True) or {}
    comp_id = (data.get('component_id') or '').strip() or None
    asm_id  = (data.get('assembly_id')  or '').strip() or None
    sched   = data.get('scheduled_date')
    if not (comp_id or asm_id):
        return jsonify({'ok': False, 'error': 'component_id or assembly_id is required'}), 400
    if not sched:
        return jsonify({'ok': False, 'error': 'scheduled_date is required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Capture current system count
                if comp_id:
                    cur.execute("""
                        SELECT COALESCE(current_count, 0) AS cnt
                        FROM inventory_items
                        WHERE item_type = 'component' AND component_id = %(cid)s::uuid
                    """, {'cid': comp_id})
                else:
                    cur.execute("""
                        SELECT COALESCE(current_count, 0) AS cnt
                        FROM inventory_items
                        WHERE item_type = 'assembly' AND assembly_id = %(aid)s::uuid
                    """, {'aid': asm_id})
                row = cur.fetchone()
                system_count = float(row['cnt']) if row else 0.0

                cur.execute("""
                    INSERT INTO cycle_count_schedules
                        (component_id, assembly_id, scheduled_date, system_count)
                    VALUES (%(cid)s, %(aid)s, %(sched)s, %(sc)s)
                    RETURNING schedule_id
                """, {
                    'cid':   comp_id,
                    'aid':   asm_id,
                    'sched': sched,
                    'sc':    system_count,
                })
                row = cur.fetchone()
                conn.commit()
        return jsonify({'ok': True, 'schedule_id': str(row['schedule_id'])}), 201
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/cycle-counts/<schedule_id>', methods=['PUT'])
def api_complete_cycle_count(schedule_id):
    """Record the operator-confirmed count and optionally adjust inventory."""
    data = request.get_json(silent=True) or {}
    confirmed_count = data.get('confirmed_count')
    if confirmed_count is None:
        return jsonify({'ok': False, 'error': 'confirmed_count is required'}), 400
    try:
        confirmed_count = float(confirmed_count)
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'confirmed_count must be numeric'}), 400

    adjust_inventory = data.get('adjust_inventory', False)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT schedule_id, component_id, assembly_id, system_count, status
                    FROM cycle_count_schedules WHERE schedule_id = %(sid)s::uuid
                """, {'sid': schedule_id})
                cc = cur.fetchone()
                if not cc:
                    return jsonify({'ok': False, 'error': 'Cycle count not found'}), 404
                if cc['status'] == 'completed':
                    return jsonify({'ok': False, 'error': 'Already completed'}), 400

                cur.execute("""
                    UPDATE cycle_count_schedules
                    SET status = 'completed', confirmed_count = %(cc)s,
                        notes = %(notes)s, completed_at = now()
                    WHERE schedule_id = %(sid)s::uuid
                """, {
                    'cc':    confirmed_count,
                    'notes': (data.get('notes') or '').strip() or None,
                    'sid':   schedule_id,
                })

                # Optionally reconcile inventory to confirmed count
                if adjust_inventory:
                    if cc['component_id']:
                        cur.execute("""
                            INSERT INTO inventory_items (item_type, component_id, current_count)
                            VALUES ('component', %(cid)s, %(cnt)s)
                            ON CONFLICT (component_id) WHERE component_id IS NOT NULL
                            DO UPDATE SET current_count = %(cnt)s, updated_at = now()
                        """, {'cid': cc['component_id'], 'cnt': confirmed_count})
                    elif cc['assembly_id']:
                        cur.execute("""
                            INSERT INTO inventory_items (item_type, assembly_id, current_count)
                            VALUES ('assembly', %(aid)s, %(cnt)s)
                            ON CONFLICT (assembly_id) WHERE assembly_id IS NOT NULL
                            DO UPDATE SET current_count = %(cnt)s, updated_at = now()
                        """, {'aid': cc['assembly_id'], 'cnt': confirmed_count})
                conn.commit()
        return jsonify({'ok': True, 'discrepancy': confirmed_count - float(cc['system_count'] or 0)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=True)
