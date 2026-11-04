#!/usr/bin/env python3
"""Build supplemental SQLite chunks from maintenance reference PDFs."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass
class PdfChunk:
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
    value = (value or "").replace("\x00", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def chunks_for_text(text: str, size: int = 1800, overlap: int = 240):
    text = normalize_text(text)
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


def pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"\n[PDF page {page_index}]\n{text}")
    return normalize_text("\n".join(parts))


def iter_pdf_chunks(pdf_paths: list[Path]):
    for pdf_path in pdf_paths:
        text = pdf_text(pdf_path)
        title = pdf_path.stem
        for index, chunk_text in enumerate(chunks_for_text(text)):
            yield PdfChunk(
                chunk_id=f"pdf:{pdf_path.name}:{index}",
                source_zip="PDF_SUPPLEMENT",
                path=pdf_path.name,
                extension=".pdf",
                document_hint="RESET_GUIDE",
                title_hint=title,
                customization_hint="AAY_CUSDOC",
                aircraft_hint="A320",
                chunk_index=index,
                text=chunk_text,
            )


def create_sqlite(db_path: Path, chunks: list[PdfChunk]) -> None:
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
            conn.execute(
                "INSERT INTO chunks_fts(rowid, text, title_hint, document_hint, path) VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, chunk.text, chunk.title_hint, chunk.document_hint, chunk.path),
            )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="airnavx_pdf_supplement")
    parser.add_argument("pdfs", nargs="+")
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    pdfs = [Path(path) for path in args.pdfs]
    chunks = list(iter_pdf_chunks(pdfs))
    create_sqlite(output / "pdf_supplement.sqlite", chunks)
    with (output / "pdf_supplement_chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
    manifest = {
        "source": "pdf_supplement",
        "pdfs": [str(path) for path in pdfs],
        "chunk_count": len(chunks),
        "sqlite": str(output / "pdf_supplement.sqlite"),
        "jsonl": str(output / "pdf_supplement_chunks.jsonl"),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
