from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "minddrop.sqlite3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT NOT NULL UNIQUE,
              display_name TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              is_admin INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              summary TEXT NOT NULL DEFAULT '',
              source_url TEXT NOT NULL DEFAULT '',
              source_type TEXT NOT NULL DEFAULT 'manual',
              visibility TEXT NOT NULL DEFAULT 'private',
              tags_json TEXT NOT NULL DEFAULT '[]',
              pinned INTEGER NOT NULL DEFAULT 0,
              sort_order REAL NOT NULL DEFAULT 0,
              checklist_json TEXT NOT NULL DEFAULT '{}',
              col_span INTEGER NOT NULL DEFAULT 1,
              height_px INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tag_colors (
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              tag TEXT NOT NULL,
              color TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (user_id, tag)
            );
            """
        )
        ensure_column(conn, "chunks", "pinned", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "chunks", "sort_order", "REAL NOT NULL DEFAULT 0")
        ensure_column(conn, "chunks", "checklist_json", "TEXT NOT NULL DEFAULT '{}'")
        ensure_column(conn, "chunks", "col_span", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(conn, "chunks", "height_px", "INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE chunks SET sort_order = id WHERE sort_order = 0")
        conn.execute("DROP TABLE IF EXISTS chunks_fts")
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(title, body, summary, tags)")
        for row in conn.execute("SELECT id, title, body, summary, tags_json FROM chunks").fetchall():
            upsert_chunk_fts(
                conn,
                row["id"],
                row["title"],
                row["body"],
                row["summary"],
                json.loads(row["tags_json"] or "[]"),
            )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return secrets.compare_digest(candidate.hex(), digest)


def user_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def create_user(conn: sqlite3.Connection, email: str, password: str, display_name: str) -> sqlite3.Row:
    is_first = user_count(conn) == 0
    cursor = conn.execute(
        """
        INSERT INTO users (email, display_name, password_hash, is_admin, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (email.lower().strip(), display_name.strip() or email.split("@")[0], hash_password(password), 1 if is_first else 0, now_iso()),
    )
    return get_user_by_id(conn, cursor.lastrowid)


def get_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def chunk_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["tags"] = json.loads(item.pop("tags_json") or "[]")
    item["pinned"] = bool(item.get("pinned", 0))
    item["checklist"] = json.loads(item.pop("checklist_json", "{}") or "{}")
    return item


def upsert_chunk_fts(conn: sqlite3.Connection, chunk_id: int, title: str, body: str, summary: str, tags: list[str]) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
    conn.execute(
        "INSERT INTO chunks_fts(rowid, title, body, summary, tags) VALUES (?, ?, ?, ?, ?)",
        (chunk_id, title, body, summary, " ".join(tags)),
    )


def create_chunk(
    conn: sqlite3.Connection,
    user_id: int,
    body: str,
    title: str,
    summary: str,
    tags: list[str],
    source_url: str = "",
    source_type: str = "manual",
    visibility: str = "private",
) -> dict[str, Any]:
    timestamp = now_iso()
    clean_tags = normalize_tags(tags)
    cursor = conn.execute(
        """
        INSERT INTO chunks (user_id, title, body, summary, source_url, source_type, visibility, tags_json, sort_order, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            title.strip()[:140] or "Untitled",
            body.strip(),
            summary.strip(),
            source_url.strip(),
            source_type.strip() or "manual",
            visibility if visibility in {"private", "team"} else "private",
            json.dumps(clean_tags),
            datetime.now(timezone.utc).timestamp(),
            timestamp,
            timestamp,
        ),
    )
    chunk_id = cursor.lastrowid
    upsert_chunk_fts(conn, chunk_id, title, body, summary, clean_tags)
    return chunk_to_dict(conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone())


def list_chunks(
    conn: sqlite3.Connection,
    user_id: int,
    q: str = "",
    tag: str = "",
    scope: str = "all",
    limit: int = 100,
) -> list[dict[str, Any]]:
    args: list[Any] = []
    visibility_clause = "(chunks.user_id = ? OR chunks.visibility = 'team')"
    args.append(user_id)
    if scope == "private":
        visibility_clause = "chunks.user_id = ? AND chunks.visibility = 'private'"
    elif scope == "team":
        visibility_clause = "chunks.visibility = 'team'"
        args = []

    tag_clause = ""
    if tag:
        tag_clause = "AND chunks.tags_json LIKE ?"
        args.append(f'%"{tag}"%')

    search = fts_query(q)
    if search:
        sql = f"""
        SELECT chunks.*
        FROM chunks_fts
        JOIN chunks ON chunks_fts.rowid = chunks.id
        WHERE chunks_fts MATCH ? AND {visibility_clause} {tag_clause}
        ORDER BY chunks.pinned DESC, chunks.sort_order DESC, rank, chunks.created_at DESC
        LIMIT ?
        """
        args = [search] + args + [limit]
    else:
        sql = f"""
        SELECT chunks.*
        FROM chunks
        WHERE {visibility_clause} {tag_clause}
        ORDER BY chunks.pinned DESC, chunks.sort_order DESC, chunks.created_at DESC
        LIMIT ?
        """
        args.append(limit)
    return [chunk_to_dict(row) for row in conn.execute(sql, args).fetchall()]


def fts_query(raw: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_]+", raw)
    return " OR ".join(f"{term.lower()}*" for term in terms[:12])


def all_tags(conn: sqlite3.Connection, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT tags_json FROM chunks WHERE user_id = ? OR visibility = 'team'",
        (user_id,),
    ).fetchall()
    tags = set()
    for row in rows:
        tags.update(json.loads(row["tags_json"] or "[]"))
    return sorted(tags)


def tag_colors(conn: sqlite3.Connection, user_id: int) -> dict[str, str]:
    rows = conn.execute("SELECT tag, color FROM tag_colors WHERE user_id = ?", (user_id,)).fetchall()
    return {row["tag"]: row["color"] for row in rows}


def set_tag_color(conn: sqlite3.Connection, user_id: int, tag: str, color: str) -> None:
    clean_tag = normalize_tags([tag])
    if not clean_tag:
        raise ValueError("Invalid tag")
    clean_color = normalize_color(color)
    conn.execute(
        """
        INSERT INTO tag_colors (user_id, tag, color, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, tag)
        DO UPDATE SET color = excluded.color, updated_at = excluded.updated_at
        """,
        (user_id, clean_tag[0], clean_color, now_iso()),
    )


def random_chunk(conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM chunks
        WHERE user_id = ? OR visibility = 'team'
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return chunk_to_dict(row) if row else None


def delete_chunk(conn: sqlite3.Connection, user_id: int, chunk_id: int) -> bool:
    row = conn.execute("SELECT id FROM chunks WHERE id = ? AND user_id = ?", (chunk_id, user_id)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
    conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
    return True


def update_chunk_flags(
    conn: sqlite3.Connection,
    user_id: int,
    chunk_id: int,
    pinned: bool | None = None,
    col_span: int | None = None,
    height_px: int | None = None,
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM chunks WHERE id = ? AND user_id = ?", (chunk_id, user_id)).fetchone()
    if not row:
        return None
    if pinned is not None:
        conn.execute(
            "UPDATE chunks SET pinned = ?, updated_at = ? WHERE id = ?",
            (1 if pinned else 0, now_iso(), chunk_id),
        )
    if col_span is not None:
        conn.execute(
            "UPDATE chunks SET col_span = ?, updated_at = ? WHERE id = ?",
            (max(1, min(4, int(col_span))), now_iso(), chunk_id),
        )
    if height_px is not None:
        conn.execute(
            "UPDATE chunks SET height_px = ?, updated_at = ? WHERE id = ?",
            (max(0, min(1200, int(height_px))), now_iso(), chunk_id),
        )
    return chunk_to_dict(conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone())


def update_checklist(conn: sqlite3.Connection, user_id: int, chunk_id: int, line: int, checked: bool) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM chunks WHERE id = ? AND user_id = ?", (chunk_id, user_id)).fetchone()
    if not row:
        return None
    checklist = json.loads(row["checklist_json"] or "{}")
    key = str(max(0, line))
    if checked:
        checklist[key] = True
    else:
        checklist.pop(key, None)
    conn.execute(
        "UPDATE chunks SET checklist_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(checklist), now_iso(), chunk_id),
    )
    return chunk_to_dict(conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone())


def reorder_chunks(conn: sqlite3.Connection, user_id: int, chunk_ids: list[int]) -> None:
    visible_ids = {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM chunks WHERE user_id = ? OR visibility = 'team'",
            (user_id,),
        ).fetchall()
    }
    total = len(chunk_ids)
    timestamp = now_iso()
    for index, chunk_id in enumerate(chunk_ids):
        if chunk_id not in visible_ids:
            continue
        conn.execute(
            "UPDATE chunks SET sort_order = ?, updated_at = ? WHERE id = ?",
            ((total - index) * 1000, timestamp, chunk_id),
        )


def normalize_tags(tags: list[str]) -> list[str]:
    clean = []
    seen = set()
    for tag in tags:
        value = "".join(ch.lower() if ch.isalnum() else "-" for ch in tag.strip())
        value = "-".join(part for part in value.split("-") if part)
        if value and value not in seen:
            seen.add(value)
            clean.append(value[:32])
    return clean[:8]


def normalize_color(color: str) -> str:
    value = color.strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return value.lower()
    raise ValueError("Color must be a hex value like #62d2a2")
