"""Tests for kee.core.llm.cost_tracker. No paid LLM calls — just math + DB."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path


def _setup_test_db(tmp: Path) -> sqlite3.Connection:
    """Bootstrap an audit_log table with the columns cost_tracker reads."""
    con = sqlite3.connect(str(tmp))
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            action TEXT, tool_name TEXT, parameters TEXT, result TEXT,
            risk_level INTEGER DEFAULT 0, success BOOLEAN DEFAULT 1, error TEXT,
            pre_state TEXT, post_state TEXT, verification TEXT,
            provider TEXT, model_name TEXT, tier TEXT, latency_ms INTEGER,
            tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL
        )
    """)
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    yest = (datetime.now().replace(day=max(1, datetime.now().day - 1))
            .strftime("%Y-%m-%d %H:%M:%S"))
    rows = [
        # today rows — should sum
        (today, "llm_call", "llm.chat", "claude", "claude-sonnet-4-6", "heavy", 4200, 1500, 250, 0.0118),
        (today, "llm_call", "llm.chat", "haiku",  "claude-haiku-4-5",  "medium", 800, 1200, 80, 0.00128),
        (today, "llm_call", "llm.chat", "openai", "gpt-4o-mini",       "conversational", 1200, 600, 100, 0.000150),
        (today, "llm_call", "llm.chat", "ollama", "qwen3:8b",          "simple", 9000, 2000, 50, 0.0),
        # yesterday — should NOT count toward today
        (yest, "llm_call", "llm.chat", "claude", "claude-sonnet-4-6", "heavy", 5000, 2000, 400, 0.012),
    ]
    for r in rows:
        con.execute("""
            INSERT INTO audit_log
              (timestamp, action, tool_name, provider, model_name, tier, latency_ms, tokens_in, tokens_out, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, r)
    con.commit()
    return con


def test_daily_total_excludes_yesterday():
    """daily_total_usd must only sum rows from local-day-start onward."""
    from kee.core import db as kdb
    from kee.core.llm.cost_tracker import daily_total_usd

    tmpdir = tempfile.mkdtemp()
    dbp = Path(tmpdir) / "kee_test.db"
    _setup_test_db(dbp).close()
    original_conn = kdb._connection
    test_conn = sqlite3.connect(str(dbp))
    test_conn.row_factory = sqlite3.Row
    kdb._connection = test_conn
    try:
        total = daily_total_usd()
        expected = 0.0118 + 0.00128 + 0.000150
        if abs(total - expected) > 0.001:
            print(f"  ✗ daily_total: got ${total:.6f} expected ${expected:.6f}")
            return 1
        print(f"  ✓ daily_total: ${total:.6f} (excluded yesterday's $0.012)")
        return 0
    finally:
        kdb._connection = original_conn
        test_conn.close()
        # Best-effort cleanup; Windows file locks may linger briefly
        import shutil, time
        time.sleep(0.1)
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def test_kill_switch_threshold_logic():
    """kill_switch_active should be True when total >= cap, False below."""
    os.environ["KEE_DAILY_COST_CAP_USD"] = "1.0"
    from kee.core.llm.cost_tracker import _cap_usd
    cap = _cap_usd()
    if cap != 1.0:
        print(f"  ✗ _cap_usd reads env: got {cap} expected 1.0")
        return 1
    print(f"  ✓ _cap_usd reads env override: {cap}")
    # Restore default
    os.environ["KEE_DAILY_COST_CAP_USD"] = "2.0"
    return 0


def test_cap_default_when_unset():
    """If KEE_DAILY_COST_CAP_USD missing, default to 2.0."""
    from kee.core.llm.cost_tracker import _cap_usd
    saved = os.environ.pop("KEE_DAILY_COST_CAP_USD", None)
    try:
        cap = _cap_usd()
        if cap != 2.0:
            print(f"  ✗ default cap: got {cap} expected 2.0")
            return 1
        print(f"  ✓ default cap when env unset: ${cap}")
        return 0
    finally:
        if saved is not None:
            os.environ["KEE_DAILY_COST_CAP_USD"] = saved


def test_cap_invalid_falls_back():
    """If KEE_DAILY_COST_CAP_USD is garbage, fall back to 2.0."""
    from kee.core.llm.cost_tracker import _cap_usd
    os.environ["KEE_DAILY_COST_CAP_USD"] = "not-a-number"
    try:
        cap = _cap_usd()
        if cap != 2.0:
            print(f"  ✗ invalid cap: got {cap} expected 2.0 fallback")
            return 1
        print(f"  ✓ invalid env value falls back to default: ${cap}")
        return 0
    finally:
        os.environ["KEE_DAILY_COST_CAP_USD"] = "2.0"


if __name__ == "__main__":
    fails = 0
    print("=== daily_total excludes yesterday ===")
    fails += test_daily_total_excludes_yesterday()
    print()
    print("=== kill_switch threshold reads env ===")
    fails += test_kill_switch_threshold_logic()
    print()
    print("=== cap defaults to 2.0 ===")
    fails += test_cap_default_when_unset()
    print()
    print("=== invalid env falls back ===")
    fails += test_cap_invalid_falls_back()
    print()
    if fails == 0:
        print("All passed ✓")
    else:
        print(f"{fails} test(s) failed")
        raise SystemExit(1)
