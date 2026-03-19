"""
Golden Nugget finder — pinpoints specific streets and micro-areas with the
highest density of confirmed hail damage reports.

Pipeline:
  1. Collect individual hail report coords from zone source_event_locs
  2. Greedy cluster reports within 0.25 miles of each other
  3. Score clusters: report_count × max_hail^1.5  (denser + bigger hail = higher)
  4. Reverse-geocode top cluster centers via Nominatim (free OSM, no key needed)
  5. Return ranked list of street-level targets

Rate limit: Nominatim allows 1 request/second — geocoding 15 targets takes ~15s.
Results are cached in memory so subsequent calls are instant.
"""
import asyncio
import logging
from math import asin, cos, radians, sin, sqrt

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "StormLeads/1.0 (contact@stormleads.com)"}
CLUSTER_RADIUS_MILES = 0.25   # reports within this distance are in the same cluster
MAX_NUGGETS = 20

# In-memory cache: rounded (lat, lon) -> street string
_geocode_cache: dict[tuple, str] = {}


# ---- Geometry helpers ----

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return R * 2 * asin(sqrt(a))


# ---- Clustering ----

def _cluster_reports(reports: list[dict]) -> list[dict]:
    """
    Greedy single-linkage clustering.
    Seed with biggest hail first so high-value reports anchor the best clusters.
    """
    sorted_rpts = sorted(reports, key=lambda r: r.get("hail_inches", 0), reverse=True)

    clusters: list[dict] = []
    for rpt in sorted_rpts:
        lat, lon = rpt["lat"], rpt["lon"]
        best_cluster = None
        best_dist = CLUSTER_RADIUS_MILES

        for c in clusters:
            d = _haversine_miles(lat, lon, c["center_lat"], c["center_lon"])
            if d < best_dist:
                best_dist = d
                best_cluster = c

        if best_cluster:
            best_cluster["reports"].append(rpt)
            best_cluster["max_hail"] = max(best_cluster["max_hail"], rpt.get("hail_inches", 0))
            # Re-center using hail-size-weighted centroid
            weights = [r.get("hail_inches", 0.5) for r in best_cluster["reports"]]
            total_w = sum(weights)
            best_cluster["center_lat"] = sum(
                r["lat"] * w for r, w in zip(best_cluster["reports"], weights)
            ) / total_w
            best_cluster["center_lon"] = sum(
                r["lon"] * w for r, w in zip(best_cluster["reports"], weights)
            ) / total_w
        else:
            clusters.append({
                "center_lat": lat,
                "center_lon": lon,
                "reports": [rpt],
                "max_hail": rpt.get("hail_inches", 0),
                "zone_id": rpt.get("zone_id", ""),
                "storm_date": rpt.get("storm_date", ""),
                "tier": rpt.get("tier", "COLD"),
                "damage_prob": rpt.get("damage_prob", 0),
            })

    # Score and annotate
    for c in clusters:
        count = len(c["reports"])
        hail = c["max_hail"]
        c["score"] = round(count * (max(hail, 0.1) ** 1.5), 3)
        c["report_count"] = count
        # Inherit the highest damage_prob from any report in this cluster
        c["damage_prob"] = max(r.get("damage_prob", 0) for r in c["reports"])

    return sorted(clusters, key=lambda c: c["score"], reverse=True)


# ---- Geocoding ----

async def _reverse_geocode(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    """
    Return street info dict:  road, neighbourhood, city, display
    """
    key = (round(lat, 4), round(lon, 4))
    if key in _geocode_cache:
        return _geocode_cache[key]

    try:
        resp = await client.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 16, "addressdetails": 1},
            headers=HEADERS,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        addr = data.get("address", {})

        road = addr.get("road") or addr.get("pedestrian") or addr.get("path") or ""
        suburb = (
            addr.get("suburb")
            or addr.get("neighbourhood")
            or addr.get("quarter")
            or addr.get("residential")
            or ""
        )
        city = addr.get("city") or addr.get("town") or addr.get("village") or ""
        state = addr.get("state", "")

        if road and suburb:
            display = f"{road}, {suburb}"
        elif road and city:
            display = f"{road}, {city}"
        elif suburb and city:
            display = f"{suburb}, {city}"
        elif city:
            display = city
        else:
            parts = data.get("display_name", "").split(",")
            display = ", ".join(p.strip() for p in parts[:2])

        result = {
            "road": road,
            "suburb": suburb,
            "city": city,
            "state": state,
            "display": display,
        }
    except Exception as e:
        logger.warning(f"Nominatim geocode failed ({lat:.4f},{lon:.4f}): {e}")
        result = {
            "road": "",
            "suburb": "",
            "city": "",
            "state": "",
            "display": f"{round(lat, 4)}, {round(lon, 4)}",
        }

    _geocode_cache[key] = result
    return result


# ---- Main entry point ----

async def find_golden_nuggets(zones: list[dict], max_results: int = MAX_NUGGETS) -> list[dict]:
    """
    Find the top street-level hail damage hot spots from zone data.

    Zones must include `source_event_locs` (added by the pipeline).
    Returns a ranked list of nugget dicts.
    """
    # Collect all individual hail reports across zones
    all_reports: list[dict] = []
    for zone in zones:
        prob = zone.get("damage_probability", 0.0)
        for ev in zone.get("source_event_locs", []):
            all_reports.append({
                **ev,
                "zone_id": zone.get("zone_id", ""),
                "storm_date": zone.get("storm_date", ""),
                "tier": zone.get("tier", "COLD"),
                "damage_prob": prob,
            })

    if not all_reports:
        logger.info(
            "Golden nuggets: no source_event_locs in zone data. "
            "Re-run pipeline scan to capture street-level report coordinates."
        )
        return []

    # Only cluster hail reports (skip wind-only)
    hail_reports = [r for r in all_reports if r.get("hail_inches", 0) > 0]
    if not hail_reports:
        logger.info("Golden nuggets: no hail reports with size data found")
        return []

    logger.info(f"Golden nuggets: clustering {len(hail_reports)} hail reports")
    clusters = _cluster_reports(hail_reports)
    top_clusters = clusters[:max_results]
    logger.info(f"Golden nuggets: geocoding {len(top_clusters)} clusters via Nominatim")

    results = []
    async with httpx.AsyncClient() as client:
        for i, cluster in enumerate(top_clusters):
            if i > 0:
                await asyncio.sleep(1.1)  # Nominatim: max 1 req/sec

            geo = await _reverse_geocode(client, cluster["center_lat"], cluster["center_lon"])

            # Calculate display radius from actual report spread
            if cluster["report_count"] > 1:
                spreads = [
                    _haversine_miles(
                        cluster["center_lat"], cluster["center_lon"],
                        r["lat"], r["lon"],
                    )
                    for r in cluster["reports"]
                ]
                radius = round(max(min(max(spreads) + 0.05, 0.5), 0.15), 2)
            else:
                radius = 0.18

            results.append({
                "rank": i + 1,
                "lat": round(cluster["center_lat"], 5),
                "lon": round(cluster["center_lon"], 5),
                "street": geo["display"],
                "road": geo["road"],
                "suburb": geo["suburb"],
                "city": geo["city"],
                "max_hail": cluster["max_hail"],
                "report_count": cluster["report_count"],
                "score": cluster["score"],
                "damage_prob": round(cluster["damage_prob"] * 100, 1),
                "zone_id": cluster["zone_id"],
                "storm_date": cluster["storm_date"],
                "tier": cluster["tier"],
                "radius_miles": radius,
            })

    logger.info(f"Golden nuggets: {len(results)} street-level targets identified")
    return results
