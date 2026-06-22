"""Router — classify each user turn into a tier and route to the right LLM.

Pipeline:
  1. Try DIRECT_ANSWERS regex match → respond from template, NO LLM call.
  2. If no match, run a tiny local model (Qwen3.5-0.8B) with a few-shot
     prompt to classify into {simple, medium, heavy}.
  3. Apply TIER_HINTS overrides from router.md.
  4. If kill switch active, downgrade medium/heavy → simple (forced local).
  5. Return RouterDecision(tier, provider_target, tool_hint, direct_reply?).

The router is the FIRST thing every user message touches in `agent.process()`.
It runs in <300ms (Qwen3.5:0.8b ~120ms + parsing). Cost: $0.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kee.config import settings
from kee.core.llm.cost_tracker import kill_switch_active
from kee.core.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


@dataclass
class RouterDecision:
    tier: str               # 'direct' | 'simple' | 'medium' | 'heavy'
    provider_target: str    # 'router' | 'ollama' | 'claude' | 'openai'
    direct_reply: str | None = None
    tool_hint: str | None = None
    reason: str = ""


# Tier → provider mapping (kill switch can override).
# Coco's strategy (2026-05-03):
#   - direct         → router.md template (instant, $0)
#   - simple         → configured local Ollama model (factual single-tool, $0)
#   - conversational → gpt-4o-mini (casual chat, cheap & fast)
#   - medium         → haiku (needs context / multi-step, mid quality)
#   - heavy          → sonnet (planning, long-form, strategy)
# The escalation ladder for chat: gpt-4o-mini → haiku → sonnet, in that
# order of cost/depth. Conversational is the new default for "chat-like"
# input. Qwen acts as co-model fallback for any cloud failure.
_TIER_PROVIDER = {
    "direct":         "router",
    "simple":         "ollama",   # local configured model, free
    "conversational": "openai",   # gpt-4o-mini, $0.15/$0.60 per Mtok
    "medium":         "haiku",    # claude-haiku-4-5, $0.80/$4
    "heavy":          "claude",   # claude-sonnet-4-6, $3/$15
}


class Router:
    def __init__(
        self,
        config_path: Path | None = None,
        router_model: str = "llama3.2:1b",
    ) -> None:
        self.config_path = config_path or (settings.vault_dir / "config" / "router.md")
        self.router_model = router_model
        self._client = OllamaClient(model=router_model, num_ctx=2048, temperature=0.0)
        self._direct_rules: list[tuple[re.Pattern, str]] = []
        self._tier_hints: dict[str, list[re.Pattern]] = {"simple": [], "medium": [], "heavy": []}
        self._load_config()

    def _load_config(self) -> None:
        """Parse the YAML blocks inside router.md.
        Re-readable on every classify() call so live edits propagate.
        """
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("router.md not found at %s", self.config_path)
            return

        # Pull out the two yaml blocks by section header
        direct_block = self._extract_yaml_after(text, "## DIRECT_ANSWERS")
        hints_block  = self._extract_yaml_after(text, "## TIER_HINTS")

        self._direct_rules = []
        for entry in self._parse_yaml_list(direct_block):
            pat = entry.get("match")
            rep = entry.get("reply")
            if pat and rep:
                try:
                    self._direct_rules.append((re.compile(pat, re.IGNORECASE), rep))
                except re.error as e:
                    logger.warning("router.md: bad regex %r — %s", pat, e)

        self._tier_hints = {"simple": [], "conversational": [], "medium": [], "heavy": []}
        hints_dict = self._parse_yaml_dict(hints_block)
        for tier in ("simple", "conversational", "medium", "heavy"):
            for pat in hints_dict.get(tier, []) or []:
                try:
                    self._tier_hints[tier].append(re.compile(pat, re.IGNORECASE))
                except re.error as e:
                    logger.warning("router.md tier_hints[%s]: bad regex %r — %s", tier, pat, e)

    # ── Tiny YAML-ish parser (we don't want a yaml dep just for this) ────
    @staticmethod
    def _extract_yaml_after(text: str, marker: str) -> str:
        idx = text.find(marker)
        if idx < 0:
            return ""
        rest = text[idx + len(marker):]
        # Find first ```yaml … ``` block
        m = re.search(r"```ya?ml\s*\n(.*?)```", rest, re.DOTALL)
        return m.group(1) if m else ""

    @staticmethod
    def _parse_yaml_list(text: str) -> list[dict[str, str]]:
        """Parse `- match: '...'` / `  reply: '...'` pairs."""
        entries: list[dict[str, str]] = []
        cur: dict[str, str] | None = None
        for ln in text.splitlines():
            stripped = ln.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- "):
                if cur:
                    entries.append(cur)
                cur = {}
                stripped = stripped[2:].strip()
            if cur is None:
                cur = {}
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                v = v.strip()
                if v.startswith(("'", '"')) and v.endswith(("'", '"')) and len(v) >= 2:
                    v = v[1:-1]
                cur[k.strip()] = v
        if cur:
            entries.append(cur)
        return entries

    @staticmethod
    def _parse_yaml_dict(text: str) -> dict[str, list[str]]:
        """Parse `key:` followed by `- 'pattern'` lines."""
        out: dict[str, list[str]] = {}
        cur_key: str | None = None
        for ln in text.splitlines():
            raw = ln.rstrip()
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not raw.startswith(" ") and stripped.endswith(":"):
                cur_key = stripped[:-1].strip()
                out[cur_key] = []
                continue
            if cur_key and stripped.startswith("- "):
                v = stripped[2:].strip()
                if v.startswith(("'", '"')) and v.endswith(("'", '"')) and len(v) >= 2:
                    v = v[1:-1]
                out[cur_key].append(v)
        return out

    # ── Direct match shortcut ────────────────────────────────────────────
    def _try_direct(self, user_text: str) -> str | None:
        for pat, template in self._direct_rules:
            if pat.match(user_text):
                now = datetime.now()
                return (template
                        .replace("{time}", now.strftime("%H:%M"))
                        .replace("{date}", now.strftime("%Y-%m-%d"))
                        .replace("{day}", now.strftime("%A"))
                        .replace("{user}", "Coco"))
        return None

    # ── Tier hint pre-classification ─────────────────────────────────────
    def _hint_tier(self, user_text: str) -> str | None:
        # Heavy beats medium beats conversational beats simple (most
        # expensive wins to be safe). Conversational sits between simple
        # and medium since it's also cloud-paid but lighter than haiku.
        for tier in ("heavy", "medium", "conversational", "simple"):
            for pat in self._tier_hints.get(tier, []):
                if pat.search(user_text):
                    return tier
        return None

    # ── LLM classifier (few-shot) ────────────────────────────────────────
    _CLASSIFIER_SYS = (
        "You are a message classifier. Output ONLY a JSON object — no prose.\n"
        "Tiers:\n"
        "  - 'simple'         : single fact lookup or one tool call "
        "(time, email count, today's events, file list)\n"
        "  - 'conversational' : casual chat, opinion, small talk, jokes — no project context required "
        "(qué tal, qué piensas, hablemos de X, cuéntame algo, qué onda)\n"
        "  - 'medium'         : needs project context, status, multi-step reasoning, code review "
        "(cómo va X, busca en archivos y dime, compara A y B, encuentra el bug)\n"
        "  - 'heavy'          : long-form planning, strategy, architecture, reports "
        "(haz un plan, redacta, diseña, estrategia para)\n"
        "\n"
        "Tool hints: calendar | gmail | files | shell | web | none\n"
        "Output schema: {\"tier\":\"simple|conversational|medium|heavy\","
        "\"tool_hint\":\"calendar|gmail|files|shell|web|none\"}\n"
        "Examples:\n"
        "  'cuántos correos'        → {\"tier\":\"simple\",\"tool_hint\":\"gmail\"}\n"
        "  'qué tengo hoy'          → {\"tier\":\"simple\",\"tool_hint\":\"calendar\"}\n"
        "  'qué tal estás'          → {\"tier\":\"conversational\",\"tool_hint\":\"none\"}\n"
        "  'qué piensas del Senado' → {\"tier\":\"conversational\",\"tool_hint\":\"none\"}\n"
        "  'cuéntame un chiste'     → {\"tier\":\"conversational\",\"tool_hint\":\"none\"}\n"
        "  'cómo va auctorum'       → {\"tier\":\"medium\",\"tool_hint\":\"none\"}\n"
        "  'busca en archivos'      → {\"tier\":\"medium\",\"tool_hint\":\"files\"}\n"
        "  'haz un plan para AEGIS' → {\"tier\":\"heavy\",\"tool_hint\":\"none\"}\n"
        "  'redacta un email'       → {\"tier\":\"heavy\",\"tool_hint\":\"none\"}\n"
    )

    _VALID_HINTS = {"calendar", "gmail", "files", "shell", "web", "none"}

    async def _classify(self, user_text: str) -> tuple[str, str | None]:
        try:
            resp = await asyncio.wait_for(
                self._client.chat(
                    messages=[
                        {"role": "system", "content": self._CLASSIFIER_SYS},
                        {"role": "user", "content": f"Input: {user_text!r}\nOutput:"},
                    ],
                    tools=None,
                    temperature=0.0,
                ),
                timeout=6.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            # Default to 'conversational' (gpt-4o-mini) when classifier
            # fails — cheaper than medium=haiku and good enough for the
            # vast majority of casual chat. Heavy tasks should be tagged
            # explicitly via tier_hints in router.md.
            logger.debug("Router LLM classify failed (%s) — defaulting to conversational", e)
            return ("conversational", None)

        raw = (resp.content or "").strip()
        m = re.search(r"\{[^{}]*\}", raw)
        if not m:
            return ("conversational", None)
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return ("conversational", None)
        tier = obj.get("tier", "conversational")
        if tier not in ("simple", "conversational", "medium", "heavy"):
            tier = "conversational"
        hint = obj.get("tool_hint")
        if hint not in self._VALID_HINTS or hint == "none":
            hint = None
        return (tier, hint)

    # ── Public ───────────────────────────────────────────────────────────
    async def route(self, user_text: str, source: str = "terminal") -> RouterDecision:
        # Re-read the md every call so live edits work
        self._load_config()
        user_text = (user_text or "").strip()
        if not user_text:
            return RouterDecision("simple", "ollama", reason="empty input")

        # 1. Direct answer shortcut
        direct = self._try_direct(user_text)
        if direct is not None:
            return RouterDecision(
                tier="direct",
                provider_target="router",
                direct_reply=direct,
                reason="direct match in router.md",
            )

        # 2. Tier hint from regex (cheap, deterministic)
        hint_tier = self._hint_tier(user_text)

        # Local-first mode: when Kee's primary provider is Ollama, avoid the
        # auxiliary Llama classifier entirely. This keeps chat on the configured
        # local model and removes the router-model dependency from every turn.
        primary = os.environ.get("KEE_LLM_PRIMARY", "ollama").strip().lower()
        if primary == "ollama":
            return RouterDecision(
                tier=hint_tier or "conversational",
                provider_target="ollama",
                tool_hint=None,
                reason="local primary=ollama; classifier skipped"
                + (f" hint={hint_tier}" if hint_tier else ""),
            )

        # 3. LLM classification
        clf_tier, tool_hint = await self._classify(user_text)
        # Combine: hint overrides classifier ONLY IF hint is more expensive
        # (so 'haz un plan' from hints wins over a too-cautious 'conversational')
        order = {"simple": 0, "conversational": 1, "medium": 2, "heavy": 3}
        clf_rank = order.get(clf_tier, 1)
        if hint_tier and order.get(hint_tier, 0) > clf_rank:
            tier = hint_tier
            reason = f"hint-override (clf={clf_tier}, hint={hint_tier})"
        else:
            tier = clf_tier
            reason = f"classifier={clf_tier}" + (f" hint={hint_tier}" if hint_tier else "")

        # 4. Apply kill switch — anything paid downgrades to local Ollama
        provider = _TIER_PROVIDER[tier]
        if provider in ("claude", "haiku", "openai") and kill_switch_active():
            provider = "ollama"
            reason += " | kill_switch_active → ollama"

        return RouterDecision(
            tier=tier,
            provider_target=provider,
            tool_hint=tool_hint,
            reason=reason,
        )
