"""Telegram bot surface — chat with Kee from your phone.

Routes Telegram messages through the same `KeeAgent.process()` pipeline as
the terminal and voice surfaces, with one `ConversationState` per
Telegram chat_id so multi-turn conversations stay coherent.

Auth: needs a bot token from @BotFather on Telegram. Set it via:
    KEE_TELEGRAM_TOKEN=...
either in `.env` (Kee will pick it up via `python-dotenv`) or in the
shell. Optional `KEE_TELEGRAM_ALLOWED_USERS=username1,username2,12345`
restricts who can talk to the bot — without it, ANY user who finds the
bot can chat. Strongly recommended to set.

Long-polling (no public webhook required), so this works behind NAT and
dev machines without exposing a port.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

from kee.core.agent import KeeAgent
from kee.core.memory import ConversationState
from kee.config import settings

logger = logging.getLogger(__name__)


# Lazy-loaded whisper for inbound voice notes. We keep one instance per
# process (telegram surface is its own subprocess under the supervisor).
_WHISPER = None


def _get_whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel("small", device="cpu", compute_type="int8")
        logger.info("Telegram: faster-whisper small loaded (CPU/int8)")
    return _WHISPER


async def _transcribe_ogg(path: str) -> str:
    """Transcribe a Telegram voice note (.ogg/Opus) → Spanish text."""
    import asyncio as _asyncio
    def _do():
        m = _get_whisper()
        segments, _ = m.transcribe(path, language="es", vad_filter=True, beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()
    return await _asyncio.get_running_loop().run_in_executor(None, _do)


def _allowed_users() -> set[str]:
    raw = os.environ.get("KEE_TELEGRAM_ALLOWED_USERS", "").strip()
    if not raw:
        return set()
    out: set[str] = set()
    for token in raw.split(","):
        token = token.strip().lstrip("@")
        if token:
            out.add(token.lower())
    return out


def _is_allowed(update: Update, allow: set[str]) -> bool:
    if not allow:
        return False  # safe default — must opt-in
    user = update.effective_user
    if user is None:
        return False
    if user.username and user.username.lower() in allow:
        return True
    return str(user.id) in allow


# Per-chat conversation state. A Telegram chat_id is the equivalent of one
# REPL session — turns within it share context.
_CONV_BY_CHAT: dict[int, ConversationState] = {}


async def _on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat else 0
    _CONV_BY_CHAT.pop(chat_id, None)
    name = user.first_name if user else "Coco"
    await update.message.reply_text(
        f"Hola {name}. Estoy aquí. Mándame texto y respondo. "
        "/reset si quieres empezar conversación nueva. /status para ver mi estado."
    )


async def _on_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else 0
    _CONV_BY_CHAT.pop(chat_id, None)
    await update.message.reply_text("Reset. Próximo mensaje empieza limpio.")


async def _on_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    agent: KeeAgent = ctx.bot_data["agent"]
    healthy = await agent.llm.health()
    text = (
        f"*Kee status*\n"
        f"`model`: {agent.llm.model}\n"
        f"`ready`: {'sí' if healthy else 'no'}\n"
        f"`tools`: {len(agent.registry.tools)}\n"
        f"`chats activos`: {len(_CONV_BY_CHAT)}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    agent: KeeAgent = ctx.bot_data["agent"]
    allowed: set[str] = ctx.bot_data["allowed"]

    if not _is_allowed(update, allowed):
        user = update.effective_user
        uname = user.username if user and user.username else None
        uid = str(user.id) if user else "?"
        logger.warning("Rejected message from %s (id=%s) — not in allowed list",
                       uname or "<no username>", uid)
        await update.message.reply_text(
            "Este bot es privado.\n\n"
            f"Tu identidad Telegram: @{uname or '<no username>'} (id={uid})\n"
            f"Allowed list actual: {sorted(allowed) or '(empty)'}\n\n"
            "Si eres Coco: actualiza KEE_TELEGRAM_ALLOWED_USERS en "
            "D:/Kee/.env con uno de los dos valores de arriba."
        )
        return

    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    if not text:
        return

    state = _CONV_BY_CHAT.get(chat_id)
    await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        response, conv = await agent.process(
            text, source="telegram", state=state,
        )
        _CONV_BY_CHAT[chat_id] = conv
    except Exception as e:
        logger.exception("agent.process raised on telegram message")
        await update.message.reply_text(
            f"⚠ Error: {type(e).__name__}: {e}. "
            "El estado se mantiene; escribe de nuevo o /reset."
        )
        return

    response = (response or "").strip()
    if not response:
        return
    # Telegram limit is 4096 chars per message. Split conservatively.
    chunks = [response[i:i + 3500] for i in range(0, len(response), 3500)]
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            # Markdown parse can fail on weird input — fall back to plain.
            await update.message.reply_text(chunk)


async def _on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice notes: download, transcribe, route through the agent.

    Coco sends a voice memo from his phone → Kee transcribes (faster-whisper
    small, CPU) → processes via the same `KeeAgent.process()` as text →
    replies in text. Optionally returns a synthesized voice reply if the
    voice config has ``speak_responses=True`` AND piper is available.
    """
    agent: KeeAgent = ctx.bot_data["agent"]
    allowed: set[str] = ctx.bot_data["allowed"]
    if not _is_allowed(update, allowed):
        return
    if update.message is None or update.message.voice is None:
        return

    chat_id = update.effective_chat.id
    await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Download the .ogg
    import tempfile, os as _os
    voice = update.message.voice
    try:
        f = await voice.get_file()
    except Exception as e:
        await update.message.reply_text(f"⚠ No pude bajar la nota de voz: {e}")
        return
    tmp_dir = settings.data_dir / "telegram_voice"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ogg_path = tmp_dir / f"{voice.file_unique_id}.ogg"
    try:
        await f.download_to_drive(str(ogg_path))
    except Exception as e:
        await update.message.reply_text(f"⚠ Download error: {e}")
        return

    try:
        transcript = await _transcribe_ogg(str(ogg_path))
    except Exception as e:
        logger.exception("STT failed on telegram voice note")
        await update.message.reply_text(f"⚠ STT error: {e}")
        return
    finally:
        try:
            ogg_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not transcript:
        await update.message.reply_text("(no entendí — la nota salió vacía)")
        return

    # Show what we heard before answering — gives Coco a chance to spot STT errors.
    await update.message.reply_text(f"🎤 _{transcript}_", parse_mode=ParseMode.MARKDOWN)

    state = _CONV_BY_CHAT.get(chat_id)
    try:
        response, conv = await agent.process(
            transcript, source="telegram-voice", state=state,
        )
        _CONV_BY_CHAT[chat_id] = conv
    except Exception as e:
        logger.exception("agent.process raised on telegram voice note")
        await update.message.reply_text(f"⚠ Error: {type(e).__name__}: {e}")
        return

    response = (response or "").strip()
    if not response:
        return
    chunks = [response[i:i + 3500] for i in range(0, len(response), 3500)]
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(chunk)


async def run(agent: KeeAgent) -> None:
    token = os.environ.get("KEE_TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "KEE_TELEGRAM_TOKEN env var not set. Get a token from @BotFather "
            "on Telegram (https://t.me/BotFather → /newbot), then put "
            "KEE_TELEGRAM_TOKEN=... in D:\\Kee\\.env or set it in your shell."
        )

    allowed = _allowed_users()
    if not allowed:
        logger.warning(
            "KEE_TELEGRAM_ALLOWED_USERS is not set — bot will REFUSE all "
            "messages until you allow at least one user. Set it to your "
            "Telegram @username (or numeric user_id) and restart."
        )

    app = Application.builder().token(token).build()
    app.bot_data["agent"] = agent
    app.bot_data["allowed"] = allowed

    app.add_handler(CommandHandler("start", _on_start))
    app.add_handler(CommandHandler("reset", _on_reset))
    app.add_handler(CommandHandler("status", _on_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    app.add_handler(MessageHandler(filters.VOICE, _on_voice))

    me = await app.bot.get_me()
    logger.info("Telegram bot up as @%s. Allowed users: %s", me.username, sorted(allowed) or "(none)")
    print(f"Telegram bot live as @{me.username}. Ctrl-C to stop.")

    # Long-polling — no webhook, no port exposed.
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    try:
        # Keep the surface alive until cancelled.
        import asyncio
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
