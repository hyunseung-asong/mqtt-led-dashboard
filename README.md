# LED Light dashboard (Flask + paho-mqtt)

A small web dashboard for the CMPE 286 audio-reactive LED light. It runs on your
PC, talks to the light through the same MQTT broker, and works next to the
Node-RED dashboard (both can be open at once).

## Run it

```bash
cd mqtt-led-dashboard
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000. To use it from your phone on the same Wi-Fi, open
`http://<your-laptop-IP>:5000`.

## What it shows and controls

| Section | MQTT topic (under `andrew286/leds/`) | Direction |
|---|---|---|
| Sound level bar | `level` (0-100) | light → dashboard |
| Screen color swatch | `ambient` (#RRGGBB) | light → dashboard |
| Light reading (dark under 150) | `lux` | light → dashboard |
| Mode buttons | sends `mode`, shows `state` | both |
| Power (USB / Wall adapter) | sends `power`, shows `power/state` | both |
| Breathe switch | sends `breathe`, shows `breathe/state` | both |
| Static color picker + presets | sends `color` (#RRGGBB) | dashboard → light |

The status chip in the corner says **Light online** when readings arrived in the
last 3 seconds, **Waiting for light** when the broker is connected but the light
is silent, and **Broker offline** when the server lost the broker.

Switching to **Wall adapter** asks for confirmation, since it lifts the
brightness cap from 40 to 255.

## Logs page

Everything the app sees is recorded while it runs, in `logs/` next to `app.py`:

- `logs/ledlight.db` (SQLite) has two tables. `readings` gets one row per level
  message (about 2 per second) with level, lux, screen color and mode.
  `events` gets every command you send, every change the light reports, and
  system events (broker connected or lost, light online or silent).
- `logs/app.log` is the same events as plain text (also printed in the terminal).

Open the **Logs** tab (http://localhost:5000/logs) to see:

- **Summary** for the chosen range (5 min, 30 min, 2 hours, All): recorded time,
  average and peak sound level, commands sent, and average response time.
- **Sound level chart** with a strip of the sensed screen color underneath. Hover for exact values.
- **Time in each mode** as a bar.
- **Event log**, updating live, filterable by Commands, Light or System.
- **Download readings CSV / events CSV**, and **Clear logs** to start fresh
  (for example right before a demo).

**Response time** is measured from the moment the dashboard publishes a mode,
power or breathe command until the light publishes the matching state back
through the broker. It's the full round trip (laptop → broker → light →
broker → laptop), so it's a good number for the Results slide. Color has no
echo from the light, so it isn't timed.

## Settings (optional environment variables)

| Variable | Default |
|---|---|
| `MQTT_BROKER` | `broker.hivemq.com` |
| `MQTT_PORT` | `1883` |
| `MQTT_PREFIX` | `andrew286/leds` |
| `PORT` (web) | `5000` |

## How it works

- `app.py` starts one paho-mqtt client in a background thread (`loop_start`),
  subscribes to the light's status topics, and keeps the latest value of each.
- The page opens `/events` (Server-Sent Events). The server sends a full
  snapshot, then every change as it arrives, so the page updates about twice a
  second without polling.
- Buttons POST `{"topic": ..., "value": ...}` to `/api/command`. The server
  checks the value against the allowed set and publishes it. The light then
  publishes its retained state, which confirms the change on the page.
- The MQTT client ID is random (`andrew286-flask-xxxxxxxx`) so it never collides
  with the XIAO's ID. Flask runs with `use_reloader=False` so only one MQTT
  client starts.

## Files

```
app.py                Flask server, MQTT client, SQLite logging
templates/_header.html  title, status and tabs shared by both pages
templates/index.html  Control page
templates/logs.html   Logs page
static/common.js      live stream and status shared by both pages
static/control.js     Control page controls
static/logs.js        Logs page chart, stats and event log
static/style.css      styling (follows the system light/dark setting)
logs/                 created on first run: ledlight.db, app.log
requirements.txt
```
