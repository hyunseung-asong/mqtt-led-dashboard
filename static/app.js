// LED Light dashboard: live updates over Server-Sent Events, commands via POST.

const $ = (id) => document.getElementById(id);
const DARK_LUX = 150;          // same threshold as the firmware
const STALE_MS = 3000;         // device publishes every 0.5 s
const MODE_NAMES = {
  static: "Static", rainbow: "Rainbow", color: "Screen color",
  volume: "Volume", ambientvol: "Screen + volume", off: "Off",
};

let s = {};                    // latest known state
let lastSeenLocal = 0;         // browser time of the last device message

// ---------------------------------------------------------------- render
function render() {
  if (s.level !== undefined) {
    $("level").textContent = s.level;
    $("level-bar").style.width = Math.max(0, Math.min(100, s.level)) + "%";
  }
  if (s.ambient) {
    $("ambient").textContent = s.ambient.toUpperCase();
    $("ambient-swatch").style.background = s.ambient;
  }
  if (s.lux !== undefined) {
    $("lux").textContent = s.lux;
    $("lux-hint").textContent = s.lux < DARK_LUX ? "Dark, sensor reads as black" : " ";
  }
  const playing = s.music === "playing";
  $("music").textContent = playing ? "Music playing" : "Music stopped";
  $("music").classList.toggle("on", playing);

  if (s.state) $("state-text").textContent = "Now: " + (MODE_NAMES[s.state] || s.state);
  setActive("mode", s.state);
  setActive("power", s.power_state);
  if (s.breathe_state) $("breathe").checked = s.breathe_state === "on";

  renderStatus();
}

function setActive(topic, value) {
  if (!value) return;
  document.querySelectorAll(`.seg[data-topic="${topic}"] button`).forEach((b) =>
    b.classList.toggle("active", b.dataset.value === value));
}

function renderStatus() {
  const el = $("status"), txt = $("status-text");
  el.className = "status";
  if (!s.connected) { el.classList.add("offline"); txt.textContent = "Broker offline"; }
  else if (Date.now() - lastSeenLocal < STALE_MS) { el.classList.add("online"); txt.textContent = "Light online"; }
  else { el.classList.add("idle"); txt.textContent = "Waiting for light"; }
}
setInterval(renderStatus, 1000);

// ---------------------------------------------------------------- live stream
function connect() {
  const es = new EventSource("/events");
  es.onmessage = (e) => {
    const u = JSON.parse(e.data);
    if ("level" in u || "ambient" in u || "lux" in u) lastSeenLocal = Date.now();
    Object.assign(s, u);
    render();
  };
  es.onerror = () => { s.connected = false; renderStatus(); }; // EventSource retries itself
}
connect();

// ---------------------------------------------------------------- commands
async function send(topic, value) {
  try {
    const r = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, value }),
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error);
    return true;
  } catch (err) {
    toast("Not sent: " + err.message);
    return false;
  }
}

let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

// segmented buttons (mode, power)
document.querySelectorAll(".seg").forEach((seg) => {
  seg.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const topic = seg.dataset.topic, value = b.dataset.value;
    if (topic === "power" && value === "wall" &&
        !confirm("Switch to full brightness? Only do this on the 5 V 4 A wall adapter.")) return;
    setActive(topic, value); // optimistic; the device's retained state confirms
    await send(topic, value);
  });
});

// breathe
$("breathe").addEventListener("change", (e) => send("breathe", e.target.checked ? "on" : "off"));

// color
const picker = $("color-picker"), hex = $("color-hex");
const isHex = (v) => /^#[0-9a-f]{6}$/i.test(v);

picker.addEventListener("input", () => { hex.value = picker.value.toUpperCase(); });
hex.addEventListener("input", () => {
  let v = hex.value.trim();
  if (v && v[0] !== "#") v = "#" + v;
  if (isHex(v)) picker.value = v;
});

async function applyColor(v) {
  v = v.trim();
  if (v[0] !== "#") v = "#" + v;
  if (!isHex(v)) { toast("Use a hex color like #3A7BFF"); return; }
  v = v.toUpperCase();
  picker.value = v; hex.value = v;
  if (await send("color", v)) toast(s.state === "static" || s.state === "volume"
    ? "Color set to " + v : "Color saved. It shows in Static and Volume modes.");
}
$("color-apply").addEventListener("click", () => applyColor(hex.value));
hex.addEventListener("keydown", (e) => { if (e.key === "Enter") applyColor(hex.value); });
$("presets").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) applyColor(b.dataset.c);
});
