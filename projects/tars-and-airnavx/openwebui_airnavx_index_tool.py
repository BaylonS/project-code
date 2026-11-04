"""
title: AirnavX Direct Manual Index
author: Baylon
version: 0.1.0
description: Search Baylon's extracted AirnavX manual index without using the AirnavX web UI.
"""

from pydantic import BaseModel, Field
import re
import sqlite3


class Tools:
    class Valves(BaseModel):
        index_path: str = Field(
            default="/opt/airnavx_index/airnavx_index.sqlite",
            description="Path to the SQLite index built by airnavx_indexer.py.",
        )
        default_customization: str = Field(
            default="AAY_CUSDOC",
            description="Default AirnavX customization/customer document set.",
        )
        max_results: int = Field(default=8, description="Maximum chunks to return.")
        max_chars_per_result: int = Field(default=1600, description="Maximum text per result.")

    def __init__(self):
        self.valves = self.Valves()

    def _normalize_aircraft(self, aircraft: str) -> str:
        value = (aircraft or "").strip().upper()
        if value.startswith("N") and len(value) > 1:
            value = value[1:]
        if value == "305":
            return "305NV"
        return value

    def _fts_query(self, query: str) -> str:
        terms = [term for term in re.split(r"\s+", query.strip()) if term]
        safe_terms = []
        for term in terms:
            term = re.sub(r'["]', "", term)
            if term:
                safe_terms.append(f'"{term}"')
        return " ".join(safe_terms)

    async def airnavx_index_search(
        self,
        query: str,
        aircraft: str = "",
        customization: str = "",
        document_type: str = "",
    ) -> str:
        """
        Search the extracted AirnavX manual index directly. Use this before answering
        aircraft manual questions. Do not tell Baylon how to search; search the index
        yourself and answer only from returned manual text.

        Baylon normally uses customization AAY_CUSDOC. Treat aircraft values like 305,
        305NV, or N305NV as MSN/TN/FSN aircraft context, usually 305NV.

        Good search flow: search a short system term first, like FMGC. Then narrow with
        circuit breaker, CB, ATA, FIN, document type, or exact phrase.

        :param query: Search text, for example FMGC, FMGC circuit breaker, or an ATA/task phrase.
        :param aircraft: Optional aircraft/MSN/TN/FSN context, for example 305, 305NV, or N305NV.
        :param customization: Optional customization. Defaults to AAY_CUSDOC.
        :param document_type: Optional manual type filter, such as AMM, ASM, TSM, or IPC.
        """
        query = (query or "").strip()
        if not query:
            return "Give me search text first."

        aircraft = self._normalize_aircraft(aircraft)
        customization = (customization or self.valves.default_customization or "").strip().upper()
        document_type = (document_type or "").strip().upper()

        try:
            conn = sqlite3.connect(self.valves.index_path)
            conn.row_factory = sqlite3.Row
        except Exception as exc:
            return (
                "AIRNAVX_INDEX_UNAVAILABLE: Could not open the extracted manual index at "
                f"{self.valves.index_path!r}: {exc}"
            )

        filters = []
        params = [self._fts_query(query)]
        if customization:
            filters.append("(c.customization_hint = ? OR c.text LIKE ? OR c.path LIKE ?)")
            params.extend([customization, f"%{customization}%", f"%{customization}%"])
        if aircraft:
            filters.append("(c.aircraft_hint = ? OR c.text LIKE ? OR c.path LIKE ?)")
            params.extend([aircraft, f"%{aircraft}%", f"%{aircraft}%"])
        if document_type:
            filters.append("(c.document_hint = ? OR c.path LIKE ? OR c.title_hint LIKE ?)")
            params.extend([document_type, f"%{document_type}%", f"%{document_type}%"])

        filter_sql = (" AND " + " AND ".join(filters)) if filters else ""
        limit = max(1, min(int(self.valves.max_results), 20))

        try:
            rows = conn.execute(
                f"""
                SELECT c.*
                FROM chunks_fts f
                JOIN chunks c ON c.id = f.rowid
                WHERE chunks_fts MATCH ? {filter_sql}
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        except Exception as exc:
            conn.close()
            return f"AIRNAVX_INDEX_SEARCH_ERROR: {exc}"
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if not rows:
            return (
                "NO_AIRNAVX_INDEX_RESULTS. I searched the extracted AirnavX manual index "
                f"for query={query!r}, customization={customization!r}, aircraft={aircraft!r}, "
                f"document_type={document_type!r}, but no readable manual chunks matched. "
                "Do not answer from general knowledge."
            )

        parts = [
            "AIRNAVX_INDEX_RESULTS",
            f"query: {query}",
            f"customization: {customization or '[not filtered]'}",
            f"aircraft: {aircraft or '[not filtered]'}",
            f"document_type: {document_type or '[not filtered]'}",
            "",
        ]
        for index, row in enumerate(rows, start=1):
            text = row["text"] or ""
            if len(text) > self.valves.max_chars_per_result:
                text = text[: self.valves.max_chars_per_result] + "\n[truncated]"
            parts.append(f"Result {index}")
            parts.append(f"source_zip: {row['source_zip']}")
            parts.append(f"path: {row['path']}")
            parts.append(f"document_hint: {row['document_hint']}")
            parts.append(f"title_hint: {row['title_hint']}")
            parts.append(f"customization_hint: {row['customization_hint']}")
            parts.append(f"aircraft_hint: {row['aircraft_hint']}")
            parts.append("text:")
            parts.append(text)
            parts.append("")

        return "\n".join(parts).strip()
