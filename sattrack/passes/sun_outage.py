"""
Sun outage predictor for GEO satellites.

A "sun outage" (solar transit) occurs when the sun passes within a small
angular distance of a geostationary satellite as seen from an earth station,
causing signal degradation or total loss.  This happens twice a year near the
equinoxes when the sun's declination approaches zero.

Algorithm
---------
1. Fetch the current TLE and verify mean_motion < 2 rev/day (GEO).
2. Compute the satellite's azimuth/elevation from the observer once — GEO
   satellites drift < 0.01 °/day, so a single sample suffices for annual
   outage windows.
3. Scan the sun's position at 5-minute intervals over the requested period
   using a compact astronomical formula (accuracy ≈ 0.01 °).
4. Detect windows where the sun–satellite angular separation < threshold_deg.
5. Report each window: start, peak (minimum separation), end, duration, and
   the minimum angular separation.
"""
from __future__ import annotations

import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sgp4.api import Satrec, jday

logger = logging.getLogger(__name__)


# ── Julian day helper ────────────────────────────────────────────────────────

def _jd(dt: datetime) -> float:
    jd_int, jd_frac = jday(
        dt.year, dt.month, dt.day,
        dt.hour, dt.minute,
        dt.second + dt.microsecond / 1e6,
    )
    return jd_int + jd_frac


# ── Sun position ─────────────────────────────────────────────────────────────

def _sun_azel(dt: datetime, obs_lat: float, obs_lon: float) -> tuple[float, float]:
    """Return (azimuth_deg, elevation_deg) of the sun from observer.

    Uses the low-precision solar-coordinates formula from Meeus,
    "Astronomical Algorithms" Ch. 25 (accuracy ≈ 0.01°).
    """
    n = _jd(dt) - 2451545.0               # days since J2000.0
    L = (280.460 + 0.9856474 * n) % 360   # mean longitude (deg)
    g = math.radians((357.528 + 0.9856003 * n) % 360)  # mean anomaly

    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)          # obliquity

    ra  = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))

    gmst_deg = (280.46061837 + 360.98564736629 * n) % 360
    lst_deg  = (gmst_deg + obs_lon) % 360
    H = math.radians(lst_deg - math.degrees(ra))

    phi = math.radians(obs_lat)
    sin_alt = math.sin(dec) * math.sin(phi) + math.cos(dec) * math.cos(phi) * math.cos(H)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt = math.asin(sin_alt)

    cos_phi = math.cos(phi)
    cos_alt = math.cos(alt)
    if cos_alt < 1e-10:
        return 0.0, math.degrees(alt)

    cos_A = (math.sin(dec) - math.sin(phi) * sin_alt) / (cos_phi * cos_alt)
    cos_A = max(-1.0, min(1.0, cos_A))
    az = math.degrees(math.acos(cos_A))
    if math.sin(H) > 0:
        az = 360.0 - az

    return az, math.degrees(alt)


# ── Satellite position (SGP4) ─────────────────────────────────────────────────

def _sat_azel(sat: Satrec, dt: datetime, obs_lat: float, obs_lon: float, obs_alt_m: float) -> tuple[float, float]:
    """Return (azimuth_deg, elevation_deg) for a satellite via SGP4 + pymap3d."""
    import pymap3d
    jd_int, jd_frac = jday(
        dt.year, dt.month, dt.day,
        dt.hour, dt.minute,
        dt.second + dt.microsecond / 1e6,
    )
    err, pos, _ = sat.sgp4(jd_int, jd_frac)
    if err != 0:
        return 0.0, -90.0
    x_ecef, y_ecef, z_ecef = pymap3d.eci2ecef(
        pos[0] * 1000, pos[1] * 1000, pos[2] * 1000, dt
    )
    az, el, _ = pymap3d.ecef2aer(x_ecef, y_ecef, z_ecef, obs_lat, obs_lon, obs_alt_m)
    return float(az), float(el)


# ── Angular separation ────────────────────────────────────────────────────────

def _angular_sep(az1: float, el1: float, az2: float, el2: float) -> float:
    """Angular separation (degrees) between two az/el directions."""
    az1r, el1r = math.radians(az1), math.radians(el1)
    az2r, el2r = math.radians(az2), math.radians(el2)
    cos_sep = (
        math.sin(el1r) * math.sin(el2r)
        + math.cos(el1r) * math.cos(el2r) * math.cos(az1r - az2r)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


# ── Main predictor ────────────────────────────────────────────────────────────

def predict_sun_outages(
    norad_id: int,
    obs_lat: float,
    obs_lon: float,
    obs_alt_m: float = 0.0,
    days: int = 365,
    threshold_deg: float = 2.0,
) -> dict[str, Any]:
    """
    Predict sun outage windows for a GEO satellite over the next *days* days.

    Returns
    -------
    {
      norad_id, observer, threshold_deg, satellite_az_deg, satellite_el_deg,
      outages: [{start, peak, end, duration_sec, min_separation_deg}]
    }
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
    mean_motion = row.get("mean_motion") or 0.0
    if mean_motion >= 2.0:
        raise ValueError(
            f"NORAD {norad_id} is not a GEO satellite (mean_motion={mean_motion:.2f} rev/day)"
        )

    sat = Satrec.twoline2rv(row["tle_line1"], row["tle_line2"])
    now = datetime.now(timezone.utc)

    # GEO drift is negligible — sample satellite position once at start
    sat_az, sat_el = _sat_azel(sat, now, obs_lat, obs_lon, obs_alt_m)

    if sat_el < 0:
        return {
            "norad_id": norad_id,
            "observer": {"lat": obs_lat, "lon": obs_lon, "alt_m": obs_alt_m},
            "threshold_deg": threshold_deg,
            "satellite_az_deg": round(sat_az, 2),
            "satellite_el_deg": round(sat_el, 2),
            "outages": [],
            "note": "Satellite is below the observer's horizon — no outages possible.",
        }

    # 5-minute scan over the requested period
    step = timedelta(minutes=5)
    end_time = now + timedelta(days=days)

    outages: list[dict[str, Any]] = []
    in_outage = False
    outage_start: datetime | None = None
    outage_peak: datetime | None = None
    outage_min_sep = 999.0

    t = now
    while t < end_time:
        sun_az, sun_el = _sun_azel(t, obs_lat, obs_lon)

        # Sun must be above horizon to cause an outage
        if sun_el > 0.0:
            sep = _angular_sep(sat_az, sat_el, sun_az, sun_el)

            if not in_outage and sep < threshold_deg:
                in_outage = True
                outage_start = t
                outage_min_sep = sep
                outage_peak = t

            elif in_outage:
                if sep < outage_min_sep:
                    outage_min_sep = sep
                    outage_peak = t
                if sep >= threshold_deg:
                    outages.append({
                        "start": outage_start.isoformat(),
                        "peak": outage_peak.isoformat(),
                        "end": t.isoformat(),
                        "duration_sec": int((t - outage_start).total_seconds()),
                        "min_separation_deg": round(outage_min_sep, 3),
                    })
                    in_outage = False
                    outage_start = None
                    outage_min_sep = 999.0

        elif in_outage:
            # Sun set — close the window
            outages.append({
                "start": outage_start.isoformat(),
                "peak": outage_peak.isoformat(),
                "end": t.isoformat(),
                "duration_sec": int((t - outage_start).total_seconds()),
                "min_separation_deg": round(outage_min_sep, 3),
            })
            in_outage = False
            outage_start = None
            outage_min_sep = 999.0

        t += step

    return {
        "norad_id": norad_id,
        "observer": {"lat": obs_lat, "lon": obs_lon, "alt_m": obs_alt_m},
        "threshold_deg": threshold_deg,
        "satellite_az_deg": round(sat_az, 2),
        "satellite_el_deg": round(sat_el, 2),
        "outages": outages,
    }
