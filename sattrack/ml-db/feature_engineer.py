"""
feature_engineer.py — Compute ML feature vectors from tle_history → tle_features.

For each satellite, iterates consecutive TLE pairs (sorted by epoch) and computes:
  - Derived orbital elements: sma_km, perigee_km, apogee_km, period_min
  - Delta elements (rate of change per hour): d_inclination, d_eccentricity, etc.
  - Space weather joined at epoch (nearest within ±1h)

Results are batch-inserted into tle_features.

Usage:
  python feature_engineer.py --all
  python feature_engineer.py --norad-ids 25544 20580
  python feature_engineer.py --all --batch-size 10000

Requires: .env with POSTGRES_* vars
"""

from __future__ import annotations

import argparse
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Generator

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MU_KM3 = 398600.4418
EARTH_RADIUS_KM = 6371.0
TWO_PI = 2 * math.pi

# ─── DB connection ────────────────────────────────────────────────────────────

def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        dbname=os.environ.get("POSTGRES_DB", "sattrack_ml"),
        user=os.environ.get("POSTGRES_USER", "sattrack_ml"),
        password=os.environ["POSTGRES_PASSWORD"],
    )


# ─── Orbital mechanics helpers ────────────────────────────────────────────────

def mean_motion_to_sma(mean_motion_revday: float) -> float:
    """Mean motion (rev/day) → semi-major axis (km)."""
    n_rad_s = mean_motion_revday * TWO_PI / 86400.0
    return (MU_KM3 / n_rad_s ** 2) ** (1.0 / 3.0)


def compute_derived(inclination, eccentricity, mean_motion) -> dict:
    """Compute sma_km, perigee_km, apogee_km, period_min from TLE elements."""
    try:
        sma = mean_motion_to_sma(mean_motion)
        perigee = sma * (1.0 - eccentricity) - EARTH_RADIUS_KM
        apogee  = sma * (1.0 + eccentricity) - EARTH_RADIUS_KM
        period  = 1440.0 / mean_motion
        return {"sma_km": sma, "perigee_km": perigee, "apogee_km": apogee, "period_min": period}
    except (ZeroDivisionError, ValueError):
        return {"sma_km": None, "perigee_km": None, "apogee_km": None, "period_min": None}


# ─── Space weather lookup cache ───────────────────────────────────────────────

class SpaceWeatherCache:
    """Load all space_weather rows into memory for fast nearest-neighbour lookup."""

    def __init__(self, conn: psycopg2.extensions.connection) -> None:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT observed_at, kp_index, ap_index, f107_flux FROM space_weather ORDER BY observed_at")
            rows = cur.fetchall()
        self._times  = [r["observed_at"] for r in rows]
        self._kp     = [r["kp_index"]    for r in rows]
        self._ap     = [r["ap_index"]    for r in rows]
        self._f107   = [r["f107_flux"]   for r in rows]
        log.info("SpaceWeatherCache: loaded %d rows", len(rows))

    def lookup(self, epoch: datetime) -> dict:
        """Return kp/ap/f107 for the nearest observation within ±1 hour."""
        if not self._times:
            return {"kp_at_epoch": None, "f107_at_epoch": None, "ap_at_epoch": None}
        # Binary search
        lo, hi = 0, len(self._times) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self._times[mid] < epoch:
                lo = mid + 1
            else:
                hi = mid
        # Check both sides of the insertion point
        best_idx = lo
        if lo > 0 and abs((self._times[lo - 1] - epoch).total_seconds()) < abs((self._times[lo] - epoch).total_seconds()):
            best_idx = lo - 1
        if abs((self._times[best_idx] - epoch).total_seconds()) > 3600:
            return {"kp_at_epoch": None, "f107_at_epoch": None, "ap_at_epoch": None}
        return {
            "kp_at_epoch":   self._kp[best_idx],
            "f107_at_epoch": self._f107[best_idx],
            "ap_at_epoch":   self._ap[best_idx],
        }


# ─── Feature computation per satellite ───────────────────────────────────────

def compute_features_for_satellite(
    rows: list[dict],
    sw_cache: SpaceWeatherCache,
) -> list[dict]:
    """
    Given time-sorted TLE rows for one satellite, compute feature rows.
    Returns list of dicts ready for insertion into tle_features.
    """
    features = []
    prev = None

    for cur in rows:
        epoch = cur["epoch"]
        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=timezone.utc)

        derived = compute_derived(cur["inclination"], cur["eccentricity"], cur["mean_motion"])
        sw = sw_cache.lookup(epoch)

        feat: dict = {
            "norad_id": cur["norad_id"],
            "epoch":    epoch,
            **derived,
            **sw,
            "dt_hours":       None,
            "d_inclination":  None,
            "d_eccentricity": None,
            "d_raan":         None,
            "d_arg_perigee":  None,
            "d_mean_motion":  None,
            "d_bstar":        None,
            "is_maneuver":          None,
            "maneuver_confidence":  None,
        }

        if prev is not None:
            prev_epoch = prev["epoch"]
            if prev_epoch.tzinfo is None:
                prev_epoch = prev_epoch.replace(tzinfo=timezone.utc)
            dt_hours = (epoch - prev_epoch).total_seconds() / 3600.0
            if dt_hours > 0:
                feat["dt_hours"]       = dt_hours
                feat["d_inclination"]  = (cur["inclination"]  - prev["inclination"])  / dt_hours
                feat["d_eccentricity"] = (cur["eccentricity"] - prev["eccentricity"]) / dt_hours
                feat["d_raan"]         = (cur["raan"]         - prev["raan"])         / dt_hours
                feat["d_arg_perigee"]  = (cur["arg_perigee"]  - prev["arg_perigee"])  / dt_hours
                feat["d_mean_motion"]  = (cur["mean_motion"]  - prev["mean_motion"])  / dt_hours
                feat["d_bstar"]        = (cur["bstar"]        - prev["bstar"])        / dt_hours

        features.append(feat)
        prev = cur

    return features


# ─── NORAD ID iteration ───────────────────────────────────────────────────────

def iter_norad_ids(conn: psycopg2.extensions.connection, norad_ids: list[int] | None) -> list[int]:
    with conn.cursor() as cur:
        if norad_ids:
            cur.execute("SELECT norad_id FROM satellites WHERE norad_id = ANY(%s) ORDER BY norad_id", (norad_ids,))
        else:
            cur.execute("SELECT norad_id FROM satellites ORDER BY norad_id")
        return [r[0] for r in cur.fetchall()]


def fetch_tle_rows(conn: psycopg2.extensions.connection, norad_id: int) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (epoch)
                   norad_id, epoch, inclination, eccentricity, raan,
                   arg_perigee, mean_anomaly, mean_motion, bstar
            FROM tle_history
            WHERE norad_id = %s
              AND inclination IS NOT NULL AND mean_motion IS NOT NULL
            ORDER BY epoch ASC, quality_score DESC
            """,
            (norad_id,),
        )
        return [dict(r) for r in cur.fetchall()]


# ─── Batch insert features ────────────────────────────────────────────────────

def insert_features(conn: psycopg2.extensions.connection, features: list[dict]) -> int:
    if not features:
        return 0
    sql = """
        INSERT INTO tle_features (
            norad_id, epoch, sma_km, perigee_km, apogee_km, period_min,
            dt_hours, d_inclination, d_eccentricity, d_raan, d_arg_perigee,
            d_mean_motion, d_bstar,
            kp_at_epoch, f107_at_epoch, ap_at_epoch,
            is_maneuver, maneuver_confidence
        ) VALUES %s
        ON CONFLICT (norad_id, epoch) DO UPDATE SET
            sma_km          = EXCLUDED.sma_km,
            perigee_km      = EXCLUDED.perigee_km,
            apogee_km       = EXCLUDED.apogee_km,
            period_min      = EXCLUDED.period_min,
            dt_hours        = EXCLUDED.dt_hours,
            d_inclination   = EXCLUDED.d_inclination,
            d_eccentricity  = EXCLUDED.d_eccentricity,
            d_raan          = EXCLUDED.d_raan,
            d_arg_perigee   = EXCLUDED.d_arg_perigee,
            d_mean_motion   = EXCLUDED.d_mean_motion,
            d_bstar         = EXCLUDED.d_bstar,
            kp_at_epoch     = EXCLUDED.kp_at_epoch,
            f107_at_epoch   = EXCLUDED.f107_at_epoch,
            ap_at_epoch     = EXCLUDED.ap_at_epoch,
            computed_at     = NOW()
    """
    data = [
        (
            f["norad_id"], f["epoch"],
            f["sma_km"], f["perigee_km"], f["apogee_km"], f["period_min"],
            f["dt_hours"], f["d_inclination"], f["d_eccentricity"], f["d_raan"],
            f["d_arg_perigee"], f["d_mean_motion"], f["d_bstar"],
            f["kp_at_epoch"], f["f107_at_epoch"], f["ap_at_epoch"],
            f["is_maneuver"], f["maneuver_confidence"],
        )
        for f in features
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, data, page_size=1000)
    conn.commit()
    return len(features)


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(norad_ids: list[int] | None, batch_size: int) -> None:
    conn = get_conn()
    sw_cache = SpaceWeatherCache(conn)
    ids = iter_norad_ids(conn, norad_ids)
    log.info("Processing %d satellites", len(ids))

    total = 0
    pending: list[dict] = []

    for i, nid in enumerate(ids, 1):
        rows = fetch_tle_rows(conn, nid)
        if len(rows) < 2:
            continue
        features = compute_features_for_satellite(rows, sw_cache)
        pending.extend(features)

        if len(pending) >= batch_size:
            total += insert_features(conn, pending)
            pending.clear()
            log.info("  [%d/%d] inserted %d rows total", i, len(ids), total)

    if pending:
        total += insert_features(conn, pending)

    conn.close()
    log.info("Feature engineering complete — %d rows written to tle_features", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute ML features from tle_history → tle_features")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Process all satellites")
    group.add_argument("--norad-ids", nargs="+", type=int, help="Specific NORAD IDs")
    parser.add_argument("--batch-size", type=int, default=10000,
                        help="Rows per DB commit (default: 10000)")
    args = parser.parse_args()

    run(
        norad_ids=None if args.all else args.norad_ids,
        batch_size=args.batch_size,
    )
