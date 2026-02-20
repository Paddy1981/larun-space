"""
Satellite pass predictor.

Algorithm:
  1. 30-second coarse elevation scan over the prediction window
  2. Bisection (20 iterations, < 1 s precision) for each AOS / LOS crossing
  3. Ternary search for TCA (maximum elevation) between AOS and LOS

GEO satellites (mean_motion < 2 rev/day) get a single entry with
type="geostationary" instead of discrete passes.
"""
from __future__ import annotations

import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import pymap3d
from sgp4.api import Satrec, jday

logger = logging.getLogger(__name__)

_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _az_to_compass(az_deg: float) -> str:
    return _COMPASS[int((az_deg + 22.5) / 45) % 8]


def _jday_from_dt(dt: datetime) -> tuple[float, float]:
    return jday(
        dt.year, dt.month, dt.day,
        dt.hour, dt.minute,
        dt.second + dt.microsecond / 1e6,
    )


def _el_az_rng(
    sat: Satrec,
    dt: datetime,
    obs_lat: float,
    obs_lon: float,
    obs_alt_m: float,
) -> tuple[float, float, float]:
    """Return (elevation_deg, azimuth_deg, range_km) or (-90, 0, 0) on error."""
    jd, fr = _jday_from_dt(dt)
    err, pos, _ = sat.sgp4(jd, fr)
    if err != 0:
        return -90.0, 0.0, 0.0
    x_ecef, y_ecef, z_ecef = pymap3d.eci2ecef(
        pos[0] * 1000, pos[1] * 1000, pos[2] * 1000, dt
    )
    az, el, rng = pymap3d.ecef2aer(
        x_ecef, y_ecef, z_ecef,
        obs_lat, obs_lon, obs_alt_m,
    )
    return float(el), float(az), float(rng) / 1000


def _bisect_crossing(
    sat: Satrec,
    t_lo: datetime,
    t_hi: datetime,
    obs_lat: float,
    obs_lon: float,
    obs_alt_m: float,
    min_el: float,
    iterations: int = 20,
) -> datetime:
    """Bisect to find the moment elevation crosses min_el."""
    el_lo = _el_az_rng(sat, t_lo, obs_lat, obs_lon, obs_alt_m)[0]
    for _ in range(iterations):
        t_mid = t_lo + (t_hi - t_lo) / 2
        el_mid = _el_az_rng(sat, t_mid, obs_lat, obs_lon, obs_alt_m)[0]
        if (el_lo < min_el) == (el_mid < min_el):
            t_lo, el_lo = t_mid, el_mid
        else:
            t_hi = t_mid
    return t_lo + (t_hi - t_lo) / 2


def _find_tca(
    sat: Satrec,
    t_aos: datetime,
    t_los: datetime,
    obs_lat: float,
    obs_lon: float,
    obs_alt_m: float,
    iterations: int = 20,
) -> tuple[datetime, float]:
    """Ternary search for maximum elevation between AOS and LOS."""
    lo, hi = t_aos, t_los
    for _ in range(iterations):
        m1 = lo + (hi - lo) / 3
        m2 = hi - (hi - lo) / 3
        el1 = _el_az_rng(sat, m1, obs_lat, obs_lon, obs_alt_m)[0]
        el2 = _el_az_rng(sat, m2, obs_lat, obs_lon, obs_alt_m)[0]
        if el1 < el2:
            lo = m1
        else:
            hi = m2
    tca = lo + (hi - lo) / 2
    max_el = _el_az_rng(sat, tca, obs_lat, obs_lon, obs_alt_m)[0]
    return tca, max_el


def predict_passes(
    norad_id: int,
    obs_lat: float,
    obs_lon: float,
    obs_alt_m: float = 0.0,
    days: int = 3,
    min_elevation: float = 10.0,
) -> dict[str, Any]:
    """
    Predict satellite passes over an observer location.

    Returns {norad_id, passes: [...]}.
    Each pass has: aos, tca, los, max_elevation_deg, duration_sec,
                   direction, range_km_at_tca.
    """
    from db.client import get_client
    result = (
        get_client()
        .table("tle_history")
        .select("tle_line1, tle_line2, mean_motion")
        .eq("norad_id", norad_id)
        .eq("is_current", True)
        .order("epoch", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise ValueError(f"No current TLE for NORAD {norad_id}")

    row = result.data[0]
    sat = Satrec.twoline2rv(row["tle_line1"], row["tle_line2"])

    # mean_motion in rev/day (sat.no_kozai is rad/min)
    mean_motion = row.get("mean_motion") or (sat.no_kozai * 1440 / (2 * math.pi))

    # GEO: mean_motion < 2 rev/day — return a single "always visible" entry
    if mean_motion < 2.0:
        now = datetime.now(timezone.utc)
        el, az, _ = _el_az_rng(sat, now, obs_lat, obs_lon, obs_alt_m)
        return {
            "norad_id": norad_id,
            "passes": [{
                "type": "geostationary",
                "aos": now.isoformat(),
                "tca": now.isoformat(),
                "los": (now + timedelta(days=days)).isoformat(),
                "max_elevation_deg": round(el, 2),
                "duration_sec": days * 86400,
                "direction": _az_to_compass(az),
                "range_km_at_tca": None,
            }],
        }

    # LEO / MEO: coarse 30-second scan then bisect
    now = datetime.now(timezone.utc)
    end_time = now + timedelta(days=days)
    step = timedelta(seconds=30)

    passes: list[dict[str, Any]] = []
    t = now
    prev_el, _, _ = _el_az_rng(sat, t, obs_lat, obs_lon, obs_alt_m)
    in_pass = prev_el >= min_elevation
    t_aos: datetime | None = t if in_pass else None

    while t < end_time:
        t_next = t + step
        curr_el, _, _ = _el_az_rng(sat, t_next, obs_lat, obs_lon, obs_alt_m)

        if not in_pass and prev_el < min_elevation and curr_el >= min_elevation:
            # Rising edge — find AOS
            t_aos = _bisect_crossing(sat, t, t_next, obs_lat, obs_lon, obs_alt_m, min_elevation)
            in_pass = True

        elif in_pass and prev_el >= min_elevation and curr_el < min_elevation:
            # Setting edge — find LOS and complete the pass
            t_los = _bisect_crossing(sat, t, t_next, obs_lat, obs_lon, obs_alt_m, min_elevation)
            tca, max_el = _find_tca(sat, t_aos, t_los, obs_lat, obs_lon, obs_alt_m)
            _, az_tca, rng_tca = _el_az_rng(sat, tca, obs_lat, obs_lon, obs_alt_m)

            passes.append({
                "aos": t_aos.isoformat(),
                "tca": tca.isoformat(),
                "los": t_los.isoformat(),
                "max_elevation_deg": round(max_el, 2),
                "duration_sec": int((t_los - t_aos).total_seconds()),
                "direction": _az_to_compass(az_tca),
                "range_km_at_tca": round(rng_tca, 1),
            })
            in_pass = False
            t_aos = None

        prev_el = curr_el
        t = t_next

    return {"norad_id": norad_id, "passes": passes}
