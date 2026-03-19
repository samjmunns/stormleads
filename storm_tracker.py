"""
Storm event processor.
Takes raw storm events from NWS, IEM LSR, and IEM SBW warning polygons,
builds accurate damage zones, and maps them to zip codes.

Pipeline:
1. NWS active alerts → point events (real-time)
2. IEM LSR → hail point reports (historical, better API than SPC)
3. IEM SBW → actual NWS warning polygons (accurate spatial shapes)
4. Build zones: polygon zones from SBW + circle zones for uncovered events
5. Score and map to zip codes
"""
import logging
from datetime import datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2

from models import (
    StormEvent, DamageZone, EventType, SeverityLevel,
)
from nws_client import NWSClient
from iem_client import IEMClient
from settings import settings

logger = logging.getLogger(__name__)


class StormTracker:
    """Processes storm events into actionable damage zones."""

    # Fallback circle clustering radius (miles) for events not in any polygon
    CLUSTER_RADIUS_MILES = 5.0
    CLUSTER_TIME_HOURS = 6

    def __init__(self):
        self.nws = NWSClient()
        self.iem = IEMClient()

    async def run_pipeline(
        self, days_back: int = 14
    ) -> list[DamageZone]:
        """
        Run the full storm tracking pipeline.

        Returns list of DamageZone objects sorted by damage probability.
        Polygon zones (from NWS warning shapes) are preferred over circles.
        """
        logger.info("Starting storm tracking pipeline...")

        # Step 1: NWS active alerts (real-time)
        nws_alerts = await self.nws.get_active_alerts()
        nws_events = self.nws.alerts_to_storm_events(nws_alerts)
        logger.info(f"NWS: {len(nws_events)} events from active alerts")

        # Step 2: IEM LSR hail reports (historical, replaces SPC CSV)
        iem_events = await self.iem.get_hail_events(days_back=days_back)
        logger.info(f"IEM LSR: {len(iem_events)} hail reports")

        # Step 3: IEM SBW warning polygons (actual spatial shapes)
        sbw_polygons = await self.iem.get_warning_polygons(days_back=days_back)
        logger.info(f"IEM SBW: {len(sbw_polygons)} warning polygons")

        all_events = nws_events + iem_events
        logger.info(f"Total point events: {len(all_events)}")

        if not all_events and not sbw_polygons:
            logger.info("No storm events found — clear skies!")
            return []

        # Step 4a: Build polygon zones from SBW warnings
        polygon_zones = self._zones_from_polygons(sbw_polygons, all_events)
        logger.info(f"Polygon zones: {len(polygon_zones)}")

        # Step 4b: Cluster any events not covered by a polygon zone
        covered_ids = {id(e) for z in polygon_zones for e in z.source_events}
        uncovered = [e for e in all_events if id(e) not in covered_ids]
        circle_zones = self._cluster_events(uncovered)
        logger.info(f"Circle zones (uncovered events): {len(circle_zones)}")

        zones = polygon_zones + circle_zones

        # Step 5: Score and sort
        for zone in zones:
            zone.calculate_damage_probability()
        zones.sort(key=lambda z: z.damage_probability, reverse=True)

        for zone in zones:
            shape = "polygon" if zone.polygon_coords else "circle"
            logger.info(
                f"Zone {zone.zone_id}: "
                f"prob={zone.damage_probability:.0%}, "
                f"hail={zone.max_hail_inches}\", "
                f"events={zone.event_count}, "
                f"shape={shape}"
            )

        return zones

    # ------------------------------------------------------------------ #
    #  POLYGON ZONE CREATION                                              #
    # ------------------------------------------------------------------ #

    def _zones_from_polygons(
        self,
        sbw_polygons: list[dict],
        events: list[StormEvent],
    ) -> list[DamageZone]:
        """
        Create DamageZones from NWS warning polygons.

        For each polygon:
        - Find LSR hail reports that fall inside it during the warning window
        - Use the polygon shape (not a circle) as the zone boundary
        - Hail size comes from matched LSR reports, falling back to hailtag
        - Skip polygons with no hail evidence
        """
        zones = []
        zone_counter = 0

        for poly in sbw_polygons:
            coords = poly["coords"]
            if len(coords) < 3:
                continue

            try:
                issued = datetime.fromisoformat(
                    poly["issued"].replace("Z", "+00:00")
                )
                expire_str = poly.get("expire", "")
                if expire_str:
                    expire = datetime.fromisoformat(
                        expire_str.replace("Z", "+00:00")
                    )
                else:
                    expire = issued + timedelta(hours=2)
            except ValueError:
                continue

            # Find events inside this polygon and time window
            matched = []
            for e in events:
                ts = e.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                time_ok = (
                    issued - timedelta(hours=1)
                    <= ts
                    <= expire + timedelta(hours=2)
                )
                if time_ok and self._point_in_polygon(
                    e.latitude, e.longitude, coords
                ):
                    matched.append(e)

            # Hail size: prefer measured reports, fall back to NWS tag
            max_hail = max(
                (e.hail_size_inches or 0.0 for e in matched), default=0.0
            )
            if max_hail == 0.0:
                max_hail = poly.get("hailtag_inches", 0.0)

            # Skip if no hail evidence at all
            if max_hail == 0.0 and not matched:
                continue

            max_wind = max(
                (e.wind_speed_mph or 0.0 for e in matched), default=0.0
            )

            # Centroid and radius from polygon vertices
            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            center_lat = sum(lats) / len(lats)
            center_lon = sum(lons) / len(lons)
            radius = max(
                self._haversine_miles(center_lat, center_lon, c[0], c[1])
                for c in coords
            )

            zone_counter += 1
            zones.append(DamageZone(
                zone_id=f"KC-{issued.strftime('%Y%m%d')}-{zone_counter:03d}",
                storm_date=issued,
                center_lat=center_lat,
                center_lon=center_lon,
                radius_miles=max(radius, 1.0),
                max_hail_inches=max_hail,
                max_wind_mph=max_wind,
                event_count=len(matched),
                source_events=matched,
                polygon_coords=coords,
            ))

        return zones

    # ------------------------------------------------------------------ #
    #  CIRCLE CLUSTERING (fallback for events outside any polygon)        #
    # ------------------------------------------------------------------ #

    def _cluster_events(
        self, events: list[StormEvent]
    ) -> list[DamageZone]:
        """Greedy spatial+temporal clustering for events not in a polygon."""
        if not events:
            return []

        events_sorted = sorted(events, key=lambda e: e.timestamp)
        assigned = [False] * len(events_sorted)
        zones = []
        zone_counter = 0

        for i, event in enumerate(events_sorted):
            if assigned[i]:
                continue
            cluster = [event]
            assigned[i] = True

            for j in range(i + 1, len(events_sorted)):
                if assigned[j]:
                    continue
                candidate = events_sorted[j]
                time_diff = abs(
                    (candidate.timestamp - event.timestamp).total_seconds()
                )
                if time_diff > self.CLUSTER_TIME_HOURS * 3600:
                    continue
                dist = self._haversine_miles(
                    event.latitude, event.longitude,
                    candidate.latitude, candidate.longitude,
                )
                if dist <= self.CLUSTER_RADIUS_MILES:
                    cluster.append(candidate)
                    assigned[j] = True

            zone_counter += 1
            zones.append(self._create_circle_zone(cluster, zone_counter))

        return zones

    def _create_circle_zone(
        self, events: list[StormEvent], counter: int
    ) -> DamageZone:
        """Create a circle DamageZone from a cluster of point events."""
        center_lat = sum(e.latitude for e in events) / len(events)
        center_lon = sum(e.longitude for e in events) / len(events)
        max_dist = max(
            (self._haversine_miles(
                center_lat, center_lon, e.latitude, e.longitude
            ) for e in events),
            default=0.0,
        )
        max_hail = max(
            (e.hail_size_inches or 0.0 for e in events), default=0.0
        )
        max_wind = max(
            (e.wind_speed_mph or 0.0 for e in events), default=0.0
        )
        storm_date = events[0].timestamp
        return DamageZone(
            zone_id=f"KC-{storm_date.strftime('%Y%m%d')}-{counter:03d}",
            storm_date=storm_date,
            center_lat=center_lat,
            center_lon=center_lon,
            radius_miles=max(max_dist, 1.0),
            max_hail_inches=max_hail,
            max_wind_mph=max_wind,
            event_count=len(events),
            source_events=events,
        )

    # ------------------------------------------------------------------ #
    #  CLUSTERING                                                         #
    # ------------------------------------------------------------------ #

    def _cluster_events(
        self, events: list[StormEvent]
    ) -> list[DamageZone]:
        """
        Cluster nearby storm events into damage zones.

        Uses a simple greedy spatial + temporal clustering:
        - Events within CLUSTER_RADIUS_MILES and CLUSTER_TIME_HOURS
          of each other belong to the same zone.
        - Each zone gets a unique ID and summary stats.

        This is intentionally simple — no need for DBSCAN or K-means
        when you're dealing with <100 events per storm.
        """
        if not events:
            return []

        # Sort by time so we process chronologically
        events_sorted = sorted(events, key=lambda e: e.timestamp)

        assigned = [False] * len(events_sorted)
        zones = []
        zone_counter = 0

        for i, event in enumerate(events_sorted):
            if assigned[i]:
                continue

            # Start a new cluster with this event
            cluster = [event]
            assigned[i] = True

            # Find all unassigned events near this one
            for j in range(i + 1, len(events_sorted)):
                if assigned[j]:
                    continue

                candidate = events_sorted[j]

                # Check time proximity
                time_diff = abs(
                    (candidate.timestamp - event.timestamp).total_seconds()
                )
                if time_diff > self.CLUSTER_TIME_HOURS * 3600:
                    continue

                # Check spatial proximity
                dist = self._haversine_miles(
                    event.latitude, event.longitude,
                    candidate.latitude, candidate.longitude,
                )
                if dist <= self.CLUSTER_RADIUS_MILES:
                    cluster.append(candidate)
                    assigned[j] = True

            # Create a DamageZone from this cluster
            zone_counter += 1
            zone = self._create_zone(cluster, zone_counter)
            zones.append(zone)

        return zones

    def _create_zone(
        self, events: list[StormEvent], counter: int
    ) -> DamageZone:
        """Create a DamageZone from a cluster of events."""
        # Calculate centroid
        center_lat = sum(e.latitude for e in events) / len(events)
        center_lon = sum(e.longitude for e in events) / len(events)

        # Calculate radius (max distance from centroid to any event)
        max_dist = 0.0
        for e in events:
            d = self._haversine_miles(
                center_lat, center_lon, e.latitude, e.longitude
            )
            max_dist = max(max_dist, d)

        # Aggregate max hail and wind
        max_hail = max(
            (e.hail_size_inches or 0.0 for e in events), default=0.0
        )
        max_wind = max(
            (e.wind_speed_mph or 0.0 for e in events), default=0.0
        )

        # Generate zone ID
        storm_date = events[0].timestamp
        zone_id = (
            f"KC-{storm_date.strftime('%Y%m%d')}-{counter:03d}"
        )

        return DamageZone(
            zone_id=zone_id,
            storm_date=storm_date,
            center_lat=center_lat,
            center_lon=center_lon,
            radius_miles=max(max_dist, 1.0),  # minimum 1 mile radius
            max_hail_inches=max_hail,
            max_wind_mph=max_wind,
            event_count=len(events),
            source_events=events,
        )

    # ------------------------------------------------------------------ #
    #  ZIP CODE MAPPING                                                   #
    # ------------------------------------------------------------------ #

    def map_zones_to_zips(
        self, zones: list[DamageZone]
    ) -> list[DamageZone]:
        """
        Map damage zones to zip codes.

        For MVP, we use a hardcoded lookup of KC metro zip codes
        with their approximate centroids. In production, this would
        use PostGIS spatial queries against TIGER/Line boundaries.
        """
        # KC metro zip codes with approximate centroids
        # Source: US Census ZCTA data
        kc_zips = {
            # ---- Kansas City MO core ----
            "64101": (39.1044, -94.5985),
            "64102": (39.0928, -94.5858),
            "64105": (39.1014, -94.5786),
            "64106": (39.1017, -94.5614),
            "64108": (39.0868, -94.5833),
            "64109": (39.0678, -94.5670),
            "64110": (39.0367, -94.5715),
            "64111": (39.0575, -94.5926),
            "64112": (39.0382, -94.5926),
            "64113": (39.0163, -94.5952),
            "64114": (38.9691, -94.5952),
            "64116": (39.1356, -94.5672),
            "64117": (39.1510, -94.5330),
            "64118": (39.1822, -94.5744),
            "64119": (39.1920, -94.5300),
            "64120": (39.1288, -94.5060),
            "64123": (39.1117, -94.5170),
            "64124": (39.1067, -94.5330),
            "64125": (39.0988, -94.4900),
            "64126": (39.0867, -94.4830),
            "64127": (39.0878, -94.5350),
            "64128": (39.0678, -94.5350),
            "64129": (39.0567, -94.5015),
            "64130": (39.0378, -94.5415),
            "64131": (38.9878, -94.5715),
            "64132": (39.0067, -94.5415),
            "64133": (39.0178, -94.4700),
            "64134": (38.9478, -94.5115),
            "64136": (39.0067, -94.4300),
            "64137": (38.9478, -94.4700),
            "64138": (38.9678, -94.4600),
            "64139": (38.9678, -94.4200),
            "64145": (38.8878, -94.5515),
            "64146": (38.8878, -94.5715),
            "64147": (38.8478, -94.5415),
            "64149": (38.8578, -94.5615),
            "64150": (39.1778, -94.6272),
            "64151": (39.1978, -94.6372),
            "64152": (39.2178, -94.6872),
            "64153": (39.2778, -94.7172),
            "64154": (39.2478, -94.6372),
            "64155": (39.2478, -94.5772),
            "64156": (39.2878, -94.5772),
            "64157": (39.2678, -94.5172),
            "64158": (39.2178, -94.5172),
            "64161": (39.1778, -94.4872),
            "64163": (39.3278, -94.6572),
            "64164": (39.3278, -94.5972),
            "64165": (39.3278, -94.5372),
            "64166": (39.3278, -94.4772),
            "64167": (39.3278, -94.4172),
            # ---- Raytown / Grandview / Belton MO (south KC) ----
            "64030": (38.8878, -94.5315),  # Grandview
            "64012": (38.8178, -94.5315),  # Belton
            "64034": (38.8578, -94.4615),  # Peculiar area
            "64029": (39.0078, -94.3700),  # Grain Valley
            "64083": (38.8278, -94.3715),  # Raymore
            "64080": (38.8078, -94.3515),  # Pleasant Hill
            # ---- Liberty / Kearney / Smithville MO (north KC) ----
            "64068": (39.2478, -94.4172),  # Liberty
            "64060": (39.3578, -94.3772),  # Kearney
            "64089": (39.3878, -94.5572),  # Smithville
            "64079": (39.3078, -94.4572),  # Platte City
            "64077": (39.3678, -94.2972),  # Orrick area
            # ---- Parkville / Riverside / NKC MO (northwest KC) ----
            "64152": (39.2178, -94.6872),
            "64153": (39.2778, -94.7172),
            "64154": (39.2478, -94.6372),
            "64150": (39.1778, -94.6272),
            "64116": (39.1356, -94.5672),
            # ---- Independence / Blue Springs / Grain Valley MO ----
            "64050": (39.0878, -94.4100),
            "64052": (39.0678, -94.4200),
            "64053": (39.1078, -94.3900),
            "64054": (39.0978, -94.3800),
            "64055": (39.0578, -94.3800),
            "64056": (39.0178, -94.3500),
            "64057": (39.0378, -94.3100),
            "64058": (39.1278, -94.3200),
            "64014": (39.0178, -94.2800),  # Blue Springs
            "64015": (38.9778, -94.2800),
            "64016": (39.0378, -94.2200),
            # ---- Lee's Summit / Lone Jack MO ----
            "64063": (38.9178, -94.3800),
            "64064": (38.9478, -94.3500),
            "64081": (38.9078, -94.3800),
            "64082": (38.8678, -94.3500),
            "64086": (38.9378, -94.3100),
            "64070": (38.8778, -94.2815),  # Lone Jack
            # ---- Johnson County KS (Overland Park, Prairie Village, etc.) ----
            "66202": (39.0013, -94.6703),  # Merriam / Mission
            "66203": (38.9913, -94.7003),
            "66204": (38.9813, -94.6803),  # Westwood / Roeland Park
            "66205": (39.0113, -94.6303),  # Prairie Village north
            "66206": (38.9613, -94.6303),  # Prairie Village south
            "66207": (38.9413, -94.6303),
            "66208": (38.9613, -94.6003),
            "66209": (38.9013, -94.6303),
            "66210": (38.9013, -94.6703),
            "66211": (38.9213, -94.6303),
            "66212": (38.9513, -94.6903),
            "66213": (38.8913, -94.6503),
            "66214": (38.9713, -94.7103),
            "66215": (38.9713, -94.7303),
            "66216": (38.9613, -94.7603),
            "66217": (38.9413, -94.7803),
            "66218": (38.9613, -94.8003),
            "66219": (38.9213, -94.7303),
            "66220": (38.8913, -94.7303),
            "66221": (38.8613, -94.6503),
            "66223": (38.8613, -94.6303),
            "66224": (38.8813, -94.6103),
            "66251": (38.9913, -94.6603),
            # ---- Olathe KS ----
            "66061": (38.8813, -94.8103),
            "66062": (38.8413, -94.7703),
            "66063": (38.8613, -94.7903),
            # ---- Lenexa / Shawnee / De Soto KS ----
            "66215": (38.9713, -94.7303),
            "66216": (38.9713, -94.7503),
            "66226": (38.9613, -94.8203),
            "66227": (38.9413, -94.8503),
            "66018": (38.9213, -94.8703),  # De Soto
            "66083": (38.8213, -94.8803),  # Spring Hill
            "66030": (38.7813, -94.9103),  # Gardner
            # ---- Wyandotte County KS (KCK) ----
            "66101": (39.1213, -94.6603),
            "66102": (39.1013, -94.6903),
            "66103": (39.0813, -94.6703),
            "66104": (39.1313, -94.7303),
            "66105": (39.0913, -94.6503),
            "66106": (39.0713, -94.7003),
            "66109": (39.1413, -94.7603),
            "66111": (39.0613, -94.7503),
            "66112": (39.1113, -94.7603),
            "66115": (39.1513, -94.6503),
        }

        for zone in zones:
            # Compute weighted epicenter from hail reports (bigger hail = more weight)
            if zone.source_events:
                total_w = sum(e.hail_size_inches or 1.0 for e in zone.source_events)
                zone.epicenter_lat = (
                    sum((e.hail_size_inches or 1.0) * e.latitude for e in zone.source_events)
                    / total_w
                )
                zone.epicenter_lon = (
                    sum((e.hail_size_inches or 1.0) * e.longitude for e in zone.source_events)
                    / total_w
                )
            else:
                zone.epicenter_lat = zone.center_lat
                zone.epicenter_lon = zone.center_lon

            # Match zip codes and sort by distance to epicenter (closest = hottest leads)
            matched_zips = []
            for zip_code, (lat, lon) in kc_zips.items():
                zone_dist = self._haversine_miles(
                    zone.center_lat, zone.center_lon, lat, lon
                )
                if zone_dist <= zone.radius_miles + 2.0:
                    epi_dist = self._haversine_miles(
                        zone.epicenter_lat, zone.epicenter_lon, lat, lon
                    )
                    matched_zips.append((zip_code, epi_dist))

            matched_zips.sort(key=lambda x: x[1])
            zone.zip_codes = list(dict.fromkeys(z for z, _ in matched_zips))
            logger.info(
                f"Zone {zone.zone_id}: epicenter ({zone.epicenter_lat:.3f}, "
                f"{zone.epicenter_lon:.3f}), {len(zone.zip_codes)} zip codes"
            )

        return zones

    # ------------------------------------------------------------------ #
    #  UTILITIES                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _point_in_polygon(
        lat: float, lon: float, coords: list[list[float]]
    ) -> bool:
        """
        Ray casting algorithm to check if (lat, lon) is inside a polygon.
        coords is [[lat, lon], ...] — Leaflet order.
        Accurate enough for the KC metro scale without any projection library.
        """
        inside = False
        n = len(coords)
        j = n - 1
        for i in range(n):
            lat_i, lon_i = coords[i]
            lat_j, lon_j = coords[j]
            if ((lon_i > lon) != (lon_j > lon)) and (
                lat < (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i) + lat_i
            ):
                inside = not inside
            j = i
        return inside

    @staticmethod
    def _haversine_miles(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Calculate distance between two points in miles."""
        R = 3959.0  # Earth radius in miles

        lat1_r, lat2_r = radians(lat1), radians(lat2)
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)

        a = (
            sin(dlat / 2) ** 2
            + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
        )
        c = 2 * atan2(sqrt(a), sqrt(1 - a))

        return R * c
