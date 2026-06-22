"""Tool: research — one-shot search + scrape top result.

Halves the conversation depth for "what is X" / "explain Y" queries.
Composes the existing `web_search` and `fetch_url` tools internally so
the agent doesn't have to make two round-trips.

Returns: top 3 search results PLUS the full readable text of result #1.
The agent can use the snippets from #2 and #3 if it needs more breadth.

Risk: 0.
"""

from __future__ import annotations

import logging
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


class ResearchTool(Tool):
    name = "research"
    description = (
        "One-shot research: web search + scrape the top result's full "
        "readable text in a single call. Use instead of chaining "
        "web_search → fetch_url manually. Cuts answer latency in half "
        "for explainer-style queries."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_chars": {"type": "integer", "default": 2500,
                          "description": "Trim the scraped page to this many chars."},
        },
        "required": ["query"],
    }

    async def execute(self, query: str, max_chars: int = 2500) -> dict[str, Any]:
        # Step 1: search via existing tool
        try:
            from kee.tools.web import tool as web_tool
        except ImportError:
            return {"ok": False, "error": "web_search tool unavailable"}
        try:
            from kee.tools.web import tool as web_search_tool
            search_result = await web_search_tool.execute(query=query, max_results=3)
        except Exception as e:
            return {"ok": False, "error": f"search failed: {e}"}

        results = search_result.get("results") or []
        if not results:
            return {"ok": False, "error": "no search results", "query": query}

        # Step 2: fetch the top result's full text
        top = results[0]
        url = top.get("href") or top.get("url")
        page_text = ""
        page_error = None
        if url:
            try:
                from kee.tools.web import fetch_tool
                fetched = await fetch_tool.execute(url=url)
                page_text = (fetched.get("text") or fetched.get("content") or "")[:max_chars]
            except Exception as e:
                page_error = str(e)

        return {
            "ok": True,
            "query": query,
            "top_result": {
                "title": top.get("title"),
                "url": url,
                "snippet": top.get("body") or top.get("snippet"),
                "page_text": page_text,
                "page_error": page_error,
            },
            "additional_results": [
                {"title": r.get("title"), "url": r.get("href") or r.get("url"),
                 "snippet": r.get("body") or r.get("snippet")}
                for r in results[1:]
            ],
        }


tool = ResearchTool()
