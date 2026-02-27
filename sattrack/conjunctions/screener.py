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
import os
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

# Risk score RCS multipliers
_RCS_MULTIPLIERS: dict[str | None, float] = {
    "LARGE": 1.5,
    "MEDIUM": 1.2,
    "SMALL": 1.0,
    None: 1.0,
}

# RCS size-class -> integer encoding expected by the XGBoost model
_RCS_ENCODED: dict[str | None, int] = {
    "LARGE": 2,
    "MEDIUM": 1,
    "SMALL": 0,
    None: 0,
}

# maneuver_type -> integer encoding expected by the model
_MANEUVER_TYPE_ENCODED: dict[str | None, int] = {
    "inclination": 1,
    "altitude": 2,
    "phasing": 3,
    "circularization": 4,
    "deorbit": 5,
    "unknown": 0,
    None: 0,
}

# ---------------------------------------------------------------------------
# Lazy-loaded XGBoost conjunction probability model
# ---------------------------------------------------------------------------
_model = None
_MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "ml-db", "models", "conjunction_v1.pkl"
)


def _get_model():
    """Return the loaded model payload dict, or None if unavailable."""
    global _model
    if _model is None:
        try:
            import importlib.util
            _mod_path = os.path.normpath(
                os.path.join(
                    os.path.dirname(__file__), "..", "ml-db", "conjunction_model.py"
                )
            )
            spec = importlib.util.spec_from_file_location("conjunction_model", _mod_path)
            conjunction_model = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(conjunction_model)
            _model = conjunction_model.load_model(_MODEL_PATH)
            if _model is None:
                logger.warning(
                    "Conjunction model not found at %s; ML scoring disabled",
                    _MODEL_PATH,
                )
        except Exception as exc:
            logger.warning("Could not load conjunction model: %s", exc)
    return _model


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


def fetch_rcs_bulk(norad_ids: list[int]) -> dict[int, dict]:
    """Query Supabase satellite_enrichment for RCS and risk fields."""
    if not norad_ids:
        return {}
    from db.client import get_client
    try:
        result = (
            get_client()
            .table("satellite_enrichment")
            .select(
                "norad_id, rcs_m2, rcs_size_class, conjunction_risk, "
                "incl_deg, eccentricity, perigee_km, apogee_km"
            )
            .in_("norad_id", norad_ids)
            .execute()
        )
        return {
            row["norad_id"]: {
                "rcs_m2": row.get("rcs_m2"),
                "rcs_size_class": row.get("rcs_size_class"),
                "conjunction_risk": row.get("conjunction_risk"),
                "incl_deg": row.get("incl_deg"),
                "eccentricity": row.get("eccentricity"),
                "perigee_km": row.get("perigee_km"),
                "apogee_km": row.get("apogee_km"),
            }
            for row in (result.data or [])
        }
    except Exception as exc:
        logger.warning("fetch_rcs_bulk failed: %s", exc)
        return {}



def _fetch_tle_features_bulk(norad_ids: list[int]) -> dict[int, dict]:
    """
    Fetch the most-recent tle_features row per NORAD ID from the local
    PostgreSQL instance (env vars: POSTGRES_HOST/PORT/DB/USER/PASSWORD).

    Returns a dict mapping norad_id to
    {perigee_km, apogee_km, kp_at_epoch, f107_at_epoch, is_maneuver, period_min}.

    Falls back gracefully to an empty dict when the local DB is unavailable
    (e.g., production Railway environment where the ML DB is not present).
    """
    if not norad_ids:
        return {}
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", 5433)),
            dbname=os.environ.get("POSTGRES_DB", "sattrack_ml"),
            user=os.environ.get("POSTGRES_USER", "sattrack_ml"),
            password=os.environ.get("POSTGRES_PASSWORD", "sattrack_ml_local"),
        )
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (norad_id)
                        norad_id, perigee_km, apogee_km,
                        kp_at_epoch, f107_at_epoch, is_maneuver, period_min
                    FROM tle_features
                    WHERE norad_id = ANY(%s) AND perigee_km IS NOT NULL
                    ORDER BY norad_id, epoch DESC
                    """,
                    (norad_ids,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return {
            row["norad_id"]: {
                "perigee_km": row.get("perigee_km"),
                "apogee_km": row.get("apogee_km"),
                "kp_at_epoch": row.get("kp_at_epoch"),
                "f107_at_epoch": row.get("f107_at_epoch"),
                "is_maneuver": row.get("is_maneuver"),
                "period_min": row.get("period_min"),
            }
            for row in rows
        }
    except Exception as exc:
        logger.warning(
            "Could not fetch tle_features for ML scoring (local DB unavailable?): %s",
            exc,
        )
        return {}


def _eccentricity_from_orbit(
    perigee_km: float | None,
    apogee_km: float | None,
) -> float:
    """Derive eccentricity from perigee/apogee altitudes above Earth surface (km)."""
    if perigee_km is None or apogee_km is None:
        return 0.0
    rp = perigee_km + _EARTH_RADIUS_KM
    ra = apogee_km + _EARTH_RADIUS_KM
    denom = ra + rp
    if denom <= 0:
        return 0.0
    return (ra - rp) / denom


def _build_model_features(
    c: dict,
    enrichment: dict[int, dict],
    sat_orbits: dict[int, dict],
) -> dict:
    """
    Build the 16-feature dict expected by the XGBoost conjunction model.

    Source priority for each field:
      perigee_km / apogee_km : tle_features (sat_orbits) then satellite_enrichment
      incl_deg               : satellite_enrichment.incl_deg (not stored in tle_features)
      eccentricity           : satellite_enrichment, or derived from perigee/apogee
      kp_at_epoch, f107      : tle_features primary row
      is_maneuver            : tle_features primary row (is_maneuver boolean flag)
      maneuver_type_encoded  : 0 (tle_features stores the flag, not the type string)
    """
    n1, n2 = c["norad_id_1"], c["norad_id_2"]
    tf1 = sat_orbits.get(n1, {})
    tf2 = sat_orbits.get(n2, {})
    en1 = enrichment.get(n1, {})
    en2 = enrichment.get(n2, {})
    p_perigee = float(tf1.get("perigee_km") or en1.get("perigee_km") or 400.0)
    p_apogee  = float(tf1.get("apogee_km")  or en1.get("apogee_km")  or 420.0)
    p_incl    = float(en1.get("incl_deg") or 0.0)
    p_ecc     = float(
        en1.get("eccentricity")
        or _eccentricity_from_orbit(
            tf1.get("perigee_km") or en1.get("perigee_km"),
            tf1.get("apogee_km")  or en1.get("apogee_km"),
        )
    )
    p_rcs = _RCS_ENCODED.get(en1.get("rcs_size_class"), 0)
    s_perigee = float(tf2.get("perigee_km") or en2.get("perigee_km") or 400.0)
    s_apogee  = float(tf2.get("apogee_km")  or en2.get("apogee_km")  or 420.0)
    s_incl    = float(en2.get("incl_deg") or 0.0)
    s_ecc     = float(
        en2.get("eccentricity")
        or _eccentricity_from_orbit(
            tf2.get("perigee_km") or en2.get("perigee_km"),
            tf2.get("apogee_km")  or en2.get("apogee_km"),
        )
    )
    s_rcs = _RCS_ENCODED.get(en2.get("rcs_size_class"), 0)
    kp   = float(tf1.get("kp_at_epoch")   or 2.0)
    f107 = float(tf1.get("f107_at_epoch") or 150.0)
    is_maneuver = 1 if tf1.get("is_maneuver") else 0
    maneuver_type_enc = 0
    return {
        "primary_perigee_km":     p_perigee,
        "primary_apogee_km":      p_apogee,
        "primary_incl_deg":       p_incl,
        "primary_eccentricity":   p_ecc,
        "primary_rcs_encoded":    p_rcs,
        "secondary_perigee_km":   s_perigee,
        "secondary_apogee_km":    s_apogee,
        "secondary_incl_deg":     s_incl,
        "secondary_eccentricity": s_ecc,
        "secondary_rcs_encoded":  s_rcs,
        "altitude_diff_km":       abs(p_apogee - s_perigee),
        "incl_diff_deg":          abs(p_incl - s_incl),
        "kp_at_epoch":            kp,
        "f107_at_epoch":          f107,
        "is_maneuver_primary":    is_maneuver,
        "maneuver_type_encoded":  maneuver_type_enc,
    }

def _compute_risk_score(
    miss_distance_km: float,
    rcs_size_primary: str | None,
    rcs_size_secondary: str | None,
) -> float:
    """Compute a risk score in [0, 10] based on miss distance and RCS size."""
    base = max(0.0, 10.0 - miss_distance_km / 10.0)
    mult_primary = _RCS_MULTIPLIERS.get(rcs_size_primary, 1.0)
    mult_secondary = _RCS_MULTIPLIERS.get(rcs_size_secondary, 1.0)
    rcs_multiplier = max(mult_primary, mult_secondary)
    return round(min(10.0, base * rcs_multiplier), 4)


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

    # Enrichment stage: fetch RCS + orbital data for all involved NORAD IDs
    if conjunctions:
        all_norad_ids = list(
            {c["norad_id_1"] for c in conjunctions}
            | {c["norad_id_2"] for c in conjunctions}
        )
        logger.info("Fetching RCS enrichment for %d unique NORAD IDs", len(all_norad_ids))
        enrichment = fetch_rcs_bulk(all_norad_ids)

        # Fetch TLE-derived orbital features from the local ML DB for model scoring.
        # Falls back to empty dict gracefully when the local DB is unavailable.
        logger.info(
            "Fetching tle_features orbital data for %d NORAD IDs", len(all_norad_ids)
        )
        sat_orbits = _fetch_tle_features_bulk(all_norad_ids)

        for c in conjunctions:
            enc1 = enrichment.get(c["norad_id_1"], {})
            enc2 = enrichment.get(c["norad_id_2"], {})
            rcs_size_primary = enc1.get("rcs_size_class")
            rcs_size_secondary = enc2.get("rcs_size_class")
            c["rcs_m2_primary"] = enc1.get("rcs_m2")
            c["rcs_size_primary"] = rcs_size_primary
            c["rcs_m2_secondary"] = enc2.get("rcs_m2")
            c["rcs_size_secondary"] = rcs_size_secondary
            c["conjunction_risk_label"] = (
                enc1.get("conjunction_risk") or enc2.get("conjunction_risk")
            )
            c["risk_score"] = _compute_risk_score(
                c["miss_distance_km"], rcs_size_primary, rcs_size_secondary,
            )

        # ML model batch scoring.
        # Adds ml_conjunction_probability to each conjunction record.
        # Skipped entirely (non-fatal warning) if the model file is missing,
        # if the local ML DB is unreachable, or any other exception occurs.
        try:
            model = _get_model()
            if model is not None:
                import importlib.util as _ilu

                _mod_path = os.path.normpath(
                    os.path.join(
                        os.path.dirname(__file__), "..", "ml-db", "conjunction_model.py"
                    )
                )
                _spec = _ilu.spec_from_file_location("conjunction_model", _mod_path)
                _cm = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_cm)

                feature_rows = [
                    _build_model_features(c, enrichment, sat_orbits)
                    for c in conjunctions
                ]
                ml_probs = _cm.batch_predict(feature_rows, _MODEL_PATH)
                for c, prob in zip(conjunctions, ml_probs):
                    c["ml_conjunction_probability"] = round(float(prob), 4)
                logger.info(
                    "ML scoring complete: %d conjunctions scored with XGBoost",
                    len(ml_probs),
                )
            else:
                logger.debug("ML model unavailable; ml_conjunction_probability not set")
        except Exception as exc:
            logger.warning("ML batch scoring failed (non-fatal): %s", exc)

    logger.info(
        "Conjunction screening complete: %d events below %.1f km",
        len(conjunctions), threshold_km,
    )
    return conjunctions
