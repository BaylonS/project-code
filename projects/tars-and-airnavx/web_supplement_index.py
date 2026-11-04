#!/usr/bin/env python3
"""Build supplemental SQLite chunks from small Airbus web reference pages."""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from collections import deque
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urldefrag, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


AIRBUS_ABBREVIATIONS_URL = "https://www.aviationhunt.com/airbus-abbreviations/"
AIRLINETECHS_START_URL = "http://mobile.airlinetechs.com/airbus/airbusHome.php"
AIRLINETECHS_PREFIX = "http://mobile.airlinetechs.com/airbus/"


@dataclass
class WebChunk:
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


class AbbreviationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_li = False
        self.current: list[str] = []
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "li":
            self.in_li = True
            self.current = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "li" and self.in_li:
            text = normalize_text(" ".join(self.current))
            if " - " in text or " - " in text.replace("\u2013", "-"):
                self.items.append(text)
            self.in_li = False
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.in_li:
            self.current.append(data)


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for key, value in attrs:
                if key.lower() == "href" and value:
                    self.links.append(value)
        if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)

    @property
    def title(self) -> str:
        return normalize_text(" ".join(self.title_parts))

    @property
    def text(self) -> str:
        return normalize_text(" ".join(self.text_parts))


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\x00", " ")
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def safe_url(url: str) -> str:
    parsed = urlparse(url)
    path = quote(parsed.path, safe="/%")
    query = quote(parsed.query, safe="=&?/%")
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, query, ""))


def fetch_url(url: str) -> str:
    request = Request(
        safe_url(url),
        headers={"User-Agent": "Mozilla/5.0 (compatible; AirnavXSupplementIndexer/1.0)"},
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_abbreviations(html_text: str) -> list[tuple[str, str]]:
    parser = AbbreviationParser()
    parser.feed(html_text)
    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in parser.items:
        item = normalize_text(item)
        if " - " not in item:
            continue
        abbr, meaning = item.split(" - ", 1)
        abbr = normalize_text(abbr)
        meaning = normalize_text(meaning)
        if not abbr or not meaning:
            continue
        if len(abbr) > 24 or len(meaning) > 160:
            continue
        key = (abbr.upper(), meaning.upper())
        if key in seen:
            continue
        seen.add(key)
        entries.append((abbr, meaning))
    return entries


def chunks_for_abbreviations(entries: list[tuple[str, str]], url: str, group_size: int = 45) -> list[WebChunk]:
    chunks: list[WebChunk] = []
    for chunk_index, start in enumerate(range(0, len(entries), group_size)):
        group = entries[start : start + group_size]
        lines = [
            "Airbus abbreviation reference.",
            f"Source URL: {url}",
            "Use this only to expand abbreviations, then search the official AirNavX/manual index for the maintenance answer.",
            "",
        ]
        lines.extend(f"{abbr} = {meaning}" for abbr, meaning in group)
        chunks.append(
            WebChunk(
                chunk_id=f"web:aviationhunt-airbus-abbreviations:{chunk_index}",
                source_zip="WEB_SUPPLEMENT",
                path=url,
                extension=".html",
                document_hint="ABBREVIATIONS",
                title_hint="Airbus Abbreviations - AviationHunt",
                customization_hint="AAY_CUSDOC",
                aircraft_hint="A320",
                chunk_index=chunk_index,
                text="\n".join(lines),
            )
        )
    return chunks


def document_hint_for_airlinetechs(url: str, text: str) -> str:
    upper = f"{url}\n{text[:1200]}".upper()
    if "/MEL/" in upper:
        return "MEL_QUICK_REF"
    if "ACRONYM" in upper:
        return "ABBREVIATIONS"
    if "CIRCUIT BREAKER" in upper or "C/B" in upper or "ALLCIRCUITBREAKERS" in upper:
        return "CB"
    return "AIRLINETECHS"


def title_for_airlinetechs(url: str, page_title: str, text: str) -> str:
    heading = ""
    for line in text.splitlines():
        line = normalize_text(line)
        if 4 <= len(line) <= 90 and not re.search(r"^(BACK|HOME|AIRBUS|MOBILE TECH)$", line, re.IGNORECASE):
            heading = line
            break
    basename = Path(urlparse(url).path).stem
    return normalize_text(" - ".join(part for part in ["AirlineTechs Airbus", heading or basename, page_title] if part))


def chunks_for_text(text: str, size: int = 1800, overlap: int = 220):
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


def crawl_airlinetechs(start_url: str, max_pages: int) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    queued: set[str] = {start_url}
    queue: deque[str] = deque([start_url])
    pages: list[tuple[str, str, str]] = []
    while queue and len(seen) < max_pages:
        url = queue.popleft()
        queued.discard(url)
        if url in seen:
            continue
        try:
            html_text = fetch_url(url)
        except Exception as exc:
            print(f"skip {url}: {exc}")
            seen.add(url)
            continue
        parser = PageParser()
        parser.feed(html_text)
        text = parser.text
        if text:
            pages.append((url, parser.title, text))
        seen.add(url)
        for href in parser.links:
            full = urldefrag(urljoin(url, href))[0]
            if not full.startswith(AIRLINETECHS_PREFIX):
                continue
            if re.search(r"\.(?:jpg|jpeg|png|gif|css|js|ico|pdf|zip)$", full, re.IGNORECASE):
                continue
            full = safe_url(full)
            if full not in seen and full not in queued:
                queue.append(full)
                queued.add(full)
    return pages


def chunks_for_airlinetechs(pages: list[tuple[str, str, str]]) -> list[WebChunk]:
    chunks: list[WebChunk] = []
    chunk_index = 0
    for url, page_title, text in pages:
        doc_hint = document_hint_for_airlinetechs(url, text)
        title = title_for_airlinetechs(url, page_title, text)
        for local_index, chunk_text in enumerate(chunks_for_text(text)):
            chunks.append(
                WebChunk(
                    chunk_id=f"web:airlinetechs:{urlparse(url).path}:{local_index}",
                    source_zip="WEB_SUPPLEMENT",
                    path=url,
                    extension=".html",
                    document_hint=doc_hint,
                    title_hint=title,
                    customization_hint="AAY_CUSDOC",
                    aircraft_hint="A320",
                    chunk_index=chunk_index,
                    text=(
                        "AirlineTechs Airbus web reference. Use as supplemental quick-reference only; verify against approved manuals.\n"
                        f"Source URL: {url}\n\n{chunk_text}"
                    ),
                )
            )
            chunk_index += 1
    return chunks


def create_sqlite(db_path: Path, chunks: list[WebChunk]) -> None:
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
    parser.add_argument("--output", default="airnavx_web_supplement")
    parser.add_argument("--abbreviations-url", default=AIRBUS_ABBREVIATIONS_URL)
    parser.add_argument("--airlinetechs-start-url", default=AIRLINETECHS_START_URL)
    parser.add_argument("--max-airlinetechs-pages", type=int, default=500)
    parser.add_argument("--skip-airlinetechs", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    chunks: list[WebChunk] = []
    page = fetch_url(args.abbreviations_url)
    abbreviation_entries = parse_abbreviations(page)
    chunks.extend(chunks_for_abbreviations(abbreviation_entries, args.abbreviations_url))

    airlinetechs_pages: list[tuple[str, str, str]] = []
    if not args.skip_airlinetechs:
        airlinetechs_pages = crawl_airlinetechs(args.airlinetechs_start_url, args.max_airlinetechs_pages)
        chunks.extend(chunks_for_airlinetechs(airlinetechs_pages))

    if not chunks:
        raise SystemExit("No web supplement chunks built.")

    create_sqlite(output / "web_supplement.sqlite", chunks)
    with (output / "web_supplement_chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(asdict(chunk), ensure_ascii=True) + "\n")
    manifest = {
        "source": "web_supplement",
        "abbreviations_url": args.abbreviations_url,
        "abbreviation_entry_count": len(abbreviation_entries),
        "airlinetechs_start_url": args.airlinetechs_start_url,
        "airlinetechs_page_count": len(airlinetechs_pages),
        "chunk_count": len(chunks),
        "sqlite": str(output / "web_supplement.sqlite"),
        "jsonl": str(output / "web_supplement_chunks.jsonl"),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
