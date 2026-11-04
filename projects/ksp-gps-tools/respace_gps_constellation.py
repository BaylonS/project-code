from __future__ import annotations

import math
import re
import shutil
import time
from pathlib import Path


SAVE = Path(r"D:\steam\SteamApps\common\Kerbal Space Program\saves\B_S Space Program\persistent.sfs")
WORK = Path(r"C:\Users\baylo\Documents\Codex\2026-06-27\ple\work")


def extract_vessels(text: str) -> list[tuple[int, int, str]]:
    starts = [m.start() for m in re.finditer(r"(?m)^\t\tVESSEL\r?\n\t\t\{", text)]
    vessels = []
    for start in starts:
        brace = text.find("{", start)
        depth = 0
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    vessels.append((start, i + 1, text[start : i + 1]))
                    break
    return vessels


def field(block: str, key: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(key)} = (.*)$", block)
    return match.group(1).strip() if match else ""


def set_field(block: str, key: str, value: str, count: int = 0) -> str:
    return re.sub(rf"(?m)^(\s*{re.escape(key)} = ).*$", rf"\g<1>{value}", block, count=count)


def circular_mean_degrees(values: list[float]) -> float:
    x = sum(math.cos(math.radians(v)) for v in values)
    y = sum(math.sin(math.radians(v)) for v in values)
    return math.degrees(math.atan2(y, x)) % 360.0


def main() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = WORK / f"gps-respace-backup-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(SAVE, backup_dir / SAVE.name)

    text = SAVE.read_text(encoding="utf-8-sig", newline="")
    newline = "\r\n" if "\r\n" in text else "\n"
    save_ut = float(re.search(r"(?m)^\s*UT = ([0-9.Ee+-]+)", text).group(1))
    vessels = extract_vessels(text)

    gps_entries = []
    for index, (_, _, block) in enumerate(vessels):
        name = field(block, "name")
        if not name.startswith("GPS PRN "):
            continue
        lan = float(field(block, "LAN"))
        prn = int(re.search(r"GPS PRN (\d+)", name).group(1))
        plane = round((lan % 360.0) / 60.0) % 6
        gps_entries.append({"index": index, "block": block, "name": name, "lan": lan, "prn": prn, "plane": plane})

    if len(gps_entries) != 32:
        raise RuntimeError(f"Expected 32 GPS PRN vessels, found {len(gps_entries)}")

    plane_lans = {
        plane: circular_mean_degrees([entry["lan"] for entry in gps_entries if entry["plane"] == plane])
        for plane in sorted({entry["plane"] for entry in gps_entries})
    }

    rebuilt = [block for _, _, block in vessels]
    for plane in sorted(plane_lans):
        plane_entries = sorted((entry for entry in gps_entries if entry["plane"] == plane), key=lambda entry: entry["prn"])
        slots = len(plane_entries)
        for slot, entry in enumerate(plane_entries):
            # Evenly spaced argument of latitude. For these low-eccentricity GPS-style orbits,
            # LPE=0 and MNA=slot angle gives a stable, readable slot layout in KSP.
            mean_anomaly = (2.0 * math.pi * slot / slots) % (2.0 * math.pi)
            block = entry["block"]
            block = set_field(block, "LAN", f"{plane_lans[plane]:.12f}", count=1)
            block = set_field(block, "LPE", "0", count=1)
            block = set_field(block, "MNA", f"{mean_anomaly:.15f}", count=1)
            block = set_field(block, "EPH", f"{save_ut:.14f}", count=1)
            block = set_field(block, "lastUT", f"{save_ut:.14f}", count=1)
            rebuilt[entry["index"]] = block

    first = min(start for start, _, _ in vessels)
    last = max(end for _, end, _ in vessels)
    new_text = text[:first] + (newline * 2).join(rebuilt) + text[last:]
    if new_text.count("{") != new_text.count("}"):
        raise RuntimeError("Brace validation failed")
    SAVE.write_text(new_text, encoding="utf-8", newline="")

    print(f"Backed up save to: {backup_dir}")
    print("Respaced GPS PRN satellites by plane:")
    for plane in sorted(plane_lans):
        count = sum(1 for entry in gps_entries if entry["plane"] == plane)
        print(f"  plane {plane}: {count} satellites, LAN {plane_lans[plane]:.3f} deg")


if __name__ == "__main__":
    main()
