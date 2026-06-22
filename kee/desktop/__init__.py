"""Kee desktop application.

Native window shell that wraps the SvelteKit dashboard so Kee lives on the
screen as a real program — not just a browser tab. Two modes:

  * **HUD** — small frameless window pinned to the top-right corner. Always
    on top, transparent background, ~320x480. Shows live state + last few
    notifications + active conversation. Default mode when invoked from
    the wake-word.
  * **Full** — proper resizable application window with the entire dashboard
    (Chat, Cycle, Vault, Health, Settings, etc).

The app talks to the running Kee API at 127.0.0.1:7330 just like any other
client — same WebSocket stream, same REST endpoints. It never duplicates
state; it's a window onto the daemon.
"""
