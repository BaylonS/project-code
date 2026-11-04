import json
import os
import sys
import time
from pathlib import Path

import paramiko


REMOTE = r"""python3 - <<'PY'
import json, os, shutil, socket, time

def cpu_sample():
    with open("/proc/stat", "r", encoding="utf-8") as f:
        parts = [int(x) for x in f.readline().split()[1:]]
    return parts[3] + parts[4], sum(parts)

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

def read_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as f:
            return float(f.read().strip()) / 1000
    except Exception:
        return None

def uptime_seconds():
    with open("/proc/uptime", "r", encoding="utf-8") as f:
        return float(f.read().split()[0])

idle1, total1 = cpu_sample()
time.sleep(0.25)
idle2, total2 = cpu_sample()
total_delta = total2 - total1
idle_delta = idle2 - idle1
disk = shutil.disk_usage("/")
load1, load5, load15 = os.getloadavg()

print(json.dumps({
    "ok": True,
    "name": socket.gethostname(),
    "cpu": {
        "percent": (1 - idle_delta / total_delta) * 100 if total_delta else None,
        "temperatureC": read_temp(),
        "load": [load1, load5, load15]
    },
    "memory": meminfo(),
    "uptimeSeconds": uptime_seconds(),
    "disks": [{"DeviceID": "/", "VolumeName": "root", "Size": disk.total, "Free": disk.free}]
}))
PY"""


def main():
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        raise SystemExit("usage: adsb_ssh_probe.py user@host")

    user, host = sys.argv[1].split("@", 1)
    key_path = Path.home() / ".ssh" / "id_ed25519"
    key = paramiko.Ed25519Key.from_private_key_file(str(key_path))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            username=user,
            pkey=key,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
            look_for_keys=False,
            allow_agent=False,
        )
        stdin, stdout, stderr = client.exec_command(REMOTE, timeout=60)
        output = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError(err or f"remote command exited {code}")
        sys.stdout.write(output)
    finally:
        client.close()


if __name__ == "__main__":
    main()
