"""
title: AirnavX CT Manual Search
author: Baylon
version: 0.4.16
description: Search the local AirnavX manual index hosted by CT 108.
"""

from pydantic import BaseModel, Field
from urllib.parse import urlencode
from urllib.request import urlopen
import csv
import json
import re


class Tools:
    class Valves(BaseModel):
        search_base_url: str = Field(
            default="http://127.0.0.1:8088",
            description="Local-only AirnavX search service URL on CT 108.",
        )
        default_customization: str = Field(
            default="AAY_CUSDOC",
            description="Default AirnavX customization/customer document set.",
        )
        max_results: int = Field(default=6, description="Maximum chunks to return.")
        max_chars_per_result: int = Field(default=900, description="Maximum text per result.")
        max_search_attempts: int = Field(default=5, description="Maximum query rewrites to try.")

    def __init__(self):
        self.valves = self.Valves()
        self.last_work_context: dict[str, str] = {}

    async def airnavx_manual_search(
        self,
        query: str,
        aircraft: str = "",
        customization: str = "",
        document_type: str = "",
    ) -> str:
        """
        Search Baylon's local AirnavX manual index. Use this before answering
        aircraft manual questions. Do not tell Baylon how to search AirnavX; call
        this tool yourself and answer only from returned manual text.

        Baylon normally uses customization AAY_CUSDOC. Treat aircraft values like
        305, 305NV, and N305NV as 305NV aircraft context. Aircraft filtering is
        best-effort until a tail-to-effectivity map is added, so include the
        source/effectivity shown in results when the exact tail is not proven.

        This tool rewrites natural maintenance questions into better CT searches.
        It can extract aircraft/tail text from the question, remove filler words,
        retry canonical searches for known patterns, and return direct answer
        blocks for common results such as FMGC circuit breakers and part numbers.
        It also remembers the last aircraft/manual/system context inside this
        tool instance so short follow-ups like "break that down", "more info",
        "same aircraft", "what about the CB", or "what is the MEL" stay attached
        to the last maintenance subject instead of searching unrelated manuals.
        For part-number questions, answer only from verified IPC/AIPC part data;
        do not use AMM procedure text as a substitute for a part number.
        For MEL/CDL/NEF questions, use the MEL_BREAKDOWN section when present
        to explain item/condition, repair category, placard/procedure notes,
        operational limits, and source reference.
        Do not explain how to search; call this tool and answer from the returned
        FINAL_ANSWER_COPY_THIS_STYLE block when present.

        :param query: Search text, for example FMGC circuit breaker or 1-CA-1 FMGC.
        :param aircraft: Optional aircraft/MSN/TN/FSN context, for example 305 or 305NV.
        :param customization: Optional customization. Defaults to AAY_CUSDOC.
        :param document_type: Optional manual type such as AMM, ASM, TSM, or IPC.
        """
        query = (query or "").strip()
        if not query:
            return "Give me search text first."

        original_user_query = query
        query, followup_note = _resolve_followup_query(self, query, aircraft)
        document_type = _effective_document_type(self, query, document_type)
        aircraft = _normalize_aircraft(
            self,
            aircraft
            or _extract_aircraft_from_query(self, query)
            or (self.last_work_context.get("aircraft", "") if followup_note else ""),
        )
        customization = (customization or self.valves.default_customization or "").strip().upper()
        route_meta = _route_query(self, query, document_type)
        if route_meta.get("route") == "CIRCUIT_BREAKER":
            document_type = ""
        if route_meta.get("route") == "MEL":
            route_meta["ata"] = ""
        limit = max(1, min(int(self.valves.max_results), 20))
        search_limit = max(limit, 12) if route_meta.get("route") == "PART_NUMBER" else limit
        search_queries, search_notes = _canonical_queries(self, query, aircraft, document_type)
        payload = {}
        rows = []
        searched_query = ""
        searched_queries: list[str] = []
        search_error = ""
        search_aircraft = "" if route_meta.get("route") == "PART_NUMBER" else aircraft
        if followup_note:
            search_notes.append(followup_note)
        for search_query in search_queries:
            try:
                payload, searched_query, _url = _search(self, search_query, search_aircraft, customization, search_limit, route_meta)
                searched_queries.append(searched_query)
                rows = payload.get("results") or []
                if rows:
                    break
            except Exception as exc:
                search_error = str(exc)

        if not rows:
            if search_error:
                return f"AIRNAVX_CT_SEARCH_UNAVAILABLE: Could not reach CT search service: {search_error}"
            return (
                "NO_AIRNAVX_CT_RESULTS. I searched Baylon's local AirnavX index "
                f"for original_query={original_user_query!r}, resolved_query={query!r}, "
                f"queries={searched_queries or search_queries!r}, customization={customization!r}, aircraft={aircraft!r}. "
                "Do not answer from general knowledge."
            )

        _remember_work_context(self, original_user_query, query, aircraft, route_meta, searched_query, rows)
        candidates = _direct_answer_candidates(self, rows, query)
        centerbody_candidates = _centerbody_part_candidates(self, rows)
        pdf_rows = _pdf_supplement_rows(self, rows)
        mel_rows = _mel_supplement_rows(self, rows)
        web_rows = _web_supplement_rows(self, rows)
        abbreviation_lines = _abbreviation_lines(self, query)

        if mel_rows and route_meta.get("route") == "MEL":
            return _format_mel_supplement_answer(
                self,
                mel_rows,
                query,
                aircraft,
                searched_query,
                searched_queries,
                customization,
                search_notes,
                payload,
            )

        if web_rows and abbreviation_lines and not re.search(
            r"\b(reset|resets|fault|ecam|warning|caution|quick\s*ref|reference|message)\b",
            query,
            re.IGNORECASE,
        ):
            return _format_abbreviation_answer(self, web_rows, query, aircraft)

        if pdf_rows and re.search(
            r"\b(reset|resets|fault|ecam|warning|caution|quick\s*ref|reference|message)\b",
            query,
            re.IGNORECASE,
        ):
            return _prepend_abbreviation_lines(
                self,
                _format_pdf_supplement_answer(
                    self,
                    pdf_rows,
                    query,
                    aircraft,
                    searched_query,
                    searched_queries,
                    customization,
                    search_notes,
                    payload,
                ),
                query,
            )

        part_number_question = _is_part_number_question(self, query)

        if centerbody_candidates:
            answer_parts = _format_centerbody_answer(self, centerbody_candidates, aircraft, query)
            answer_parts.extend(
                [
                    "",
                    "SEARCH_DETAILS",
                f"original_query: {query}",
                f"searched_query: {searched_query}",
                f"all_queries_tried: {searched_queries}",
                f"route: {route_meta}",
                f"customization: {customization or '[not filtered]'}",
                    "; ".join(search_notes),
                    payload.get("note", ""),
                ]
            )
            return "\n".join(part for part in answer_parts if part is not None).strip()

        if part_number_question:
            part_candidates = _part_number_candidates(self, rows, query, aircraft)
            if part_candidates:
                answer_parts = _format_part_answer(self, part_candidates, query, aircraft)
                answer_parts.extend(
                    [
                        "",
                        "SEARCH_DETAILS",
                        f"original_query: {query}",
                        f"searched_query: {searched_query}",
                        f"all_queries_tried: {searched_queries}",
                        f"route: {route_meta}",
                        f"customization: {customization or '[not filtered]'}",
                        "; ".join(search_notes),
                        payload.get("note", ""),
                    ]
                )
                return "\n".join(part for part in answer_parts if part is not None).strip()

            return "\n".join(
                part
                for part in [
                    "NO_VERIFIED_IPC_PART_NUMBER",
                    "I searched the IPC/AIPC route for a part-number answer but did not find a verified P/N in the indexed IPC data.",
                    "Do not answer this part-number question from AMM procedure text.",
                    f"original_query: {query}",
                    f"searched_query: {searched_query}",
                    f"all_queries_tried: {searched_queries}",
                    f"route: {route_meta}",
                    f"aircraft: {aircraft or '[not filtered]'}",
                    f"customization: {customization or '[not filtered]'}",
                    "; ".join(search_notes),
                    payload.get("note", ""),
                ]
                if part
            ).strip()

        parts = [
            "AIRNAVX_CT_RESULTS",
            f"original_query: {query}",
            f"searched_query: {searched_query}",
            f"all_queries_tried: {searched_queries}",
            f"route: {route_meta}",
            f"aircraft: {aircraft or '[not filtered]'}",
            f"customization: {customization or '[not filtered]'}",
            "; ".join(search_notes),
            payload.get("note", ""),
            "",
        ]
        if candidates:
            answer_parts = _format_breaker_answer(self, candidates, aircraft)
            source_keys: set[str] = set()
            for row in rows:
                row_text = re.sub(r"\s+", " ", row.get("text") or "").upper()
                if any(candidate.get("raw", "") in row_text for candidate in candidates):
                    effectivity = row.get("aircraft_hint", "")
                    source_key = (
                        f"{row.get('source_zip', '')} :: {row.get('path', '')} "
                        f":: chunk {row.get('chunk_index', '')}"
                    )
                    if effectivity:
                        source_key += f" :: effectivity_hint {effectivity}"
                    if source_key not in source_keys:
                        source_keys.add(source_key)
                        answer_parts.append(f"Reference: {source_key}")
            if aircraft:
                answer_parts.append(f"Aircraft context: {aircraft}")
            answer_parts.extend(
                [
                    "",
                    "SEARCH_DETAILS",
                    f"original_query: {query}",
                    f"searched_query: {searched_query}",
                    f"all_queries_tried: {searched_queries}",
                    f"route: {route_meta}",
                    f"customization: {customization or '[not filtered]'}",
                    "; ".join(search_notes),
                    payload.get("note", ""),
                ]
            )
            return "\n".join(part for part in answer_parts if part is not None).strip()

        for index, row in enumerate(rows, start=1):
            text = row.get("text") or ""
            if len(text) > self.valves.max_chars_per_result:
                text = text[: self.valves.max_chars_per_result] + "\n[truncated]"
            parts.append(f"Result {index}")
            parts.append(f"source_zip: {row.get('source_zip', '')}")
            parts.append(f"path: {row.get('path', '')}")
            parts.append(f"title_hint: {row.get('title_hint', '')}")
            parts.append(f"customization_hint: {row.get('customization_hint', '')}")
            parts.append(f"aircraft_hint/effectivity_hint: {row.get('aircraft_hint', '')}")
            parts.append("text:")
            parts.append(text)
            parts.append("")

        return "\n".join(part for part in parts if part is not None).strip()

def _normalize_aircraft(self, aircraft: str) -> str:
    value = (aircraft or "").strip().upper()
    value = re.sub(r"^N", "", value)
    value = re.sub(r"[^0-9A-Z]", "", value)
    if value.isdigit():
        return f"{value}NV"
    return value

def _extract_aircraft_from_query(self, query: str) -> str:
    query = query or ""
    patterns = [
        r"\b(?:AIRCRAFT|ACFT|A/C|TAIL|MSN)\s*(?:N)?(\d{3})(?:NV)?\b",
        r"\bN?(\d{3}NV)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return _normalize_aircraft(self, match.group(1))
    return ""

def _remove_aircraft_tokens(self, query: str, aircraft: str) -> str:
    cleaned = query or ""
    aircraft = _normalize_aircraft(self, aircraft)
    if aircraft:
        aircraft_stem = re.sub(r"NV$", "", aircraft)
        tokens = [aircraft, f"N{aircraft}", aircraft_stem]
        for token in tokens:
            if token:
                cleaned = re.sub(rf"\b{re.escape(token)}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:AIRCRAFT|ACFT|A/C|TAIL|MSN)\b", " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" ?.,")

def _has_new_work_subject(self, query: str) -> bool:
    text = (query or "").upper()
    return bool(
        _extract_aircraft_from_query(self, query)
        or re.search(
            r"\b(FMGC|CIDS|BMC|ACP|ACARS|VHF|BSCU|PTU|APU|TGB|TRANSFER\s+GEAR\s*BOX|TRANSFER\s+GEARBOX|CENTERBODY|CENTREBODY|CENTRBDY|CENTERBD|CAB\s+PR|CABIN\s+PRESS|OVERHEAD\s+BIN|STOWAGE|BRAKE|HYD|PACK)\b",
            text,
        )
        or re.search(r"\bATA\s*[0-9]{2}\b", text)
    )

def _is_followup_query(self, query: str) -> bool:
    text = re.sub(r"\s+", " ", (query or "").strip().upper())
    if not text or not self.last_work_context:
        return False
    if _looks_like_abbreviation_question(self, query):
        return False
    if _has_new_work_subject(self, query):
        return False
    followup_patterns = [
        r"^BREAK (?:IT |THAT |THIS )?DOWN$",
        r"^MORE(?: INFO| DETAIL| DETAILS)?$",
        r"^(?:WHAT|HOW) ABOUT (?:THE )?(?:CB|C/B|MEL|CDL|NEF|PART|RESET|SOURCE|REFERENCE|LIMIT|TIME|LOCATION|PROCEDURE)S?$",
        r"^(?:CB|C/B|MEL|CDL|NEF|PART NUMBER|P/N|RESET|SOURCE|REFERENCE|LIMIT|TIME|LOCATION|PROCEDURE)S?$",
        r"^(?:SAME|SAME AIRCRAFT|SAME ONE)$",
        r"^(?:ANYTHING ELSE|WHAT ELSE)$",
    ]
    if any(re.search(pattern, text) for pattern in followup_patterns):
        return True
    return bool(
        len(text.split()) <= 6
        and re.search(r"\b(IT|THAT|THIS|SAME|CB|C/B|MEL|CDL|NEF|PART|RESET|SOURCE|REFERENCE|LIMIT|TIME|LOCATION|PROCEDURE|PLACARD)\b", text)
    )

def _resolve_followup_query(self, query: str, aircraft: str = "") -> tuple[str, str]:
    if not _is_followup_query(self, query):
        return query, ""

    context_query = self.last_work_context.get("resolved_query") or self.last_work_context.get("query") or ""
    context_subject = self.last_work_context.get("subject") or ""
    context_aircraft = aircraft or self.last_work_context.get("aircraft", "")
    context_route = self.last_work_context.get("route", "")
    if not context_query and not context_subject:
        return query, ""

    request = query
    if re.search(r"\bCB|C/B|CIRCUIT\s+BREAKER", query, re.IGNORECASE):
        request = "circuit breakers"
    elif re.search(r"\bMEL|CDL|NEF|DEFER", query, re.IGNORECASE):
        request = "MEL deferral"
    elif re.search(r"\bPART|P/N|PN", query, re.IGNORECASE):
        request = "part number"
    elif re.search(r"\bRESET|PROCEDURE", query, re.IGNORECASE):
        request = "reset procedure"
    elif re.search(r"\bBREAK|MORE|DETAIL", query, re.IGNORECASE):
        request = ""
    elif re.search(r"\bLIMIT|TIME|PLACARD", query, re.IGNORECASE):
        request = query

    if not request and context_route == "MEL":
        resolved = context_query or context_subject
    elif request == "circuit breakers":
        base = context_subject or context_query
        base = re.sub(
            r"\b(MEL|CDL|NEF|DEFERRAL|DEFERRED|DEFER|INOP|INOPERATIVE|PART\s*NUMBER|PART|PN|P/N|IPC|AIPC)\b",
            " ",
            base,
            flags=re.IGNORECASE,
        )
        base = re.sub(r"\s+", " ", base).strip(" ?.,")
        resolved = f"{base} circuit breaker".strip()
    else:
        base = context_query or context_subject
        resolved = f"{base} {request}".strip()
    if context_aircraft and not _extract_aircraft_from_query(self, resolved):
        resolved = f"{resolved} aircraft {context_aircraft}"
    return resolved, f"Resolved short follow-up {query!r} using last work context: {(context_query or context_subject)!r}."

def _subject_from_query(self, query: str) -> str:
    text = re.sub(r"\s+", " ", (query or "").strip(" ?.,"))
    text = _remove_aircraft_tokens(self, text, _extract_aircraft_from_query(self, text))
    text = re.sub(
        r"\b(what|which|where|is|are|the|for|on|of|to|a|an|please|lookup|look|up|find|give|me|should|i|apply|tell|about)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip(" ?.,")
    return text[:120]

def _remember_work_context(
    self,
    original_query: str,
    resolved_query: str,
    aircraft: str,
    route_meta: dict[str, str],
    searched_query: str,
    rows: list[dict],
) -> None:
    route = route_meta.get("route", "")
    if route == "GENERAL" and not re.search(
        r"\b(AIRCRAFT|ACFT|A/C|TAIL|MSN|AIRBUS|A320|ATA|AMM|TSM|IPC|AIPC|MEL|CDL|NEF|ECAM|C/B|CB|CIRCUIT|BREAKER|RESET|FAULT|PART|P/N|FIN|DEFER|INOP)\b",
        resolved_query or "",
        re.IGNORECASE,
    ):
        return
    self.last_work_context = {
        "query": original_query,
        "resolved_query": resolved_query,
        "searched_query": searched_query,
        "subject": _subject_from_query(self, resolved_query) or searched_query,
        "aircraft": aircraft,
        "route": route,
        "ata": route_meta.get("ata", ""),
        "manual_type": route_meta.get("manual_type", ""),
        "last_reference": _reference_key(self, rows[0]) if rows else "",
    }

def _looks_like_abbreviation_question(self, query: str) -> bool:
    text = (query or "").upper()
    if re.search(r"\b(MEAN|MEANING|MEANS|STAND(?:S)?\s+FOR|ABBREV|ABBREVIATION)\b", text):
        return True
    return bool(re.search(r"\bWHAT\s+(?:IS|DOES)\s+(?:THE\s+)?[A-Z0-9/ -]{2,12}\s*(?:MEAN|STAND\s+FOR)?\??$", text))

def _route_query(self, query: str, document_type: str = "") -> dict[str, str]:
    text = (query or "").upper()
    manual_types: list[str] = []
    atas: list[str] = []
    route = "GENERAL"

    def add_ata(value: str) -> None:
        if value and value not in atas:
            atas.append(value)

    def add_manual(value: str) -> None:
        value = value.upper()
        if value and value not in manual_types:
            manual_types.append(value)

    explicit_atas = re.findall(r"\bATA\s*([0-9]{2})\b", text)
    for ata in explicit_atas:
        add_ata(ata)

    if _is_part_number_question(self, query):
        route = "PART_NUMBER"
        add_manual("AIPC")
    if re.search(r"\b(RESET|RESETS|CLEAR|FAULT|ECAM|WARNING|CAUTION|MESSAGE)\b", text):
        route = "RESET" if "RESET" in text or "CLEAR" in text else "FAULT"
        add_manual("RESET_GUIDE")
        add_manual("TSM")
        add_manual("AMM")
    if re.search(r"\b(MEL|CDL|NEF|DEFERRAL|DEFERRED|DEFER|INOP|INOPERATIVE)\b", text):
        route = "MEL"
        add_manual("MEL_CDL_NEF")
        add_manual("MEL_QUICK_REF")
    if re.search(r"\b(CIRCUIT|CIRCIT|CIRCUT|BREAKER|BREAKERS|BRAKER|BRAKERS|CB|C/B)\b", text):
        route = "CIRCUIT_BREAKER" if route == "GENERAL" else route
        add_manual("CB")
    if _looks_like_abbreviation_question(self, query) and _abbreviation_terms(self, query):
        route = "ABBREVIATION"
        add_manual("ABBREVIATIONS")

    system_ata = {
        r"\bCAB\s+PR\b": "21",
        r"\bCABIN\s+PRESS": "21",
        r"\bPACK\b": "21",
        r"\bFMGC\b": "22",
        r"\bAUTO\s+FLT\b": "22",
        r"\bAUTOPILOT\b": "22",
        r"\bCIDS\b": "23",
        r"\bVHF\b": "23",
        r"\bACARS\b": "23",
        r"\bACP\b": "23",
        r"\bAUDIO\s+CONTROL\s+PANEL\b": "23",
        r"\bAUDIO\s+MANAGEMENT\b": "23",
        r"\bOVERHEAD\s+BIN\b": "25",
        r"\bSTOWAGE\b": "25",
        r"\bBAGGAGE\s+BIN\b": "25",
        r"\bELEC\b": "24",
        r"\bBATTERY\b": "24",
        r"\bAPU\b": "49",
        r"\bHYD(?:RAULIC)?\b": "29",
        r"\bPTU\b": "29",
        r"\bBRAKE\b|\bBRAKES\b|\bBSCU\b": "32",
        r"\bLANDING\s+GEAR\b": "32",
        r"\bLIGHT(?:S|ING)?\b": "33",
        r"\bBMC\b|\bBLEED\s+MONITORING\s+COMPUTER\b": "36",
        r"\bTGB\b|\bTRANSFER\s+GEAR\s*BOX\b|\bTRANSFER\s+GEARBOX\b": "72",
        r"\bCENTERBODY\b|\bCENTREBODY\b|\bCENTRBDY\b|\bCENTERBD\b": "78",
        r"\bEXHAUST\b": "78",
        r"\bTHRUST\s+REVERSER\b": "78",
        r"\bENGINE\b": "78" if re.search(r"\b(CENTERBODY|CENTREBODY|EXHAUST)\b", text) else "72",
    }
    for pattern, ata in system_ata.items():
        if re.search(pattern, text):
            add_ata(ata)

    doc_type = (document_type or "").strip().upper()
    if doc_type and not (route == "CIRCUIT_BREAKER" and doc_type != "CB"):
        add_manual(doc_type)

    return {
        "route": route,
        "ata": ",".join(atas),
        "manual_type": ",".join(manual_types),
    }

def _effective_document_type(self, query: str, document_type: str = "") -> str:
    doc_type = (document_type or "").strip().upper()
    is_part_number_question = _is_part_number_question(self, query)
    if is_part_number_question and doc_type not in {"", "IPC", "AIPC"}:
        return ""
    if doc_type == "IPC":
        return "AIPC"
    return doc_type

def _is_part_number_question(self, query: str) -> bool:
    text = query or ""
    if re.search(r"\b(PART|PN|P/N|PART\s*NUMBER|IPC|AIPC|FIN|FIGURE|ILLUSTRATION|ILLUS)\b", text, re.IGNORECASE):
        return True
    return bool(_part_number_tokens(self, text))

def _part_number_tokens(self, query: str) -> list[str]:
    tokens: list[str] = []
    patterns = [
        r"\b\d{3}-\d{4}-\d{3}\b",
        r"\b[A-Z0-9][A-Z0-9-]{4,}[A-Z0-9]\b",
    ]
    ignored = {
        "AIRCRAFT",
        "AUDIO",
        "CONTROL",
        "PANEL",
        "NUMBER",
        "CENTERBODY",
        "CAPTAIN",
        "FIRST",
        "OFFICER",
        "AAY_CUSDOC",
    }
    for pattern in patterns:
        for match in re.findall(pattern, query or "", re.IGNORECASE):
            token = match.upper()
            if not re.search(r"\d", token):
                continue
            if not re.fullmatch(r"\d{3}-\d{4}-\d{3}", token) and not re.search(r"[A-Z]", token):
                continue
            if re.fullmatch(r"N?\d{3}NV|\d{3}", token):
                continue
            if re.fullmatch(r"\d{2}-\d{2}", token):
                continue
            if _is_manual_reference_token(self, token):
                continue
            if token not in ignored and token not in tokens:
                tokens.append(token)
    return tokens

def _part_number_regex(self) -> str:
    return r"(?:\d{3}-\d{4}-\d{3}|(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{4,}[A-Z0-9])"

def _is_manual_reference_token(self, token: str) -> bool:
    token = (token or "").strip().upper()
    # AMM/TSM task references can look like part numbers; never return them as IPC P/Ns.
    return bool(re.fullmatch(r"\d{2}-\d{2}-\d{2}-\d{3}-\d{3}(?:-[A-Z])?", token))

def _part_lookup_terms(self, query: str) -> set[str]:
    ignore = {
        "WHAT",
        "WAS",
        "IS",
        "ARE",
        "THE",
        "FOR",
        "PART",
        "NUMBER",
        "AIRCRAFT",
        "ACFT",
        "LOOK",
        "FIND",
        "CAPT",
        "CAPTAIN",
        "FIRST",
        "OFFICER",
        "ENGINE",
    }
    terms = {
        word.upper()
        for word in re.findall(r"[A-Z0-9]{3,}", query or "", flags=re.IGNORECASE)
        if word.upper() not in ignore
    }
    if re.search(r"\bACP\b", query or "", re.IGNORECASE):
        terms.add("ACP")
    if re.search(r"\bTGB\b|\bTRANSFER\s+GEAR\s*BOX\b|\bTRANSFER\s+GEARBOX\b", query or "", re.IGNORECASE):
        terms.update({"TGB", "TRANSFER GEARBOX", "GEARBOX"})
    return terms

def _canonical_queries(self, query: str, aircraft: str, document_type: str) -> tuple[list[str], list[str]]:
    original = (query or "").strip()
    cleaned = _remove_aircraft_tokens(self, original, aircraft)
    notes: list[str] = []
    queries: list[str] = []
    abbreviation_terms = _abbreviation_terms(self, original)
    is_reset_question = bool(
        re.search(r"\b(reset|resets|fault|ecam|warning|caution|quick\s*ref|reference|message)\b", original, re.IGNORECASE)
    )
    is_abbreviation_question = _looks_like_abbreviation_question(self, original)
    is_mel_question = bool(re.search(r"\b(MEL|CDL|NEF|deferral|deferred|defer|inop|inoperative)\b", original, re.IGNORECASE))
    is_circuit_breaker_question = bool(
        re.search(
            r"\b(circuit|circit|circut|breaker|breakers|braker|brakers|cb|c/b)\b",
            original,
            re.IGNORECASE,
        )
        and not re.search(r"\b(mean|meaning|means|stand(?:s)?\s+for|abbrev|abbreviation)\b", original, re.IGNORECASE)
    )

    def add(value: str, note: str = "", include_document_type: bool = True) -> None:
        value = re.sub(r"\s+", " ", (value or "").strip(" ?.,"))
        if document_type and include_document_type:
            value = f"{value} {document_type.strip().upper()}"
        if value and value.lower() not in {item.lower() for item in queries}:
            queries.append(value)
            if note:
                notes.append(note)

    if re.search(r"\bFMGC\b", original, re.IGNORECASE) and re.search(
        r"\b(circuit|circit|circut|breaker|breakers|braker|brakers|cb|c/b)\b",
        original,
        re.IGNORECASE,
    ):
        add("FMGC circuit breaker", "Used canonical FMGC breaker search.")

    if abbreviation_terms and is_abbreviation_question and not is_reset_question and not is_circuit_breaker_question:
        add(
            "Airbus Abbreviations AviationHunt " + " ".join(abbreviation_terms),
            "Searched Airbus abbreviation supplement.",
        )

    if re.search(r"\bBMC\b|\bBLEED\s+MONITORING\s+COMPUTER\b", original, re.IGNORECASE) and is_circuit_breaker_question:
        add("1-HA-1 BMC-1 3-HA-1 2-HA-1", "Used exact BMC breaker table search.")
        add("BMC-1 BMC-2 AIR BLEED MONG CTL")
        add("Removal of the BMC 1-HA-1 1-HA-2 circuit breaker")
        add("BLEED MONITORING COMPUTER circuit breaker")
        add("BMC circuit breaker")

    if re.search(
        r"\b(centerbody|centrebody|center\s*body|centre\s*body)\b",
        original,
        re.IGNORECASE,
    ) and re.search(r"\b(engine|part|pn|p/n|number|assy|assembly)\b", original, re.IGNORECASE):
        add("centerbody assy hot exhaust", "Used canonical engine centerbody search.")
        add("3900KM1 CENTERBODY ASSY")

    if re.search(r"\bTGB\b|\bTRANSFER\s+GEAR\s*BOX\b|\bTRANSFER\s+GEARBOX\b", original, re.IGNORECASE) and _is_part_number_question(self, original):
        add("TGB TRANSFER GEARBOX", "Used canonical engine TGB/transfer gearbox IPC search.")
        add("TRANSFER GEARBOX")
        add("TRANSFER GEAR BOX")
        add("GEARBOX TGB")

    if is_mel_question:
        mel_query = cleaned or original
        mel_query = re.sub(
            r"\b(what|which|is|are|the|for|on|of|to|a|an|please|lookup|look|up|find|give|me|should|i|apply|mel|cdl|nef)\b",
            " ",
            mel_query,
            flags=re.IGNORECASE,
        )
        mel_query = re.sub(r"\s+", " ", mel_query).strip(" ?.,")
        if mel_query:
            add(f"{mel_query} MEL", "Searched MEL/CDL/NEF supplement.")
            add(mel_query, "Searched MEL keyword form.")

    if not is_mel_question and re.search(r"\bACP\b|\bAUDIO\s+CONTROL\s+PANEL\b", original, re.IGNORECASE):
        add("ACP2788AB05", "Searched known ACP P/N family.")
        add("ACP AUDIO CONTROL PANEL", "Used canonical ACP IPC search.")

    part_numbers = _part_number_tokens(self, original)
    for part_number in part_numbers:
        add(f'"{part_number}"', "Searched exact quoted part number for structured IPC list data.", False)
        add(part_number, "Searched exact part number from question.", False)

    fin_matches = re.findall(r"\b\d{3,4}-?[A-Z]{1,3}-?\d\b|\b\d{3,4}[A-Z]{1,3}\d\b", original, re.IGNORECASE)
    for fin in fin_matches:
        add(fin.upper().replace("-", ""), "Searched exact FIN from question.")

    if re.search(r"\bCIDS\b", original, re.IGNORECASE) and is_reset_question:
        add("A320 Reset Guide FULL CIDS RESET", "Used canonical CIDS reset guide search.")
        add("COM CIDS 1+2 FAULT", "Used canonical CIDS fault/manual search.")

    if is_reset_question:
        reset_query = cleaned or original
        reset_query = re.sub(
            r"\b(what|is|are|the|for|on|of|to|a|an|please|lookup|look|up|find|give|me|how|do|i|clear)\b",
            " ",
            reset_query,
            flags=re.IGNORECASE,
        )
        reset_query = re.sub(r"\s+", " ", reset_query).strip(" ?.,")
        if reset_query:
            add(f"A320 Reset Guide {reset_query}", "Searched PDF reset guide supplement.")
            add(f"REFERENCE ONLY {reset_query}", "Searched PDF quick reference supplement.")

    if abbreviation_terms and is_abbreviation_question and not is_circuit_breaker_question:
        add(
            "Airbus Abbreviations AviationHunt " + " ".join(abbreviation_terms),
            "Searched Airbus abbreviation supplement.",
        )

    if cleaned and cleaned.lower() != original.lower():
        add(cleaned, "Removed aircraft/tail token from keyword search.")
    add(original)

    # Last-resort keyword simplifications for natural questions.
    simplified = cleaned
    simplified = re.sub(r"\b(what|where|which|is|are|the|for|on|of|to|a|an|please|lookup|look|up|find|give|me|number|location|located)\b", " ", simplified, flags=re.IGNORECASE)
    simplified = re.sub(r"\s+", " ", simplified).strip(" ?.,")
    if simplified:
        add(simplified, "Removed filler words from keyword search.")

    return queries[: max(1, min(int(self.valves.max_search_attempts), 10))], notes

def _abbreviation_terms(self, query: str) -> list[str]:
    query = query or ""
    terms: list[str] = []
    if re.search(r"\bCIDS\b", query, re.IGNORECASE):
        terms.append("CIDS")
    if re.search(r"\bCB\b|\bC/B\b", query, re.IGNORECASE):
        terms.append("C/B")
    return terms

def _abbreviation_lines(self, query: str) -> list[str]:
    terms = _abbreviation_terms(self, query)
    lines: list[str] = []
    if "CIDS" in terms:
        lines.append("CIDS = Cabin Intercommunication Data System")
    if "C/B" in terms:
        lines.append("C/B or CB = Circuit Breaker")
    return lines

def _search(
    self,
    query: str,
    aircraft: str,
    customization: str,
    limit: int,
    route_meta: dict[str, str] | None = None,
) -> tuple[dict, str, str]:
    params = {
        "q": query,
        "aircraft": aircraft,
        "customization": customization,
        "limit": str(limit),
    }
    if route_meta:
        for key in ("route", "ata", "manual_type"):
            if route_meta.get(key):
                params[key] = route_meta[key]
    url = self.valves.search_base_url.rstrip("/") + "/search?" + urlencode(params)
    with urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8")), query, url

def _media_lookup(self, source_zip: str, path: str, query: str, limit: int = 4) -> list[dict]:
    if not source_zip or not path:
        return []
    params = {
        "source_zip": source_zip,
        "path": path,
        "q": query,
        "limit": str(limit),
    }
    url = self.valves.search_base_url.rstrip("/") + "/media/lookup?" + urlencode(params)
    try:
        with urlopen(url, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    return payload.get("results") or []

def _media_lines(self, best: dict[str, str], query: str) -> list[str]:
    media = _media_lookup(self, best.get("source_zip", ""), best.get("path", ""), query)
    if not media:
        return []
    lines = ["", "IPC figure candidates:"]
    displayable = [item for item in media if item.get("displayable") and item.get("url")]
    first = displayable[0] if displayable else media[0]
    if first.get("url"):
        lines.append(f"IPC Figure: {first['url']}")
        if first.get("displayable"):
            lines.append(f"![IPC Figure]({first['url']})")
    for item in media[:3]:
        if item is first:
            continue
        if item.get("url"):
            lines.append(f"Other figure candidate: {item['url']}")
    return lines

def _reference_key(self, row_or_candidate: dict) -> str:
    reference = (
        f"{row_or_candidate.get('source_zip', '')} :: {row_or_candidate.get('path', '')} "
        f":: chunk {row_or_candidate.get('chunk_index', '')}"
    )
    if row_or_candidate.get("effectivity"):
        reference += f" :: effectivity_hint {row_or_candidate['effectivity']}"
    elif row_or_candidate.get("aircraft_hint"):
        reference += f" :: effectivity_hint {row_or_candidate['aircraft_hint']}"
    return reference

def _display_list(self, values: list[str], limit: int = 5) -> str:
    cleaned: list[str] = []
    for value in values:
        value = re.sub(r"\s+", " ", (value or "").strip())
        if value and value not in cleaned:
            cleaned.append(value)
    return " / ".join(cleaned[:limit])

def _aircraft_number(self, aircraft: str) -> int | None:
    match = re.search(r"\d{3}", aircraft or "")
    if not match:
        return None
    return int(match.group(0))

def _effectivity_matches_aircraft(self, effectivity: str, aircraft: str) -> bool:
    aircraft_no = _aircraft_number(self, aircraft)
    if aircraft_no is None:
        return False
    for token in re.findall(r"\d{3,6}", effectivity or ""):
        if len(token) == 3 and int(token) == aircraft_no:
            return True
        if len(token) == 6:
            start = int(token[:3])
            end = int(token[3:])
            if start <= aircraft_no <= end:
                return True
    return False

def _candidate_score(self, candidate: dict[str, str], query: str, aircraft: str) -> int:
    score = 0
    part_tokens = set(_part_number_tokens(self, query))
    if candidate.get("part_number", "") in part_tokens:
        score += 200
    if _effectivity_matches_aircraft(self, candidate.get("effectivity", ""), aircraft):
        score += 100
    fin = candidate.get("fin", "").replace("-", "").upper()
    query_upper = (query or "").upper()
    if "CAPT" in query_upper or "CAPTAIN" in query_upper:
        if fin in {"2RN1", "2-RN-1"}:
            score += 30
    if re.search(r"\bF/?O\b|FIRST\s+OFFICER", query_upper):
        if fin in {"2RN2", "2-RN-2"}:
            score += 30
    source_priority = {"AIPC item table": 30, "AIPC FIN list": 20, "AIPC P/N list": 10}
    score += source_priority.get(candidate.get("source_type", ""), 0)
    return score

def _candidate_matches_terms(self, candidate: dict[str, str], terms: set[str]) -> bool:
    if not terms:
        return True
    haystack = " ".join(
        [
            candidate.get("ipc_name", ""),
            candidate.get("designation", ""),
            candidate.get("ata_title", ""),
            candidate.get("fin", ""),
            candidate.get("part_number", ""),
        ]
    ).upper()
    return any(term in haystack for term in terms)

def _csv_candidate_lines(self, text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if line:
            lines.append(line)
    inline_records = re.findall(r'"AIPC"(?:,"[^"]*"){3,20}', text or "", flags=re.IGNORECASE)
    for record in inline_records:
        if record not in lines:
            lines.append(record)
    return lines

def _parse_aipc_csv_lines(self, row: dict, wanted_terms: set[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    text = row.get("text") or ""
    if ".CSV" not in (row.get("path") or "").upper():
        return parsed
    path_upper = (row.get("path") or "").upper()
    for raw_line in _csv_candidate_lines(self, text):
        line = raw_line.strip()
        if not line or "AIPC" not in line.upper():
            continue
        try:
            fields = next(csv.reader([line]))
        except Exception:
            continue
        if len(fields) < 6 or fields[0].upper() != "AIPC":
            continue
        if "GENERAL_FIN_LIST" in path_upper and len(fields) >= 10:
            candidate = {
                "part_number": fields[3].strip().upper(),
                "fin": fields[1].strip().upper(),
                "circuit": fields[2].strip().upper(),
                "manufacturer": fields[4].strip().upper(),
                "ipc_name": fields[5].strip().upper(),
                "designation": fields[5].strip().upper(),
                "unit_code": fields[6].strip().upper(),
                "effectivity": fields[7].strip().upper() or row.get("aircraft_hint", ""),
                "ata": fields[8].strip().upper(),
                "ata_title": fields[9].strip().upper(),
                "source_type": "AIPC FIN list",
                "source_zip": row.get("source_zip", ""),
                "path": row.get("path", ""),
                "chunk_index": str(row.get("chunk_index", "")),
            }
        elif "GENERAL_PN_DATA_LIST" in path_upper and len(fields) >= 6:
            candidate = {
                "part_number": fields[1].strip().upper(),
                "manufacturer": fields[2].strip().upper(),
                "ipc_name": fields[5].strip().upper(),
                "designation": fields[5].strip().upper(),
                "material_group": fields[4].strip().upper(),
                "maintenance_manual": fields[10].strip().upper() if len(fields) > 10 else "",
                "effectivity": row.get("aircraft_hint", ""),
                "source_type": "AIPC P/N list",
                "source_zip": row.get("source_zip", ""),
                "path": row.get("path", ""),
                "chunk_index": str(row.get("chunk_index", "")),
            }
        else:
            continue
        if _candidate_matches_terms(self, candidate, wanted_terms):
            parsed.append(candidate)
    return parsed

def _parse_aipc_item_text(self, row: dict, wanted_terms: set[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    text = re.sub(r"\s+", " ", (row.get("text") or "").upper())
    if not text:
        return parsed
    pn = _part_number_regex(self)
    item_pattern = re.compile(
        rf"(?P<pn>\b{pn}\b)\s+"
        r"(?P<mfr>[A-Z0-9]{4,5})\s+"
        r"(?P=pn)\s+(?P=mfr)\s+"
        r"(?P<short>[A-Z][A-Z0-9/-]{2,})\s+"
        r"(?P<name>[A-Z][A-Z0-9 /().-]*?)\s+"
        r"(?P<item>\d{3})\s+(?P<figure_item>\d{4})",
    )
    for match in item_pattern.finditer(text):
        if _is_manual_reference_token(self, match.group("pn")):
            continue
        name = re.sub(r"\s+", " ", match.group("name")).strip()
        trailing = text[match.end() : match.end() + 180]
        candidate = {
            "part_number": match.group("pn"),
            "manufacturer": match.group("mfr"),
            "ipc_name": match.group("short"),
            "designation": name,
            "item_number": _display_list(self, [match.group("item"), match.group("figure_item")], 2),
            "fin": _display_list(self, re.findall(r"\b\d{3,4}-?[A-Z]{1,3}-?\d\b", trailing), 4),
            "effectivity": row.get("aircraft_hint", ""),
            "source_type": "AIPC item table",
            "source_zip": row.get("source_zip", ""),
            "path": row.get("path", ""),
            "chunk_index": str(row.get("chunk_index", "")),
        }
        if _candidate_matches_terms(self, candidate, wanted_terms):
            parsed.append(candidate)
    return parsed

def _ipc_breakdown_candidates(self, rows, wanted_terms: set[str], query: str = "", aircraft: str = "") -> list[dict[str, str]]:
    by_key: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for row in rows:
        for candidate in _parse_aipc_item_text(self, row, wanted_terms) + _parse_aipc_csv_lines(self, row, wanted_terms):
            key = (
                candidate.get("part_number", ""),
                candidate.get("fin", ""),
                candidate.get("item_number", ""),
                candidate.get("ipc_name", ""),
                candidate.get("designation", ""),
            )
            existing = by_key.get(key)
            if existing is None or _candidate_score(self, candidate, query, aircraft) > _candidate_score(self, existing, query, aircraft):
                by_key[key] = candidate
    candidates = list(by_key.values())
    priority = {"AIPC item table": 0, "AIPC FIN list": 1, "AIPC P/N list": 2}
    candidates.sort(
        key=lambda item: (
            -_candidate_score(self, item, query, aircraft),
            priority.get(item.get("source_type", ""), 9),
        )
    )
    return candidates

def _append_ipc_breakdown_lines(self, lines: list[str], best: dict[str, str], candidates: list[dict[str, str]]) -> None:
    lines.append("")
    lines.append("IPC breakdown:")
    if best.get("item_number"):
        lines.append(f"Item number: {best['item_number']}")
    if best.get("fin"):
        lines.append(f"FIN: {best['fin']}")
    if best.get("ipc_name") or best.get("designation"):
        names = _display_list(self, [best.get("ipc_name", ""), best.get("designation", "")], 3)
        lines.append(f"IPC names/designations: {names}")
    lines.append(f"P/N: {best['part_number']}")
    if best.get("manufacturer"):
        lines.append(f"MFR: {best['manufacturer']}")
    ata = _display_list(self, [best.get("ata", ""), best.get("ata_title", "")], 2)
    if ata:
        lines.append(f"ATA: {ata}")
    if best.get("unit_code"):
        lines.append(f"Unit/effectivity code: {best['unit_code']}")

    alternate_names: list[str] = []
    alternate_items: list[str] = []
    alternate_fins: list[str] = []
    for candidate in candidates:
        if candidate.get("part_number") != best.get("part_number"):
            continue
        alternate_names.extend([candidate.get("ipc_name", ""), candidate.get("designation", "")])
        alternate_items.append(candidate.get("item_number", ""))
        alternate_fins.append(candidate.get("fin", ""))
    names = _display_list(self, alternate_names, 6)
    items = _display_list(self, alternate_items, 4)
    fins = _display_list(self, alternate_fins, 8)
    if names and names != _display_list(self, [best.get("ipc_name", ""), best.get("designation", "")], 3):
        lines.append(f"Other IPC names found for same P/N: {names}")
    if items and items != best.get("item_number"):
        lines.append(f"Other item numbers found for same P/N: {items}")
    if fins and fins != best.get("fin"):
        lines.append(f"Other FINs found for same P/N: {fins}")

def _breaker_target_terms(self, query: str) -> set[str]:
    ignore = {
        "WHAT",
        "WHICH",
        "WHERE",
        "ARE",
        "THE",
        "FOR",
        "AIRCRAFT",
        "ACFT",
        "CIRCUIT",
        "CIRCIT",
        "CIRCUT",
        "BREAKER",
        "BREAKERS",
        "BRAKER",
        "BRAKERS",
        "LOCATION",
        "LOCATED",
    }
    return {
        word.upper()
        for word in re.findall(r"[A-Z][A-Z0-9/-]{2,}", query or "", flags=re.IGNORECASE)
        if word.upper() not in ignore and not re.fullmatch(r"N?\d{3}NV|\d{3}", word.upper())
    }

def _direct_answer_candidates(self, rows, query: str = "") -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    target_terms = _breaker_target_terms(self, query)
    breaker_patterns = [
        re.compile(
            r"\b(?P<fin>\d{1,2}-[A-Z]{2}-\d)\b\s+"
            r"(?P<component>[A-Z0-9][A-Z0-9/-]{2,})\s+"
            r"(?P<circuit>\d{1,2}-[A-Z]{2}-\d)\s+"
            r"(?P<description>[A-Z][A-Z0-9/ -]*?)\s+"
            r"(?P<panel>\d{2,3}VU)\s+(?P<location>[A-Z]\d{2})\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?P<circuit>\d{1,2}-[A-Z]{2}-\d)\b\s+"
            r"(?P<description>[A-Z][A-Z0-9/ -]*?)\s+"
            r"(?P<panel>\d{2,3}VU)\s+(?P<location>[A-Z]\d{2})\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?P<circuit>\d{1,2}-[A-Z]{2})\b\s+"
            r"(?P<description>[A-Z][A-Z0-9/ -]*?)\s+"
            r"(?P<panel>\d{2,4}VU)\s+(?P<location>[A-Z]\d{2})\b",
            re.IGNORECASE,
        ),
    ]
    for row in rows:
        text = re.sub(r"\s+", " ", row.get("text") or "").upper()
        bmc_context = "BMC" in target_terms and ("BMC" in text or "BLEED MONITORING COMPUTER" in text)
        strict_component_terms = {
            "FMGC",
            "BMC",
            "CIDS",
            "ACP",
            "ACARS",
            "BSCU",
            "FADEC",
            "EIU",
            "LGCIU",
            "SDCU",
            "TGB",
        }
        allow_row_context = not bool(target_terms & strict_component_terms)
        row_context_matches = bool(allow_row_context and target_terms and any(term in text for term in target_terms))
        for pattern in breaker_patterns:
            for match in pattern.finditer(text):
                item = {key: (value or "").upper() for key, value in match.groupdict(default="").items()}
                item["raw"] = match.group(0).upper()
                if not item.get("component") and re.search(r"\b\d{1,2}-[A-Z]{2}-\d\b", item.get("description", "")):
                    continue
                searchable = " ".join(
                    [
                        item.get("fin", ""),
                        item.get("component", ""),
                        item.get("circuit", ""),
                        item.get("description", ""),
                    ]
                )
                if target_terms and not bmc_context and not row_context_matches and not any(term in searchable for term in target_terms):
                    continue
                if bmc_context and not item.get("component"):
                    item["component"] = "BMC"
                key = (item.get("circuit", ""), item.get("panel", ""), item.get("location", ""))
                existing = by_key.get(key)
                if existing is None or (not existing.get("fin") and item.get("fin")):
                    by_key[key] = item
    candidates = list(by_key.values())
    candidates.sort(key=lambda item: (item.get("fin", "") or item.get("circuit", ""), item.get("panel", ""), item.get("location", "")))
    return candidates

def _format_breaker_answer(self, candidates: list[dict[str, str]], aircraft: str) -> list[str]:
    lines = [
        "FINAL_ANSWER_COPY_THIS_STYLE",
        "For circuit breaker lookup, the indexed manual returned:",
    ]
    for candidate in candidates:
        description = candidate.get("description", "")
        component = candidate.get("component", "")
        fin = candidate.get("fin", "")
        circuit = candidate.get("circuit", "")
        panel = candidate.get("panel", "")
        location = candidate.get("location", "")
        label_parts = []
        if fin:
            label_parts.append(f"CB/FIN: {fin}")
        if component:
            label_parts.append(f"Component: {component}")
        if circuit:
            label_parts.append(f"Circuit: {circuit}")
        if description:
            label_parts.append(f"Description: {description}")
        if panel or location:
            label_parts.append(f"Panel: {panel or '[unknown]'}")
            label_parts.append(f"Location/grid: {location or '[unknown]'}")
        lines.append(" / ".join(label_parts) if label_parts else candidate.get("raw", ""))
    return lines

def _centerbody_part_candidates(self, rows) -> list[dict[str, str]]:
    ipc_candidates = _ipc_breakdown_candidates(self, rows, {"CENTERBODY", "CENTRBDY", "CENTERBD"})
    if ipc_candidates:
        return ipc_candidates

    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    pn_pattern = re.compile(rf"\b{_part_number_regex(self)}\b")
    for row in rows:
        text = re.sub(r"\s+", " ", row.get("text") or "").upper()
        if "CENTERBODY" not in text and "CENTRBDY" not in text and "CENTERBD" not in text:
            continue
        if "HOT EXHAUST" not in text and "EXHAUST" not in text:
            continue
        for match in pn_pattern.finditer(text):
            pn = match.group(0)
            window = text[max(0, match.start() - 120) : match.end() + 160]
            source_line = ""
            for line in (row.get("text") or "").upper().splitlines():
                if pn in line:
                    source_line = line
                    break
            source_context = source_line or window
            if not any(term in source_context for term in ["CENTERBODY", "CENTRBDY", "CENTERBD"]):
                continue
            if "NOZZLE" in source_context and not re.search(r"CENTERBOD?Y|CENTRBDY|CENTERBD", source_context):
                continue
            if pn in seen:
                continue
            seen.add(pn)
            fin_match = re.search(r"\b3900-?KM-?[12]\b|\b3900KM[12]\b", source_context)
            candidates.append(
                {
                    "part_number": pn,
                    "fin": fin_match.group(0) if fin_match else "",
                    "designation": "CENTERBODY ASSY",
                    "source_zip": row.get("source_zip", ""),
                    "path": row.get("path", ""),
                    "chunk_index": str(row.get("chunk_index", "")),
                    "effectivity": row.get("aircraft_hint", ""),
                    "source_type": "text fallback",
                }
            )
    return candidates

def _part_number_candidates(self, rows, query: str, aircraft: str = "") -> list[dict[str, str]]:
    exact_part_tokens = set(_part_number_tokens(self, query))
    query_terms = _part_lookup_terms(self, query)
    ipc_candidates = _ipc_breakdown_candidates(
        self,
        rows,
        query_terms,
        query,
        aircraft or _extract_aircraft_from_query(self, query),
    )
    if ipc_candidates:
        return ipc_candidates

    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    pn_pattern = re.compile(rf"\b{_part_number_regex(self)}\b")
    query_words = _part_lookup_terms(self, query)
    for row in rows:
        source_type = (row.get("manual_type_hint") or row.get("document_hint") or row.get("path") or "").upper()
        if "AIPC" not in source_type and "IPC" not in source_type and "GENERAL_FIN_LIST" not in source_type and "GENERAL_PN_DATA_LIST" not in source_type:
            continue
        text = re.sub(r"\s+", " ", row.get("text") or "").upper()
        for match in pn_pattern.finditer(text):
            pn = match.group(0)
            if _is_manual_reference_token(self, pn):
                continue
            if exact_part_tokens and pn not in exact_part_tokens:
                continue
            if pn in seen:
                continue
            window = text[max(0, match.start() - 120) : match.end() + 160]
            if query_words and not any(word in window for word in query_words):
                continue
            designation_match = re.search(
                r"\b(TRANSFER GEAR ?BOX|TGB|GEARBOX|CENTERBODY ASSY|CENTERBODY|CENTRBDY|NOZZLE(?: ASSY)?|FILTER(?: ASSY)?|BRACKET|DOWEL|LATCH|KIT INSTL-[A-Z ]+)\b",
                window,
            )
            seen.add(pn)
            candidates.append(
                {
                    "part_number": pn,
                    "designation": designation_match.group(1) if designation_match else "PART",
                    "source_zip": row.get("source_zip", ""),
                    "path": row.get("path", ""),
                    "chunk_index": str(row.get("chunk_index", "")),
                    "effectivity": row.get("aircraft_hint", ""),
                    "source_type": "text fallback",
                }
            )
    candidates.sort(key=lambda item: -_candidate_score(self, item, query, aircraft))
    return candidates

def _format_centerbody_answer(self, candidates: list[dict[str, str]], aircraft: str, query: str) -> list[str]:
    best = candidates[0]
    lines = [
        "FINAL_ANSWER_COPY_THIS_STYLE",
        "For engine centerbody, the indexed manual returned:",
        f"{best['part_number']} / {best['designation']}",
        f"Reference: {_reference_key(self, best)}",
    ]
    _append_ipc_breakdown_lines(self, lines, best, candidates)
    if aircraft:
        lines.append(f"Aircraft context: {aircraft}")
    if len(candidates) > 1:
        alternates = ", ".join(candidate["part_number"] for candidate in candidates[1:4])
        if alternates:
            lines.append(f"Other centerbody variants nearby in the index: {alternates}")
    lines.extend(_media_lines(self, best, query))
    return lines

def _format_part_answer(self, candidates: list[dict[str, str]], query: str, aircraft: str) -> list[str]:
    best = candidates[0]
    subject = "part number"
    noun_match = re.search(r"\b(?:for|of|on)\s+(?:the\s+)?([a-z0-9 /-]{3,50})", query, re.IGNORECASE)
    if noun_match:
        subject = noun_match.group(1).strip(" ?.,")
    lines = [
        "FINAL_ANSWER_COPY_THIS_STYLE",
        f"For {subject}, the indexed manual returned:",
        f"{best['part_number']} / {best['designation']}",
        f"Reference: {_reference_key(self, best)}",
    ]
    _append_ipc_breakdown_lines(self, lines, best, candidates)
    if aircraft:
        lines.append(f"Aircraft context: {aircraft}")
    lines.extend(_media_lines(self, best, query))
    return lines

def _pdf_supplement_rows(self, rows) -> list[dict]:
    return [row for row in rows if row.get("source_zip") == "PDF_SUPPLEMENT"]

def _mel_supplement_rows(self, rows) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("source_zip") == "MEL_SUPPLEMENT"
        or (row.get("source_zip") == "WEB_SUPPLEMENT" and row.get("document_hint") == "MEL_QUICK_REF")
    ]

def _web_supplement_rows(self, rows) -> list[dict]:
    return [row for row in rows if row.get("source_zip") == "WEB_SUPPLEMENT"]

def _prepend_abbreviation_lines(self, answer: str, query: str) -> str:
    lines = _abbreviation_lines(self, query)
    if not lines:
        return answer
    prefix = [
        "Term expansion:",
        *lines,
        "Abbreviation reference: WEB_SUPPLEMENT :: https://www.aviationhunt.com/airbus-abbreviations/",
        "",
    ]
    if answer.startswith("FINAL_ANSWER_COPY_THIS_STYLE\n"):
        return answer.replace(
            "FINAL_ANSWER_COPY_THIS_STYLE\n",
            "FINAL_ANSWER_COPY_THIS_STYLE\n" + "\n".join(prefix),
            1,
        )
    return "\n".join(prefix) + answer

def _format_abbreviation_answer(self, rows: list[dict], query: str, aircraft: str) -> str:
    parts = [
        "FINAL_ANSWER_COPY_THIS_STYLE",
        "The abbreviation supplement returned:",
    ]
    parts.extend(_abbreviation_lines(self, query) or ["No built-in abbreviation expansion matched the question."])
    for row in rows[:2]:
        parts.append(
            f"Reference: {row.get('source_zip', '')} :: {row.get('path', '')} "
            f":: chunk {row.get('chunk_index', '')}"
        )
    if aircraft:
        parts.append(f"Aircraft context: {aircraft}")
    return "\n".join(parts).strip()

def _format_pdf_supplement_answer(
    self,
    rows: list[dict],
    query: str,
    aircraft: str,
    searched_query: str,
    searched_queries: list[str],
    customization: str,
    search_notes: list[str],
    payload: dict,
) -> str:
    parts = [
        "FINAL_ANSWER_COPY_THIS_STYLE",
        "The reset-guide supplement search returned:",
    ]
    for index, row in enumerate(rows[:3], start=1):
        text = re.sub(r"\s+", " ", row.get("text") or "").strip()
        if len(text) > 1200:
            text = text[:1200].rstrip() + " [truncated]"
        parts.append("")
        parts.append(f"Result {index}: {row.get('title_hint') or row.get('path')}")
        parts.append(text)
        reference = (
            f"{row.get('source_zip', '')} :: {row.get('path', '')} "
            f":: chunk {row.get('chunk_index', '')}"
        )
        parts.append(f"Reference: {reference}")
    if aircraft:
        parts.append(f"Aircraft context: {aircraft}")
    parts.extend(
        [
            "",
            "SEARCH_DETAILS",
            f"original_query: {query}",
            f"searched_query: {searched_query}",
            f"all_queries_tried: {searched_queries}",
            f"customization: {customization or '[not filtered]'}",
            "; ".join(search_notes),
            payload.get("note", ""),
        ]
    )
    return "\n".join(part for part in parts if part is not None).strip()

def _format_mel_supplement_answer(
    self,
    rows: list[dict],
    query: str,
    aircraft: str,
    searched_query: str,
    searched_queries: list[str],
    customization: str,
    search_notes: list[str],
    payload: dict,
) -> str:
    rows = _rank_mel_rows(self, rows, query)
    parts = [
        "FINAL_ANSWER_COPY_THIS_STYLE",
        "The MEL/CDL/NEF supplement search returned:",
        "Verify against the current approved company MEL before applying a deferral.",
    ]
    parts.extend(_mel_breakdown_lines(self, rows, query))
    for index, row in enumerate(rows[:4], start=1):
        text = re.sub(r"\s+", " ", row.get("text") or "").strip()
        if len(text) > 1400:
            text = text[:1400].rstrip() + " [truncated]"
        parts.append("")
        parts.append(f"Result {index}: {row.get('title_hint') or row.get('path')}")
        parts.append(text)
        reference = (
            f"{row.get('source_zip', '')} :: {row.get('path', '')} "
            f":: chunk {row.get('chunk_index', '')}"
        )
        parts.append(f"Reference: {reference}")
    if aircraft:
        parts.append(f"Aircraft context: {aircraft}")
    parts.extend(
        [
            "",
            "SEARCH_DETAILS",
            f"original_query: {query}",
            f"searched_query: {searched_query}",
            f"all_queries_tried: {searched_queries}",
            f"customization: {customization or '[not filtered]'}",
            "; ".join(search_notes),
            payload.get("note", ""),
        ]
    )
    return "\n".join(part for part in parts if part is not None).strip()

def _mel_breakdown_lines(self, rows: list[dict], query: str) -> list[str]:
    if not rows:
        return []
    combined = " ".join(re.sub(r"\s+", " ", row.get("text") or "").strip() for row in rows[:4])
    combined = re.sub(r"\s+", " ", combined).strip()
    best = _extract_mel_item(self, combined, query)
    provisos = _extract_mel_provisos(self, combined)
    notes = _extract_mel_notes(self, combined)
    placards = _extract_mel_placards(self, combined)
    maintenance = _extract_mel_maintenance_refs(self, combined)

    lines = ["", "MEL_BREAKDOWN"]
    if best.get("item"):
        lines.append(f"Item: {best['item']}")
    if best.get("name"):
        lines.append(f"Item name/condition: {best['name']}")
    if best.get("repair_category"):
        lines.append(f"Repair category/time limit code: {best['repair_category']}")
    if best.get("qty_inst") or best.get("qty_req"):
        lines.append(f"QTY INST / QTY REQ: {best.get('qty_inst') or '?'} / {best.get('qty_req') or '?'}")
    if best.get("mo_flags"):
        lines.append(f"Procedure flags: {best['mo_flags']}")
    if provisos:
        lines.append("Dispatch/deferral conditions found:")
        lines.extend(f"- {item}" for item in provisos[:8])
    if notes:
        lines.append("Notes found:")
        lines.extend(f"- {item}" for item in notes[:6])
    if placards:
        lines.append("Placard/sticker text found:")
        lines.extend(f"- {item}" for item in placards[:6])
    if maintenance:
        lines.append("Maintenance/operations references found:")
        lines.extend(f"- {item}" for item in maintenance[:6])
    if len(lines) == 2:
        lines.append("No structured MEL fields could be parsed; use the result chunks below.")
    return lines

def _extract_mel_item(self, text: str, query: str) -> dict[str, str]:
    query_upper = (query or "").upper()
    candidates: list[dict[str, str]] = []
    item_pattern = re.compile(
        r"(?P<item>\b\d{2}-\d{2}-\d{2}[A-Z]?)\s+"
        r"(?P<body>.{0,260}?)\s+"
        r"(?P<cp>[YN])\s+(?P<category>[A-D])\s+(?P<qty_inst>[-0-9]+)\s+(?P<qty_req>[-0-9]+)\s*"
        r"(?P<mo>(?:\([MO]\)){0,2})\s+May be inoperative provided:",
        re.IGNORECASE,
    )
    for match in item_pattern.finditer(text or ""):
        item_number = match.group("item").upper()
        body = re.sub(r"\s+", " ", match.group("body")).strip(" -")
        nested_item = re.search(r"\b(\d{2}-\d{2}-\d{2}[A-Z])\s+(.+)$", body, re.IGNORECASE)
        if nested_item:
            item_number = nested_item.group(1).upper()
            body = nested_item.group(2).strip(" -")
        score = 0
        for token in re.findall(r"[A-Z0-9]{3,}", query_upper):
            if token in body.upper():
                score += 5
        if re.search(r"[A-Z]$", item_number):
            score += 10
        if "OVERHEAD" in query_upper and "STOW" in body.upper():
            score += 20
        if "BIN" in query_upper and "BIN" in body.upper():
            score += 20
        candidates.append(
            {
                "item": item_number,
                "name": body,
                "repair_category": match.group("category").upper(),
                "qty_inst": match.group("qty_inst"),
                "qty_req": match.group("qty_req"),
                "mo_flags": match.group("mo").upper(),
                "score": str(score),
            }
        )
    if candidates:
        candidates.sort(key=lambda item: int(item.get("score", "0")), reverse=True)
        return candidates[0]

    fallback = re.search(r"\b(?P<item>\d{2}-\d{2}-\d{2}[A-Z]?)\s+(?P<name>.{0,140}?)(?:Subsystem|MEL ITEM|May be inoperative|$)", text or "", re.IGNORECASE)
    if fallback:
        return {"item": fallback.group("item").upper(), "name": re.sub(r"\s+", " ", fallback.group("name")).strip()}
    return {}

def _extract_mel_provisos(self, text: str) -> list[str]:
    match = re.search(
        r"May be inoperative provided:\s*(?P<body>.*?)(?:NOTE:|PLACARD MEL ITEM|MAINTENANCE \(M\)|OPERATIONS \(O\)|Allegiant Air|\[PDF page|$)",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return []
    body = match.group("body")
    parts = re.split(r"\s+[a-d]\)\s+", " " + body, flags=re.IGNORECASE)
    cleaned: list[str] = []
    for part in parts:
        value = re.sub(r"\s+", " ", part).strip(" ;,.")
        value = re.sub(r"^[a-d]\)\s*", "", value, flags=re.IGNORECASE)
        if value and len(value) > 8 and value not in cleaned:
            cleaned.append(value)
    return cleaned

def _extract_mel_notes(self, text: str) -> list[str]:
    notes: list[str] = []
    for match in re.finditer(r"NOTE:\s*(.*?)(?=\s+NOTE:|\s+PLACARD MEL ITEM|\s+MAINTENANCE \(M\)|\s+OPERATIONS \(O\)|\s+Allegiant Air|\s+\[PDF page|$)", text or "", re.IGNORECASE):
        note = re.sub(r"\s+", " ", match.group(1)).strip(" ;,.")
        if note and note not in notes:
            notes.append(note)
    return notes

def _extract_mel_placards(self, text: str) -> list[str]:
    placards: list[str] = []
    for match in re.finditer(r"\b(M\d{3}\s*/\s*M\d{3}\s*/\s*M\d{3}|M\d{3})\s+TEXT\s+(.{0,220}?)(?=\s+\d{2}-\d{2}-\d{2}[A-Z]?|\s+MAINTENANCE \(M\)|\s+OPERATIONS \(O\)|\s+Allegiant Air|$)", text or "", re.IGNORECASE):
        value = re.sub(r"\s+", " ", match.group(0)).strip(" ;,.")
        if value and value not in placards:
            placards.append(value)
    if not placards:
        for match in re.finditer(r"PLACARD MEL ITEM\s+(.{0,260}?)(?=\s+MAINTENANCE \(M\)|\s+OPERATIONS \(O\)|\s+Allegiant Air|$)", text or "", re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(1)).strip(" ;,.")
            if value and value not in placards:
                placards.append(value)
    return placards

def _extract_mel_maintenance_refs(self, text: str) -> list[str]:
    refs: list[str] = []
    for pattern in [
        r"Refer to AMM\s+([0-9A-Z-]+)\s*[? -]+(.{0,120}?)(?=\.|\s+\d+\.|$)",
        r"MAINTENANCE \(M\)\s*(.{0,260}?)(?=\s+OPERATIONS \(O\)|\s+Allegiant Air|$)",
        r"OPERATIONS \(O\)\s*(.{0,260}?)(?=\s+MAINTENANCE \(M\)|\s+Allegiant Air|$)",
    ]:
        for match in re.finditer(pattern, text or "", re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(0)).strip(" ;,.")
            if value and value not in refs:
                refs.append(value)
    return refs

def _rank_mel_rows(self, rows: list[dict], query: str) -> list[dict]:
    query_text = re.sub(r"\s+", " ", (query or "").upper())
    query_terms = [
        term
        for term in re.findall(r"[A-Z0-9/-]{3,}", query_text)
        if term not in {"WHAT", "WHICH", "MEL", "CDL", "NEF", "FOR", "THE", "APPLY", "SHOULD"}
    ]
    phrases: list[str] = []
    if re.search(r"\bOVERHEAD\s+BIN\b", query_text):
        phrases.extend(["OVERHEAD BIN", "OVERHEAD STORAGE BIN", "STOWAGE BIN", "STORAGE BIN"])
    if re.search(r"\bACP\b|AUDIO\s+CONTROL\s+PANEL", query_text):
        phrases.extend(["AUDIO CONTROL PANEL", "ACP", "23-51-02"])

    def score(row: dict) -> int:
        text = re.sub(r"\s+", " ", (row.get("text") or "").upper())
        value = int(row.get("route_score") or 0)
        for phrase in phrases:
            if phrase in text:
                value += 60
        for term in query_terms:
            if term in text:
                value += 5
        if "SUBSYSTEM CP TIME LIMIT CODE" in text or "REMARKS AND EXCEPTIONS" in text:
            value += 45
        if "PLACARD MEL ITEM" in text:
            value += 25
        if "END MEL" in text:
            value += 15
        if "ACRONYM DEFINITION" in text:
            value -= 80
        return value

    return sorted(rows, key=score, reverse=True)


