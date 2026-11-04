#!/usr/bin/env python3
"""Small local HTTP search service for the AirnavX SQLite index."""

from __future__ import annotations

import argparse
import mimetypes
import json
import re
import sqlite3
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def normalize_aircraft(value: str) -> str:
    value = (value or "").strip().upper()
    value = re.sub(r"^N", "", value)
    value = re.sub(r"[^0-9A-Z]", "", value)
    if value.isdigit():
        return f"{value}NV"
    return value


def fts_query(query: str) -> str:
    terms = [term for term in re.split(r"\s+", query.strip()) if term]
    return " ".join(f'"{term}"' if not term.endswith("*") else term for term in terms)


ATA_SYSTEM_HINTS = {
    r"\bAIR\s+CONDITIONING\b": "21",
    r"\bCAB\s+PR\b": "21",
    r"\bCABIN\s+PRESS": "21",
    r"\bFMGC\b": "22",
    r"\bAUTO\s+FLT\b": "22",
    r"\bAUTOPILOT\b": "22",
    r"\bCIDS\b": "23",
    r"\bCABIN\s+INTERCOMMUNICATION\b": "23",
    r"\bVHF\b": "23",
    r"\bACARS\b": "23",
    r"\bELEC\b": "24",
    r"\bBATTERY\b": "24",
    r"\bAPU\b": "49",
    r"\bHYD(?:RAULIC)?\b": "29",
    r"\bPTU\b": "29",
    r"\bBRAKE\b|\bBRAKES\b|\bBSCU\b": "32",
    r"\bLANDING\s+GEAR\b": "32",
    r"\bLIGHT(?:S|ING)?\b": "33",
    r"\bENGINE\b": "72",
    r"\bEXHAUST\b": "78",
    r"\bCENTERBODY\b": "78",
    r"\bCENTRBDY\b": "78",
    r"\bCENTERBD\b": "78",
    r"\bTHRUST\s+REVERSER\b": "78",
}


def split_csv(value: str) -> list[str]:
    return [item.strip().upper() for item in re.split(r"[, ]+", value or "") if item.strip()]


def infer_ata(path: str, title: str, text: str) -> str:
    haystack = f"{path}\n{title}\n{text[:2500]}".upper()
    explicit_patterns = [
        r"\bATA\s*([0-9]{2})\b",
        r"\bENSBLIST_([0-9]{2})\b",
        r"\bEN([0-9]{2})[0-9A-Z_-]{4,}\b",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, haystack)
        if match:
            ata = match.group(1)
            if 20 <= int(ata) <= 99:
                return ata
    for pattern, ata in ATA_SYSTEM_HINTS.items():
        if re.search(pattern, haystack):
            return ata
    return ""


def infer_manual_type(source_zip: str, path: str, document_hint: str, title: str, text: str) -> str:
    haystack = f"{source_zip}\n{path}\n{document_hint}\n{title}\n{text[:2500]}".upper()
    if source_zip == "MEL_SUPPLEMENT":
        return "MEL_CDL_NEF"
    if source_zip == "PDF_SUPPLEMENT":
        if "RESET" in haystack:
            return "RESET_GUIDE"
        return "PDF_SUPPLEMENT"
    if source_zip == "WEB_SUPPLEMENT":
        hint = (document_hint or "").upper()
        if hint:
            return hint
        if "/MEL/" in path.upper():
            return "MEL_QUICK_REF"
        if "ACRONYM" in haystack or "ABBREVIATION" in haystack:
            return "ABBREVIATIONS"
        return "WEB_SUPPLEMENT"
    if "AIPC" in haystack or " IPC" in haystack or "ILLUSTRATED PARTS" in haystack:
        return "AIPC"
    if re.search(r"\bTSM\b|TROUBLESHOOT", haystack):
        return "TSM"
    if re.search(r"\bAMM\b|MAINTENANCE MANUAL", haystack):
        return "AMM"
    if re.search(r"\bASM\b|SCHEMATIC", haystack):
        return "ASM"
    if re.search(r"\bAWM\b|\bWDM\b|WIRING", haystack):
        return "WDM"
    if "CIRCUIT BREAKER" in haystack or re.search(r"\bC/B\b", haystack):
        return "CB"
    return (document_hint or "").upper()


def route_score(row: dict, query: str, atas: list[str], manual_types: list[str], route: str) -> int:
    text = (row.get("text") or "").upper()
    path = (row.get("path") or "").upper()
    title = (row.get("title_hint") or "").upper()
    haystack = f"{path}\n{title}\n{text[:3000]}"
    score = 0
    ata_hint = row.get("ata_hint") or ""
    manual_type = row.get("manual_type_hint") or ""
    if atas and ata_hint in atas:
        score += 60
    elif atas:
        score -= 30
    if manual_types and manual_type in manual_types:
        score += 70
    elif manual_types:
        score -= 20
    route = (route or "").upper()
    if route == "PART_NUMBER":
        if manual_type == "AIPC":
            score += 80
        if re.search(r"\b\d{3}-\d{4}-\d{3}\b", text):
            score += 40
        if "FIN" in haystack or re.search(r"\b\d{3,4}-?[A-Z]{1,3}-?\d\b", haystack):
            score += 15
    elif route == "RESET":
        if manual_type == "RESET_GUIDE":
            score += 90
        if "RESET" in haystack:
            score += 35
        if "C/B" in haystack or "CIRCUIT BREAKER" in haystack:
            score += 15
    elif route == "CIRCUIT_BREAKER":
        if "C/B" in haystack or "CIRCUIT BREAKER" in haystack or re.search(r"\b[0-9]{2,3}VU\b", haystack):
            score += 60
    elif route == "FAULT":
        if manual_type in {"TSM", "RESET_GUIDE", "AMM"}:
            score += 45
        if "FAULT" in haystack or "ECAM" in haystack:
            score += 30
    elif route == "ABBREVIATION":
        if manual_type == "ABBREVIATIONS":
            score += 90
    elif route == "MEL":
        if manual_type == "MEL_CDL_NEF":
            score += 120
        elif manual_type == "MEL_QUICK_REF":
            score += 70
        if re.search(r"\b(MEL|CDL|NEF|DEFERRAL|DEFERRED|INOP|INOPERATIVE)\b", haystack):
            score += 30
    for token in re.findall(r"[A-Z0-9/-]{3,}", query.upper()):
        if token in haystack:
            score += 2
    return score


def enrich_row(row: sqlite3.Row | dict) -> dict:
    value = dict(row)
    value["ata_hint"] = infer_ata(value.get("path", ""), value.get("title_hint", ""), value.get("text", ""))
    value["manual_type_hint"] = infer_manual_type(
        value.get("source_zip", ""),
        value.get("path", ""),
        value.get("document_hint", ""),
        value.get("title_hint", ""),
        value.get("text", ""),
    )
    return value


def search_index(
    db_path: Path,
    query: str,
    limit: int,
    aircraft: str,
    customization: str,
    ata: str = "",
    manual_type: str = "",
    route: str = "",
) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    filters: list[str] = []
    params: list[str] = []

    normalized_aircraft = normalize_aircraft(aircraft)
    if normalized_aircraft:
        # Tail/MSN filtering is best-effort until a formal tail-to-effectivity map exists.
        variants = {aircraft.upper(), normalized_aircraft, normalized_aircraft.removesuffix("NV")}
        aircraft_clause = []
        for variant in sorted(v for v in variants if v):
            aircraft_clause.append("(aircraft_hint = ? OR text LIKE ? OR path LIKE ?)")
            params.extend([variant, f"%{variant}%", f"%{variant}%"])
        filters.append("(" + " OR ".join(aircraft_clause) + ")")

    customization = (customization or "").strip().upper()
    if customization:
        filters.append("(customization_hint = ? OR text LIKE ? OR path LIKE ?)")
        params.extend([customization, f"%{customization}%", f"%{customization}%"])

    filter_sql = (" AND " + " AND ".join(filters)) if filters else ""
    sql = f"""
        SELECT c.*
        FROM chunks_fts f
        JOIN chunks c ON c.id = f.rowid
        WHERE chunks_fts MATCH ? {filter_sql}
        LIMIT ?
    """
    try:
        fetch_limit = max(limit, 75 if (ata or manual_type or route) else limit)
        rows = conn.execute(sql, [fts_query(query), *params, fetch_limit]).fetchall()
    except sqlite3.OperationalError:
        terms = [term for term in re.split(r"\s+", query.strip()) if term]
        like_sql = " AND ".join("text LIKE ?" for _ in terms)
        rows = conn.execute(
            f"SELECT * FROM chunks WHERE {like_sql} LIMIT ?",
            [*(f"%{term}%" for term in terms), max(limit, 75 if (ata or manual_type or route) else limit)],
        ).fetchall()
    finally:
        conn.close()

    atas = split_csv(ata)
    manual_types = split_csv(manual_type)
    enriched = [enrich_row(row) for row in rows]
    for row in enriched:
        row["route_score"] = route_score(row, query, atas, manual_types, route)
    if atas or manual_types or route:
        enriched.sort(key=lambda item: item.get("route_score", 0), reverse=True)
        if atas or manual_types:
            preferred = [
                row
                for row in enriched
                if (not atas or row.get("ata_hint") in atas)
                and (not manual_types or row.get("manual_type_hint") in manual_types)
            ]
            if preferred:
                enriched = preferred + [row for row in enriched if row not in preferred]
    return enriched[:limit]


def configured_zips(manifest_path: Path) -> list[Path]:
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            zips = [Path(path) for path in manifest.get("zips", [])]
            return [path for path in zips if path.exists()]
        except Exception:
            pass
    fallback = Path("/mnt/airnavx_data")
    return [path for path in (fallback / "ata.zip", fallback / "XML.zip") if path.exists()]


def zip_by_name(manifest_path: Path, source_zip: str) -> Path | None:
    source_zip = (source_zip or "").strip()
    for zip_path in configured_zips(manifest_path):
        if zip_path.name == source_zip or str(zip_path) == source_zip:
            return zip_path
    return None


def read_zip_member(manifest_path: Path, source_zip: str, member_path: str) -> bytes | None:
    zip_path = zip_by_name(manifest_path, source_zip)
    if not zip_path:
        return None
    member_path = (member_path or "").lstrip("/")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if member_path not in zf.namelist():
                return None
            return zf.read(member_path)
    except Exception:
        return None


MEDIA_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".cgm",
    ".pdf",
}


DISPLAYABLE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


def xml_media_refs(xml_text: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = (value or "").strip().strip("'\"")
        value = value.replace("\\", "/")
        if not value or value.startswith(("http://", "https://", "urn:")):
            return
        ext = Path(value).suffix.lower()
        if ext in MEDIA_EXTENSIONS and value not in seen:
            seen.add(value)
            refs.append(value)

    for match in re.finditer(
        r"""(?:href|src|file|filename|entityname|infoEntityIdent|boardno|gnbr|graphic|imagemap)\s*=\s*["']([^"']+)["']""",
        xml_text,
        re.IGNORECASE,
    ):
        add(match.group(1))

    for match in re.finditer(r"""["']([^"']+\.(?:png|jpe?g|gif|svg|bmp|tiff?|webp|cgm|pdf))["']""", xml_text, re.IGNORECASE):
        add(match.group(1))

    return refs


def media_candidates(manifest_path: Path, source_zip: str, document_path: str, query: str, max_items: int = 12) -> list[dict]:
    zip_path = zip_by_name(manifest_path, source_zip)
    if not zip_path:
        return []

    document_path = (document_path or "").lstrip("/")
    query_tokens = {
        token.upper()
        for token in re.findall(r"[A-Z0-9]{3,}", query or "", flags=re.IGNORECASE)
        if token.upper() not in {"THE", "FOR", "PART", "NUMBER", "AIRCRAFT", "ACFT", "ASSY"}
    }
    doc_stem = Path(document_path).stem.upper()
    doc_base = re.sub(r"[^A-Z0-9]", "", doc_stem)
    doc_prefixes = {doc_base}
    if len(doc_base) > 2:
        doc_prefixes.add(doc_base.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    doc_prefixes = {value for value in doc_prefixes if len(value) >= 5}

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            refs: list[str] = []
            if document_path in names:
                try:
                    raw_xml = zf.read(document_path).decode("utf-8", "replace")
                    refs = xml_media_refs(raw_xml)
                except Exception:
                    refs = []

            by_name = {name.replace("\\", "/"): name for name in names}
            candidates: list[str] = []
            seen: set[str] = set()

            def add_candidate(name: str) -> None:
                normalized = name.replace("\\", "/").lstrip("/")
                if normalized in by_name:
                    normalized = by_name[normalized]
                else:
                    suffix_matches = [path for path in names if path.replace("\\", "/").endswith("/" + normalized)]
                    if suffix_matches:
                        normalized = suffix_matches[0]
                    else:
                        return
                if Path(normalized).suffix.lower() not in MEDIA_EXTENSIONS:
                    return
                if normalized not in seen:
                    seen.add(normalized)
                    candidates.append(normalized)

            for ref in refs:
                add_candidate(ref)
                if "/" not in ref and document_path:
                    add_candidate(str(Path(document_path).parent / ref).replace("\\", "/"))

            if len(candidates) < max_items:
                for name in names:
                    ext = Path(name).suffix.lower()
                    if ext not in MEDIA_EXTENSIONS:
                        continue
                    upper_name = re.sub(r"[^A-Z0-9]", "", name.upper())
                    score = 0
                    if any(prefix and prefix in upper_name for prefix in doc_prefixes):
                        score += 5
                    if query_tokens and any(token in upper_name for token in query_tokens):
                        score += 2
                    if score:
                        add_candidate(name)
                    if len(candidates) >= max_items:
                        break

            results = []
            for name in candidates[:max_items]:
                ext = Path(name).suffix.lower()
                content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                results.append(
                    {
                        "source_zip": zip_path.name,
                        "path": name,
                        "extension": ext,
                        "content_type": content_type,
                        "displayable": ext in DISPLAYABLE_EXTENSIONS,
                    }
                )
            return results
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    db_path: Path
    manifest_path: Path

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/health":
            manifest = {}
            if self.manifest_path.exists():
                manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self.send_json(200, {"ok": True, "db": str(self.db_path), "manifest": manifest})
            return

        if parsed.path == "/media/lookup":
            source_zip = (params.get("source_zip") or [""])[0]
            document_path = (params.get("path") or [""])[0]
            query = (params.get("q") or [""])[0]
            limit = min(max(int((params.get("limit") or ["8"])[0]), 1), 25)
            results = media_candidates(self.manifest_path, source_zip, document_path, query, limit)
            base = f"http://{self.headers.get('Host', f'{self.server.server_address[0]}:{self.server.server_address[1]}')}"
            for item in results:
                from urllib.parse import urlencode

                item["url"] = base + "/media/file?" + urlencode({"source_zip": item["source_zip"], "path": item["path"]})
            self.send_json(200, {"ok": True, "result_count": len(results), "results": results})
            return

        if parsed.path == "/media/file":
            source_zip = (params.get("source_zip") or [""])[0]
            member_path = (params.get("path") or [""])[0]
            body = read_zip_member(self.manifest_path, source_zip, member_path)
            if body is None:
                self.send_json(404, {"ok": False, "error": "Media file not found"})
                return
            content_type = mimetypes.guess_type(member_path)[0] or "application/octet-stream"
            self.send_bytes(200, body, content_type)
            return

        if parsed.path != "/search":
            self.send_json(404, {"ok": False, "error": "Use /health, /search?q=..., /media/lookup, or /media/file"})
            return

        query = (params.get("q") or [""])[0].strip()
        if not query:
            self.send_json(400, {"ok": False, "error": "Missing q parameter"})
            return
        limit = min(max(int((params.get("limit") or ["8"])[0]), 1), 25)
        aircraft = (params.get("aircraft") or [""])[0]
        customization = (params.get("customization") or ["AAY_CUSDOC"])[0]
        ata = (params.get("ata") or [""])[0]
        manual_type = (params.get("manual_type") or [""])[0]
        route = (params.get("route") or [""])[0]
        rows = search_index(self.db_path, query, limit, aircraft, customization, ata, manual_type, route)
        self.send_json(
            200,
            {
                "ok": True,
                "query": query,
                "aircraft": normalize_aircraft(aircraft),
                "customization": customization,
                "ata": split_csv(ata),
                "manual_type": split_csv(manual_type),
                "route": route,
                "result_count": len(rows),
                "results": rows,
                "note": "Aircraft/tail filtering is best-effort until a tail-to-effectivity map is added.",
            },
        )

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--index", default="/opt/airnavx_index/airnavx_index.sqlite")
    args = parser.parse_args()

    Handler.db_path = Path(args.index)
    Handler.manifest_path = Handler.db_path.with_name("manifest.json")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
