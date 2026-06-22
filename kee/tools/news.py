"""Tool: news — top news headlines via DuckDuckGo News.

Free, no API key. Uses the same `duckduckgo-search` package the
existing `web_search` tool uses, but hits the news endpoint specifically
so we get headlines + sources + timestamps instead of generic web links.

Risk: 0.
"""

from __future__ import annotations

from typing import Any

from kee.tools.base import Tool


class NewsTool(Tool):
    name = "news"
    description = (
        "Top news headlines for a topic via DuckDuckGo News. Returns "
        "title, source, timestamp, snippet for the top N results. Free, "
        "no API key needed. Use for daily briefings (sleep_cycle), "
        "user queries about current events, market sentiment context."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Topic / keyword(s)"},
            "max_results": {"type": "integer", "default": 6},
            "region": {"type": "string", "default": "mx-es",
                       "description": "Region code (mx-es / us-en / es-es / wt-wt)"},
            "timelimit": {"type": "string", "default": "d",
                          "enum": ["d", "w", "m"],
                          "description": "d=last day, w=last week, m=last month"},
        },
        "required": ["query"],
    }

    async def execute(
        self,
        query: str,
        max_results: int = 6,
        region: str = "mx-es",
        timelimit: str = "d",
    ) -> dict[str, Any]:
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                return {"ok": False, "error": "duckduckgo-search not installed"}

        try:
            with DDGS() as ddgs:
                # ddgs has a `news` method
                results = list(ddgs.news(
                    query, region=region, timelimit=timelimit,
                    max_results=max_results,
                ))
            slim = [
                {
                    "title": r.get("title"),
                    "source": r.get("source"),
                    "date": r.get("date"),
                    "url": r.get("url"),
                    "snippet": (r.get("body") or "")[:280],
                }
                for r in results
            ]
            return {"ok": True, "query": query, "count": len(slim), "items": slim}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


tool = NewsTool()
