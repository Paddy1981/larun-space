/**
 * sattrack-api.js — API client for LARUN SatTrack backend
 * All /v1/ endpoints on the Railway deployment.
 */

const API_BASE = "https://web-production-5f0e4.up.railway.app";

async function _get(path, params = {}) {
  const url = new URL(API_BASE + path);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });
  const res = await fetch(url.toString());
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}

// ── Satellites ──────────────────────────────────────────────────────────────

export async function listSatellites({ limit = 200, orbit_class, status, search } = {}) {
  return _get("/v1/satellites", { limit, orbit_class, status, search });
}

export async function getSatellite(noradId) {
  return _get(`/v1/satellites/${noradId}`);
}

export async function getCurrentTle(noradId) {
  return _get(`/v1/tle/${noradId}`);
}

// ── Propagation ──────────────────────────────────────────────────────────────

export async function getPosition(noradId, { t, obs_lat, obs_lon, obs_alt_m } = {}) {
  return _get(`/v1/propagate/${noradId}`, { t, obs_lat, obs_lon, obs_alt_m });
}

export async function getGroundtrack(noradId, { minutes = 90, step_s = 60 } = {}) {
  return _get(`/v1/propagate/${noradId}/groundtrack`, { minutes, step_s });
}

// ── Passes ───────────────────────────────────────────────────────────────────

export async function getPasses(noradId, { lat, lon, alt_m = 0, days = 3, min_elevation = 10 } = {}) {
  return _get(`/v1/passes/${noradId}`, { lat, lon, alt_m, days, min_elevation });
}

// ── Conjunctions ─────────────────────────────────────────────────────────────

export async function getConjunctions({ threshold_km = 10, limit = 50 } = {}) {
  return _get("/v1/conjunctions", { threshold_km, limit });
}

// ── Status / Weather ─────────────────────────────────────────────────────────

export async function getStatus() {
  return _get("/v1/status");
}

export async function getCurrentWeather() {
  return _get("/v1/weather/current");
}
