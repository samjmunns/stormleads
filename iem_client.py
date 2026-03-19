"""
Iowa Environmental Mesonet (IEM) client.

Two data sources:
1. LSR (Local Storm Reports) — hail point data, better API than SPC CSVs,
   same underlying NWS spotter data.
2. SBW (Storm-Based Warnings) — actual NWS warning polygon shapes so zones
   appear as real footprints instead of estimated circles.

No API key required. IEM is a free public service from Iowa State.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from models import StormEvent, EventType
from settings import settings

logger = logging.getLogger(__name__)

LSR_URL = "https://mesonet.agron.iastate.edu/api/1/nws/lsrs_by_point.json"
SBW_LINE_URL = "https://mesonet.agron.iastate.edu/api/1/nws/sbw_by_line.json"
PRODUCT_TEXT_URL = "https://mesonet.agron.iastate.edu/api/1/nwstext"


class IEMClient:
    """Iowa Environmental Mesonet API client."""

    def __init__(self):
        self.kc = settings.kc_metro
        self.headers = {"User-Agent": settings.nws.user_agent}

    # ------------------------------------------------------------------ #
    #  LSR — HAIL POINT REPORTS                                           #
    # ------------------------------------------------------------------ #

    async def get_hail_events(self, days_back: int = 14) -> list[StormEvent]:
        """
        Fetch hail Local Storm Reports from IEM for KC metro.

        Same underlying data as SPC but via a proper JSON API —
        no CSV parsing, returns all reports within a radius of KC center.
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days_back)
        params = {
            "lon": self.kc.center_lon,
            "lat": self.kc.center_lat,
            "radius_miles": int(self.kc.radius_miles),
            "begints": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
                resp = await client.get(LSR_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"IEM LSR request failed: {e}")
            return []

        # IEM returns data as a list of objects (dicts), not arrays
        events = []

        for r in data.get("data", []):
            if r.get("typetext") != "HAIL":
                continue
            try:
                size = float(r.get("magnitude") or 0)
            except (ValueError, TypeError):
                continue
            if size < settings.nws.min_hail_inches:
                continue
            try:
                ts = datetime.fromisoformat(
                    r["valid"].replace("Z", "+00:00")
                )
                events.append(StormEvent(
                    event_type=EventType.HAIL,
                    latitude=float(r["lat"]),
                    longitude=float(r["lon"]),
                    timestamp=ts,
                    hail_size_inches=size,
                    source="iem_lsr",
                    description=r.get("remark") or "",
                    county=r.get("county", ""),
                    state=r.get("state", ""),
                ))
            except (KeyError, ValueError, TypeError) as e:
                logger.debug(f"Skipping LSR row: {e}")

        logger.info(
            f"IEM LSR: {len(events)} hail reports in KC metro (last {days_back}d)"
        )
        return events

    # ------------------------------------------------------------------ #
    #  SBW — WARNING POLYGONS                                             #
    # ------------------------------------------------------------------ #

    async def get_warning_polygons(self, days_back: int = 14) -> list[dict]:
        """
        Fetch NWS Severe Thunderstorm / Tornado warning polygons for KC metro.

        Strategy:
        1. Use sbw_by_line to find all warnings intersecting KC metro bbox
        2. Filter to hail-tagged events (hailtag > 0)
        3. Fetch each warning's raw product text and parse the LAT...LON polygon

        The LAT...LON block in NWS warning text is the actual warning polygon —
        this is what gets broadcast to emergency systems and is accurate to
        the nearest 0.01 degree (~0.5 mile).

        Returns list of dicts with:
          - issued / expire: ISO strings
          - wfo: forecast office
          - hailtag_inches: tagged hail size
          - coords: [[lat, lon], ...] (Leaflet order, ready to pass to L.polygon)
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days_back)

        # Use KC metro bounding box corners as the line
        kc = self.kc
        params = {
            "start_lat": kc.lat_min,
            "start_lon": kc.lon_min,
            "end_lat": kc.lat_max,
            "end_lon": kc.lon_max,
            "begints": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        try:
            async with httpx.AsyncClient(
                headers=self.headers, timeout=30.0
            ) as client:
                resp = await client.get(SBW_LINE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"IEM SBW by line failed: {e}")
            return []

        rows = data.get("data", [])
        # Filter to SV/TO warnings that were hail-tagged
        hail_warnings = [
            r for r in rows
            if r.get("phenomena") in ("SV", "TO")
            and r.get("hailtag") and float(r.get("hailtag") or 0) > 0
        ]
        logger.info(
            f"IEM SBW: {len(hail_warnings)} hail-tagged warnings "
            f"(of {len(rows)} total) in KC metro last {days_back}d"
        )

        # Fetch polygon from each warning's product text
        polygons = []
        async with httpx.AsyncClient(headers=self.headers, timeout=30.0) as client:
            for row in hail_warnings:
                product_id = row.get("product_id", "")
                if not product_id:
                    continue
                try:
                    r = await client.get(f"{PRODUCT_TEXT_URL}/{product_id}")
                    r.raise_for_status()
                    coords = self._parse_latlon_polygon(r.text)
                    if len(coords) < 3:
                        continue

                    polygons.append({
                        "issued": row.get("issue", ""),
                        "expire": row.get("expire", ""),
                        "wfo": row.get("wfo", ""),
                        "hailtag_inches": float(row.get("hailtag") or 0),
                        "coords": coords,
                    })
                except (httpx.HTTPError, ValueError) as e:
                    logger.debug(f"Could not fetch polygon for {product_id}: {e}")

        logger.info(f"IEM SBW: got {len(polygons)} polygons from warning texts")
        return polygons

    # ------------------------------------------------------------------ #
    #  UTILITIES                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_latlon_polygon(warning_text: str) -> list[list[float]]:
        """
        Parse the LAT...LON polygon block from NWS warning text.

        NWS format: "LAT...LON 3902 9487 3910 9495 3942 9483 3920 9442"
        Each pair is (lat*100, lon*100). Longitude is always positive in the
        text but is western hemisphere (negative in standard coordinates).
        """
        match = re.search(
            r"LAT\.\.\.LON\s+([\d\s]+?)(?=\n\n|TIME\.\.\.MOT|$$)",
            warning_text,
            re.DOTALL,
        )
        if not match:
            return []

        nums = re.findall(r"\d+", match.group(1))
        if len(nums) < 6 or len(nums) % 2 != 0:
            return []

        coords = []
        for i in range(0, len(nums), 2):
            lat = int(nums[i]) / 100.0
            lon = -int(nums[i + 1]) / 100.0  # western hemisphere
            # Sanity check — must be within continental US bounds
            if 24.0 <= lat <= 50.0 and -125.0 <= lon <= -65.0:
                coords.append([lat, lon])

        return coords
