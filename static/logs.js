// Logs page: summary stats, level history chart, mode share, live event log.
// Needs common.js.

const MODE_NAMES = {
  static: "Static", rainbow: "Rainbow", color: "Screen color",
  volume: "Volume", ambientvol: "Screen + volume", off: "Off", unknown: "Unknown",
};
const MODE_COLORS = {
  static: "#3a7bff", rainbow: "#af52de", color: "#30b158", volume: "#ff9500",
  ambientvol: "#00a3a3", off: "#8e8e93", unknown: "#c7c7cc",
};
const MAX_ROWS = 300;

let minutes = 30;
let kind = "";
let chartData = null;

// ---------------------------------------------------------------- helpers
const pad = (n) => String(n).padStart(2, "0");
function clock(ts, secs = true) {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}` + (secs ? `:${pad(d.getSeconds())}` : "");
}
function duration(sec) {
  if (sec < 60) return Math.round(sec) + " s";
  if (sec < 3600) return Math.floor(sec / 60) + " min " + pad(Math.round(sec % 60)) + " s";
  return Math.floor(sec / 3600) + " h " + pad(Math.floor((sec % 3600) / 60)) + " min";
}
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SVG = "http://www.w3.org/2000/svg";
function el(tag, attrs) {
  const e = document.createElementNS(SVG, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

// ---------------------------------------------------------------- stats
async function loadStats() {
  const st = await (await fetch(`/api/logs/stats?minutes=${minutes}`)).json();
  $("st-time").textContent = st.samples ? duration(st.samples * 0.5) : "–";
  $("st-samples").textContent = st.samples ? `${st.samples.toLocaleString()} readings` : "no readings";
  $("st-avg").textContent = st.avg_level ?? "–";
  $("st-peak").textContent = st.peak_level != null ? `peak ${st.peak_level}` : " ";
  $("st-cmd").textContent = st.commands;
  $("st-conf").textContent = st.commands ? `${st.confirmed} confirmed by light` : " ";
  $("st-lat").textContent = st.avg_latency != null ? st.avg_latency + " ms" : "–";
  $("st-latrange").textContent = st.avg_latency != null
    ? `range ${st.min_latency}–${st.max_latency} ms` : "command to light confirm";
  $("range-note").textContent = st.first_ts && minutes === 0
    ? `since ${new Date(st.first_ts * 1000).toLocaleString()}` : "";

  // mode share
  const share = $("share"), legend = $("share-legend");
  share.innerHTML = ""; legend.innerHTML = "";
  if (!st.modes.length) { share.innerHTML = '<div class="share-empty"></div>'; return; }
  st.modes.forEach((m) => {
    const c = MODE_COLORS[m.state] || MODE_COLORS.unknown;
    const seg = document.createElement("div");
    seg.style.cssText = `flex:${m.pct};background:${c}`;
    seg.title = `${MODE_NAMES[m.state] || m.state}: ${m.pct}%`;
    share.appendChild(seg);
    legend.insertAdjacentHTML("beforeend",
      `<span><i style="background:${c}"></i>${esc(MODE_NAMES[m.state] || m.state)} <b>${m.pct}%</b></span>`);
  });
}

// ---------------------------------------------------------------- chart
async function loadChart() {
  chartData = await (await fetch(`/api/logs/readings?minutes=${minutes}`)).json();
  drawChart();
}

function drawChart() {
  const svg = $("chart-svg"), strip = $("strip"), axis = $("axis");
  svg.innerHTML = ""; strip.innerHTML = ""; axis.innerHTML = "";
  const pts = chartData ? chartData.points : [];
  $("chart-empty").hidden = pts.length > 0;
  if (!pts.length) return;

  const W = svg.clientWidth, H = svg.clientHeight, L = 28, T = 8, B = 6;
  const t0 = chartData.start, t1 = chartData.end, span = Math.max(1, t1 - t0);
  const x = (t) => L + Math.max(0, (t - t0) / span) * (W - L);
  const y = (v) => T + (1 - v / 100) * (H - T - B);
  const gap = chartData.bucket * 3;   // break the line where the light was offline

  // grid
  [0, 25, 50, 75, 100].forEach((v) => {
    svg.appendChild(el("line", { x1: L, x2: W, y1: y(v), y2: y(v), class: "grid" }));
    const t = el("text", { x: L - 6, y: y(v) + 4, class: "ylab", "text-anchor": "end" });
    t.textContent = v; svg.appendChild(t);
  });

  const bw = Math.max(1, x(t0 + chartData.bucket) - x(t0));   // width of one bucket

  // level area + line, split at gaps
  let line = "", area = "", runStart = null, prev = null;
  const closeRun = () => { if (prev) area += `L${x(prev.t)},${y(0)}L${x(runStart.t)},${y(0)}Z`; };
  pts.forEach((p) => {
    if (!prev || p.t - prev.t > gap) {
      closeRun(); runStart = p;
      line += `M${x(p.t)},${y(p.level)}`; area += `M${x(p.t)},${y(p.level)}`;
    } else {
      line += `L${x(p.t)},${y(p.level)}`; area += `L${x(p.t)},${y(p.level)}`;
    }
    prev = p;
  });
  closeRun();
  svg.appendChild(el("path", { d: area, class: "area" }));
  svg.appendChild(el("path", { d: line, class: "line" }));
  svg.appendChild(el("line", { x1: 0, x2: 0, y1: T, y2: H - B, class: "cursor", id: "cursor" }));

  // screen color strip (same x scale)
  strip.style.marginLeft = L + "px";
  pts.forEach((p) => {
    if (!p.ambient) return;
    const d = document.createElement("div");
    d.style.cssText = `left:${x(p.t) - L}px;width:${bw + 0.5}px;background:${p.ambient}`;
    strip.appendChild(d);
  });

  // time axis
  const secs = span < 600, ticks = W < 500 ? 2 : 4;
  for (let i = 0; i <= ticks; i++) {
    const t = t0 + (span * i) / ticks;
    const s = document.createElement("span");
    s.style.left = x(t) + "px";
    s.textContent = i === ticks ? "now" : clock(t, secs);
    axis.appendChild(s);
  }
}

// hover tooltip
$("chart").addEventListener("mousemove", (e) => {
  if (!chartData || !chartData.points.length) return;
  const svg = $("chart-svg"), r = svg.getBoundingClientRect(), L = 28;
  const t = chartData.start + ((e.clientX - r.left - L) / (r.width - L)) * (chartData.end - chartData.start);
  let best = null;
  for (const p of chartData.points) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  if (!best || Math.abs(best.t - t) > chartData.bucket * 3) { $("tip").style.opacity = 0; return; }
  const px = L + ((best.t - chartData.start) / (chartData.end - chartData.start)) * (r.width - L);
  const c = $("cursor"); c.setAttribute("x1", px); c.setAttribute("x2", px); c.style.opacity = 1;
  const tip = $("tip");
  tip.innerHTML = `<b>${clock(best.t)}</b> · level ${best.level} (peak ${best.peak})` +
    (best.ambient ? ` <i style="background:${best.ambient}"></i>` : "");
  tip.style.opacity = 1;
  tip.style.left = Math.min(Math.max(px, 90), r.width - 90) + "px";
});
$("chart").addEventListener("mouseleave", () => {
  $("tip").style.opacity = 0;
  const c = $("cursor"); if (c) c.style.opacity = 0;
});
window.addEventListener("resize", drawChart);

// ---------------------------------------------------------------- event log
function rowHtml(ev) {
  const d = new Date(ev.ts * 1000);
  const date = d.toDateString() === new Date().toDateString() ? "" : d.toLocaleDateString() + " ";
  return `<tr class="new">
    <td class="mono nowrap">${date}${clock(ev.ts)}</td>
    <td><span class="kind kind-${esc(ev.kind)}">${esc(ev.kind)}</span></td>
    <td class="nowrap">${esc(ev.name)} <b>${esc(ev.value)}</b></td>
    <td class="muted">${esc(ev.detail)}</td></tr>`;
}

async function loadEvents() {
  const rows = await (await fetch(`/api/logs/events?limit=${MAX_ROWS}&kind=${kind}`)).json();
  $("log-body").innerHTML = rows.map(rowHtml).join("").replaceAll(' class="new"', "");
  $("log-empty").hidden = rows.length > 0;
}

eventHandlers.push((ev) => {
  if (kind && ev.kind !== kind) return;
  const body = $("log-body");
  body.insertAdjacentHTML("afterbegin", rowHtml(ev));
  while (body.rows.length > MAX_ROWS) body.deleteRow(-1);
  $("log-empty").hidden = true;
  if (ev.kind !== "light" || ev.latency_ms != null) loadStats();
});

// ---------------------------------------------------------------- controls
$("range").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  $("range").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  minutes = Number(b.dataset.min);
  loadStats(); loadChart();
});
$("filter").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  $("filter").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
  kind = b.dataset.kind;
  loadEvents();
});
$("clear").addEventListener("click", async () => {
  if (!confirm("Delete all recorded readings and events? Download the CSVs first if you need them.")) return;
  await fetch("/api/logs/clear", { method: "POST" });
  toast("Logs cleared");
  loadStats(); loadChart(); loadEvents();
});

loadStats(); loadChart(); loadEvents();
setInterval(() => { loadStats(); loadChart(); }, 5000);
