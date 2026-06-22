"""Smart audio WS routing — no LLM, no network, $0.

Verifies the BATCH 5 change to `_broadcast()`:
  - Default events go to every subscriber
  - `audio_only=True` skips subscribers with `wants_audio: False`
  - Full queue is silently dropped (no exception bubbles)

Run::

    .venv\\Scripts\\python.exe tests/test_audio_routing.py
"""

from __future__ import annotations

import asyncio


def _setup() -> None:
    """Reset the module-level queue list so tests don't leak into each other."""
    from kee.surfaces import api
    api._STREAM_QUEUES.clear()


def _add(wants_audio: bool, *, maxsize: int = 100) -> "asyncio.Queue":
    from kee.surfaces import api
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    api._STREAM_QUEUES.append({
        "queue": q, "wants_audio": wants_audio, "device_type": "test",
    })
    return q


def test_audio_only_skips_browser() -> int:
    from kee.surfaces import api
    _setup()
    browser = _add(wants_audio=False)
    hud = _add(wants_audio=True)
    api._broadcast({"type": "voice_audio_chunk", "index": 0}, audio_only=True)
    if browser.empty() and hud.qsize() == 1:
        print("  [ok] audio-only event skipped non-audio subscriber")
        return 0
    print(f"  [FAIL] browser={browser.qsize()} hud={hud.qsize()}")
    return 1


def test_default_event_reaches_all() -> int:
    from kee.surfaces import api
    _setup()
    a = _add(wants_audio=False)
    b = _add(wants_audio=True)
    api._broadcast({"type": "audit", "ok": True})
    if a.qsize() == 1 and b.qsize() == 1:
        print("  [ok] non-audio event fanned out to all subscribers")
        return 0
    print(f"  [FAIL] a={a.qsize()} b={b.qsize()}")
    return 1


def test_full_queue_drops_silently() -> int:
    from kee.surfaces import api
    _setup()
    q = _add(wants_audio=True, maxsize=2)
    # Fill it
    q.put_nowait({"type": "x", "i": 0})
    q.put_nowait({"type": "x", "i": 1})
    try:
        api._broadcast({"type": "x", "i": 2})
    except Exception as e:
        print(f"  [FAIL] broadcast raised on full queue: {e!r}")
        return 1
    # Queue should still hold exactly two items, third dropped.
    if q.qsize() == 2:
        print("  [ok] full queue overflow dropped silently")
        return 0
    print(f"  [FAIL] expected qsize=2, got {q.qsize()}")
    return 1


def test_auto_classify_voice_audio() -> int:
    """voice_audio_* events should auto-route to audio subscribers only,
    even without an explicit audio_only=True flag."""
    from kee.surfaces import api
    _setup()
    browser = _add(wants_audio=False)
    hud = _add(wants_audio=True)
    api._broadcast({"type": "voice_audio_chunk", "index": 0})  # no flag
    if browser.empty() and hud.qsize() == 1:
        print("  [ok] voice_audio_* auto-classified as audio-only")
        return 0
    print(f"  [FAIL] browser={browser.qsize()} hud={hud.qsize()}")
    return 1


def test_audio_only_false_overrides_autoclassify() -> int:
    """Explicit audio_only=False should fan out even voice_audio_* events."""
    from kee.surfaces import api
    _setup()
    browser = _add(wants_audio=False)
    hud = _add(wants_audio=True)
    api._broadcast({"type": "voice_audio_chunk", "index": 0}, audio_only=False)
    if browser.qsize() == 1 and hud.qsize() == 1:
        print("  [ok] explicit audio_only=False overrides auto-classify")
        return 0
    print(f"  [FAIL] browser={browser.qsize()} hud={hud.qsize()}")
    return 1


def test_disconnected_entry_not_revisited() -> int:
    """If an entry is removed mid-iteration, broadcast still completes."""
    from kee.surfaces import api
    _setup()
    a = _add(wants_audio=True)
    b = _add(wants_audio=True)
    # Remove one before broadcasting; broadcast should still hit the other.
    api._STREAM_QUEUES.pop(0)
    api._broadcast({"type": "x"})
    if a.empty() and b.qsize() == 1:
        print("  [ok] removed subscriber not visited; remainder served")
        return 0
    print(f"  [FAIL] a={a.qsize()} b={b.qsize()}")
    return 1


if __name__ == "__main__":
    print("=== smart audio WS routing ===")
    fails = 0
    fails += test_audio_only_skips_browser()
    fails += test_default_event_reaches_all()
    fails += test_full_queue_drops_silently()
    fails += test_auto_classify_voice_audio()
    fails += test_audio_only_false_overrides_autoclassify()
    fails += test_disconnected_entry_not_revisited()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
