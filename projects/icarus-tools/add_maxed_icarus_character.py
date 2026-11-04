#!/usr/bin/env python3
"""Add a maxed character to an Icarus Characters.json save."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import time
from pathlib import Path


PAK_PREFIX = "C:/BA/work/92bbbfa44df12262/Temp/Data/Talents/"
PLAYER_ARCHETYPES = {
    "Survival",
    "Player_Adventure",
    "Combat",
    "Construction",
    "Solo",
    "Blueprint_Player",
    "Blueprint_Crafting",
    "Blueprint_Machine",
    "Blueprint_Fabricator",
    "Blueprint_Tier5_Manufacturer",
}


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


def load_root(path: Path) -> tuple[dict, list[dict]]:
    with path.open(encoding="utf-8-sig") as handle:
        root = json.load(handle)
    if set(root) != {"Characters.json"} or not isinstance(root["Characters.json"], list):
        raise ValueError("Unexpected Characters.json wrapper structure")
    characters = [
        json.loads(entry) if isinstance(entry, str) else copy.deepcopy(entry)
        for entry in root["Characters.json"]
    ]
    return root, characters


def make_maxed_talents(repak: Path, pak: Path) -> tuple[list[dict], set[str]]:
    talent_data = read_pak_json(repak, pak, "D_Talents.json")
    tree_data = read_pak_json(repak, pak, "D_TalentTrees.json")

    player_trees = {
        tree["Name"]
        for tree in tree_data["Rows"]
        if (tree.get("Archetype") or {}).get("RowName") in PLAYER_ARCHETYPES
    }

    default_rewards = talent_data.get("Defaults", {}).get(
        "Rewards",
        [{"GrantedStats": {}, "GrantedFlags": []}],
    )
    maxed: list[dict] = []
    selected_trees: set[str] = set()
    for talent in talent_data["Rows"]:
        tree = (talent.get("TalentTree") or {}).get("RowName")
        if tree not in player_trees:
            continue
        if talent.get("bDefaultUnlocked"):
            continue
        if "Rewards" in talent and not talent["Rewards"]:
            continue

        rewards = talent.get("Rewards", default_rewards)
        maxed.append({"RowName": talent["Name"], "Rank": max(1, len(rewards))})
        selected_trees.add(tree)

    return maxed, selected_trees


def serialize_character(character: dict) -> str:
    return json.dumps(character, ensure_ascii=False, indent="\t").replace("\n", "\r\n")


def write_root(path: Path, root: dict) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        json.dump(root, handle, ensure_ascii=False, indent="\t")
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repak", type=Path, required=True)
    parser.add_argument("--pak", type=Path, required=True)
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    root, characters = load_root(args.characters)
    if any(character.get("CharacterName", "").casefold() == args.name.casefold()
           for character in characters):
        raise ValueError(f"A character named {args.name!r} already exists")

    source = next(
        (
            character
            for character in characters
            if character.get("CharacterName", "").casefold()
            == args.source_name.casefold()
        ),
        None,
    )
    if source is None:
        raise ValueError(f"Source character {args.source_name!r} was not found")

    used_slots = {int(character["ChrSlot"]) for character in characters}
    slot = next(candidate for candidate in range(100) if candidate not in used_slots)
    all_flags = sorted(
        {
            int(flag)
            for character in characters
            for flag in character.get("UnlockedFlags", [])
        }
    )
    max_xp = max(int(character.get("XP", 0)) for character in characters)
    talents, trees = make_maxed_talents(args.repak, args.pak)

    new_character = copy.deepcopy(source)
    new_character.update(
        {
            "CharacterName": args.name,
            "ChrSlot": slot,
            "XP": max_xp,
            "XP_Debt": 0,
            "IsDead": False,
            "IsAbandoned": False,
            "LastProspectId": "home",
            "Location": "Prospect_Conifer",
            "UnlockedFlags": all_flags,
            "Talents": talents,
            "TimeLastPlayed": int(time.time()),
        }
    )

    root["Characters.json"].append(serialize_character(new_character))
    write_root(args.characters, root)

    print(f"AddedName={args.name}")
    print(f"AddedSlot={slot}")
    print(f"SourceName={source['CharacterName']}")
    print(f"XP={max_xp}")
    print(f"UnlockedFlags={all_flags}")
    print(f"TalentTrees={len(trees)}")
    print(f"MaxedTalentsAndBlueprints={len(talents)}")


if __name__ == "__main__":
    main()
