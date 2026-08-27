"""Sleep Cycle daemon — Kee's REM phase.

Runs at 04:00 local each day. Fourteen phases, each independent and resumable
(the numbered list below covers the first seven; the rest — self-evolution,
plan-commit-linker, backup, episodic index, worker re-index, stale-plan
archival and tool-evolution — run after `_phase_digest` in `run()`):

  1. **Summarize** every conversation from the last 24h that doesn't yet
     have a summary persisted to `conversations.summary`.
  2. **Stats** — derive simple behavioural numbers (tool counts, success
     rates, peak activity hour, etc.) from the audit log.
  3. **Axioms** — ask the LLM to extract qualitative observations about
     the day from the summaries + stats.
  4. **Update user_behavior.json** at `vault/config/user_behavior.json`
     with the merged stats + axioms.
  5. **Propose identity updates** — write any proposed change as a
     standalone markdown file in `vault/_kee/identity_proposals/<date>.md`.
     Identity files (`identity.md`, `soul.md`, `user.md`) are **never**
     modified automatically — Coco reviews and applies by hand.
  6. **Daily digest** — a 1-paragraph morning brief written to
     `vault/_kee/daily/<date>.md`. Future Phase 5 delivery surfaces
     (Telegram, dashboard) read from there.

The daemon also performs lightweight cleanup: heartbeat audit rows older
than 30 days and message rows older than 90 days are pruned. Tool-call
rows are kept forever.

CLI: `python -m kee.main sleep-cycle` runs ALL phases once and exits.
`--sleep-cycle` on the terminal command spawns the daemon in the
background that wakes up every 4 AM. `--sleep-cycle-now` runs once
immediately and then daemonises.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any

from kee.config import settings
from kee.core import db
from kee.core.memory import MemoryManager
from kee.core.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


@dataclass
class SleepReport:
    started_at: datetime
    finished_at: datetime | None = None
    summarized: int = 0
    stats: dict[str, Any] = field(default_factory=dict)
    axioms: list[str] = field(default_factory=list)
    proposal_path: str | None = None
    digest_path: str | None = None
    pruned_rows: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "summarized": self.summarized,
            "stats": self.stats,
            "axioms": self.axioms,
            "proposal_path": self.proposal_path,
            "digest_path": self.digest_path,
            "pruned_rows": self.pruned_rows,
            "errors": self.errors,
        }


class SleepCycleDaemon:
    def __init__(
        self,
        memory: MemoryManager,
        llm: OllamaClient,
        audit,  # AuditLogger — typed as Any to dodge the import cycle
        wake_hour: int = 4,
        prune_heartbeats_days: int = 30,
        prune_messages_days: int = 90,
    ) -> None:
        self.memory = memory
        self.llm = llm
        self.audit = audit
        self.wake_hour = wake_hour
        self.prune_heartbeats_days = prune_heartbeats_days
        self.prune_messages_days = prune_messages_days

        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_report: SleepReport | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────
    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        loop = loop or asyncio.get_event_loop()
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run_forever())
            logger.info("Sleep Cycle daemon armed (wake hour: %02d:00)", self.wake_hour)

    def stop(self) -> None:
        self._stop.set()

    @property
    def last_report(self) -> SleepReport | None:
        return self._last_report

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            wake = self._next_wake(now)
            sleep_for = (wake - now).total_seconds()
            logger.info("Sleep Cycle: sleeping %.0f minutes until %s",
                        sleep_for / 60, wake.isoformat(timespec="seconds"))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
                return  # stop signaled
            except asyncio.TimeoutError:
                pass  # time to wake
            try:
                await self.run_once()
            except Exception:
                logger.exception("Sleep Cycle run failed")

    def _next_wake(self, now: datetime) -> datetime:
        target = now.replace(
            hour=self.wake_hour, minute=0, second=0, microsecond=0,
        )
        if target <= now:
            target = target + timedelta(days=1)
        return target

    # ── Single full pass ──────────────────────────────────────────────────
    async def run_once(self) -> SleepReport:
        report = SleepReport(started_at=datetime.now())
        logger.info("Sleep Cycle: starting full pass")
        try:
            report.summarized = await self._phase_summarize()
        except Exception as e:
            logger.exception("phase summarize failed")
            report.errors.append(f"summarize: {e}")
        try:
            report.stats = self._phase_stats()
        except Exception as e:
            logger.exception("phase stats failed")
            report.errors.append(f"stats: {e}")
        try:
            report.axioms = await self._phase_axioms(report.stats)
        except Exception as e:
            logger.exception("phase axioms failed")
            report.errors.append(f"axioms: {e}")
        try:
            self._phase_update_behavior(report.stats, report.axioms)
        except Exception as e:
            logger.exception("phase behavior failed")
            report.errors.append(f"behavior: {e}")
        try:
            report.proposal_path = await self._phase_propose_identity(report.axioms)
        except Exception as e:
            logger.exception("phase propose failed")
            report.errors.append(f"propose: {e}")
        try:
            report.pruned_rows = self._phase_cleanup()
        except Exception as e:
            logger.exception("phase cleanup failed")
            report.errors.append(f"cleanup: {e}")
        try:
            report.digest_path = await self._phase_digest(report)
        except Exception as e:
            logger.exception("phase digest failed")
            report.errors.append(f"digest: {e}")

        # Phase 8 — Self-evolution proposal (Phase 7 §"Self-editing
        # codebase daemon"). Free Ollama call. Opt-out via env.
        try:
            import os as _os
            if _os.environ.get("KEE_SLEEP_SELF_EVOLUTION", "1") not in ("0", "false", "off"):
                from kee.cognition import self_evolution as se
                se_result = await se.draft_proposal(window_days=7, llm=self.llm)
                if se_result.get("ok") and not se_result.get("dedup"):
                    report.stats["self_evolution"] = {
                        "proposal_path": se_result.get("path"),
                        "hash": se_result.get("hash"),
                    }
                    logger.info("Sleep Cycle: code proposal drafted at %s",
                                se_result.get("path"))
        except Exception as e:
            logger.exception("self-evolution phase failed")
            report.errors.append(f"self_evolution: {e}")

        # Phase 10 — Plan ↔ commit auto-linker. Find pending plans whose
        # task tokens overlap with recent commit subjects; mark strong
        # matches as executed automatically. Pure SQL + git CLI, no LLM.
        try:
            import os as _os
            if _os.environ.get("KEE_SLEEP_PLAN_LINKER", "1") not in ("0", "false", "off"):
                from kee.cognition import plan_commit_linker as pcl
                link_result = pcl.propose_plan_links(window_days=14, apply=True)
                report.stats["plan_commit_linker"] = link_result
        except Exception as e:
            logger.exception("plan-commit-linker phase failed")
            report.errors.append(f"plan_linker: {e}")

        # Phase 14 — Daily backups. WAL-safe sqlite snapshot via
        # .backup() over a read-only second connection + vault tar.gz +
        # opt-in worker chroma over SSH. Rotation prunes >30d.
        try:
            import os as _os
            if _os.environ.get("KEE_SLEEP_BACKUP", "1") not in ("0", "false", "off"):
                from kee.cognition.backup import run_backups
                bk = run_backups()
                report.stats["backup"] = {
                    "sqlite_ok": bk.get("sqlite", {}).get("ok"),
                    "vault_ok": bk.get("vault", {}).get("ok"),
                    "worker_chroma_ok": bk.get("worker_chroma", {}).get("ok"),
                    "elapsed_s": bk.get("elapsed_s"),
                    "out_dir": bk.get("out_dir"),
                    "rotated_removed": bk.get("rotated_removed", []),
                }
                logger.info(
                    "Sleep Cycle: backup done in %.1fs (%s rotated)",
                    bk.get("elapsed_s", 0),
                    len(bk.get("rotated_removed", [])),
                )
        except Exception as e:
            logger.exception("backup phase failed")
            report.errors.append(f"backup: {e}")

        # Phase 13 — Episodic memory indexer. Embeds the previous day's
        # conversations / dispatches / plans / focus / learnings /
        # notifications / perception events into the `episodic` ChromaDB
        # collection so the agent can semantically recall across time.
        # No-op when the worker is offline.
        try:
            import os as _os
            if _os.environ.get("KEE_SLEEP_EPISODIC", "1") not in ("0", "false", "off"):
                from kee.cognition.episodic_indexer import EpisodicIndexer
                ep = EpisodicIndexer()
                ep_result = await ep.index_window(window_days=7)
                if not ep_result.get("offline"):
                    report.stats["episodic"] = ep_result
                    logger.info(
                        "Sleep Cycle: episodic indexed %d events",
                        ep_result.get("indexed", 0),
                    )
        except Exception as e:
            logger.exception("episodic phase failed")
            report.errors.append(f"episodic: {e}")

        # Phase 12 — Worker re-index. If the Auctorum worker is online
        # (we can reach its health aggregator) AND the local vault has
        # `.md` files newer than the last_indexed_at marker, kick a
        # full re-index. Catches the "I edited 5 notes today on a flight
        # with no Tailscale" scenario — by morning the worker is back and
        # ChromaDB needs to catch up.
        try:
            import os as _os
            if _os.environ.get("KEE_SLEEP_REINDEX", "1") not in ("0", "false", "off"):
                from kee.cognition.worker_reindex import maybe_reindex
                ri = await maybe_reindex()
                if ri.get("ran"):
                    report.stats["worker_reindex"] = ri
                    logger.info("Sleep Cycle: re-indexed %d files",
                                ri.get("indexed", 0))
        except Exception as e:
            logger.exception("worker re-index phase failed")
            report.errors.append(f"worker_reindex: {e}")

        # Phase 11 — Stale-plan archival. Plans pending >30d that didn't
        # match any commits get auto-cancelled with outcome
        # "auto-archived: stale 30d+, never executed". Keeps the
        # plan_execution_rate metric meaningful (instead of being dragged
        # down by months-old "ship X" plans that are no longer relevant).
        try:
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE plan_history "
                    "SET executed = 1, "
                    "    executed_at = CURRENT_TIMESTAMP, "
                    "    outcome = COALESCE(outcome, "
                    "        'auto-archived: stale 30d+, never executed') "
                    "WHERE executed = 0 "
                    "AND timestamp <= datetime('now', '-30 days')"
                )
                report.stats["stale_plans_archived"] = cur.rowcount
                if cur.rowcount:
                    logger.info("Sleep Cycle: archived %d stale plans",
                                cur.rowcount)
        except Exception as e:
            logger.exception("stale-plan archival failed")
            report.errors.append(f"plan_archival: {e}")

        # Phase 9 — Tool-evolution rewrites. For tools whose schema keeps
        # confusing the LLM (kwarg_hallucination ≥ N hits in window), draft
        # a description rewrite proposal. Free Ollama call. Opt-out via env.
        try:
            import os as _os
            if _os.environ.get("KEE_SLEEP_TOOL_EVOLUTION", "1") not in ("0", "false", "off"):
                from kee.cognition import tool_evolution as te
                rewrites = await te.draft_rewrite_proposals(
                    llm=self.llm, window_days=7,
                )
                if rewrites:
                    report.stats["tool_evolution"] = {
                        "proposals": rewrites,
                        "count": len(rewrites),
                    }
                    logger.info(
                        "Sleep Cycle: %d tool-rewrite proposal(s) drafted",
                        len(rewrites),
                    )
        except Exception as e:
            logger.exception("tool-evolution phase failed")
            report.errors.append(f"tool_evolution: {e}")

        report.finished_at = datetime.now()
        self._last_report = report
        self.audit.log_event("sleep_cycle", report.to_dict())
        logger.info(
            "Sleep Cycle: done in %.1fs (summarized=%d, axioms=%d, "
            "proposal=%s, digest=%s, pruned=%s)",
            (report.finished_at - report.started_at).total_seconds(),
            report.summarized, len(report.axioms),
            bool(report.proposal_path), bool(report.digest_path),
            report.pruned_rows,
        )
        return report

    # ── Phase 1: summarize unsummarized conversations ─────────────────────
    async def _phase_summarize(self, hours: int = 24) -> int:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        with db.cursor() as cur:
            cur.execute(
                "SELECT id FROM conversations "
                "WHERE last_active >= ? AND (summary IS NULL OR summary = '') "
                "ORDER BY last_active ASC",
                (cutoff,),
            )
            ids = [row["id"] for row in cur.fetchall()]
        n = 0
        for cid in ids:
            try:
                if await self.memory.summarize_conversation(cid, self.llm):
                    n += 1
            except Exception:
                logger.debug("summarize skipped for %s", cid, exc_info=True)
        return n

    # ── Phase 2: derive stats from audit log ──────────────────────────────
    def _phase_stats(self, hours: int = 24) -> dict[str, Any]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        with db.cursor() as cur:
            cur.execute(
                "SELECT action, tool_name, success, "
                "strftime('%H', timestamp) AS hour "
                "FROM audit_log WHERE timestamp >= ?",
                (cutoff,),
            )
            rows = cur.fetchall()

            cur.execute(
                "SELECT COUNT(*) AS n FROM conversations WHERE last_active >= ?",
                (cutoff,),
            )
            convs = cur.fetchone()["n"]

            cur.execute(
                "SELECT COUNT(*) AS n FROM anomalies WHERE timestamp >= ?",
                (cutoff,),
            )
            anomalies = cur.fetchone()["n"]

        tool_calls = [r for r in rows if r["action"] == "tool_call"]
        tool_counter = Counter(r["tool_name"] for r in tool_calls)
        successes = sum(1 for r in tool_calls if r["success"])
        hours_counter = Counter(r["hour"] for r in rows if r["hour"] is not None)
        peak_hour = (
            int(hours_counter.most_common(1)[0][0]) if hours_counter else None
        )

        # New: kwarg hallucination roll-up so the axiom phase can spot
        # tools whose parameter schema confuses the LLM (and propose a
        # description rewrite). Bucketed by tool_name.
        halluc_rows = [r for r in rows if r["action"] == "kwarg_hallucination"]
        halluc_per_tool = Counter(r["tool_name"] for r in halluc_rows)
        # Symmetric counter: how often the LLM omitted required kwargs.
        # Different signal — usually means the tool description doesn't
        # make the requirement obvious.
        missing_rows = [r for r in rows
                        if r["action"] == "kwarg_missing_required"]
        missing_per_tool = Counter(r["tool_name"] for r in missing_rows)

        # New: cross-process conversation quality roll-up. Pulled from the
        # `conversation_qa` audit rows that `conversation_monitor.observe`
        # writes per turn. Fed into the digest so the morning brief can
        # admit "ayer mi voz score promedio fue 0.74".
        qa_rows = [r for r in rows if r["action"] == "conversation_qa"]
        qa_n = len(qa_rows)
        qa_avg: float | None = None
        if qa_n:
            try:
                with db.cursor() as cur2:
                    cur2.execute(
                        "SELECT parameters FROM audit_log "
                        "WHERE action='conversation_qa' "
                        "AND timestamp >= ?",
                        (cutoff,),
                    )
                    scores: list[float] = []
                    for r2 in cur2.fetchall():
                        try:
                            payload = json.loads(r2["parameters"] or "{}")
                            scores.append(float(payload.get("score") or 0))
                        except Exception:
                            continue
                    if scores:
                        qa_avg = round(sum(scores) / len(scores), 3)
            except Exception:
                pass

        # New: planned-vs-executed gap. If the agent keeps generating plans
        # but rarely marks them executed, that's a coordination smell — Sleep
        # Cycle can flag it in the digest.
        plan_total = 0
        plan_executed = 0
        try:
            with db.cursor() as cur2:
                cur2.execute(
                    "SELECT COUNT(*) AS n FROM plan_history "
                    "WHERE timestamp >= ?",
                    (cutoff,),
                )
                plan_total = cur2.fetchone()["n"]
                cur2.execute(
                    "SELECT COUNT(*) AS n FROM plan_history "
                    "WHERE timestamp >= ? AND executed = 1",
                    (cutoff,),
                )
                plan_executed = cur2.fetchone()["n"]
        except Exception:
            # Table might not exist on very old DBs.
            pass

        return {
            "window_hours": hours,
            "conversations": convs,
            "audit_rows": len(rows),
            "tool_calls": len(tool_calls),
            "tool_success_rate": (
                round(successes / len(tool_calls), 3) if tool_calls else None
            ),
            "tool_breakdown": dict(tool_counter.most_common(10)),
            "anomalies": anomalies,
            "peak_activity_hour": peak_hour,
            "heartbeat_events": sum(1 for r in rows if r["action"] == "heartbeat"),
            "kwarg_hallucinations": len(halluc_rows),
            "kwarg_hallucinations_per_tool": dict(halluc_per_tool.most_common(10)),
            "kwarg_missing_required": len(missing_rows),
            "kwarg_missing_per_tool": dict(missing_per_tool.most_common(10)),
            "plans_total": plan_total,
            "plans_executed": plan_executed,
            "plans_pending": max(0, plan_total - plan_executed),
            "plan_execution_rate": (
                round(plan_executed / plan_total, 3) if plan_total else None
            ),
            "qa_samples": qa_n,
            "qa_avg_score": qa_avg,
        }

    # ── Phase 3: LLM-derived qualitative axioms ───────────────────────────
    async def _phase_axioms(self, stats: dict[str, Any], hours: int = 24) -> list[str]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        with db.cursor() as cur:
            cur.execute(
                "SELECT summary FROM conversations "
                "WHERE last_active >= ? AND summary IS NOT NULL AND summary != '' "
                "ORDER BY last_active DESC LIMIT 20",
                (cutoff,),
            )
            summaries = [row["summary"] for row in cur.fetchall()]

        if not summaries and stats.get("tool_calls", 0) == 0:
            return []  # nothing happened today; no point bothering the LLM

        # If the LLM has been hallucinating kwargs on specific tools, surface
        # those at the top so the axiom phase can propose schema/description
        # rewrites in tomorrow's identity proposal.
        halluc_hint = ""
        per_tool = stats.get("kwarg_hallucinations_per_tool") or {}
        if per_tool:
            top = sorted(per_tool.items(), key=lambda kv: kv[1], reverse=True)[:3]
            halluc_hint = (
                "\n## Alucinación de parámetros (kwargs inventados)\n"
                + "\n".join(f"- {tool}: {n} veces" for tool, n in top)
                + "\n→ Si un patrón se repite, sugiere reescribir la "
                  "descripción del tool para evitarlo.\n"
            )

        prompt = (
            "Eres el módulo de Sleep Cycle de Kee. Acabas de revisar el día.\n\n"
            "## Estadísticas (últimas 24h)\n"
            f"{json.dumps(stats, indent=2, ensure_ascii=False)}\n\n"
            f"{halluc_hint}"
            "## Resúmenes de conversaciones (más recientes primero)\n"
            + "\n".join(f"- {s}" for s in summaries[:10])
            + "\n\nExtrae **máximo 5 axiomas** — observaciones concretas y "
            "accionables sobre el comportamiento de Armando hoy. Cada axioma "
            "debe ser una sola oración en español, sin filler. Ejemplos válidos:\n"
            "  - 'Armando prefiere que cite resultados reales antes que '"
            "narrar acciones que no ejecuté'\n"
            "  - 'Cuando dice usa X, no debo substituir X por otra herramienta'\n\n"
            "Responde SOLO con un array JSON de strings. Sin prosa adicional."
        )
        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": "Eres conciso. Solo JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                owner="sleep_cycle",
            )
        except Exception as e:
            logger.warning("axiom LLM call failed: %s", e)
            return []

        content = (response.content or "").strip()
        # Strip code-fence wrapper if the model added one.
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()
        try:
            parsed = json.loads(content)
            return [str(x).strip() for x in parsed if isinstance(x, str)][:5]
        except (json.JSONDecodeError, TypeError):
            # Fall back to line-based parse.
            lines = [
                ln.strip(" -*\t") for ln in content.splitlines() if ln.strip()
            ]
            return lines[:5]

    # ── Phase 4: persist behaviour model ──────────────────────────────────
    def _phase_update_behavior(
        self,
        stats: dict[str, Any],
        axioms: list[str],
    ) -> None:
        path = settings.vault_dir / "config" / "user_behavior.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {"_corrupt_backup": path.read_text(encoding="utf-8")[:2000]}

        updated = {
            "last_run": datetime.utcnow().isoformat(timespec="seconds"),
            "rolling_stats": stats,
            "axioms_recent": axioms,
            "history": existing.get("history", []),
        }
        # Keep last 14 daily snapshots in `history`.
        snapshot = {
            "date": date.today().isoformat(),
            "stats": stats,
            "axioms": axioms,
        }
        history = (existing.get("history", []) + [snapshot])[-14:]
        updated["history"] = history

        path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Phase 5: identity proposal (write-only, never apply) ──────────────
    async def _phase_propose_identity(
        self,
        axioms: list[str],
    ) -> str | None:
        if not axioms:
            return None
        # Cap at one proposal per day per the v2 spec — overwrite if exists.
        out_dir = settings.vault_dir / "_kee" / "identity_proposals"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{date.today().isoformat()}.md"

        # Lazy LLM-driven proposal: read soul.md so the model can suggest a
        # specific patch instead of generic platitudes.
        soul = settings.soul_path.read_text(encoding="utf-8") if settings.soul_path.exists() else ""

        prompt = (
            "Eres el módulo de Identity Evolution de Kee. Lee los axiomas "
            "extraídos hoy y soul.md actual. Propón **un único cambio "
            "mínimo** a soul.md que internalice los axiomas más fuertes. "
            "El cambio debe ser una adición de 1-3 líneas en una sección "
            "existente, o una nueva sección de 1-3 líneas. NO reemplaces "
            "secciones enteras. NO modifiques las reglas marcadas IMMUTABLE.\n\n"
            "## Axiomas de hoy\n"
            + "\n".join(f"- {a}" for a in axioms)
            + "\n\n## soul.md actual\n```markdown\n"
            + soul[:3000]
            + "\n```\n\n"
            "Responde con un único bloque markdown que contenga:\n"
            "  - Sección 'PROPUESTA' con el patch sugerido literal (markdown).\n"
            "  - Sección 'JUSTIFICACIÓN' con 1-2 oraciones explicando por qué.\n"
            "Nada más. Si no hay un cambio claro, responde la palabra SKIP."
        )

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": "Conservador. Pequeño. Reversible."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                owner="sleep_cycle",
            )
        except Exception as e:
            logger.warning("identity proposal LLM call failed: %s", e)
            return None

        content = (response.content or "").strip()
        if not content or content.upper().startswith("SKIP"):
            return None

        body = (
            f"# Identity proposal — {date.today().isoformat()}\n\n"
            f"_Generated by Sleep Cycle. **Not applied.** Review and apply by hand._\n\n"
            f"## Axioms feeding this proposal\n"
            + "\n".join(f"- {a}" for a in axioms)
            + "\n\n## Proposal\n\n"
            + content
            + "\n"
        )
        out_path.write_text(body, encoding="utf-8")
        return str(out_path)

    # ── Identity Evolution auto-apply (Phase 5 close-out) ─────────────────
    @staticmethod
    def list_proposals() -> list[dict[str, Any]]:
        """Return all generated proposal files with metadata + applied flag."""
        out: list[dict[str, Any]] = []
        d = settings.vault_dir / "_kee" / "identity_proposals"
        if not d.exists():
            return out
        for f in sorted(d.glob("*.md"), reverse=True):
            text = f.read_text(encoding="utf-8")
            applied = "**APPLIED**" in text or text.startswith("# APPLIED")
            out.append({
                "date": f.stem,
                "path": str(f),
                "bytes": len(text),
                "applied": applied,
            })
        return out

    @staticmethod
    def apply_proposal(proposal_date: str) -> dict[str, Any]:
        """Parse the PROPUESTA block out of a proposal file and append it to
        soul.md with a git-style commit annotation. Idempotent: marks the
        proposal as APPLIED so it can't be re-applied.

        Conservative: never replaces existing soul.md sections; appends to
        the end as a new "Sleep Cycle additions" section.
        """
        import re
        proposal_path = settings.vault_dir / "_kee" / "identity_proposals" / f"{proposal_date}.md"
        if not proposal_path.exists():
            return {"ok": False, "error": f"proposal {proposal_date} not found"}
        text = proposal_path.read_text(encoding="utf-8")
        if "**APPLIED**" in text:
            return {"ok": False, "error": "proposal already applied"}

        # Pull out the model's "PROPUESTA" block. The proposal LLM was
        # told to put the patch under that header. Be lenient — match
        # `## PROPUESTA` or `### PROPUESTA` or 'Proposal' (en).
        m = re.search(
            r"(?im)^#{2,3}\s*(?:PROPUESTA|PROPOSAL)\s*$\n+(.*?)(?=\n#{2,3}\s|\Z)",
            text, re.DOTALL,
        )
        if not m:
            return {"ok": False, "error": "could not find PROPUESTA block in proposal"}
        patch = m.group(1).strip()
        if not patch:
            return {"ok": False, "error": "empty patch"}

        # Strip code fences if the model wrapped the patch
        patch = re.sub(r"^```(?:markdown)?\s*\n?|\n?```\s*$", "", patch, flags=re.MULTILINE).strip()
        if not patch:
            return {"ok": False, "error": "patch became empty after fence strip"}

        # Append to soul.md under a date-stamped section
        soul = settings.soul_path
        if not soul.exists():
            return {"ok": False, "error": "soul.md not found"}
        current = soul.read_text(encoding="utf-8")
        marker = f"<!-- sleep-cycle-applied: {proposal_date} -->"
        if marker in current:
            return {"ok": False, "error": "soul.md already has marker for this date"}
        appended = (
            current.rstrip()
            + f"\n\n{marker}\n"
            + f"## Sleep Cycle addition — {proposal_date}\n\n"
            + f"_Auto-applied from `vault/_kee/identity_proposals/{proposal_date}.md`._\n\n"
            + patch
            + "\n"
        )
        soul.write_text(appended, encoding="utf-8")

        # Mark the proposal as applied
        annotated = (
            f"# APPLIED on {datetime.utcnow().isoformat()}Z — see soul.md marker `{marker}`\n\n"
            "**APPLIED** — this proposal was auto-applied to soul.md.\n\n---\n\n"
            + text
        )
        proposal_path.write_text(annotated, encoding="utf-8")

        # Best-effort git commit so the change is reversible
        commit_ok = False
        try:
            import subprocess
            r = subprocess.run(
                ["git", "-C", str(settings.project_root), "add",
                 str(soul.relative_to(settings.project_root)),
                 str(proposal_path.relative_to(settings.project_root))],
                capture_output=True, timeout=10,
            )
            if r.returncode == 0:
                msg = f"sleep-cycle: apply identity proposal {proposal_date}"
                r2 = subprocess.run(
                    ["git", "-C", str(settings.project_root), "commit",
                     "-m", msg, "--no-verify"],
                    capture_output=True, timeout=10,
                )
                commit_ok = (r2.returncode == 0)
        except Exception as e:
            logger.debug("git commit skipped: %s", e)

        return {
            "ok": True,
            "proposal_date": proposal_date,
            "soul_bytes_added": len(appended) - len(current),
            "git_committed": commit_ok,
            "patch_preview": patch[:300],
        }

    # ── Phase 6: cleanup old rows ─────────────────────────────────────────
    def _phase_cleanup(self) -> dict[str, int]:
        hb_cutoff = datetime.utcnow() - timedelta(days=self.prune_heartbeats_days)
        msg_cutoff = datetime.utcnow() - timedelta(days=self.prune_messages_days)
        result = {"heartbeats": 0, "messages": 0}
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM audit_log WHERE action = 'heartbeat' AND timestamp < ?",
                (hb_cutoff,),
            )
            result["heartbeats"] = cur.rowcount or 0
            cur.execute(
                "DELETE FROM messages WHERE created_at < ?",
                (msg_cutoff,),
            )
            result["messages"] = cur.rowcount or 0

        # Also archive workspaces older than 30 days.
        ws_root = settings.project_root / "workspaces"
        archived = 0
        if ws_root.exists():
            archive = ws_root / "_archive"
            for child in ws_root.iterdir():
                if child.name.startswith("_"):
                    continue
                if not child.is_dir():
                    continue
                age_days = (datetime.utcnow() - datetime.utcfromtimestamp(child.stat().st_mtime)).days
                if age_days > 30:
                    archive.mkdir(exist_ok=True)
                    try:
                        shutil.move(str(child), archive / child.name)
                        archived += 1
                    except Exception:
                        logger.debug("could not archive workspace %s", child, exc_info=True)
        result["workspaces_archived"] = archived
        return result

    # ── Phase 7: morning digest ───────────────────────────────────────────
    async def _phase_digest(self, report: SleepReport) -> str | None:
        out_dir = settings.vault_dir / "_kee" / "daily"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{date.today().isoformat()}.md"

        # Pull yesterday's narrative as ground truth — events the LLM
        # CANNOT invent. The prompt then asks for the human-voice
        # summary anchored on these real events.
        narrative_md = ""
        try:
            from kee.tools.narrate_day import tool as nd
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            nar = await nd.execute(date=yesterday)
            if nar.get("ok"):
                narrative_md = nar.get("markdown", "")[:3000]
        except Exception as e:
            logger.debug("narrate_day in digest failed: %s", e)

        prompt = (
            "Eres Kee. Acabas de procesar tu Sleep Cycle. Escribe un "
            "morning brief para Armando — un solo párrafo de 4-7 "
            "oraciones, en español, voz directa y sin filler. Cubre "
            "(en este orden si aplican): qué hice ayer, qué patrones "
            "observé en tu actividad, qué propongo para hoy, y un dato "
            "puntual si destaca.\n\n"
            f"## Eventos reales de ayer (NO inventar más allá de esto)\n"
            f"{narrative_md or '(sin eventos registrados)'}\n\n"
            f"## Stats agregados\n{json.dumps(report.stats, ensure_ascii=False)}\n\n"
            f"## Axiomas observados\n{json.dumps(report.axioms, ensure_ascii=False)}\n\n"
            f"Conversaciones resumidas: {report.summarized}\n"
            f"Pruned rows: {json.dumps(report.pruned_rows)}\n"
        )
        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": "Voz Kee. Directo. Spanish."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                owner="sleep_cycle",
            )
        except Exception as e:
            logger.warning("digest LLM call failed: %s", e)
            return None

        content = (response.content or "").strip()
        if not content:
            return None

        body = (
            f"# {date.today().isoformat()} — Morning brief\n\n"
            f"_Generated at {datetime.now().isoformat(timespec='seconds')}_\n\n"
            f"{content}\n\n"
            "---\n\n"
            f"## Stats raw\n```json\n{json.dumps(report.stats, indent=2, ensure_ascii=False)}\n```\n"
        )
        out_path.write_text(body, encoding="utf-8")
        return str(out_path)
