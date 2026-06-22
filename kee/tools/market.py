"""Tool: market — crypto + stock prices and alerts.

Phase 8 §"AEGIS Terminal market integration". Read-only — no broker keys
needed. Two free public sources:

  * CoinGecko (crypto, no key, generous public tier): `/simple/price`
  * Yahoo Finance (stocks/ETFs/FX, public quote endpoint, no key)

Actions:
  - 'price':       quote one or more symbols (auto-detects asset class)
  - 'watchlist':   read/write `vault/config/watchlist.json`
  - 'check_alerts': evaluate every watchlist row vs its alert thresholds,
                    fire `notify_user` when triggered (heartbeat hook)
  - 'history':     last N days of close prices (Yahoo only — crypto skips)

Risk: 0 (read-only HTTP).

Watchlist file format (JSON list of dicts):
  [
    {"symbol": "BTC",  "asset": "crypto", "above": 70000, "below": 60000},
    {"symbol": "AAPL", "asset": "stock",  "above": 250},
    {"symbol": "MXN=X","asset": "fx",     "below": 17.5}
  ]
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


# ── Watchlist persistence ────────────────────────────────────────────────
def watchlist_path() -> Path:
    return settings.vault_dir / "config" / "watchlist.json"


def load_watchlist() -> list[dict]:
    p = watchlist_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("watchlist corrupt — returning empty")
        return []


def save_watchlist(rows: list[dict]) -> None:
    p = watchlist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    tmp.replace(p)


# ── CoinGecko symbol → id mapping (top tickers, expandable) ─────────────
_CRYPTO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "MATIC": "matic-network", "DOGE": "dogecoin", "ADA": "cardano",
    "AVAX": "avalanche-2", "LINK": "chainlink", "DOT": "polkadot",
    "USDT": "tether", "USDC": "usd-coin", "XRP": "ripple",
    "BNB": "binancecoin", "TRX": "tron", "TON": "the-open-network",
}


# ── Quote fetchers ───────────────────────────────────────────────────────
async def _quote_crypto(symbols: list[str]) -> dict[str, dict]:
    ids = [_CRYPTO_IDS.get(s.upper()) for s in symbols if s.upper() in _CRYPTO_IDS]
    if not ids:
        return {}
    url = (
        f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(ids)}"
        f"&vs_currencies=usd,mxn&include_24hr_change=true"
    )
    out: dict[str, dict] = {}
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            resp = await c.get(url)
            data = resp.json() if resp.status_code == 200 else {}
        rev_ids = {v: k for k, v in _CRYPTO_IDS.items()}
        for cid, q in data.items():
            sym = rev_ids.get(cid, cid)
            out[sym] = {
                "asset": "crypto", "price_usd": q.get("usd"),
                "price_mxn": q.get("mxn"),
                "change_24h_pct": q.get("usd_24h_change"),
                "source": "coingecko",
            }
    except Exception as e:
        logger.warning("coingecko fetch failed: %s", e)
    return out


async def _quote_yahoo(symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    headers = {"User-Agent": "Mozilla/5.0 (kee-market-tool)"}
    out: dict[str, dict] = {}

    # Strategy: /v7/finance/quote was deprecated mid-2024 for unauth'd
    # callers; fall back to per-symbol /v8/finance/chart (still public)
    # which carries the same fields under chart.result[0].meta.
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=headers) as c:
            for sym in symbols:
                url = (
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                    f"?interval=1d&range=2d"
                )
                try:
                    r = await c.get(url)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    result = data.get("chart", {}).get("result", [])
                    if not result:
                        continue
                    meta = result[0].get("meta", {}) or {}
                    price = meta.get("regularMarketPrice")
                    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
                    change = None
                    if price is not None and prev:
                        change = ((price - prev) / prev) * 100.0
                    out[sym] = {
                        "asset": "fx" if "=" in sym else "stock",
                        "price_usd": price,
                        "change_24h_pct": change,
                        "currency": meta.get("currency"),
                        "name": meta.get("longName") or meta.get("shortName") or sym,
                        "source": "yahoo_chart",
                    }
                except Exception as e:
                    logger.debug("yahoo chart fetch failed for %s: %s", sym, e)
    except Exception as e:
        logger.warning("yahoo client failed: %s", e)
    return out


async def quote(symbols: list[str]) -> dict[str, dict]:
    """Auto-route symbols by asset class. Crypto → CoinGecko, rest → Yahoo."""
    crypto = [s for s in symbols if s.upper() in _CRYPTO_IDS]
    rest = [s for s in symbols if s.upper() not in _CRYPTO_IDS]
    out: dict[str, dict] = {}
    if crypto:
        out.update(await _quote_crypto(crypto))
    if rest:
        out.update(await _quote_yahoo(rest))
    return out


async def history(symbol: str, days: int = 30) -> dict[str, Any]:
    """Last `days` of daily close prices via Yahoo."""
    end = int(time.time())
    start = end - days * 86400
    url = (
        f"https://query1.finance.yahoo.com/v7/finance/chart/{symbol}"
        f"?period1={start}&period2={end}&interval=1d"
    )
    headers = {"User-Agent": "Mozilla/5.0 (kee-market-tool)"}
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=headers) as c:
            r = await c.get(url)
            data = r.json() if r.status_code == 200 else {}
        result = data.get("chart", {}).get("result", [])
        if not result:
            return {"ok": False, "error": "no data"}
        chart = result[0]
        ts = chart.get("timestamp", []) or []
        closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", []) or []
        return {
            "ok": True,
            "symbol": symbol,
            "days": days,
            "points": [{"t": t, "close": c} for t, c in zip(ts, closes) if c is not None],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Alert evaluation ────────────────────────────────────────────────────
async def check_alerts(notify: bool = True) -> dict[str, Any]:
    """Evaluate every watchlist row vs its `above`/`below` thresholds.

    Fires `notify_user` (cross-platform desktop+telegram) for any triggered
    alert. Per-row dedup via `_kee/market_alerts_state.json` so the same
    alert doesn't re-fire every heartbeat.
    """
    rows = load_watchlist()
    if not rows:
        return {"checked": 0, "fired": 0, "alerts": []}
    symbols = [r["symbol"] for r in rows]
    quotes = await quote(symbols)
    state_path = settings.vault_dir / "_kee" / "market_alerts_state.json"
    state: dict[str, str] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    fired: list[dict] = []
    for r in rows:
        sym = r["symbol"]
        q = quotes.get(sym)
        if not q or q.get("price_usd") is None:
            continue
        price = float(q["price_usd"])
        triggered: list[str] = []
        if r.get("above") is not None and price >= float(r["above"]):
            triggered.append(f"above_{r['above']}")
        if r.get("below") is not None and price <= float(r["below"]):
            triggered.append(f"below_{r['below']}")
        for trig in triggered:
            key = f"{sym}:{trig}"
            today = time.strftime("%Y-%m-%d")
            if state.get(key) == today:
                continue        # already fired today
            state[key] = today
            ev = {
                "symbol": sym, "asset": q.get("asset"), "price": price,
                "trigger": trig, "change_24h_pct": q.get("change_24h_pct"),
            }
            fired.append(ev)
            if notify:
                try:
                    from kee.perception.notifications import notify_user
                    await notify_user(
                        title=f"📈 {sym} {trig}",
                        body=f"{sym} = ${price:,.2f} (Δ24h={q.get('change_24h_pct') or 0:.2f}%)",
                        urgency="normal", source="market",
                    )
                except Exception as e:
                    logger.warning("notify_user failed: %s", e)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return {"checked": len(rows), "fired": len(fired), "alerts": fired}


# ── Tool surface ─────────────────────────────────────────────────────────
class MarketTool(Tool):
    name = "market"
    description = (
        "Crypto + stock + FX market data and price alerts. Read-only, no "
        "broker keys needed (CoinGecko + Yahoo Finance public APIs). "
        "Alerts evaluate against your watchlist at vault/config/watchlist.json "
        "and fire desktop+Telegram notifications via notify_user.\n"
        "Actions:\n"
        "  - 'price': quote(symbols=['BTC','AAPL','MXN=X'])\n"
        "  - 'watchlist': list/add/remove rows ({symbol, asset, above, below})\n"
        "  - 'check_alerts': evaluate watchlist vs thresholds, notify on triggers\n"
        "  - 'history': last N days of close prices (Yahoo)"
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["price", "watchlist", "check_alerts", "history"],
                "default": "price",
            },
            "symbols": {"type": "array", "items": {"type": "string"}},
            "symbol": {"type": "string"},
            "days": {"type": "integer", "default": 30},
            "watchlist_op": {
                "type": "string",
                "enum": ["list", "add", "remove"],
                "default": "list",
            },
            "row": {"type": "object", "description": "{symbol, asset, above, below} for add"},
            "remove_symbol": {"type": "string"},
        },
    }

    async def execute(
        self,
        action: str = "price",
        symbols: Optional[list[str]] = None,
        symbol: Optional[str] = None,
        days: int = 30,
        watchlist_op: str = "list",
        row: Optional[dict] = None,
        remove_symbol: Optional[str] = None,
    ) -> dict[str, Any]:
        if action == "price":
            syms = symbols or ([symbol] if symbol else [])
            if not syms:
                return {"ok": False, "error": "symbols required"}
            return {"ok": True, "quotes": await quote(syms)}

        if action == "history":
            if not symbol:
                return {"ok": False, "error": "symbol required"}
            return await history(symbol, days=days)

        if action == "check_alerts":
            return {"ok": True, **await check_alerts(notify=True)}

        if action == "watchlist":
            current = load_watchlist()
            if watchlist_op == "list":
                return {"ok": True, "watchlist": current}
            if watchlist_op == "add":
                if not row or "symbol" not in row:
                    return {"ok": False, "error": "row.symbol required"}
                current = [r for r in current if r["symbol"] != row["symbol"]]
                current.append(row)
                save_watchlist(current)
                return {"ok": True, "watchlist": current, "added": row["symbol"]}
            if watchlist_op == "remove":
                if not remove_symbol:
                    return {"ok": False, "error": "remove_symbol required"}
                current = [r for r in current if r["symbol"] != remove_symbol]
                save_watchlist(current)
                return {"ok": True, "watchlist": current, "removed": remove_symbol}

        return {"ok": False, "error": f"unknown action '{action}'"}


tool = MarketTool()
