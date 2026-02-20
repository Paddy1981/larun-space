/**
 * sattrack-globe.js — Leaflet map + satellite.js real-time rendering layer
 *
 * Uses satellite.js (browser SGP4) propagated from cached TLE records so we
 * don't hit the API on every render tick.  Groundtrack arc is fetched once
 * from the API when a satellite is selected.
 */

import { getGroundtrack } from "./sattrack-api.js";

const TILE_URL =
  "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR =
  '&copy; <a href="https://carto.com/">CARTO</a> contributors';

let _map = null;
let _markers = {};          // norad_id → Leaflet marker
let _tleRecords = {};       // norad_id → { tle_line1, tle_line2, satrec }
let _selectedId = null;
let _groundtrackLayer = null;
let _onSelectCallback = null;
let _animFrameId = null;

// ── Init ─────────────────────────────────────────────────────────────────────

export function initMap(containerId) {
  _map = L.map(containerId, {
    center: [20, 0],
    zoom: 2,
    zoomControl: true,
    attributionControl: true,
    worldCopyJump: true,
  });

  L.tileLayer(TILE_URL, {
    attribution: TILE_ATTR,
    subdomains: "abcd",
    maxZoom: 19,
  }).addTo(_map);

  return _map;
}

export function onSatelliteSelect(cb) {
  _onSelectCallback = cb;
}

// ── TLE cache management ──────────────────────────────────────────────────────

export function loadTles(tleList) {
  // tleList: [{norad_id, tle_line1, tle_line2, name, orbit_class}, ...]
  tleList.forEach((row) => {
    if (!row.tle_line1 || !row.tle_line2) return;
    try {
      const satrec = satellite.twoline2satrec(row.tle_line1, row.tle_line2);
      _tleRecords[row.norad_id] = { ...row, satrec };
    } catch (_) {
      // skip bad TLEs silently
    }
  });
}

export function clearTles() {
  _tleRecords = {};
  Object.values(_markers).forEach((m) => _map.removeLayer(m));
  _markers = {};
}

// ── Satellite marker creation ─────────────────────────────────────────────────

function _orbitColor(orbit_class) {
  const colors = { LEO: "#6366f1", MEO: "#3b82f6", GEO: "#fbbf24", HEO: "#10b981" };
  return colors[orbit_class] || "#888888";
}

function _makeIcon(color, selected = false) {
  const size = selected ? 12 : 7;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size + 4}" height="${size + 4}">
    <circle cx="${(size + 4) / 2}" cy="${(size + 4) / 2}" r="${size / 2}"
      fill="${color}" stroke="${selected ? "#fff" : "rgba(0,0,0,0.4)"}" stroke-width="${selected ? 2 : 1}"/>
  </svg>`;
  return L.divIcon({
    html: svg,
    className: "",
    iconSize: [size + 4, size + 4],
    iconAnchor: [(size + 4) / 2, (size + 4) / 2],
  });
}

// ── Real-time propagation loop ────────────────────────────────────────────────

function _propagateNow(satrec) {
  const now = new Date();
  const posVel = satellite.propagate(satrec, now);
  if (!posVel || !posVel.position) return null;
  const gmst = satellite.gstime(now);
  const geo = satellite.eciToGeodetic(posVel.position, gmst);
  return {
    lat: satellite.degreesLat(geo.latitude),
    lon: satellite.degreesLong(geo.longitude),
    alt_km: geo.height,
  };
}

function _tick() {
  const ids = Object.keys(_tleRecords);
  ids.forEach((nid) => {
    const rec = _tleRecords[nid];
    const pos = _propagateNow(rec.satrec);
    if (!pos) return;

    const color = _orbitColor(rec.orbit_class);
    const selected = parseInt(nid) === _selectedId;

    if (_markers[nid]) {
      _markers[nid].setLatLng([pos.lat, pos.lon]);
      if (selected) _markers[nid].setIcon(_makeIcon(color, true));
    } else {
      const icon = _makeIcon(color, selected);
      const marker = L.marker([pos.lat, pos.lon], { icon, title: rec.name })
        .addTo(_map)
        .on("click", () => _handleClick(parseInt(nid)));
      _markers[nid] = marker;
    }
  });

  _animFrameId = requestAnimationFrame(_tick);
}

export function startRendering() {
  if (_animFrameId) cancelAnimationFrame(_animFrameId);
  _animFrameId = requestAnimationFrame(_tick);
}

export function stopRendering() {
  if (_animFrameId) cancelAnimationFrame(_animFrameId);
  _animFrameId = null;
}

// ── Selection + groundtrack ───────────────────────────────────────────────────

function _handleClick(noradId) {
  _selectedId = noradId;

  // Reset all icons
  Object.entries(_markers).forEach(([nid, m]) => {
    const rec = _tleRecords[nid];
    if (!rec) return;
    m.setIcon(_makeIcon(_orbitColor(rec.orbit_class), parseInt(nid) === _selectedId));
  });

  if (_onSelectCallback) _onSelectCallback(noradId);
  _fetchGroundtrack(noradId);
}

export function selectSatellite(noradId) {
  _handleClick(noradId);
  const marker = _markers[noradId];
  if (marker) _map.panTo(marker.getLatLng(), { animate: true });
}

async function _fetchGroundtrack(noradId) {
  if (_groundtrackLayer) {
    _map.removeLayer(_groundtrackLayer);
    _groundtrackLayer = null;
  }
  try {
    const data = await getGroundtrack(noradId, { minutes: 90, step_s: 60 });
    const latlngs = data.points.map((p) => [p.lat, p.lon]);
    if (latlngs.length < 2) return;

    // Split at antimeridian crossings to avoid long horizontal lines
    const segments = [];
    let seg = [latlngs[0]];
    for (let i = 1; i < latlngs.length; i++) {
      if (Math.abs(latlngs[i][1] - latlngs[i - 1][1]) > 180) {
        segments.push(seg);
        seg = [];
      }
      seg.push(latlngs[i]);
    }
    segments.push(seg);

    _groundtrackLayer = L.layerGroup(
      segments.map((s) =>
        L.polyline(s, { color: "#6366f1", weight: 1.5, opacity: 0.6, dashArray: "4 4" })
      )
    ).addTo(_map);
  } catch (err) {
    console.warn("Groundtrack fetch failed:", err);
  }
}

export function clearGroundtrack() {
  if (_groundtrackLayer) {
    _map.removeLayer(_groundtrackLayer);
    _groundtrackLayer = null;
  }
  _selectedId = null;
}
