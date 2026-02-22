"""
SGP4 orbit propagator — TEME → geodetic.

Wraps the sgp4 library (C-extension Satrec) and pymap3d for:
  - Single-point position + optional look angles
  - Vectorised groundtrack via sgp4_array

Note: SGP4 returns TEME coordinates. pymap3d.eci2geodetic / eci2ecef use
J2000 as a close approximation; error is < 5 km for LEO, acceptable for
a real-time tracker.
"""
from __future__ import annotations

import math
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import pymap3d
from sgp4.api import Satrec, jday

logger = logging.getLogger(__name__)

# In-memory TLE cache: {norad_id: (monotonic_time, (line1, line2))}
# Avoids a Supabase round-trip on every propagation request.
_TLE_CACHE: dict[int, tuple[float, tuple[str, str]]] = {}
_TLE_CACHE_TTL = 60.0  # seconds


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _jday_from_dt(dt: datetime) -> tuple[float, float]:
    """Split datetime → (jd, fraction) for sgp4."""
    return jday(
        dt.year, dt.month, dt.day,
        dt.hour, dt.minute,
        dt.second + dt.microsecond / 1e6,
    )


_SGP4_ERRORS = {
    1: "mean eccentricity out of range",
    2: "mean motion less than zero",
    3: "perturbed eccentricity out of range",
    4: "semi-latus rectum less than zero",
    5: "epoch elements are sub-orbital",
    6: "satellite has decayed",
}


def _check_sgp4(err: int, norad_id: int) -> None:
    if err != 0:
        msg = _SGP4_ERRORS.get(err, f"code {err}")
        raise ValueError(f"SGP4 error ({msg}) for NORAD {norad_id}")


def _load_tle(norad_id: int) -> tuple[str, str] | None:
    """Fetch the current best TLE lines from tle_history, with 60-second cache."""
    now = time.monotonic()
    cached = _TLE_CACHE.get(norad_id)
    if cached is not None and (now - cached[0]) < _TLE_CACHE_TTL:
        return cached[1]

    from db.client import get_client
    result = (
        get_client()
        .table("tle_history")
        .select("tle_line1, tle_line2")
        .eq("norad_id", norad_id)
        .eq("is_current", True)
        .order("epoch", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    r = result.data[0]
    tle = (r["tle_line1"], r["tle_line2"])
    _TLE_CACHE[norad_id] = (now, tle)
    return tle


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def propagate_single(
    norad_id: int,
    t: datetime,
    obs_lat: float | None = None,
    obs_lon: float | None = None,
    obs_alt_m: float = 0.0,
) -> dict[str, Any]:
    """
    Propagate satellite to time *t*.

    Returns position dict with optional look angles when obs_lat/lon provided.
    Raises ValueError for missing TLE or SGP4 failure (decayed satellite, etc.).
    """
    tle = _load_tle(norad_id)
    if tle is None:
        raise ValueError(f"No current TLE for NORAD {norad_id}")

    sat = Satrec.twoline2rv(tle[0], tle[1])
    jd, fr = _jday_from_dt(t)
    err, pos, vel = sat.sgp4(jd, fr)
    _check_sgp4(err, norad_id)

    # TEME (km) → geodetic via pymap3d (treated as ECI; < 5 km approx error)
    lat, lon, alt_m = pymap3d.eci2geodetic(
        pos[0] * 1000, pos[1] * 1000, pos[2] * 1000, t
    )
    velocity_km_s = math.sqrt(vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2)

    out: dict[str, Any] = {
        "norad_id": norad_id,
        "t": t.isoformat(),
        "lat": round(float(lat), 6),
        "lon": round(float(lon), 6),
        "alt_km": round(float(alt_m) / 1000, 3),
        "velocity_km_s": round(velocity_km_s, 4),
    }

    if obs_lat is not None and obs_lon is not None:
        try:
            x_ecef, y_ecef, z_ecef = pymap3d.eci2ecef(
                pos[0] * 1000, pos[1] * 1000, pos[2] * 1000, t
            )
            az, el, rng = pymap3d.ecef2aer(
                x_ecef, y_ecef, z_ecef,
                obs_lat, obs_lon, obs_alt_m,
            )
            out["az_deg"] = round(float(az), 2)
            out["el_deg"] = round(float(el), 2)
            out["range_km"] = round(float(rng) / 1000, 3)
        except Exception as exc:
            logger.warning("Look-angle calculation failed: %s", exc)

    return out


def propagate_groundtrack(
    norad_id: int,
    minutes: int = 90,
    step_s: int = 60,
) -> dict[str, Any]:
    """
    Compute groundtrack points for the next *minutes* minutes.

    Uses sgp4_array (C-extension vectorised) for efficiency.
    Returns {norad_id, points: [{t, lat, lon, alt_km}, ...]}.
    """
    import numpy as np

    tle = _load_tle(norad_id)
    if tle is None:
        raise ValueError(f"No current TLE for NORAD {norad_id}")

    sat = Satrec.twoline2rv(tle[0], tle[1])
    now = datetime.now(timezone.utc)
    n = int(minutes * 60 / step_s) + 1
    times = [now + timedelta(seconds=i * step_s) for i in range(n)]

    pairs = [_jday_from_dt(t) for t in times]
    jd_arr = np.array([p[0] for p in pairs])
    fr_arr = np.array([p[1] for p in pairs])

    errs, positions, _ = sat.sgp4_array(jd_arr, fr_arr)

    points = []
    for i, (e, pos) in enumerate(zip(errs, positions)):
        if e != 0:
            continue
        lat, lon, alt_m = pymap3d.eci2geodetic(
            pos[0] * 1000, pos[1] * 1000, pos[2] * 1000, times[i]
        )
        points.append({
            "t": times[i].isoformat(),
            "lat": round(float(lat), 5),
            "lon": round(float(lon), 5),
            "alt_km": round(float(alt_m) / 1000, 3),
        })

    return {"norad_id": norad_id, "points": points}
