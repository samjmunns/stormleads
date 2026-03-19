"""
NOAA MRMS (Multi-Radar Multi-Sensor) MESH hail data via Iowa Environmental Mesonet.

MESH = Maximum Expected Size of Hail — radar-derived hail size estimate at every
storm cell location. Unlike LSR spotter reports, MESH fills in the full storm path
even where no human was standing to measure hail.

Source: IEM NEXRAD Storm Attributes archive
Docs: mesonet.agron.iastate.edu/request/gis/nexrad_storm_attrs.php
Free, no API key required.
"""
import csv
import io
import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/nexrad_storm_attrs.py"
HEADERS = {"User-Agent": "StormLeads/1.0 (contact@stormleads.com)"}

# NEXRAD radars covering KC metro
#   KEAX = Kansas City/Pleasant Hill MO (primary KC radar)
#   KTWX = Topeka KS (western KC suburbs)
KC_RADARS = ["KEAX", "KTWX"]

# KC metro bounding box for filtering
KC_LAT_MIN, KC_LAT_MAX = 38.75, 39.45
KC_LON_MIN, KC_LON_MAX = -95.00, -94.20

# Only keep cells with meaningful hail estimates
MIN_HAIL_INCHES = 0.50


async def get_mrms_hail(days_back: int = 14) -> list[dict]:
    """
    Fetch NEXRAD storm attribute records (MESH hail estimates) for KC metro.

    Returns a list of dicts, one per storm cell reading that had hail >= 0.5":
      lat       — cell latitude
      lon       — cell longitude
      max_hail  — max hail size estimate in inches (MESH equivalent)
      posh      — probability of severe hail (%)
      poh       — probability of any hail (%)
      valid     — ISO timestamp of the reading
      radar     — radar site (KEAX or KTWX)
      storm_id  — storm cell identifier (e.g. "A3")
      color     — map display color based on hail size
    """
    now = datetime.now(timezone.utc)
    sts = now - timedelta(days=days_back)

    all_points: list[dict] = []
    for radar in KC_RADARS:
        pts = await _fetch_radar(radar, sts, now)
        all_points.extend(pts)

    # Deduplicate: if two radars reported the same storm cell at the same time
    # keep the one with the larger hail estimate
    seen: dict[tuple, dict] = {}
    for pt in all_points:
        key = (round(pt["lat"], 2), round(pt["lon"], 2), pt["valid"][:13])  # hourly bucket
        if key not in seen or pt["max_hail"] > seen[key]["max_hail"]:
            seen[key] = pt

    result = sorted(seen.values(), key=lambda x: x["valid"])
    logger.info(f"MRMS: {len(result)} hail cells in KC metro over last {days_back}d")
    return result


async def _fetch_radar(radar: str, sts: datetime, ets: datetime) -> list[dict]:
    """Fetch storm attribute CSV for one radar site and return KC-filtered points."""
    params = {
        "sts": sts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ets": ets.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "radar": radar,
        "fmt": "csv",
    }
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=40.0) as client:
            resp = await client.get(IEM_URL, params=params)
            resp.raise_for_status()
            return _parse_csv(resp.text, radar)
    except httpx.HTTPError as e:
        logger.warning(f"MRMS fetch failed for {radar}: {e}")
        return []


def _parse_csv(text: str, radar: str) -> list[dict]:
    """Parse IEM storm attributes CSV, filter to KC metro, return hail points."""
    points = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            try:
                lat = float(row.get("lat") or row.get("latitude") or 0)
                lon = float(row.get("lon") or row.get("longitude") or 0)

                if not (KC_LAT_MIN <= lat <= KC_LAT_MAX and KC_LON_MIN <= lon <= KC_LON_MAX):
                    continue

                max_hail = float(row.get("max_size") or 0)
                if max_hail < MIN_HAIL_INCHES:
                    continue

                posh = float(row.get("posh") or 0)
                poh = float(row.get("poh") or 0)
                valid = row.get("valid", "")
                storm_id = row.get("storm_id", "")

                points.append({
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "max_hail": round(max_hail, 2),
                    "posh": int(round(posh)),
                    "poh": int(round(poh)),
                    "valid": valid,
                    "radar": radar,
                    "storm_id": storm_id,
                    "color": _hail_color(max_hail),
                })
            except (ValueError, TypeError):
                continue
    except Exception as e:
        logger.warning(f"MRMS CSV parse error ({radar}): {e}")
    return points


def _hail_color(inches: float) -> str:
    """Map hail size to a display color."""
    if inches >= 2.5:
        return "#9d00ff"   # purple  — baseball+ (severe)
    if inches >= 2.0:
        return "#f85149"   # red     — hen egg
    if inches >= 1.5:
        return "#e8562a"   # dark orange — walnut
    if inches >= 1.0:
        return "#f0883e"   # orange  — quarter
    if inches >= 0.75:
        return "#d29922"   # yellow  — penny
    return "#6e7681"       # gray    — marble (marginal)
