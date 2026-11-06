#include <FastLED.h>
#include <Wire.h>
#include <Adafruit_TCS34725.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoOTA.h>
 
// ---------- Settings ----------
#include "secrets.h"   // WIFI_SSID, WIFI_PASS (git-ignored; see secrets.example.h)
const char* BROKER    = "broker.hivemq.com";
#define PREFIX "andrew286/leds"
 
#define LED_PIN    D10
#define MIC_PIN    A1
#define BUTTON_PIN 9      // the BOOT button
#define NUM_LEDS   60
 
#define QUIET       900   // mic swing that counts as silence (from Step 4)
#define LOUD        3300  // mic swing that counts as full volume (from Step 4)
 
#define WR   1200  // white calibration from Step 7
#define WG   1300
#define WB   1100
#define DARK 150   // Clear value with the monitor off
 
// ---------- Power modes ----------
// Every lighting mode scales its brightness from 0 up to the current power mode's cap.
#define USB_MAX_BRIGHT  40     // brightness cap on USB (0 to 255)
#define USB_MAX_MA      400    // safety net: FastLED never draws more than this on USB
#define WALL_MAX_BRIGHT 255    // full brightness on the wall adapter
#define WALL_MAX_MA     3500   // safety net for the 4 A adapter
 
// ---------- Lighting modes ----------
// static     : one color, full brightness (breathe toggle pulses it 3 s up, 3 s down)
// rainbow    : whole strip slowly fades through the rainbow
// color      : shows the color the sensor sees on the screen
// volume     : static color, brightness follows the volume
// ambientvol : sensed screen color, brightness follows the volume
// off        : all LEDs off
const char* MODES[] = {"static", "rainbow", "color", "volume", "ambientvol", "off"};
const int NUM_MODES = sizeof(MODES) / sizeof(MODES[0]);
 
// ---------- State ----------
CRGB leds[NUM_LEDS];
Adafruit_TCS34725 tcs(TCS34725_INTEGRATIONTIME_50MS, TCS34725_GAIN_16X);
WiFiClient net;
PubSubClient mqtt(net);
 
String mode = "static";
bool wallPower = false;                  // always starts in USB mode, the safe choice
bool breathe = false;
CRGB staticColor = CRGB(255, 140, 60);   // warm white until the dashboard sets one
 
float volume = 0;           // smoothed volume, 0.0 to 1.0
int   soundPercent = 0;     // volume as 0 to 100, for the dashboard
 
CRGB     sensed = CRGB::Black;   // latest color from the sensor, at full value
uint16_t lastClear = 0;          // latest Clear (total light) reading
CRGB     shown = CRGB::Black;    // sensed color currently on the strip, fades toward sensed
 
uint8_t hue = 0;
bool otaStarted = false;
unsigned long lastPublish = 0, lastTry = 0, lastSensor = 0, lastHueStep = 0;
unsigned long pressStart = 0;
bool buttonWasDown = false, longPressDone = false;
 
uint8_t maxBright() { return wallPower ? WALL_MAX_BRIGHT : USB_MAX_BRIGHT; }
 
// ---------- Microphone ----------
int readSwing() {
  int lo = 4095, hi = 0;
  unsigned long start = millis();
  while (millis() - start < 20) {
    int v = analogRead(MIC_PIN);
    lo = min(lo, v);
    hi = max(hi, v);
  }
  return hi - lo;
}
 
void updateVolume() {
  int swing = readSwing();
  float target = constrain((float)(swing - QUIET) / (LOUD - QUIET), 0.0f, 1.0f);
  volume = (target > volume) ? target : volume * 0.92;   // jump up fast, fall back slowly
  soundPercent = volume * 100;
}
 
// ---------- Color sensor (read every 250 ms; each read takes about 50 ms) ----------
void updateSensor() {
  if (millis() - lastSensor < 250) return;
  lastSensor = millis();
  uint16_t r, g, b, c;
  tcs.getRawData(&r, &g, &b, &c);
  lastClear = c;
  if (c < DARK) {
    sensed = CRGB(255, 120, 40);               // screen off or dark: warm white
    return;
  }
  float fr = (float)r / WR, fg = (float)g / WG, fb = (float)b / WB;
  float m = max(fr, max(fg, fb));
  CRGB raw(255 * fr / m, 255 * fg / m, 255 * fb / m);
  CHSV hsv = rgb2hsv_approximate(raw);
  hsv.s = min(255, hsv.s * 2);                 // boost color; remove if it looks too strong
  hsv.v = 255;                                 // full value: brightness is set separately
  sensed = CRGB(hsv);
}
 
// ---------- Lighting modes: each fills the strip and returns its brightness ----------
uint8_t staticMode() {
  fill_solid(leds, NUM_LEDS, staticColor);
  if (!breathe) return maxBright();
  float phase = (millis() % 6000) / 6000.0;                 // 6 s cycle
  return maxBright() * (1 - cos(phase * 2 * PI)) / 2;       // 0 to max in 3 s, back in 3 s
}
 
uint8_t rainbowMode() {
  if (millis() - lastHueStep > 120) {                       // one hue step every 120 ms:
    lastHueStep = millis();                                 // a full rainbow in about 30 s
    hue++;
  }
  fill_solid(leds, NUM_LEDS, CRGB(CHSV(hue, 255, 255)));
  return maxBright();
}
 
uint8_t colorMode() {
  shown = blend(shown, sensed, 24);                         // fade instead of jump
  fill_solid(leds, NUM_LEDS, shown);
  return maxBright();
}
 
uint8_t volumeMode() {
  fill_solid(leds, NUM_LEDS, staticColor);
  return volume * maxBright();                              // quiet = 0, loud = max
}
 
uint8_t ambientVolMode() {
  shown = blend(shown, sensed, 24);
  fill_solid(leds, NUM_LEDS, shown);
  return volume * maxBright();
}
 
uint8_t offMode() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  return 0;
}
 
// ---------- MQTT ----------
void publishStatus() {
  if (!mqtt.connected()) return;
  mqtt.publish(PREFIX "/state", mode.c_str(), true);                     // retained
  mqtt.publish(PREFIX "/power/state", wallPower ? "wall" : "usb", true);
  mqtt.publish(PREFIX "/breathe/state", breathe ? "on" : "off", true);
}
 
void setMode(const String& m) {
  for (int i = 0; i < NUM_MODES; i++) {
    if (m == MODES[i]) {
      mode = m;
      publishStatus();
      return;
    }
  }
  Serial.println("Unknown mode: " + m);                    // ignore anything else
}
 
void setPower(bool wall) {
  wallPower = wall;
  FastLED.setMaxPowerInVoltsAndMilliamps(5, wall ? WALL_MAX_MA : USB_MAX_MA);
  Serial.println(wall ? "Power mode: wall" : "Power mode: USB");
  publishStatus();
}
 
bool isOn(const String& s) { return s == "on" || s == "true" || s == "1"; }
 
void onMessage(char* topic, byte* payload, unsigned int len) {
  String msg;
  for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  msg.trim();
  String t = topic;
  if (t == PREFIX "/mode") {
    setMode(msg);
  } else if (t == PREFIX "/color" && msg.startsWith("#")) {
    staticColor = CRGB((uint32_t)strtoul(msg.c_str() + 1, nullptr, 16));
  } else if (t == PREFIX "/breathe") {
    breathe = isOn(msg);
    publishStatus();
  } else if (t == PREFIX "/power") {
    if (msg == "wall") setPower(true);
    else if (msg == "usb") setPower(false);
  }
}
 
void connectMqtt() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  String id = "leds-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  if (mqtt.connect(id.c_str())) {
    mqtt.subscribe(PREFIX "/mode", 1);
    mqtt.subscribe(PREFIX "/color", 1);
    mqtt.subscribe(PREFIX "/breathe", 1);
    mqtt.subscribe(PREFIX "/power", 1);
    publishStatus();
    Serial.println("MQTT connected");
  }
}
 
void publishData() {
  if (!mqtt.connected() || millis() - lastPublish < 500) return;   // twice a second
  lastPublish = millis();
  char hex[8];
  snprintf(hex, sizeof(hex), "#%02X%02X%02X", sensed.r, sensed.g, sensed.b);
  mqtt.publish(PREFIX "/level", String(soundPercent).c_str());
  mqtt.publish(PREFIX "/ambient", hex);
  mqtt.publish(PREFIX "/lux", String(lastClear).c_str());
}
 
// ---------- BOOT button: tap = next lighting mode, hold 2 s = switch power mode ----------
void checkButton() {
  bool down = digitalRead(BUTTON_PIN) == LOW;
  if (down && !buttonWasDown) pressStart = millis();
  if (down && !longPressDone && millis() - pressStart > 2000) {
    setPower(!wallPower);
    longPressDone = true;
  }
  if (!down && buttonWasDown) {
    if (!longPressDone && millis() - pressStart > 30) {   // a tap, not switch bounce
      int next = 0;
      for (int i = 0; i < NUM_MODES; i++) {
        if (mode == MODES[i]) next = (i + 1) % NUM_MODES;
      }
      setMode(MODES[next]);
    }
    longPressDone = false;
  }
  buttonWasDown = down;
}
 
void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
  setPower(false);                       // start in USB mode
  Wire.begin(D4, D5);
  if (!tcs.begin()) Serial.println("Sensor not found - check wiring");
 
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  mqtt.setServer(BROKER, 1883);
  mqtt.setCallback(onMessage);
}
 
void loop() {
  // Network: OTA starts once Wi-Fi is up; MQTT retries every 5 s without blocking the LEDs
  if (WiFi.status() == WL_CONNECTED && !otaStarted) {
    ArduinoOTA.setHostname("leds");
    ArduinoOTA.begin();
    otaStarted = true;
    Serial.println("Wi-Fi connected, IP " + WiFi.localIP().toString());
  }
  if (otaStarted) ArduinoOTA.handle();
  if (!mqtt.connected() && millis() - lastTry > 5000) {
    lastTry = millis();
    connectMqtt();
  }
  mqtt.loop();
 
  // Sensing
  checkButton();
  updateVolume();
  updateSensor();
 
  // Draw the current mode, then apply its brightness
  uint8_t bright;
  if      (mode == "static")     bright = staticMode();
  else if (mode == "rainbow")    bright = rainbowMode();
  else if (mode == "color")      bright = colorMode();
  else if (mode == "volume")     bright = volumeMode();
  else if (mode == "ambientvol") bright = ambientVolMode();
  else                           bright = offMode();
  FastLED.setBrightness(bright);
  FastLED.show();
 
  publishData();
}
 