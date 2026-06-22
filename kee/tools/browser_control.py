"""Browser automation tool — Playwright on Chromium.

Used by the agent to verify deploys, fill forms, scrape content, drive
admin dashboards. The browser is a singleton so multiple turns reuse the
same Chromium instance — opening a fresh browser per call burns ~2 s.

Default mode is **headed** so Coco can see what Kee is doing during dev.
Set `KEE_BROWSER_HEADLESS=1` to flip it for production / unattended runs.

Risk: 1 (visible side effects, can submit forms, can navigate anywhere).
The agent should treat any action that submits a form or hits an external
service as risk 2 in spirit and confirm.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


# Override Playwright's browser cache path so Chromium lives on D:/ alongside
# everything else. Set BEFORE the playwright module probes for binaries.
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(settings.models_dir / "playwright"),
)


class _BrowserSingleton:
    """Module-level Chromium that survives across tool calls."""

    _pw = None
    _browser = None
    _context = None
    _page = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_page(cls):
        async with cls._lock:
            if cls._page is not None and not cls._page.is_closed():
                return cls._page
            from playwright.async_api import async_playwright
            cls._pw = await async_playwright().start()
            headless = os.environ.get("KEE_BROWSER_HEADLESS", "0") == "1"
            cls._browser = await cls._pw.chromium.launch(headless=headless)
            cls._context = await cls._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Kee/0.1 Chromium-via-Playwright"
                ),
            )
            cls._page = await cls._context.new_page()
            logger.info("Chromium launched (headless=%s)", headless)
            return cls._page

    @classmethod
    async def close(cls):
        async with cls._lock:
            try:
                if cls._context is not None:
                    await cls._context.close()
                if cls._browser is not None:
                    await cls._browser.close()
                if cls._pw is not None:
                    await cls._pw.stop()
            finally:
                cls._page = cls._context = cls._browser = cls._pw = None


class BrowserControlTool(Tool):
    name = "browser_control"
    description = (
        "Drive a real Chromium browser via Playwright. Use for: verifying "
        "a Vercel deploy actually serves content, scraping a page, filling "
        "a form, taking a screenshot of a dashboard, monitoring a CI run "
        "page. The browser is shared across calls — `close` ends the "
        "session and frees the process.\n"
        "Actions:\n"
        "  - 'navigate' — go to `url`; optional `wait_for` CSS selector\n"
        "  - 'click' — click `selector`\n"
        "  - 'fill' — type `text` into `selector`\n"
        "  - 'press' — press a key like 'Enter' or 'Escape'\n"
        "  - 'get_text' — return innerText of `selector` (or full body if omitted)\n"
        "  - 'get_attribute' — return value of `attribute` on `selector`\n"
        "  - 'screenshot' — write a PNG to `D:/Kee/data/browser/<slug>.png`\n"
        "  - 'wait' — wait for `selector` (default 10 s timeout)\n"
        "  - 'eval' — run JS expression `script` and return its value\n"
        "  - 'close' — shut down the browser\n"
        "Risk 1. If you submit a form that affects external state, treat as risk 2."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "navigate", "click", "fill", "press", "get_text",
                    "get_attribute", "screenshot", "wait", "eval", "close",
                    # Jarvis-pattern extensions (2026-05-04):
                    "tabs_list", "tab_switch", "tab_close", "fill_form",
                    "upload_file", "scroll", "go_back", "go_forward", "reload",
                ],
            },
            "url": {"type": "string"},
            "selector": {"type": "string", "description": "CSS selector."},
            "text": {"type": "string"},
            "key": {"type": "string"},
            "attribute": {"type": "string"},
            "wait_for": {"type": "string", "description": "CSS selector to wait for after navigate."},
            "timeout_s": {"type": "integer", "default": 15},
            "script": {"type": "string", "description": "JS expression for action='eval'."},
            "filename": {"type": "string", "description": "Custom screenshot filename slug."},
            "tab_index": {"type": "integer", "description": "Target tab index for tab_switch / tab_close (0-based)."},
            "fields": {"type": "object", "description": "selector→value map for fill_form (bulk)."},
            "submit_selector": {"type": "string", "description": "Optional CSS selector to click after fill_form."},
            "file_path": {"type": "string", "description": "Absolute path to a local file for upload_file."},
            "scroll_direction": {"type": "string", "enum": ["up", "down", "top", "bottom"], "default": "down"},
            "scroll_amount": {"type": "integer", "default": 500, "description": "Pixels for direction scroll."},
        },
        "required": ["action"],
    }

    async def execute(
        self,
        action: str,
        url: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        key: str | None = None,
        attribute: str | None = None,
        wait_for: str | None = None,
        timeout_s: int = 15,
        script: str | None = None,
        filename: str | None = None,
        tab_index: int | None = None,
        fields: dict | None = None,
        submit_selector: str | None = None,
        file_path: str | None = None,
        scroll_direction: str = "down",
        scroll_amount: int = 500,
    ) -> dict[str, Any]:
        timeout_ms = max(1000, timeout_s * 1000)

        if action == "close":
            await _BrowserSingleton.close()
            return {"status": "closed"}

        try:
            page = await _BrowserSingleton.get_page()
        except Exception as e:
            return {
                "status": "browser_unavailable",
                "error": str(e),
                "fix_hint": (
                    "Run `D:/Kee/.venv/Scripts/python.exe -m playwright "
                    "install --no-shell chromium` once."
                ),
            }

        try:
            if action == "navigate":
                if not url:
                    return {"error": "navigate requires `url`"}
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                if wait_for:
                    await page.wait_for_selector(wait_for, timeout=timeout_ms)
                return {
                    "status": "navigated",
                    "url": page.url,
                    "title": await page.title(),
                }

            if action == "click":
                if not selector:
                    return {"error": "click requires `selector`"}
                await page.click(selector, timeout=timeout_ms)
                return {"status": "clicked", "selector": selector}

            if action == "fill":
                if not (selector and text is not None):
                    return {"error": "fill requires `selector` and `text`"}
                await page.fill(selector, text, timeout=timeout_ms)
                return {"status": "filled", "selector": selector, "len": len(text)}

            if action == "press":
                if not key:
                    return {"error": "press requires `key`"}
                if selector:
                    await page.press(selector, key, timeout=timeout_ms)
                else:
                    await page.keyboard.press(key)
                return {"status": "pressed", "key": key}

            if action == "get_text":
                if selector:
                    el = await page.wait_for_selector(selector, timeout=timeout_ms)
                    raw = (await el.inner_text()) if el else ""
                else:
                    raw = await page.inner_text("body")
                return {
                    "status": "ok",
                    "text": raw[:6000],
                    "truncated": len(raw) > 6000,
                    "url": page.url,
                }

            if action == "get_attribute":
                if not (selector and attribute):
                    return {"error": "get_attribute requires `selector` and `attribute`"}
                el = await page.wait_for_selector(selector, timeout=timeout_ms)
                value = await el.get_attribute(attribute) if el else None
                return {"status": "ok", "selector": selector,
                        "attribute": attribute, "value": value}

            if action == "screenshot":
                out_dir = settings.data_dir / "browser"
                out_dir.mkdir(parents=True, exist_ok=True)
                slug = filename or _slugify(page.url) or "screenshot"
                out_path = out_dir / f"{slug}.png"
                await page.screenshot(path=str(out_path), full_page=True)
                return {
                    "status": "saved",
                    "path": str(out_path),
                    "url": page.url,
                }

            if action == "wait":
                if not selector:
                    return {"error": "wait requires `selector`"}
                await page.wait_for_selector(selector, timeout=timeout_ms)
                return {"status": "appeared", "selector": selector}

            if action == "eval":
                if not script:
                    return {"error": "eval requires `script`"}
                result = await page.evaluate(script)
                return {
                    "status": "ok",
                    "result": str(result)[:2000] if result is not None else None,
                }

            # ── Jarvis-pattern extensions ─────────────────────────────────

            if action == "tabs_list":
                ctx = page.context
                tabs = []
                for i, p in enumerate(ctx.pages):
                    try:
                        tabs.append({
                            "index": i, "url": p.url,
                            "title": await p.title(),
                            "is_current": (p == page),
                        })
                    except Exception:
                        continue
                return {"status": "ok", "tabs": tabs}

            if action == "tab_switch":
                if tab_index is None:
                    return {"error": "tab_switch requires `tab_index`"}
                ctx = page.context
                if tab_index < 0 or tab_index >= len(ctx.pages):
                    return {"error": f"tab_index out of range (0..{len(ctx.pages)-1})"}
                target = ctx.pages[tab_index]
                await target.bring_to_front()
                _BrowserSingleton._page = target  # rebind singleton
                return {"status": "switched", "tab_index": tab_index,
                        "url": target.url, "title": await target.title()}

            if action == "tab_close":
                if tab_index is None:
                    return {"error": "tab_close requires `tab_index`"}
                ctx = page.context
                if tab_index < 0 or tab_index >= len(ctx.pages):
                    return {"error": f"tab_index out of range"}
                target = ctx.pages[tab_index]
                await target.close()
                # If we closed the active tab, fall back to the first remaining
                if not ctx.pages:
                    _BrowserSingleton._page = None
                else:
                    _BrowserSingleton._page = ctx.pages[0]
                return {"status": "closed", "remaining": len(ctx.pages)}

            if action == "fill_form":
                if not fields or not isinstance(fields, dict):
                    return {"error": "fill_form requires `fields` (dict of selector→value)"}
                filled = {}
                for sel, val in fields.items():
                    try:
                        await page.fill(sel, str(val), timeout=timeout_ms)
                        filled[sel] = "ok"
                    except Exception as e:
                        filled[sel] = f"error: {e}"
                if submit_selector:
                    try:
                        await page.click(submit_selector, timeout=timeout_ms)
                        return {"status": "filled_and_submitted",
                                "fields": filled, "url": page.url}
                    except Exception as e:
                        return {"status": "filled_but_submit_failed",
                                "fields": filled, "submit_error": str(e)}
                return {"status": "filled", "fields": filled}

            if action == "upload_file":
                if not (selector and file_path):
                    return {"error": "upload_file requires `selector` and `file_path`"}
                from pathlib import Path
                fp = Path(file_path).expanduser().resolve()
                if not fp.exists():
                    return {"error": f"file not found: {fp}"}
                await page.set_input_files(selector, str(fp), timeout=timeout_ms)
                return {"status": "uploaded", "selector": selector, "file": str(fp)}

            if action == "scroll":
                if scroll_direction == "top":
                    await page.evaluate("window.scrollTo(0, 0)")
                elif scroll_direction == "bottom":
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                elif scroll_direction == "up":
                    await page.evaluate(f"window.scrollBy(0, -{int(scroll_amount)})")
                else:  # down
                    await page.evaluate(f"window.scrollBy(0, {int(scroll_amount)})")
                return {"status": "scrolled", "direction": scroll_direction}

            if action == "go_back":
                await page.go_back(timeout=timeout_ms, wait_until="domcontentloaded")
                return {"status": "back", "url": page.url}

            if action == "go_forward":
                await page.go_forward(timeout=timeout_ms, wait_until="domcontentloaded")
                return {"status": "forward", "url": page.url}

            if action == "reload":
                await page.reload(timeout=timeout_ms, wait_until="domcontentloaded")
                return {"status": "reloaded", "url": page.url}

            return {"error": f"unknown action: {action}"}

        except Exception as e:  # surface playwright errors verbatim to the model
            logger.warning("browser_control %s raised: %s", action, e)
            return {
                "status": "playwright_error",
                "action": action,
                "error": f"{type(e).__name__}: {str(e)[:300]}",
                "url": getattr(page, "url", None),
            }


def _slugify(s: str, max_len: int = 40) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.lower()).strip("-")
    return s[:max_len].rstrip("-") or ""


tool = BrowserControlTool()
