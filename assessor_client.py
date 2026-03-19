"""
Property data for KC metro storm zones.

Strategy:
  1. USACE National Structure Inventory (NSI) — per-structure data including
     year built, occupancy type (owner/renter), structure value, sqft.
     Free public API, no auth required. 3,000–5,000 structures per square mile.

  2. Jackson County MO Address Points — street addresses with lat/lon.
     Spatial-join matched to NSI points by proximity (~50m).

  3. Reverse geocoding fallback (Nominatim) for any NSI point with no
     address match.

NSI occupancy types we care about:
  RES1-*  = single-family residential → owner-occupied lead
  RES2    = manufactured/mobile home  → owner-occupied lead
  RES3A/B = multi-family (≤10 / >10 units) → likely rented, lower priority
  COM/IND/GOV/REL = skip
"""
import logging
import math
from collections import defaultdict

import httpx

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "StormLeads/1.0 (contact@stormleads.com)"}

# NSI — POST a GeoJSON polygon, get back structures with year built etc.
NSI_URL = "https://nsi.sec.usace.army.mil/nsiapi/structures"

# Jackson County MO address points (public ArcGIS, no auth)
JCMO_ADDR_URL = (
    "https://jcgis.jacksongov.org/arcgis/rest/services/"
    "ParcelViewer/ParcelsPointsAscendBackup/FeatureServer/0/query"
)

# Nominatim reverse geocode (fallback, 1 req/sec rate limit)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# Max NSI structures to return per zone
MAX_RESULTS = 500

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
        """
        center = zone.get("center", {})
        lat = center.get("lat", 39.0997)
        lon = center.get("lon", -94.5786)
        radius = zone.get("radius_miles", 3.0)

        # 1. Fetch NSI structures (year built, type, value)
        nsi_points = await self._fetch_nsi(lat, lon, radius)
        if not nsi_points:
            logger.warning("NSI returned no structures for zone")
            return []

        # 2. Fetch county address points for the same area
        bbox = _radius_to_bbox(lat, lon, radius)
        addr_index = await self._fetch_address_index(bbox)

        # 3. Match each NSI point to nearest street address
        properties = []
        current_year = 2026
        for pt in nsi_points:
            if not _is_residential(pt["occtype"]):
                continue
            if not _in_circle(pt["lat"], pt["lon"], lat, lon, radius):
                continue

            address = _nearest_address(pt["lat"], pt["lon"], addr_index)
            yr = pt.get("year_built")
            roof_age = current_year - yr if yr else None
            owner_status = _owner_status(pt["occtype"])

            # Skip large multi-family if over 50% of zone seems rented (keep RES1/RES2 focus)
            if pt["occtype"].upper().startswith("RES3B"):
                continue

            priority = _priority_label(roof_age)

            properties.append({
                "address": address or "Address unknown",
                "lat": pt["lat"],
                "lon": pt["lon"],
                "year_built": yr,
                "roof_age": roof_age,
                "owner_status": owner_status,
                "occ_type": _occ_label(pt["occtype"]),
                "structure_value": pt.get("structure_value"),
                "sqft": pt.get("sqft"),
                "county": _county(pt["lon"]),
                "priority": priority,
                "priority_color": _priority_color(roof_age),
            })

        # Sort oldest first
        properties.sort(key=lambda p: (p.get("year_built") or 9999))

        # Add rank and cap at MAX_RESULTS
        for i, p in enumerate(properties[:MAX_RESULTS]):
            p["rank"] = i + 1

        logger.info(
            f"Assessor: {len(properties)} residential properties in zone "
            f"{zone.get('zone_id','?')} — {len(nsi_points)} NSI structures queried"
        )
        return properties[:MAX_RESULTS]

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

    async def _fetch_address_index(self, bbox: dict) -> dict:
        """
        Fetch Jackson County address points and build a spatial grid index
        keyed by (round(lat,3), round(lon,3)) for fast nearest-neighbor lookup.
        """
        params = {
            "geometry": f"{bbox['lon_min']},{bbox['lat_min']},{bbox['lon_max']},{bbox['lat_max']}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FULLADDR,MUNICIPALITY,ZIP,XCOORD,YCOORD,StateAbbrv",
            "returnGeometry": "true",
            "resultRecordCount": 5000,
            "f": "json",
        }
        index: dict[tuple, list] = defaultdict(list)
        # Paginate — service returns max 2000 per request, zone may have 10k+ addresses
        offset = 0
        page_size = 2000
        pages_fetched = 0
        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as client:
                while pages_fetched < 5:  # cap at 10k addresses total
                    params["resultRecordCount"] = page_size
                    params["resultOffset"] = offset
                    resp = await client.get(JCMO_ADDR_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    features = data.get("features", [])
                    for f in features:
                        attrs = f.get("attributes", {})
                        geo = f.get("geometry", {})
                        # Use geometry.x/y (outSR=4326 converts these to WGS84)
                        # XCOORD/YCOORD attributes are in State Plane feet — don't use
                        alon = float(geo.get("x") or 0)
                        alat = float(geo.get("y") or 0)
                        if not (38 < alat < 41 and -96 < alon < -93):
                            continue
                        addr = (attrs.get("FULLADDR") or "").strip()
                        city = (attrs.get("MUNICIPALITY") or "").strip().title()
                        state = (attrs.get("StateAbbrv") or "MO").strip()
                        if not addr:
                            continue
                        entry = {"addr": addr, "city": city, "state": state, "lat": alat, "lon": alon}
                        key = (round(alat, 3), round(alon, 3))
                        index[key].append(entry)
                    pages_fetched += 1
                    offset += page_size
                    if not data.get("exceededTransferLimit"):
                        break  # no more pages
            total = sum(len(v) for v in index.values())
            logger.info(f"Address index: {total} points across {pages_fetched} page(s)")
        except Exception as e:
            logger.warning(f"Address index fetch failed: {e}")
        return dict(index)


# ---- Helpers ----

def _nearest_address(lat: float, lon: float, index: dict) -> str | None:
    """Find the nearest street address in the grid index within ~100m."""
    best_dist = 999.0
    best_addr = None

    rlat = round(lat, 3)
    rlon = round(lon, 3)
    step = 0.001  # ~100m grid

    for dlat in (-step, 0, step):
        for dlon in (-step, 0, step):
            key = (round(rlat + dlat, 3), round(rlon + dlon, 3))
            for entry in index.get(key, []):
                d = _dist_miles(lat, lon, entry["lat"], entry["lon"])
                if d < best_dist:
                    best_dist = d
                    city = entry.get("city", "")
                    state = entry.get("state", "MO")
                    best_addr = entry["addr"] + (f", {city}" if city else "") + f", {state}"

    return best_addr if best_dist < 0.1 else None  # within ~0.1 mile


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


def _county(lon: float) -> str:
    """Rough county determination by longitude."""
    return "Johnson, KS" if lon < -94.60 else "Jackson, MO"


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
