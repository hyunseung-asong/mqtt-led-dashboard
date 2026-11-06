"""
LED Light Dashboard - Flask + paho-mqtt
CMPE 286 MQTT audio-reactive LED light (XIAO ESP32-C3)

The Flask server keeps one MQTT connection to the broker. It caches the
latest value of every device topic and streams changes to the browser with
Server-Sent Events (/events). Controls in the page POST to /api/command,
and the server publishes them to the device's command topics.

Everything is also recorded in logs/ledlight.db (SQLite):
  readings  one row per level message (~2/s): level, lux, ambient, music, mode
  events    commands sent, light state changes (with response time), system events
A text log is written to logs/app.log. The Logs page (/logs) shows both.

Run:  python app.py   then open http://localhost:5000
"""

import csv
import io
import json
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler

import paho.mqtt.client as mqtt
from flask import Flask, Response, jsonify, render_template, request

# ---------------------------------------------------------------- settings
BROKER = os.environ.get("MQTT_BROKER", "broker.hivemq.com")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
PREFIX = os.environ.get("MQTT_PREFIX", "andrew286/leds")
# Must differ from the XIAO's client ID, or the broker kicks one of them off.
CLIENT_ID = f"andrew286-flask-{uuid.uuid4().hex[:8]}"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(LOG_DIR, "ledlight.db")
SILENT_AFTER_S = 5  # light counts as offline after this long with no readings

# Topics the device publishes (relative to PREFIX)
STATUS_TOPICS = [
    "level",         # 0-100, every 0.5 s
    "ambient",       # "#RRGGBB" sensed screen color, every 0.5 s
    "lux",           # raw clear channel, every 0.5 s
    "state",         # retained: static|rainbow|color|volume|ambientvol|off
    "power/state",   # retained: usb|wall
    "breathe/state", # retained: on|off
    "music",         # retained: playing|stopped
]
STATE_KEYS = ("state", "power_state", "breathe_state", "music")

# Commands the device accepts: topic -> allowed payloads (None = validated below)
COMMANDS = {
    "mode": {"static", "rainbow", "color", "volume", "ambientvol", "off"},
    "power": {"usb", "wall"},
    "breathe": {"on", "off"},
    "color": None,  # "#RRGGBB"
}
# Which retained state topic confirms each command (for response time)
CONFIRMS = {"mode": "state", "power": "power_state", "breathe": "breathe_state"}

# ---------------------------------------------------------------- text log
os.makedirs(LOG_DIR, exist_ok=True)
log = logging.getLogger("ledlight")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
for _h in (logging.StreamHandler(),
           RotatingFileHandler(os.path.join(LOG_DIR, "app.log"),
                               maxBytes=1_000_000, backupCount=3, encoding="utf-8")):
    _h.setFormatter(_fmt)
    log.addHandler(_h)

# ---------------------------------------------------------------- database
db_lock = threading.Lock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS readings (
    ts REAL NOT NULL, level INTEGER, lux INTEGER, ambient TEXT, music TEXT, state TEXT);
CREATE INDEX IF NOT EXISTS readings_ts ON readings(ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
    kind TEXT NOT NULL,          -- command | light | system
    name TEXT NOT NULL, value TEXT, detail TEXT, latency_ms INTEGER);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
""")


def db_exec(sql, args=()):
    with db_lock:
        cur = db.execute(sql, args)
        db.commit()
        return cur.lastrowid


def db_query(sql, args=()):
    with db_lock:
        return [dict(r) for r in db.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- live state
state_lock = threading.Lock()
latest = {"connected": False, "last_seen": None}
pending = {}          # command topic -> (value, sent_ts), for response time
light_online = False
listeners = []        # one queue per open browser tab
listeners_lock = threading.Lock()


def push(msg_obj):
    msg = json.dumps(msg_obj)
    with listeners_lock:
        for q in list(listeners):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass  # a stalled tab just misses this frame


def broadcast(update):
    """Store a state update and push it to every connected browser."""
    with state_lock:
        latest.update(update)
    push(update)


def record_event(kind, name, value="", detail="", latency_ms=None):
    """Write an event to the database and the text log, and push it live."""
    ts = time.time()
    eid = db_exec(
        "INSERT INTO events (ts, kind, name, value, detail, latency_ms) VALUES (?,?,?,?,?,?)",
        (ts, kind, name, value, detail, latency_ms))
    log.info("%-7s %-14s %-12s %s", kind, name, value, detail)
    push({"_event": {"id": eid, "ts": ts, "kind": kind, "name": name, "value": value,
                     "detail": detail, "latency_ms": latency_ms}})


def valid_hex(s):
    return (
        len(s) == 7 and s[0] == "#"
        and all(c in "0123456789abcdefABCDEF" for c in s[1:])
    )


# ---------------------------------------------------------------- MQTT
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        client.subscribe([(f"{PREFIX}/{t}", 0) for t in STATUS_TOPICS])
        broadcast({"connected": True})
        record_event("system", "broker", "connected", f"{BROKER}:{PORT} as {CLIENT_ID}")
    else:
        record_event("system", "broker", "refused", str(reason_code))


def on_disconnect(client, userdata, flags, reason_code, properties):
    broadcast({"connected": False})
    record_event("system", "broker", "disconnected", f"{reason_code}, retrying")


def on_message(client, userdata, msg):
    global light_online
    key = msg.topic[len(PREFIX) + 1:].replace("/", "_")  # power/state -> power_state
    payload = msg.payload.decode(errors="replace").strip()
    now = time.time()

    if key in ("level", "lux"):
        try:
            payload = int(float(payload))
        except ValueError:
            return

    with state_lock:
        previous = latest.get(key)
    broadcast({key: payload, "last_seen": now})

    if key == "level":
        if not light_online:
            light_online = True
            record_event("system", "light", "online", "readings arriving")
        with state_lock:
            row = (now, payload, latest.get("lux"), latest.get("ambient"),
                   latest.get("music"), latest.get("state"))
        db_exec("INSERT INTO readings (ts, level, lux, ambient, music, state) VALUES (?,?,?,?,?,?)", row)

    elif key in STATE_KEYS and payload != previous:
        detail, latency = ("retained value" if msg.retain else ""), None
        for cmd, confirm_key in CONFIRMS.items():
            if confirm_key == key and cmd in pending:
                value, sent = pending[cmd]
                if value == payload:
                    latency = round((now - sent) * 1000)
                    detail = f"confirmed {latency} ms after command"
                    del pending[cmd]
        name = {"state": "mode", "power_state": "power", "breathe_state": "breathe"}.get(key, key)
        record_event("light", name, payload, detail, latency)


def watchdog():
    """Log when the light stops publishing readings."""
    global light_online
    while True:
        time.sleep(1)
        with state_lock:
            seen = latest.get("last_seen")
        if light_online and seen and time.time() - seen > SILENT_AFTER_S:
            light_online = False
            record_event("system", "light", "silent", f"no readings for {SILENT_AFTER_S} s")
        for cmd, (value, sent) in list(pending.items()):
            if time.time() - sent > 10:
                pending.pop(cmd, None)
                record_event("system", cmd, value, "no confirmation from light within 10 s")


mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
mqttc.on_connect = on_connect
mqttc.on_disconnect = on_disconnect
mqttc.on_message = on_message
mqttc.reconnect_delay_set(min_delay=1, max_delay=30)

# ---------------------------------------------------------------- Flask pages
app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)  # keep the console readable


@app.route("/")
def index():
    return render_template("index.html", prefix=PREFIX, broker=BROKER, page="control")


@app.route("/logs")
def logs_page():
    return render_template("logs.html", prefix=PREFIX, broker=BROKER, page="logs")


# ---------------------------------------------------------------- control API
@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify(latest)


@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(silent=True) or {}
    topic = str(data.get("topic", ""))
    value = str(data.get("value", "")).strip()

    if topic not in COMMANDS:
        return jsonify(ok=False, error="unknown command"), 400
    allowed = COMMANDS[topic]
    if allowed is not None and value not in allowed:
        return jsonify(ok=False, error="bad value"), 400
    if topic == "color":
        if not valid_hex(value):
            return jsonify(ok=False, error="color must be #RRGGBB"), 400
        value = value.upper()
    if not mqttc.is_connected():
        return jsonify(ok=False, error="not connected to broker"), 503

    if topic in CONFIRMS:
        pending[topic] = (value, time.time())
    mqttc.publish(f"{PREFIX}/{topic}", value, qos=0)
    record_event("command", topic, value, "sent from dashboard")
    return jsonify(ok=True)


# ---------------------------------------------------------------- logs API
def since(minutes):
    return 0 if minutes <= 0 else time.time() - minutes * 60


@app.route("/api/logs/events")
def api_events():
    limit = min(int(request.args.get("limit", 200)), 2000)
    kind = request.args.get("kind", "")
    if kind:
        rows = db_query("SELECT * FROM events WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
    else:
        rows = db_query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    return jsonify(rows)


@app.route("/api/logs/readings")
def api_readings():
    """Readings for the chart, averaged into at most ~300 time buckets."""
    t0 = since(float(request.args.get("minutes", 10)))
    span = db_query("SELECT MIN(ts) a, MAX(ts) b FROM readings WHERE ts >= ?", (t0,))[0]
    if span["a"] is None:
        return jsonify(bucket=0, points=[])
    start = t0 if t0 else span["a"]
    bucket = max(0.5, (time.time() - start) / 300)
    rows = db_query("""
        SELECT CAST(ts / ? AS INTEGER) * ? AS t, ROUND(AVG(level), 1) AS level,
               MAX(level) AS peak, ROUND(AVG(lux)) AS lux,
               MAX(music = 'playing') AS music, ambient
        FROM readings WHERE ts >= ? GROUP BY CAST(ts / ? AS INTEGER) ORDER BY t
    """, (bucket, bucket, t0, bucket))
    return jsonify(bucket=bucket, start=start, end=time.time(), points=rows)


@app.route("/api/logs/stats")
def api_stats():
    t0 = since(float(request.args.get("minutes", 10)))
    r = db_query("""
        SELECT COUNT(*) n, ROUND(AVG(level), 1) avg_level, MAX(level) peak_level,
               ROUND(AVG(lux)) avg_lux, SUM(music = 'playing') music_n,
               MIN(ts) first_ts, MAX(ts) last_ts
        FROM readings WHERE ts >= ?""", (t0,))[0]
    modes = db_query("""SELECT COALESCE(state, 'unknown') state, COUNT(*) n
                        FROM readings WHERE ts >= ? GROUP BY state ORDER BY n DESC""", (t0,))
    c = db_query("""
        SELECT SUM(kind = 'command') commands,
               ROUND(AVG(latency_ms)) avg_latency, MIN(latency_ms) min_latency,
               MAX(latency_ms) max_latency, COUNT(latency_ms) confirmed
        FROM events WHERE ts >= ?""", (t0,))[0]
    n = r["n"] or 0
    return jsonify(
        samples=n,
        avg_level=r["avg_level"], peak_level=r["peak_level"], avg_lux=r["avg_lux"],
        music_pct=round(100 * (r["music_n"] or 0) / n, 1) if n else None,
        first_ts=r["first_ts"], last_ts=r["last_ts"],
        modes=[{"state": m["state"], "pct": round(100 * m["n"] / n, 1)} for m in modes] if n else [],
        commands=c["commands"] or 0, confirmed=c["confirmed"] or 0,
        avg_latency=c["avg_latency"], min_latency=c["min_latency"], max_latency=c["max_latency"],
    )


def csv_response(rows, columns, filename):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time"] + columns)
    for row in rows:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"]))
        w.writerow([f"{stamp}.{int(row['ts'] % 1 * 1000):03d}"] + [row[c] for c in columns])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/api/logs/readings.csv")
def export_readings():
    rows = db_query("SELECT * FROM readings ORDER BY ts")
    return csv_response(rows, ["level", "lux", "ambient", "music", "state"], "readings.csv")


@app.route("/api/logs/events.csv")
def export_events():
    rows = db_query("SELECT * FROM events ORDER BY id")
    return csv_response(rows, ["kind", "name", "value", "detail", "latency_ms"], "events.csv")


@app.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    db_exec("DELETE FROM readings")
    db_exec("DELETE FROM events")
    record_event("system", "logs", "cleared", "all recorded data deleted")
    return jsonify(ok=True)


# ---------------------------------------------------------------- live stream
@app.route("/events")
def events():
    """Server-Sent Events stream: a full snapshot first, then each change."""
    q = queue.Queue(maxsize=200)
    with listeners_lock:
        listeners.append(q)

    def stream():
        try:
            with state_lock:
                yield f"data: {json.dumps(latest)}\n\n"
            while True:
                try:
                    yield f"data: {q.get(timeout=15)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with listeners_lock:
                listeners.remove(q)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    record_event("system", "app", "started", f"logging to {DB_PATH}")
    threading.Thread(target=watchdog, daemon=True).start()
    mqttc.connect_async(BROKER, PORT, keepalive=30)
    mqttc.loop_start()
    # use_reloader=False: the reloader would start a second process and a
    # second MQTT client. threaded=True lets the SSE stream and API share it.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")),
            debug=False, use_reloader=False, threaded=True)
