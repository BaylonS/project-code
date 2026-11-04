from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import httpx


def heuristic_enrichment(text: str, source_url: str = "") -> dict[str, object]:
    compact = " ".join(text.split())
    title = compact[:72] + ("..." if len(compact) > 72 else "")
    lowered = compact.lower()
    tags: list[str] = []
    rules = {
        "idea": ["idea", "what if", "could build", "concept"],
        "task": ["todo", "follow up", "need to", "remember to", "deadline"],
        "meeting": ["meeting", "call", "agenda", "notes from"],
        "link": ["http://", "https://", "www."],
        "code": ["function", "docker", "api", "bug", "error", "proxmox"],
        "work": ["client", "coworker", "project", "launch", "invoice"],
    }
    for tag, needles in rules.items():
        if any(needle in lowered for needle in needles):
            tags.append(tag)
    if source_url:
        host = urlparse(source_url).netloc.replace("www.", "")
        if host:
            tags.append(host.split(".")[0])
    if not tags:
        tags = ["note"]
    return {"title": title or "Untitled", "summary": compact[:220], "tags": tags}


async def enrich_chunk(text: str, source_url: str = "") -> dict[str, object]:
    base = os.getenv("LLM_API_BASE", "").rstrip("/")
    model = os.getenv("LLM_MODEL", "llama3.1")
    api_key = os.getenv("LLM_API_KEY", "local")
    if not base:
        return heuristic_enrichment(text, source_url)

    prompt = {
        "role": "system",
        "content": (
            "You organize a private brain-dump inbox. Return strict JSON with keys "
            "title, summary, tags. title is under 12 words, summary is one sentence, "
            "tags is 2-6 short lowercase slugs. Do not include markdown."
        ),
    }
    user = {"role": "user", "content": f"Source URL: {source_url or 'none'}\n\nChunk:\n{text}"}
    payload = {
        "model": model,
        "messages": [prompt, user],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        tags = parsed.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        return {
            "title": str(parsed.get("title") or "")[:140],
            "summary": str(parsed.get("summary") or "")[:500],
            "tags": [str(tag) for tag in tags],
        }
    except Exception:
        return heuristic_enrichment(text, source_url)
