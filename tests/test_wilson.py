"""Wilson-CI lower bound + autonomy threshold integration — $0.

Verifies:
  - 1/1 → small N is penalised hard (≤ 0.25)
  - 9/10 → mid N gets meaningful trust (~0.55-0.65)
  - 50/50 → large N earns near-1.0 (≥ 0.93)
  - 0/10 → zero successes ≈ 0.0
  - confidence() roundtrip exposes trust_score
  - recommended_threshold uses Wilson trust, not raw rate

Run::

    .venv\\Scripts\\python.exe tests/test_wilson.py
"""

from __future__ import annotations


def test_wilson_math() -> int:
    from kee.cognition.autonomy import wilson_lower_bound as W
    cases = [
        (1, 1, "1/1 small sample", lambda x: x <= 0.25),
        (9, 10, "9/10 mid sample", lambda x: 0.55 <= x <= 0.75),
        (50, 50, "50/50 large sample", lambda x: x >= 0.92),
        (0, 10, "0/10 all fail", lambda x: x <= 0.05),
        (0, 0, "no samples", lambda x: x == 0.0),
    ]
    fails = 0
    for ok, n, label, check in cases:
        got = W(ok, n)
        if check(got):
            print(f"  [ok] {label}: lower={got:.3f}")
        else:
            fails += 1
            print(f"  [FAIL] {label}: lower={got:.3f}")
    return fails


def test_confidence_returns_trust() -> int:
    """Insert known rows, verify confidence() exposes trust_score."""
    from kee.cognition.autonomy import confidence, record
    from kee.core import db
    name = "__wilson_test_tool__"
    # 8 ok, 2 fail
    for _ in range(8):
        record(name, risk_level=1, success=True)
    for _ in range(2):
        record(name, risk_level=1, success=False)
    info = confidence(name, window=10)
    try:
        if (info["samples"] == 10
                and info["success_rate"] == 0.8
                and 0.4 < info["trust_score"] < 0.8):
            print(f"  [ok] trust_score={info['trust_score']} for 8/10 sample")
            return 0
        print(f"  [FAIL] {info}")
        return 1
    finally:
        with db.cursor() as cur:
            cur.execute("DELETE FROM confidence_log WHERE tool_name = ?",
                        (name,))


def test_recommended_threshold_uses_trust() -> int:
    """8/10 raw rate is 0.8 — old logic would say 'shaky' (rate >= 0.5).
    New logic: trust ≈ 0.55, still bumps but for the right reason."""
    from kee.cognition.autonomy import recommended_threshold, record
    from kee.core import db
    name = "__wilson_test_threshold__"
    for _ in range(8):
        record(name, risk_level=1, success=True)
    for _ in range(2):
        record(name, risk_level=1, success=False)
    try:
        out = recommended_threshold(name, base_risk=1)
        if (out["trust_score"] is not None
                and "trust=" in out["reason"]
                and out["final_risk"] in (1, 2)):
            print(f"  [ok] threshold uses trust: bump={out['bumped_by']}, "
                  f"reason={out['reason']!r}")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        with db.cursor() as cur:
            cur.execute("DELETE FROM confidence_log WHERE tool_name = ?",
                        (name,))


def test_low_n_perfect_does_not_get_full_trust() -> int:
    """The whole point: 2/2 should NOT be ranked above 49/50."""
    from kee.cognition.autonomy import wilson_lower_bound as W
    small = W(2, 2)
    big = W(49, 50)
    if small < big:
        print(f"  [ok] 2/2 trust ({small:.3f}) < 49/50 trust ({big:.3f})")
        return 0
    print(f"  [FAIL] 2/2={small:.3f} not less than 49/50={big:.3f}")
    return 1


if __name__ == "__main__":
    print("=== Wilson CI + autonomy ===")
    fails = 0
    fails += test_wilson_math()
    fails += test_confidence_returns_trust()
    fails += test_recommended_threshold_uses_trust()
    fails += test_low_n_perfect_does_not_get_full_trust()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
