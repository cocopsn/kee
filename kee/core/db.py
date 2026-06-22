"""SQLite layer.

Provides a single connection to `kee.db`, runs schema migrations on startup,
and exposes a cursor context manager. Stdlib `sqlite3` only — every query
in Kee is small enough that an async driver buys nothing, and the agent
loop is I/O-bound on the LLM call, not the DB.

Schema includes hooks added by the Phase 0 hardening patches:
  * audit_log.pre_state / post_state / verification — verification loop
  * tool_registry.probationary — created via create_tool, GC-eligible
  * anomalies                — anomaly events from verification failures
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kee.config import settings

logger = logging.getLogger(__name__)

_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active   TIMESTAMP,
    summary       TEXT,
    token_count   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT REFERENCES conversations(id),
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    tool_name       TEXT,
    tool_result     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action        TEXT NOT NULL,
    tool_name     TEXT NOT NULL,
    parameters    TEXT,
    result        TEXT,
    risk_level    INTEGER DEFAULT 0,
    success       BOOLEAN DEFAULT 1,
    error         TEXT,
    pre_state     TEXT,
    post_state    TEXT,
    verification  TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON audit_log(timestamp);

-- Inbound + outbound notifications. Any source can POST to /notifications/inbound.
-- Outbound rows are written by `notify_user()` for traceability.
CREATE TABLE IF NOT EXISTS notifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    direction     TEXT NOT NULL,        -- 'inbound' | 'outbound'
    source        TEXT NOT NULL,        -- 'whatsapp' | 'slack' | 'system' | 'tool_created' | etc.
    title         TEXT,
    body          TEXT NOT NULL,
    urgency       INTEGER DEFAULT 1,    -- 0=low 1=normal 2=critical
    handled       BOOLEAN DEFAULT 0,
    metadata      TEXT
);

CREATE INDEX IF NOT EXISTS idx_notifications_recent
    ON notifications(handled, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_source
    ON notifications(source);

CREATE TABLE IF NOT EXISTS anomalies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    audit_id      INTEGER REFERENCES audit_log(id),
    tool_name     TEXT NOT NULL,
    kind          TEXT NOT NULL,        -- 'unexpected_change' | 'rollback' | 'sandbox_fail'
    detail        TEXT,
    severity      INTEGER DEFAULT 1     -- 1..3
);

CREATE TABLE IF NOT EXISTS task_ledger (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scheduled_for   TIMESTAMP,
    completed_at    TIMESTAMP,
    command         TEXT NOT NULL,
    result          TEXT,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS tool_registry (
    name              TEXT PRIMARY KEY,
    description       TEXT NOT NULL,
    parameters_schema TEXT NOT NULL,
    source            TEXT NOT NULL,
    file_path         TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used         TIMESTAMP,
    use_count         INTEGER DEFAULT 0,
    risk_level        INTEGER DEFAULT 0,
    probationary      BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS goals (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT,
    deadline      TIMESTAMP,
    status        TEXT DEFAULT 'active',
    progress_pct  INTEGER DEFAULT 0,
    milestones    TEXT,
    project       TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP
);

-- World Model: causal graph of entities Coco operates on. Used by the
-- agent's Impact Assessment to reason about cascading effects before
-- acting. v2 §III Gap 2 + §III Gap 8.
CREATE TABLE IF NOT EXISTS world_entities (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,    -- 'project'|'person'|'system'|'service'|'metric'|'tool'
    state         TEXT,             -- JSON
    criticality   INTEGER DEFAULT 5,-- 1 (low) .. 10 (mission-critical)
    notes         TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS world_relations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT REFERENCES world_entities(id) ON DELETE CASCADE,
    target_id     TEXT REFERENCES world_entities(id) ON DELETE CASCADE,
    relation      TEXT NOT NULL,    -- 'depends_on'|'affects'|'generates'|'blocks'|'owns'|'uses'
    weight        REAL DEFAULT 1.0, -- 0..1, edge strength
    description   TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, target_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_world_relations_source
    ON world_relations(source_id);
CREATE INDEX IF NOT EXISTS idx_world_relations_target
    ON world_relations(target_id);

-- Internal Economy: per-tool / per-call cost ledger. Phase 5 §III Gap 10.
-- We log every call that has a measurable monetary cost (claude_code's
-- `total_cost_usd`, future Anthropic SDK calls if any, paid APIs).
-- Free local LLM calls are NOT logged here — they're in audit_log.
CREATE TABLE IF NOT EXISTS cost_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tool_name     TEXT NOT NULL,
    cost_usd      REAL NOT NULL,
    model         TEXT,                -- e.g. 'claude-sonnet-4-7' if known
    duration_ms   INTEGER,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    task_summary  TEXT,                -- short note: what the spend was for
    audit_id      INTEGER REFERENCES audit_log(id)
);

CREATE INDEX IF NOT EXISTS idx_cost_ledger_timestamp
    ON cost_ledger(timestamp);

-- Per-tool confidence trail. Phase 5 §III Gap 11 (Dynamic Autonomy
-- Threshold). The autonomy module reads recent rows here to decide
-- whether to escalate a borderline call to "ask first".
CREATE TABLE IF NOT EXISTS confidence_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tool_name     TEXT NOT NULL,
    risk_level    INTEGER DEFAULT 0,
    success       BOOLEAN DEFAULT 1,
    user_corrected BOOLEAN DEFAULT 0  -- set when the user pushed back on the result
);

CREATE INDEX IF NOT EXISTS idx_confidence_tool
    ON confidence_log(tool_name, timestamp);

-- Plan history. Every MultiPathPlanner run lands here so the agent can
-- recall prior plans for similar tasks, Sleep Cycle can compute the
-- "planned vs executed" gap, and the dashboard can render a planner
-- timeline. `executed` flips when the agent (or the user) marks the plan
-- as carried out via the `plan` tool's `mark_executed` action.
CREATE TABLE IF NOT EXISTS plan_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    task            TEXT NOT NULL,
    context         TEXT,
    selected_json   TEXT,        -- the winning plan as JSON
    alternatives_json TEXT,      -- runners-up as JSON
    world_entity    TEXT,
    world_impact    REAL,        -- numeric impact_score from world_model
    executed        INTEGER DEFAULT 0,
    executed_at     TIMESTAMP,
    outcome         TEXT          -- free-form note when marked executed
);

CREATE INDEX IF NOT EXISTS idx_plan_history_ts
    ON plan_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_plan_history_executed
    ON plan_history(executed, timestamp);

-- Focus sessions. The agent (or Coco directly) declares "I'm working on
-- X for the next N minutes"; the heartbeat watches active-window /
-- commits and bumps `drift_count` if attention strays. There is at most
-- ONE active session at a time (ended_at IS NULL).
CREATE TABLE IF NOT EXISTS focus_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    project     TEXT NOT NULL,
    intent      TEXT,
    deadline    TIMESTAMP,
    ended_at    TIMESTAMP,
    outcome     TEXT,
    drift_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_focus_active
    ON focus_sessions(ended_at, started_at);

-- Scheduled callbacks. Lightweight reminders the agent (or Coco) sets;
-- the heartbeat polls this every tick and fires `fired=0` rows whose
-- `fire_at <= now`. `kind` is free-form ('reminder', 'check_deploy',
-- 'morning_brief', etc.); `payload` is a JSON blob the heartbeat passes
-- to the agent as the prompt body when firing.
CREATE TABLE IF NOT EXISTS scheduled_callbacks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fire_at     TIMESTAMP NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT,
    fired       INTEGER DEFAULT 0,
    fired_at    TIMESTAMP,
    cancelled   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_callbacks_due
    ON scheduled_callbacks(fired, cancelled, fire_at);

-- Persistent learnings — durable knowledge nuggets the agent (or Coco)
-- explicitly chooses to remember. Distinct from `messages` (transcript)
-- and `vault/notes` (long-form). Use case: "always pin the version of
-- node-globals at D:/Kee/node-globals" or "Coco prefers Sonnet for
-- code review, not Haiku".
CREATE TABLE IF NOT EXISTS learnings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    topic         TEXT NOT NULL,
    content       TEXT NOT NULL,
    source_msg_id INTEGER,
    reinforced    INTEGER DEFAULT 1,
    forgotten     INTEGER DEFAULT 0,
    forgotten_at  TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_learnings_topic
    ON learnings(topic, forgotten);
"""

# For databases that already exist from earlier runs, add any new columns
# missing from older schemas. Each entry is (table, column, definition).
_ADDITIVE_MIGRATIONS = (
    ("audit_log", "pre_state", "TEXT"),
    ("audit_log", "post_state", "TEXT"),
    ("audit_log", "verification", "TEXT"),
    ("tool_registry", "probationary", "BOOLEAN DEFAULT 0"),
    # LLM provenance + cost (multi-provider chain + router tier)
    ("audit_log", "provider", "TEXT"),
    ("audit_log", "model_name", "TEXT"),
    ("audit_log", "tier", "TEXT"),
    ("audit_log", "latency_ms", "INTEGER"),
    ("audit_log", "tokens_in", "INTEGER"),
    ("audit_log", "tokens_out", "INTEGER"),
    ("audit_log", "cost_usd", "REAL"),
)


_lock = threading.Lock()
_connection: sqlite3.Connection | None = None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDITIVE_MIGRATIONS:
        try:
            if not _column_exists(conn, table, column):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                logger.info("Migration: added %s.%s", table, column)
        except sqlite3.OperationalError as e:
            logger.warning("Migration skipped (%s.%s): %s", table, column, e)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,  # autocommit; explicit transactions where needed
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def get_connection() -> sqlite3.Connection:
    """Lazy singleton connection. Thread-safe via check_same_thread=False."""
    global _connection
    with _lock:
        if _connection is None:
            logger.info("Opening SQLite at %s", settings.db_path)
            _connection = _connect(settings.db_path)
        return _connection


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()


def close() -> None:
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None
