"""Tool: weather — current + forecast via Open-Meteo (free, no API key).

Uses two Open-Meteo endpoints:
  * https://geocoding-api.open-meteo.com  → city name → lat/lon
  * https://api.open-meteo.com/v1/forecast → current + daily

Default city: Coco's location (read from `vault/config/user.md` if it
mentions a city; falls back to "Saltillo" in northern Mexico).

Risk: 0.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_DEFAULT_CITY = "Saltillo, Mexico"


def _detect_city_from_user_md() -> str:
    """Look for a 'city:' / 'ubicación:' line in vault/config/user.md."""
    p = settings.vault_dir / "config" / "user.md"
    if not p.exists():
        return _DEFAULT_CITY
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return _DEFAULT_CITY
    for pat in (r"\bciudad:\s*([^\n]+)", r"\bcity:\s*([^\n]+)",
                r"\bubicación:\s*([^\n]+)", r"\blocation:\s*([^\n]+)"):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".,")
    return _DEFAULT_CITY


async def _geocode(city: str) -> Optional[dict]:
    url = "https://geocoding-api.open-meteo.com/v1/search"
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(url, params={"name": city, "count": 1, "language": "es", "format": "json"})
            d = r.json() if r.status_code == 200 else {}
        results = d.get("results") or []
        if not results:
            return None
        r0 = results[0]
        return {
            "lat": r0["latitude"],
            "lon": r0["longitude"],
            "name": r0.get("name", city),
            "country": r0.get("country", ""),
            "admin1": r0.get("admin1", ""),
        }
    except Exception as e:
        logger.warning("geocode failed: %s", e)
        return None


def _wcode_to_text(code: int) -> str:
    """WMO weather code → Spanish description."""
    table = {
        0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado",
        3: "nublado", 45: "neblina", 48: "neblina con escarcha",
        51: "llovizna ligera", 53: "llovizna", 55: "llovizna intensa",
        61: "lluvia ligera", 63: "lluvia", 65: "lluvia fuerte",
        71: "nieve ligera", 73: "nieve", 75: "nevada fuerte",
        80: "chubascos", 81: "chubascos fuertes", 82: "tormenta de chubascos",
        95: "tormenta", 96: "tormenta con granizo", 99: "tormenta severa",
    }
    return table.get(int(code), f"código {code}")


async def _forecast(lat: float, lon: float) -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 3,
    }
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.get(url, params=params)
        if r.status_code != 200:
            return {"ok": False, "error": f"api {r.status_code}"}
        return {"ok": True, **r.json()}


class WeatherTool(Tool):
    name = "weather"
    description = (
        "Get current weather + 3-day forecast for a city. Free Open-Meteo "
        "API, no key needed. Default city auto-detected from "
        "vault/config/user.md (looks for a 'ciudad:' or 'city:' line)."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string",
                     "description": "City name. Empty = default from user.md."},
            "action": {
                "type": "string",
                "enum": ["now", "today", "forecast"],
                "default": "now",
            },
        },
    }

    async def execute(self, city: str = "", action: str = "now") -> dict[str, Any]:
        target_city = city.strip() or _detect_city_from_user_md()
        geo = await _geocode(target_city)
        if not geo:
            return {"ok": False, "error": f"could not geocode {target_city!r}"}

        f = await _forecast(geo["lat"], geo["lon"])
        if not f.get("ok"):
            return f

        cur = f.get("current", {}) or {}
        daily = f.get("daily", {}) or {}

        if action == "now":
            return {
                "ok": True,
                "city": geo["name"],
                "country": geo["country"],
                "temperature_c": cur.get("temperature_2m"),
                "feels_like_c": cur.get("apparent_temperature"),
                "humidity_pct": cur.get("relative_humidity_2m"),
                "wind_kmh": cur.get("wind_speed_10m"),
                "summary": _wcode_to_text(cur.get("weather_code", 0)),
            }

        if action == "today":
            return {
                "ok": True,
                "city": geo["name"],
                "summary": _wcode_to_text(daily.get("weather_code", [0])[0]),
                "max_c": daily.get("temperature_2m_max", [0])[0],
                "min_c": daily.get("temperature_2m_min", [0])[0],
                "rain_chance_pct": daily.get("precipitation_probability_max", [0])[0],
            }

        # forecast (3 days)
        days = []
        n = len(daily.get("weather_code", []))
        for i in range(min(3, n)):
            days.append({
                "date": daily.get("time", [None])[i],
                "summary": _wcode_to_text(daily["weather_code"][i]),
                "max_c": daily["temperature_2m_max"][i],
                "min_c": daily["temperature_2m_min"][i],
                "rain_chance_pct": daily.get("precipitation_probability_max", [0]*n)[i],
            })
        return {"ok": True, "city": geo["name"], "country": geo["country"], "days": days}


tool = WeatherTool()
