"""
title: Local Device Search
author: Baylon + Codex
description: Search and read text files from explicitly allowed folders and mounted devices.
required_open_webui_version: 0.4.0
version: 0.1.0
licence: MIT
"""

from pathlib import Path

from pydantic import BaseModel, Field


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".py", ".js", ".html"}


def _roots(raw):
    return [Path(item.strip()).expanduser().resolve() for item in raw.split(";") if item.strip()]


def _inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_path(path, roots):
    candidate = Path(path).expanduser().resolve()
    if not any(_inside(candidate, root) for root in roots):
        allowed = "; ".join(str(root) for root in roots) or "[none configured]"
        raise ValueError(f"Path is outside allowed folders. Allowed folders: {allowed}")
    return candidate


class Tools:
    class Valves(BaseModel):
        allowed_folders: str = Field(
            default="/mnt;/data;/app/backend/data",
            description="Semicolon-separated folders this tool may search/read.",
        )
        max_files_to_scan: int = Field(default=800, description="Maximum files to inspect per search.")
        max_results: int = Field(default=20, description="Maximum matching files to return.")
        max_chars_to_read: int = Field(default=8000, description="Maximum characters returned by local_file_read.")

    def __init__(self):
        self.valves = self.Valves()

    async def local_file_search(self, query: str, folder: str = "", extension: str = "") -> str:
        """
        Search allowed local text files.

        :param query: Text to find.
        :param folder: Optional allowed subfolder to search.
        :param extension: Optional extension filter, for example .md or txt.
        """
        query = (query or "").strip()
        if not query:
            return "Give me search text first."

        roots = _roots(self.valves.allowed_folders)
        if not roots:
            return "LOCAL_DEVICE_SEARCH_NOT_CONFIGURED: No allowed folders are configured."

        search_roots = roots
        if folder.strip():
            try:
                search_roots = [_safe_path(folder, roots)]
            except Exception as exc:
                return f"LOCAL_DEVICE_SEARCH_BLOCKED: {exc}"

        ext = (extension or "").strip().lower()
        if ext and not ext.startswith("."):
            ext = "." + ext

        found = []
        scanned = 0
        needle = query.lower()
        for root in search_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if scanned >= int(self.valves.max_files_to_scan):
                    break
                if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                if ext and path.suffix.lower() != ext:
                    continue
                scanned += 1
                try:
                    lines = path.read_text(errors="replace").splitlines()
                except Exception:
                    continue
                for line_no, line in enumerate(lines, start=1):
                    if needle in line.lower():
                        found.append(f"{path} : line {line_no}: {line.strip()[:250]}")
                        break
                if len(found) >= int(self.valves.max_results):
                    break

        if not found:
            return f"NO_LOCAL_DEVICE_MATCHES. Searched {scanned} text files for {query!r}."

        return "\n".join(["LOCAL_DEVICE_SEARCH_RESULTS", f"query: {query}", f"files_scanned: {scanned}", "", *found])

    async def local_file_read(self, path: str, max_chars: int = 8000) -> str:
        """
        Read an allowed local text file.

        :param path: Full file path inside one of the allowed folders.
        :param max_chars: Maximum characters to return.
        """
        roots = _roots(self.valves.allowed_folders)
        if not roots:
            return "LOCAL_DEVICE_SEARCH_NOT_CONFIGURED: No allowed folders are configured."

        try:
            safe = _safe_path(path, roots)
        except Exception as exc:
            return f"LOCAL_DEVICE_READ_BLOCKED: {exc}"

        if not safe.exists():
            return f"LOCAL_DEVICE_READ_MISSING: {safe}"
        if not safe.is_file() or safe.suffix.lower() not in TEXT_EXTENSIONS:
            return f"LOCAL_DEVICE_READ_UNSUPPORTED: {safe} is not a supported text file."

        limit = max(200, min(int(max_chars or self.valves.max_chars_to_read), 50000))
        text = safe.read_text(errors="replace")
        if len(text) > limit:
            text = text[:limit] + "\n[truncated]"
        return f"LOCAL_DEVICE_FILE\npath: {safe}\n\n{text}"
