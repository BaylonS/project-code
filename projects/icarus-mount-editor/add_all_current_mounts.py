#!/usr/bin/env python3
"""Add one of every valid rideable animal mount to Mounts.json."""

from __future__ import annotations

import argparse
from pathlib import Path

from mount_editor import MountEditor
from mount_types import MOUNT_TYPES


TARGETS = [
    ("Terrenus", "Terrenus"),
    ("Horse", "Horse"),
    ("Moa", "Moa"),
    ("ArcticMoa", "Arctic Moa"),
    ("Buffalo", "Buffalo"),
    ("Tusker", "Tusker"),
    ("Zebra", "Zebra"),
    ("WoolyZebra", "Woolly Zebra"),
    ("SwampBird", "Ubi"),
    ("WoollyMammoth", "Woolly Mammoth"),
    ("Raptor", "Raptor"),
    ("DesertRaptor", "Dune Raptor"),
    ("Bull", "Bull"),
    ("Draven", "Draven"),
    ("Slinker", "Slinker"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()

    editor = MountEditor()
    editor.load(str(args.file))

    source_index = next(
        (
            mount.index
            for mount in editor.mounts
            if mount.get_value("AISetupRowName") == "Tamed_Forest_Wolf"
        ),
        None,
    )
    if source_index is None:
        raise ValueError("A Tamed_Forest_Wolf source record is required")

    existing_ai = {
        mount.get_value("AISetupRowName")
        for mount in editor.mounts
    }
    added: list[tuple[int, str, str]] = []
    skipped: list[tuple[str, str]] = []

    for type_key, friendly_name in TARGETS:
        mount_type = MOUNT_TYPES[type_key]
        if mount_type.ai_setup in existing_ai:
            skipped.append((friendly_name, mount_type.ai_setup))
            continue

        index = editor.clone_mount(source_index, friendly_name, new_type=type_key)
        editor.reset_mount_talents(index)
        existing_ai.add(mount_type.ai_setup)
        added.append((index, friendly_name, mount_type.ai_setup))

    editor.save(backup=False)

    print(f"ExistingBefore={len(editor.mounts) - len(added)}")
    print(f"AddedCount={len(added)}")
    for index, name, ai_setup in added:
        print(f"Added[{index}]={name}|{ai_setup}")
    print(f"SkippedCount={len(skipped)}")
    for name, ai_setup in skipped:
        print(f"SkippedExisting={name}|{ai_setup}")
    print(f"FinalCount={len(editor.mounts)}")


if __name__ == "__main__":
    main()
