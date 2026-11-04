from __future__ import annotations

import math
import re
from pathlib import Path


SAVE = Path(r"D:\steam\SteamApps\common\Kerbal Space Program\saves\B_S Space Program\persistent.sfs")
KERBIN_RADIUS = 600000.0
MU_KERBIN = 3.5316e12


def extract_vessels(text: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"(?m)^\t\tVESSEL\r?\n\t\t\{", text):
        start = match.start()
        brace = text.find("{", start)
        depth = 0
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : i + 1])
                    break
    return blocks


def field(block: str, key: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(key)} = (.*)$", block)
    return match.group(1).strip() if match else ""


def num(block: str, key: str) -> float:
    return float(field(block, key))


def solve_kepler(mean_anomaly: float, ecc: float) -> float:
    mean_anomaly %= 2.0 * math.pi
    eccentric_anomaly = mean_anomaly if ecc < 0.8 else math.pi
    for _ in range(20):
        delta = (eccentric_anomaly - ecc * math.sin(eccentric_anomaly) - mean_anomaly) / (
            1.0 - ecc * math.cos(eccentric_anomaly)
        )
        eccentric_anomaly -= delta
        if abs(delta) < 1e-12:
            break
    return eccentric_anomaly


def position_from_orbit(orbit: dict[str, float], ut: float) -> tuple[float, float, float]:
    sma = orbit["SMA"]
    ecc = orbit["ECC"]
    inc = math.radians(orbit["INC"])
    argp = math.radians(orbit["LPE"])
    raan = math.radians(orbit["LAN"])
    mean_motion = math.sqrt(MU_KERBIN / (sma**3))
    mean_anomaly = orbit["MNA"] + mean_motion * (ut - orbit["EPH"])
    eccentric_anomaly = solve_kepler(mean_anomaly, ecc)
    x_orb = sma * (math.cos(eccentric_anomaly) - ecc)
    y_orb = sma * math.sqrt(1.0 - ecc * ecc) * math.sin(eccentric_anomaly)

    cos_o, sin_o = math.cos(raan), math.sin(raan)
    cos_i, sin_i = math.cos(inc), math.sin(inc)
    cos_w, sin_w = math.cos(argp), math.sin(argp)

    x = (cos_o * cos_w - sin_o * sin_w * cos_i) * x_orb + (-cos_o * sin_w - sin_o * cos_w * cos_i) * y_orb
    y = (sin_o * cos_w + cos_o * sin_w * cos_i) * x_orb + (-sin_o * sin_w + cos_o * cos_w * cos_i) * y_orb
    z = (sin_w * sin_i) * x_orb + (cos_w * sin_i) * y_orb
    return x, y, z


def angle_deg(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    da = math.sqrt(sum(v * v for v in a))
    db = math.sqrt(sum(v * v for v in b))
    dot = sum(x * y for x, y in zip(a, b)) / (da * db)
    dot = min(1.0, max(-1.0, dot))
    return math.degrees(math.acos(dot))


def main() -> None:
    text = SAVE.read_text(encoding="utf-8-sig", errors="replace")
    save_ut = float(re.search(r"(?m)^\s*UT = ([0-9.Ee+-]+)", text).group(1))
    sats = []
    for block in extract_vessels(text):
        name = field(block, "name")
        if not name.startswith("GPS PRN "):
            continue
        orbit = {key: num(block, key) for key in ("SMA", "ECC", "INC", "LPE", "LAN", "MNA", "EPH")}
        pos = position_from_orbit(orbit, save_ut)
        radius = math.sqrt(sum(v * v for v in pos))
        sub_lat = math.degrees(math.asin(pos[2] / radius))
        sub_lon = math.degrees(math.atan2(pos[1], pos[0]))
        sats.append({"name": name, "orbit": orbit, "pos": pos, "radius": radius, "lat": sub_lat, "lon": sub_lon})

    if len(sats) != 32:
        raise RuntimeError(f"Expected 32 GPS PRN satellites, found {len(sats)}")

    by_plane: dict[int, list[str]] = {}
    for sat in sats:
        plane = round((sat["orbit"]["LAN"] % 360.0) / 60.0) % 6
        by_plane.setdefault(plane, []).append(sat["name"])

    min_pair = min(
        (angle_deg(a["pos"], b["pos"]), a["name"], b["name"])
        for i, a in enumerate(sats)
        for b in sats[i + 1 :]
    )

    # Grid coverage check. Counts sats above Kerbin horizon and inside GPS transmitter range.
    gps_range = 2_500_000.0
    min_visible = 999
    max_visible = 0
    worst_points = []
    for lat in range(-90, 91, 5):
        for lon in range(-180, 180, 5):
            lat_r = math.radians(lat)
            lon_r = math.radians(lon)
            ground = (
                KERBIN_RADIUS * math.cos(lat_r) * math.cos(lon_r),
                KERBIN_RADIUS * math.cos(lat_r) * math.sin(lon_r),
                KERBIN_RADIUS * math.sin(lat_r),
            )
            visible = 0
            for sat in sats:
                sx, sy, sz = sat["pos"]
                gx, gy, gz = ground
                diff = (sx - gx, sy - gy, sz - gz)
                distance = math.sqrt(sum(v * v for v in diff))
                above_horizon = sum(d * g for d, g in zip(diff, ground)) > 0.0
                if above_horizon and distance <= gps_range:
                    visible += 1
            if visible < min_visible:
                min_visible = visible
                worst_points = [(lat, lon, visible)]
            elif visible == min_visible:
                worst_points.append((lat, lon, visible))
            max_visible = max(max_visible, visible)

    print(f"GPS satellites in save: {len(sats)}")
    print("Plane distribution by ~60 degree RAAN bins:")
    for plane in sorted(by_plane):
        print(f"  plane {plane}: {len(by_plane[plane])}")
    print(f"Closest satellite spacing angle: {min_pair[0]:.2f} deg between {min_pair[1]} and {min_pair[2]}")
    print(f"Coverage grid min visible satellites: {min_visible}")
    print(f"Coverage grid max visible satellites: {max_visible}")
    print(f"Worst grid examples: {worst_points[:8]}")
    print("Sample sub-satellite points:")
    for sat in sorted(sats, key=lambda s: s["name"])[:8]:
        print(f"  {sat['name']}: lat {sat['lat']:.1f}, lon {sat['lon']:.1f}, SMA {sat['orbit']['SMA']:.1f}")


if __name__ == "__main__":
    main()
