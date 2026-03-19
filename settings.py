"""
StormLeads configuration.
All settings for the KC metro MVP.
"""
import os
from dataclasses import dataclass, field


@dataclass
class NWSConfig:
    """National Weather Service API configuration."""
    base_url: str = "https://api.weather.gov"
    # NWS requires a User-Agent with contact info — not an API key
    user_agent: str = os.getenv(
        "NWS_USER_AGENT", "StormLeads/1.0 (contact@stormleads.com)"
    )
    # NWS forecast offices covering KC metro
    # EAX = Pleasant Hill, MO (primary KC office)
    # TOP = Topeka, KS (covers western KC suburbs)
    forecast_offices: list[str] = field(default_factory=lambda: ["EAX", "TOP"])
    # How often to poll for new alerts (seconds)
    poll_interval: int = 21600  # 6 hours
    # Alert types we care about
    relevant_event_types: list[str] = field(default_factory=lambda: [
        "Severe Thunderstorm Warning",
        "Severe Thunderstorm Watch",
        "Tornado Warning",
        "Tornado Watch",
        "Special Weather Statement",
    ])
    # Minimum hail size we care about (inches) — 0.5" = marble size
    min_hail_inches: float = 0.5
    # Minimum wind speed we care about (mph)
    min_wind_mph: float = 40.0


@dataclass
class SPCConfig:
    """Storm Prediction Center storm reports configuration."""
    # SPC publishes daily CSVs of storm reports
    base_url: str = "https://www.spc.noaa.gov/climo/reports"
    # Report types: hail, wind, tornado
    report_types: list[str] = field(default_factory=lambda: ["hail", "wind"])


@dataclass
class KCMetroConfig:
    """Kansas City metro area geographic bounds."""
    # Bounding box for KC metro (generous bounds)
    lat_min: float = 38.75   # south edge (Cass County)
    lat_max: float = 39.45   # north edge (Clay/Platte)
    lon_min: float = -95.00  # west edge (Johnson County KS)
    lon_max: float = -94.20  # east edge (Jackson County)
    # Center point for radius-based queries
    center_lat: float = 39.0997
    center_lon: float = -94.5786
    # Search radius in miles
    radius_miles: float = 35.0
    # FIPS codes for target counties
    # Used for assessor lookups and Census data
    counties: dict[str, dict] = field(default_factory=lambda: {
        "jackson_mo": {
            "fips": "29095",
            "name": "Jackson County",
            "state": "MO",
        },
        "clay_mo": {
            "fips": "29047",
            "name": "Clay County",
            "state": "MO",
        },
        "platte_mo": {
            "fips": "29165",
            "name": "Platte County",
            "state": "MO",
        },
        "cass_mo": {
            "fips": "29037",
            "name": "Cass County",
            "state": "MO",
        },
        "johnson_ks": {
            "fips": "20091",
            "name": "Johnson County",
            "state": "KS",
        },
        "wyandotte_ks": {
            "fips": "20209",
            "name": "Wyandotte County",
            "state": "KS",
        },
    })
    # NWS zones that cover KC metro (for alert filtering)
    nws_zones: list[str] = field(default_factory=lambda: [
        "MOZ028", "MOZ029", "MOZ036", "MOZ037",  # MO counties
        "MOZ038", "MOZ046", "MOZ047",
        "KSZ104", "KSZ105",                       # KS counties
    ])


@dataclass
class ScoringConfig:
    """Lead scoring weights — these are your tuning knobs."""
    # Weights must sum to 1.0
    weight_damage_probability: float = 0.30
    weight_roof_age: float = 0.25
    weight_owner_occupied: float = 0.20
    weight_insurance_leniency: float = 0.15
    weight_property_value: float = 0.10
    # Tier thresholds
    hot_threshold: int = 80
    warm_threshold: int = 50


@dataclass
class DatabaseConfig:
    """Database connection settings."""
    url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://localhost:5432/stormleads"
    )


@dataclass
class Settings:
    """Root configuration object."""
    nws: NWSConfig = field(default_factory=NWSConfig)
    spc: SPCConfig = field(default_factory=SPCConfig)
    kc_metro: KCMetroConfig = field(default_factory=KCMetroConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


# Singleton
settings = Settings()
