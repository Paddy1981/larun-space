/**
 * sattrack-app.js — App controller, state, polling
 *
 * Orchestrates:
 *  - Initial satellite list load + TLE batch fetch
 *  - 30-second position refresh (satellite.js client-side, no API poll)
 *  - Space weather panel (polls /v1/weather/current every 10 min)
 *  - Conjunction alerts panel (polls /v1/conjunctions every 10 min)
 *  - Pass modal triggered on satellite selection
 */

import * as API from "./sattrack-api.js";
import { getSession, signInWithGitHub, signOut, onAuthStateChange } from "./sattrack-auth.js";
import {
  initMap,
  loadTles,
  clearTles,
  startRendering,
  stopRendering,
  selectSatellite,
  clearGroundtrack,
  onSatelliteSelect,
} from "./sattrack-globe.js";

// ── State ─────────────────────────────────────────────────────────────────────

const state = {
  satellites: [],   // full list from /v1/satellites
  tleMap: {},       // norad_id → tle record
  filter: "ALL",    // ALL | LEO | MEO | GEO | HEO
  search: "",
  selected: null,   // norad_id of selected sat
  weather: null,
  conjunctions: [],
  userLat: null,
  userLon: null,
};

// ── DOM refs ──────────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);
const satList     = $("sat-list");
const searchInput = $("search-input");
const satCount    = $("sat-count");
const modalBackdrop = $("modal-backdrop");
const modalTitle    = $("modal-title");
const modalBody     = $("modal-body");
const modalSubtitle = $("modal-subtitle");
const toastEl       = $("toast");
const conjList      = $("conj-list");

// ── Toast ─────────────────────────────────────────────────────────────────────

let _toastTimer;
function showToast(msg, duration = 3000) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toastEl.classList.remove("show"), duration);
}

// ── Satellite list rendering ──────────────────────────────────────────────────

function getFiltered() {
  return state.satellites.filter((s) => {
    if (state.filter !== "ALL" && s.orbit_class !== state.filter) return false;
    if (state.search && !s.name.toLowerCase().includes(state.search.toLowerCase())) return false;
    return true;
  });
}

function renderSatList() {
  const filtered = getFiltered();
  satCount.textContent = `${filtered.length.toLocaleString()} satellites`;
  satList.innerHTML = "";

  if (filtered.length === 0) {
    satList.innerHTML = `<div class="sat-list-empty">No satellites match filter</div>`;
    return;
  }

  const frag = document.createDocumentFragment();
  // Show first 300 for performance
  filtered.slice(0, 300).forEach((sat) => {
    const el = document.createElement("div");
    el.className = "sat-item" + (sat.norad_id === state.selected ? " active" : "");
    el.dataset.id = sat.norad_id;
    el.innerHTML = `
      <div class="sat-dot ${(sat.orbit_class || "").toLowerCase()}"></div>
      <div class="sat-info">
        <div class="sat-name">${sat.name}</div>
        <div class="sat-meta">${sat.norad_id} · ${sat.orbit_class || "?"}</div>
      </div>`;
    el.addEventListener("click", () => onListClick(sat.norad_id));
    frag.appendChild(el);
  });
  satList.appendChild(frag);
}

// ── Initial data load ─────────────────────────────────────────────────────────

async function loadSatellites() {
  showToast("Loading satellites…");
  try {
    // Load up to 2000 active satellites
    const data = await API.listSatellites({ limit: 500, status: "active" });
    state.satellites = data.data || [];
    renderSatList();
    showToast(`Loaded ${state.satellites.length} satellites`);
    await loadTlesBatch();
  } catch (err) {
    showToast("Failed to load satellites", 5000);
    console.error(err);
  }
}

async function loadTlesBatch() {
  // Fetch TLEs for the listed satellites in small parallel batches
  const ids = state.satellites.map((s) => s.norad_id).slice(0, 200);
  const results = await Promise.allSettled(ids.map((id) => API.getCurrentTle(id)));

  const tleList = [];
  results.forEach((r, i) => {
    if (r.status === "fulfilled" && r.value) {
      const tle = r.value;
      const sat = state.satellites.find((s) => s.norad_id === ids[i]);
      state.tleMap[ids[i]] = tle;
      tleList.push({
        norad_id: ids[i],
        tle_line1: tle.tle_line1,
        tle_line2: tle.tle_line2,
        name: sat ? sat.name : String(ids[i]),
        orbit_class: sat ? sat.orbit_class : "LEO",
      });
    }
  });

  clearTles();
  loadTles(tleList);
  startRendering();
}

// ── Filter buttons ────────────────────────────────────────────────────────────

document.querySelectorAll(".filter-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.filter;
    renderSatList();
  });
});

searchInput.addEventListener("input", () => {
  state.search = searchInput.value.trim();
  renderSatList();
});

// ── Map satellite selection ───────────────────────────────────────────────────

onSatelliteSelect((noradId) => {
  state.selected = noradId;
  renderSatList();
  openPassModal(noradId);
});

function onListClick(noradId) {
  state.selected = noradId;
  renderSatList();
  selectSatellite(noradId);
  openPassModal(noradId);
}

// ── Pass modal ────────────────────────────────────────────────────────────────

function openPassModal(noradId) {
  const sat = state.satellites.find((s) => s.norad_id === noradId);
  modalTitle.textContent = sat ? sat.name : `NORAD ${noradId}`;
  modalSubtitle.textContent = "Fetching your location…";
  modalBody.innerHTML = `<div class="modal-loading"><span class="loading-spinner"></span></div>`;
  modalBackdrop.classList.add("open");

  // Get observer location then fetch passes
  _getLocation()
    .then(({ lat, lon }) => {
      state.userLat = lat;
      state.userLon = lon;
      modalSubtitle.textContent = `Next passes from ${lat.toFixed(2)}°, ${lon.toFixed(2)}°`;
      return API.getPasses(noradId, { lat, lon, days: 3, min_elevation: 10 });
    })
    .then((data) => renderPassTable(data.passes))
    .catch((err) => {
      modalBody.innerHTML = `<div class="modal-error">${err.message}</div>`;
    });
}

function _getLocation() {
  if (state.userLat !== null) {
    return Promise.resolve({ lat: state.userLat, lon: state.userLon });
  }
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Geolocation not supported — enter coordinates manually"));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      () => reject(new Error("Location denied — cannot predict passes without observer position"))
    );
  });
}

function _elClass(el) {
  if (el >= 60) return "good";
  if (el >= 20) return "moderate";
  return "";
}

function _fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function _fmtDate(iso) {
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

function renderPassTable(passes) {
  if (!passes || passes.length === 0) {
    modalBody.innerHTML = `<div class="modal-loading">No visible passes in the next 3 days.</div>`;
    return;
  }

  // Handle geostationary
  if (passes[0].type === "geostationary") {
    const p = passes[0];
    modalBody.innerHTML = `
      <div class="modal-loading" style="color:var(--star-gold)">
        Geostationary satellite — always above horizon.<br>
        Elevation: <strong>${p.max_elevation_deg}°</strong> · Direction: <strong>${p.direction}</strong>
      </div>`;
    return;
  }

  const rows = passes.slice(0, 5).map((p) => `
    <tr>
      <td>${_fmtDate(p.aos)}</td>
      <td>${_fmtTime(p.aos)}</td>
      <td>${_fmtTime(p.tca)}</td>
      <td>${_fmtTime(p.los)}</td>
      <td class="${_elClass(p.max_elevation_deg)}">${p.max_elevation_deg}°</td>
      <td>${Math.round(p.duration_sec)}s</td>
      <td>${p.direction}</td>
    </tr>`).join("");

  modalBody.innerHTML = `
    <table class="pass-table">
      <thead>
        <tr>
          <th>Date</th><th>AOS</th><th>TCA</th><th>LOS</th>
          <th>Max El</th><th>Dur</th><th>Dir</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// Close modal
modalBackdrop.addEventListener("click", (e) => {
  if (e.target === modalBackdrop) closeModal();
});
$("modal-close").addEventListener("click", closeModal);

function closeModal() {
  modalBackdrop.classList.remove("open");
  clearGroundtrack();
  state.selected = null;
  renderSatList();
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

// ── Space weather panel ───────────────────────────────────────────────────────

async function refreshWeather() {
  try {
    const w = await API.getCurrentWeather();
    state.weather = w;

    const kp = w.kp_index ?? "—";
    const f107 = w.f107_flux ? w.f107_flux.toFixed(1) : "—";

    const kpClass = kp < 3 ? "good" : kp < 6 ? "moderate" : "high";
    const kpPct = Math.min((kp / 9) * 100, 100);
    const barColor = kp < 3 ? "var(--success)" : kp < 6 ? "var(--warning)" : "var(--error)";

    $("kp-value").textContent = kp;
    $("kp-value").className = `weather-value ${kpClass}`;
    $("f107-value").textContent = f107;
    $("kp-bar-fill").style.width = `${kpPct}%`;
    $("kp-bar-fill").style.background = barColor;
    $("weather-updated").textContent = w.kp_observed_at
      ? `Updated ${new Date(w.kp_observed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
      : "";
  } catch (_) {
    // silently fail — weather is secondary
  }
}

// ── Conjunction panel ─────────────────────────────────────────────────────────

async function refreshConjunctions() {
  try {
    const data = await API.getConjunctions({ threshold_km: 10, limit: 20 });
    state.conjunctions = data.conjunctions || [];

    if (!data.last_computed) {
      conjList.innerHTML = `<div class="conj-empty">Screening not yet run<br>(first run after 6 h)</div>`;
      return;
    }

    if (state.conjunctions.length === 0) {
      conjList.innerHTML = `<div class="conj-empty">No conjunctions below 10 km</div>`;
      return;
    }

    conjList.innerHTML = state.conjunctions.map((c) => {
      const distClass = c.miss_distance_km < 2 ? "critical" : "";
      return `
        <div class="conj-item">
          <div class="conj-names">${c.name_1} × ${c.name_2}</div>
          <div class="conj-dist ${distClass}">${c.miss_distance_km.toFixed(2)} km miss</div>
          <div class="conj-time">TCA ${new Date(c.tca_time).toLocaleString([], {
            month: "short", day: "numeric",
            hour: "2-digit", minute: "2-digit",
          })}</div>
        </div>`;
    }).join("");
  } catch (_) {
    conjList.innerHTML = `<div class="conj-empty">Conjunction data unavailable</div>`;
  }
}

// ── Map control buttons ───────────────────────────────────────────────────────

$("btn-locate").addEventListener("click", () => {
  _getLocation()
    .then(({ lat, lon }) => {
      state.userLat = lat;
      state.userLon = lon;
      showToast(`Location set: ${lat.toFixed(2)}°, ${lon.toFixed(2)}°`);
    })
    .catch((e) => showToast(e.message, 4000));
});

$("btn-clear").addEventListener("click", () => {
  clearGroundtrack();
  state.selected = null;
  renderSatList();
});

// ── Auth nav ──────────────────────────────────────────────────────────────────

const authBtn = $("auth-btn");

function updateAuthBtn(session) {
  if (session?.user) {
    const label = session.user.user_metadata?.user_name
      || session.user.email
      || "Account";
    authBtn.textContent = label;
    authBtn.title = "Sign out";
    authBtn.onclick = () => signOut().catch(() => {});
  } else {
    authBtn.textContent = "Sign In";
    authBtn.title = "Sign in with GitHub";
    authBtn.onclick = () => signInWithGitHub().catch((e) => showToast(e.message, 4000));
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  initMap("map");

  // Auth: restore existing session then keep nav in sync
  const session = await getSession();
  updateAuthBtn(session);
  onAuthStateChange(updateAuthBtn);

  await loadSatellites();
  await Promise.all([refreshWeather(), refreshConjunctions()]);

  // Periodic refresh (weather + conjunctions every 10 min)
  setInterval(refreshWeather, 10 * 60 * 1000);
  setInterval(refreshConjunctions, 10 * 60 * 1000);
}

init();
