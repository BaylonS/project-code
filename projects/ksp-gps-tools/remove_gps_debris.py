from __future__ import annotations

import re
import shutil
import time
from pathlib import Path


SAVE = Path(r"D:\steam\SteamApps\common\Kerbal Space Program\saves\B_S Space Program\persistent.sfs")
LOADMETA = SAVE.with_suffix(".loadmeta")
WORK = Path(r"C:\Users\baylo\Documents\Codex\2026-06-27\ple\work")


def extract_vessels(text: str) -> list[tuple[int, int, str]]:
    starts = [m.start() for m in re.finditer(r"(?m)^\t\tVESSEL\r?\n\t\t\{", text)]
    vessels: list[tuple[int, int, str]] = []
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


def main() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = WORK / f"gps-debris-removal-backup-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(SAVE, backup_dir / SAVE.name)
    if LOADMETA.exists():
        shutil.copy2(LOADMETA, backup_dir / LOADMETA.name)

    text = SAVE.read_text(encoding="utf-8-sig", newline="")
    newline = "\r\n" if "\r\n" in text else "\n"
    vessels = extract_vessels(text)
    kept: list[str] = []
    removed = 0
    for _, _, block in vessels:
        if field(block, "name") == "GPS Debris" and field(block, "sit") == "ORBITING" and field(block, "REF") == "1":
            removed += 1
        else:
            kept.append(block)

    if removed == 0:
        print("No orbiting GPS Debris vessels found.")
        return

    first = min(start for start, _, _ in vessels)
    last = max(end for _, end, _ in vessels)
    new_text = text[:first] + (newline * 2).join(kept) + text[last:]
    if new_text.count("{") != new_text.count("}"):
        raise RuntimeError("Brace validation failed")
    SAVE.write_text(new_text, encoding="utf-8", newline="")

    if LOADMETA.exists():
        meta = LOADMETA.read_text(encoding="utf-8-sig", newline="")
        match = re.search(r"(?m)^(\s*vesselCount = )(\d+)", meta)
        if match:
            new_count = max(0, int(match.group(2)) - removed)
            meta = re.sub(r"(?m)^(\s*vesselCount = )\d+", rf"\g<1>{new_count}", meta, count=1)
            LOADMETA.write_text(meta, encoding="utf-8", newline="")

    print(f"Backed up save to: {backup_dir}")
    print(f"Removed orbiting GPS Debris vessels: {removed}")


if __name__ == "__main__":
    main()
