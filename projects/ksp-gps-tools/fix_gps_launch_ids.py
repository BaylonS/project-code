from __future__ import annotations

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


def main() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = WORK / f"gps-launchid-fix-backup-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(SAVE, backup_dir / SAVE.name)

    text = SAVE.read_text(encoding="utf-8-sig", newline="")
    newline = "\r\n" if "\r\n" in text else "\n"
    vessels = extract_vessels(text)
    fixed = 0
    rebuilt = []
    next_launch_id = 12
    for _, _, block in vessels:
        if field(block, "name").startswith("GPS PRN "):
            block = re.sub(r"(?m)^(\s*launchID = )\d+", rf"\g<1>{next_launch_id}", block)
            next_launch_id += 1
            fixed += 1
        rebuilt.append(block)

    first = min(start for start, _, _ in vessels)
    last = max(end for _, end, _ in vessels)
    text = re.sub(r"(?m)^(\s*launchID = )\d+", rf"\g<1>{next_launch_id - 1}", text, count=1)
    new_text = text[:first] + (newline * 2).join(rebuilt) + text[last:]
    if new_text.count("{") != new_text.count("}"):
        raise RuntimeError("Brace validation failed")
    SAVE.write_text(new_text, encoding="utf-8", newline="")
    print(f"Backed up save to: {backup_dir}")
    print(f"Fixed GPS launch IDs: {fixed}")
    print(f"Top launchID is now: {next_launch_id - 1}")


if __name__ == "__main__":
    main()
