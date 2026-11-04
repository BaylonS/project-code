const state = {
  localHistory: [],
  piHistory: [],
  skyWindowMinutes: 5,
  skyData: null,
  skyBounds: null
};

const $ = (id) => document.getElementById(id);

function fmtPercent(value) {
  return Number.isFinite(value) ? `${Math.round(value)}%` : "--";
}

function fmtBytes(bytes) {
  if (!Number.isFinite(bytes)) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function fmtUptime(seconds) {
  if (!Number.isFinite(seconds)) return "--";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function fmtNumber(value) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString() : "--";
}

function fmtAltitude(value) {
  return Number.isFinite(value) ? `${Math.round(value / 100) * 100} ft` : "--";
}

function fmtSpeed(value) {
  return Number.isFinite(value) ? `${Math.round(value)} kt` : "--";
}

function setBar(id, value) {
  $(id).style.width = `${Math.max(0, Math.min(100, value || 0))}%`;
}

function setPill(id, text, tone) {
  const el = $(id);
  el.textContent = text;
  el.className = `pill ${tone}`;
}

function pushHistory(key, value) {
  if (!Number.isFinite(value)) return;
  state[key].push(value);
  if (state[key].length > 48) state[key].shift();
}

function drawSpark(canvasId, values, color) {
  const canvas = $(canvasId);
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const y = (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  if (values.length < 2) return;

  const step = width / (values.length - 1);
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();

  values.forEach((value, index) => {
    const x = index * step;
    const y = height - (Math.max(0, Math.min(100, value)) / 100) * (height - 12) - 6;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderDisks(id, disks) {
  const tbody = $(id);
  if (!Array.isArray(disks)) {
    tbody.innerHTML = `<tr><td colspan="3">${disks?.error || "Unavailable"}</td></tr>`;
    return;
  }
  tbody.innerHTML = disks.map((disk) => {
    const size = Number(disk.Size);
    const free = Number(disk.Free);
    const used = size - free;
    const label = `${disk.DeviceID || disk.mount || "Disk"} ${disk.VolumeName ? `(${disk.VolumeName})` : ""}`;
    return `<tr><td>${escapeHtml(label)}</td><td>${fmtBytes(used)}</td><td>${fmtBytes(free)}</td></tr>`;
  }).join("") || `<tr><td colspan="3">No disks found</td></tr>`;
}

function renderProcesses(processes) {
  const tbody = $("processes");
  if (!Array.isArray(processes)) {
    tbody.innerHTML = `<tr><td colspan="3">${processes?.error || "Unavailable"}</td></tr>`;
    return;
  }
  tbody.innerHTML = processes.map((proc) => (
    `<tr><td>${escapeHtml(proc.ProcessName || "process")} <span class="muted">#${proc.Id}</span></td><td>${proc.CPU ?? "--"}s</td><td>${fmtBytes(Number(proc.Memory))}</td></tr>`
  )).join("") || `<tr><td colspan="3">No process data</td></tr>`;
}

function renderLocal(local) {
  $("local-name").textContent = local.name || "This computer";
  $("local-state").textContent = local.ok ? "Online" : "Issue";
  setPill("local-pill", local.ok ? "Online" : "Issue", local.ok ? "ok" : "bad");
  $("local-cpu").textContent = fmtPercent(local.cpu?.percent);
  $("local-memory").textContent = fmtPercent(local.memory?.percent);
  $("local-uptime").textContent = fmtUptime(local.uptimeSeconds);
  setBar("local-cpu-bar", local.cpu?.percent);
  setBar("local-memory-bar", local.memory?.percent);
  pushHistory("localHistory", local.cpu?.percent);
  drawSpark("local-chart", state.localHistory, "#66a9ff");
  renderDisks("local-disks", local.disks);
  renderProcesses(local.processes);
}

function renderPi(pi) {
  $("pi-name").textContent = pi.name || pi.host || "dashpi";
  $("pi-state").textContent = pi.ok ? "Online" : "Needs SSH";
  setPill("pi-pill", pi.ok ? "Online" : "Needs SSH", pi.ok ? "ok" : "warn");

  const error = $("pi-error");
  if (!pi.ok) {
    $("pi-cpu").textContent = "--";
    $("pi-memory").textContent = "--";
    $("pi-temp").textContent = "--";
    setBar("pi-cpu-bar", 0);
    setBar("pi-memory-bar", 0);
    error.textContent = pi.error || "Pi data unavailable";
    error.classList.remove("hidden");
    $("pi-services").innerHTML = "";
    drawSpark("pi-chart", state.piHistory, "#f1b957");
    return;
  }

  error.classList.add("hidden");
  $("pi-cpu").textContent = fmtPercent(pi.cpu?.percent);
  $("pi-memory").textContent = fmtPercent(pi.memory?.percent);
  $("pi-temp").textContent = Number.isFinite(pi.cpu?.temperatureC) ? `${Math.round(pi.cpu.temperatureC)} deg C` : "--";
  setBar("pi-cpu-bar", pi.cpu?.percent);
  setBar("pi-memory-bar", pi.memory?.percent);
  pushHistory("piHistory", pi.cpu?.percent);
  drawSpark("pi-chart", state.piHistory, "#55d68b");

  $("pi-services").innerHTML = (pi.services || []).map((service) => (
    `<span class="chip">${escapeHtml(service.name)}: ${escapeHtml(service.status)}</span>`
  )).join("");
}

function renderAdsbStatus(adsb) {
  const ok = Boolean(adsb?.ok);
  const count = adsb?.adsb?.aircraftSeen;
  $("adsb-state").textContent = ok
    ? `${Number.isFinite(count) ? count : "--"} aircraft`
    : "Offline";
}

function renderStarCitizen(sc) {
  const status = sc.status || "Unknown";
  const tone = status === "Operational" ? "ok" : status === "Maintenance" ? "warn" : "bad";
  $("sc-state").textContent = status;
  $("sc-status").textContent = status;
  $("sc-status-block").className = `sc-status ${tone}`;
  $("sc-summary").textContent = sc.error || sc.summary || "";
  $("sc-components").innerHTML = (sc.components || []).map((component) => (
    `<span class="chip">${escapeHtml(component.name)}: ${escapeHtml(component.status)}</span>`
  )).join("");
}

function renderGrafana(grafana) {
  const ok = Boolean(grafana?.ok);
  const status = grafana?.status || "Unknown";
  $("grafana-state").textContent = status;
  $("grafana-title").textContent = grafana?.title || "Grafana";
  $("grafana-link").href = grafana?.url || $("grafana-link").href;
  setPill("grafana-pill", status, ok ? "ok" : "bad");
  $("grafana-latency").textContent = Number.isFinite(grafana?.latencyMs) ? `${grafana.latencyMs} ms` : "--";

  if (!ok) {
    $("grafana-summary").textContent = grafana?.error || "Grafana is not reachable from this computer.";
    return;
  }

  $("grafana-summary").textContent = grafana.frameBlocked
    ? `Reachable, but embedded display is blocked by Grafana (${grafana.frameReason}). Use Open Grafana for the full homelab hardware monitor.`
    : "Reachable and available for embedding.";
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function allTrackPoints(data) {
  const points = [];
  for (const trail of Object.values(data?.history || {})) {
    for (const point of trail) {
      if (Number.isFinite(point.lat) && Number.isFinite(point.lon)) points.push(point);
    }
  }
  for (const plane of data?.aircraft || []) {
    if (Number.isFinite(plane.lat) && Number.isFinite(plane.lon)) points.push(plane);
  }
  return points;
}

function boundsFor(data) {
  const points = allTrackPoints(data);
  if (!points.length) return state.skyBounds;

  let minLat = Infinity;
  let maxLat = -Infinity;
  let minLon = Infinity;
  let maxLon = -Infinity;
  for (const point of points) {
    minLat = Math.min(minLat, point.lat);
    maxLat = Math.max(maxLat, point.lat);
    minLon = Math.min(minLon, point.lon);
    maxLon = Math.max(maxLon, point.lon);
  }

  const latPad = Math.max(0.02, (maxLat - minLat) * 0.22);
  const lonPad = Math.max(0.02, (maxLon - minLon) * 0.22);
  state.skyBounds = {
    minLat: minLat - latPad,
    maxLat: maxLat + latPad,
    minLon: minLon - lonPad,
    maxLon: maxLon + lonPad
  };
  return state.skyBounds;
}

function bearingDistanceNm(fromLat, fromLon, toLat, toLon) {
  const rad = Math.PI / 180;
  const lat1 = fromLat * rad;
  const lat2 = toLat * rad;
  const dLat = (toLat - fromLat) * rad;
  const dLon = (toLon - fromLon) * rad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  const distanceNm = 3440.065 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  const bearing = (Math.atan2(y, x) / rad + 360) % 360;
  return { bearing, distanceNm };
}

function projectPoint(point, data, width, height) {
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.46;
  const receiver = data?.receiver;

  if (Number.isFinite(receiver?.lat) && Number.isFinite(receiver?.lon)) {
    const { bearing, distanceNm } = bearingDistanceNm(receiver.lat, receiver.lon, point.lat, point.lon);
    const altitudeNm = Number.isFinite(point.altitudeFt) ? Math.max(0, point.altitudeFt) / 6076.12 : null;
    const elevation = altitudeNm === null || distanceNm < 0.05
      ? null
      : Math.atan2(altitudeNm, distanceNm) * 180 / Math.PI;
    const radial = elevation === null
      ? Math.min(1, distanceNm / 60)
      : 1 - Math.max(0, Math.min(1, elevation / 85));
    const angle = bearing * Math.PI / 180;
    return {
      x: cx + Math.sin(angle) * radial * radius,
      y: cy - Math.cos(angle) * radial * radius,
      bearing,
      distanceNm,
      elevation
    };
  }

  const bounds = boundsFor(data);
  if (!bounds) return null;
  const lonRange = Math.max(0.0001, bounds.maxLon - bounds.minLon);
  const latRange = Math.max(0.0001, bounds.maxLat - bounds.minLat);
  return {
    x: ((point.lon - bounds.minLon) / lonRange) * width,
    y: height - ((point.lat - bounds.minLat) / latRange) * height,
    bearing: point.trackDeg || 0,
    distanceNm: null,
    elevation: null
  };
}

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(640, Math.floor(rect.width * dpr));
  const height = Math.max(420, Math.floor(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return { width, height };
}

function drawSkyBackground(ctx, width, height, hasReceiver) {
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.46;
  const sky = ctx.createRadialGradient(cx, cy, radius * 0.05, cx, cy, radius);
  sky.addColorStop(0, "#315f7a");
  sky.addColorStop(0.55, "#173244");
  sky.addColorStop(1, "#091116");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = "rgba(234, 248, 255, 0.18)";
  ctx.lineWidth = 1;
  for (const scale of [0.25, 0.5, 0.75, 1]) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius * scale, 0, Math.PI * 2);
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(234, 248, 255, 0.12)";
  for (let deg = 0; deg < 360; deg += 30) {
    const angle = deg * Math.PI / 180;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.sin(angle) * radius, cy - Math.cos(angle) * radius);
    ctx.stroke();
  }

  ctx.fillStyle = "rgba(234, 248, 255, 0.7)";
  ctx.font = `${Math.max(13, Math.round(width / 85))}px system-ui`;
  ctx.textAlign = "center";
  ctx.fillText("N", cx, cy - radius - 12);
  ctx.fillText("S", cx, cy + radius + 24);
  ctx.fillText("E", cx + radius + 18, cy + 5);
  ctx.fillText("W", cx - radius - 18, cy + 5);
  ctx.fillStyle = "rgba(234, 248, 255, 0.44)";
  ctx.fillText(hasReceiver ? "Zenith" : "Aircraft positions", cx, cy + 5);
}

function aircraftLabel(plane) {
  return plane.flight || plane.hex || "Unknown";
}

function drawAircraftMarker(ctx, x, y, heading, color) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate((Number.isFinite(heading) ? heading : 0) * Math.PI / 180);
  ctx.fillStyle = color;
  ctx.strokeStyle = "rgba(4, 10, 12, 0.85)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, -12);
  ctx.lineTo(8, 9);
  ctx.lineTo(0, 5);
  ctx.lineTo(-8, 9);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawSky() {
  const canvas = $("sky-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const { width, height } = setupCanvas(canvas);
  const data = state.skyData;
  const hasReceiver = Number.isFinite(data?.receiver?.lat) && Number.isFinite(data?.receiver?.lon);
  drawSkyBackground(ctx, width, height, hasReceiver);

  if (data) {
    const now = Date.now();
    const cutoff = now - state.skyWindowMinutes * 60 * 1000;
    const trails = data.history || {};

    for (const [hex, points] of Object.entries(trails)) {
      const recent = points.filter((point) => point.t >= cutoff);
      if (recent.length < 2) continue;

      ctx.lineWidth = 2;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      let started = false;
      for (const point of recent) {
        const projected = projectPoint(point, data, width, height);
        if (!projected) continue;
        if (!started) {
          ctx.moveTo(projected.x, projected.y);
          started = true;
        } else {
          ctx.lineTo(projected.x, projected.y);
        }
      }
      const newestAge = now - (recent.at(-1)?.t || now);
      const alpha = Math.max(0.16, 0.72 - newestAge / (state.skyWindowMinutes * 60 * 1000));
      ctx.strokeStyle = hex ? `rgba(117, 205, 255, ${alpha})` : "rgba(117, 205, 255, 0.35)";
      ctx.stroke();
    }

    const aircraft = (data.aircraft || [])
      .filter((plane) => Number.isFinite(plane.lat) && Number.isFinite(plane.lon))
      .sort((a, b) => (b.altitudeFt || 0) - (a.altitudeFt || 0));

    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (const plane of aircraft) {
      const projected = projectPoint(plane, data, width, height);
      if (!projected) continue;
      const fresh = !Number.isFinite(plane.seenSeconds) || plane.seenSeconds < 25;
      const color = fresh ? "#f6f0a8" : "#8db1c7";
      drawAircraftMarker(ctx, projected.x, projected.y, plane.trackDeg, color);
      ctx.fillStyle = fresh ? "rgba(246, 240, 168, 0.92)" : "rgba(196, 217, 226, 0.62)";
      ctx.font = `${Math.max(12, Math.round(width / 96))}px system-ui`;
      ctx.fillText(aircraftLabel(plane), projected.x + 12, projected.y - 9);
      ctx.fillStyle = "rgba(224, 238, 244, 0.55)";
      ctx.fillText(fmtAltitude(plane.altitudeFt), projected.x + 12, projected.y + 9);
    }

    const sweep = ((now / 4500) % 1) * Math.PI * 2;
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(width, height) * 0.46;
    ctx.strokeStyle = "rgba(246, 240, 168, 0.28)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.sin(sweep) * radius, cy - Math.cos(sweep) * radius);
    ctx.stroke();
  }

  requestAnimationFrame(drawSky);
}

function renderSky(data) {
  state.skyData = data;
  const ok = Boolean(data?.ok);
  const total = data?.summary?.total;
  $("sky-feed-state").textContent = ok ? "Live feed" : "Feed offline";
  $("sky-count").textContent = `${Number.isFinite(total) ? total : 0} aircraft`;
  $("sky-messages").textContent = fmtNumber(data?.messages);
  $("sky-tracked").textContent = fmtNumber(data?.summary?.positioned);
  $("sky-highest").textContent = data?.summary?.highest
    ? `${aircraftLabel(data.summary.highest)} ${fmtAltitude(data.summary.highest.altitudeFt)}`
    : "--";
  $("sky-fastest").textContent = data?.summary?.fastest
    ? `${aircraftLabel(data.summary.fastest)} ${fmtSpeed(data.summary.fastest.speedKt)}`
    : "--";

  const error = $("sky-error");
  if (!ok) {
    error.textContent = data?.error || "Aircraft feed is not reachable.";
    error.classList.remove("hidden");
  } else {
    error.classList.add("hidden");
  }

  const aircraft = [...(data?.aircraft || [])]
    .filter((plane) => Number.isFinite(plane.lat) && Number.isFinite(plane.lon))
    .sort((a, b) => (a.seenSeconds || 0) - (b.seenSeconds || 0))
    .slice(0, 8);

  $("aircraft-list").innerHTML = aircraft.map((plane) => (
    `<div class="aircraft-row">
      <strong>${escapeHtml(aircraftLabel(plane))}</strong>
      <span>${escapeHtml(plane.hex || "")}</span>
      <span>${fmtAltitude(plane.altitudeFt)} / ${fmtSpeed(plane.speedKt)}</span>
    </div>`
  )).join("") || `<div class="aircraft-row empty">No positioned aircraft in the current feed.</div>`;
}

async function refreshSky() {
  try {
    const response = await fetch("/api/adsb/sky", { cache: "no-store" });
    renderSky(await response.json());
  } catch (error) {
    renderSky({ ok: false, error: error.message, aircraft: [], history: {}, summary: { total: 0, positioned: 0 } });
  }
}

async function refresh() {
  $("refresh").disabled = true;
  try {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    const data = await response.json();
    renderLocal(data.local);
    renderPi(data.pi);
    renderAdsbStatus(data.adsb);
    renderStarCitizen(data.starCitizen);
    renderGrafana(data.grafana);
    $("last-updated").textContent = `Updated ${new Date(data.generatedAt).toLocaleTimeString()}`;
  } catch (error) {
    $("last-updated").textContent = `Update failed: ${error.message}`;
  } finally {
    $("refresh").disabled = false;
  }
}

$("refresh").addEventListener("click", refresh);
for (const button of document.querySelectorAll(".sky-window")) {
  button.addEventListener("click", () => {
    state.skyWindowMinutes = Number(button.dataset.window) || 5;
    for (const other of document.querySelectorAll(".sky-window")) {
      other.classList.toggle("active", other === button);
    }
  });
}
refresh();
refreshSky();
drawSky();
setInterval(refresh, 15000);
setInterval(refreshSky, 2500);
