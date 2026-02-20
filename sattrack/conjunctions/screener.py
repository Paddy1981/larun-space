"""
LEO conjunction screener.

Three-stage pipeline:
  1. Filter  — active LEO satellites with quality_score >= 40 (~5k sats)
  2. Coarse  — numpy batch propagation at 60 s steps; bucket by 50 km altitude
               shell + 10° lat band; only compare same/adjacent buckets
  3. Refine  — parabolic interpolation on distance-vs-time for flagged pairs
"""
from __future__ import annotations

import math
import logging
from datetime import datetime, timezone, timedelta
from itertools import combinations
from typing import Any

import numpy as np
from sgp4.api import Satrec, jday

logger = logging.getLogger(__name__)

_ALT_SHELL_KM = 50        # altitude bucket size
_LAT_BAND_DEG = 10        # latitude band size
_COARSE_STEP_S = 60       # propagation step for coarse scan
_EARTH_RADIUS_KM = 6371.0


def _jday_from_dt(dt: datetime) -> tuple[float, float]:
    return jday(
        dt.year, dt.month, dt.day,
        dt.hour, dt.minute,
        dt.second + dt.microsecond / 1e6,
    )


def _load_active_leo() -> list[dict]:
    """Return active LEO satellites with quality_score >= 40."""
    from db.client import get_client
    result = (
        get_client()
        .table("tle_history")
        .select(
            "norad_id, tle_line1, tle_line2, quality_score, "
            "satellites!inner(name, orbit_class, status)"
        )
        .eq("is_current", True)
        .gte("quality_score", 40)
        .eq("satellites.orbit_class", "LEO")
        .eq("satellites.status", "active")
        .execute()
    )
    return result.data or []


def _propagate_batch(
    sats: list[dict],
    times: list[datetime],
) -> dict[int, np.ndarray]:
    """
    Propagate all sats at every time step.

    Returns {norad_id: positions_km shape (N, 3)}.
    SGP4 error rows are set to NaN.
    """
    pairs = [_jday_from_dt(t) for t in times]
    jd_arr = np.array([p[0] for p in pairs])
    fr_arr = np.array([p[1] for p in pairs])

    positions: dict[int, np.ndarray] = {}
    for row in sats:
        try:
            sat = Satrec.twoline2rv(row["tle_line1"], row["tle_line2"])
            errs, pos_list, _ = sat.sgp4_array(jd_arr, fr_arr)
            pos = np.array(pos_list, dtype=np.float64)
            pos[np.array(errs) != 0] = np.nan
            positions[row["norad_id"]] = pos
        except Exception as exc:
            logger.debug("sgp4 failed for NORAD %s: %s", row["norad_id"], exc)
    return positions


def _bucket_key(pos_km: np.ndarray) -> tuple[int, int]:
    """Map TEME position to (altitude_shell, lat_band) bucket."""
    r = float(np.linalg.norm(pos_km))
    alt = r - _EARTH_RADIUS_KM
    lat = math.degrees(math.asin(float(pos_km[2]) / r))
    return int(alt / _ALT_SHELL_KM), int((lat + 90) / _LAT_BAND_DEG)


def screen_conjunctions(
    threshold_km: float = 10.0,
    hours: int = 24,
) -> list[dict[str, Any]]:
    """
    Run the full conjunction screening pipeline.

    Returns a list of conjunction records ready for upsert_conjunctions().
    """
    logger.info("Loading active LEO satellites...")
    sats = _load_active_leo()
    if len(sats) < 2:
        logger.warning("Fewer than 2 active LEO sats found; skipping")
        return []
    logger.info("Screening %d active LEO satellites over %d h window", len(sats), hours)

    now = datetime.now(timezone.utc)
    n_steps = int(hours * 3600 / _COARSE_STEP_S)
    times = [now + timedelta(seconds=i * _COARSE_STEP_S) for i in range(n_steps + 1)]

    # Stage 2 — batch propagation
    positions = _propagate_batch(sats, times)
    norad_ids = list(positions.keys())
    sat_names = {r["norad_id"]: r["satellites"]["name"] for r in sats}

    # Coarse filter — only compare pairs that share a bucket at any step
    coarse_threshold = threshold_km * 5
    candidate_pairs: set[tuple[int, int]] = set()

    for step_i in range(n_steps):
        buckets: dict[tuple[int, int], list[int]] = {}
        for nid in norad_ids:
            pos = positions[nid][step_i]
            if np.any(np.isnan(pos)):
                continue
            bk = _bucket_key(pos)
            # Register in primary + all 8 adjacent buckets
            for da in (-1, 0, 1):
                for db in (-1, 0, 1):
                    buckets.setdefault((bk[0] + da, bk[1] + db), []).append(nid)

        for bucket_nids in buckets.values():
            unique = list(set(bucket_nids))
            if len(unique) < 2:
                continue
            for n1, n2 in combinations(unique, 2):
                pair = (min(n1, n2), max(n1, n2))
                if pair in candidate_pairs:
                    continue
                p1 = positions[n1][step_i]
                p2 = positions[n2][step_i]
                if np.any(np.isnan(p1)) or np.any(np.isnan(p2)):
                    continue
                if float(np.linalg.norm(p1 - p2)) < coarse_threshold:
                    candidate_pairs.add(pair)

    logger.info("Coarse pass: %d candidate pairs to refine", len(candidate_pairs))

    # Stage 3 — TCA refinement via parabolic interpolation
    conjunctions: list[dict[str, Any]] = []
    for n1, n2 in candidate_pairs:
        pos1 = positions[n1]
        pos2 = positions[n2]
        dists = np.linalg.norm(pos1 - pos2, axis=1)

        valid = ~(np.isnan(pos1[:, 0]) | np.isnan(pos2[:, 0]))
        if not np.any(valid):
            continue

        masked = np.where(valid, dists, np.inf)
        min_idx = int(np.argmin(masked))
        min_dist = float(masked[min_idx])

        if min_dist > threshold_km:
            continue

        # Parabolic interpolation
        if 0 < min_idx < len(times) - 1 and valid[min_idx - 1] and valid[min_idx + 1]:
            d0, d1, d2 = dists[min_idx - 1], dists[min_idx], dists[min_idx + 1]
            denom = d0 - 2 * d1 + d2
            offset = float((d0 - d2) / (2 * denom)) if denom != 0 else 0.0
            tca_time = times[min_idx] + timedelta(seconds=offset * _COARSE_STEP_S)
        else:
            tca_time = times[min_idx]

        # Relative velocity at TCA (finite difference over one step)
        if min_idx > 0 and valid[min_idx - 1]:
            rel_vel = float(
                np.linalg.norm(
                    (pos1[min_idx] - pos2[min_idx]) - (pos1[min_idx - 1] - pos2[min_idx - 1])
                ) / _COARSE_STEP_S
            )
        else:
            rel_vel = 0.0

        conjunctions.append({
            "norad_id_1": n1,
            "norad_id_2": n2,
            "tca_time": tca_time.isoformat(),
            "miss_distance_km": round(min_dist, 4),
            "relative_velocity_km_s": round(rel_vel, 4),
            "screening_window_hrs": hours,
        })

    logger.info(
        "Conjunction screening complete: %d events below %.1f km",
        len(conjunctions), threshold_km,
    )
    return conjunctions
