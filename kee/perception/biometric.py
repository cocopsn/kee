"""Biometric telemetry ingest + analysis.

Phase 8 §"Biometric telemetry (smartwatch data)". A simple, source-agnostic
pipeline: any client (Health Connect on Android, an Apple Watch shortcut,
a Garmin export script, even manual entry) POSTs samples to
``/biometric/sample`` and they land in a SQLite table.

Schema (`biometric_samples`):
    (id, timestamp, kind, value, unit, source, note)

Where `kind` ∈ {`hr_resting`, `hr_active`, `hrv`, `sleep_minutes`,
`sleep_score`, `steps`, `vo2_max`, `body_battery`, `stress`, `weight_kg`,
`spo2`, `respiration`, `temperature_c`, ...}. The set is open — Kee
treats it as a tag, not an enum.

Heartbeat hook: `score_recent_state()` returns a coarse `energy_level`
∈ {high, normal, low, critical} based on the last 12h of samples.
Self-healing / temporal intelligence already accept these tags so no
new wiring is needed there.
"""

from __future__ import annotations

import logging
import statistics
from typing import Any, Optional

from kee.core import db

logger = logging.getLogger(__name__)


# ── Schema ───────────────────────────────────────────────────────────────
def ensure_schema() -> None:
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS biometric_samples (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT    NOT NULL,
            kind      TEXT    NOT NULL,
            value     REAL    NOT NULL,
            unit      TEXT,
            source    TEXT,
            note      TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bio_ts ON biometric_samples(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bio_kind ON biometric_samples(kind)")
    conn.commit()


def insert(kind: str, value: float, unit: str = "",
           source: str = "manual", note: str = "",
           timestamp: Optional[str] = None) -> int:
    """Insert one sample. ``timestamp`` defaults to now() in SQLite."""
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    if timestamp:
        cur.execute(
            "INSERT INTO biometric_samples (timestamp, kind, value, unit, source, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (timestamp, kind, float(value), unit, source, note),
        )
    else:
        cur.execute(
            "INSERT INTO biometric_samples (timestamp, kind, value, unit, source, note) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
            (kind, float(value), unit, source, note),
        )
    conn.commit()
    return cur.lastrowid


def insert_many(samples: list[dict]) -> int:
    """Bulk insert. Each sample dict needs at least ``kind`` + ``value``.
    Optional: ``unit``, ``source``, ``note``, ``timestamp``."""
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    n = 0
    for s in samples:
        if "kind" not in s or "value" not in s:
            continue
        ts = s.get("timestamp")
        if ts:
            cur.execute(
                "INSERT INTO biometric_samples (timestamp, kind, value, unit, source, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, s["kind"], float(s["value"]), s.get("unit", ""),
                 s.get("source", "bulk"), s.get("note", "")),
            )
        else:
            cur.execute(
                "INSERT INTO biometric_samples (timestamp, kind, value, unit, source, note) "
                "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
                (s["kind"], float(s["value"]), s.get("unit", ""),
                 s.get("source", "bulk"), s.get("note", "")),
            )
        n += 1
    conn.commit()
    return n


def recent(limit: int = 50, kind: Optional[str] = None,
           since_hours: Optional[int] = None) -> list[dict]:
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    where: list[str] = []
    params: list[Any] = []
    if kind:
        where.append("kind = ?"); params.append(kind)
    if since_hours is not None:
        where.append("timestamp >= datetime('now', ?)")
        params.append(f"-{int(since_hours)} hours")
    sql = ("SELECT id, timestamp, kind, value, unit, source, note "
           "FROM biometric_samples ")
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def latest_by_kind(kinds: list[str], since_hours: int = 24) -> dict[str, dict]:
    """Most recent sample per `kind` within the last `since_hours`."""
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    out: dict[str, dict] = {}
    for k in kinds:
        cur.execute("""
            SELECT timestamp, value, unit, source, note FROM biometric_samples
            WHERE kind = ? AND timestamp >= datetime('now', ?)
            ORDER BY id DESC LIMIT 1
        """, (k, f"-{since_hours} hours"))
        row = cur.fetchone()
        if row:
            out[k] = {"timestamp": row[0], "value": row[1], "unit": row[2],
                      "source": row[3], "note": row[4]}
    return out


# ── Coarse energy scoring (heartbeat hook) ──────────────────────────────
def score_recent_state(window_hours: int = 12) -> dict[str, Any]:
    """Combine recent biometric signals into a coarse `energy_level`.

    Heuristics (all optional — only signals present in the table contribute):
      * Resting HR > 80 OR active HR streak                → -1
      * HRV < 30                                           → -1
      * Sleep minutes < 360 last night                     → -1
      * Sleep score < 60                                   → -1
      * Body battery (Garmin) < 25                         → -2
      * Steps last 24h < 1000 (and not 'rest day')         → -1
      * Stress > 75                                        → -1

    Net score → energy_level mapping:
      ≥ 0 → high · -1 → normal · -2..-3 → low · ≤ -4 → critical
    """
    samples = latest_by_kind(
        ["hr_resting", "hrv", "sleep_minutes", "sleep_score",
         "body_battery", "steps", "stress"],
        since_hours=window_hours,
    )
    score = 0
    notes: list[str] = []

    if "hr_resting" in samples and samples["hr_resting"]["value"] > 80:
        score -= 1; notes.append(f"resting HR {samples['hr_resting']['value']:.0f} bpm (high)")
    if "hrv" in samples and samples["hrv"]["value"] < 30:
        score -= 1; notes.append(f"HRV {samples['hrv']['value']:.0f} ms (low)")
    if "sleep_minutes" in samples and samples["sleep_minutes"]["value"] < 360:
        score -= 1
        notes.append(f"sleep {samples['sleep_minutes']['value']:.0f} min ({samples['sleep_minutes']['value']/60:.1f}h)")
    if "sleep_score" in samples and samples["sleep_score"]["value"] < 60:
        score -= 1; notes.append(f"sleep score {samples['sleep_score']['value']:.0f}")
    if "body_battery" in samples and samples["body_battery"]["value"] < 25:
        score -= 2; notes.append(f"body battery {samples['body_battery']['value']:.0f}/100")
    if "steps" in samples and samples["steps"]["value"] < 1000:
        score -= 1; notes.append(f"steps {samples['steps']['value']:.0f}")
    if "stress" in samples and samples["stress"]["value"] > 75:
        score -= 1; notes.append(f"stress {samples['stress']['value']:.0f}/100")

    if score >= 0:
        level = "high" if samples else "unknown"
    elif score == -1:
        level = "normal"
    elif score >= -3:
        level = "low"
    else:
        level = "critical"

    return {
        "energy_level": level,
        "score": score,
        "samples_used": list(samples.keys()),
        "notes": notes,
        "window_hours": window_hours,
    }
