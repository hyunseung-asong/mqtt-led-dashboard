// Shared by both pages: live stream from the server, status chip, toast.

const $ = (id) => document.getElementById(id);
const STALE_MS = 3000;          // light publishes every 0.5 s
const s = {};                   // latest known state
let lastSeenLocal = 0;          // browser time of the last light reading
const stateHandlers = [];       // called with each state update
const eventHandlers = [];       // called with each new log event

function renderStatus() {
  const el = $("status"), txt = $("status-text");
  el.className = "status";
  if (!s.connected) { el.classList.add("offline"); txt.textContent = "Broker offline"; }
  else if (Date.now() - lastSeenLocal < STALE_MS) { el.classList.add("online"); txt.textContent = "Light online"; }
  else { el.classList.add("idle"); txt.textContent = "Waiting for light"; }
}
setInterval(renderStatus, 1000);

function connectStream() {
  const es = new EventSource("/events");
  es.onmessage = (e) => {
    const u = JSON.parse(e.data);
    if (u._event) { eventHandlers.forEach((f) => f(u._event)); return; }
    if ("level" in u || "ambient" in u || "lux" in u) lastSeenLocal = Date.now();
    Object.assign(s, u);
    stateHandlers.forEach((f) => f(u));
    renderStatus();
  };
  es.onerror = () => { s.connected = false; renderStatus(); }; // EventSource retries itself
}

let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

// start after page scripts register their handlers
window.addEventListener("DOMContentLoaded", connectStream);
