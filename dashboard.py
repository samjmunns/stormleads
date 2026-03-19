"""
StormLeads Dashboard — Local web UI.

A single-file web server that shows storm damage zones on a map.
Run with: python dashboard.py
Then open: http://localhost:8000

Uses FastAPI to serve the page and the JSON data.
The map is built with Leaflet.js (loaded from CDN, no install needed).
"""
import json
import os
import asyncio
import secrets
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
import uvicorn

logger = logging.getLogger("stormleads.dashboard")

# ---- Auto-scan scheduler ----
scheduler = AsyncIOScheduler()

async def _auto_scan():
    """Runs the storm pipeline automatically every 6 hours."""
    logger.info("Auto-scan: starting scheduled pipeline run")
    try:
        from main import run_storm_pipeline
        zones = await run_storm_pipeline(days_back=14)
        logger.info(f"Auto-scan: complete — {len(zones)} zones found")
    except Exception as e:
        logger.error(f"Auto-scan: pipeline failed — {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: run pipeline immediately if no data file, then schedule every 6 hours
    if not DATA_FILE.exists():
        logger.info("No data file found — running initial storm scan")
        asyncio.create_task(_auto_scan())
    scheduler.add_job(_auto_scan, "interval", hours=6, id="auto_scan")
    scheduler.start()
    logger.info("Auto-scan scheduler started — pipeline runs every 6 hours")
    yield
    # On shutdown
    scheduler.shutdown()

app = FastAPI(title="StormLeads Dashboard", lifespan=lifespan)

# ---- Auth config (set these as environment variables in Railway) ----
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "stormleads2024")
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))


# ---- Auth middleware — runs on every request ----
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    public = {"/login", "/logout"}
    if request.url.path not in public:
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/login", status_code=302)
    return await call_next(request)


# SessionMiddleware must be added after the decorator so it runs first (outermost)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400 * 30)

# Path to pipeline output
DATA_FILE = Path("damage_zones_latest.json")

# Sample data for when there are no active storms
SAMPLE_DATA = [
    {
        "zone_id": "KC-20250315-001",
        "storm_date": "2025-03-15T18:30:00+00:00",
        "center": {"lat": 39.0178, "lon": -94.2800},
        "radius_miles": 3.2,
        "damage_probability": 0.85,
        "severity": "high",
        "max_hail_inches": 2.0,
        "max_wind_mph": 72.0,
        "event_count": 8,
        "zip_codes": ["64014", "64015", "64056", "64057", "64086"],
        "epicenter": {"lat": 39.0178, "lon": -94.2800},
        "report_radius_miles": 2.1,
        "tier": "HOT",
    },
    {
        "zone_id": "KC-20250315-002",
        "storm_date": "2025-03-15T19:15:00+00:00",
        "center": {"lat": 38.9613, "lon": -94.6303},
        "radius_miles": 4.1,
        "damage_probability": 0.70,
        "severity": "high",
        "max_hail_inches": 1.75,
        "max_wind_mph": 65.0,
        "event_count": 5,
        "zip_codes": ["66206", "66207", "66208", "66211", "66224"],
        "epicenter": {"lat": 38.9613, "lon": -94.6303},
        "report_radius_miles": 1.8,
        "tier": "HOT",
    },
    {
        "zone_id": "KC-20250315-003",
        "storm_date": "2025-03-15T18:45:00+00:00",
        "center": {"lat": 39.0867, "lon": -94.5350},
        "radius_miles": 2.8,
        "damage_probability": 0.55,
        "severity": "moderate",
        "max_hail_inches": 1.25,
        "max_wind_mph": 60.0,
        "event_count": 3,
        "zip_codes": ["64124", "64127", "64128", "64123"],
        "epicenter": {"lat": 39.0867, "lon": -94.5350},
        "report_radius_miles": 1.5,
        "tier": "WARM",
    },
    {
        "zone_id": "KC-20250315-004",
        "storm_date": "2025-03-15T20:00:00+00:00",
        "center": {"lat": 39.1920, "lon": -94.5300},
        "radius_miles": 2.0,
        "damage_probability": 0.40,
        "severity": "moderate",
        "max_hail_inches": 1.0,
        "max_wind_mph": 58.0,
        "event_count": 2,
        "zip_codes": ["64119", "64155", "64157", "64158"],
        "epicenter": {"lat": 39.1920, "lon": -94.5300},
        "report_radius_miles": 1.2,
        "tier": "WARM",
    },
    {
        "zone_id": "KC-20250315-005",
        "storm_date": "2025-03-15T19:30:00+00:00",
        "center": {"lat": 38.8813, "lon": -94.8103},
        "radius_miles": 1.5,
        "damage_probability": 0.25,
        "severity": "low",
        "max_hail_inches": 0.75,
        "max_wind_mph": 45.0,
        "event_count": 1,
        "zip_codes": ["66061", "66062"],
        "epicenter": {"lat": 38.8813, "lon": -94.8103},
        "report_radius_miles": 0.75,
        "tier": "COLD",
    },
]


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StormLeads — Sign In</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'DM Sans', sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .login-box {
    width: 360px;
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 12px;
    padding: 40px 36px;
  }
  .logo {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 28px;
    justify-content: center;
  }
  .logo-icon {
    width: 36px; height: 36px;
    background: linear-gradient(135deg, #f97316, #ef4444);
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
  }
  .logo h1 { font-size: 20px; font-weight: 700; color: #f0f6fc; }
  .logo h1 span { color: #f97316; }
  .tagline { text-align:center; font-size:13px; color:#6e7681; margin-bottom:28px; }
  label { display:block; font-size:12px; font-weight:600; color:#8b949e; margin-bottom:6px; }
  input[type=password] {
    width: 100%;
    padding: 10px 12px;
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #f0f6fc;
    font-family: inherit;
    font-size: 14px;
    outline: none;
    margin-bottom: 16px;
  }
  input[type=password]:focus { border-color: #f97316; }
  button {
    width: 100%;
    padding: 10px;
    background: #f97316;
    border: none;
    border-radius: 6px;
    color: #fff;
    font-family: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }
  button:hover { background: #ea580c; }
  .error {
    background: #3a1a1a;
    border: 1px solid #f85149;
    border-radius: 6px;
    padding: 10px 12px;
    font-size: 13px;
    color: #f85149;
    margin-bottom: 16px;
  }
  .footer { text-align:center; margin-top:20px; font-size:11px; color:#484f58; }
</style>
</head>
<body>
<div class="login-box">
  <div class="logo">
    <div class="logo-icon">&#9889;</div>
    <h1>Storm<span>Leads</span></h1>
  </div>
  <p class="tagline">KC Metro Storm Damage Intelligence</p>
  {error}
  <form method="post" action="/login">
    <label>Password</label>
    <input type="password" name="password" autofocus placeholder="Enter access password">
    <button type="submit">Sign In</button>
  </form>
  <p class="footer">Authorized access only</p>
</div>
</body>
</html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse(url="/", status_code=302)
    return LOGIN_PAGE.replace("{error}", "")


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if secrets.compare_digest(password, DASHBOARD_PASSWORD):
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=302)
    error = '<div class="error">Incorrect password. Please try again.</div>'
    return HTMLResponse(LOGIN_PAGE.replace("{error}", error), status_code=401)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@app.get("/api/zones")
async def get_zones(
    days: int = Query(default=14, ge=1, le=90),
    min_hail: float = Query(default=0.0, ge=0.0),
    min_wind: float = Query(default=0.0, ge=0.0),
):
    """Return damage zones filtered by age, hail size, and wind speed."""
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            data = json.load(f)
        if data:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            filtered = []
            for zone in data:
                try:
                    storm_dt = datetime.fromisoformat(zone["storm_date"])
                    if storm_dt.tzinfo is None:
                        storm_dt = storm_dt.replace(tzinfo=timezone.utc)
                    if storm_dt < cutoff:
                        continue
                except (KeyError, ValueError):
                    pass
                if min_hail > 0 and zone.get("max_hail_inches", 0) < min_hail:
                    continue
                if min_wind > 0 and zone.get("max_wind_mph", 0) < min_wind:
                    continue
                filtered.append(zone)
            return JSONResponse(content={"zones": filtered, "source": "live"})

    # Sample data — apply hail/wind filters but skip date filter
    filtered_sample = [
        z for z in SAMPLE_DATA
        if (min_hail == 0 or z.get("max_hail_inches", 0) >= min_hail)
        and (min_wind == 0 or z.get("max_wind_mph", 0) >= min_wind)
    ]
    return JSONResponse(content={"zones": filtered_sample, "source": "sample"})


@app.get("/api/leads")
async def get_leads(
    days: int = Query(default=14, ge=1, le=90),
    min_hail: float = Query(default=0.0, ge=0.0),
):
    """Return zip-level lead scores based on recent storm data + Census demographics."""
    from lead_scorer import score_leads

    # Reuse zone-loading logic
    zones_response = await get_zones(days=days, min_hail=min_hail, min_wind=0.0)
    zones_data = json.loads(zones_response.body)
    zones = zones_data.get("zones", [])
    source = zones_data.get("source", "unknown")

    leads = await score_leads(zones)
    return JSONResponse(content={"leads": leads, "source": source, "zone_count": len(zones)})


@app.get("/api/golden-nuggets")
async def get_golden_nuggets(
    days: int = Query(default=14, ge=1, le=90),
    min_hail: float = Query(default=0.0, ge=0.0),
    max_results: int = Query(default=15, ge=3, le=20),
):
    """Return street-level golden nugget targets from clustered hail reports."""
    from golden_nugget import find_golden_nuggets

    zones_response = await get_zones(days=days, min_hail=min_hail, min_wind=0.0)
    zones_data = json.loads(zones_response.body)
    zones = zones_data.get("zones", [])
    source = zones_data.get("source", "unknown")

    nuggets = await find_golden_nuggets(zones, max_results=max_results)
    needs_rescan = len(nuggets) == 0
    return JSONResponse(content={
        "nuggets": nuggets,
        "source": source,
        "needs_rescan": needs_rescan,
        "zone_count": len(zones),
    })


@app.get("/api/run-pipeline")
async def run_pipeline(days: int = Query(default=14, ge=1, le=90)):
    """Trigger the storm pipeline on demand."""
    try:
        from main import run_storm_pipeline
        results = await run_storm_pipeline(days_back=days)
        return JSONResponse(content={
            "status": "success",
            "zones_found": len(results),
        })
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=500,
        )


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the main dashboard page."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StormLeads — KC Metro Dashboard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'DM Sans', -apple-system, sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ---- HEADER ---- */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 24px;
    background: #161b22;
    border-bottom: 1px solid #21262d;
    flex-shrink: 0;
  }

  .logo {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .logo-icon {
    width: 32px; height: 32px;
    background: linear-gradient(135deg, #f97316, #ef4444);
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
  }

  .logo h1 {
    font-size: 18px;
    font-weight: 600;
    color: #f0f6fc;
    letter-spacing: -0.3px;
  }

  .logo h1 span { color: #f97316; }

  .header-meta {
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 13px;
    color: #8b949e;
  }

  .data-badge {
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .badge-live { background: #1a3a2a; color: #3fb950; }
  .badge-sample { background: #3a2a1a; color: #f0883e; }

  .btn {
    padding: 6px 14px;
    border-radius: 6px;
    border: 1px solid #30363d;
    background: #21262d;
    color: #c9d1d9;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
  }

  .btn:hover { background: #30363d; border-color: #8b949e; }
  .btn-primary { background: #f97316; border-color: #f97316; color: #fff; }
  .btn-primary:hover { background: #ea580c; }

  /* ---- LAYOUT ---- */
  .main-layout {
    display: flex;
    flex: 1;
    overflow: hidden;
  }

  /* ---- SIDEBAR ---- */
  .sidebar {
    width: 380px;
    background: #161b22;
    border-right: 1px solid #21262d;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    overflow: hidden;
  }

  .sidebar-header {
    padding: 16px 20px 12px;
    border-bottom: 1px solid #21262d;
  }

  .sidebar-header h2 {
    font-size: 14px;
    font-weight: 600;
    color: #f0f6fc;
    margin-bottom: 8px;
  }

  .stat-row {
    display: flex;
    gap: 8px;
  }

  .stat-chip {
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    font-family: 'DM Mono', monospace;
  }

  .stat-hot { background: #3a1a1a; color: #f85149; }
  .stat-warm { background: #3a2a1a; color: #f0883e; }
  .stat-cold { background: #1a2a3a; color: #58a6ff; }

  .zone-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }

  .zone-list::-webkit-scrollbar { width: 6px; }
  .zone-list::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }

  /* ---- ZONE CARD ---- */
  .zone-card {
    padding: 14px 16px;
    margin-bottom: 6px;
    border-radius: 8px;
    border: 1px solid #21262d;
    background: #0d1117;
    cursor: pointer;
    transition: all 0.15s;
  }

  .zone-card:hover { border-color: #30363d; background: #161b22; }
  .zone-card.active { border-color: #f97316; background: #1a1510; }

  .zone-card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .zone-id {
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    font-weight: 500;
    color: #f0f6fc;
  }

  .tier-badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }

  .tier-HOT { background: #f85149; color: #fff; }
  .tier-WARM { background: #f0883e; color: #fff; }
  .tier-COLD { background: #388bfd; color: #fff; }

  .zone-stats {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 6px;
  }

  .zone-stat {
    font-size: 11px;
    color: #8b949e;
  }

  .zone-stat strong {
    display: block;
    font-size: 16px;
    font-weight: 600;
    color: #c9d1d9;
    font-family: 'DM Mono', monospace;
  }

  .zone-zips {
    margin-top: 8px;
    font-size: 11px;
    color: #6e7681;
  }

  .zone-zips span {
    display: inline-block;
    background: #21262d;
    padding: 1px 6px;
    border-radius: 3px;
    margin: 1px 2px;
    font-family: 'DM Mono', monospace;
  }

  /* ---- MAP ---- */
  .map-container {
    flex: 1;
    position: relative;
  }

  #map { width: 100%; height: 100%; }

  .map-toggle-btn {
    position: absolute;
    top: 12px;
    right: 12px;
    z-index: 1000;
    padding: 7px 14px;
    border-radius: 6px;
    border: 1px solid rgba(255,255,255,0.15);
    background: rgba(13,17,23,0.85);
    color: #c9d1d9;
    font-family: 'DM Sans', sans-serif;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    backdrop-filter: blur(4px);
    transition: all 0.15s;
  }
  .map-toggle-btn:hover { background: rgba(30,37,46,0.95); border-color: rgba(255,255,255,0.3); }

  .leaflet-popup-content-wrapper {
    background: #161b22 !important;
    color: #c9d1d9 !important;
    border-radius: 8px !important;
    border: 1px solid #30363d !important;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4) !important;
  }

  .leaflet-popup-tip { background: #161b22 !important; }

  .popup-content h3 {
    font-family: 'DM Mono', monospace;
    font-size: 14px;
    color: #f0f6fc;
    margin-bottom: 6px;
  }

  .popup-content p {
    font-size: 12px;
    margin: 3px 0;
    color: #8b949e;
  }

  .popup-content strong { color: #c9d1d9; }

  /* ---- EPICENTER MARKER ---- */
  .epi-marker {
    width: 52px;
    height: 52px;
    border-radius: 50%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: 'DM Mono', monospace;
    font-weight: 700;
    background: rgba(13, 17, 23, 0.92);
    border: 2.5px solid;
    cursor: pointer;
    box-shadow: 0 0 0 4px rgba(0,0,0,0.3);
    line-height: 1.1;
  }
  .epi-label {
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 0.3px;
    opacity: 0.8;
    font-family: 'DM Sans', sans-serif;
  }
  .epi-size { font-size: 13px; }
  .epi-HOT { color: #f85149; border-color: #f85149; box-shadow: 0 0 0 4px rgba(248,81,73,0.2); }
  .epi-WARM { color: #f0883e; border-color: #f0883e; box-shadow: 0 0 0 4px rgba(240,136,62,0.15); }
  .epi-COLD { color: #388bfd; border-color: #388bfd; box-shadow: 0 0 0 4px rgba(56,139,253,0.15); }

  /* ---- TABS ---- */
  .tab-nav {
    display: flex;
    gap: 0;
    border-bottom: 1px solid #21262d;
    background: #161b22;
    flex-shrink: 0;
  }

  .tab-btn {
    padding: 10px 20px;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    color: #8b949e;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    transition: all 0.15s;
  }

  .tab-btn:hover { color: #c9d1d9; }
  .tab-btn.active { color: #f0f6fc; border-bottom-color: #f97316; }

  .tab-panel { display: none; flex: 1; overflow: hidden; flex-direction: column; }
  .tab-panel.active { display: flex; }

  /* ---- LEAD SCORER ---- */
  .scorer-panel {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    background: #0d1117;
  }

  .scorer-panel::-webkit-scrollbar { width: 6px; }
  .scorer-panel::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }

  .scorer-controls {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }

  .scorer-controls .filter-group { flex: 0 0 auto; }

  .scorer-table-wrap {
    overflow-x: auto;
    border-radius: 8px;
    border: 1px solid #21262d;
  }

  .scorer-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    min-width: 800px;
  }

  .scorer-table th {
    padding: 10px 14px;
    background: #161b22;
    color: #8b949e;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    text-align: left;
    border-bottom: 1px solid #21262d;
    white-space: nowrap;
  }

  .scorer-table th.numeric { text-align: right; }

  .scorer-table td {
    padding: 10px 14px;
    border-bottom: 1px solid #161b22;
    vertical-align: middle;
    white-space: nowrap;
  }

  .scorer-table tr:last-child td { border-bottom: none; }
  .scorer-table tr:hover td { background: #161b22; }

  .score-bar-cell { width: 140px; }

  .score-bar-wrap {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .score-bar {
    flex: 1;
    height: 6px;
    background: #21262d;
    border-radius: 3px;
    overflow: hidden;
  }

  .score-bar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.3s;
  }

  .score-val {
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    color: #f0f6fc;
    min-width: 36px;
    text-align: right;
  }

  .rank-num {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #6e7681;
    min-width: 24px;
  }

  .zip-code-cell {
    font-family: 'DM Mono', monospace;
    font-size: 14px;
    font-weight: 500;
    color: #f0f6fc;
  }

  .hail-cell {
    font-family: 'DM Mono', monospace;
    font-weight: 600;
  }

  .storm-counts {
    display: flex;
    gap: 6px;
  }

  .storm-count-chip {
    padding: 2px 6px;
    border-radius: 4px;
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    font-weight: 600;
  }

  .count-active { background: #1a3a2a; color: #3fb950; }
  .count-zero { background: #1a1f27; color: #484f58; }

  .demo-cell {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #8b949e;
    text-align: right;
  }

  .demo-cell.available { color: #c9d1d9; }

  .scorer-empty {
    text-align: center;
    padding: 60px 20px;
    color: #6e7681;
    font-size: 14px;
    line-height: 1.7;
  }

  /* ---- FILTER BAR ---- */
  .filter-bar {
    padding: 10px 20px 12px;
    border-bottom: 1px solid #21262d;
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }

  .filter-group {
    display: flex;
    flex-direction: column;
    gap: 3px;
    flex: 1;
    min-width: 80px;
  }

  .filter-group label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #6e7681;
  }

  .filter-group select {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 5px;
    color: #c9d1d9;
    font-family: inherit;
    font-size: 12px;
    padding: 4px 6px;
    cursor: pointer;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238b949e'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 6px center;
    padding-right: 20px;
  }

  .filter-group select:focus {
    outline: none;
    border-color: #f97316;
  }

  /* ---- EMPTY STATE ---- */
  .empty-state {
    padding: 40px 20px;
    text-align: center;
    color: #6e7681;
  }

  .empty-state p { font-size: 14px; line-height: 1.6; }

  /* ---- LOADING ---- */
  .loading-spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid #30363d;
    border-top-color: #f97316;
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    margin-right: 6px;
    vertical-align: middle;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ---- GOLDEN NUGGETS ---- */
  .nuggets-layout {
    display: flex;
    flex: 1;
    overflow: hidden;
  }

  .nuggets-sidebar {
    width: 360px;
    flex-shrink: 0;
    background: #161b22;
    border-right: 1px solid #21262d;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .nuggets-header {
    padding: 16px 20px 12px;
    border-bottom: 1px solid #21262d;
  }

  .nuggets-header h2 {
    font-size: 14px;
    font-weight: 600;
    color: #f0f6fc;
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }

  .nuggets-header p {
    font-size: 12px;
    color: #6e7681;
    line-height: 1.5;
  }

  .nuggets-controls {
    padding: 10px 20px 12px;
    border-bottom: 1px solid #21262d;
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: flex-end;
  }

  .nuggets-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }

  .nuggets-list::-webkit-scrollbar { width: 6px; }
  .nuggets-list::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }

  .nugget-card {
    padding: 12px 14px;
    margin-bottom: 6px;
    border-radius: 8px;
    border: 1px solid #21262d;
    background: #0d1117;
    cursor: pointer;
    transition: all 0.15s;
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }

  .nugget-card:hover { border-color: #30363d; background: #161b22; }
  .nugget-card.active { border-color: #f59e0b; background: #1a1500; }

  .nugget-rank {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: #f59e0b;
    color: #0d1117;
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    margin-top: 2px;
  }

  .nugget-rank.rank-top3 {
    background: linear-gradient(135deg, #f59e0b, #ef4444);
    box-shadow: 0 0 0 3px rgba(245,158,11,0.25);
  }

  .nugget-body { flex: 1; min-width: 0; }

  .nugget-street {
    font-size: 14px;
    font-weight: 600;
    color: #f0f6fc;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 5px;
  }

  .nugget-meta {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }

  .nugget-stat {
    font-size: 11px;
    color: #8b949e;
    display: flex;
    align-items: center;
    gap: 3px;
  }

  .nugget-stat strong { color: #c9d1d9; font-size: 12px; }

  .nugget-hail-hot { color: #f85149 !important; }
  .nugget-hail-warm { color: #f0883e !important; }

  .nugget-zone-tag {
    font-family: 'DM Mono', monospace;
    font-size: 10px;
    color: #484f58;
    margin-top: 4px;
  }

  .nugget-map-wrap {
    flex: 1;
    position: relative;
  }

  #nugget-map { width: 100%; height: 100%; }

  .copy-btn {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    margin: 8px;
    border-radius: 6px;
    border: 1px solid #30363d;
    background: #21262d;
    color: #c9d1d9;
    font-family: inherit;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    width: calc(100% - 16px);
    justify-content: center;
    transition: all 0.15s;
  }

  .copy-btn:hover { background: #30363d; }
  .copy-btn.copied { background: #1a3a2a; color: #3fb950; border-color: #3fb950; }

  .nugget-pin-icon {
    width: 30px;
    height: 30px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'DM Mono', monospace;
    font-weight: 700;
    font-size: 12px;
    border: 2.5px solid rgba(255,255,255,0.2);
    box-shadow: 0 2px 8px rgba(0,0,0,0.5);
    color: #0d1117;
  }

  .rescan-notice {
    margin: 20px;
    padding: 16px;
    border-radius: 8px;
    background: #1a1a1a;
    border: 1px solid #30363d;
    font-size: 13px;
    color: #8b949e;
    line-height: 1.6;
    text-align: center;
  }

  .rescan-notice strong { color: #f0f6fc; display: block; margin-bottom: 6px; }

  /* ---- SOURCES TAB ---- */
  .sources-panel {
    flex: 1;
    overflow-y: auto;
    padding: 32px;
    background: #0d1117;
  }

  .sources-panel::-webkit-scrollbar { width: 6px; }
  .sources-panel::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }

  .sources-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(440px, 1fr));
    gap: 16px;
    max-width: 1200px;
  }

  .source-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 10px;
    padding: 20px 24px;
  }

  .source-card-header {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    margin-bottom: 14px;
  }

  .source-icon {
    width: 40px;
    height: 40px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    flex-shrink: 0;
  }

  .source-icon-blue  { background: #0d2137; }
  .source-icon-green { background: #0d2a1a; }
  .source-icon-orange { background: #2a1a0d; }
  .source-icon-purple { background: #1a0d2a; }
  .source-icon-gold   { background: #2a2000; }
  .source-icon-red    { background: #2a0d0d; }

  .source-title {
    font-size: 15px;
    font-weight: 600;
    color: #f0f6fc;
    margin-bottom: 2px;
  }

  .source-subtitle {
    font-size: 12px;
    color: #6e7681;
  }

  .source-body {
    font-size: 13px;
    color: #8b949e;
    line-height: 1.7;
  }

  .source-body p { margin-bottom: 8px; }
  .source-body p:last-child { margin-bottom: 0; }

  .source-body strong { color: #c9d1d9; }

  .source-body .highlight {
    background: #21262d;
    border-left: 3px solid #f97316;
    padding: 8px 12px;
    border-radius: 0 6px 6px 0;
    margin: 10px 0;
    font-size: 12px;
  }

  .source-body .caveat {
    background: #1a1a0d;
    border-left: 3px solid #f0883e;
    padding: 8px 12px;
    border-radius: 0 6px 6px 0;
    margin: 10px 0;
    font-size: 12px;
    color: #8b7a6e;
  }

  .source-body .caveat strong { color: #f0883e; }

  .source-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: #58a6ff;
    text-decoration: none;
    font-size: 12px;
    margin-top: 8px;
  }

  .source-link:hover { text-decoration: underline; }

  .formula-table {
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0;
    font-size: 12px;
  }

  .formula-table th {
    text-align: left;
    padding: 5px 8px;
    color: #6e7681;
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    border-bottom: 1px solid #21262d;
  }

  .formula-table td {
    padding: 5px 8px;
    border-bottom: 1px solid #161b22;
    color: #8b949e;
    vertical-align: top;
  }

  .formula-table td:first-child {
    font-family: 'DM Mono', monospace;
    color: #c9d1d9;
    white-space: nowrap;
  }

  .formula-table tr:last-child td { border-bottom: none; }

  .sources-intro {
    max-width: 700px;
    margin-bottom: 24px;
  }

  .sources-intro h2 {
    font-size: 20px;
    font-weight: 700;
    color: #f0f6fc;
    margin-bottom: 6px;
  }

  .sources-intro p {
    font-size: 13px;
    color: #6e7681;
    line-height: 1.6;
  }

  .accuracy-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    margin-left: 6px;
    vertical-align: middle;
  }

  .acc-high   { background: #1a3a2a; color: #3fb950; }
  .acc-medium { background: #3a2a1a; color: #f0883e; }
  .acc-low    { background: #3a3a1a; color: #d29922; }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-icon">&#9889;</div>
    <h1>Storm<span>Leads</span></h1>
  </div>
  <div class="header-meta">
    <span id="data-source" class="data-badge badge-sample">Sample data</span>
    <span id="last-updated" style="font-size:12px;color:#6e7681">—</span>
    <span id="last-storm" style="font-size:12px;color:#6e7681">—</span>
    <button class="btn btn-primary" onclick="runPipeline()">
      Scan for storms
    </button>
    <a href="/logout" class="btn" style="text-decoration:none">Sign out</a>
  </div>
</header>

<div class="tab-nav">
  <button class="tab-btn active" id="tab-map-btn" onclick="switchTab('map')">&#127757; Storm Map</button>
  <button class="tab-btn" id="tab-leads-btn" onclick="switchTab('leads')">&#127919; Lead Scorer</button>
  <button class="tab-btn" id="tab-nuggets-btn" onclick="switchTab('nuggets')">&#128304; Golden Nuggets</button>
  <button class="tab-btn" id="tab-sources-btn" onclick="switchTab('sources')">&#128196; Sources</button>
</div>

<div class="main-layout">
  <!-- ===== MAP TAB ===== -->
  <div class="tab-panel active" id="tab-map" style="flex-direction:row;flex:1;overflow:hidden;">
    <aside class="sidebar">
      <div class="sidebar-header">
        <h2>Damage zones</h2>
        <div class="stat-row" id="stat-row">
          <span class="stat-chip stat-hot" id="stat-hot">0 hot</span>
          <span class="stat-chip stat-warm" id="stat-warm">0 warm</span>
          <span class="stat-chip stat-cold" id="stat-cold">0 cold</span>
        </div>
      </div>
      <div class="filter-bar">
        <div class="filter-group">
          <label>Storm age</label>
          <select id="filter-days" onchange="loadZones()">
            <option value="3">Last 3 days</option>
            <option value="7">Last 7 days</option>
            <option value="14" selected>Last 14 days</option>
            <option value="30">Last 30 days</option>
          </select>
        </div>
        <div class="filter-group">
          <label>Min hail</label>
          <select id="filter-hail" onchange="loadZones()">
            <option value="0" selected>Any size</option>
            <option value="0.5">0.5"+ (marble)</option>
            <option value="0.75">0.75"+ (penny)</option>
            <option value="1.0">1.0"+ (quarter)</option>
            <option value="1.5">1.5"+ (walnut)</option>
            <option value="2.0">2.0"+ (hen egg)</option>
          </select>
        </div>
        <div class="filter-group">
          <label>Min wind</label>
          <select id="filter-wind" onchange="loadZones()">
            <option value="0" selected>Any speed</option>
            <option value="40">40+ mph</option>
            <option value="58">58+ mph</option>
            <option value="70">70+ mph</option>
            <option value="80">80+ mph</option>
          </select>
        </div>
      </div>
      <div class="zone-list" id="zone-list"></div>
    </aside>
    <div class="map-container">
      <div id="map"></div>
      <button class="map-toggle-btn" id="map-toggle-btn" onclick="toggleMapLayer()">&#128754; Satellite</button>
    </div>
  </div>

  <!-- ===== GOLDEN NUGGETS TAB ===== -->
  <div class="tab-panel" id="tab-nuggets">
    <div class="nuggets-layout">
      <aside class="nuggets-sidebar">
        <div class="nuggets-header">
          <h2>&#128304; Golden Nugget Leads</h2>
          <p>Highest-density hail clusters — specific streets to canvass first.</p>
        </div>
        <div class="nuggets-controls">
          <div class="filter-group">
            <label>Storm age</label>
            <select id="nuggets-filter-days" onchange="loadNuggets()">
              <option value="3">Last 3 days</option>
              <option value="7">Last 7 days</option>
              <option value="14" selected>Last 14 days</option>
              <option value="30">Last 30 days</option>
            </select>
          </div>
          <div class="filter-group">
            <label>Min hail</label>
            <select id="nuggets-filter-hail" onchange="loadNuggets()">
              <option value="0" selected>Any size</option>
              <option value="0.75">0.75"+</option>
              <option value="1.0">1.0"+</option>
              <option value="1.5">1.5"+</option>
            </select>
          </div>
          <div class="filter-group">
            <label>Max targets</label>
            <select id="nuggets-max" onchange="loadNuggets()">
              <option value="5">Top 5</option>
              <option value="10">Top 10</option>
              <option value="15" selected>Top 15</option>
              <option value="20">Top 20</option>
            </select>
          </div>
        </div>
        <div id="nuggets-loading" style="display:none;padding:16px 20px;color:#8b949e;font-size:12px;line-height:1.8">
          <span class="loading-spinner"></span><span id="nuggets-loading-msg">Finding street addresses...</span><br>
          <span style="color:#484f58;font-size:11px">Geocoding addresses ~1/sec via OpenStreetMap</span>
        </div>
        <div id="nuggets-list"></div>
        <button class="copy-btn" id="copy-canvass-btn" onclick="copyCanvassList()" style="display:none">
          &#128203; Copy canvass list
        </button>
      </aside>
      <div class="nugget-map-wrap" style="position:relative">
        <div id="nugget-map"></div>
        <button class="map-toggle-btn" id="nugget-toggle-btn" onclick="toggleNuggetLayer()">&#128754; Satellite</button>
      </div>
    </div>
  </div>

  <!-- ===== SOURCES TAB ===== -->
  <div class="tab-panel" id="tab-sources">
    <div class="sources-panel">
      <div class="sources-intro">
        <h2>Data Sources &amp; Methodology</h2>
        <p>Every number in StormLeads is traceable to a public data source. This tab explains where the data comes from, how it is processed, and where the limitations are so you can make informed decisions in the field.</p>
      </div>
      <div class="sources-grid">

        <!-- NWS Alerts -->
        <div class="source-card">
          <div class="source-card-header">
            <div class="source-icon source-icon-blue">&#127782;</div>
            <div>
              <div class="source-title">NWS Active Alerts <span class="accuracy-badge acc-high">High confidence</span></div>
              <div class="source-subtitle">National Weather Service &mdash; api.weather.gov</div>
            </div>
          </div>
          <div class="source-body">
            <p>The <strong>Storm Map zones</strong> are seeded from NWS Severe Thunderstorm Warnings and Tornado Warnings issued for the Kansas City metro area (WFO EAX). These are official government-issued warnings with defined geographic polygons.</p>
            <div class="highlight">
              Endpoint: <strong>api.weather.gov/alerts/active</strong><br>
              Area filter: Kansas &amp; Missouri counties in the KC metro<br>
              Update cadence: Real-time (pulled on each pipeline run)
            </div>
            <p>Each warning includes the warned area polygon (the exact geographic shape the NWS drew), hail size and wind speed mentioned in the warning text, and timestamps for when the storm was active.</p>
            <div class="caveat">
              <strong>Limitation:</strong> NWS warnings cover the <em>potential</em> threat area, which is intentionally drawn larger than the actual damage footprint to protect public safety. A zone on the map does not mean every address inside it was hit.
            </div>
          </div>
        </div>

        <!-- IEM LSR Reports -->
        <div class="source-card">
          <div class="source-card-header">
            <div class="source-icon source-icon-green">&#127783;</div>
            <div>
              <div class="source-title">Hail &amp; Wind Reports <span class="accuracy-badge acc-high">High confidence</span></div>
              <div class="source-subtitle">Iowa Environmental Mesonet &mdash; IEM Local Storm Reports (LSR)</div>
            </div>
          </div>
          <div class="source-body">
            <p>Individual hail size and wind speed measurements come from the <strong>IEM LSR (Local Storm Reports)</strong> database. These are real-time spotter reports relayed through the NWS.</p>
            <div class="highlight">
              Endpoint: <strong>mesonet.agron.iastate.edu/geojson/lsr.php</strong><br>
              Sources: Trained NWS spotters, emergency managers, law enforcement<br>
              Coverage: KC metro bounding box (38.7&ndash;39.5&deg;N, 94.1&ndash;95.3&deg;W)
            </div>
            <p>Each LSR report includes: <strong>precise lat/lon</strong> of where the hail was measured, <strong>physical hail size</strong> in inches (someone picked it up and measured it), timestamp, and the source (spotter, public, law enforcement, etc.).</p>
            <p>These point reports are used to calculate the <strong>damage epicenter</strong> (weighted by hail size) and to power the Golden Nugget street-level clustering.</p>
            <div class="caveat">
              <strong>Limitation:</strong> Coverage is uneven. A 3&rdquo; hailstone only gets logged if a trained spotter was present. Suburban and rural areas are underreported compared to urban neighborhoods near NWS offices. Absence of a report does not mean no hail fell.
            </div>
          </div>
        </div>

        <!-- IEM SBW Polygons -->
        <div class="source-card">
          <div class="source-card-header">
            <div class="source-icon source-icon-blue">&#128205;</div>
            <div>
              <div class="source-title">Warning Polygons <span class="accuracy-badge acc-high">High confidence</span></div>
              <div class="source-subtitle">IEM Storm-Based Warnings &mdash; NWS Product Text</div>
            </div>
          </div>
          <div class="source-body">
            <p>The <strong>exact warning polygon shape</strong> shown on the map comes from the NWS warning product text, retrieved via the IEM Storm-Based Warnings (SBW) API.</p>
            <div class="highlight">
              Endpoint: <strong>mesonet.agron.iastate.edu/geojson/sbw_by_line.json</strong><br>
              Polygon format: LAT...LON blocks in NWS warning text (hundredths of degrees)<br>
              Example: <strong>3917 9448</strong> &rarr; 39.17&deg;N, 94.48&deg;W
            </div>
            <p>Each warning polygon is the actual boundary the NWS meteorologist drew at the time of issuance, reflecting the forecasted storm path and affected area. LSR point reports are matched to polygons using a ray-casting point-in-polygon algorithm.</p>
            <div class="caveat">
              <strong>Limitation:</strong> Warning polygons represent the <em>warned</em> area, not the actual damage area. They are typically 2&ndash;5x larger than the true damage footprint. The tight circles on the map (based on actual report spread) are a better indicator of where damage actually occurred.
            </div>
          </div>
        </div>

        <!-- Census ACS -->
        <div class="source-card">
          <div class="source-card-header">
            <div class="source-icon source-icon-purple">&#127968;</div>
            <div>
              <div class="source-title">Neighborhood Demographics <span class="accuracy-badge acc-high">2024 data</span></div>
              <div class="source-subtitle">US Census Bureau &mdash; American Community Survey (ACS) 5-Year Estimates</div>
            </div>
          </div>
          <div class="source-body">
            <p>Owner-occupancy rate, median household income, and median home value come from the <strong>ACS 5-year estimates</strong> at the ZIP Code Tabulation Area (ZCTA) level.</p>
            <div class="highlight">
              Endpoint: <strong>api.census.gov/data/2024/acs/acs5</strong><br>
              Variables: B25003 (owner-occupancy), B19013 (income), B25077 (home value)<br>
              No API key required &mdash; free public data
            </div>
            <table class="formula-table">
              <tr><th>Variable</th><th>What it means for leads</th></tr>
              <tr><td>Owner-occupancy %</td><td>Higher = more homeowners who can file insurance claims. Renters rarely initiate roof repairs.</td></tr>
              <tr><td>Median income</td><td>Sweet spot $65k&ndash;$110k: middle-class homeowners file claims quickly and follow through. Very high income = slower claim cycle, very low = may not have insurance.</td></tr>
              <tr><td>Median home value</td><td>Sweet spot $180k&ndash;$450k: homes worth insuring but not so high that owners use private adjusters who slow the process.</td></tr>
            </table>
            <div class="caveat">
              <strong>Limitation:</strong> The 5-year estimates pool survey responses from 2020&ndash;2024, so they reflect conditions across that range rather than a single snapshot. Home values in particular move faster than the survey captures. Treat as relative ranking rather than exact current values.
            </div>
          </div>
        </div>

        <!-- Damage Probability -->
        <div class="source-card">
          <div class="source-card-header">
            <div class="source-icon source-icon-orange">&#128200;</div>
            <div>
              <div class="source-title">Damage Probability Score <span class="accuracy-badge acc-medium">Modeled estimate</span></div>
              <div class="source-subtitle">Based on insurance industry hail damage studies</div>
            </div>
          </div>
          <div class="source-body">
            <p>The <strong>Dmg Prob %</strong> column estimates the likelihood that a home in the zone has visible hail damage to its roof or siding. It is <em>not</em> a guarantee — it is a model based on published research.</p>
            <table class="formula-table">
              <tr><th>Max Hail Size</th><th>Base Probability</th><th>Source</th></tr>
              <tr><td>2.5"+ (baseball)</td><td>95%</td><td>Near-certain shingle damage</td></tr>
              <tr><td>2.0"&ndash;2.5" (hen egg)</td><td>85%</td><td>Likely functional damage</td></tr>
              <tr><td>1.5"&ndash;2.0" (walnut)</td><td>70%</td><td>Probable damage to older roofs</td></tr>
              <tr><td>1.0"&ndash;1.5" (quarter)</td><td>40%</td><td>Possible damage, age-dependent</td></tr>
              <tr><td>0.75"&ndash;1.0" (penny)</td><td>15%</td><td>Cosmetic/minor only</td></tr>
              <tr><td>&lt; 0.75"</td><td>5%</td><td>Unlikely significant damage</td></tr>
            </table>
            <p>Boosts applied: <strong>+15%</strong> for wind &ge; 80 mph, <strong>+10%</strong> for wind &ge; 60 mph, <strong>+5%</strong> for 5+ reports in the same zone (higher confidence).</p>
            <div class="caveat">
              <strong>Limitation:</strong> Actual damage depends on roof age, material, pitch, and hail trajectory. A 20-year-old 3-tab shingle roof is far more likely to show damage than a 2-year-old impact-resistant roof at the same hail size. These probabilities assume an average KC metro housing stock.
            </div>
          </div>
        </div>

        <!-- Lead Score Formula -->
        <div class="source-card">
          <div class="source-card-header">
            <div class="source-icon source-icon-gold">&#127919;</div>
            <div>
              <div class="source-title">Lead Score Formula <span class="accuracy-badge acc-medium">Modeled estimate</span></div>
              <div class="source-subtitle">Composite ranking for insurance-covered roof replacements</div>
            </div>
          </div>
          <div class="source-body">
            <p>The <strong>Lead Score (0&ndash;100)</strong> on the Lead Scorer tab combines storm damage data with neighborhood demographics into a single number for prioritizing where to canvass.</p>
            <table class="formula-table">
              <tr><th>Component</th><th>Weight</th><th>Why</th></tr>
              <tr><td>Damage probability</td><td>40%</td><td>Primary driver — no damage, no lead</td></tr>
              <tr><td>Home value score</td><td>25%</td><td>Homes worth insuring in the $180k&ndash;$450k sweet spot</td></tr>
              <tr><td>Owner-occupancy</td><td>20%</td><td>Owners file claims; renters don&apos;t</td></tr>
              <tr><td>Income score</td><td>15%</td><td>Middle-income homeowners act fastest on claims</td></tr>
            </table>
            <div class="highlight">
              Score = (0.40 &times; DmgProb) + (0.25 &times; HomeValueScore) + (0.20 &times; OwnerRate) + (0.15 &times; IncomeScore)
            </div>
            <div class="caveat">
              <strong>Limitation:</strong> The weights are reasonable but not scientifically validated against actual conversion rates. If you have historical canvassing data (which streets converted vs. didn&apos;t), those conversion rates could be used to back-calibrate the weights.
            </div>
          </div>
        </div>

        <!-- Golden Nuggets -->
        <div class="source-card">
          <div class="source-card-header">
            <div class="source-icon source-icon-gold">&#128304;</div>
            <div>
              <div class="source-title">Golden Nugget Clustering <span class="accuracy-badge acc-high">High confidence</span></div>
              <div class="source-subtitle">Greedy spatial clustering of LSR point reports + OpenStreetMap geocoding</div>
            </div>
          </div>
          <div class="source-body">
            <p>Golden Nuggets identify the <strong>densest clusters of actual hail reports</strong> within 0.25 miles of each other — these are specific street-level locations where multiple spotters confirmed large hail falling.</p>
            <div class="highlight">
              Algorithm: Greedy clustering seeded by largest hail first<br>
              Cluster radius: 0.25 miles<br>
              Score: <strong>report_count &times; max_hail<sup>1.5</sup></strong><br>
              Geocoding: Nominatim (OpenStreetMap) &mdash; nominatim.openstreetmap.org
            </div>
            <p>The hail<sup>1.5</sup> exponent means hail size matters more than count — a single 4&rdquo; report outscores two 1&rdquo; reports because large hail causes disproportionately more damage.</p>
            <p>Street names come from <strong>OpenStreetMap via Nominatim reverse geocoding</strong> — free, no API key, but rate-limited to 1 request per second. Results are cached in memory for the session.</p>
            <div class="caveat">
              <strong>Limitation:</strong> Clusters are only as good as the underlying LSR reports. If no spotters were in a neighborhood, that neighborhood won&apos;t show up as a Golden Nugget even if it was hit. Always cross-reference with the broader storm zones on the map.
            </div>
          </div>
        </div>

        <!-- Confidence summary -->
        <div class="source-card">
          <div class="source-card-header">
            <div class="source-icon source-icon-red">&#9888;&#65039;</div>
            <div>
              <div class="source-title">Overall Confidence &amp; Limitations</div>
              <div class="source-subtitle">What to trust, what to verify in the field</div>
            </div>
          </div>
          <div class="source-body">
            <p><strong>Most reliable:</strong> Hail report locations and sizes (IEM LSR). These are physical measurements from trained spotters at specific coordinates.</p>
            <p><strong>Reliable as ranking tools:</strong> Lead scores and damage probabilities. Use them to decide <em>which streets to visit first</em>, not as guarantees of damage.</p>
            <p><strong>Use with caution:</strong> Census demographics are ACS 2024 5-year estimates (pooled 2020–2024). Home values move faster than the survey captures, so treat them as directional rather than exact.</p>
            <p><strong>What this tool cannot tell you:</strong></p>
            <table class="formula-table">
              <tr><td>Roof age &amp; material</td><td>Biggest factor after hail size — not in any public dataset</td></tr>
              <tr><td>Prior claim history</td><td>Homes recently re-roofed may not need work</td></tr>
              <tr><td>HOA restrictions</td><td>Some neighborhoods have contractor approval requirements</td></tr>
              <tr><td>MESH radar hail data</td><td>Radar-estimated hail (MRMS MESH) not yet integrated &mdash; would improve zone accuracy</td></tr>
            </table>
            <div class="highlight">
              Best practice: Use Golden Nuggets + Lead Scorer to choose your top 3&ndash;5 streets, then do a quick visual inspection (binoculars from the street) to confirm granulation on roofs before spending time door-knocking an entire neighborhood.
            </div>
          </div>
        </div>

      </div>
    </div>
  </div>

  <!-- ===== LEAD SCORER TAB ===== -->
  <div class="tab-panel" id="tab-leads">
    <div class="scorer-panel">
      <div class="scorer-controls">
        <div class="filter-group">
          <label>Storm age</label>
          <select id="leads-filter-days" onchange="loadLeads()">
            <option value="3">Last 3 days</option>
            <option value="7">Last 7 days</option>
            <option value="14" selected>Last 14 days</option>
            <option value="30">Last 30 days</option>
          </select>
        </div>
        <div class="filter-group">
          <label>Min hail</label>
          <select id="leads-filter-hail" onchange="loadLeads()">
            <option value="0" selected>Any size</option>
            <option value="0.75">0.75"+ (penny)</option>
            <option value="1.0">1.0"+ (quarter)</option>
            <option value="1.5">1.5"+ (walnut)</option>
          </select>
        </div>
        <span id="leads-source-badge" class="data-badge badge-sample" style="margin-left:auto">Sample data</span>
        <span id="leads-zone-count" style="font-size:12px;color:#6e7681"></span>
      </div>
      <div id="leads-loading" style="display:none;padding:20px;color:#8b949e;font-size:13px">
        <span class="loading-spinner"></span>Scoring leads — fetching Census demographics...
      </div>
      <div id="leads-table-wrap"></div>
    </div>
  </div>
</div>

<script>
  // ---- MAP SETUP ----
  const map = L.map('map', {
    zoomControl: true,
    attributionControl: false
  }).setView([39.0997, -94.5786], 11);

  // Map tile layers
  const darkTile = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 19 });
  const satelliteTile = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19 });
  const satelliteLabel = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', { maxZoom: 19 });
  darkTile.addTo(map);

  let isSatellite = false;
  function toggleMapLayer() {
    if (isSatellite) {
      map.removeLayer(satelliteTile);
      map.removeLayer(satelliteLabel);
      darkTile.addTo(map);
      isSatellite = false;
      document.getElementById('map-toggle-btn').textContent = '🛰 Satellite';
    } else {
      map.removeLayer(darkTile);
      satelliteTile.addTo(map);
      satelliteLabel.addTo(map);
      isSatellite = true;
      document.getElementById('map-toggle-btn').textContent = '🗺 Dark Map';
    }
  }

  // Layer group for damage zones
  const zoneLayer = L.layerGroup().addTo(map);

  // Store zone circles for click highlighting
  let zoneCircles = {};
  let activeZoneId = null;

  // ---- TIER COLORS ----
  const tierColors = {
    HOT:  { fill: '#f85149', stroke: '#f85149', opacity: 0.25 },
    WARM: { fill: '#f0883e', stroke: '#f0883e', opacity: 0.20 },
    COLD: { fill: '#388bfd', stroke: '#388bfd', opacity: 0.15 },
  };

  // ---- LOAD DATA ----
  async function loadZones() {
    const days = document.getElementById('filter-days').value;
    const minHail = document.getElementById('filter-hail').value;
    const minWind = document.getElementById('filter-wind').value;
    const params = new URLSearchParams({ days, min_hail: minHail, min_wind: minWind });
    try {
      const res = await fetch('/api/zones?' + params);
      const data = await res.json();
      renderZones(data.zones, data.source);
    } catch (e) {
      console.error('Failed to load zones:', e);
      document.getElementById('zone-list').innerHTML =
        '<div class="empty-state"><p>Could not load data.<br>Make sure the server is running.</p></div>';
    }
  }

  function renderZones(zones, source) {
    // Update source badge
    const badge = document.getElementById('data-source');
    if (source === 'live') {
      badge.textContent = 'Live data';
      badge.className = 'data-badge badge-live';
    } else {
      badge.textContent = 'Sample data';
      badge.className = 'data-badge badge-sample';
    }

    // Update timestamp
    document.getElementById('last-updated').textContent =
      'Scanned ' + new Date().toLocaleTimeString();

    // Show most recent storm date
    const stormEl = document.getElementById('last-storm');
    if (zones.length > 0) {
      const dates = zones.map(z => new Date(z.storm_date)).filter(d => !isNaN(d));
      if (dates.length) {
        const latest = new Date(Math.max(...dates));
        const daysAgo = Math.floor((Date.now() - latest) / 86400000);
        const dateStr = latest.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        stormEl.textContent = 'Last storm: ' + dateStr + ' (' + daysAgo + 'd ago)';
        stormEl.style.color = daysAgo <= 3 ? '#3fb950' : daysAgo <= 7 ? '#f0883e' : '#6e7681';
      }
    } else {
      stormEl.textContent = 'No storms in selected window';
      stormEl.style.color = '#484f58';
    }

    // Count tiers
    let hot = 0, warm = 0, cold = 0;
    zones.forEach(z => {
      if (z.tier === 'HOT') hot++;
      else if (z.tier === 'WARM') warm++;
      else cold++;
    });
    document.getElementById('stat-hot').textContent = hot + ' hot';
    document.getElementById('stat-warm').textContent = warm + ' warm';
    document.getElementById('stat-cold').textContent = cold + ' cold';

    // Clear existing
    zoneLayer.clearLayers();
    zoneCircles = {};
    const list = document.getElementById('zone-list');

    if (zones.length === 0) {
      list.innerHTML = '<div class="empty-state">' +
        '<p>No damage zones found.<br>Clear skies in KC metro!<br><br>' +
        'Click "Scan for storms" to check again,<br>or the dashboard will show sample data.</p></div>';
      return;
    }

    // Render each zone
    list.innerHTML = '';
    zones.forEach(zone => {
      // Map circle
      const tc = tierColors[zone.tier] || tierColors.COLD;
      const epiLat = zone.epicenter ? zone.epicenter.lat : zone.center.lat;
      const epiLon = zone.epicenter ? zone.epicenter.lon : zone.center.lon;

      // Tight circle based on actual report spread — not the broad warning polygon
      const reportRadius = (zone.report_radius_miles || 1.5) * 1609.34;
      const hasReports = zone.event_count > 0;
      const shape = L.circle([epiLat, epiLon], {
        radius: reportRadius,
        color: tc.stroke,
        weight: 2,
        fillColor: tc.fill,
        fillOpacity: hasReports ? tc.opacity + 0.05 : tc.opacity,
        dashArray: hasReports ? null : '5 4',  // dashed border = no direct reports
      }).addTo(zoneLayer);

      // Epicenter marker — weighted center of actual hail damage reports
      const epiIcon = L.divIcon({
        className: '',
        html: '<div class="epi-marker epi-' + zone.tier + '">' +
                '<span class="epi-size">' + zone.max_hail_inches + '"</span>' +
                '<span class="epi-label">hail</span>' +
              '</div>',
        iconSize: [52, 52],
        iconAnchor: [26, 26],
      });
      const topZipStr = zone.zip_codes.slice(0, 3).join(', ') || '—';
      L.marker([epiLat, epiLon], {icon: epiIcon, zIndexOffset: 1000})
        .bindPopup(
          '<div class="popup-content">' +
          '<h3>' + zone.zone_id + '</h3>' +
          '<p style="color:#f97316;font-size:11px;margin-bottom:6px">⬤ Damage epicenter</p>' +
          '<p>Damage probability: <strong>' + Math.round(zone.damage_probability * 100) + '%</strong></p>' +
          '<p>Max hail: <strong>' + zone.max_hail_inches + '"</strong></p>' +
          '<p>Reports in zone: <strong>' + (zone.event_count || 'polygon only') + '</strong></p>' +
          '<p>Top zip codes: <strong>' + topZipStr + '</strong></p>' +
          '<p style="color:#6e7681;font-size:11px">Zips sorted by proximity to epicenter</p>' +
          '</div>'
        )
        .addTo(zoneLayer);

      zoneCircles[zone.zone_id] = shape;

      // Sidebar card
      const card = document.createElement('div');
      card.className = 'zone-card';
      card.id = 'card-' + zone.zone_id;
      card.onclick = () => focusZone(zone);
      // Top 5 zips are already sorted by proximity to epicenter
      const topZips = zone.zip_codes.slice(0, 5);
      const moreZips = zone.zip_codes.length > 5 ? zone.zip_codes.length - 5 : 0;
      card.innerHTML =
        '<div class="zone-card-top">' +
          '<span class="zone-id">' + zone.zone_id + '</span>' +
          '<span class="tier-badge tier-' + zone.tier + '">' + zone.tier + '</span>' +
        '</div>' +
        '<div class="zone-stats">' +
          '<div class="zone-stat">Damage<strong>' + Math.round(zone.damage_probability * 100) + '%</strong></div>' +
          '<div class="zone-stat">Hail<strong>' + zone.max_hail_inches + '"</strong></div>' +
          '<div class="zone-stat">Reports<strong>' + (zone.event_count || '—') + '</strong></div>' +
        '</div>' +
        '<div class="zone-zips" title="Zip codes sorted by proximity to damage epicenter">' +
          '<span style="color:#6e7681;margin-right:2px">Top zips:</span>' +
          topZips.map(z => '<span>' + z + '</span>').join('') +
          (moreZips > 0 ? '<span style="opacity:0.5">+' + moreZips + ' more</span>' : '') +
        '</div>';
      list.appendChild(card);
    });

    // Fit map to show all zones
    if (zones.length > 0) {
      const bounds = L.latLngBounds(
        zones.map(z => [z.center.lat, z.center.lon])
      );
      map.fitBounds(bounds.pad(0.3));
    }
  }

  function focusZone(zone) {
    // Deactivate previous
    if (activeZoneId) {
      const prev = document.getElementById('card-' + activeZoneId);
      if (prev) prev.classList.remove('active');
    }

    // Activate new
    activeZoneId = zone.zone_id;
    const card = document.getElementById('card-' + zone.zone_id);
    if (card) card.classList.add('active');

    // Pan to epicenter (or polygon bounds for big zones) and open popup
    const shape = zoneCircles[zone.zone_id];
    const epiLat = zone.epicenter ? zone.epicenter.lat : zone.center.lat;
    const epiLon = zone.epicenter ? zone.epicenter.lon : zone.center.lon;
    if (shape) {
      if (zone.polygon_coords && zone.polygon_coords.length > 2) {
        map.flyToBounds(shape.getBounds(), { duration: 0.5, padding: [60, 60] });
      } else {
        map.flyTo([epiLat, epiLon], 13, { duration: 0.5 });
      }
      shape.openPopup();
    }
  }

  async function runPipeline() {
    const btn = event.target;
    btn.disabled = true;
    const days = document.getElementById('filter-days').value;
    btn.innerHTML = '<span class="loading-spinner"></span>Scanning...';

    try {
      const res = await fetch('/api/run-pipeline?days=' + days);
      const data = await res.json();

      if (data.status === 'success') {
        // Reload zones with fresh data
        await loadZones();
      } else {
        alert('Pipeline error: ' + (data.message || 'Unknown error'));
      }
    } catch (e) {
      alert('Could not run pipeline: ' + e.message);
    }

    btn.disabled = false;
    btn.textContent = 'Scan for storms';
  }

  // ---- TABS ----
  function switchTab(name) {
    ['map', 'leads', 'nuggets', 'sources'].forEach(t => {
      document.getElementById('tab-' + t).classList.toggle('active', t === name);
      document.getElementById('tab-' + t + '-btn').classList.toggle('active', t === name);
    });
    if (name === 'leads') {
      const mapDays = document.getElementById('filter-days').value;
      document.getElementById('leads-filter-days').value = mapDays;
      loadLeads();
    } else if (name === 'nuggets') {
      const mapDays = document.getElementById('filter-days').value;
      document.getElementById('nuggets-filter-days').value = mapDays;
      initNuggetMap();
      loadNuggets();
    } else {
      setTimeout(() => map.invalidateSize(), 50);
    }
  }

  // ---- LEAD SCORER ----
  let leadsLoaded = false;

  async function loadLeads() {
    const days = document.getElementById('leads-filter-days').value;
    const minHail = document.getElementById('leads-filter-hail').value;
    const wrap = document.getElementById('leads-table-wrap');
    const loading = document.getElementById('leads-loading');

    wrap.innerHTML = '';
    loading.style.display = 'block';
    leadsLoaded = false;

    try {
      const params = new URLSearchParams({ days, min_hail: minHail });
      const res = await fetch('/api/leads?' + params);
      const data = await res.json();

      loading.style.display = 'none';

      // Update source badge
      const badge = document.getElementById('leads-source-badge');
      if (data.source === 'live') {
        badge.textContent = 'Live data';
        badge.className = 'data-badge badge-live';
      } else {
        badge.textContent = 'Sample data';
        badge.className = 'data-badge badge-sample';
      }
      document.getElementById('leads-zone-count').textContent =
        data.zone_count + ' storm zone' + (data.zone_count === 1 ? '' : 's') + ' analyzed';

      renderLeads(data.leads || []);
      leadsLoaded = true;
    } catch (e) {
      loading.style.display = 'none';
      wrap.innerHTML = '<div class="scorer-empty">Could not load lead scores.<br>Make sure the server is running.</div>';
    }
  }

  function renderLeads(leads) {
    const wrap = document.getElementById('leads-table-wrap');
    if (!leads.length) {
      wrap.innerHTML = '<div class="scorer-empty">No zip codes to score.<br>Try widening the storm age filter or run a fresh scan.</div>';
      return;
    }

    function fmtMoney(v) {
      if (v == null) return '—';
      if (v >= 1000) return '$' + (v / 1000).toFixed(0) + 'k';
      return '$' + v;
    }
    function fmtPct(v) { return v == null ? '—' : v + '%'; }

    function scoreColor(s) {
      if (s >= 70) return '#3fb950';
      if (s >= 50) return '#f0883e';
      return '#8b949e';
    }

    function countChip(n) {
      const cls = n > 0 ? 'count-active' : 'count-zero';
      return '<span class="storm-count-chip ' + cls + '">' + n + '</span>';
    }

    let html = '<div class="scorer-table-wrap"><table class="scorer-table">';
    html += '<thead><tr>' +
      '<th>#</th>' +
      '<th>ZIP</th>' +
      '<th class="score-bar-cell">Lead Score</th>' +
      '<th class="numeric">Dmg Prob</th>' +
      '<th class="numeric">Max Hail</th>' +
      '<th>Storms (3d / 7d / 14d / 30d)</th>' +
      '<th class="numeric">Owner%</th>' +
      '<th class="numeric">Med Income</th>' +
      '<th class="numeric">Med Home Val</th>' +
      '</tr></thead><tbody>';

    leads.forEach((lead, i) => {
      const sc = lead.score;
      const col = scoreColor(sc);
      html += '<tr>' +
        '<td><span class="rank-num">' + (i + 1) + '</span></td>' +
        '<td class="zip-code-cell">' + lead.zip + '</td>' +
        '<td class="score-bar-cell">' +
          '<div class="score-bar-wrap">' +
            '<div class="score-bar"><div class="score-bar-fill" style="width:' + sc + '%;background:' + col + '"></div></div>' +
            '<span class="score-val" style="color:' + col + '">' + sc + '</span>' +
          '</div>' +
        '</td>' +
        '<td class="demo-cell available" style="text-align:right">' + lead.damage_prob + '%</td>' +
        '<td class="hail-cell" style="text-align:right;color:' + (lead.max_hail >= 1.5 ? '#f85149' : lead.max_hail >= 1.0 ? '#f0883e' : '#c9d1d9') + '">' +
          (lead.max_hail > 0 ? lead.max_hail + '"' : '—') +
        '</td>' +
        '<td><div class="storm-counts">' +
          countChip(lead.storms_3d) + countChip(lead.storms_7d) + countChip(lead.storms_14d) + countChip(lead.storms_30d) +
        '</div></td>' +
        '<td class="demo-cell ' + (lead.owner_rate != null ? 'available' : '') + '">' + fmtPct(lead.owner_rate) + '</td>' +
        '<td class="demo-cell ' + (lead.median_income != null ? 'available' : '') + '">' + fmtMoney(lead.median_income) + '</td>' +
        '<td class="demo-cell ' + (lead.median_home_value != null ? 'available' : '') + '">' + fmtMoney(lead.median_home_value) + '</td>' +
      '</tr>';
    });

    html += '</tbody></table></div>';

    // Score legend
    html += '<div style="margin-top:12px;font-size:11px;color:#6e7681;line-height:1.8">' +
      'Score = 40% damage probability + 25% home value + 20% owner-occupancy + 15% income &nbsp;|&nbsp; ' +
      'Storm counts show how many zones hit this zip in the last 3 / 7 / 14 / 30 days &nbsp;|&nbsp; ' +
      'Demographics: US Census ACS 5-year estimates' +
    '</div>';

    wrap.innerHTML = html;
  }

  // ---- GOLDEN NUGGETS ----
  let nuggetMap = null;
  let nuggetLayer = null;
  let activeNuggetRank = null;
  let currentNuggets = [];

  function initNuggetMap() {
    if (nuggetMap) {
      setTimeout(() => nuggetMap.invalidateSize(), 50);
      return;
    }
    nuggetMap = L.map('nugget-map', {
      zoomControl: true,
      attributionControl: false,
    }).setView([39.0997, -94.5786], 11);

    const nuggetDarkTile = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { maxZoom: 19 });
    const nuggetSatTile = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19 });
    const nuggetSatLabel = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', { maxZoom: 19 });
    nuggetDarkTile.addTo(nuggetMap);

    let isNuggetSat = false;
    window.toggleNuggetLayer = function() {
      if (isNuggetSat) {
        nuggetMap.removeLayer(nuggetSatTile);
        nuggetMap.removeLayer(nuggetSatLabel);
        nuggetDarkTile.addTo(nuggetMap);
        isNuggetSat = false;
        document.getElementById('nugget-toggle-btn').textContent = '🛰 Satellite';
      } else {
        nuggetMap.removeLayer(nuggetDarkTile);
        nuggetSatTile.addTo(nuggetMap);
        nuggetSatLabel.addTo(nuggetMap);
        isNuggetSat = true;
        document.getElementById('nugget-toggle-btn').textContent = '🗺 Dark Map';
      }
    };

    nuggetLayer = L.layerGroup().addTo(nuggetMap);
  }

  async function loadNuggets() {
    const days = document.getElementById('nuggets-filter-days').value;
    const minHail = document.getElementById('nuggets-filter-hail').value;
    const maxResults = document.getElementById('nuggets-max').value;
    const list = document.getElementById('nuggets-list');
    const loading = document.getElementById('nuggets-loading');
    const copyBtn = document.getElementById('copy-canvass-btn');

    list.innerHTML = '';
    copyBtn.style.display = 'none';
    loading.style.display = 'block';
    document.getElementById('nuggets-loading-msg').textContent =
      'Finding street addresses... (~' + Math.ceil(maxResults * 1.1) + 's)';

    try {
      const params = new URLSearchParams({ days, min_hail: minHail, max_results: maxResults });
      const res = await fetch('/api/golden-nuggets?' + params);
      const data = await res.json();
      loading.style.display = 'none';
      currentNuggets = data.nuggets || [];

      if (data.needs_rescan || currentNuggets.length === 0) {
        list.innerHTML =
          '<div class="rescan-notice">' +
          '<strong>Street-level data not available yet</strong>' +
          'Click "Scan for storms" on the Storm Map tab to run a fresh pipeline scan. ' +
          'This captures the exact coordinates of each hail report so Golden Nuggets can identify specific streets.' +
          '</div>';
        return;
      }

      renderNuggets(currentNuggets);
      copyBtn.style.display = 'flex';
    } catch (e) {
      loading.style.display = 'none';
      list.innerHTML = '<div class="rescan-notice"><strong>Error loading nuggets</strong>' + e.message + '</div>';
    }
  }

  function renderNuggets(nuggets) {
    const list = document.getElementById('nuggets-list');
    list.innerHTML = '';

    if (nuggetLayer) nuggetLayer.clearLayers();

    const bounds = [];

    nuggets.forEach((n, i) => {
      const isTop3 = i < 3;
      const hailColor = n.max_hail >= 2.0 ? '#f85149' : n.max_hail >= 1.0 ? '#f0883e' : '#c9d1d9';
      const pinColor = isTop3
        ? (i === 0 ? '#f59e0b' : i === 1 ? '#c0c0c0' : '#cd7f32')
        : '#6e7681';

      // Map: tight circle for report cluster
      if (nuggetMap) {
        const radiusM = (n.radius_miles || 0.2) * 1609.34;
        const tierColors = { HOT: '#f85149', WARM: '#f0883e', COLD: '#388bfd' };
        const fill = tierColors[n.tier] || '#8b949e';

        L.circle([n.lat, n.lon], {
          radius: radiusM,
          color: fill,
          weight: 2,
          fillColor: fill,
          fillOpacity: 0.25,
        }).addTo(nuggetLayer)
          .bindPopup(
            '<div class="popup-content">' +
            '<h3>#' + n.rank + ' ' + n.street + '</h3>' +
            '<p>Max hail: <strong>' + n.max_hail + '"</strong></p>' +
            '<p>Reports in cluster: <strong>' + n.report_count + '</strong></p>' +
            '<p>Damage probability: <strong>' + n.damage_prob + '%</strong></p>' +
            '<p>Zone: <strong>' + n.zone_id + '</strong></p>' +
            '</div>'
          );

        // Numbered pin marker
        const pinIcon = L.divIcon({
          className: '',
          html: '<div class="nugget-pin-icon" style="background:' + pinColor + ';border-color:rgba(255,255,255,0.3)">' + n.rank + '</div>',
          iconSize: [30, 30],
          iconAnchor: [15, 15],
        });
        L.marker([n.lat, n.lon], { icon: pinIcon, zIndexOffset: 500 })
          .addTo(nuggetLayer)
          .on('click', () => focusNugget(n));

        bounds.push([n.lat, n.lon]);
      }

      // Sidebar card
      const card = document.createElement('div');
      card.className = 'nugget-card';
      card.id = 'nugget-card-' + n.rank;
      card.onclick = () => focusNugget(n);

      const dateStr = n.storm_date ? new Date(n.storm_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';

      card.innerHTML =
        '<div class="nugget-rank' + (isTop3 ? ' rank-top3' : '') + '">' + n.rank + '</div>' +
        '<div class="nugget-body">' +
          '<div class="nugget-street" title="' + n.street + '">' + n.street + '</div>' +
          '<div class="nugget-meta">' +
            '<span class="nugget-stat"><strong class="' + (n.max_hail >= 2.0 ? 'nugget-hail-hot' : n.max_hail >= 1.0 ? 'nugget-hail-warm' : '') + '">' + n.max_hail + '"</strong> hail</span>' +
            '<span class="nugget-stat"><strong>' + n.report_count + '</strong> report' + (n.report_count !== 1 ? 's' : '') + '</span>' +
            '<span class="nugget-stat"><strong>' + n.damage_prob + '%</strong> dmg</span>' +
            (dateStr ? '<span class="nugget-stat" style="color:#484f58">' + dateStr + '</span>' : '') +
          '</div>' +
          '<div class="nugget-zone-tag">' + n.zone_id + (n.tier ? ' &bull; ' + n.tier : '') + '</div>' +
        '</div>';

      list.appendChild(card);
    });

    // Fit nugget map to show all nuggets
    if (nuggetMap && bounds.length > 0) {
      nuggetMap.fitBounds(L.latLngBounds(bounds).pad(0.4));
    }
  }

  function focusNugget(nugget) {
    if (activeNuggetRank) {
      const prev = document.getElementById('nugget-card-' + activeNuggetRank);
      if (prev) prev.classList.remove('active');
    }
    activeNuggetRank = nugget.rank;
    const card = document.getElementById('nugget-card-' + nugget.rank);
    if (card) {
      card.classList.add('active');
      card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    if (nuggetMap) {
      nuggetMap.flyTo([nugget.lat, nugget.lon], 15, { duration: 0.4 });
    }
  }

  function copyCanvassList() {
    if (!currentNuggets.length) return;
    const lines = ['StormLeads — Golden Nugget Canvass List', '='.repeat(40), ''];
    currentNuggets.forEach(n => {
      const date = n.storm_date ? new Date(n.storm_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
      lines.push(
        '#' + n.rank + ' ' + n.street,
        '   Hail: ' + n.max_hail + '" | Reports: ' + n.report_count + ' | Dmg prob: ' + n.damage_prob + '%' + (date ? ' | Storm: ' + date : ''),
        '   Zone: ' + n.zone_id,
        ''
      );
    });
    navigator.clipboard.writeText(lines.join('\\n')).then(() => {
      const btn = document.getElementById('copy-canvass-btn');
      btn.textContent = '✓ Copied!';
      btn.classList.add('copied');
      setTimeout(() => {
        btn.innerHTML = '&#128203; Copy canvass list';
        btn.classList.remove('copied');
      }, 2000);
    });
  }

  // ---- INIT ----
  loadZones();
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print()
    print("  ⚡ StormLeads Dashboard")
    print("  ────────────────────────")
    print(f"  Open in your browser: http://localhost:{port}")
    print("  Press Ctrl+C to stop")
    print()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
