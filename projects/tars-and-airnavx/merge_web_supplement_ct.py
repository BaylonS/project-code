#!/usr/bin/env python3
"""Merge web supplement chunks into the CT AirnavX SQLite index."""

from __future__ import annotations

import sqlite3
from pathlib import Path


MAIN = Path("/opt/airnavx_index/airnavx_index.sqlite")
SUPP = Path("/opt/airnavx_index/web_supplement/web_supplement.sqlite")
SOURCE = "WEB_SUPPLEMENT"


def main() -> None:
    if not MAIN.exists():
        raise SystemExit(f"main index missing: {MAIN}")
    if not SUPP.exists():
        raise SystemExit(f"supplement missing: {SUPP}")

    conn = sqlite3.connect(str(MAIN))
    conn.row_factory = sqlite3.Row
    try:
        existing_ids = [
            row[0]
            for row in conn.execute("SELECT id FROM chunks WHERE source_zip = ?", (SOURCE,)).fetchall()
        ]
        for rowid in existing_ids:
            conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (rowid,))
        conn.execute("DELETE FROM chunks WHERE source_zip = ?", (SOURCE,))

        conn.execute("ATTACH DATABASE ? AS supp", (str(SUPP),))
        rows = conn.execute(
            """
            SELECT chunk_id, source_zip, path, extension, document_hint, title_hint,
                   customization_hint, aircraft_hint, chunk_index, text
            FROM supp.chunks
            ORDER BY id
            """
        ).fetchall()
        inserted = 0
        for row in rows:
            cur = conn.execute(
                """
                INSERT INTO chunks (
                    chunk_id, source_zip, path, extension, document_hint, title_hint,
                    customization_hint, aircraft_hint, chunk_index, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(row),
            )
            conn.execute(
                "INSERT INTO chunks_fts(rowid, text, title_hint, document_hint, path) VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, row["text"], row["title_hint"], row["document_hint"], row["path"]),
            )
            inserted += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM chunks WHERE source_zip = ?", (SOURCE,)).fetchone()[0]
        print(f"web supplement inserted={inserted} removed={len(existing_ids)} total={total}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
