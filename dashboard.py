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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="StormLeads Dashboard")

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
    <span id="last-updated">—</span>
    <button class="btn btn-primary" onclick="runPipeline()">
      Scan for storms
    </button>
  </div>
</header>

<div class="main-layout">
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
  </div>
</div>

<script>
  // ---- MAP SETUP ----
  const map = L.map('map', {
    zoomControl: true,
    attributionControl: false
  }).setView([39.0997, -94.5786], 11);

  // Dark map tiles (free, no API key)
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 19
  }).addTo(map);

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
      'Updated ' + new Date().toLocaleTimeString();

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

  // ---- INIT ----
  loadZones();
</script>
</body>
</html>"""


if __name__ == "__main__":
    print()
    print("  ⚡ StormLeads Dashboard")
    print("  ────────────────────────")
    print("  Open in your browser: http://localhost:8000")
    print("  Press Ctrl+C to stop")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
