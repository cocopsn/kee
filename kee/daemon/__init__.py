"""Kee daemon orchestration.

The supervisor launches every Kee surface (api, telegram, voice, notif-bridge,
heartbeat, sleep-cycle) as a child process, restarts crashed children with
exponential backoff, streams each surface's output to its own log file in
``data/`` (the same paths the dashboard's Health page already reads), and
persists status to ``data/supervisor_state.json`` so the dashboard can render
who's alive without poking the OS.

This is what makes Kee a *resident identity* on the machine, independent of
whether anyone has the dashboard open.
"""
