"""
title: AirnavX Manual Bridge
author: Baylon
version: 0.1.0
description: Search and fetch AirnavX manual content through Baylon's isolated Proxmox bridge.
"""

from html.parser import HTMLParser
from pydantic import BaseModel, Field
from urllib.parse import quote_plus, urljoin, urlparse
import json
import re
import urllib.error
import urllib.request


class _TextExtractor(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip_depth += 1
        if tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip_depth:
            self.skip_depth -= 1
        if tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self):
        text = " ".join(self.parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class Tools:
    class Valves(BaseModel):
        base_url: str = Field(
            default="http://127.0.0.1:15986/airnavx",
            description="AirnavX bridge URL reachable from the Open WebUI server.",
        )
        timeout_seconds: int = Field(default=25, description="HTTP timeout.")
        max_chars: int = Field(
            default=8000,
            description="Maximum characters returned to the model.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _base(self):
        return self.valves.base_url.rstrip("/") + "/"

    def _safe_url(self, path):
        base = self._base()
        path = (path or "").strip()
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        elif path.startswith("/"):
            parsed_base = urlparse(base)
            url = "%s://%s%s" % (parsed_base.scheme, parsed_base.netloc, path)
        else:
            url = urljoin(base, path.lstrip("/"))

        parsed_base = urlparse(base)
        parsed_url = urlparse(url)
        if parsed_url.scheme != parsed_base.scheme or parsed_url.netloc != parsed_base.netloc:
            raise ValueError("Refusing to fetch outside the configured AirnavX bridge.")
        return url

    def _request(self, url, method="GET", body=None, content_type="application/json"):
        data = None
        headers = {
            "User-Agent": "OpenWebUI-AirnavX-Bridge/0.1",
            "Accept": "text/html,application/json,text/plain,*/*",
        }
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = body
            headers["Content-Type"] = content_type

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.valves.timeout_seconds) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                status = response.status
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")[:1000]
            return {
                "ok": False,
                "url": url,
                "status": exc.code,
                "content_type": "",
                "text": "HTTP error %s: %s" % (exc.code, details),
            }
        except urllib.error.URLError as exc:
            return {
                "ok": False,
                "url": url,
                "status": 0,
                "content_type": "",
                "text": "Could not reach AirnavX bridge: %s" % exc.reason,
            }

        text = raw.decode("utf-8", "replace")
        return {
            "ok": True,
            "url": url,
            "status": status,
            "content_type": content_type,
            "text": text,
        }

    def _clean(self, result):
        text = result["text"]
        content_type = result.get("content_type", "")
        if "json" in content_type:
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except Exception:
                pass
        elif "<html" in text[:500].lower() or "<!doctype" in text[:500].lower():
            parser = _TextExtractor()
            parser.feed(text)
            text = parser.text()

        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) > self.valves.max_chars:
            text = text[: self.valves.max_chars] + "\n\n[Truncated by AirnavX tool]"
        header = "AirnavX fetch: %s\nHTTP status: %s\n\n" % (
            result.get("url", ""),
            result.get("status", ""),
        )
        return header + (text or "[No readable text returned]")

    def _sample(self, result):
        text = result.get("text", "")
        if "<html" in text[:500].lower() or "<!doctype" in text[:500].lower():
            parser = _TextExtractor()
            parser.feed(text)
            text = parser.text()
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 700:
            text = text[:700] + "..."
        has_readable_result = bool(text and text != "[empty]")
        return {
            "url": result.get("url", ""),
            "ok": result.get("ok", False),
            "status": result.get("status", ""),
            "content_type": result.get("content_type", ""),
            "has_readable_result": has_readable_result,
            "sample": text or "[empty]",
        }

    def _inspect(self, result):
        text = result["text"]
        links = []
        for match in re.finditer(r"""(?i)(?:href|src|action)\s*=\s*["']([^"']+)["']""", text):
            value = match.group(1).strip()
            if value and value not in links:
                links.append(value)

        endpoints = []
        for match in re.finditer(r"""(?i)["']([^"']*(?:api|search|document|manual|elastic|content|airnavx)[^"']*)["']""", text):
            value = match.group(1).strip()
            if value and value not in endpoints:
                endpoints.append(value)

        snippet = text[: self.valves.max_chars]
        if len(text) > self.valves.max_chars:
            snippet += "\n\n[Raw snippet truncated by AirnavX tool]"

        return (
            "AirnavX inspect: %s\nHTTP status: %s\nContent-Type: %s\n\n"
            "Links/scripts/actions:\n- %s\n\nPossible endpoint strings:\n- %s\n\nRaw snippet:\n%s"
            % (
                result.get("url", ""),
                result.get("status", ""),
                result.get("content_type", ""),
                "\n- ".join(links[:80]) or "[none found]",
                "\n- ".join(endpoints[:120]) or "[none found]",
                snippet or "[empty response]",
            )
        )

    def _keyword_context(self, result, keywords):
        text = result["text"]
        keywords = [k.strip() for k in (keywords or []) if k and k.strip()]
        if not keywords:
            keywords = [
                "search",
                "media",
                "document",
                "content",
                "elastic",
                "part",
                "pn",
                "task",
                "Airnaveo",
            ]

        pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)
        snippets = []
        seen = set()
        for match in pattern.finditer(text):
            start = max(0, match.start() - 260)
            end = min(len(text), match.end() + 420)
            snippet = text[start:end].replace("\r", " ").replace("\n", " ")
            snippet = re.sub(r"\s+", " ", snippet).strip()
            key = snippet[:220]
            if key not in seen:
                seen.add(key)
                snippets.append(snippet)
            if len(snippets) >= 80:
                break

        quoted = []
        quoted_pattern = re.compile(
            r"""["']([^"']*(?:search|media|document|content|elastic|part|pn|task|Airnaveo)[^"']*)["']""",
            re.IGNORECASE,
        )
        for match in quoted_pattern.finditer(text):
            value = match.group(1).strip()
            if value and value not in quoted:
                quoted.append(value)
            if len(quoted) >= 160:
                break

        urlish = []
        urlish_pattern = re.compile(
            r"""(?i)(?:https?://|/|\.{1,2}/)[A-Za-z0-9_./?&=%:#-]*(?:search|media|document|content|elastic|part|pn|task|Airnaveo)[A-Za-z0-9_./?&=%:#-]*"""
        )
        for match in urlish_pattern.finditer(text):
            value = match.group(0).strip().rstrip(".,;)")
            if value and value not in urlish:
                urlish.append(value)
            if len(urlish) >= 160:
                break

        body = (
            "AirnavX keyword context: %s\nHTTP status: %s\nContent-Type: %s\n\n"
            "URL-like matches:\n- %s\n\nQuoted keyword strings:\n- %s\n\nContext snippets:\n- %s"
            % (
                result.get("url", ""),
                result.get("status", ""),
                result.get("content_type", ""),
                "\n- ".join(urlish) or "[none found]",
                "\n- ".join(quoted) or "[none found]",
                "\n\n- ".join(snippets) or "[none found]",
            )
        )
        if len(body) > self.valves.max_chars:
            body = body[: self.valves.max_chars] + "\n\n[Truncated by AirnavX tool]"
        return body

    async def airnavx_status(self) -> str:
        """
        Check whether the isolated AirnavX bridge is reachable.
        """
        result = self._request(self._base())
        return self._clean(result)

    async def airnavx_fetch_path(self, path: str) -> str:
        """
        Fetch a specific AirnavX path and return readable text.
        Use this when the user provides or the model knows an AirnavX path.
        :param path: Relative path under /airnavx, such as search/text or a document route.
        """
        try:
            url = self._safe_url(path)
        except ValueError as exc:
            return str(exc)
        result = self._request(url)
        return self._clean(result)

    async def airnavx_inspect_path(self, path: str) -> str:
        """
        Inspect a specific AirnavX path and return raw page snippets plus discovered href, src, action, and endpoint-looking strings.
        Use this to discover the real AirnavX search API before tuning airnavx_search.
        :param path: Relative path under /airnavx, such as /, search/text, or a JavaScript bundle path.
        """
        try:
            url = self._safe_url(path)
        except ValueError as exc:
            return str(exc)
        result = self._request(url)
        return self._inspect(result)

    async def airnavx_keyword_context(self, path: str, keywords: str = "") -> str:
        """
        Return JavaScript or page snippets around AirnavX keywords, plus quoted strings and URL-like matches.
        Use this when airnavx_inspect_path finds only short keyword fragments.
        :param path: Relative path under /airnavx, such as scripts/app-7d9997849f.js.
        :param keywords: Optional comma-separated keyword list. Defaults to search, media, document, content, elastic, part, pn, task, Airnaveo.
        """
        try:
            url = self._safe_url(path)
        except ValueError as exc:
            return str(exc)
        terms = [part.strip() for part in (keywords or "").split(",") if part.strip()]
        result = self._request(url)
        return self._keyword_context(result, terms)

    async def airnavx_route_probe(self, query: str) -> str:
        """
        Probe known AirnavX search routes and API-looking paths with a query.
        Use this to identify which discovered route returns real manual results.
        :param query: Search text, for example hydraulic, bleed, tire, reset, or an ATA topic.
        """
        query = (query or "").strip()
        if not query:
            return "Give me search text first."

        encoded = quote_plus(query)
        paths = [
            "search?q=%s" % encoded,
            "search?wc=&q=%s" % encoded,
            "search?wc=FULLSEARCH&q=%s" % encoded,
            "search/text?q=%s" % encoded,
            "search/text?wc=FULLSEARCH&q=%s" % encoded,
            "searchDocument?q=%s" % encoded,
            "searchDocument?query=%s" % encoded,
            "searchExternalRevisionItemId?q=%s" % encoded,
            "searchIllustration?q=%s" % encoded,
            "extendedsearch?wc=FULLSEARCH&params=%s" % encoded,
            "/Airnaveo/media/searchByLinkedTasks?q=%s" % encoded,
            "/Airnaveo/media/searchByLinkedTasks?query=%s" % encoded,
        ]

        probes = []
        for path in paths:
            result = self._request(self._safe_url(path))
            probes.append(self._sample(result))

        post_url = self._safe_url("/Airnaveo/media/searchByLinkedTasks")
        for body in ({"q": query}, {"query": query}, {"search": query}, {"text": query}):
            result = self._request(post_url, method="POST", body=body)
            sample = self._sample(result)
            sample["post_body"] = body
            probes.append(sample)

        if not any(item.get("has_readable_result") for item in probes):
            return (
                "NO_READABLE_AIRNAVX_RESULTS for %r. The AirnavX bridge was reachable, "
                "but every probed search route returned an empty readable sample. "
                "Do not answer from general knowledge; ask Baylon to search/open the "
                "manual result in AirnavX or provide a specific AirnavX path.\n\n%s"
                % (query, json.dumps(probes, indent=2, ensure_ascii=False))
            )

        return json.dumps(probes, indent=2, ensure_ascii=False)

    async def airnavx_search(self, query: str) -> str:
        """
        Search AirnavX manual content for a keyword, ATA topic, part number, or phrase.
        Use this before answering aircraft manual questions. Do not tell the user how
        to search AirnavX when this tool is available; search AirnavX yourself, then
        answer only from readable manual/source text returned by the tool.
        Baylon's normal AirnavX context is customization AAY_CUSDOC. Treat aircraft
        numbers such as 305, 305NV, or N305NV as the MSN/TN/FSN context, usually 305NV.
        Good search strategy: search the system first, such as FMGC, then narrow with
        circuit breaker, CB, aircraft MSN/tail, document type, ATA, FIN, or exact phrase.
        A bridge status page, endpoint list, or "no obvious result" fallback is not a
        manual source and must not be cited as the answer.
        :param query: Search text, for example an ATA chapter, warning text, fault code, or part number.
        """
        query = (query or "").strip()
        if not query:
            return "Give me search text first."

        encoded = quote_plus(query)
        candidates = [
            "search?wc=FULLSEARCH&q=%s" % encoded,
            "search?q=%s" % encoded,
            "search/text?wc=FULLSEARCH&q=%s" % encoded,
            "search/text?q=%s" % encoded,
            "searchDocument?q=%s" % encoded,
            "searchDocument?query=%s" % encoded,
            "extendedsearch?wc=FULLSEARCH&params=%s" % encoded,
            "/Airnaveo/media/searchByLinkedTasks?q=%s" % encoded,
            "/Airnaveo/media/searchByLinkedTasks?query=%s" % encoded,
        ]

        outputs = []
        for path in candidates:
            url = self._safe_url(path)
            result = self._request(url)
            cleaned = self._clean(result)
            sample = self._sample(result)
            if result.get("ok") and sample.get("has_readable_result") and query.lower() in sample.get("sample", "").lower():
                return cleaned
            outputs.append(cleaned[:1200])

        return (
            "I reached the AirnavX bridge, but the first-pass search endpoints did not "
            "return an obvious result for %r. Try airnavx_fetch_path with a known "
            "AirnavX path, or open AirnavX once and give me the result path so I can "
            "lock onto the exact search endpoint.\n\nTried:\n- %s\n\nSample outputs:\n%s"
            % (query, "\n- ".join(candidates), "\n\n---\n\n".join(outputs[:2]))
        )
