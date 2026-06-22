"""Inbox triage classification heuristics — $0.

Pure unit test of `_classify()` against representative sender/subject
combinations. No Gmail call needed.

Run::

    .venv\\Scripts\\python.exe tests/test_inbox_triage.py
"""

from __future__ import annotations


CASES = [
    # (from, subject, expected_category)
    ("Vercel <noreply@vercel.com>", "Your invoice is ready",     "billing"),
    ("Stripe <invoices@stripe.com>", "Receipt for May",           "billing"),
    ("GitHub <noreply@github.com>", "New PR opened",              "github"),
    ("Sandra <sandra@auctorum.com>", "Cliente pendiente",         "work"),
    ("LinkedIn <noreply@linkedin.com>", "Your weekly summary",    "social"),
    ("Random <foo@example.com>", "URGENT: server down!",          "urgent"),
    ("Calendly <noreply@calendly.com>", "Meeting reminder",       "calendar"),
    ("Anyone <foo@example.com>", "Verify your email — code 4829", "auth_codes"),
    ("Marketing <hello@mailchimp.com>", "Newsletter #42",         "marketing"),
    ("Random <foo@example.com>", "newsletter",                    "other"),
    ("No Reply <noreply@somecompany.com>", "Reset password",      "noreply"),
]


def test_classify_buckets() -> int:
    from kee.tools.inbox_triage import _classify
    fails = 0
    for sender, subject, expected in CASES:
        got = _classify({"from": sender, "subject": subject})
        if got == expected:
            print(f"  [ok] {sender[:30]:30s} -> {got}")
        else:
            fails += 1
            print(f"  [FAIL] {sender[:30]:30s} '{subject[:30]}' -> "
                  f"{got} (expected {expected})")
    return fails


def test_unknown_falls_to_other() -> int:
    from kee.tools.inbox_triage import _classify
    got = _classify({"from": "Stranger <abc@xyzweird.io>",
                     "subject": "hola"})
    if got == "other":
        print("  [ok] unknown sender -> other")
        return 0
    print(f"  [FAIL] expected 'other', got {got!r}")
    return 1


def test_no_from_header_safe() -> int:
    from kee.tools.inbox_triage import _classify
    got = _classify({"from": None, "subject": None})
    if got == "other":
        print("  [ok] None inputs handled safely")
        return 0
    print(f"  [FAIL] {got}")
    return 1


if __name__ == "__main__":
    print("=== inbox_triage ===")
    fails = 0
    fails += test_classify_buckets()
    fails += test_unknown_falls_to_other()
    fails += test_no_from_header_safe()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
