#!/usr/bin/env python3
"""Inspect Icarus mount/tame tables from the installed data.pak."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


PAK_PREFIX = "C:/BA/work/92bbbfa44df12262/Temp/Data/AI/"


def read_pak_json(repak: Path, pak: Path, name: str) -> dict:
    result = subprocess.run(
        [
            str(repak),
            "get",
            "--strip-prefix=",
            str(pak),
            f"{PAK_PREFIX}{name}",
        ],
        check=True,
        capture_output=True,
    )
    return json.loads(result.stdout.decode("utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repak", type=Path, required=True)
    parser.add_argument("--pak", type=Path, required=True)
    args = parser.parse_args()

    tables = {
        name: read_pak_json(args.repak, args.pak, name)
        for name in (
            "D_AISetup.json",
            "D_Mounts.json",
            "D_Tames.json",
        )
    }

    ai_by_name = {row["Name"]: row for row in tables["D_AISetup.json"]["Rows"]}
    tame_by_ai = {
        (row.get("TamedAI") or {}).get("RowName"): row
        for row in tables["D_Tames.json"]["Rows"]
    }

    print(
        "MountRow | AISetup | ActorClass | SaddleQuery | TalentTree | "
        "NaturallyTameable | Feature"
    )
    for mount in tables["D_Mounts.json"]["Rows"]:
        ai_name = (mount.get("AISetup") or {}).get("RowName", "None")
        ai_row = ai_by_name.get(ai_name, {})
        actor_class = ai_row.get("ActorClass", "None")
        saddle = (mount.get("RelevantSaddleQuery") or {}).get("RowName", "None")
        talent_tree = (mount.get("MountTalentArchetype") or {}).get(
            "RowName", "None"
        )
        tame = tame_by_ai.get(ai_name)
        feature = (
            ((tame or {}).get("Metadata") or {})
            .get("RequiredFeatureLevel", {})
            .get("RowName", "None")
        )
        print(
            f"{mount['Name']} | {ai_name} | {actor_class} | {saddle} | "
            f"{talent_tree} | {bool(tame)} | {feature}"
        )


if __name__ == "__main__":
    main()
