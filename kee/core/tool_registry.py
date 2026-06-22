"""Dynamic tool registry.

Loads built-in tools at startup and watches `vault/_kee/tools/` for custom
tools that Kee has written for itself. Tracks per-tool metadata (use_count,
last_used, probationary status) in both memory and the SQLite tool_registry
table — the in-memory copy is authoritative during a session, SQLite makes
it durable across restarts.

Public surface:
  * `register(tool, probationary=False)` — add a tool.
  * `unregister(name)` — remove a tool from the in-memory registry.
  * `execute(name, params)` — invoke + bump usage counters.
  * `get_schemas()` — render the OpenAI/Ollama tool-calling schema list.
  * `get_probationary_tools()` — for the GC.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow_naive() -> datetime:
    """Drop-in for the deprecated `datetime.utcnow()`. Returns a naive
    UTC datetime so SQLite stringifies it without a `+00:00` suffix
    (matches existing rows). Once Python 3.16 lands, every utcnow() in
    the codebase should migrate through this helper."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

from kee.config import settings
from kee.core import db
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


class _ToolEntry:
    __slots__ = ("tool", "probationary", "use_count", "last_used", "registered_at")

    def __init__(self, tool: Tool, probationary: bool = False) -> None:
        self.tool = tool
        self.probationary = probationary
        self.use_count = 0
        self.last_used: datetime | None = None
        self.registered_at = _utcnow_naive()


class ToolRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, _ToolEntry] = {}

    # ── Compatibility property: tests / other modules look at `.tools` ────
    @property
    def tools(self) -> dict[str, Tool]:
        return {name: e.tool for name, e in self._entries.items()}

    # ── Registration ──────────────────────────────────────────────────────
    def register(self, tool: Tool, probationary: bool = False) -> None:
        if not tool.name:
            raise ValueError(f"Tool {type(tool).__name__} has no name")
        if tool.name in self._entries:
            logger.warning("Tool '%s' is being overwritten in the registry", tool.name)
        self._entries[tool.name] = _ToolEntry(tool, probationary=probationary)
        self._persist(tool, probationary)
        logger.debug(
            "Registered tool: %s (risk=%d, source=%s, probationary=%s)",
            tool.name, tool.risk_level, tool.source, probationary,
        )

    def unregister(self, name: str) -> bool:
        return self._entries.pop(name, None) is not None

    def _persist(self, tool: Tool, probationary: bool) -> None:
        try:
            with db.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tool_registry
                        (name, description, parameters_schema, source, file_path,
                         risk_level, probationary)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        description = excluded.description,
                        parameters_schema = excluded.parameters_schema,
                        source = excluded.source,
                        file_path = excluded.file_path,
                        risk_level = excluded.risk_level,
                        probationary = excluded.probationary
                    """,
                    (
                        tool.name,
                        tool.description,
                        json.dumps(tool.parameters_schema, ensure_ascii=False),
                        tool.source,
                        getattr(tool, "file_path", None),
                        tool.risk_level,
                        int(probationary),
                    ),
                )
        except Exception as e:
            logger.warning("Failed to persist tool '%s' to SQLite: %s", tool.name, e)

    # ── Built-in / custom loading ─────────────────────────────────────────
    def load_builtins(self) -> None:
        from kee.tools import shell, files, web, memory_tool, system as system_tool
        from kee.tools import create_tool as create_tool_mod
        from kee.tools import goals as goals_mod
        from kee.tools import claude_code as claude_code_mod
        from kee.tools import keecode as keecode_mod
        from kee.tools import vercel_deploy as vercel_mod
        from kee.tools import github as github_mod
        from kee.tools import infer_goal as infer_goal_mod
        from kee.tools import notify as notify_mod
        from kee.tools import open_app as open_app_mod
        from kee.tools import browser_control as browser_mod
        from kee.tools import world as world_mod
        from kee.tools import planner as planner_mod
        from kee.tools import economy as economy_mod
        from kee.tools import calendar_tool as calendar_mod
        from kee.tools import gmail_tool as gmail_mod
        from kee.tools import email_send as email_mod
        from kee.tools import whatsapp_send as whatsapp_mod
        from kee.tools import screen as screen_mod
        from kee.tools import spotify as spotify_mod
        from kee.tools import wol as wol_mod
        from kee.tools import market as market_mod
        from kee.tools import home_assistant as hass_mod
        from kee.tools import desktop_control as desktop_ctrl_mod
        # Jarvis-inspired ports (2026-05-04 audit batch):
        from kee.tools import clipboard as clipboard_mod
        from kee.tools import windows as windows_mod
        from kee.tools import weather as weather_mod
        from kee.tools import news as news_mod
        from kee.tools import research as research_mod
        from kee.tools import system_control as syscon_mod
        from kee.tools import tool_reliability as tool_rel_mod
        from kee.tools import notes as notes_mod
        # Batch 2 (2026-05-04 evening Cities-Skylines session):
        from kee.tools import work_session as worksess_mod
        from kee.tools import perf_stats as perf_mod
        from kee.tools import scaffold as scaffold_mod
        from kee.tools import user_patterns as user_patterns_mod
        from kee.tools import quality_snapshot as quality_mod
        from kee.tools import recall as recall_mod
        from kee.tools import dispatch as dispatch_mod
        from kee.tools import reflect as reflect_mod
        from kee.tools import inbox_triage as inbox_triage_mod
        from kee.tools import commits as commits_mod
        from kee.tools import focus as focus_mod
        from kee.tools import brief as brief_mod
        from kee.tools import smart_search as smart_search_mod
        from kee.tools import schedule_self as schedule_self_mod
        from kee.tools import context as context_mod
        from kee.tools import emit as emit_mod
        from kee.tools import pomodoro as pomodoro_mod
        from kee.tools import learn as learn_mod
        from kee.tools import projects as projects_mod
        from kee.tools import vault_search as vault_search_mod
        from kee.tools import worker_health as worker_health_mod
        from kee.tools import vision as vision_mod
        from kee.tools import describe_screen as describe_screen_mod
        from kee.tools import episodic as episodic_mod
        from kee.tools import narrate_day as narrate_day_mod
        from kee.tools import recap_week as recap_week_mod
        from kee.tools import apply_rewrite as apply_rewrite_mod
        from kee.tools import compare_days as compare_days_mod

        for module in (shell, files, web, memory_tool, system_tool,
                       create_tool_mod, goals_mod, claude_code_mod,
                       keecode_mod,
                       vercel_mod, github_mod, infer_goal_mod, notify_mod,
                       open_app_mod, browser_mod, world_mod, planner_mod,
                       economy_mod, calendar_mod, gmail_mod, email_mod,
                       whatsapp_mod, screen_mod, spotify_mod, wol_mod,
                       market_mod, hass_mod, desktop_ctrl_mod,
                       clipboard_mod, windows_mod, weather_mod, news_mod,
                       research_mod, syscon_mod, tool_rel_mod, notes_mod,
                       worksess_mod, perf_mod, scaffold_mod, user_patterns_mod,
                       quality_mod, recall_mod, dispatch_mod,
                       reflect_mod, inbox_triage_mod, commits_mod,
                       focus_mod, brief_mod, smart_search_mod,
                       schedule_self_mod, context_mod, emit_mod,
                       pomodoro_mod, learn_mod, projects_mod,
                       vault_search_mod, worker_health_mod, vision_mod,
                       describe_screen_mod, episodic_mod, narrate_day_mod,
                       recap_week_mod, apply_rewrite_mod,
                       compare_days_mod):
            found = 0
            for attr_name in dir(module):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(module, attr_name)
                if isinstance(attr, Tool):
                    self.register(attr, probationary=False)
                    found += 1
            if found == 0:
                logger.warning("Module %s exposes no Tool instances", module.__name__)

    def load_custom(self) -> None:
        custom_dir = settings.custom_tools_dir
        custom_dir.mkdir(parents=True, exist_ok=True)
        for tool_file in custom_dir.glob("*.py"):
            if tool_file.name.startswith("_"):
                continue
            try:
                self._load_module(tool_file)
            except Exception as e:
                logger.error("Failed to load custom tool %s: %s", tool_file, e)

    def _load_module(self, path: Path) -> None:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            attr = getattr(module, attr_name)
            if isinstance(attr, Tool):
                # Custom tools land probationary by default.
                attr.source = "custom"
                attr.file_path = str(path)
                self.register(attr, probationary=True)

    # ── Execution ─────────────────────────────────────────────────────────
    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        if name not in self._entries:
            raise KeyError(f"Unknown tool: {name}")
        entry = self._entries[name]
        # Filter out kwargs the tool's `execute` doesn't actually accept.
        # Models routinely hallucinate extras (e.g. `query=`, `id=`); without
        # filtering they raise TypeError and waste a turn iteration.
        filtered = self._filter_kwargs(entry.tool, params)
        # Pre-validate `required` kwargs against the declared schema. Cheaper
        # to short-circuit here than let the call raise TypeError mid-execute.
        missing = self._missing_required(entry.tool, filtered)
        if missing:
            self._record_missing_required(entry.tool, missing,
                                          provided=list(filtered.keys()))
            return {
                "ok": False,
                "error": f"missing required argument(s): {sorted(missing)}",
                "tool": entry.tool.name,
                "required": missing,
                "hint": ("LLM omitted required kwargs; re-call with all "
                         "fields under `parameters_schema.required`."),
            }
        try:
            return await entry.tool.execute(**filtered)
        finally:
            entry.use_count += 1
            entry.last_used = _utcnow_naive()
            self._bump_persisted_usage(name, entry.use_count, entry.last_used)

    @staticmethod
    def _filter_kwargs(tool: Tool, params: dict[str, Any]) -> dict[str, Any]:
        """Strip unknown kwargs unless the tool declares `**kwargs`.

        Hallucinated kwargs (those the LLM imagines but the tool doesn't
        accept) are recorded to `audit_log` with action='kwarg_hallucination'
        so Sleep Cycle can spot recurring offenders. The cleaned params are
        what actually reach the tool.
        """
        try:
            sig = inspect.signature(tool.execute)
        except (TypeError, ValueError):
            return params
        # If the tool's signature uses VAR_KEYWORD (**kwargs), keep everything.
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            return params
        accepted = {n for n, p in sig.parameters.items()
                    if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                  inspect.Parameter.KEYWORD_ONLY)}
        unknown = set(params) - accepted
        if unknown:
            logger.debug(
                "Tool %r ignoring unexpected kwargs: %s",
                tool.name, sorted(unknown),
            )
            try:
                with db.cursor() as cur:
                    cur.execute(
                        "INSERT INTO audit_log "
                        "(action, tool_name, success, parameters) "
                        "VALUES (?, ?, ?, ?)",
                        ("kwarg_hallucination", tool.name, 1,
                         json.dumps({"unknown": sorted(unknown),
                                     "accepted": sorted(accepted)},
                                    ensure_ascii=False)),
                    )
            except Exception as e:  # noqa: BLE001
                # Telemetry must never break tool execution.
                logger.debug("kwarg_hallucination telemetry skipped: %s", e)
        return {k: v for k, v in params.items() if k in accepted}

    @staticmethod
    def _missing_required(tool: Tool, params: dict[str, Any]) -> list[str]:
        """Return any `required` kwargs from the schema that aren't in
        `params` (after `_filter_kwargs` ran). Empty list when fine."""
        sch = getattr(tool, "parameters_schema", None) or {}
        required = sch.get("required") or []
        if not isinstance(required, list):
            return []
        return [r for r in required if r not in params]

    @staticmethod
    def _record_missing_required(
        tool: Tool, missing: list[str], *, provided: list[str],
    ) -> None:
        """Audit a `kwarg_missing_required` row so Sleep Cycle can see when
        the LLM keeps omitting required fields for the same tool — usually
        a sign the description doesn't make the requirement obvious."""
        try:
            with db.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_log "
                    "(action, tool_name, success, parameters) "
                    "VALUES (?, ?, ?, ?)",
                    ("kwarg_missing_required", tool.name, 0,
                     json.dumps({"missing": sorted(missing),
                                 "provided": sorted(provided)},
                                ensure_ascii=False)),
                )
        except Exception as e:
            logger.debug("missing-required telemetry skipped: %s", e)

    def _bump_persisted_usage(self, name: str, count: int, ts: datetime) -> None:
        try:
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE tool_registry SET use_count = ?, last_used = ? WHERE name = ?",
                    (count, ts, name),
                )
        except Exception as e:
            logger.debug("Could not bump persisted usage for %s: %s", name, e)

    # ── Introspection ─────────────────────────────────────────────────────
    def get_schemas(self) -> list[dict[str, Any]]:
        return [e.tool.to_schema() for e in self._entries.values()]

    def get_compact_schemas(
        self,
        max_desc_chars: int = 90,
    ) -> list[dict[str, Any]]:
        """LLM-facing schema with truncated descriptions.

        The full descriptions average ~250 tokens each — across 65 tools
        that's ~16k tokens of overhead before any system prompt or user
        message lands. On an 8GB GPU with num_ctx=8192 every turn was
        overflowing the context window. The compact form keeps the first
        line + first ``max_desc_chars`` characters of the description,
        which is enough for the model to pick the right tool, and drops
        the schema cost from ~15.9k to ~4.5k tokens (4x shrink).

        The dashboard's /tools page still uses ``get_schemas`` for the
        full version — only the LLM call goes through the compact form.
        """
        out = []
        for e in self._entries.values():
            schema = e.tool.to_schema()
            fn = schema.get("function") if isinstance(schema, dict) else None
            if not isinstance(fn, dict):
                out.append(schema)
                continue
            desc = (fn.get("description") or "").strip()
            # First paragraph + char cap. Tools that put the actionable
            # bullet list further down will be summarised by their first
            # paragraph — that's the right tradeoff at agent-loop time.
            first_para = desc.split("\n\n", 1)[0].strip()
            short = first_para[:max_desc_chars].rstrip()
            if len(first_para) > max_desc_chars:
                short = short.rstrip(",.;:") + "..."
            fn["description"] = short
            out.append(schema)
        return out

    def get_risk_level(self, name: str) -> int:
        entry = self._entries.get(name)
        return entry.tool.risk_level if entry else 3

    def names(self) -> list[str]:
        return list(self._entries.keys())

    def manifest(self) -> str:
        if not self._entries:
            return "No tools registered."
        lines = []
        for e in self._entries.values():
            short = e.tool.description.strip().split("\n")[0][:120]
            tag = " [probationary]" if e.probationary else ""
            lines.append(f"- {e.tool.name} (risk {e.tool.risk_level}){tag}: {short}")
        return "\n".join(lines)

    def get_probationary_tools(self) -> list[dict[str, Any]]:
        out = []
        now = _utcnow_naive()
        for e in self._entries.values():
            if not e.probationary:
                continue
            age_days = (now - e.registered_at).total_seconds() / 86400.0
            out.append({
                "name": e.tool.name,
                "file_path": getattr(e.tool, "file_path", None),
                "use_count": e.use_count,
                "last_used": e.last_used,
                "registered_at": e.registered_at,
                "age_days": age_days,
            })
        return out
