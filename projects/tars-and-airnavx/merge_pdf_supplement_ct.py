#!/usr/bin/env python3
"""Merge PDF supplement chunks into the CT AirnavX SQLite index."""

from __future__ import annotations

import sqlite3
from pathlib import Path


MAIN = Path("/opt/airnavx_index/airnavx_index.sqlite")
SUPP = Path("/opt/airnavx_index/pdf_supplement/pdf_supplement.sqlite")


def main() -> None:
    if not MAIN.exists():
        raise SystemExit(f"main index missing: {MAIN}")
    if not SUPP.exists():
        raise SystemExit(f"supplement missing: {SUPP}")

    conn = sqlite3.connect(str(MAIN))
    conn.row_factory = sqlite3.Row
    try:
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
        skipped = 0
        for row in rows:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, source_zip, path, extension, document_hint, title_hint,
                        customization_hint, aircraft_hint, chunk_index, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(row),
                )
            except sqlite3.IntegrityError:
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO chunks_fts(rowid, text, title_hint, document_hint, path) VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, row["text"], row["title_hint"], row["document_hint"], row["path"]),
            )
            inserted += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM chunks WHERE source_zip='PDF_SUPPLEMENT'").fetchone()[0]
        print(f"pdf supplement inserted={inserted} skipped={skipped} total={total}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
