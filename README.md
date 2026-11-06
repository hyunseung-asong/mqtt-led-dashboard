# LED Light dashboard (Flask + paho-mqtt)

A small web dashboard for the CMPE 286 audio-reactive LED light. It runs on your
PC, talks to the light through the same MQTT broker, and works next to the
Node-RED dashboard (both can be open at once).

## Run it

```bash
cd flask-dashboard
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
| Music pill | `music` (playing / stopped) | light → dashboard |
| Mode buttons | sends `mode`, shows `state` | both |
| Power (USB / Wall adapter) | sends `power`, shows `power/state` | both |
| Breathe switch | sends `breathe`, shows `breathe/state` | both |
| Static color picker + presets | sends `color` (#RRGGBB) | dashboard → light |

The status chip in the corner says **Light online** when readings arrived in the
last 3 seconds, **Waiting for light** when the broker is connected but the light
is silent, and **Broker offline** when the server lost the broker.

Switching to **Wall adapter** asks for confirmation, since it lifts the
brightness cap from 40 to 255.

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
app.py               Flask server + MQTT client
templates/index.html page layout
static/style.css     styling (follows the system light/dark setting)
static/app.js        live updates and controls
requirements.txt
```
