"""Identity loader.

Reads `identity.md`, `soul.md`, `user.md` from the vault and concatenates
them into a single system prompt. Re-reads on every call so live edits to
the markdown take effect on the next conversation without a restart.

Also pulls the Sleep Cycle's most recent axioms (`user_behavior.json`)
and injects them — closing the cognitive loop: Sleep Cycle observes →
axioms → next conversation behaviour shifts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from kee.config import settings

logger = logging.getLogger(__name__)


def _read(path: Path) -> str:
    if not path.exists():
        logger.warning("Identity file missing: %s", path)
        return ""
    return path.read_text(encoding="utf-8").strip()


def _load_recent_axioms() -> str:
    """Pull the most recent axioms Sleep Cycle wrote. Empty string if none.

    Format that goes into the system prompt:

        ## Recent observations about Armando (Sleep Cycle, last run YYYY-MM-DD)
        - axiom 1
        - axiom 2
        ...

    The agent should TREAT these as soft hints — they describe what Sleep
    Cycle saw recently, not absolute rules. Hard rules live in soul.md.
    """
    path = settings.vault_dir / "config" / "user_behavior.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    axioms = data.get("axioms_recent") or []
    if not axioms:
        return ""
    last_run = data.get("last_run", "?")[:10]  # ISO date prefix
    lines = "\n".join(f"- {a}" for a in axioms[:5])
    return (
        f"## Recent observations about Armando "
        f"(Sleep Cycle last ran {last_run})\n"
        f"_Soft hints, not rules. soul.md still wins._\n\n{lines}"
    )


_PROJECT_ANCHOR = (
    "# COCO'S PROJECTS — anchor against training-data hallucination\n"
    "When Coco mentions any of these names, they refer to HIS personal "
    "projects (defined in user.md below). NEVER use generic facts from your "
    "training data about external companies/products with similar names.\n"
    "  - AUCTORUM = Coco's commercial AI agency (WhatsApp AI agents + landing "
    "pages). NOT a blockchain platform, NOT an NFT marketplace, NOT any "
    "external company. If you don't have updated info from user.md or recent "
    "conversation, say 'no tengo info reciente sobre AUCTORUM, ¿qué tal va?'.\n"
    "  - AEGIS / AEGIS Terminal = Coco's personal investment platform.\n"
    "  - NETPROBE = Coco's raw socket network scanner.\n"
    "  - Vox Praxis = Coco's hackathon Ciberdemocracia team.\n"
    "  - Fat Dogs = Coco's flag football team (he's captain).\n"
    "  - Kee = THIS system (you).\n"
    "If asked 'cómo va X' for any of these, answer in 1-2 sentences from "
    "user.md context only. NEVER fabricate criticality scores, blockchain "
    "claims, GitHub repos, or features you can't see in user.md.\n"
)

_SURFACE_HARD_RULES = {
    "telegram": (
        "# ACTIVE SURFACE: TELEGRAM (one-shot phone message)\n"
        "HARD RULES — these override everything below:\n"
        "1. Answer ONLY the literal question. One thought, then stop.\n"
        "2. ALWAYS reply in the SAME language Coco wrote in (Spanish input → "
        "Spanish reply, English input → English reply). Never switch.\n"
        "3. NEVER call `plan`, `world_model`, or any meta tool unless the user's "
        "literal text contains the words 'planea', 'plan', 'lista proyectos', "
        "'world model', or asks for one of those explicitly.\n"
        "4. 'cómo va X' / 'qué hay de X' / 'estado de X' = a STATUS QUESTION. "
        "Answer in 1-2 sentences from what you already know about X. Do NOT "
        "run a planning tool. Do NOT fabricate scores, repos, or criticality.\n"
        "5. If you don't know the status, say 'no tengo información actualizada' "
        "in one sentence. Don't invent.\n"
        "6. Maximum ONE tool call per turn unless the user's literal text "
        "asks for two distinct things.\n"
        "7. NEVER end with offers like '¿Te gustaría...?', '¿Quieres que...?', "
        "'Would you like...?', 'Want me to...?', 'Let me know if...', "
        "'¿Hay algo específico...?', 'How can I help...?'. The answer ends "
        "when the answer is given. Coco will ask if he needs more.\n"
        "\n"
        "TOOL-USE HINTS for terse surfaces (call the right tool the FIRST time):\n"
        "  - 'qué hora es' / 'qué hora son' / 'la hora' → DO NOT call any tool. "
        "The current time is in your CONTEXT block at the top of this prompt. "
        "Read it from there and answer 'Las HH:MM' in one line.\n"
        "  - 'qué tengo hoy' / 'qué eventos' / 'mi calendario' → CALL "
        "`calendar` with action='today' (or 'upcoming' if asked for the week). "
        "Then list events as bullets, max 6.\n"
        "  - 'cuántos correos' / 'unread' / 'inbox' → CALL `gmail` with "
        "action='unread_count'. Return just the number.\n"
        "  - 'busca en mis archivos' / 'mis archivos' → CALL `files` with "
        "action='list', path='vault' (or the path Coco specified).\n"
    ),
    "voice": (
        "# ACTIVE SURFACE: VOICE (speaking out loud through TTS)\n"
        "HARD RULES — these override everything below:\n"
        "1. ALWAYS reply in **Spanish** unless Coco explicitly asks for "
        "another language. Whisper sometimes mis-tags utterances as English; "
        "trust intent (Spanish) over tag.\n"
        "2. Maximum 2 sentences in the reply. Aim for 15-25 words.\n"
        "3. **NEVER use markdown** in voice replies. No backticks, no "
        "asterisks, no bullet points, no code blocks, no URLs. Plain prose. "
        "Piper TTS will narrate symbols literally if you write them.\n"
        "4. **NEVER say 'como una IA' / 'soy un modelo' / 'as an AI' / "
        "'absolutely' / 'certainly' / 'lo siento, pero'.** You are Kee, "
        "a person — speak directly.\n"
        "5. NEVER call `plan` or `world_model` unless the user literally asked "
        "for a plan or project list.\n"
        "6. Imperatives ('abre X', 'borra Y') get a one-word ack after the "
        "side effect succeeds, nothing more.\n"
        "7. Never invent facts, scores, repos, or criticality numbers.\n"
        "8. NEVER end with follow-up offers ('¿quieres que...?', "
        "'¿te gustaría...?'). The answer ends when the answer is given.\n"
        "9. Numbers in speech: write them out ('veintiocho', not '28'); "
        "skip code paths ('una ruta del disco', not 'D:\\Kee\\config\\soul.md').\n"
    ),
}


class IdentityLoader:
    def build_system_prompt(
        self,
        capabilities: str | None = None,
        source: str | None = None,
    ) -> str:
        """Return the full system prompt assembled from identity + soul + user.

        `capabilities` is an optional dynamic section (e.g. tool list, perception
        status) appended at the end so the model knows what it can do *right now*.
        `source` is the active surface ("terminal" / "voice" / "telegram"). For
        terse surfaces we prepend a hard, single-block rule the model can't miss.
        """
        identity = _read(settings.identity_path)
        soul = _read(settings.soul_path)
        user = _read(settings.user_path)
        axioms = _load_recent_axioms()

        now = datetime.now().strftime("%A, %Y-%m-%d %H:%M")

        sections: list[str] = []
        if source and source in _SURFACE_HARD_RULES:
            sections.append(_SURFACE_HARD_RULES[source])
        # Build a richer ambient block — focus session, last commit,
        # pending callbacks, today's spend — so the LLM lands on every
        # turn already grounded in real state. Pure SQL + git, no LLM,
        # cheap (sub-100ms).
        ambient_lines = [
            f"# CONTEXT",
            f"Current time: {now}",
            f"Location: Saltillo / Monterrey, México",
            f"Active surface: {source or 'terminal'}",
        ]
        try:
            from kee.tools.focus import _current as _focus_current
            f = _focus_current()
            if f:
                ambient_lines.append(
                    f"Active focus: {f.get('project')} "
                    f"(intent: {f.get('intent') or '?'}, "
                    f"drift_count: {f.get('drift_count', 0)})"
                )
        except Exception:
            pass
        try:
            from kee.core import db as _db
            con = _db.get_connection()
            n_callbacks = con.execute(
                "SELECT COUNT(*) FROM scheduled_callbacks "
                "WHERE fired = 0 AND cancelled = 0"
            ).fetchone()[0]
            if n_callbacks:
                ambient_lines.append(
                    f"Pending callbacks: {n_callbacks} (use `schedule_self list`)"
                )
        except Exception:
            pass
        try:
            from kee.core import db as _db
            con = _db.get_connection()
            cost_today = con.execute(
                "SELECT SUM(cost_usd) FROM audit_log "
                "WHERE provider IS NOT NULL "
                "AND timestamp >= date('now', 'localtime')"
            ).fetchone()[0]
            if cost_today and float(cost_today) > 0:
                ambient_lines.append(
                    f"LLM spend today: ${float(cost_today):.3f}"
                )
        except Exception:
            pass
        sections.append("\n".join(ambient_lines))
        sections += [identity, soul, user]
        if axioms:
            sections.append(axioms)
        # Inject top reinforced learnings so the agent has them at hand
        # without calling `learn top` first. Pure SQL, capped to 5 + 800
        # chars total to keep token cost negligible.
        try:
            from kee.core import db as _db
            con = _db.get_connection()
            rows = con.execute(
                "SELECT topic, content, reinforced FROM learnings "
                "WHERE forgotten = 0 "
                "ORDER BY reinforced DESC, id DESC LIMIT 5"
            ).fetchall()
            if rows:
                lines = ["## Cosas que Kee debe recordar (reinforced)"]
                budget = 800
                for topic, content, n in rows:
                    line = f"- **{topic}** (×{n}): {content}"
                    if budget - len(line) < 0:
                        break
                    lines.append(line)
                    budget -= len(line)
                lines.append(
                    "_Si aplicas una de estas en tu respuesta, llama "
                    "`learn reinforce id=…` para subirla en el ranking._"
                )
                sections.append("\n".join(lines))
        except Exception:
            pass
        # Cross-session project awareness (Jarvis-pattern dispatch registry).
        # Empty when no recent activity, so it costs zero tokens then.
        try:
            from kee.cognition.dispatch_registry import format_for_prompt as _dispatch_block
            block = _dispatch_block(max_chars=600)
            if block:
                sections.append(block)
        except Exception:
            pass
        if capabilities:
            sections.append(capabilities)

        return "\n\n---\n\n".join(s for s in sections if s)
