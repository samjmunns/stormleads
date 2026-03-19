"""
Storm Prediction Center (SPC) storm reports parser.
Downloads daily CSV reports of hail, wind, and tornado events.

SPC publishes these daily at:
  https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_filtered_hail.csv
  https://www.spc.noaa.gov/climo/reports/YYMMDD_rpts_filtered_wind.csv

These are ground-truth reports from trained spotters, law enforcement,
and the public — much more reliable than radar estimates alone.
"""
import csv
import io
import logging
from datetime import datetime, timedelta, timezone

import httpx

from models import StormEvent, SPCReport, EventType
from settings import settings

logger = logging.getLogger(__name__)


class SPCClient:
    """Client for SPC daily storm reports."""

    def __init__(self):
        self.base_url = settings.spc.base_url
        self.kc = settings.kc_metro

    async def get_reports(
        self,
        date: datetime | None = None,
        days_back: int = 3,
    ) -> list[SPCReport]:
        """
        Fetch SPC storm reports for one or more days.

        Args:
            date: Specific date to fetch. If None, fetches last N days.
            days_back: Number of days to look back (default 3).

        Returns:
            List of SPCReport objects within our KC metro bounds.
        """
        all_reports = []

        if date:
            dates = [date]
        else:
            today = datetime.now(timezone.utc)
            dates = [today - timedelta(days=i) for i in range(days_back)]

        for d in dates:
            for report_type in settings.spc.report_types:
                try:
                    reports = await self._fetch_daily_reports(d, report_type)
                    # Filter to KC metro area
                    kc_reports = [
                        r for r in reports
                        if self._is_in_kc_metro(r.latitude, r.longitude)
                    ]
                    all_reports.extend(kc_reports)
                    if kc_reports:
                        logger.info(
                            f"Found {len(kc_reports)} {report_type} reports "
                            f"in KC metro for {d.strftime('%Y-%m-%d')}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch {report_type} reports for "
                        f"{d.strftime('%Y-%m-%d')}: {e}"
                    )

        return all_reports

    async def _fetch_daily_reports(
        self, date: datetime, report_type: str
    ) -> list[SPCReport]:
        """
        Download and parse a single day's SPC report CSV.

        CSV format (hail):
          Time,Speed,Location,County,State,Lat,Lon,Comments
          Example: 1523,175,3 NW OLATHE,JOHNSON,KS,38.89,-94.84,

        Note: SPC "Speed" for hail = size in 100ths of inch (175 = 1.75")
              SPC "Speed" for wind = speed in knots
        """
        date_str = date.strftime("%y%m%d")
        url = (
            f"{self.base_url}/{date_str}_rpts_filtered_{report_type}.csv"
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

        reports = []
        content = response.text

        # SPC CSVs have a header row, then data
        reader = csv.reader(io.StringIO(content))

        # Skip header
        try:
            header = next(reader)
        except StopIteration:
            return []

        for row in reader:
            try:
                report = self._parse_row(row, report_type, date)
                if report:
                    reports.append(report)
            except Exception as e:
                logger.debug(f"Skipping malformed SPC row: {row} ({e})")

        return reports

    def _parse_row(
        self, row: list[str], report_type: str, date: datetime
    ) -> SPCReport | None:
        """Parse a single CSV row into an SPCReport."""
        if len(row) < 7:
            return None

        try:
            time_str = row[0].strip()
            speed_val = row[1].strip()
            location = row[2].strip()
            county = row[3].strip()
            state = row[4].strip()
            lat = float(row[5].strip())
            lon = float(row[6].strip())
            remarks = row[7].strip() if len(row) > 7 else ""
        except (ValueError, IndexError):
            return None

        # Parse time (HHMM format, UTC)
        try:
            hour = int(time_str[:2])
            minute = int(time_str[2:4])
            report_time = date.replace(
                hour=hour, minute=minute, second=0, microsecond=0,
                tzinfo=timezone.utc,
            )
        except (ValueError, IndexError):
            report_time = date

        # Parse measurement based on report type
        event_type = EventType.HAIL if report_type == "hail" else EventType.WIND
        hail_size = None
        wind_speed = None

        try:
            val = float(speed_val)
            if report_type == "hail":
                # SPC hail "Speed" is size in 100ths of inch
                hail_size = val / 100.0
            else:
                # SPC wind is in knots, convert to mph
                wind_speed = val * 1.151
        except ValueError:
            pass

        return SPCReport(
            report_time=report_time,
            event_type=event_type,
            latitude=lat,
            longitude=lon,
            hail_size_inches=hail_size,
            wind_speed_mph=wind_speed,
            location=location,
            county=county,
            state=state,
            source="spc_report",
            remarks=remarks,
        )

    def _is_in_kc_metro(self, lat: float, lon: float) -> bool:
        """Check if a coordinate falls within our KC metro bounding box."""
        return (
            self.kc.lat_min <= lat <= self.kc.lat_max
            and self.kc.lon_min <= lon <= self.kc.lon_max
        )

    def reports_to_storm_events(
        self, reports: list[SPCReport]
    ) -> list[StormEvent]:
        """Convert SPC reports into StormEvent objects."""
        events = []
        for report in reports:
            # Filter by our minimum thresholds
            if (
                report.event_type == EventType.HAIL
                and report.hail_size_inches
                and report.hail_size_inches < settings.nws.min_hail_inches
            ):
                continue
            if (
                report.event_type == EventType.WIND
                and report.wind_speed_mph
                and report.wind_speed_mph < settings.nws.min_wind_mph
            ):
                continue

            events.append(
                StormEvent(
                    event_type=report.event_type,
                    latitude=report.latitude,
                    longitude=report.longitude,
                    timestamp=report.report_time,
                    hail_size_inches=report.hail_size_inches,
                    wind_speed_mph=report.wind_speed_mph,
                    source="spc_report",
                    description=report.remarks,
                    county=report.county,
                    state=report.state,
                )
            )

        logger.info(
            f"Converted {len(reports)} SPC reports into "
            f"{len(events)} storm events (after threshold filter)"
        )
        return events
