# StormLeads — Storm Damage Lead Generation Engine

AI-powered lead generation for storm damage restoration contractors.
Tracks severe weather, identifies damaged neighborhoods, and scores
properties by conversion likelihood.

## MVP Scope
- **Region**: Kansas City metro (NWS office EAX + surrounding)
- **Weather**: NWS API alerts + SPC storm reports (hail, wind)
- **Properties**: County assessor data (Jackson, Johnson, Wyandotte, Clay, Platte, Cass)
- **Scoring**: Weighted formula (no ML yet)

## Project Structure
```
stormleads/
├── config/
│   └── settings.py          # All configuration and API settings
├── src/
│   ├── weather/
│   │   ├── nws_client.py     # NWS API client (alerts, radar, forecasts)
│   │   ├── spc_client.py     # SPC storm reports parser (hail/wind CSVs)
│   │   ├── storm_tracker.py  # Storm event processor and zone mapper
│   │   └── models.py         # Data models for weather events
│   ├── db/
│   │   ├── schema.sql        # PostGIS schema
│   │   └── database.py       # Database connection and queries
│   ├── scoring/
│   │   └── lead_scorer.py    # Weighted scoring engine
│   └── utils/
│       └── geo.py            # Geocoding and spatial utilities
├── main.py                   # Entry point / scheduler
├── requirements.txt
└── README.md
```

## Setup
```bash
pip install -r requirements.txt

# Set up PostgreSQL + PostGIS (or use Supabase free tier)
psql -d stormleads -f src/db/schema.sql

# Run the storm tracker
python main.py
```

## Data Sources (all free)
| Source | URL | Data |
|--------|-----|------|
| NWS API | api.weather.gov | Alerts, forecasts, radar |
| SPC Storm Reports | spc.noaa.gov | Hail size, wind speed, damage |
| US Census TIGER | census.gov | Address geocoding, boundaries |
| County Assessors | varies by county | Owner, year built, value |

## Environment Variables
```
DATABASE_URL=postgresql://user:pass@localhost:5432/stormleads
NWS_USER_AGENT=StormLeads (your@email.com)
```
