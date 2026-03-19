"""
County assessor property data for KC metro.

Queries public ArcGIS REST services to get property-level data
(address, year built, assessed value, owner type) within storm zones.

Sources:
  Jackson County MO — jcgis.jacksongov.org (public, no auth required)
  Johnson County KS — open data portal (public layer)

Returns address-level leads so canvassers know exactly which houses to visit,
ranked by estimated roof age (oldest first = highest priority).
"""
import logging
import math
from typing import Any

import httpx

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "StormLeads/1.0 (contact@stormleads.com)"}

# Jackson County MO — AssessorAnalysis service (public)
JACKSON_URL = (
    "https://jcgis.jacksongov.org/arcgis/rest/services/"
    "AssessorAnalysis/Assessor_Neighborhood_and_Region_Analysis/MapServer/5/query"
)

# Jackson County MO — Parcel points (backup, has more address fields)
JACKSON_PARCEL_URL = (
    "https://jcgis.jacksongov.org/arcgis/rest/services/"
    "ParcelViewer/ParcelsPointsAscendBackup/FeatureServer/0/query"
)

# Johnson County KS — public open data portal parcel layer
JOHNSON_URL = (
    "https://services1.arcgis.com/fBc8EJBxQRMcHlei/arcgis/rest/services/"
    "JoCo_Parcels/FeatureServer/0/query"
)

MAX_RECORDS = 2000

# Common year-built field names across different ArcGIS services
YEAR_BUILT_FIELDS = (
    "YEAR_BUILT", "YR_BLT", "YearBuilt", "year_built",
    "BUILT", "EFFYR", "EFF_YR", "EFFECT_YR", "YEAR_EFFEC",
    "ImprovementYear", "YEAR_IMPR",
)

# Common owner-type exclusion patterns (skip commercial/industrial)
SKIP_TYPES = ("commercial", "industrial", "exempt", "government", "utility")


class AssessorClient:
    """Fetches property-level data from county assessor ArcGIS services."""

    async def get_properties_in_zone(self, zone: dict) -> list[dict]:
        """
        Return property records for addresses within a storm zone.

        Zone dict should have: center (lat/lon), radius_miles, zip_codes.
        Properties are sorted by year built (oldest first = highest priority).
        """
        center = zone.get("center", {})
        lat = center.get("lat", 39.0997)
        lon = center.get("lon", -94.5786)
        radius = zone.get("radius_miles", 3.0)

        # Build bounding box from center + radius
        bbox = _radius_to_bbox(lat, lon, radius)

        properties: list[dict] = []

        # Jackson County MO — try primary service then backup
        jc_props = await self._fetch_jackson(bbox)
        properties.extend(jc_props)

        # Johnson County KS — try open data portal
        jo_props = await self._fetch_johnson(bbox)
        properties.extend(jo_props)

        # Filter to only residential properties inside the actual circle
        properties = [
            p for p in properties
            if _in_circle(p["lat"], p["lon"], lat, lon, radius)
            and p.get("property_type", "").lower() not in SKIP_TYPES
        ]

        # Sort oldest first (most likely to need roof replacement)
        properties.sort(key=lambda p: (p.get("year_built") or 9999))

        # Add rank
        for i, p in enumerate(properties):
            p["rank"] = i + 1

        logger.info(
            f"Assessor: {len(properties)} residential properties in zone "
            f"{zone.get('zone_id', '?')} (r={radius:.1f}mi)"
        )
        return properties

    async def _fetch_jackson(self, bbox: dict) -> list[dict]:
        """Query Jackson County MO ArcGIS service."""
        params = _arcgis_bbox_params(bbox)
        params["outFields"] = "*"  # request all fields to discover year_built
        params["resultRecordCount"] = MAX_RECORDS

        # Try primary AssessorAnalysis service
        data = await _arcgis_query(JACKSON_URL, params)
        if data:
            props = _parse_jackson(data)
            if props:
                return props

        # Fallback: parcel points service
        data = await _arcgis_query(JACKSON_PARCEL_URL, params)
        if data:
            return _parse_jackson_parcel(data)

        return []

    async def _fetch_johnson(self, bbox: dict) -> list[dict]:
        """Query Johnson County KS public parcel layer."""
        params = _arcgis_bbox_params(bbox)
        params["outFields"] = "*"
        params["resultRecordCount"] = MAX_RECORDS

        data = await _arcgis_query(JOHNSON_URL, params)
        if data:
            return _parse_johnson(data)
        return []


# ---- ArcGIS helpers ----

def _arcgis_bbox_params(bbox: dict) -> dict:
    """Standard ArcGIS REST query params for a bounding box in WGS84."""
    return {
        "geometry": f"{bbox['lon_min']},{bbox['lat_min']},{bbox['lon_max']},{bbox['lat_max']}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "true",
        "f": "json",
    }


async def _arcgis_query(url: str, params: dict) -> list[dict] | None:
    """Make an ArcGIS feature query and return features list, or None on error."""
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        features = data.get("features", [])
        if features is None:
            return None
        return features
    except Exception as e:
        logger.warning(f"ArcGIS query failed ({url}): {e}")
        return None


def _parse_jackson(features: list[dict]) -> list[dict]:
    """Parse Jackson County MO AssessorAnalysis features into property dicts."""
    props = []
    for f in features:
        attrs = f.get("attributes", {})
        geo = f.get("geometry", {})

        address = (
            attrs.get("SitusAddress") or
            attrs.get("FULLADDR") or
            attrs.get("situs_address") or ""
        ).strip()
        if not address:
            continue

        city = (attrs.get("SitusCity") or attrs.get("situs_city") or "").strip()
        state = "MO"
        zip_code = str(attrs.get("SitusZipCode") or attrs.get("ZIP") or "").strip()
        assessed = int(attrs.get("AssessedValue") or attrs.get("ASSESSED_VALUE") or 0)
        market = int(attrs.get("MarketValue") or attrs.get("MARKET_VALUE") or 0)
        owner = (attrs.get("owner") or attrs.get("OWNER") or "").strip()

        year_built = _find_year_built(attrs)
        lat, lon = _extract_coords(geo, attrs)
        ptype = _infer_property_type(attrs, assessed)

        if not lat or not lon:
            continue

        props.append({
            "address": address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "county": "Jackson",
            "lat": lat,
            "lon": lon,
            "year_built": year_built,
            "assessed_value": market or assessed,
            "owner": owner,
            "property_type": ptype,
        })
    return props


def _parse_jackson_parcel(features: list[dict]) -> list[dict]:
    """Parse Jackson County parcel point service features."""
    props = []
    for f in features:
        attrs = f.get("attributes", {})
        geo = f.get("geometry", {})

        address = (attrs.get("FULLADDR") or attrs.get("SitusAddress") or "").strip()
        if not address:
            continue

        lat, lon = _extract_coords(geo, attrs)
        if not lat or not lon:
            continue

        props.append({
            "address": address,
            "city": (attrs.get("MUNICIPALITY") or attrs.get("CITY") or "").strip(),
            "state": "MO",
            "zip": str(attrs.get("ZIP") or "").strip(),
            "county": "Jackson",
            "lat": lat,
            "lon": lon,
            "year_built": _find_year_built(attrs),
            "assessed_value": 0,
            "owner": "",
            "property_type": "residential",
        })
    return props


def _parse_johnson(features: list[dict]) -> list[dict]:
    """Parse Johnson County KS public parcel features."""
    props = []
    for f in features:
        attrs = f.get("attributes", {})
        geo = f.get("geometry", {})

        # Johnson County field names vary by service version
        address = (
            attrs.get("SITE_ADDR") or
            attrs.get("SiteAddress") or
            attrs.get("FULLADDR") or
            attrs.get("ADDRESS") or ""
        ).strip()
        if not address:
            continue

        city = (attrs.get("SITE_CITY") or attrs.get("CITY") or "Overland Park").strip()
        zip_code = str(attrs.get("SITE_ZIP") or attrs.get("ZIP") or "").strip()
        assessed = int(attrs.get("ASSESSED_VALUE") or attrs.get("TotalAssessed") or 0)
        owner = (attrs.get("OWNER_NAME") or attrs.get("OWNER") or "").strip()
        year_built = _find_year_built(attrs)
        lat, lon = _extract_coords(geo, attrs)
        ptype = _infer_property_type(attrs, assessed)

        if not lat or not lon:
            continue

        props.append({
            "address": address,
            "city": city,
            "state": "KS",
            "zip": zip_code,
            "county": "Johnson",
            "lat": lat,
            "lon": lon,
            "year_built": year_built,
            "assessed_value": assessed,
            "owner": owner,
            "property_type": ptype,
        })
    return props


def _find_year_built(attrs: dict[str, Any]) -> int | None:
    """Search an attributes dict for a year-built field."""
    for field in YEAR_BUILT_FIELDS:
        val = attrs.get(field)
        if val:
            try:
                yr = int(float(str(val)))
                if 1880 <= yr <= 2026:
                    return yr
            except (ValueError, TypeError):
                continue
    return None


def _extract_coords(geo: dict, attrs: dict) -> tuple[float | None, float | None]:
    """Extract lat/lon from ArcGIS geometry or attribute coordinate fields."""
    # GeoJSON-style point
    if geo.get("x") is not None and geo.get("y") is not None:
        x, y = float(geo["x"]), float(geo["y"])
        # Sanity check: must be in continental US lat/lon range
        if -130 < x < -60 and 25 < y < 50:
            return round(y, 5), round(x, 5)

    # Attribute fields (XCOORD/YCOORD or LAT/LON)
    for lat_f, lon_f in [("LAT", "LON"), ("YCOORD", "XCOORD"), ("latitude", "longitude")]:
        if attrs.get(lat_f) and attrs.get(lon_f):
            try:
                lat = float(attrs[lat_f])
                lon = float(attrs[lon_f])
                if 25 < lat < 50 and -130 < lon < -60:
                    return round(lat, 5), round(lon, 5)
            except (ValueError, TypeError):
                continue

    return None, None


def _infer_property_type(attrs: dict, assessed_value: int) -> str:
    """Guess residential vs commercial from attribute fields."""
    for field in ("PROPERTY_TYPE", "PropertyType", "LandUse", "USE_CODE", "CLASS"):
        val = str(attrs.get(field) or "").lower()
        for skip in SKIP_TYPES:
            if skip in val:
                return skip
        if any(t in val for t in ("residential", "single", "sfr", "dwelling")):
            return "residential"
    # Assessed value heuristic: very high = likely commercial
    if assessed_value > 1_000_000:
        return "commercial"
    return "residential"


# ---- Geometry helpers ----

def _radius_to_bbox(lat: float, lon: float, radius_miles: float) -> dict:
    """Convert center + radius to a bounding box (with 20% padding)."""
    pad = radius_miles * 1.2
    lat_deg = pad / 69.0
    lon_deg = pad / (69.0 * math.cos(math.radians(lat)))
    return {
        "lat_min": lat - lat_deg,
        "lat_max": lat + lat_deg,
        "lon_min": lon - lon_deg,
        "lon_max": lon + lon_deg,
    }


def _in_circle(lat: float, lon: float, clat: float, clon: float, radius_miles: float) -> bool:
    """Return True if (lat, lon) is within radius_miles of (clat, clon)."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat - clat)
    dlon = math.radians(lon - clon)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(clat)) * math.cos(math.radians(lat)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a)) <= radius_miles
