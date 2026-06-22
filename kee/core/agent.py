"""Kee agent core — ReAct loop with native Ollama tool calling.

The flow per user input:
  1. Build state (conversation row in SQLite).
  2. Assemble system prompt: identity + soul + user + dynamic capabilities.
  3. Optional semantic memory retrieval (Phase 3 ChromaDB; stubbed earlier).
  4. Reasoning loop:
        response = llm.chat(messages, tools=registry.get_schemas())
        if response.tool_calls:
            for each call:
                pre_state = capture_state(...)
                result = registry.execute(...)
                post_state = capture_state(...)
                verification = verify(...)
                audit.log_action(..., pre_state, post_state, verification)
                if not verification.ok:
                    audit.log_anomaly(...)
            continue
        else:
            persist + audit + return text

Concurrency-safe: every LLM call goes through `scheduler.llm_call()`. Phase 0
has only one consumer (the terminal) so the lock is never contended, but the
plumbing is in place for Phase 2-3 when voice + heartbeat join in.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from kee.config import settings
from kee.core import services
from kee.core.audit import AuditLogger
from kee.core.identity import IdentityLoader
from kee.core.memory import ConversationState, MemoryManager
from kee.core.ollama_client import ChatResponse, OllamaClient, OllamaUnavailable
from kee.core.scheduler import KeeScheduler, get_default
from kee.core.tool_registry import ToolRegistry
from kee.core.verify import capture_state, serialize_state, verify

logger = logging.getLogger(__name__)


# Patterns that indicate the model is *narrating* a tool call as plain text
# instead of actually invoking it (a known failure mode of abliterated tool
# models when the request is casual). Detected only when zero tools fired in
# the current turn — the same wording is legitimate AFTER a real call.
_FAKE_CALL_NAME_RE = re.compile(
    r"(?im)^\s*(?:tool|tool_name|calling|name)\s*[:=]\s*[\"'`]?([a-z][a-z0-9_]+)[\"'`]?",
)
_FAKE_RESULT_LINE_RE = re.compile(
    r"(?im)^\s*(?:result|files\s+created|workdir|workspace|exit\s*code)\s*[:=]",
)


def _looks_like_fake_tool_call(
    text: str,
    available_tools: set[str],
) -> str | None:
    """Return the fake tool name (or '<unknown>') if `text` reads like a
    tool-call-as-narration. Returns None when the text seems normal."""
    for m in _FAKE_CALL_NAME_RE.finditer(text):
        name = m.group(1).strip()
        if name in available_tools:
            return name
    if _FAKE_RESULT_LINE_RE.search(text):
        return "<unknown>"
    return None


# Trailing "do you want me to / ¿te gustaría?" offers. The model loves to
# tack these on. Two flavours:
#   - Paragraph offer: comes after \n\n
#   - Inline offer: comes after a separator like " - ", " — ", " · ", or
#     simply at the start of the last sentence ("This suggests a backlog -
#     would you like me to help prioritize?")
# The opener phrase + everything to end-of-string is removed.
_OFFER_OPENER = (
    r"(?: te\s+gustar[ií]a"
    r"  | quieres\s+que"
    r"  | quieres\s+(?:profundizar|saber|que\s+te|m[aá]s\s+detalle)"
    r"  | necesitas\s+que"
    r"  | te\s+ayudo"
    r"  | (?:puedo|podr[ií]a)\s+ayudar"
    r"  | (?:d[ií]me|av[ií]same)\s+si"
    r"  | (?:hay|existe)\s+algo\s+(?:espec[ií]fico|m[aá]s|particular)"
    r"  | (?:en\s+qu[eé]|c[oó]mo)\s+(?:te|m[aá]s)\s+puedo\s+ayudar"
    r"  | qu[eé]\s+acci[oó]n\s+(?:le|te)\s+gustar[ií]a"
    r"  | would\s+you\s+like"
    r"  | want\s+me\s+to"
    r"  | do\s+you\s+want"
    r"  | shall\s+i"
    r"  | should\s+i"
    r"  | let\s+me\s+know"
    r"  | if\s+you\s+(?:want|need|'?d\s+like|wish)"
    r"  | feel\s+free\s+to"
    r"  | is\s+there\s+anything"
    r"  | how\s+can\s+i\s+(?:help|assist)"
    r"  | how\s+may\s+i\s+(?:help|assist)"
    r"  | anything\s+(?:else|specific)"
    r")"
)

# Locate any offer opener anywhere in the text.
_OFFER_OPENER_RE = re.compile(rf"(?ix)(?:¿|\?)?\s*{_OFFER_OPENER}")


def _strip_followup_offers(text: str) -> str:
    """Remove trailing follow-up offers from a Telegram/voice reply.

    Strategy: walk through opener matches earliest-first. For each one,
    check if the text immediately preceding it ends in a sentence (.!?…)
    OR has a legitimate separator (newline/dash/em-dash/bullet/comma).
    The first opener that satisfies that wins — anything from that
    position onward is the offer block, cut it.
    Earliest-first is important because the model often chains openers
    ('Let me know if you want…' or 'Want me to do X, would you like Y?'):
    we want to cut at the start of the chain, not the middle.
    """
    if not text:
        return text
    matches = list(_OFFER_OPENER_RE.finditer(text))
    if not matches:
        return text
    for m in matches:
        pre_full = text[: m.start()]
        # Walk back past trailing whitespace + soft separators
        p = pre_full.rstrip(" \t\r\n-—–·,;:")
        if len(p) < 8:
            continue  # too little substantive content before this opener
        ends_sentence = p[-1] in ".!?…"
        had_separator = len(pre_full.rstrip()) != len(p) or pre_full.rstrip(" \t").endswith("\n")
        if not (ends_sentence or had_separator):
            # The opener phrase appears mid-sentence without a clean
            # separator — probably part of substantive content, not an
            # offer. Skip and keep looking for a later opener that does
            # have a clean cut point.
            continue
        if not ends_sentence:
            p += "."
        return p.strip()
    return text


# ── Tool-call salvage ────────────────────────────────────────────────────
# The abliterated Qwen3.5 9B sometimes loses its native tool-emit channel
# and writes the call as JSON text instead. We parse it back into real
# dispatch so the user isn't blocked by a model defect.

# Pulls the body out of fenced code blocks (```json ... ``` or ``` ... ```).
_CODE_BLOCK_RE = re.compile(r"```(?:json|tool)?\s*\n?([\s\S]+?)\n?```", re.IGNORECASE)
# Fallback: bare top-level JSON object (very loose; we still json.loads it).
_BARE_JSON_RE = re.compile(r"(\{(?:[^{}]|\{[^{}]*\})*\})", re.DOTALL)
# Last-resort: line shaped like `tool: NAME [args: {...}]`. Catches the
# YAML/JS-like forms abliterated Qwen3.5 emits when its tool-emit channel
# is broken (e.g. `tool: goals args: { query: "all" }`).
_INLINE_CALL_RE = re.compile(
    r"(?im)^\s*(?:tool|tool_name|calling|name)\s*[:=]\s*[\"'`]?([a-z][a-z0-9_]+)[\"'`]?"
    r"(?:[\s,]+(?:args?|arguments|parameters|params)\s*[:=]\s*(\{[^\n]*\}))?",
)


def _coerce_loose_json(text: str) -> str:
    """Best-effort fix-up for JS-like object literals the model emits.

    Quotes bare keys, converts single quotes to double quotes — enough for
    `json.loads` to swallow common patterns like `{ query: "all" }`.
    """
    s = text.strip()
    # Quote bare keys: { foo: ... }  →  { "foo": ... }
    s = re.sub(r'(?P<lead>[{,\s])(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:', r'\g<lead>"\g<key>":', s)
    # Single → double quotes for values, when not embedded in double-quoted strings.
    if '"' not in s.replace("\\\"", ""):
        s = s.replace("'", '"')
    return s


def _extract_json_candidates(text: str) -> list[str]:
    """Yield JSON-looking substrings from the model's text response."""
    out: list[str] = []
    for m in _CODE_BLOCK_RE.finditer(text):
        out.append(m.group(1).strip())
    if not out:
        for m in _BARE_JSON_RE.finditer(text):
            out.append(m.group(1).strip())
    return out


_NAME_KEYS = ("name", "tool", "tool_name")
_ARGS_KEYS = ("arguments", "args", "parameters", "params", "input")


def _normalize_text_tool_call(
    candidate: dict[str, Any],
    available_tools: set[str],
) -> tuple[str, dict[str, Any]] | None:
    """If `candidate` looks like a tool call, return (name, args). Else None.

    Accepts shapes the model has been observed to emit:
      {"name":"X", "arguments":{...}}
      {"tool":"X", "args":{...}}
      {"tool":"X", "path":"...", "action":"..."}     ← flat
    """
    if not isinstance(candidate, dict):
        return None

    # Find the name.
    name = None
    for k in _NAME_KEYS:
        v = candidate.get(k)
        if isinstance(v, str) and v in available_tools:
            name = v
            break
    if name is None:
        return None

    # Find the args.
    for k in _ARGS_KEYS:
        v = candidate.get(k)
        if isinstance(v, dict):
            return name, v

    # Flat shape: every key except the name field IS an arg.
    flat = {k: v for k, v in candidate.items() if k not in _NAME_KEYS}
    return name, flat


def salvage_tool_calls(
    text: str,
    available_tools: set[str],
) -> list[tuple[str, dict[str, Any]]]:
    """Find and parse any text-form tool calls in `text`.

    Tries three strategies in order:
      1. Strict JSON inside ```fences``` or bare {…}.
      2. Same blocks coerced through `_coerce_loose_json` (quotes bare keys,
         flips single→double quotes) — handles JS-like literals.
      3. Inline `tool: NAME args: {...}` lines — abliterated Qwen3.5's
         signature failure mode where it emits a YAML-ish call.

    Returns (tool_name, arguments) tuples. Quietly ignores unparseable input.
    """
    out: list[tuple[str, dict[str, Any]]] = []

    for raw in _extract_json_candidates(text):
        # Strategy 1: strict.
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Strategy 2: loose (best-effort coerce).
            try:
                parsed = json.loads(_coerce_loose_json(raw))
            except json.JSONDecodeError:
                continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            normalized = _normalize_text_tool_call(item, available_tools)
            if normalized is not None:
                out.append(normalized)

    if out:
        return out

    # Strategy 3: inline `tool: NAME args: {...}`.
    for m in _INLINE_CALL_RE.finditer(text):
        name = m.group(1).strip()
        if name not in available_tools:
            continue
        raw_args = m.group(2)
        args: dict[str, Any] = {}
        if raw_args:
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                try:
                    args = json.loads(_coerce_loose_json(raw_args))
                except json.JSONDecodeError:
                    args = {}
        out.append((name, args))

    return out


class KeeAgent:
    def __init__(
        self,
        llm: OllamaClient | None = None,
        registry: ToolRegistry | None = None,
        memory: MemoryManager | None = None,
        audit: AuditLogger | None = None,
        identity: IdentityLoader | None = None,
        scheduler: KeeScheduler | None = None,
        chain: Any = None,
        router: Any = None,
    ):
        self.scheduler = scheduler or get_default()
        # Legacy direct Ollama client kept for code paths that still touch
        # `self.llm` (health checks, status surfaces). The reasoning loop
        # now goes through `self.chain` instead.
        self.llm = llm or OllamaClient(scheduler=self.scheduler)
        # Multi-provider chain (Claude → OpenAI → Ollama, by default).
        # Built lazily so we don't pay the API import cost in test fixtures.
        if chain is not None:
            self.chain = chain
        else:
            from kee.core.llm.chain import build_default_chain
            self.chain = build_default_chain()
        # Router classifies each user turn into a tier and chooses provider
        if router is not None:
            self.router = router
        else:
            from kee.core.router import Router
            self.router = Router()
        self.registry = registry or ToolRegistry()
        self.memory = memory or MemoryManager()
        self.audit = audit or AuditLogger()
        self.identity = identity or IdentityLoader()

        services.bind(self.memory, self.audit, self.registry)

    # ── Bootstrap ─────────────────────────────────────────────────────────
    def bootstrap(self) -> None:
        if not self.registry.tools:
            self.registry.load_builtins()
            self.registry.load_custom()
            logger.info("Bootstrapped registry with %d tools", len(self.registry.tools))

    # ── Public entry point ────────────────────────────────────────────────
    async def process(
        self,
        user_input: str,
        source: str = "terminal",
        state: ConversationState | None = None,
    ) -> tuple[str, ConversationState]:
        """Process a user turn.

        If `state` is None, starts a new conversation. Otherwise appends to
        the existing one — this is how surfaces (terminal, voice, telegram)
        get continuity across turns within a single session.

        Returns (assistant_response_text, updated_state).
        """
        self.bootstrap()

        first_turn = state is None
        if first_turn:
            state = self.memory.start_conversation(source=source)

        # Reset the per-turn iteration budget. Without this, once a previous
        # turn hit max_iterations, every subsequent turn would short-circuit
        # to "reasoning limit" before even reaching the LLM.
        state.iteration = 0

        capabilities = self._build_capabilities_block()
        system_prompt = self.identity.build_system_prompt(
            capabilities=capabilities, source=source,
        )
        memory_context = await self.memory.retrieve(user_input, top_k=5)

        if first_turn:
            # Seed with system messages on the very first turn.
            state.messages = [{"role": "system", "content": system_prompt}]
            if memory_context:
                state.messages.append({"role": "system", "content": memory_context})
            # Cross-conversation context: pull summaries of recent past
            # conversations so a new chat picks up where things left off,
            # regardless of which surface the past chat was on.
            try:
                cc = self.memory.cross_conversation_context(
                    exclude_id=state.id, limit=5,
                )
                if cc:
                    state.messages.append({"role": "system", "content": cc})
            except Exception as e:
                logger.debug("cross_conversation_context failed: %s", e)
            # Attached files: tell the model they exist + how to read them.
            if state.attached_files:
                lines = ["## Archivos adjuntos por Coco en esta sesión"]
                for p in state.attached_files:
                    lines.append(f"- `{p}`")
                lines.append(
                    "\nLee con `files action=read path=<ruta>` cuando sea "
                    "relevante. No leas todos preventivamente."
                )
                state.messages.append({"role": "system", "content": "\n".join(lines)})
        else:
            # Refresh the system prompt in place so identity edits propagate
            # without losing the conversation. The first message is always
            # the system prompt by construction.
            if state.messages and state.messages[0].get("role") == "system":
                state.messages[0] = {"role": "system", "content": system_prompt}
            else:
                state.messages.insert(0, {"role": "system", "content": system_prompt})
            # Drop the old per-turn memory context (second system message)
            # and reinsert a fresh one if any.
            if (
                len(state.messages) > 1
                and state.messages[1].get("role") == "system"
                and state.messages[1].get("content", "").startswith("## Relevant memory")
            ):
                state.messages.pop(1)
            if memory_context:
                state.messages.insert(1, {"role": "system", "content": memory_context})

        state.messages.append({"role": "user", "content": user_input})
        self.memory.store_message(state.id, "user", user_input)

        # ── Router: classify and decide which provider handles this turn ──
        try:
            decision = await self.router.route(user_input, source=source)
        except Exception as e:
            logger.warning("Router failed (%s) — defaulting to medium/claude", e)
            from kee.core.router import RouterDecision
            decision = RouterDecision(tier="medium", provider_target="claude",
                                       reason=f"router-error: {e}")
        state.router_decision = decision  # for surface UIs / tests
        logger.info(
            "Router → tier=%s provider=%s tool_hint=%s reason=%s",
            decision.tier, decision.provider_target, decision.tool_hint,
            decision.reason,
        )

        # Direct-answer fast path: respond from template, no LLM call.
        if decision.tier == "direct" and decision.direct_reply is not None:
            final = decision.direct_reply
            state.messages.append({"role": "assistant", "content": final})
            self.memory.store_message(state.id, "assistant", final)
            self.audit.log_llm_call(
                conversation_id=state.id,
                provider="router", model_name="router.md",
                tier="direct", latency_ms=0,
                tokens_in=0, tokens_out=len(final) // 4, cost_usd=0.0,
            )
            self.audit.log_response(state.id, final)
            return final, state

        # If router suggested a tool_hint, append it to the latest user
        # message as a low-key nudge (the LLM may or may not follow it).
        if decision.tool_hint and decision.tool_hint != "none":
            nudge = (f"\n\n[router hint: this likely needs the `{decision.tool_hint}` "
                     f"tool — call it directly]")
            # Append to the just-added user message
            state.messages[-1]["content"] = state.messages[-1]["content"] + nudge

        try:
            final = await self._reason(state, decision=decision)
        except OllamaUnavailable as e:
            err = f"⚠ Ollama is not available: {e}"
            self.memory.store_message(state.id, "system", err)
            return err, state

        # Surface-specific post-processing.
        if source in ("telegram", "voice"):
            final = _strip_followup_offers(final)

        # ── QA verify-and-retry (Jarvis pattern) ──────────────────────
        # Voice replies get one regenerate attempt if the QA score is low.
        # Cost: one extra LLM call max, only when quality really tanks.
        # Skip for chat (visual surface — markdown is fine).
        if source == "voice":
            try:
                from kee.cognition.response_qa import check as _qa_check
                from kee.cognition.response_qa import summary_for_retry
                v = _qa_check(final, source="voice", expected_lang="es")
                if v.score < 0.6 and not getattr(state, "_qa_retried", False):
                    logger.warning(
                        "voice reply QA score=%.2f issues=%s — regenerating",
                        v.score, v.issues,
                    )
                    state._qa_retried = True   # bump so we only retry once
                    state.messages.append({
                        "role": "system",
                        "content": summary_for_retry(v),
                    })
                    try:
                        final2 = await self._reason(state, decision=None)
                        if final2 and final2.strip():
                            v2 = _qa_check(final2, source="voice", expected_lang="es")
                            if v2.score >= v.score:
                                final = final2
                                logger.info(
                                    "voice QA retry score %.2f → %.2f, accepted",
                                    v.score, v2.score,
                                )
                    except Exception:
                        logger.exception("QA retry failed")
            except Exception:
                pass

        # Always feed the rolling quality monitor — covers every surface so
        # `quality_snapshot` and the dashboard sparkline reflect real usage,
        # not just voice. Pure heuristic, no LLM cost.
        try:
            from kee.cognition.conversation_monitor import observe as _qa_obs
            _qa_obs(final, source=source or "chat",
                    expected_lang="es" if source == "voice" else "es")
        except Exception:
            logger.debug("conversation_monitor observe skipped", exc_info=True)

        # Auto-record dispatch breadcrumb when the user's turn mentions a
        # known active project. Cheap substring match; no LLM, no audit
        # bloat (we only fire on actual matches). Lets `dispatch_registry`
        # populate organically without the agent having to call
        # `dispatch.record` manually.
        try:
            self._auto_dispatch(user_input, source=source)
        except Exception:
            logger.debug("auto_dispatch skipped", exc_info=True)

        # Persist the final assistant turn to the on-disk transcript and to
        # the in-memory messages list so the next turn sees it.
        state.messages.append({"role": "assistant", "content": final})
        self.memory.store_message(state.id, "assistant", final)
        self.audit.log_response(state.id, final)
        return final, state

    # ── Reasoning loop ────────────────────────────────────────────────────
    async def _reason(self, state: ConversationState, decision: Any = None) -> str:
        # Compact schemas: descriptions truncated to ~90 chars so the
        # 65-tool schema fits in num_ctx alongside the system prompt. The
        # full descriptions remain available via registry.get_schemas()
        # for the dashboard /tools page and human introspection.
        tools_schema = self.registry.get_compact_schemas()

        # Conversational/simple turns don't need tools — the router
        # already classified them as chat. Skipping the schema saves
        # ~3,000 tokens of context budget for the actual reply, which is
        # the difference between "Hola" (1 token) and a real response.
        # Tool tiers (medium/heavy) keep the full schema.
        if decision is not None and getattr(decision, "tier", None) in (
            "conversational", "simple",
        ):
            tools_schema = []
            logger.info(
                "Router tier=%s — skipping tool schema to save context budget.",
                decision.tier,
            )
        # Capture the original user input — the last user message currently
        # in state.messages, before the loop starts appending nudge messages.
        # Used as the minimal-context fallback when Ollama refuses the full
        # request (template parser failure or context overflow).
        original_user_input = next(
            (
                (m.get("content") or "")
                for m in reversed(state.messages)
                if m.get("role") == "user"
            ),
            "",
        )
        # Surface-based hard tool gating. Telegram/voice are terse one-shot
        # surfaces; the model has shown it will reach for `plan` /
        # `world_model` on innocent status questions ("como va auctorum")
        # and fabricate scores. Strip those tools from the schema unless
        # the user's literal text invoked them. Cheaper and more reliable
        # than relying on prompt rules a small model will ignore.
        if state.source in ("telegram", "voice"):
            last_user = ""
            for m in reversed(state.messages):
                if m.get("role") == "user":
                    last_user = (m.get("content") or "").lower()
                    break
            triggers = (
                "planea", "plan ", "haz un plan", "alternativa",
                "lista proyectos", "world model", "modelo del mundo",
                "impacto", "criticality", "world_model",
                "que sigue con", "siguiente paso para",
            )
            if not any(t in last_user for t in triggers):
                blocked_names = {"plan", "world_model"}
                tools_schema = [
                    s for s in tools_schema
                    if (s.get("function", {}).get("name") if isinstance(s, dict) else None)
                    not in blocked_names
                ]
                logger.info(
                    "Surface=%s, hard-blocked tools without trigger: %s",
                    state.source, sorted(blocked_names),
                )
        nudged_for_silence = False
        nudged_for_fake_call = False
        tool_failures: dict[str, int] = {}
        total_failures = 0           # across all tools in this turn
        real_tool_calls_in_turn = 0  # only counts actual tool invocations
        # When Ollama's tool-call parser dies (Qwen XML malformed → 500),
        # disable tools for the *next* iteration so the model is forced to
        # answer in plain text instead of looping back into the same crash.
        suppress_tools_next = False
        # Tracks how many CONSECUTIVE iterations Ollama returned an
        # empty/error response with no recoverable content. If this happens
        # twice in a row even with tools suppressed, the model's chat
        # template itself is broken (or the context is too small) — bail
        # out cleanly instead of looping max_iterations.
        consecutive_empty_errors = 0
        tool_names = set(self.registry.tools.keys())

        while state.iteration < settings.max_iterations:
            state.iteration += 1
            logger.debug("Agent iteration %d", state.iteration)

            this_iter_tools = None if suppress_tools_next else tools_schema
            if suppress_tools_next:
                logger.info(
                    "Iteration %d: tools suppressed after Ollama parse failure",
                    state.iteration,
                )
            suppress_tools_next = False

            # Route the call through the multi-provider chain. If the router
            # picked a specific provider for this turn (medium → claude,
            # heavy → claude, simple → ollama), pin to it via force_provider.
            # Heavy tier asks for more output room.
            force = decision.provider_target if decision and decision.provider_target in ("ollama", "ollama_remote", "claude", "haiku", "openai") else None
            max_tok = 4096 if (decision and decision.tier == "heavy") else None
            try:
                response = await self.chain.chat(
                    messages=state.messages,
                    tools=this_iter_tools,
                    force_provider=force,
                    max_tokens=max_tok,
                )
                # Audit the LLM call (cost, latency, provider, tier)
                if response.provider_name:
                    try:
                        self.audit.log_llm_call(
                            conversation_id=state.id,
                            provider=response.provider_name,
                            model_name=response.model_name or "?",
                            tier=(decision.tier if decision else "unknown"),
                            latency_ms=response.latency_ms,
                            tokens_in=response.tokens_in,
                            tokens_out=response.tokens_out,
                            cost_usd=response.cost_usd,
                        )
                    except Exception:
                        pass  # never let audit failure break the agent loop
            except Exception as e:
                # If the chain raised (all providers failed) — surface as
                # OllamaUnavailable so the existing handler covers it.
                logger.exception("LLM chain failed at iteration %d", state.iteration)
                raise OllamaUnavailable(f"All LLM providers failed: {e}") from e

            # Detect Ollama-side errors (tool-parse failures, context-size
            # overflow, etc.) that return empty content. The original logic
            # only handled tool-parse failures by suppressing tools for the
            # next iteration; if the SAME error keeps happening even with
            # tools suppressed, we'd loop forever. Track consecutive empties
            # and break out cleanly on the second one.
            raw_err = None
            if isinstance(response.raw, dict):
                raw_err = response.raw.get("_error")
            response_is_empty_error = (
                raw_err is not None and not response.content
            )
            if response_is_empty_error:
                consecutive_empty_errors += 1
                tool_parse = response.raw.get("_tool_parse_failure")
                ctx_exceeded = "exceed_context_size" in (raw_err or "")

                # Second empty-error in a row → stop looping. Either the
                # model template is fundamentally broken or the context
                # window is too small. Fall back to a plain-text retry
                # WITHOUT system prompt or accumulated nudges, then break.
                if consecutive_empty_errors >= 2:
                    # INFO not WARNING — the system handles this correctly
                    # by falling back to minimal context, the user gets a
                    # real reply, no reason to dump a warning above their
                    # prompt in the REPL.
                    logger.info(
                        "Two consecutive empty Ollama errors "
                        "(parse=%s, ctx_exceeded=%s) — bailing out of the "
                        "agent loop and attempting a minimal-context retry.",
                        tool_parse, ctx_exceeded,
                    )
                    try:
                        minimal = [
                            {"role": "user", "content": original_user_input},
                        ]
                        fallback = await self.chain.chat(
                            messages=minimal,
                            tools=None,
                            force_provider=force,
                            max_tokens=max_tok,
                        )
                        fallback_content = (fallback.content or "").strip()
                    except Exception as e:
                        logger.warning("minimal-context retry failed: %s", e)
                        fallback_content = ""
                    if fallback_content:
                        return fallback_content
                    if ctx_exceeded:
                        return (
                            "El prompt rebasó la ventana de contexto del "
                            "modelo. Sube `KEE_NUM_CTX` (recomendado 8192) "
                            "o reduce el system prompt y vuelve a intentar."
                        )
                    return (
                        "El modelo local no pudo generar una respuesta para "
                        "este turno. Si esto se repite, prueba con otro "
                        "modelo (`KEE_MODEL`) o reinicia Ollama."
                    )

                # First empty error — arm the suppress + nudge retry, but
                # tailor the nudge to the actual failure mode.
                if tool_parse:
                    nudge = (
                        "Your previous reply produced a malformed tool call "
                        "and Ollama could not parse it. Reply now in plain "
                        "Spanish text — no tool calls — describing what you "
                        "intended to do or what blocks you."
                    )
                elif ctx_exceeded:
                    nudge = (
                        "The previous request exceeded the context window. "
                        "Reply now with a short plain-text answer to the "
                        "user's last message. No tool calls, no preamble."
                    )
                else:
                    nudge = (
                        "Ollama returned an error and produced no text. "
                        "Reply now with a brief plain-text answer to the "
                        "user's last message. No tool calls."
                    )
                state.messages.append({"role": "user", "content": nudge})
                suppress_tools_next = True
                continue
            else:
                consecutive_empty_errors = 0

            if response.has_tool_calls:
                # Guardrail 1: if total failures across ALL tools in this turn
                # exceeds 4, stop calling tools entirely. The model is flailing.
                if total_failures >= 4:
                    state.messages.append({
                        "role": "user",
                        "content": (
                            f"You've had {total_failures} tool failures in this "
                            "turn. Stop calling tools. Respond to me with plain "
                            "text summarising what you tried and what's blocking you."
                        ),
                    })
                    continue

                # Guardrail 2: if any specific tool just failed twice, block it.
                blocked = [
                    tc["name"] for tc in response.tool_calls
                    if tool_failures.get(tc["name"], 0) >= 2
                ]
                if blocked:
                    # Self-correction attempt BEFORE the hard block.
                    # Skip on voice (small max_iterations budget) and
                    # when the recovery LLM is unreachable.
                    corrected = None
                    if (state.source != "voice"
                            and not getattr(state, "_self_correction_done", False)):
                        try:
                            corrected = await self._propose_tool_correction(
                                state, blocked,
                            )
                        except Exception as e:
                            logger.debug("self-correction skipped: %s", e)
                    if corrected:
                        state._self_correction_done = True
                        # Inject the proposed correction as a user instruction
                        # and reset the per-tool failure counter so the next
                        # turn gets ONE more attempt.
                        for name in set(blocked):
                            tool_failures[name] = 1
                        state.messages.append({
                            "role": "user",
                            "content": (
                                "Tool autocorrection — try this exact call "
                                "instead. The previous attempts failed:\n"
                                f"{corrected}"
                            ),
                        })
                        # Audit so Sleep Cycle can spot recurring corrections.
                        try:
                            from kee.core import db as _db
                            with _db.cursor() as cur:
                                cur.execute(
                                    "INSERT INTO audit_log "
                                    "(action, tool_name, success, parameters) "
                                    "VALUES (?, ?, ?, ?)",
                                    ("self_correction", ",".join(sorted(set(blocked))),
                                     1, corrected[:600]),
                                )
                        except Exception:
                            pass
                        continue
                    state.messages.append({
                        "role": "user",
                        "content": (
                            f"Tools {sorted(set(blocked))} have failed twice. "
                            "Stop calling them. Pick a different tool, or "
                            "respond to me with plain text."
                        ),
                    })
                    continue

                fails_before = sum(tool_failures.values())
                await self._handle_tool_calls(state, response, tool_failures)
                total_failures += sum(tool_failures.values()) - fails_before
                real_tool_calls_in_turn += len(response.tool_calls)
                continue

            content = (response.content or "").strip()

            # SALVAGE STEP — if the model emitted a structured-looking JSON
            # in its text instead of a real tool call (a known abliterated
            # Qwen3.5 failure mode), parse it and dispatch as if it were
            # real. This unblocks the user when the model can SHAPE a call
            # but can't EMIT one through the proper channel.
            if content and not response.has_tool_calls:
                salvaged = salvage_tool_calls(content, tool_names)
                if salvaged:
                    logger.warning(
                        "Salvaging %d text-form tool call(s): %s",
                        len(salvaged), [s[0] for s in salvaged],
                    )
                    # Replay them as if the model had emitted them properly.
                    state.messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": n, "arguments": a}}
                            for (n, a) in salvaged
                        ],
                    })
                    for (name, args) in salvaged:
                        ok = await self._execute_tool(state, name, args)
                        if not ok:
                            tool_failures[name] = tool_failures.get(name, 0) + 1
                    real_tool_calls_in_turn += len(salvaged)
                    continue  # let the model react to the actual results

            # Hallucination guard: model wrote `tool: X / Result: ... /
            # Files created: ...` as TEXT but never actually called a tool
            # this turn. Force one retry with a sharp correction.
            if (
                content
                and real_tool_calls_in_turn == 0
                and not nudged_for_fake_call
            ):
                fake = _looks_like_fake_tool_call(content, tool_names)
                if fake is not None:
                    logger.warning(
                        "Detected fake tool-call narration (claimed=%s) "
                        "without any real tool invocation. Forcing retry.",
                        fake,
                    )
                    state.messages.append({
                        "role": "user",
                        "content": (
                            f"You wrote text that looks like a `{fake}` tool "
                            "call (with `Result:` / `Files created:` / "
                            "`Workdir:` lines), but you did NOT actually "
                            "invoke any tool — your previous reply was just "
                            "narration. Nothing ran. Nothing was created.\n\n"
                            "Now do ONE of:\n"
                            "  (a) Emit a real structured tool call to "
                            "perform the action.\n"
                            "  (b) Reply honestly in plain text saying you "
                            "cannot or will not perform it.\n\n"
                            "Do NOT write another fake `Result:` block."
                        ),
                    })
                    nudged_for_fake_call = True
                    continue

            if content:
                return content

            # Empty assistant turn. Models sometimes go silent after tool
            # calls — they think the side-effect *is* the answer. We nudge
            # exactly once for a verbal reply, then accept whatever comes.
            tools_were_used = any(m.get("role") == "tool" for m in state.messages)
            if tools_were_used and not nudged_for_silence:
                nudged_for_silence = True
                state.messages.append({
                    "role": "user",
                    "content": (
                        "Respond now with plain text: tell me what you did and "
                        "the relevant result. Do not call any more tools."
                    ),
                })
                continue

            return content or "(silence)"

        return (
            "I've reached my reasoning limit for this task. "
            "Tell me how you'd like to continue."
        )

    async def _handle_tool_calls(
        self,
        state: ConversationState,
        response: ChatResponse,
        tool_failures: dict[str, int] | None = None,
    ) -> None:
        # Append the assistant message with tool_calls in a shape that
        # works for BOTH Ollama and OpenAI. OpenAI requires `id` on each
        # call (and the matching `tool_call_id` on the tool response —
        # set in `_execute_tool` via state._last_tool_call_id below).
        state.messages.append({
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": [
                {
                    "id": tc.get("id") or f"call_{i}",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for i, tc in enumerate(response.tool_calls)
            ],
        })

        for i, tc in enumerate(response.tool_calls):
            # Stash the call id so _execute_tool can attach it to the
            # tool response message (OpenAI rejects empty tool_call_id).
            state._pending_tool_call_id = tc.get("id") or f"call_{i}"
            ok = await self._execute_tool(state, tc["name"], tc["arguments"])
            if tool_failures is not None and not ok:
                tool_failures[tc["name"]] = tool_failures.get(tc["name"], 0) + 1

    async def _execute_tool(
        self,
        state: ConversationState,
        name: str,
        raw_args: Any,
    ) -> bool:
        """Run a single tool call. Returns True on success, False on failure
        (exception, exit_code != 0, or verification anomaly).

        Catches all exceptions (including TypeError from bad arguments) and
        feeds the error string back to the LLM as a tool result so it can
        recover or pick a different tool.
        """
        # Coerce arguments into a dict.
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}

        # 1. Pre-state.
        pre_state = capture_state(name, args)

        # 2. Execute.
        success = True
        error: str | None = None
        try:
            result: Any = await self.registry.execute(name, args)
        except Exception as e:  # noqa: BLE001 — surface to the model
            # One-line summary at WARNING; full traceback only at DEBUG.
            # Keeps the REPL clean while still recoverable for `KEE_LOG_LEVEL=DEBUG`.
            logger.warning("Tool %s raised: %s: %s", name, type(e).__name__, e)
            logger.debug("Traceback for %s:", name, exc_info=True)
            success = False
            error = f"{type(e).__name__}: {e}"
            result = {"error": error}

        # 3. Post-state.
        post_state = capture_state(name, args)

        # 4. Verify.
        verification = verify(name, args, result, pre_state, post_state)

        # 5. Audit (single row carrying everything).
        risk = self.registry.get_risk_level(name)
        audit_id = self.audit.log_action(
            tool_name=name,
            parameters=args,
            result=result,
            risk_level=risk,
            success=success,
            error=error,
            pre_state=pre_state,
            post_state=post_state,
            verification=verification,
        )

        # 6. Anomaly?
        if not verification["ok"]:
            self.audit.log_anomaly(
                tool_name=name,
                verification=verification,
                audit_id=audit_id,
                kind="unexpected_change",
                severity=2 if risk >= 2 else 1,
            )

        # 6b. Confidence trail (Dynamic Autonomy Threshold). Best-effort —
        # never block the agent loop on a logging failure.
        try:
            from kee.cognition import autonomy
            autonomy.record(
                tool_name=name,
                risk_level=risk,
                success=success and verification["ok"],
            )
        except Exception:
            logger.debug("autonomy.record skipped", exc_info=True)

        # 7. Append result to the conversation so the model can react.
        try:
            serialized = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            serialized = str(result)

        state.messages.append({
            "role": "tool",
            "name": name,
            "content": serialized,
            # OpenAI requires the matching call id; Ollama ignores extras.
            "tool_call_id": getattr(state, "_pending_tool_call_id", None) or f"call_{name}",
        })

        # Surface verification failures to the model too — it should know
        # something looked off, not silently move on.
        if not verification["ok"]:
            state.messages.append({
                "role": "tool",
                "name": name,
                "content": json.dumps(
                    {"_verification": verification},
                    ensure_ascii=False, default=str,
                ),
                "tool_call_id": getattr(state, "_pending_tool_call_id", None) or f"call_{name}_v",
            })
            _ = serialize_state(pre_state)  # available in audit row

        # A tool counts as failed if it raised, came back with a shell-style
        # non-zero exit, or failed verification.
        soft_failed = (
            isinstance(result, dict)
            and (
                result.get("exit_code") not in (None, 0)
                or "error" in result
            )
        )
        return success and verification["ok"] and not soft_failed

    # ── Self-correction ───────────────────────────────────────────────────
    async def _propose_tool_correction(
        self, state: ConversationState, blocked: list[str],
    ) -> str | None:
        """When a tool has failed twice, ask the cheap local model to
        analyse the error+inputs and propose a corrected call. Returns
        a plain string the next iteration sees as a user instruction
        (not a structured tool call — the agent decides whether to act
        on it). Returns None to fall through to the hard block.

        Uses local Ollama directly (free, ~1-3s, no chain cost). On
        any error, returns None so the existing breaker takes over.
        """
        # Pull the last 6 messages for context — covers the failed call,
        # the error result, and the agent's prior reasoning.
        recent = state.messages[-6:] if len(state.messages) > 6 else state.messages
        ctx_lines = []
        for m in recent:
            role = m.get("role", "?")
            content = (m.get("content") or "")[:400]
            name = m.get("name") or m.get("tool_name") or ""
            ctx_lines.append(f"[{role}{f' ({name})' if name else ''}] {content}")
        ctx = "\n".join(ctx_lines)

        prompt = (
            f"Tool calls to {sorted(set(blocked))} have failed twice in this turn.\n"
            f"Context (last 6 messages):\n---\n{ctx}\n---\n\n"
            "Diagnose what went wrong (wrong arg name, missing required, "
            "wrong type, path doesn't exist, etc.) and propose ONE concrete "
            "alternative — either a corrected call to the same tool with "
            "fixed arguments OR a different tool that achieves the goal. "
            "Output 2-4 short lines in Spanish. NO JSON, NO code fences. "
            "Be specific about which arg to change."
        )
        try:
            from kee.core.ollama_client import OllamaClient
            client = OllamaClient()
            resp = await client.chat(
                messages=[
                    {"role": "system",
                     "content": "Eres un debugger técnico. Conciso. Spanish."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
        except Exception as e:
            logger.debug("self-correction llm failed: %s", e)
            return None
        text = (resp.content or "").strip()
        if len(text) < 10:
            return None
        return text[:600]

    # ── Self-awareness ────────────────────────────────────────────────────
    def _auto_dispatch(self, user_input: str, source: str | None) -> None:
        """Look at the user's turn for mentions of known active projects;
        if any match, drop a `dispatch` breadcrumb so `dispatch_registry`
        builds project-level history without the agent having to call
        `dispatch.record` explicitly. No-op when nothing matches.
        """
        if not user_input or len(user_input) < 5:
            return
        text = user_input.lower()
        # Hardcode well-known projects + a small "any project" fallback. The
        # dispatch_registry will surface the active set via active_projects(),
        # but we avoid a DB read on every turn — the static list covers
        # 99% of real mentions and a stale entry is harmless.
        known = ("auctorum", "kee", "nahual", "medconcierge", "netprobe",
                 "fat dogs", "hackathon", "isc2")
        for proj in known:
            if proj in text:
                try:
                    from kee.cognition import dispatch_registry as dr
                    dr.record_dispatch(
                        project=proj,
                        kind="mention",
                        summary=(user_input[:120] + "…")
                                 if len(user_input) > 120 else user_input,
                        metadata={"source": source or "?"},
                    )
                except Exception:
                    pass
                # One project per turn — first match wins.
                return

    def _build_capabilities_block(self) -> str:
        return (
            "## Current Capabilities\n\n"
            f"### Tools available ({len(self.registry.tools)})\n"
            f"{self.registry.manifest()}\n\n"
            f"{self._build_filesystem_block()}\n\n"
            "### Notes\n"
            "- Decide WHEN to use a tool. If the user just wants to chat, answer directly.\n"
            "- Tool results are appended as `tool` role messages.\n"
            "- After a tool call, reason about the result before responding.\n"
            "- Tool calls go through a verification loop. If a result is flagged "
            "with `_verification.ok = false`, treat it as suspect and act accordingly.\n"
            "- **Never fabricate file paths or contents.** If you don't know where "
            "something lives, list a directory you do know (start at the project "
            "root below) or ask the user.\n"
        )

    def _build_filesystem_block(self) -> str:
        from kee.config import settings as _s
        return (
            "### Filesystem context\n"
            f"- **Project root** (this Kee installation): `{_s.project_root}`\n"
            f"- **Vault**: `{_s.vault_dir}`  (Obsidian-style notes)\n"
            f"- **Identity files**: `{_s.identity_path.parent}` "
            "(identity.md, soul.md, user.md, goals.md)\n"
            f"- **Goals file**: `{_s.vault_dir / 'config' / 'goals.md'}` "
            "— use the `goals` tool to query, don't grep the file.\n"
            f"- **Generated tools**: `{_s.custom_tools_dir}`\n"
            f"- **SQLite DB**: `{_s.db_path}` (conversations, audit, tools)\n"
            "- The user's other projects (AUCTORUM, AEGIS Terminal, NETPROBE, "
            "Vox Praxis, Fat Dogs) are **not** in any predictable path on this "
            "machine. Do not guess `D:\\AUCTORUM\\…` etc. — ask the user.\n"
        )
