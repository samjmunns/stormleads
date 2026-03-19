"""
Property data for KC metro storm zones.

Strategy:
  1. USACE National Structure Inventory (NSI) — per-structure data including
     year built, occupancy type (owner/renter), structure value, sqft.
     Free public API, no auth required. 3,000–5,000 structures per square mile.

  2. Nominatim reverse geocoding — converts NSI lat/lon to street addresses.
     Results are cached to disk so each address is only looked up once.
     Covers all KC metro counties (Jackson/Clay/Platte MO, Johnson/Wyandotte KS).
     Max 3 concurrent requests; results cached to data/address_cache.json.

NSI occupancy types we care about:
  RES1-*  = single-family residential → owner-occupied lead
  RES2    = manufactured/mobile home  → owner-occupied lead
  RES3A/B = multi-family (≤10 / >10 units) → likely rented, lower priority
  COM/IND/GOV/REL = skip
"""
import asyncio
import json
import logging
import math
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "StormLeads/1.0 (contact@stormleads.com)"}

# NSI — POST a GeoJSON polygon, get back structures with year built etc.
NSI_URL = "https://nsi.sec.usace.army.mil/nsiapi/structures"

# Nominatim reverse geocoding (OSM-based, free, no key required)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# Max NSI structures to return per zone
MAX_RESULTS = 500

# Max addresses to resolve per zone via Nominatim (rest show lat/lon)
# Oldest/highest-priority leads are resolved first; cache fills over time.
MAX_GEOCODE = 30

# Disk cache for lat/lon → address lookups
ADDR_CACHE_FILE = Path("data/address_cache.json")

# Module-level cache (loaded once at import)
_addr_cache: dict = {}


def _load_cache() -> None:
    global _addr_cache
    try:
        if ADDR_CACHE_FILE.exists():
            _addr_cache = json.loads(ADDR_CACHE_FILE.read_text())
            logger.info(f"Address cache loaded: {len(_addr_cache)} entries")
    except Exception as e:
        logger.warning(f"Address cache load failed: {e}")
        _addr_cache = {}


def _save_cache() -> None:
    try:
        ADDR_CACHE_FILE.parent.mkdir(exist_ok=True)
        ADDR_CACHE_FILE.write_text(json.dumps(_addr_cache))
    except Exception as e:
        logger.warning(f"Address cache save failed: {e}")


_load_cache()


# Occupancy type → owner status label
def _owner_status(occtype: str) -> str:
    ot = occtype.upper()
    if ot.startswith("RES1") or ot.startswith("RES2"):
        return "Owner-Occupied"
    if ot.startswith("RES3"):
        return "Likely Rented"
    return "Unknown"


def _occ_label(occtype: str) -> str:
    ot = occtype.upper()
    if ot.startswith("RES1-1S"):
        return "Single Family 1-Story"
    if ot.startswith("RES1-2S"):
        return "Single Family 2-Story"
    if ot.startswith("RES1-3S"):
        return "Single Family 3-Story"
    if ot.startswith("RES1"):
        return "Single Family"
    if ot.startswith("RES2"):
        return "Mobile / Manufactured"
    if ot.startswith("RES3A"):
        return "Multi-Family (small)"
    if ot.startswith("RES3B"):
        return "Multi-Family (large)"
    return occtype


def _is_residential(occtype: str) -> bool:
    ot = occtype.upper()
    return ot.startswith("RES1") or ot.startswith("RES2") or ot.startswith("RES3A")


class AssessorClient:

    async def get_properties_in_zone(self, zone: dict) -> list[dict]:
        """
        Return property records for residential structures within a storm zone.

        Returns list sorted oldest-first (highest roof-replacement priority).
        Each dict has: address, year_built, roof_age, owner_status, occ_type,
                       structure_value, sqft, lat, lon, county, priority

        Uses epicenter + report_radius_miles (tight hail circle) rather than
        the larger NWS warning zone radius to keep queries fast and accurate.
        """
        epicenter = zone.get("epicenter") or zone.get("center", {})
        lat = epicenter.get("lat", 39.0997)
        lon = epicenter.get("lon", -94.5786)
        # report_radius_miles is the tight 75th-pct hail footprint, cap at 8mi
        radius = min(zone.get("report_radius_miles") or zone.get("radius_miles", 3.0), 8.0)

        # 1. Fetch NSI structures (year built, type, value)
        nsi_points = await self._fetch_nsi(lat, lon, radius)
        if not nsi_points:
            logger.warning("NSI returned no structures for zone")
            return []

        # 2. Filter to residential points inside the circle
        current_year = 2026
        candidates = []
        for pt in nsi_points:
            if not _is_residential(pt["occtype"]):
                continue
            if pt["occtype"].upper().startswith("RES3B"):
                continue
            if not _in_circle(pt["lat"], pt["lon"], lat, lon, radius):
                continue
            yr = pt.get("year_built")
            candidates.append({
                "lat": pt["lat"],
                "lon": pt["lon"],
                "year_built": yr,
                "roof_age": current_year - yr if yr else None,
                "owner_status": _owner_status(pt["occtype"]),
                "occ_type": _occ_label(pt["occtype"]),
                "structure_value": pt.get("structure_value"),
                "sqft": pt.get("sqft"),
                "county": _county(pt["lon"], pt["lat"]),
                "priority": _priority_label(current_year - yr if yr else None),
                "priority_color": _priority_color(current_year - yr if yr else None),
            })

        # Sort oldest first before geocoding so we resolve the most valuable leads
        candidates.sort(key=lambda p: (p.get("year_built") or 9999))

        # 3. Resolve addresses: check cache first, then batch Nominatim for uncached
        to_geocode = []
        for p in candidates[:MAX_RESULTS]:
            key = _cache_key(p["lat"], p["lon"])
            if key not in _addr_cache:
                to_geocode.append(p)
            if len(to_geocode) >= MAX_GEOCODE:
                break

        if to_geocode:
            await self._batch_geocode(to_geocode)
            _save_cache()

        # 4. Assign addresses from cache
        properties = []
        for p in candidates[:MAX_RESULTS]:
            key = _cache_key(p["lat"], p["lon"])
            cached = _addr_cache.get(key)
            p["address"] = cached if cached else f"{p['lat']:.4f}, {p['lon']:.4f}"
            properties.append(p)

        # Add rank
        for i, p in enumerate(properties):
            p["rank"] = i + 1

        logger.info(
            f"Assessor: {len(properties)} residential properties in zone "
            f"{zone.get('zone_id','?')} — {len(nsi_points)} NSI structures queried, "
            f"{len(to_geocode)} new addresses resolved"
        )
        return properties

    async def _fetch_nsi(self, lat: float, lon: float, radius_miles: float) -> list[dict]:
        """POST a circle polygon to NSI and return residential structure list."""
        polygon = _circle_polygon(lat, lon, radius_miles)
        body = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [polygon]},
                "properties": {},
            }],
        }
        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=40.0) as client:
                resp = await client.post(NSI_URL, json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"NSI fetch failed: {e}")
            return []

        results = []
        for f in data.get("features", []):
            p = f.get("properties", {})
            geo = f.get("geometry", {})
            coords = geo.get("coordinates", [None, None]) if geo.get("type") == "Point" else [None, None]
            x = p.get("x") or (coords[0] if coords else None)
            y = p.get("y") or (coords[1] if coords else None)
            if not x or not y:
                continue
            yr = p.get("med_yr_blt")
            results.append({
                "lat": round(float(y), 5),
                "lon": round(float(x), 5),
                "occtype": p.get("occtype", ""),
                "year_built": int(yr) if yr and 1850 < float(yr) < 2026 else None,
                "structure_value": round(float(p.get("val_struct") or 0)),
                "sqft": round(float(p.get("sqft") or 0)),
            })
        return results

    async def _batch_geocode(self, properties: list[dict]) -> None:
        """
        Reverse geocode up to MAX_GEOCODE properties via Nominatim.
        Uses a semaphore to cap concurrency at 3 simultaneous requests.
        Results are written directly into _addr_cache.
        """
        semaphore = asyncio.Semaphore(5)

        async def _one(p: dict, client: httpx.AsyncClient) -> None:
            key = _cache_key(p["lat"], p["lon"])
            async with semaphore:
                try:
                    resp = await client.get(NOMINATIM_URL, params={
                        "lat": p["lat"],
                        "lon": p["lon"],
                        "format": "json",
                        "zoom": 18,
                        "addressdetails": 1,
                    })
                    resp.raise_for_status()
                    data = resp.json()
                    a = data.get("address", {})
                    num = a.get("house_number", "")
                    road = a.get("road", "")
                    city = a.get("city") or a.get("town") or a.get("suburb", "")
                    state = a.get("state", "")
                    if road:
                        addr = (f"{num} " if num else "") + road
                        if city:
                            addr += f", {city}"
                        if state:
                            addr += f", {state}"
                        _addr_cache[key] = addr
                    else:
                        _addr_cache[key] = None
                except Exception as e:
                    logger.debug(f"Nominatim failed for {p['lat']},{p['lon']}: {e}")
                    _addr_cache[key] = None

        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            await asyncio.gather(*[_one(p, client) for p in properties])


# ---- Helpers ----

def _cache_key(lat: float, lon: float) -> str:
    return f"{round(lat, 4)},{round(lon, 4)}"


def _priority_label(roof_age: int | None) -> str:
    if roof_age is None:
        return "Unknown"
    if roof_age >= 35:
        return "Very High"
    if roof_age >= 25:
        return "High"
    if roof_age >= 15:
        return "Moderate"
    return "Lower"


def _priority_color(roof_age: int | None) -> str:
    if roof_age is None:
        return "#6e7681"
    if roof_age >= 35:
        return "#f85149"
    if roof_age >= 25:
        return "#f0883e"
    if roof_age >= 15:
        return "#d29922"
    return "#6e7681"


def _county(lon: float, lat: float = 39.1) -> str:
    """Rough county determination by lat/lon for KC metro."""
    if lon < -94.90:
        return "Johnson, KS"
    if lon < -94.60:
        return "Wyandotte, KS" if lat < 39.10 else "Platte, MO"
    if lat > 39.17:
        return "Clay, MO"
    return "Jackson, MO"


def _radius_to_bbox(lat: float, lon: float, radius_miles: float) -> dict:
    pad = radius_miles * 1.1
    lat_deg = pad / 69.0
    lon_deg = pad / (69.0 * math.cos(math.radians(lat)))
    return {
        "lat_min": lat - lat_deg, "lat_max": lat + lat_deg,
        "lon_min": lon - lon_deg, "lon_max": lon + lon_deg,
    }


def _in_circle(lat: float, lon: float, clat: float, clon: float, radius_miles: float) -> bool:
    return _dist_miles(lat, lon, clat, clon) <= radius_miles


def _dist_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _circle_polygon(lat: float, lon: float, radius_miles: float, n_points: int = 32) -> list:
    """Generate a polygon approximating a circle for the NSI POST body."""
    R_earth = 3958.8
    coords = []
    for i in range(n_points + 1):
        angle = math.radians(360 * i / n_points)
        dlat = (radius_miles / R_earth) * math.cos(angle)
        dlon = (radius_miles / R_earth) * math.sin(angle) / math.cos(math.radians(lat))
        coords.append([round(lon + math.degrees(dlon), 5), round(lat + math.degrees(dlat), 5)])
    return coords
