#!/usr/bin/env python3
"""Inspect current Icarus talent tables against a Characters.json save."""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
from pathlib import Path


PAK_PREFIX = "C:/BA/work/92bbbfa44df12262/Temp/Data/Talents/"


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


def load_characters(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as handle:
        root = json.load(handle)
    return [json.loads(entry) for entry in root["Characters.json"]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repak", type=Path, required=True)
    parser.add_argument("--pak", type=Path, required=True)
    parser.add_argument("--characters", type=Path, required=True)
    args = parser.parse_args()

    talent_rows = read_pak_json(args.repak, args.pak, "D_Talents.json")["Rows"]
    tree_rows = read_pak_json(args.repak, args.pak, "D_TalentTrees.json")["Rows"]
    characters = load_characters(args.characters)

    saved_names = {
        talent["RowName"]
        for character in characters
        for talent in character.get("Talents", [])
    }
    talent_by_name = {row["Name"]: row for row in talent_rows}

    saved_tree_counts = collections.Counter(
        (talent_by_name[name].get("TalentTree") or {}).get("RowName", "None")
        for name in saved_names
        if name in talent_by_name
    )
    current_tree_counts = collections.Counter(
        (row.get("TalentTree") or {}).get("RowName", "None")
        for row in talent_rows
    )

    print("Tree | Archetype | Current rows | Present in saves")
    for tree in tree_rows:
        name = tree["Name"]
        archetype = (tree.get("Archetype") or {}).get("RowName", "None")
        print(
            f"{name} | {archetype} | "
            f"{current_tree_counts[name]} | {saved_tree_counts[name]}"
        )

    missing_from_table = sorted(saved_names - set(talent_by_name))
    print(f"\nSaved rows absent from current D_Talents: {len(missing_from_table)}")
    print(missing_from_table)

    player_archetypes = {
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
    player_trees = {
        tree["Name"]
        for tree in tree_rows
        if (tree.get("Archetype") or {}).get("RowName") in player_archetypes
    }
    selected = [
        talent
        for talent in talent_rows
        if (talent.get("TalentTree") or {}).get("RowName") in player_trees
    ]
    rank_counts = collections.Counter(
        len(talent.get("Rewards") or [{"GrantedStats": {}, "GrantedFlags": []}])
        for talent in selected
    )
    print(f"\nCurrent player/blueprint trees selected: {len(player_trees)}")
    print(sorted(player_trees))
    print(f"Current player/blueprint talents selected: {len(selected)}")
    print(f"Maximum-rank distribution: {dict(sorted(rank_counts.items()))}")
    explicit_empty_rewards = sorted(
        talent["Name"]
        for talent in selected
        if "Rewards" in talent and not talent["Rewards"]
    )
    print(f"Explicit empty reward rows: {len(explicit_empty_rewards)}")
    print(explicit_empty_rewards)
    default_unlocked = sorted(
        talent["Name"] for talent in selected if talent.get("bDefaultUnlocked")
    )
    storable = [
        talent
        for talent in selected
        if not ("Rewards" in talent and not talent["Rewards"])
        and not talent.get("bDefaultUnlocked")
    ]
    print(f"Default-unlocked rows (not stored): {len(default_unlocked)}")
    print(default_unlocked)
    print(f"Final storable maxed talents/blueprints: {len(storable)}")


if __name__ == "__main__":
    main()
