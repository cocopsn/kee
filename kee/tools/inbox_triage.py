"""Tool: inbox_triage — heuristic Gmail unread classification.

Pulls unread INBOX threads via the existing Gmail tool, then assigns each
to a category (billing, work, personal, marketing, urgent) using sender-
domain + subject-keyword heuristics. Zero LLM cost; the agent can ask
about a specific bucket ("¿llegó algo de Vercel hoy?") without
re-classifying every time.

Risk: 0 — read-only over Gmail.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


# Domain / subject heuristics. Order matters: first match wins.
_RULES: list[tuple[str, re.Pattern, re.Pattern | None]] = [
    # name, sender-domain regex (optional), subject regex (optional)
    ("urgent",        re.compile(r"."),
     re.compile(r"\b(urgent|asap|action required|inmediato|urgente|"
                r"hoy|hoy mismo|deadline|expir(?:a|ó|ed))\b", re.I)),
    # Note: GitHub is its own bucket below (lots of notifications). Don't
    # capture it here even though it sometimes sends invoices.
    ("billing",       re.compile(
        r"@(vercel|stripe|paypal|aws|openai|anthropic|cursor|"
        r"netlify|cloudflare|digitalocean|railway|hetzner|linode|"
        r"namecheap|godaddy|gsuite|workspace\.google)\.com",
        re.I), None),
    ("auth_codes",    re.compile(r"."),
     re.compile(r"\b(verification|verify|otp|código de verificación|"
                r"two[- ]?factor|2fa|sign[- ]?in)\b", re.I)),
    ("calendar",      re.compile(
        r"@(calendar-noreply\.google|calendly|cal\.com)\.",
        re.I), None),
    ("github",        re.compile(r"@github\.com", re.I), None),
    ("work",          re.compile(
        r"@(auctorum|grupojavier|nahual|netprobe)\.", re.I), None),
    ("marketing",     re.compile(
        r"@(mailchimp|substack|hubspot|sendgrid|mailgun|mailerlite|"
        r"mailjet|customer\.io|braze|klaviyo|drip)\.", re.I), None),
    ("social",        re.compile(
        r"@(linkedin|twitter|x|facebook|instagram|discord|telegram|"
        r"whatsapp|reddit|hackernews|stackoverflow)\.", re.I), None),
    ("school",        re.compile(r"@(itcsaltillo|tecmilenio|udem)\.", re.I),
     None),
    ("noreply",       re.compile(r"no[- ._]?reply|donotreply", re.I), None),
]


def _extract_domain(from_header: str | None) -> str:
    if not from_header:
        return ""
    # "Name <user@domain.com>" or just "user@domain.com"
    m = re.search(r"@[\w.\-]+", from_header)
    return m.group(0).lower() if m else ""


def _classify(thread: dict) -> str:
    sender = thread.get("from") or ""
    subject = thread.get("subject") or ""
    domain = _extract_domain(sender)
    for name, sender_rx, subject_rx in _RULES:
        if sender_rx and sender_rx.search(sender + " " + domain):
            if subject_rx is None or subject_rx.search(subject):
                return name
    return "other"


class InboxTriageTool(Tool):
    name = "inbox_triage"
    description = (
        "Categoriza el inbox de Gmail (no leído) por heurística — sin LLM, "
        "sin costo. Buckets: urgent, billing, auth_codes, calendar, github, "
        "work, marketing, social, school, noreply, other. Útil cuando Coco "
        "pregunta '¿algo importante en el correo?' o cuando el agente debe "
        "decidir si interrumpirlo. Devuelve `{by_category, count, samples}` "
        "con muestras del subject + sender por bucket. Wraps `gmail` tool."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer", "default": 30,
                "description": "How many unread threads to pull from Gmail "
                               "before classifying.",
            },
            "category": {
                "type": "string",
                "description": "If provided, return only threads in this "
                               "bucket.",
            },
        },
    }

    async def execute(
        self,
        max_results: int = 30,
        category: str | None = None,
    ) -> dict[str, Any]:
        from kee.tools.gmail_tool import tool as gmail
        # Pull unread threads via existing Gmail action — no auth here.
        resp = await gmail.execute(
            action="search", query="is:unread in:inbox",
            max_results=int(max_results),
        )
        if resp.get("status") == "auth_required":
            return {"ok": False, "error": "Gmail auth required",
                    "hint": "run `python -m kee.main google-auth` first"}
        if resp.get("error"):
            return {"ok": False, "error": resp["error"]}
        threads = resp.get("threads") or []
        # Older gmail.search returned `results`; normalise.
        if not threads and isinstance(resp.get("results"), list):
            threads = resp["results"]

        bucketed: dict[str, list[dict]] = {}
        for t in threads:
            cat = _classify(t)
            bucketed.setdefault(cat, []).append({
                "subject": (t.get("subject") or "")[:120],
                "from": (t.get("from") or "")[:80],
                "thread_id": t.get("thread_id"),
                "snippet": (t.get("snippet") or "")[:180],
            })

        by_category = {
            cat: {"count": len(items),
                  "samples": items[:3]}  # cap per-bucket samples
            for cat, items in sorted(bucketed.items(),
                                     key=lambda kv: len(kv[1]),
                                     reverse=True)
        }
        out: dict[str, Any] = {
            "ok": True,
            "scanned": len(threads),
            "by_category": by_category,
            "totals": dict(Counter({cat: v["count"]
                                    for cat, v in by_category.items()})),
        }
        if category:
            out["filtered"] = category
            out["matches"] = bucketed.get(category, [])
            out["count"] = len(out["matches"])
        return out


tool = InboxTriageTool()
