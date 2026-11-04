#!/usr/bin/env python3
"""
Build a direct-search index from AirnavX Standalone data zip files.

This is intentionally independent of the AirnavX web UI. It reads files such as
data/ata.zip and data/XML.zip, extracts readable manual text, chunks it, and
writes:

- inventory.json
- airnavx_chunks.jsonl
- airnavx_index.sqlite, when SQLite FTS5 is available
"""

from __future__ import annotations

import argparse
import collections
import html
import json
import os
import re
import sqlite3
import sys
import zipfile
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator


TEXT_EXTENSIONS = {
    ".xml",
    ".sgm",
    ".sgml",
    ".html",
    ".htm",
    ".xhtml",
    ".txt",
    ".json",
    ".csv",
    ".properties",
}

PDF_EXTENSIONS = {".pdf"}

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".jar",
    ".class",
    ".exe",
    ".dll",
    ".so",
    ".db",
    ".sqlite",
    ".idx",
    ".bin",
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag.lower() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "td", "th"}:
            self.parts.append("\n")
        for key, value in attrs:
            if value and key.lower() in {"title", "alt", "label"}:
                self.parts.append(str(value))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data:
            self.parts.append(data)

    def text(self) -> str:
        return normalize_text(" ".join(self.parts))


@dataclass
class Chunk:
    chunk_id: str
    source_zip: str
    path: str
    extension: str
    document_hint: str
    title_hint: str
    customization_hint: str
    aircraft_hint: str
    chunk_index: int
    text: str


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\x00", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def bytes_to_text(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def strip_markup(raw_text: str) -> str:
    parser = TextExtractor()
    try:
        parser.feed(raw_text)
        text = parser.text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw_text)
        text = normalize_text(text)
    return text


def extract_pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            return ""

    import io

    try:
        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return normalize_text("\n".join(parts))
    except Exception:
        return ""


def readable_text(path: str, raw: bytes) -> str:
    ext = Path(path).suffix.lower()
    if ext in PDF_EXTENSIONS:
        return extract_pdf_text(raw)

    text = bytes_to_text(raw)
    if ext in {".xml", ".sgm", ".sgml", ".html", ".htm", ".xhtml"} or "<" in text[:500]:
        return strip_markup(text)
    return normalize_text(text)


def chunks_for_text(text: str, size: int = 1800, overlap: int = 240) -> Iterator[str]:
    text = normalize_text(text)
    if not text:
        return

    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if boundary > start + int(size * 0.55):
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            yield chunk
        if end >= len(text):
            break
        start = max(0, end - overlap)


def guess_document(path: str, text: str) -> str:
    haystack = f"{path}\n{text[:1000]}".upper()
    for doc in ("AMM", "ASM", "TSM", "IPC", "SRM", "NTM", "MPD", "TEM", "WDM", "AWM", "ESPM"):
        if re.search(rf"\b{re.escape(doc)}\b", haystack):
            return doc
    return ""


def guess_title(path: str, text: str) -> str:
    candidates = []
    for line in normalize_text(text[:2000]).splitlines():
        line = line.strip()
        if 8 <= len(line) <= 180:
            candidates.append(line)
    if candidates:
        return candidates[0]
    return Path(path).name


def guess_customization(path: str, text: str) -> str:
    haystack = f"{path}\n{text[:3000]}".upper()
    match = re.search(r"\b[A-Z]{2,4}_CUSDOC\b", haystack)
    return match.group(0) if match else ""


def guess_aircraft(path: str, text: str) -> str:
    haystack = f"{path}\n{text[:5000]}".upper()
    matches = re.findall(r"\b(?:N)?(\d{3}[A-Z]{0,3})\b", haystack)
    if "305NV" in matches:
        return "305NV"
    return matches[0] if matches else ""


def find_zips(airnavx_root: Path, data_dir: Path | None) -> list[Path]:
    base = data_dir if data_dir else airnavx_root / "data"
    preferred = [base / "ata.zip", base / "XML.zip"]
    found = [path for path in preferred if path.exists()]
    for path in sorted(base.glob("*.zip")):
        if path not in found:
            found.append(path)
    return found


def inventory(zips: Iterable[Path], output_dir: Path) -> dict:
    summary = {
        "zips": [],
        "extension_counts": collections.Counter(),
        "samples_by_extension": collections.defaultdict(list),
    }

    for zip_path in zips:
        with zipfile.ZipFile(zip_path) as zf:
            infos = [info for info in zf.infolist() if not info.is_dir()]
            zip_summary = {
                "zip": str(zip_path),
                "file_count": len(infos),
                "uncompressed_bytes": sum(info.file_size for info in infos),
            }
            summary["zips"].append(zip_summary)
            for info in infos:
                ext = Path(info.filename).suffix.lower() or "[no extension]"
                summary["extension_counts"][ext] += 1
                samples = summary["samples_by_extension"][ext]
                if len(samples) < 20:
                    samples.append(info.filename)

    serializable = {
        "zips": summary["zips"],
        "extension_counts": dict(summary["extension_counts"].most_common()),
        "samples_by_extension": dict(summary["samples_by_extension"]),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "inventory.json").write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    return serializable


def iter_chunks(zips: Iterable[Path], max_file_mb: int, chunk_size: int, overlap: int) -> Iterator[Chunk]:
    for zip_path in zips:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                ext = Path(info.filename).suffix.lower()
                if ext in BINARY_EXTENSIONS:
                    continue
                if ext not in TEXT_EXTENSIONS and ext not in PDF_EXTENSIONS:
                    continue
                if max_file_mb > 0 and info.file_size > max_file_mb * 1024 * 1024:
                    continue
                try:
                    raw = zf.read(info)
                except Exception:
                    continue
                text = readable_text(info.filename, raw)
                if len(text) < 30:
                    continue

                doc = guess_document(info.filename, text)
                title = guess_title(info.filename, text)
                customization = guess_customization(info.filename, text)
                aircraft = guess_aircraft(info.filename, text)

                for index, chunk_text in enumerate(chunks_for_text(text, chunk_size, overlap)):
                    chunk_id = f"{zip_path.name}:{info.filename}:{index}"
                    yield Chunk(
                        chunk_id=chunk_id,
                        source_zip=zip_path.name,
                        path=info.filename,
                        extension=ext,
                        document_hint=doc,
                        title_hint=title,
                        customization_hint=customization,
                        aircraft_hint=aircraft,
                        chunk_index=index,
                        text=chunk_text,
                    )


def create_sqlite(db_path: Path, chunks: Iterable[Chunk]) -> int:
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                chunk_id TEXT UNIQUE,
                source_zip TEXT,
                path TEXT,
                extension TEXT,
                document_hint TEXT,
                title_hint TEXT,
                customization_hint TEXT,
                aircraft_hint TEXT,
                chunk_index INTEGER,
                text TEXT
            )
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE chunks_fts USING fts5(
                    text,
                    title_hint,
                    document_hint,
                    path,
                    content='chunks',
                    content_rowid='id'
                )
                """
            )
            has_fts = True
        except sqlite3.OperationalError:
            has_fts = False

        count = 0
        for chunk in chunks:
            cur = conn.execute(
                """
                INSERT INTO chunks (
                    chunk_id, source_zip, path, extension, document_hint, title_hint,
                    customization_hint, aircraft_hint, chunk_index, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.source_zip,
                    chunk.path,
                    chunk.extension,
                    chunk.document_hint,
                    chunk.title_hint,
                    chunk.customization_hint,
                    chunk.aircraft_hint,
                    chunk.chunk_index,
                    chunk.text,
                ),
            )
            if has_fts:
                conn.execute(
                    "INSERT INTO chunks_fts(rowid, text, title_hint, document_hint, path) VALUES (?, ?, ?, ?, ?)",
                    (cur.lastrowid, chunk.text, chunk.title_hint, chunk.document_hint, chunk.path),
                )
            count += 1
            if count % 1000 == 0:
                conn.commit()
                print(f"Indexed {count} chunks...", file=sys.stderr)
        conn.commit()
        return count
    finally:
        conn.close()


def build(args: argparse.Namespace) -> None:
    airnavx_root = Path(args.airnavx_root)
    data_dir = Path(args.data_dir) if args.data_dir else None
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    zips = find_zips(airnavx_root, data_dir)
    if not zips:
        raise SystemExit(f"No zip files found under {data_dir or airnavx_root / 'data'}")

    inv = inventory(zips, output_dir)
    print(json.dumps(inv["extension_counts"], indent=2))

    jsonl_path = output_dir / "airnavx_chunks.jsonl"
    db_path = output_dir / "airnavx_index.sqlite"

    def chunk_stream() -> Iterator[Chunk]:
        with jsonl_path.open("w", encoding="utf-8") as handle:
            for chunk in iter_chunks(zips, args.max_file_mb, args.chunk_size, args.overlap):
                handle.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
                yield chunk

    count = create_sqlite(db_path, chunk_stream())
    manifest = {
        "airnavx_root": str(airnavx_root),
        "data_dir": str(data_dir or airnavx_root / "data"),
        "zips": [str(path) for path in zips],
        "chunk_count": count,
        "jsonl": str(jsonl_path),
        "sqlite": str(db_path),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


def search(args: argparse.Namespace) -> None:
    db_path = Path(args.index) / "airnavx_index.sqlite" if Path(args.index).is_dir() else Path(args.index)
    if not db_path.exists():
        raise SystemExit(f"Index not found: {db_path}")

    terms = [term for term in re.split(r"\s+", args.query.strip()) if term]
    fts_query = " ".join(f'"{term}"' if not term.endswith("*") else term for term in terms)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        where = []
        params: list[str] = []
        if args.aircraft:
            where.append("(aircraft_hint = ? OR text LIKE ? OR path LIKE ?)")
            params.extend([args.aircraft.upper(), f"%{args.aircraft}%", f"%{args.aircraft}%"])
        if args.customization:
            where.append("(customization_hint = ? OR text LIKE ? OR path LIKE ?)")
            params.extend([args.customization.upper(), f"%{args.customization}%", f"%{args.customization}%"])
        filter_sql = (" AND " + " AND ".join(where)) if where else ""
        sql = f"""
            SELECT c.*
            FROM chunks_fts f
            JOIN chunks c ON c.id = f.rowid
            WHERE chunks_fts MATCH ? {filter_sql}
            LIMIT ?
        """
        rows = conn.execute(sql, [fts_query, *params, args.limit]).fetchall()
    except sqlite3.OperationalError:
        like_terms = [f"%{term}%" for term in terms]
        sql = "SELECT * FROM chunks WHERE " + " AND ".join("text LIKE ?" for _ in like_terms) + " LIMIT ?"
        rows = conn.execute(sql, [*like_terms, args.limit]).fetchall()
    finally:
        conn.close()

    for row in rows:
        print("=" * 90)
        print(f"Source: {row['source_zip']} :: {row['path']}")
        print(f"Doc: {row['document_hint']}  Title: {row['title_hint']}")
        print(f"Customization: {row['customization_hint']}  Aircraft: {row['aircraft_hint']}")
        print()
        print(row["text"][: args.max_chars])


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/search a direct AirnavX manual text index.")
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="Build the direct-search AirnavX index.")
    build_parser.add_argument("--airnavx-root", default=r"E:\AirnavX Standalone Rev 46")
    build_parser.add_argument("--data-dir", default="")
    build_parser.add_argument("--output", default="airnavx_index")
    build_parser.add_argument("--max-file-mb", type=int, default=80)
    build_parser.add_argument("--chunk-size", type=int, default=1800)
    build_parser.add_argument("--overlap", type=int, default=240)
    build_parser.set_defaults(func=build)

    search_parser = sub.add_parser("search", help="Search an existing AirnavX index.")
    search_parser.add_argument("query")
    search_parser.add_argument("--index", default="airnavx_index")
    search_parser.add_argument("--aircraft", default="")
    search_parser.add_argument("--customization", default="AAY_CUSDOC")
    search_parser.add_argument("--limit", type=int, default=8)
    search_parser.add_argument("--max-chars", type=int, default=1400)
    search_parser.set_defaults(func=search)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
