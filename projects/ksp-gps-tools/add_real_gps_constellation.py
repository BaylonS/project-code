from __future__ import annotations

import csv
import json
import math
import random
import re
import shutil
import time
import uuid
from pathlib import Path


SAVE = Path(r"D:\steam\SteamApps\common\Kerbal Space Program\saves\B_S Space Program\persistent.sfs")
LOADMETA = SAVE.with_suffix(".loadmeta")
GPS_JSON = Path(r"C:\Users\baylo\Documents\Codex\2026-06-27\ple\work\gps-ops-20260628.json")
WORK = Path(r"C:\Users\baylo\Documents\Codex\2026-06-27\ple\work")
SUMMARY = WORK / "gps_constellation_added.csv"

MU_KERBIN = 3.5316e12
KERBIN_DAY_SECONDS = 21600.0
KERBIN_RADIUS = 600000.0

OLD_GPS_NAMES = {
    "GPS PROBE 4",
    "GPS Probe 1",
    "GPS Probe 2",
    "GPS Probe 3",
}


def extract_vessels(text: str) -> list[tuple[int, int, str]]:
    starts = [m.start() for m in re.finditer(r"(?m)^\t\tVESSEL\r?\n\t\t\{", text)]
    vessels: list[tuple[int, int, str]] = []
    for start in starts:
        brace = text.find("{", start)
        depth = 0
        end = None
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            raise RuntimeError(f"Unclosed VESSEL block at offset {start}")
        vessels.append((start, end, text[start:end]))
    return vessels


def field(block: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)} = (.*)$", block)
    return match.group(1).strip() if match else None


def set_field(block: str, key: str, value: str, count: int = 0) -> str:
    pattern = rf"(?m)^(\s*{re.escape(key)} = ).*$"
    return re.sub(pattern, rf"\g<1>{value}", block, count=count)


def tle_to_kerbin_sma(mean_motion_rev_day: float) -> float:
    period = KERBIN_DAY_SECONDS / mean_motion_rev_day
    return (MU_KERBIN * (period / (2.0 * math.pi)) ** 2.0) ** (1.0 / 3.0)


def unique_part_numbers(block: str, launch_id: int) -> str:
    rng = random.SystemRandom()

    def replace_int(match: re.Match[str]) -> str:
        indent = match.group(1)
        key = match.group(2)
        value = rng.randrange(1, 4_294_000_000)
        return f"{indent}{key} = {value}"

    block = re.sub(r"(?m)^(\s*)(cid|uid|mid|persistentId) = \d+$", replace_int, block)
    block = re.sub(r"(?m)^(\s*launchID = )\d+$", rf"\g<1>{launch_id}", block)
    return block


def make_vessel(template: str, sat: dict, save_ut: float, launch_id: int) -> tuple[str, dict]:
    prn_match = re.search(r"PRN\s+(\d+)", sat["OBJECT_NAME"])
    prn = prn_match.group(1).zfill(2) if prn_match else sat["OBJECT_NAME"][-2:]
    family_match = re.search(r"GPS\s+([A-Z0-9-]+)", sat["OBJECT_NAME"])
    family = family_match.group(1) if family_match else "SAT"
    name = f"GPS PRN {prn} {family}"
    pid = uuid.uuid4().hex
    vessel_persistent = str(random.SystemRandom().randrange(1, 4_294_000_000))
    mean_motion = float(sat["MEAN_MOTION"])
    sma = tle_to_kerbin_sma(mean_motion)
    ecc = float(sat["ECCENTRICITY"])
    inc = float(sat["INCLINATION"])
    lan = float(sat["RA_OF_ASC_NODE"])
    lpe = float(sat["ARG_OF_PERICENTER"])
    mna = math.radians(float(sat["MEAN_ANOMALY"]))
    altitude = sma - KERBIN_RADIUS

    block = template
    original_name = field(block, "name") or "GPS Probe 1"
    block = block.replace(original_name, name)
    block = set_field(block, "pid", pid, count=1)
    block = set_field(block, "persistentId", vessel_persistent, count=1)
    block = set_field(block, "name", name, count=1)
    block = set_field(block, "type", "Probe", count=1)
    block = set_field(block, "sit", "ORBITING", count=1)
    block = set_field(block, "landed", "False", count=1)
    block = set_field(block, "splashed", "False", count=1)
    block = set_field(block, "landedAt", "", count=1)
    block = set_field(block, "displaylandedAt", "", count=1)
    block = set_field(block, "met", "0", count=1)
    block = set_field(block, "lct", f"{save_ut:.14f}", count=1)
    block = set_field(block, "lastUT", f"{save_ut:.14f}", count=1)
    block = set_field(block, "distanceTraveled", "0", count=1)
    block = set_field(block, "lat", "0", count=1)
    block = set_field(block, "lon", "0", count=1)
    block = set_field(block, "alt", f"{altitude:.9f}", count=1)
    block = set_field(block, "hgt", f"{altitude:.9f}", count=1)
    block = set_field(block, "vesselSpawning", "False", count=1)
    block = re.sub(r"(?m)^(\s*gpsActive = ).*$", r"\g<1>True", block)

    replacements = {
        "SMA": f"{sma:.12f}",
        "ECC": f"{ecc:.12f}",
        "INC": f"{inc:.12f}",
        "LPE": f"{lpe:.12f}",
        "LAN": f"{lan:.12f}",
        "MNA": f"{mna:.15f}",
        "EPH": f"{save_ut:.14f}",
        "REF": "1",
    }
    for key, value in replacements.items():
        block = set_field(block, key, value, count=1)
    block = set_field(block, "IDENT", "Squad/Kerbin", count=1)
    block = unique_part_numbers(block, launch_id)

    info = {
        "name": name,
        "source_object": sat["OBJECT_NAME"],
        "norad_cat_id": sat["NORAD_CAT_ID"],
        "epoch": sat["EPOCH"],
        "sma_m": sma,
        "altitude_m": altitude,
        "eccentricity": ecc,
        "inclination_deg": inc,
        "raan_deg": lan,
        "arg_periapsis_deg": lpe,
        "mean_anomaly_rad": mna,
        "pid": pid,
    }
    return block, info


def main() -> None:
    if not SAVE.exists():
        raise FileNotFoundError(SAVE)
    sats = json.loads(GPS_JSON.read_text(encoding="utf-8"))
    if len(sats) < 24:
        raise RuntimeError(f"Expected an operational GPS constellation, found only {len(sats)} objects")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = WORK / f"gps-save-backup-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(SAVE, backup_dir / SAVE.name)
    if LOADMETA.exists():
        shutil.copy2(LOADMETA, backup_dir / LOADMETA.name)

    text = SAVE.read_text(encoding="utf-8-sig", newline="")
    newline = "\r\n" if "\r\n" in text else "\n"
    vessels = extract_vessels(text)
    template = None
    removed_names: list[str] = []
    kept_ranges: list[tuple[int, int, str]] = []
    for start, end, block in vessels:
        name = field(block, "name")
        if name == "GPS Probe 1":
            template = block
        if name in OLD_GPS_NAMES:
            removed_names.append(name or "")
            continue
        kept_ranges.append((start, end, block))

    if template is None:
        raise RuntimeError("Could not find GPS Probe 1 to use as the craft template")

    # Remove any previous generated GPS PRN vessels before adding this fresh constellation.
    final_ranges: list[tuple[int, int, str]] = []
    for start, end, block in kept_ranges:
        name = field(block, "name") or ""
        if name.startswith("GPS PRN "):
            removed_names.append(name)
            continue
        final_ranges.append((start, end, block))

    save_ut_match = re.search(r"(?m)^\s*UT = ([0-9.Ee+-]+)", text)
    if not save_ut_match:
        raise RuntimeError("Could not find FLIGHTSTATE UT")
    save_ut = float(save_ut_match.group(1))

    launch_id_match = re.search(r"(?m)^\s*launchID = (\d+)", text)
    base_launch_id = int(launch_id_match.group(1)) if launch_id_match else 100
    new_blocks: list[str] = []
    summary: list[dict] = []
    for offset, sat in enumerate(sorted(sats, key=lambda s: int(re.search(r"PRN\s+(\d+)", s["OBJECT_NAME"]).group(1)))):
        block, info = make_vessel(template, sat, save_ut, base_launch_id + 1 + offset)
        new_blocks.append(block)
        summary.append(info)

    if not final_ranges:
        raise RuntimeError("No vessel ranges found after filtering")

    first_vessel = min(start for start, _, _ in vessels)
    last_vessel = max(end for _, end, _ in vessels)
    before = text[:first_vessel]
    middle = (newline * 2).join(block for _, _, block in final_ranges + [(0, 0, b) for b in new_blocks])
    after = text[last_vessel:]
    new_text = before + middle + after

    if new_text.count("{") != new_text.count("}"):
        raise RuntimeError("Brace validation failed after save edit")

    SAVE.write_text(new_text, encoding="utf-8", newline="")

    if LOADMETA.exists():
        meta = LOADMETA.read_text(encoding="utf-8-sig", newline="")
        old_count_match = re.search(r"(?m)^(\s*vesselCount = )(\d+)$", meta)
        if old_count_match:
            old_count = int(old_count_match.group(2))
            new_count = old_count - len(set(OLD_GPS_NAMES).intersection(removed_names)) + len(new_blocks)
            meta = re.sub(r"(?m)^(\s*vesselCount = )\d+$", rf"\g<1>{new_count}", meta, count=1)
            LOADMETA.write_text(meta, encoding="utf-8", newline="")

    with SUMMARY.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        writer.writeheader()
        writer.writerows(summary)

    print(f"Backed up save to: {backup_dir}")
    print(f"Removed old GPS vessels: {', '.join(removed_names) if removed_names else 'none'}")
    print(f"Added GPS vessels: {len(new_blocks)}")
    print(f"Wrote summary: {SUMMARY}")


if __name__ == "__main__":
    main()
