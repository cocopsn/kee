"""Web tool — search and fetch.

`web_search` uses DuckDuckGo's instant-answer JSON API (no scraping, no key).
`fetch_url` does a plain HTTP GET and returns the body, truncated.

Both are read-only (risk 0). Phase 3 can swap the search backend for a
self-hosted SearXNG instance.
"""

from __future__ import annotations

from typing import Any

import httpx

from kee.tools.base import Tool


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web for a query. Returns the DuckDuckGo instant answer "
        "and related topics. Use for factual lookups; for in-depth content "
        "follow up with fetch_url on a specific result."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(self, query: str, max_results: int = 5) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query,
                    "format": "json",
                    "no_html": 1,
                    "skip_disambig": 1,
                },
            )
        try:
            data = r.json()
        except ValueError:
            return {"error": "Invalid JSON from DuckDuckGo", "status": r.status_code}

        results = []
        for topic in data.get("RelatedTopics", []):
            if "Text" in topic:
                results.append({
                    "title": topic.get("Text", "")[:200],
                    "url": topic.get("FirstURL", ""),
                })
            elif "Topics" in topic:  # category container
                for sub in topic.get("Topics", []):
                    if "Text" in sub:
                        results.append({
                            "title": sub.get("Text", "")[:200],
                            "url": sub.get("FirstURL", ""),
                        })
            if len(results) >= max_results:
                break

        return {
            "query": query,
            "abstract": data.get("AbstractText", ""),
            "abstract_source": data.get("AbstractSource", ""),
            "abstract_url": data.get("AbstractURL", ""),
            "answer": data.get("Answer", ""),
            "results": results[:max_results],
        }


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "HTTP GET a URL and return the response body. Use after web_search "
        "to read the actual content of a result. Truncated to 8000 chars."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "default": 8000},
        },
        "required": ["url"],
    }

    async def execute(self, url: str, max_chars: int = 8000) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            try:
                r = await client.get(url, headers={"User-Agent": "Kee/0.1 (sovereign agent)"})
            except httpx.HTTPError as e:
                return {"error": str(e), "url": url}

        body = r.text
        truncated = len(body) > max_chars
        return {
            "url": str(r.url),
            "status_code": r.status_code,
            "content_type": r.headers.get("content-type", ""),
            "body": body[:max_chars],
            "truncated": truncated,
        }


# Single module-level export — the registry only loads `tool`.
# We expose two tools by attaching them as a list and registering both.
tool = WebSearchTool()
fetch_tool = FetchUrlTool()
