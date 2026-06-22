"""Run the entire $0 regression suite in one shot.

No LLM calls, no network. Just calls each test_*.py as a subprocess and
sums the exit codes. Used as the pre-commit / pre-deploy gate.

Run::

    .venv\\Scripts\\python.exe tests/run_all.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS = [
    "test_strip_helpers.py",
    "test_terminal_helpers.py",
    "test_router_parser.py",
    "test_chain_ordering.py",
    "test_cost_tracker.py",
    "test_response_qa.py",
    "test_audio_routing.py",
    "test_user_patterns.py",
    "test_quality_snapshot.py",
    "test_tool_schemas.py",
    "test_keecode.py",
    "test_hallucination_loop.py",
    "test_tool_evolution.py",
    "test_recall.py",
    "test_plan_history.py",
    "test_wilson.py",
    "test_missing_required.py",
    "test_reflect.py",
    "test_inbox_triage.py",
    "test_cognitive_heartbeat.py",
    "test_commits.py",
    "test_focus.py",
    "test_notification_router.py",
    "test_plan_linker.py",
    "test_schedule_self.py",
    "test_learn.py",
    "test_projects.py",
    "test_worker_health.py",
    "test_real_rag.py",   # skipped unless KEE_TEST_REAL_RAG=1
    "test_episodic_indexer.py",
    "test_narrate_day.py",
    "test_backup.py",
    "test_self_correction.py",
    "test_voice_streaming.py",
    "test_compare_days.py",
    "test_recap_week.py",
    "test_apply_rewrite.py",
]


def main() -> int:
    here = Path(__file__).parent
    failures: list[str] = []
    # Force UTF-8 stdio so the older suites (which print ✓/✗) don't blow up
    # on Windows' default cp1252 console.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for name in TESTS:
        path = here / name
        if not path.exists():
            print(f"--- SKIP {name} (not present) ---")
            continue
        print(f"\n=== {name} ===", flush=True)
        rc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(here.parent),
            env=env,
        ).returncode
        if rc != 0:
            failures.append(name)
    print()
    if failures:
        print(f"FAIL: {len(failures)} suite(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK: {len(TESTS)} suite(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
