"""
Data models for weather events and storm reports.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SeverityLevel(Enum):
    """How severe the storm event is for our purposes."""
    LOW = "low"           # Marginal hail (<1"), moderate wind
    MODERATE = "moderate" # 1-1.5" hail, 58-70 mph wind
    HIGH = "high"         # 1.5-2.5" hail, 70-90 mph wind
    EXTREME = "extreme"   # 2.5"+ hail (baseball+), 90+ mph wind


class EventType(Enum):
    HAIL = "hail"
    WIND = "wind"
    TORNADO = "tornado"


@dataclass
class StormEvent:
    """A single storm event (hail strike or wind event) at a location."""
    event_type: EventType
    latitude: float
    longitude: float
    timestamp: datetime
    # Hail-specific
    hail_size_inches: float | None = None  # diameter
    # Wind-specific
    wind_speed_mph: float | None = None
    # Metadata
    source: str = ""          # "nws_alert", "spc_report", "spotter"
    description: str = ""
    severity: SeverityLevel = SeverityLevel.LOW
    # Location context (filled in by storm processor)
    nearest_city: str = ""
    county: str = ""
    state: str = ""
    zip_code: str = ""

    def __post_init__(self):
        """Auto-calculate severity based on measurements."""
        if self.event_type == EventType.HAIL and self.hail_size_inches:
            if self.hail_size_inches >= 2.5:
                self.severity = SeverityLevel.EXTREME
            elif self.hail_size_inches >= 1.5:
                self.severity = SeverityLevel.HIGH
            elif self.hail_size_inches >= 1.0:
                self.severity = SeverityLevel.MODERATE
            else:
                self.severity = SeverityLevel.LOW
        elif self.event_type == EventType.WIND and self.wind_speed_mph:
            if self.wind_speed_mph >= 90:
                self.severity = SeverityLevel.EXTREME
            elif self.wind_speed_mph >= 70:
                self.severity = SeverityLevel.HIGH
            elif self.wind_speed_mph >= 58:
                self.severity = SeverityLevel.MODERATE
            else:
                self.severity = SeverityLevel.LOW
        elif self.event_type == EventType.TORNADO:
            self.severity = SeverityLevel.EXTREME


@dataclass
class NWSAlert:
    """A parsed NWS weather alert."""
    alert_id: str
    event_type: str           # "Severe Thunderstorm Warning", etc.
    headline: str
    description: str
    severity: str             # NWS severity: Minor, Moderate, Severe, Extreme
    urgency: str              # Immediate, Expected, Future
    onset: datetime
    expires: datetime
    affected_zones: list[str] = field(default_factory=list)
    # Parsed from description text
    max_hail_inches: float | None = None
    max_wind_mph: float | None = None
    # Geometry (polygon defining the warning area)
    polygon_coords: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class SPCReport:
    """A single SPC storm report (from daily CSV)."""
    report_time: datetime
    event_type: EventType
    latitude: float
    longitude: float
    # Measurements
    hail_size_inches: float | None = None  # SPC reports in 100ths of inch
    wind_speed_mph: float | None = None
    # Location info from SPC
    location: str = ""
    county: str = ""
    state: str = ""
    source: str = ""    # "trained spotter", "public", "radar", etc.
    remarks: str = ""


@dataclass
class DamageZone:
    """A geographic zone with estimated storm damage.
    This is the output of the storm processor — it maps
    weather events to neighborhoods.
    """
    zone_id: str              # e.g. "KC-2025-0315-001"
    storm_date: datetime
    # Geographic bounds
    center_lat: float
    center_lon: float
    radius_miles: float       # how wide the damage zone is
    zip_codes: list[str] = field(default_factory=list)
    # Damage assessment
    max_hail_inches: float = 0.0
    max_wind_mph: float = 0.0
    event_count: int = 0      # how many reports in this zone
    severity: SeverityLevel = SeverityLevel.LOW
    # Calculated damage probability (0.0 to 1.0)
    damage_probability: float = 0.0
    # Source events that created this zone
    source_events: list[StormEvent] = field(default_factory=list)
    # Actual NWS warning polygon shape [[lat, lon], ...] — empty = use circle
    polygon_coords: list[list[float]] = field(default_factory=list)
    # Weighted epicenter of actual hail reports (heaviest hail = most weight)
    # Falls back to zone centroid when no point reports exist
    epicenter_lat: float = 0.0
    epicenter_lon: float = 0.0

    def calculate_damage_probability(self) -> float:
        """
        Estimate probability of actual roof/siding damage
        based on storm measurements.

        Based on insurance industry hail damage studies:
        - <1" hail: ~5-15% chance of shingle damage
        - 1-1.5": ~30-50% chance
        - 1.5-2": ~60-80% chance
        - 2"+: ~85-95% chance
        Wind adds to the probability (wind-driven hail is worse).
        """
        prob = 0.0

        # Hail component
        if self.max_hail_inches >= 2.5:
            prob = 0.95
        elif self.max_hail_inches >= 2.0:
            prob = 0.85
        elif self.max_hail_inches >= 1.5:
            prob = 0.70
        elif self.max_hail_inches >= 1.0:
            prob = 0.40
        elif self.max_hail_inches >= 0.75:
            prob = 0.15
        else:
            prob = 0.05

        # Wind boost — wind-driven hail causes more damage
        if self.max_wind_mph >= 80:
            prob = min(1.0, prob + 0.15)
        elif self.max_wind_mph >= 60:
            prob = min(1.0, prob + 0.10)

        # Multiple reports in same zone = higher confidence
        if self.event_count >= 5:
            prob = min(1.0, prob + 0.05)

        self.damage_probability = round(prob, 2)
        return self.damage_probability
