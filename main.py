"""
StormLeads — Main entry point.

Runs the storm tracking pipeline and outputs damage zones.
In production this would run on a cron schedule (every 6 hours)
or trigger on NWS alert webhooks.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from storm_tracker import StormTracker
from models import DamageZone


def _report_radius(zone: DamageZone) -> float:
    """
    Compute a tight circle radius from the core concentration of hail reports.

    Uses the 75th-percentile distance from the epicenter — this covers the
    main damage cluster while dropping scattered outliers on the edge of the
    warning area. Adding a 0.75-mile buffer for ground-level uncertainty.

    Falls back to 1.5 miles for polygon-only zones with no direct reports.
    Minimum 0.75 miles so a single-point zone is still visible on the map.
    """
    if not zone.source_events:
        return 1.5

    distances = sorted(
        StormTracker._haversine_miles(
            zone.epicenter_lat, zone.epicenter_lon,
            e.latitude, e.longitude,
        )
        for e in zone.source_events
    )
    # 75th percentile = covers core damage area, excludes far-flung outliers
    idx = int(len(distances) * 0.75)
    p75 = distances[min(idx, len(distances) - 1)]
    # Cap at 6 miles — beyond that, reports are from multiple cells or
    # scattered across the broad warning polygon, not a single damage zone
    return round(max(min(p75 + 0.75, 6.0), 0.75), 2)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("stormleads")


async def run_storm_pipeline(days_back: int = 14):
    """
    Run the full storm tracking pipeline.
    This is what runs every 6 hours (or on demand after a storm).
    """
    logger.info("=" * 60)
    logger.info("StormLeads — Storm Tracking Pipeline")
    logger.info(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("Region: Kansas City Metro")
    logger.info(f"Looking back: {days_back} days")
    logger.info("=" * 60)

    tracker = StormTracker()

    # Step 1: Run the pipeline (fetch + cluster + score)
    zones = await tracker.run_pipeline(days_back=days_back)

    if not zones:
        logger.info("No actionable storm damage zones found.")
        logger.info("Pipeline complete — no leads to generate.")
        return []

    # Step 2: Map zones to zip codes
    zones = tracker.map_zones_to_zips(zones)

    # Step 3: Output results
    logger.info("=" * 60)
    logger.info(f"RESULTS: {len(zones)} damage zones identified")
    logger.info("=" * 60)

    results = []
    for zone in zones:
        result = {
            "zone_id": zone.zone_id,
            "storm_date": zone.storm_date.isoformat(),
            "center": {
                "lat": round(zone.center_lat, 4),
                "lon": round(zone.center_lon, 4),
            },
            "radius_miles": round(zone.radius_miles, 1),
            "damage_probability": zone.damage_probability,
            "severity": zone.severity.value,
            "max_hail_inches": zone.max_hail_inches,
            "max_wind_mph": zone.max_wind_mph,
            "event_count": zone.event_count,
            "zip_codes": zone.zip_codes,
            "epicenter": {
                "lat": round(zone.epicenter_lat, 4),
                "lon": round(zone.epicenter_lon, 4),
            },
            "report_radius_miles": _report_radius(zone),
            "tier": (
                "HOT" if zone.damage_probability >= 0.7
                else "WARM" if zone.damage_probability >= 0.4
                else "COLD"
            ),
            # Individual hail report coordinates — used by Golden Nugget finder
            "source_event_locs": [
                {
                    "lat": round(e.latitude, 5),
                    "lon": round(e.longitude, 5),
                    "hail_inches": e.hail_size_inches or 0.0,
                }
                for e in (zone.source_events or [])
                if e.latitude and e.longitude and e.hail_size_inches
            ],
        }
        results.append(result)

        tier_emoji = (
            "🔴" if result["tier"] == "HOT"
            else "🟡" if result["tier"] == "WARM"
            else "⚪"
        )
        logger.info(
            f"  {tier_emoji} {zone.zone_id} | "
            f"Damage prob: {zone.damage_probability:.0%} | "
            f"Hail: {zone.max_hail_inches}\" | "
            f"Wind: {zone.max_wind_mph}mph | "
            f"Zips: {len(zone.zip_codes)} | "
            f"Events: {zone.event_count}"
        )

    # Save results to JSON (would go to database in production)
    output_path = "damage_zones_latest.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Results saved to {output_path}")

    return results


def main():
    """Entry point."""
    asyncio.run(run_storm_pipeline())


if __name__ == "__main__":
    main()
