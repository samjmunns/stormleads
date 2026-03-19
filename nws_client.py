"""
National Weather Service API client.
Fetches active alerts, parses hail/wind measurements from warning text,
and extracts warning polygons for geographic targeting.

API docs: https://www.weather.gov/documentation/services-web-api
No API key required — just a User-Agent header with contact info.
"""
import re
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from models import NWSAlert, StormEvent, EventType, SeverityLevel
from settings import settings

logger = logging.getLogger(__name__)


class NWSClient:
    """Client for the National Weather Service API."""

    def __init__(self):
        self.base_url = settings.nws.base_url
        self.headers = {
            "User-Agent": settings.nws.user_agent,
            "Accept": "application/geo+json",
        }
        self.relevant_events = settings.nws.relevant_event_types

    async def _get(self, endpoint: str) -> dict:
        """Make a GET request to the NWS API."""
        url = f"{self.base_url}{endpoint}"
        async with httpx.AsyncClient(
            headers=self.headers, timeout=30.0
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    # ------------------------------------------------------------------ #
    #  ACTIVE ALERTS                                                      #
    # ------------------------------------------------------------------ #

    async def get_active_alerts(self) -> list[NWSAlert]:
        """
        Fetch all active severe weather alerts for our target area.

        Uses the /alerts/active endpoint filtered by NWS zones
        covering the KC metro.
        """
        alerts = []

        # Query by zone — NWS zones map to counties
        for zone in settings.kc_metro.nws_zones:
            try:
                data = await self._get(f"/alerts/active/zone/{zone}")
                features = data.get("features", [])
                for feature in features:
                    alert = self._parse_alert(feature)
                    if alert and alert.event_type in self.relevant_events:
                        alerts.append(alert)
            except httpx.HTTPError as e:
                logger.warning(f"Failed to fetch alerts for zone {zone}: {e}")
            except Exception as e:
                logger.error(f"Error parsing alerts for zone {zone}: {e}")

        # Deduplicate by alert ID
        seen_ids = set()
        unique_alerts = []
        for alert in alerts:
            if alert.alert_id not in seen_ids:
                seen_ids.add(alert.alert_id)
                unique_alerts.append(alert)

        logger.info(
            f"Fetched {len(unique_alerts)} active alerts for KC metro"
        )
        return unique_alerts

    async def get_recent_alerts(self, hours_back: int = 48) -> list[NWSAlert]:
        """
        Fetch recent (not just active) alerts for our area.
        Useful for finding storms that already passed through.
        """
        alerts = []
        for office in settings.nws.forecast_offices:
            try:
                data = await self._get(f"/offices/{office}/headlines")
                # Parse headlines for recent severe weather
                for item in data.get("@graph", []):
                    if any(
                        kw in item.get("name", "").lower()
                        for kw in ["hail", "wind", "thunderstorm", "tornado"]
                    ):
                        logger.info(
                            f"Recent headline from {office}: {item.get('name')}"
                        )
            except httpx.HTTPError as e:
                logger.warning(
                    f"Failed to fetch headlines for office {office}: {e}"
                )
        return alerts

    # ------------------------------------------------------------------ #
    #  ALERT PARSING                                                      #
    # ------------------------------------------------------------------ #

    def _parse_alert(self, feature: dict) -> NWSAlert | None:
        """Parse a GeoJSON alert feature into our NWSAlert model."""
        props = feature.get("properties", {})

        try:
            alert = NWSAlert(
                alert_id=props.get("id", ""),
                event_type=props.get("event", ""),
                headline=props.get("headline", ""),
                description=props.get("description", ""),
                severity=props.get("severity", ""),
                urgency=props.get("urgency", ""),
                onset=self._parse_timestamp(props.get("onset", "")),
                expires=self._parse_timestamp(props.get("expires", "")),
                affected_zones=props.get("affectedZones", []),
            )

            # Extract hail and wind from the description text
            alert.max_hail_inches = self._extract_hail_size(
                props.get("description", "")
                + " "
                + props.get("parameters", {}).get("maxHailSize", [""])[0]
            )
            alert.max_wind_mph = self._extract_wind_speed(
                props.get("description", "")
                + " "
                + props.get("parameters", {}).get("maxWindGust", [""])[0]
            )

            # Extract warning polygon if available
            geometry = feature.get("geometry")
            if geometry and geometry.get("type") == "Polygon":
                coords = geometry["coordinates"][0]
                alert.polygon_coords = [
                    (lat, lon) for lon, lat in coords
                ]

            return alert

        except Exception as e:
            logger.error(f"Failed to parse alert: {e}")
            return None

    def _extract_hail_size(self, text: str) -> float | None:
        """
        Extract hail size from NWS warning text.

        NWS warnings typically say things like:
        - "Quarter size hail" (1.00")
        - "Golf ball size hail" (1.75")
        - "Baseball size hail" (2.75")
        - "Up to 2 inch hail"
        - "maxHailSize: 1.50"
        """
        if not text:
            return None

        text_lower = text.lower()

        # Try numeric pattern first: "X.XX inch hail" or "X inch hail"
        numeric = re.search(
            r'(\d+\.?\d*)\s*(?:inch|in\.?|")\s*(?:hail|diameter)', text_lower
        )
        if numeric:
            return float(numeric.group(1))

        # NWS maxHailSize parameter (already in inches)
        param = re.search(r'(\d+\.?\d*)', text)
        if param and "hail" in text_lower:
            val = float(param.group(1))
            if 0.5 <= val <= 5.0:  # sanity check
                return val

        # Common size descriptions used by NWS
        size_map = {
            "pea": 0.25,
            "marble": 0.50,
            "dime": 0.75,
            "penny": 0.75,
            "nickel": 0.88,
            "quarter": 1.00,
            "half dollar": 1.25,
            "walnut": 1.50,
            "ping pong": 1.50,
            "golf ball": 1.75,
            "hen egg": 2.00,
            "tennis ball": 2.50,
            "baseball": 2.75,
            "apple": 3.00,
            "softball": 4.00,
            "grapefruit": 4.50,
        }

        for name, size in size_map.items():
            if name in text_lower:
                return size

        return None

    def _extract_wind_speed(self, text: str) -> float | None:
        """
        Extract wind speed from NWS warning text.

        Patterns:
        - "60 mph wind gusts"
        - "winds up to 70 mph"
        - "maxWindGust: 65"
        """
        if not text:
            return None

        # Try "XX mph" pattern
        mph = re.search(r'(\d+)\s*mph', text.lower())
        if mph:
            return float(mph.group(1))

        # Try "XX knots" and convert
        knots = re.search(r'(\d+)\s*(?:knots|kts)', text.lower())
        if knots:
            return float(knots.group(1)) * 1.151

        return None

    def _parse_timestamp(self, ts: str) -> datetime:
        """Parse an ISO timestamp from NWS."""
        if not ts:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    #  CONVERT ALERTS TO STORM EVENTS                                     #
    # ------------------------------------------------------------------ #

    def alerts_to_storm_events(
        self, alerts: list[NWSAlert]
    ) -> list[StormEvent]:
        """
        Convert NWS alerts into StormEvent objects that can be
        fed into the storm processor.
        """
        events = []

        for alert in alerts:
            # We need either hail or wind data to create a useful event
            if not alert.max_hail_inches and not alert.max_wind_mph:
                continue

            # Use polygon centroid as event location, or skip
            if alert.polygon_coords:
                lat = sum(c[0] for c in alert.polygon_coords) / len(
                    alert.polygon_coords
                )
                lon = sum(c[1] for c in alert.polygon_coords) / len(
                    alert.polygon_coords
                )
            else:
                continue

            # Create hail event if hail was reported
            if (
                alert.max_hail_inches
                and alert.max_hail_inches >= settings.nws.min_hail_inches
            ):
                events.append(
                    StormEvent(
                        event_type=EventType.HAIL,
                        latitude=lat,
                        longitude=lon,
                        timestamp=alert.onset,
                        hail_size_inches=alert.max_hail_inches,
                        wind_speed_mph=alert.max_wind_mph,
                        source="nws_alert",
                        description=alert.headline,
                    )
                )

            # Create wind event if wind was significant
            elif (
                alert.max_wind_mph
                and alert.max_wind_mph >= settings.nws.min_wind_mph
            ):
                events.append(
                    StormEvent(
                        event_type=EventType.WIND,
                        latitude=lat,
                        longitude=lon,
                        timestamp=alert.onset,
                        wind_speed_mph=alert.max_wind_mph,
                        source="nws_alert",
                        description=alert.headline,
                    )
                )

        logger.info(
            f"Converted {len(alerts)} alerts into {len(events)} storm events"
        )
        return events
