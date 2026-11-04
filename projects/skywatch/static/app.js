const canvas = document.getElementById("radar");
const ctx = canvas.getContext("2d");
const contactsEl = document.getElementById("contacts");
const priorityEl = document.getElementById("priority");
const statusEl = document.getElementById("status");
const sourceEl = document.getElementById("source");
const logEl = document.getElementById("log");
const logStatsEl = document.getElementById("logStats");

let state = { aircraft: [], receiver: { lat: 36.77, lon: -114.129 }, stats: {} };
let sweep = 0;
const trails = new Map();

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * dpr);
  canvas.height = Math.floor(rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

window.addEventListener("resize", resizeCanvas);
resizeCanvas();

function fmt(v, suffix = "") {
  return v === null || v === undefined || v === "" ? "--" : `${v}${suffix}`;
}

function aircraftLabel(ac) {
  return ac.flight || ac.hex.toUpperCase();
}

function project(ac, w, h) {
  if (ac.lat == null || ac.lon == null) return null;
  const latNm = (ac.lat - state.receiver.lat) * 60;
  const lonNm = (ac.lon - state.receiver.lon) * 60 * Math.cos(state.receiver.lat * Math.PI / 180);
  const maxNm = 180;
  const radius = Math.min(w, h) * 0.43;
  return {
    x: w / 2 + (lonNm / maxNm) * radius,
    y: h / 2 - (latNm / maxNm) * radius,
    r: Math.hypot(latNm, lonNm)
  };
}

function drawRadar() {
  const rect = canvas.getBoundingClientRect();
  const w = rect.width;
  const h = rect.height;
  ctx.clearRect(0, 0, w, h);

  const cx = w / 2;
  const cy = h / 2;
  const radius = Math.min(w, h) * 0.43;

  ctx.fillStyle = "#030703";
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = "rgba(76,255,136,.18)";
  ctx.lineWidth = 1;
  for (let r = radius / 4; r <= radius; r += radius / 4) {
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
  }
  for (let a = 0; a < Math.PI * 2; a += Math.PI / 8) {
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(a) * radius, cy + Math.sin(a) * radius);
    ctx.stroke();
  }

  ctx.fillStyle = "rgba(76,255,136,.85)";
  ctx.beginPath();
  ctx.arc(cx, cy, 4, 0, Math.PI * 2);
  ctx.fill();

  const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
  grad.addColorStop(0, "rgba(76,255,136,.18)");
  grad.addColorStop(1, "rgba(76,255,136,0)");
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.arc(cx, cy, radius, sweep - 0.34, sweep + 0.02);
  ctx.closePath();
  ctx.fill();

  const now = Date.now();
  for (const ac of state.aircraft || []) {
    const p = project(ac, w, h);
    if (!p || p.r > 180) continue;
    const trail = trails.get(ac.hex) || [];
    trail.push({ x: p.x, y: p.y, t: now });
    trails.set(ac.hex, trail.filter(pt => now - pt.t < 120000).slice(-45));

    ctx.strokeStyle = "rgba(76,255,136,.32)";
    ctx.beginPath();
    for (const [i, pt] of trails.get(ac.hex).entries()) {
      if (i === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    }
    ctx.stroke();

    const hot = ac.emergency && ac.emergency !== "none";
    ctx.fillStyle = hot ? "#ff5c70" : altitudeColor(ac.altitude);
    ctx.beginPath();
    ctx.arc(p.x, p.y, hot ? 5 : 3.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = hot ? "#ffb3bf" : "#b8ffd0";
    ctx.font = "12px Consolas, monospace";
    ctx.fillText(aircraftLabel(ac), p.x + 8, p.y - 6);
  }

  sweep += 0.018;
  requestAnimationFrame(drawRadar);
}

function altitudeColor(alt) {
  if (alt == null) return "#4cff88";
  if (alt < 5000) return "#ffd166";
  if (alt > 30000) return "#72c7ff";
  return "#4cff88";
}

function renderContacts() {
  const aircraft = state.aircraft || [];
  document.getElementById("liveCount").textContent = state.stats?.aircraft ?? aircraft.length;
  document.getElementById("posCount").textContent = state.stats?.positioned ?? 0;
  document.getElementById("msgCount").textContent = state.messages ?? 0;
  sourceEl.textContent = state.source ? `SRC ${state.source}` : "";
  statusEl.textContent = state.ok ? `LINK OK ${new Date((state.updated || 0) * 1000).toLocaleTimeString()}` : `LINK ERROR ${state.error || ""}`;

  const farthest = state.stats?.farthest;
  const highest = state.stats?.highest;
  priorityEl.innerHTML = `
    <div class="callsign">${farthest ? aircraftLabel(farthest) : "NO TRACK"}</div>
    <div class="hex">${farthest ? farthest.hex.toUpperCase() : ""}</div>
    <div class="details">
      FAR ${farthest ? fmt(farthest.distance_nm, " nm") : "--"}<br>
      HIGH ${highest ? `${aircraftLabel(highest)} ${fmt(highest.altitude, " ft")}` : "--"}<br>
      EMERGENCY ${(state.stats?.emergency || []).length}
    </div>`;

  contactsEl.innerHTML = aircraft.slice(0, 80).map(ac => `
    <div class="contact">
      <div>
        <div class="callsign">${aircraftLabel(ac)}</div>
        <div class="hex">${ac.hex.toUpperCase()}</div>
      </div>
      <div class="pill ${ac.emergency && ac.emergency !== "none" ? "danger" : ""}">${fmt(ac.distance_nm, "nm")}</div>
      <div class="details">
        ALT ${fmt(ac.altitude, "ft")} | SPD ${fmt(ac.speed, "kt")} | HDG ${fmt(ac.track)} | RSSI ${fmt(ac.rssi)}
      </div>
    </div>
  `).join("");
}

async function refreshState() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    state = await res.json();
    renderContacts();
  } catch (err) {
    statusEl.textContent = `UI LINK ERROR ${err.message}`;
  }
}

async function refreshLog() {
  const q = document.getElementById("search").value.trim();
  const [logRes, statRes] = await Promise.all([
    fetch(`/api/log?limit=300&q=${encodeURIComponent(q)}`, { cache: "no-store" }),
    fetch("/api/stats", { cache: "no-store" })
  ]);
  const rows = await logRes.json();
  const stats = await statRes.json();
  logStatsEl.textContent = `${stats.unique_aircraft || 0} unique aircraft | ${stats.positions_logged || 0} positions logged`;
  logEl.innerHTML = rows.map(row => `
    <div class="log-row">
      <div>
        <div class="callsign">${row.flight || row.hex.toUpperCase()}</div>
        <div class="hex">${row.hex.toUpperCase()}</div>
      </div>
      <div class="pill">${new Date(row.last_seen * 1000).toLocaleTimeString()}</div>
      <div class="details">
        FIRST ${new Date(row.first_seen * 1000).toLocaleString()}<br>
        SEEN ${row.seen_count} | ALT ${fmt(row.last_altitude, "ft")} | FAR ${fmt(row.last_distance_nm, "nm")}
      </div>
    </div>
  `).join("");
}

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`${btn.dataset.view}View`).classList.add("active");
    if (btn.dataset.view === "log") refreshLog();
  });
});

document.getElementById("refreshLog").addEventListener("click", refreshLog);
document.getElementById("search").addEventListener("keydown", e => {
  if (e.key === "Enter") refreshLog();
});

drawRadar();
refreshState();
setInterval(refreshState, 2000);
setInterval(() => {
  if (document.getElementById("logView").classList.contains("active")) refreshLog();
}, 10000);
