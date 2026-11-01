#!/usr/bin/env python3
"""treadmill-bridge: read live telemetry from the NordicFTMS console over Wahoo
DIRCON (Direct Connect, plain TCP) and publish it to Home Assistant via MQTT
discovery.

READ-ONLY by design. It only ever sends DIRCON DiscoverServices and
EnableNotifications requests and reads Notification pushes. It never writes an
FTMS control-point characteristic and issues NO motor command — there is no
code path here that writes a value to the treadmill.

Transport (validated live against the console 2026-11-01, see
docs/runbooks/treadmill-glassos-bridge.md and the reference client
bridge-dev/dircon_probe.py):

  DIRCON framing — 6-byte header:
    [0] 0x01 protocol version
    [1] message type
    [2] seq            (UNIQUE, incrementing per request — the server silently
                        drops a request whose seq equals the previous one)
    [3] response code
    [4:6] payload length, BIG-endian
    [6:]  payload
  Message types: 0x01 DiscoverServices, 0x05 EnableNotifications,
  0x06 Notification (server push, seq 0, payload = 16-byte char UUID + value).

  On connect: DiscoverServices (seq 1), then EnableNotifications on the FTMS
  Treadmill Data characteristic 0x2ACD (seq 2, payload = 16-byte UUID + 0x01).
  Notifications then arrive ~2 Hz continuously.

  FTMS Treadmill Data 0x2ACD: flags u16 LE, then (per flag bits) speed u16 LE
  x0.01 km/h, distance u24 LE metres, incline s16 LE x0.1 % (+ ramp s16, 0x7FFF
  = N/A, ignored).

Publishes:
  - retained discovery configs under <DISCOVERY_PREFIX>/sensor/<obj>/config
    (one shared "Treadmill" device) for speed (km/h), distance (m), incline (%)
  - live state under <STATE_PREFIX>/<field>
  - availability under <STATE_PREFIX>/status (online / offline), also the MQTT
    Last-Will so a crash marks the device unavailable in HA

Writes <STATE_DIR>/bridge.heartbeat every loop for the compose healthcheck.
"""
import os
import json
import signal
import socket
import struct
import sys
import time

import paho.mqtt.client as mqtt

# --- config (env) -----------------------------------------------------------
TREADMILL_HOST = os.environ.get("TREADMILL_HOST", "")
DIRCON_PORT = int(os.environ.get("DIRCON_PORT", "36866"))
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "") or None
MQTT_PASS = os.environ.get("MQTT_PASS", "") or None
DISCOVERY_PREFIX = os.environ.get("DISCOVERY_PREFIX", "homeassistant")
STATE_PREFIX = os.environ.get("STATE_PREFIX", "kdk/treadmill")
STATE_DIR = os.environ.get("STATE_DIR", "/state")
# DIRCON reconnect backoff (seconds). Capped well under the healthcheck window
# so the heartbeat stays fresh while reconnecting.
BACKOFF_START = float(os.environ.get("BACKOFF_START", "2"))
BACKOFF_MAX = float(os.environ.get("BACKOFF_MAX", "60"))

HEARTBEAT = os.path.join(STATE_DIR, "bridge.heartbeat")
AVAIL_TOPIC = f"{STATE_PREFIX}/status"
DEVICE = {
    "identifiers": ["kdk_treadmill"],
    "name": "Treadmill",
    "manufacturer": "NordicTrack",
    "model": "Commercial 1250",
}
# object_id -> discovery payload fragment. object_id fixes the HA entity id
# (sensor.treadmill_<field>), which the treadmill.yaml HA package references.
SENSORS = {
    "treadmill_speed": {
        "name": "Speed",
        "field": "speed_kmh",
        "unit_of_measurement": "km/h",
        "device_class": "speed",
        "state_class": "measurement",
        "icon": "mdi:run-fast",
        "suggested_display_precision": 1,
    },
    "treadmill_distance": {
        "name": "Distance",
        "field": "distance_m",
        "unit_of_measurement": "m",
        "device_class": "distance",
        "state_class": "measurement",
        "icon": "mdi:map-marker-distance",
    },
    "treadmill_incline": {
        "name": "Incline",
        "field": "incline_pct",
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "icon": "mdi:angle-acute",
        "suggested_display_precision": 1,
    },
}

# --- DIRCON protocol --------------------------------------------------------
MV = 0x01
DISCOVER_SERVICES = 0x01
ENABLE_NOTIFY = 0x05
CHAR_NOTIFY = 0x06


def uuid16(u16):  # 0xXXXX -> the 16-byte 128-bit UUID
    return bytes.fromhex("0000%04x00001000800000805f9b34fb" % u16)


FTMS_SVC = uuid16(0x1826)
TREADMILL_DATA = uuid16(0x2ACD)


def pkt(ident, data=b"", seq=0):
    return bytes([MV, ident, seq, 0x00]) + struct.pack(">H", len(data)) + data


def recv_pkt(sock):
    hdr = b""
    while len(hdr) < 6:
        b = sock.recv(6 - len(hdr))
        if not b:
            return None
        hdr += b
    ident, seq, rc = hdr[1], hdr[2], hdr[3]
    length = struct.unpack(">H", hdr[4:6])[0]
    data = b""
    while len(data) < length:
        b = sock.recv(length - len(data))
        if not b:
            return None
        data += b
    return ident, seq, rc, data


def parse_treadmill(v):
    """Decode FTMS Treadmill Data (0x2ACD). Returns the subset we publish."""
    if len(v) < 2:
        return {}
    flags = struct.unpack("<H", v[:2])[0]
    i = 2
    out = {}

    def u16():
        nonlocal i
        x = struct.unpack("<H", v[i:i + 2])[0]
        i += 2
        return x

    def s16():
        nonlocal i
        x = struct.unpack("<h", v[i:i + 2])[0]
        i += 2
        return x

    def u24():
        nonlocal i
        x = v[i] | (v[i + 1] << 8) | (v[i + 2] << 16)
        i += 3
        return x

    def u8():
        nonlocal i
        x = v[i]
        i += 1
        return x

    try:
        # bit0 = More Data (0 => Instantaneous Speed IS present).
        if not (flags & 0x01):
            out["speed_kmh"] = round(u16() * 0.01, 2)
        if flags & 0x02:
            u16()  # average speed (skip)
        if flags & 0x04:
            out["distance_m"] = u24()
        if flags & 0x08:
            out["incline_pct"] = round(s16() * 0.1, 1)
            s16()  # ramp angle (0x7FFF = N/A); ignored
        if flags & 0x10:
            u16(); u16()  # positive/negative elevation gain (skip)
        if flags & 0x20:
            u8()  # instantaneous pace (skip)
        if flags & 0x40:
            u8()  # average pace (skip)
        # Remaining optional fields (energy, HR, MET, times) are not parsed:
        # once we have what we publish there is nothing after it we consume.
    except (IndexError, struct.error):
        # Partial/unexpected frame — publish whatever parsed cleanly so far.
        pass
    return out


# --- heartbeat / logging ----------------------------------------------------
def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


def beat():
    try:
        with open(HEARTBEAT, "w") as f:
            f.write(str(time.time()))
    except OSError as e:
        log(f"heartbeat write failed: {e}")


# --- MQTT -------------------------------------------------------------------
def build_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="treadmill-bridge")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.will_set(AVAIL_TOPIC, payload="offline", qos=1, retain=True)

    def on_connect(cl, userdata, flags, reason_code, properties):
        if reason_code == 0:
            log(f"mqtt connected {MQTT_HOST}:{MQTT_PORT}")
            publish_discovery(cl)
        else:
            log(f"mqtt connect failed: {reason_code}")

    client.on_connect = on_connect
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


def publish_discovery(client):
    """Retained HA MQTT-discovery configs. Re-published on every (re)connect."""
    for obj, spec in SENSORS.items():
        cfg = {
            "name": spec["name"],
            "unique_id": obj,
            "object_id": obj,
            "state_topic": state_topic(obj),
            "availability_topic": AVAIL_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": DEVICE,
        }
        for k in ("unit_of_measurement", "device_class", "state_class",
                  "icon", "suggested_display_precision"):
            if k in spec:
                cfg[k] = spec[k]
        client.publish(f"{DISCOVERY_PREFIX}/sensor/{obj}/config",
                       json.dumps(cfg), qos=1, retain=True)
    log("published MQTT discovery configs")


def state_topic(obj):
    # treadmill_speed -> kdk/treadmill/speed
    return f"{STATE_PREFIX}/{obj.split('_', 1)[1]}"


# --- main loop --------------------------------------------------------------
_running = True


def _stop(signum, frame):
    global _running
    _running = False
    log(f"signal {signum} — shutting down")


def stream_once(client):
    """One DIRCON connection: subscribe to 0x2ACD and publish until it drops."""
    s = socket.create_connection((TREADMILL_HOST, DIRCON_PORT), timeout=8)
    s.settimeout(8)
    log(f"dircon connected {TREADMILL_HOST}:{DIRCON_PORT}")
    seq = 0

    def req(ident, data=b""):
        nonlocal seq
        seq = (seq + 1) & 0xFF  # UNIQUE incrementing seq — server drops repeats
        s.sendall(pkt(ident, data, seq))

    req(DISCOVER_SERVICES)
    r = recv_pkt(s)
    if r is None:
        raise ConnectionError("no response to DiscoverServices")
    req(ENABLE_NOTIFY, TREADMILL_DATA + b"\x01")
    r = recv_pkt(s)
    if r is None:
        raise ConnectionError("no ack to EnableNotifications")
    log(f"subscribed to 0x2ACD (ack rc={r[2]})")

    client.publish(AVAIL_TOPIC, "online", qos=1, retain=True)
    last = {}
    while _running:
        beat()
        s.settimeout(30)  # a quiet gap longer than this = treat as dropped
        try:
            r = recv_pkt(s)
        except socket.timeout:
            raise ConnectionError("no notifications for 30s")
        if r is None:
            raise ConnectionError("connection closed by peer")
        ident, _rseq, _rc, data = r
        if ident != CHAR_NOTIFY or len(data) < 16:
            continue
        if data[:16] != TREADMILL_DATA:
            continue  # ignore any other characteristic (e.g. RSC 0x2A53)
        vals = parse_treadmill(data[16:])
        for obj, spec in SENSORS.items():
            v = vals.get(spec["field"])
            if v is None or last.get(obj) == v:
                continue  # publish only changed values (gentle on the recorder)
            client.publish(state_topic(obj), v, qos=0, retain=False)
            last[obj] = v
    s.close()


def main():
    if not TREADMILL_HOST:
        log("TREADMILL_HOST is not set — nothing to connect to")
        return 1
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    os.makedirs(STATE_DIR, exist_ok=True)
    beat()
    log(f"treadmill-bridge start (treadmill={TREADMILL_HOST}:{DIRCON_PORT}, "
        f"mqtt={MQTT_HOST}:{MQTT_PORT}, auth={'yes' if MQTT_USER else 'anon'})")

    client = build_mqtt()
    backoff = BACKOFF_START
    try:
        while _running:
            beat()
            try:
                stream_once(client)
                backoff = BACKOFF_START
            except (OSError, ConnectionError) as e:
                # DIRCON dropped (console reboot, app restart, belt powered off).
                # Mark unavailable, back off, and retry. The heartbeat above keeps
                # the container healthy across the retry.
                log(f"dircon error: {e}; retry in {backoff:.0f}s")
                try:
                    client.publish(AVAIL_TOPIC, "offline", qos=1, retain=True)
                except Exception:
                    pass
                slept = 0.0
                while _running and slept < backoff:
                    beat()
                    time.sleep(min(5.0, backoff - slept))
                    slept += 5.0
                backoff = min(BACKOFF_MAX, backoff * 2)
    finally:
        try:
            client.publish(AVAIL_TOPIC, "offline", qos=1, retain=True)
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
