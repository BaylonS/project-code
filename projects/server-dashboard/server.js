import http from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.DASHBOARD_PORT || 4177);
const PI_HOST = process.env.DASHBOARD_PI_HOST || "dashpi@127.0.0.1";
const ADSB_HOST = process.env.DASHBOARD_ADSB_HOST || "pi@127.0.0.1";
const ADSB_AIRCRAFT_URL = process.env.DASHBOARD_ADSB_AIRCRAFT_URL || "http://127.0.0.1/tar1090/data/aircraft.json";
const ADSB_RECEIVER_LAT = Number.parseFloat(process.env.DASHBOARD_ADSB_RECEIVER_LAT || "");
const ADSB_RECEIVER_LON = Number.parseFloat(process.env.DASHBOARD_ADSB_RECEIVER_LON || "");
const SC_STATUS_URL = "https://status.robertsspaceindustries.com/";
const GRAFANA_URL = process.env.DASHBOARD_GRAFANA_URL || "http://127.0.0.1:3000/d/homelab-hardware/homelab-hardware-monitor?orgId=1&from=now-1h&to=now&timezone=browser&refresh=10s";
const ADSB_HTTP_TIMEOUT_MS = Number(process.env.DASHBOARD_ADSB_HTTP_TIMEOUT_MS || 2500);
const STATUS_HTTP_TIMEOUT_MS = Number(process.env.DASHBOARD_STATUS_HTTP_TIMEOUT_MS || 5000);
const GRAFANA_HTTP_TIMEOUT_MS = Number(process.env.DASHBOARD_GRAFANA_HTTP_TIMEOUT_MS || 3000);

let starCitizenCache = {
  fetchedAt: 0,
  data: null
};
let adsbSshCache = {
  fetchedAt: 0,
  data: null,
  refreshing: false
};
const adsbTrackHistory = new Map();
let adsbSkyCache = {
  fetchedAt: 0,
  data: null
};

function json(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store"
  });
  res.end(payload);
}

function prometheusEscape(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/"/g, '\\"');
}

function metricLine(name, labels, value) {
  if (!Number.isFinite(value)) return "";
  const renderedLabels = Object.entries(labels || {})
    .map(([key, val]) => `${key}="${prometheusEscape(val)}"`)
    .join(",");
  return `${name}${renderedLabels ? `{${renderedLabels}}` : ""} ${value}\n`;
}

function statusValue(status, expected) {
  return String(status || "").toLowerCase() === expected.toLowerCase() ? 1 : 0;
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 5000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal
    });
  } finally {
    clearTimeout(timeout);
  }
}

function metricsText(data) {
  let out = "";
  out += "# HELP dashboard_local_cpu_percent Windows host CPU usage percent.\n";
  out += "# TYPE dashboard_local_cpu_percent gauge\n";
  out += metricLine("dashboard_local_cpu_percent", { host: data.local?.name || "local" }, data.local?.cpu?.percent);
  out += "# HELP dashboard_local_memory_percent Windows host memory usage percent.\n";
  out += "# TYPE dashboard_local_memory_percent gauge\n";
  out += metricLine("dashboard_local_memory_percent", { host: data.local?.name || "local" }, data.local?.memory?.percent);
  out += metricLine("dashboard_local_memory_used_bytes", { host: data.local?.name || "local" }, data.local?.memory?.used);
  out += metricLine("dashboard_local_memory_total_bytes", { host: data.local?.name || "local" }, data.local?.memory?.total);
  out += metricLine("dashboard_local_uptime_seconds", { host: data.local?.name || "local" }, data.local?.uptimeSeconds);

  if (Array.isArray(data.local?.gpus)) {
    out += "# HELP dashboard_local_gpu_utilization_percent Windows GPU usage percent.\n";
    out += "# TYPE dashboard_local_gpu_utilization_percent gauge\n";
    for (const gpu of data.local.gpus) {
      out += metricLine("dashboard_local_gpu_utilization_percent", { host: data.local.name, gpu: gpu.name }, gpu.utilizationPercent);
      out += metricLine("dashboard_local_gpu_temperature_celsius", { host: data.local.name, gpu: gpu.name }, gpu.temperatureC);
      out += metricLine("dashboard_local_gpu_memory_used_bytes", { host: data.local.name, gpu: gpu.name }, gpu.memoryUsedBytes);
      out += metricLine("dashboard_local_gpu_memory_total_bytes", { host: data.local.name, gpu: gpu.name }, gpu.memoryTotalBytes);
      out += metricLine("dashboard_local_gpu_memory_percent", { host: data.local.name, gpu: gpu.name }, gpu.memoryPercent);
      out += metricLine("dashboard_local_gpu_power_watts", { host: data.local.name, gpu: gpu.name }, gpu.powerWatts);
    }
  }

  if (Array.isArray(data.local?.disks)) {
    for (const disk of data.local.disks) {
      out += metricLine("dashboard_local_disk_size_bytes", { host: data.local.name, drive: disk.DeviceID || "disk", volume: disk.VolumeName || "" }, Number(disk.Size));
      out += metricLine("dashboard_local_disk_free_bytes", { host: data.local.name, drive: disk.DeviceID || "disk", volume: disk.VolumeName || "" }, Number(disk.Free));
    }
  }

  out += "# HELP dashboard_pi_up Whether the Raspberry Pi SSH metrics probe succeeded.\n";
  out += "# TYPE dashboard_pi_up gauge\n";
  out += metricLine("dashboard_pi_up", { host: data.pi?.host || "pi" }, data.pi?.ok ? 1 : 0);
  out += metricLine("dashboard_pi_latency_ms", { host: data.pi?.host || "pi" }, data.pi?.latencyMs);
  if (data.pi?.ok) {
    out += metricLine("dashboard_pi_cpu_percent", { host: data.pi.name || data.pi.host }, data.pi.cpu?.percent);
    out += metricLine("dashboard_pi_cpu_temperature_celsius", { host: data.pi.name || data.pi.host }, data.pi.cpu?.temperatureC);
    out += metricLine("dashboard_pi_memory_percent", { host: data.pi.name || data.pi.host }, data.pi.memory?.percent);
    out += metricLine("dashboard_pi_uptime_seconds", { host: data.pi.name || data.pi.host }, data.pi.uptimeSeconds);
    if (Array.isArray(data.pi.disks)) {
      for (const disk of data.pi.disks) {
        out += metricLine("dashboard_pi_disk_size_bytes", { host: data.pi.name || data.pi.host, mount: disk.DeviceID || "/" }, Number(disk.Size));
        out += metricLine("dashboard_pi_disk_free_bytes", { host: data.pi.name || data.pi.host, mount: disk.DeviceID || "/" }, Number(disk.Free));
      }
    }
  }

  out += "# HELP dashboard_adsb_up Whether the ADS-B Pi Zero is reachable by HTTP or SSH.\n";
  out += "# TYPE dashboard_adsb_up gauge\n";
  out += metricLine("dashboard_adsb_up", { host: data.adsb?.host || ADSB_HOST }, data.adsb?.ok ? 1 : 0);
  out += metricLine("dashboard_adsb_ssh_up", { host: data.adsb?.host || ADSB_HOST }, data.adsb?.sshOk ? 1 : 0);
  out += metricLine("dashboard_adsb_http_up", { url: data.adsb?.aircraftUrl || ADSB_AIRCRAFT_URL }, data.adsb?.httpOk ? 1 : 0);
  out += metricLine("dashboard_adsb_latency_ms", { host: data.adsb?.host || ADSB_HOST }, data.adsb?.latencyMs);
  if (data.adsb?.adsb) {
    out += metricLine("dashboard_adsb_aircraft_seen", { host: data.adsb.name || data.adsb.host || ADSB_HOST }, data.adsb.adsb?.aircraftSeen);
    out += metricLine("dashboard_adsb_messages", { host: data.adsb.name || data.adsb.host || ADSB_HOST }, data.adsb.adsb?.messages);
    out += metricLine("dashboard_adsb_aircraft_positioned", { host: data.adsb.name || data.adsb.host || ADSB_HOST }, data.adsb.adsb?.aircraftPositioned);
    out += metricLine("dashboard_adsb_aircraft_airborne", { host: data.adsb.name || data.adsb.host || ADSB_HOST }, data.adsb.adsb?.aircraftAirborne);
    out += metricLine("dashboard_adsb_highest_altitude_feet", { host: data.adsb.name || data.adsb.host || ADSB_HOST }, data.adsb.adsb?.highestAltitudeFt);
    out += metricLine("dashboard_adsb_fastest_speed_knots", { host: data.adsb.name || data.adsb.host || ADSB_HOST }, data.adsb.adsb?.fastestSpeedKt);
  }
  if (data.adsb?.sshOk) {
    out += metricLine("dashboard_adsb_cpu_percent", { host: data.adsb.name || data.adsb.host }, data.adsb.cpu?.percent);
    out += metricLine("dashboard_adsb_cpu_temperature_celsius", { host: data.adsb.name || data.adsb.host }, data.adsb.cpu?.temperatureC);
    out += metricLine("dashboard_adsb_memory_percent", { host: data.adsb.name || data.adsb.host }, data.adsb.memory?.percent);
    out += metricLine("dashboard_adsb_uptime_seconds", { host: data.adsb.name || data.adsb.host }, data.adsb.uptimeSeconds);
    if (Array.isArray(data.adsb.disks)) {
      for (const disk of data.adsb.disks) {
        out += metricLine("dashboard_adsb_disk_size_bytes", { host: data.adsb.name || data.adsb.host, mount: disk.DeviceID || "/" }, Number(disk.Size));
        out += metricLine("dashboard_adsb_disk_free_bytes", { host: data.adsb.name || data.adsb.host, mount: disk.DeviceID || "/" }, Number(disk.Free));
      }
    }
    if (Array.isArray(data.adsb.services)) {
      for (const service of data.adsb.services) {
        out += metricLine("dashboard_adsb_service_up", { host: data.adsb.name || data.adsb.host, service: service.name }, service.status === "active" ? 1 : 0);
      }
    }
  }

  out += "# HELP star_citizen_status Star Citizen official RSI status as one-hot labels.\n";
  out += "# TYPE star_citizen_status gauge\n";
  for (const status of ["Operational", "Maintenance", "Issue", "Unknown"]) {
    out += metricLine("star_citizen_status", { status }, statusValue(data.starCitizen?.status, status));
  }
  out += metricLine("star_citizen_operational", {}, statusValue(data.starCitizen?.status, "Operational"));
  out += metricLine("star_citizen_maintenance", {}, statusValue(data.starCitizen?.status, "Maintenance"));
  out += metricLine("star_citizen_issue", {}, statusValue(data.starCitizen?.status, "Issue"));

  out += "# HELP grafana_homelab_up Whether the existing Grafana homelab dashboard is reachable.\n";
  out += "# TYPE grafana_homelab_up gauge\n";
  out += metricLine("grafana_homelab_up", { url: data.grafana?.url || "" }, data.grafana?.ok ? 1 : 0);
  out += metricLine("grafana_homelab_latency_ms", { url: data.grafana?.url || "" }, data.grafana?.latencyMs);
  out += metricLine("grafana_homelab_frame_blocked", { url: data.grafana?.url || "" }, data.grafana?.frameBlocked ? 1 : 0);

  return out;
}

function notFound(res) {
  res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
  res.end("Not found");
}

function cpuTimes() {
  return os.cpus().map((cpu) => cpu.times);
}

function cpuPercentBetween(start, end) {
  let idle = 0;
  let total = 0;

  for (let i = 0; i < start.length; i += 1) {
    const a = start[i];
    const b = end[i];
    const idleDelta = b.idle - a.idle;
    const totalDelta =
      b.user - a.user +
      b.nice - a.nice +
      b.sys - a.sys +
      b.irq - a.irq +
      idleDelta;

    idle += idleDelta;
    total += totalDelta;
  }

  if (!total) return null;
  return Math.max(0, Math.min(100, (1 - idle / total) * 100));
}

async function getWindowsDisk() {
  if (process.platform !== "win32") {
    try {
      const { stdout } = await execFileAsync("df", [
        "-B1",
        "-P",
        "-x",
        "tmpfs",
        "-x",
        "devtmpfs"
      ], { timeout: 6000, windowsHide: true });

      return stdout
        .trim()
        .split(/\r?\n/)
        .slice(1)
        .map((line) => line.trim().split(/\s+/))
        .filter((parts) => parts.length >= 6)
        .map(([filesystem, size, used, free, percent, ...mountParts]) => ({
          DeviceID: mountParts.join(" ") || filesystem,
          VolumeName: filesystem,
          Size: Number(size),
          Free: Number(free),
          Used: Number(used),
          Percent: percent
        }));
    } catch (error) {
      return { error: shortError(error) };
    }
  }

  const script = [
    "$ErrorActionPreference = 'Stop';",
    "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" |",
    "Select-Object DeviceID,VolumeName,@{n='Size';e={[int64]$_.Size}},@{n='Free';e={[int64]$_.FreeSpace}} |",
    "ConvertTo-Json -Compress"
  ].join(" ");

  try {
    const { stdout } = await execFileAsync("powershell.exe", [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      script
    ], { timeout: 6000, windowsHide: true });

    if (!stdout.trim()) return [];
    const parsed = JSON.parse(stdout);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch (error) {
    return { error: shortError(error) };
  }
}

async function getTopWindowsProcesses() {
  if (process.platform !== "win32") {
    try {
      const { stdout } = await execFileAsync("ps", [
        "-eo",
        "comm,pid,pcpu,rss",
        "--sort=-pcpu"
      ], { timeout: 6000, windowsHide: true });

      return stdout
        .trim()
        .split(/\r?\n/)
        .slice(1, 6)
        .map((line) => {
          const match = line.trim().match(/^(.+?)\s+(\d+)\s+([\d.]+)\s+(\d+)$/);
          if (!match) return null;
          return {
            ProcessName: match[1],
            Id: Number(match[2]),
            CPU: Number(match[3]),
            Memory: Number(match[4]) * 1024
          };
        })
        .filter(Boolean);
    } catch (error) {
      return { error: shortError(error) };
    }
  }

  const script = [
    "$ErrorActionPreference = 'SilentlyContinue';",
    "Get-Process |",
    "Sort-Object CPU -Descending |",
    "Select-Object -First 5 ProcessName,Id,@{n='CPU';e={[math]::Round([double]($_.CPU),1)}},@{n='Memory';e={[int64]$_.WorkingSet64}} |",
    "ConvertTo-Json -Compress"
  ].join(" ");

  try {
    const { stdout } = await execFileAsync("powershell.exe", [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      script
    ], { timeout: 6000, windowsHide: true });

    if (!stdout.trim()) return [];
    const parsed = JSON.parse(stdout);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch (error) {
    return { error: shortError(error) };
  }
}

async function getGpuMetrics() {
  try {
    const { stdout } = await execFileAsync("nvidia-smi", [
      "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
      "--format=csv,noheader,nounits"
    ], { timeout: 6000, windowsHide: true });

    return stdout
      .trim()
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => {
        const [name, temp, util, memUsed, memTotal, power] = line.split(",").map((part) => part.trim());
        const memoryUsedBytes = Number(memUsed) * 1024 * 1024;
        const memoryTotalBytes = Number(memTotal) * 1024 * 1024;
        return {
          name,
          temperatureC: Number(temp),
          utilizationPercent: Number(util),
          memoryUsedBytes,
          memoryTotalBytes,
          memoryPercent: memoryTotalBytes ? (memoryUsedBytes / memoryTotalBytes) * 100 : null,
          powerWatts: Number(power)
        };
      });
  } catch {
    return [];
  }
}

async function getLocalMetrics() {
  const start = cpuTimes();
  await new Promise((resolve) => setTimeout(resolve, 450));
  const end = cpuTimes();
  const totalMem = os.totalmem();
  const freeMem = os.freemem();

  const [disk, processes, gpus] = await Promise.all([
    getWindowsDisk(),
    getTopWindowsProcesses(),
    getGpuMetrics()
  ]);

  return {
    ok: true,
    name: os.hostname(),
    platform: `${os.type()} ${os.release()}`,
    arch: os.arch(),
    cpu: {
      model: os.cpus()[0]?.model || "Unknown CPU",
      cores: os.cpus().length,
      percent: cpuPercentBetween(start, end)
    },
    memory: {
      total: totalMem,
      used: totalMem - freeMem,
      free: freeMem,
      percent: totalMem ? ((totalMem - freeMem) / totalMem) * 100 : null
    },
    uptimeSeconds: os.uptime(),
    disks: disk,
    gpus,
    processes,
    checkedAt: new Date().toISOString()
  };
}

function remotePythonScript() {
  return String.raw`python3 - <<'PY'
import json, os, shutil, socket, subprocess, time, urllib.request

def read_float(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        return None

def cpu_sample():
    with open("/proc/stat", "r", encoding="utf-8") as f:
        parts = [int(x) for x in f.readline().split()[1:]]
    idle = parts[3] + parts[4]
    total = sum(parts)
    return idle, total

def meminfo():
    data = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            key, val = line.split(":", 1)
            data[key] = int(val.strip().split()[0]) * 1024
    total = data.get("MemTotal", 0)
    available = data.get("MemAvailable", 0)
    used = max(0, total - available)
    return {"total": total, "used": used, "free": available, "percent": (used / total * 100) if total else None}

def uptime_seconds():
    with open("/proc/uptime", "r", encoding="utf-8") as f:
        return float(f.read().split()[0])

def read_aircraft_json():
    candidates = [
        "/run/readsb/aircraft.json",
        "/run/dump1090-fa/aircraft.json",
        "/var/run/dump1090-fa/aircraft.json",
        "/var/www/html/dump1090/data/aircraft.json",
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            aircraft = data.get("aircraft", [])
            return {"aircraftSeen": len(aircraft), "messages": data.get("messages")}
        except Exception:
            pass
    for url in [
        "http://127.0.0.1/tar1090/data/aircraft.json",
        "http://127.0.0.1/dump1090-fa/data/aircraft.json",
        "http://127.0.0.1/readsb/data/aircraft.json",
    ]:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                data = json.loads(response.read().decode("utf-8"))
            aircraft = data.get("aircraft", [])
            return {"aircraftSeen": len(aircraft), "messages": data.get("messages")}
        except Exception:
            pass
    return {"aircraftSeen": None, "messages": None}

idle1, total1 = cpu_sample()
time.sleep(0.35)
idle2, total2 = cpu_sample()
total_delta = total2 - total1
idle_delta = idle2 - idle1
cpu_percent = (1 - idle_delta / total_delta) * 100 if total_delta else None
disk = shutil.disk_usage("/")
temp = read_float("/sys/class/thermal/thermal_zone0/temp")
load1, load5, load15 = os.getloadavg()

services = []
for service in ["ssh", "NetworkManager", "docker", "tailscaled", "nginx", "apache2", "dump1090-fa", "readsb", "piaware", "fr24feed", "tar1090", "adsbexchange-feed", "mlat-client"]:
    result = subprocess.run(["systemctl", "is-active", service], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    status = result.stdout.strip() if result.stdout.strip() else "inactive"
    if result.returncode == 0 or service in ["dump1090-fa", "readsb", "piaware", "fr24feed", "tar1090", "adsbexchange-feed", "mlat-client"]:
        services.append({"name": service, "status": status})

print(json.dumps({
    "ok": True,
    "name": socket.gethostname(),
    "platform": subprocess.run(["uname", "-srmo"], text=True, stdout=subprocess.PIPE).stdout.strip(),
    "cpu": {"percent": cpu_percent, "load": [load1, load5, load15], "temperatureC": (temp / 1000) if temp else None},
    "memory": meminfo(),
    "uptimeSeconds": uptime_seconds(),
    "disks": [{"DeviceID": "/", "VolumeName": "root", "Size": disk.total, "Free": disk.free}],
    "services": services,
    "adsb": read_aircraft_json(),
    "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
}))
PY`;
}

async function getLinuxHostMetrics(host) {
  const started = Date.now();
  try {
    const { stdout } = await execFileAsync("ssh", [
      "-o",
      "BatchMode=yes",
      "-o",
      "ConnectTimeout=5",
      host,
      remotePythonScript()
    ], { timeout: 9000, windowsHide: true });

    const parsed = JSON.parse(stdout);
    return {
      ...parsed,
      host,
      latencyMs: Date.now() - started
    };
  } catch (error) {
    return {
      ok: false,
      host,
      error: shortError(error),
      checkedAt: new Date().toISOString()
    };
  }
}

async function getPiMetrics() {
  return getLinuxHostMetrics(PI_HOST);
}

async function getAdsbMetrics() {
  const [sshMetrics, httpMetrics] = await Promise.all([
    getCachedAdsbSshMetrics(),
    getAdsbHttpMetrics()
  ]);

  if (sshMetrics.ok) {
    return {
      ...sshMetrics,
      ok: true,
      sshOk: true,
      httpOk: httpMetrics.ok,
      aircraftUrl: ADSB_AIRCRAFT_URL,
      adsb: httpMetrics.adsb || sshMetrics.adsb
    };
  }

  return {
    ...sshMetrics,
    ok: httpMetrics.ok,
    sshOk: false,
    httpOk: httpMetrics.ok,
    aircraftUrl: ADSB_AIRCRAFT_URL,
    adsb: httpMetrics.adsb,
    latencyMs: httpMetrics.latencyMs,
    httpError: httpMetrics.error,
    checkedAt: new Date().toISOString()
  };
}

function refreshAdsbSshMetrics() {
  if (adsbSshCache.refreshing) return;
  adsbSshCache.refreshing = true;
  getAdsbSshMetrics()
    .then((data) => {
      adsbSshCache = {
        fetchedAt: Date.now(),
        data,
        refreshing: false
      };
    })
    .catch((error) => {
      adsbSshCache = {
        fetchedAt: Date.now(),
        data: {
          ok: false,
          host: ADSB_HOST,
          error: shortError(error),
          checkedAt: new Date().toISOString()
        },
        refreshing: false
      };
    });
}

async function getCachedAdsbSshMetrics() {
  const now = Date.now();
  if (!adsbSshCache.data || now - adsbSshCache.fetchedAt > 45_000) {
    refreshAdsbSshMetrics();
  }

  return adsbSshCache.data || {
    ok: false,
    host: ADSB_HOST,
    error: "ADS-B SSH metrics warming up.",
    checkedAt: new Date().toISOString()
  };
}

async function getAdsbSshMetrics() {
  const started = Date.now();
  try {
    const pythonCommand = process.platform === "win32" ? "python" : "python3";
    const { stdout } = await execFileAsync(pythonCommand, [
      path.join(__dirname, "adsb_ssh_probe.py"),
      ADSB_HOST
    ], { timeout: 80000, windowsHide: true });

    const parsed = JSON.parse(stdout);
    return {
      ...parsed,
      host: ADSB_HOST,
      latencyMs: Date.now() - started
    };
  } catch (error) {
    return {
      ok: false,
      host: ADSB_HOST,
      error: shortError(error),
      checkedAt: new Date().toISOString()
    };
  }
}

async function getAdsbHttpMetrics() {
  const started = Date.now();
  try {
    const response = await fetchWithTimeout(ADSB_AIRCRAFT_URL, {
      headers: { "user-agent": "BayloDashboard/0.1" }
    }, ADSB_HTTP_TIMEOUT_MS);
    const data = await response.json();
    const feedNowMs = Number.isFinite(Number(data.now)) ? Number(data.now) * 1000 : Date.now();
    const aircraft = Array.isArray(data.aircraft)
      ? data.aircraft.map((plane) => normalizeAircraft(plane, feedNowMs, Date.now()))
      : [];
    const summary = summarizeSky(aircraft);

    return {
      ok: response.ok,
      latencyMs: Date.now() - started,
      adsb: {
        aircraftSeen: aircraft.length,
        messages: Number(data.messages),
        aircraftPositioned: summary.positioned,
        aircraftAirborne: summary.airborne,
        highestAltitudeFt: summary.highest?.altitudeFt ?? null,
        fastestSpeedKt: summary.fastest?.speedKt ?? null
      }
    };
  } catch (error) {
    return {
      ok: false,
      latencyMs: Date.now() - started,
      error: shortError(error)
    };
  }
}

function normalizeAltitude(value) {
  if (value === "ground") return 0;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeAircraft(raw, feedNowMs, receivedAt) {
  const lat = Number(raw.lat);
  const lon = Number(raw.lon);
  const seenPosition = Number(raw.seen_pos ?? raw.seen);
  const seenAt = Number.isFinite(seenPosition) && Number.isFinite(feedNowMs)
    ? feedNowMs - (seenPosition * 1000)
    : receivedAt;

  return {
    hex: String(raw.hex || raw.icao || "").toUpperCase(),
    flight: String(raw.flight || raw.callsign || "").trim(),
    lat: Number.isFinite(lat) ? lat : null,
    lon: Number.isFinite(lon) ? lon : null,
    altitudeFt: normalizeAltitude(raw.alt_baro ?? raw.alt_geom ?? raw.altitude),
    speedKt: Number.isFinite(Number(raw.gs)) ? Number(raw.gs) : null,
    trackDeg: Number.isFinite(Number(raw.track)) ? Number(raw.track) : null,
    verticalRateFpm: Number.isFinite(Number(raw.baro_rate ?? raw.geom_rate)) ? Number(raw.baro_rate ?? raw.geom_rate) : null,
    category: raw.category || null,
    squawk: raw.squawk || null,
    rssi: Number.isFinite(Number(raw.rssi)) ? Number(raw.rssi) : null,
    messages: Number.isFinite(Number(raw.messages)) ? Number(raw.messages) : null,
    seenSeconds: Number.isFinite(Number(raw.seen)) ? Number(raw.seen) : null,
    seenPositionSeconds: Number.isFinite(seenPosition) ? seenPosition : null,
    emergency: raw.emergency || null,
    seenAt: new Date(seenAt).toISOString()
  };
}

function updateAdsbTrackHistory(aircraft, nowMs) {
  const cutoff = nowMs - 60 * 60 * 1000;
  const activeHexes = new Set();

  for (const plane of aircraft) {
    if (!plane.hex || !Number.isFinite(plane.lat) || !Number.isFinite(plane.lon)) continue;
    activeHexes.add(plane.hex);
    const points = adsbTrackHistory.get(plane.hex) || [];
    const last = points.at(-1);
    const point = {
      t: Date.parse(plane.seenAt) || nowMs,
      lat: plane.lat,
      lon: plane.lon,
      altitudeFt: plane.altitudeFt,
      speedKt: plane.speedKt,
      trackDeg: plane.trackDeg
    };

    if (!last || Math.abs(last.lat - point.lat) > 0.0001 || Math.abs(last.lon - point.lon) > 0.0001 || point.t - last.t > 10_000) {
      points.push(point);
    }

    const trimmed = points.filter((item) => item.t >= cutoff).slice(-180);
    adsbTrackHistory.set(plane.hex, trimmed);
  }

  for (const [hex, points] of adsbTrackHistory.entries()) {
    const recent = points.filter((item) => item.t >= cutoff).slice(-180);
    if (!recent.length && !activeHexes.has(hex)) adsbTrackHistory.delete(hex);
    else adsbTrackHistory.set(hex, recent);
  }
}

function summarizeSky(aircraft) {
  const positioned = aircraft.filter((plane) => Number.isFinite(plane.lat) && Number.isFinite(plane.lon));
  const airborne = aircraft.filter((plane) => Number.isFinite(plane.altitudeFt) && plane.altitudeFt > 0);
  const highest = [...airborne].sort((a, b) => (b.altitudeFt || 0) - (a.altitudeFt || 0))[0] || null;
  const fastest = aircraft
    .filter((plane) => Number.isFinite(plane.speedKt))
    .sort((a, b) => (b.speedKt || 0) - (a.speedKt || 0))[0] || null;

  return {
    total: aircraft.length,
    positioned: positioned.length,
    airborne: airborne.length,
    highest,
    fastest
  };
}

async function getAdsbSkyData() {
  const now = Date.now();
  if (adsbSkyCache.data && now - adsbSkyCache.fetchedAt < 1800) {
    return adsbSkyCache.data;
  }

  try {
    const response = await fetchWithTimeout(ADSB_AIRCRAFT_URL, {
      headers: { "user-agent": "BayloDashboard/0.1" }
    }, ADSB_HTTP_TIMEOUT_MS);
    const feed = await response.json();
    const feedNowMs = Number.isFinite(Number(feed.now)) ? Number(feed.now) * 1000 : now;
    const aircraft = Array.isArray(feed.aircraft)
      ? feed.aircraft.map((plane) => normalizeAircraft(plane, feedNowMs, now))
      : [];

    updateAdsbTrackHistory(aircraft, now);

    const history = {};
    for (const [hex, points] of adsbTrackHistory.entries()) {
      history[hex] = points;
    }

    const data = {
      ok: response.ok,
      url: ADSB_AIRCRAFT_URL,
      generatedAt: new Date(now).toISOString(),
      feedNow: Number.isFinite(feedNowMs) ? new Date(feedNowMs).toISOString() : null,
      messages: Number.isFinite(Number(feed.messages)) ? Number(feed.messages) : null,
      receiver: {
        lat: Number.isFinite(ADSB_RECEIVER_LAT) ? ADSB_RECEIVER_LAT : null,
        lon: Number.isFinite(ADSB_RECEIVER_LON) ? ADSB_RECEIVER_LON : null
      },
      aircraft,
      history,
      summary: summarizeSky(aircraft)
    };

    adsbSkyCache = { fetchedAt: now, data };
    return data;
  } catch (error) {
    return {
      ok: false,
      url: ADSB_AIRCRAFT_URL,
      generatedAt: new Date(now).toISOString(),
      receiver: {
        lat: Number.isFinite(ADSB_RECEIVER_LAT) ? ADSB_RECEIVER_LAT : null,
        lon: Number.isFinite(ADSB_RECEIVER_LON) ? ADSB_RECEIVER_LON : null
      },
      aircraft: [],
      history: Object.fromEntries(adsbTrackHistory.entries()),
      summary: summarizeSky([]),
      error: shortError(error)
    };
  }
}

function stripHtml(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&rarr;/g, "->")
    .replace(/&#10006;/g, "Unresolved")
    .replace(/&#10004;/g, "Resolved")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(Number.parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, num) => String.fromCodePoint(Number.parseInt(num, 10)))
    .replace(/\s+/g, " ")
    .trim();
}

function findIncident(text) {
  const match = text.match(/Latest incidents\s+(.+?)(?:Live Service Maintenance|Live Deployment|Platform Services Disruption|$)/i);
  if (!match) return null;
  return match[1].slice(0, 520).trim();
}

async function getStarCitizenStatus() {
  const now = Date.now();
  if (starCitizenCache.data && now - starCitizenCache.fetchedAt < 60_000) {
    return starCitizenCache.data;
  }

  try {
    const response = await fetchWithTimeout(SC_STATUS_URL, {
      headers: {
        "user-agent": "BayloDashboard/0.1"
      }
    }, STATUS_HTTP_TIMEOUT_MS);
    const html = await response.text();
    const text = stripHtml(html);
    const lower = text.toLowerCase();

    const inMaintenance = lower.includes("in maintenance") || lower.includes("maintenance window");
    const degraded = lower.includes("degraded") || lower.includes("disruption") || lower.includes("investigating");
    const unresolved = lower.includes("unresolved");
    const operational = !inMaintenance && !degraded && !unresolved;

    let status = "Operational";
    if (inMaintenance) status = "Maintenance";
    else if (degraded || unresolved) status = "Issue";

    const components = ["Platform", "Persistent Universe", "Arena Commander"].map((name) => {
      const componentRegex = new RegExp(`${name}\\s+(Operational|Maintenance|Degraded|Outage|Issue)`, "i");
      const found = text.match(componentRegex);
      return { name, status: found?.[1] || (inMaintenance ? "Maintenance" : "Unknown") };
    });

    const data = {
      ok: response.ok,
      status,
      url: SC_STATUS_URL,
      components,
      summary: findIncident(text) || text.slice(0, 420),
      checkedAt: new Date().toISOString()
    };

    starCitizenCache = { fetchedAt: now, data };
    return data;
  } catch (error) {
    return {
      ok: false,
      status: "Unknown",
      url: SC_STATUS_URL,
      error: shortError(error),
      checkedAt: new Date().toISOString()
    };
  }
}

async function getGrafanaStatus() {
  const started = Date.now();
  try {
    const response = await fetchWithTimeout(GRAFANA_URL, {
      headers: {
        "user-agent": "BayloDashboard/0.1"
      }
    }, GRAFANA_HTTP_TIMEOUT_MS);
    const html = await response.text();
    const title = html.match(/<title>(.*?)<\/title>/i)?.[1]?.trim() || "Grafana";
    const frameHeader = response.headers.get("x-frame-options") || "";
    const csp = response.headers.get("content-security-policy") || "";
    const frameBlocked =
      frameHeader.toLowerCase().includes("deny") ||
      frameHeader.toLowerCase().includes("sameorigin") ||
      csp.toLowerCase().includes("frame-ancestors");

    return {
      ok: response.ok,
      status: response.ok ? "Reachable" : `HTTP ${response.status}`,
      title,
      url: GRAFANA_URL,
      latencyMs: Date.now() - started,
      frameBlocked,
      frameReason: frameBlocked ? (frameHeader ? `X-Frame-Options: ${frameHeader}` : "Content-Security-Policy frame restrictions") : null,
      checkedAt: new Date().toISOString()
    };
  } catch (error) {
    return {
      ok: false,
      status: "Offline",
      url: GRAFANA_URL,
      error: shortError(error),
      checkedAt: new Date().toISOString()
    };
  }
}

function shortError(error) {
  const output = [error.message, error.stderr, error.stdout]
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();

  if (output.includes("Permission denied")) {
    return "SSH authentication failed. Add an SSH key for dashboard access or run with an interactive SSH agent.";
  }
  if (output.includes("timed out")) return "Request timed out.";
  return output.slice(0, 240) || "Unknown error";
}

async function snapshot() {
  const [local, pi, adsb, starCitizen, grafana] = await Promise.all([
    getLocalMetrics(),
    getPiMetrics(),
    getAdsbMetrics(),
    getStarCitizenStatus(),
    getGrafanaStatus()
  ]);

  return {
    generatedAt: new Date().toISOString(),
    config: {
      piHost: PI_HOST,
      adsbHost: ADSB_HOST,
      adsbAircraftUrl: ADSB_AIRCRAFT_URL,
      adsbReceiverLat: Number.isFinite(ADSB_RECEIVER_LAT) ? ADSB_RECEIVER_LAT : null,
      adsbReceiverLon: Number.isFinite(ADSB_RECEIVER_LON) ? ADSB_RECEIVER_LON : null,
      starCitizenUrl: SC_STATUS_URL,
      grafanaUrl: GRAFANA_URL
    },
    local,
    pi,
    adsb,
    starCitizen,
    grafana
  };
}

async function serveStatic(req, res) {
  const cleanPath = req.url === "/" ? "/index.html" : req.url.split("?")[0];
  const filePath = path.join(__dirname, "public", path.normalize(cleanPath).replace(/^(\.\.[/\\])+/, ""));

  if (!filePath.startsWith(path.join(__dirname, "public")) || !existsSync(filePath)) {
    notFound(res);
    return;
  }

  const ext = path.extname(filePath).toLowerCase();
  const contentType = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml"
  }[ext] || "application/octet-stream";

  res.writeHead(200, { "content-type": contentType });
  res.end(await readFile(filePath));
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.url?.startsWith("/api/snapshot")) {
      json(res, 200, await snapshot());
      return;
    }
    if (req.url?.startsWith("/api/config")) {
      json(res, 200, {
        piHost: PI_HOST,
        adsbHost: ADSB_HOST,
        adsbAircraftUrl: ADSB_AIRCRAFT_URL,
        adsbReceiverLat: Number.isFinite(ADSB_RECEIVER_LAT) ? ADSB_RECEIVER_LAT : null,
        adsbReceiverLon: Number.isFinite(ADSB_RECEIVER_LON) ? ADSB_RECEIVER_LON : null,
        starCitizenUrl: SC_STATUS_URL,
        grafanaUrl: GRAFANA_URL,
        port: PORT
      });
      return;
    }
    if (req.url?.startsWith("/api/adsb/sky")) {
      json(res, 200, await getAdsbSkyData());
      return;
    }
    if (req.url?.startsWith("/metrics")) {
      const payload = metricsText(await snapshot());
      res.writeHead(200, {
        "content-type": "text/plain; version=0.0.4; charset=utf-8",
        "cache-control": "no-store"
      });
      res.end(payload);
      return;
    }
    await serveStatic(req, res);
  } catch (error) {
    json(res, 500, { ok: false, error: shortError(error) });
  }
});

server.listen(PORT, () => {
  console.log(`Dashboard running at http://localhost:${PORT}`);
  console.log(`Pi target: ${PI_HOST}`);
  console.log(`ADS-B target: ${ADSB_HOST}`);
  console.log(`ADS-B aircraft URL: ${ADSB_AIRCRAFT_URL}`);
});
