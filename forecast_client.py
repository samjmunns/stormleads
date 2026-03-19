"""
14-day weather forecast for KC metro using Open-Meteo.
Free, no API key required. Updates automatically every hour.

Provides:
- Daily high/low temps, precipitation probability, wind
- WMO weather codes (95/96/99 = thunderstorm/hail)
- Computed hail risk level and canvassing score per day

Open-Meteo docs: https://open-meteo.com/en/docs
"""
import logging
from datetime import date, datetime, timezone

import httpx

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HEADERS = {"User-Agent": "StormLeads/1.0 (contact@stormleads.com)"}

# KC Metro center
KC_LAT = 39.0997
KC_LON = -94.5786

# WMO code descriptions (subset we care about)
WMO_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Icy fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm + hail",
    99: "Severe thunderstorm + hail",
}


def _hail_risk(code: int, precip_prob: int) -> dict:
    """
    Compute hail risk level from WMO weather code and precip probability.
    Returns dict with level (str) and color (str).
    """
    if code == 99:
        return {"level": "High", "color": "#f85149"}
    if code == 96:
        return {"level": "Elevated", "color": "#f0883e"}
    if code == 95 and precip_prob >= 40:
        return {"level": "Moderate", "color": "#d29922"}
    if code == 95:
        return {"level": "Low", "color": "#8b949e"}
    if code in (80, 81, 82) and precip_prob >= 60:
        return {"level": "Low", "color": "#8b949e"}
    return {"level": "Minimal", "color": "#484f58"}


def _canvass_score(code: int, high_f: float, precip_prob: int, wind_mph: float) -> dict:
    """
    Rate each day for door-knocking conditions (1–10).
    Returns dict with score (int), label (str), color (str).
    """
    # Hard stops
    if code in (95, 96, 99):
        return {"score": 1, "label": "Stay in", "color": "#f85149"}
    if code in (65, 82) or precip_prob >= 80:
        return {"score": 2, "label": "Poor", "color": "#f85149"}
    if code in (63, 81) or precip_prob >= 60:
        return {"score": 4, "label": "Fair", "color": "#f0883e"}
    if code in (51, 53, 55, 61, 80) or precip_prob >= 40:
        return {"score": 5, "label": "Fair", "color": "#f0883e"}

    # Temperature comfort
    if high_f >= 100 or high_f <= 30:
        temp_mod = -3
    elif high_f >= 92 or high_f <= 40:
        temp_mod = -2
    elif high_f >= 85 or high_f <= 50:
        temp_mod = -1
    elif 65 <= high_f <= 82:
        temp_mod = 1
    else:
        temp_mod = 0

    # Wind
    wind_mod = -1 if wind_mph >= 25 else 0

    # Base score by sky condition
    base = {0: 10, 1: 9, 2: 8, 3: 6}.get(code, 6)
    score = max(1, min(10, base + temp_mod + wind_mod))

    if score >= 9:
        label, color = "Ideal", "#3fb950"
    elif score >= 7:
        label, color = "Good", "#3fb950"
    elif score >= 5:
        label, color = "Decent", "#d29922"
    else:
        label, color = "Fair", "#f0883e"

    return {"score": score, "label": label, "color": color}


async def get_forecast() -> dict:
    """
    Fetch 14-day daily forecast for KC metro from Open-Meteo.

    Returns dict with:
      days    — list of daily forecast dicts
      week1   — summary for days 1-7
      week2   — summary for days 8-14
    """
    params = {
        "latitude": KC_LAT,
        "longitude": KC_LON,
        "daily": ",".join([
            "weathercode",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max",
            "precipitation_sum",
            "windspeed_10m_max",
            "wind_gusts_10m_max",
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/Chicago",
        "forecast_days": 14,
    }

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
            resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.HTTPError as e:
        logger.error(f"Open-Meteo forecast failed: {e}")
        return {"days": [], "week1": None, "week2": None, "error": str(e)}

    daily = raw.get("daily", {})
    dates = daily.get("time", [])
    codes = daily.get("weathercode", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    precip_probs = daily.get("precipitation_probability_max", [])
    precip_sums = daily.get("precipitation_sum", [])
    winds = daily.get("windspeed_10m_max", [])
    gusts = daily.get("wind_gusts_10m_max", [])

    days = []
    for i, d in enumerate(dates):
        code = int(codes[i]) if i < len(codes) else 0
        high = round(highs[i], 0) if i < len(highs) and highs[i] is not None else None
        low = round(lows[i], 0) if i < len(lows) and lows[i] is not None else None
        prob = int(precip_probs[i]) if i < len(precip_probs) and precip_probs[i] is not None else 0
        precip = round(precip_sums[i], 2) if i < len(precip_sums) and precip_sums[i] is not None else 0
        wind = round(winds[i], 0) if i < len(winds) and winds[i] is not None else 0
        gust = round(gusts[i], 0) if i < len(gusts) and gusts[i] is not None else 0

        dt = datetime.strptime(d, "%Y-%m-%d")
        hail = _hail_risk(code, prob)
        canvass = _canvass_score(code, high or 70, prob, wind or 0)

        # Accuracy note — forecast gets less reliable beyond day 7
        accuracy = "High" if i < 3 else "Medium" if i < 7 else "Low"

        days.append({
            "date": d,
            "day_name": dt.strftime("%A"),
            "day_short": dt.strftime("%a"),
            "date_display": dt.strftime("%b %-d"),
            "is_today": i == 0,
            "code": code,
            "description": WMO_DESCRIPTIONS.get(code, "Unknown"),
            "high_f": high,
            "low_f": low,
            "precip_prob": prob,
            "precip_inches": precip,
            "wind_mph": wind,
            "gust_mph": gust,
            "hail_risk": hail,
            "canvass": canvass,
            "forecast_accuracy": accuracy,
        })

    def _week_summary(week_days):
        if not week_days:
            return None
        good = sum(1 for d in week_days if d["canvass"]["score"] >= 7)
        storm = sum(1 for d in week_days if d["code"] >= 95)
        hail_days = sum(1 for d in week_days if d["code"] in (96, 99))
        best = max(week_days, key=lambda d: d["canvass"]["score"])
        return {
            "good_days": good,
            "storm_days": storm,
            "hail_days": hail_days,
            "best_day": best["day_name"],
        }

    logger.info(f"Open-Meteo: got {len(days)}-day forecast for KC metro")
    return {
        "days": days,
        "week1": _week_summary(days[:7]),
        "week2": _week_summary(days[7:]),
    }
