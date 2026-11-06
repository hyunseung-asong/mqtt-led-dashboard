"""
LED Light Dashboard - Flask + paho-mqtt
CMPE 286 MQTT audio-reactive LED light (XIAO ESP32-C3)

The Flask server keeps one MQTT connection to the broker. It caches the
latest value of every device topic and streams changes to the browser with
Server-Sent Events (/events). Controls in the page POST to /api/command,
and the server publishes them to the device's command topics.

Run:  python app.py   then open http://localhost:5000
"""

import json
import os
import queue
import threading
import time
import uuid

import paho.mqtt.client as mqtt
from flask import Flask, Response, jsonify, render_template, request

# ---------------------------------------------------------------- settings
BROKER = os.environ.get("MQTT_BROKER", "broker.hivemq.com")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
PREFIX = os.environ.get("MQTT_PREFIX", "andrew286/leds")
# Must differ from the XIAO's client ID, or the broker kicks one of them off.
CLIENT_ID = f"andrew286-flask-{uuid.uuid4().hex[:8]}"

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

# Commands the device accepts: topic -> allowed payloads (None = validated below)
COMMANDS = {
    "mode": {"static", "rainbow", "color", "volume", "ambientvol", "off"},
    "power": {"usb", "wall"},
    "breathe": {"on", "off"},
    "color": None,  # "#RRGGBB"
}

# ---------------------------------------------------------------- state
state_lock = threading.Lock()
latest = {"connected": False, "last_seen": None}
listeners = []  # one queue per open browser tab
listeners_lock = threading.Lock()


def broadcast(update):
    """Store an update and push it to every connected browser."""
    with state_lock:
        latest.update(update)
    msg = json.dumps(update)
    with listeners_lock:
        for q in list(listeners):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass  # a stalled tab just misses this frame


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
        print(f"[mqtt] connected to {BROKER}:{PORT} as {CLIENT_ID}")
    else:
        print(f"[mqtt] connect failed: {reason_code}")


def on_disconnect(client, userdata, flags, reason_code, properties):
    broadcast({"connected": False})
    print(f"[mqtt] disconnected ({reason_code}), retrying...")


def on_message(client, userdata, msg):
    key = msg.topic[len(PREFIX) + 1:].replace("/", "_")  # power/state -> power_state
    payload = msg.payload.decode(errors="replace").strip()
    if key in ("level", "lux"):
        try:
            payload = int(float(payload))
        except ValueError:
            return
    broadcast({key: payload, "last_seen": time.time()})


mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
mqttc.on_connect = on_connect
mqttc.on_disconnect = on_disconnect
mqttc.on_message = on_message
mqttc.reconnect_delay_set(min_delay=1, max_delay=30)

# ---------------------------------------------------------------- Flask
app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", prefix=PREFIX, broker=BROKER)


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

    mqttc.publish(f"{PREFIX}/{topic}", value, qos=0)
    print(f"[cmd] {PREFIX}/{topic} <- {value}")
    return jsonify(ok=True)


@app.route("/events")
def events():
    """Server-Sent Events stream: a full snapshot first, then each change."""
    q = queue.Queue(maxsize=100)
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
    mqttc.connect_async(BROKER, PORT, keepalive=30)
    mqttc.loop_start()
    # use_reloader=False: the reloader would start a second process and a
    # second MQTT client. threaded=True lets the SSE stream and API share it.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")),
            debug=False, use_reloader=False, threaded=True)
