"""
maneuver_detector.py — Rule-based maneuver detection from tle_features.

Reads tle_features rows with delta elements and applies threshold-based rules
to label maneuver events. Writes results to:
  - maneuver_events table (one row per detected maneuver)
  - tle_features.is_maneuver + maneuver_confidence (labels in-place)

Maneuver types detected:
  - inclination:      |d_inclination| > threshold_inc
  - altitude/phasing: |d_mean_motion| > threshold_mm (with direction flip)
  - circularization:  d_eccentricity < -threshold_ecc (eccentricity decreasing)
  - deorbit:          perigee_km < threshold_perigee AND d_mean_motion < -threshold_mm

Usage:
  python maneuver_detector.py
  python maneuver_detector.py --norad-ids 25544 20580
  python maneuver_detector.py --threshold-inc 0.003 --threshold-mm 0.00015
  python maneuver_detector.py --all

Requires: .env with POSTGRES_* vars
"""

from __future__ import annotations

import argparse
import logging
import math
import os
from datetime import timezone

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

# Default detection thresholds
DEFAULT_THRESHOLD_INC    = 0.005   # deg/hr — inclination maneuver
DEFAULT_THRESHOLD_MM     = 0.0002  # rev/day/hr — altitude/phasing maneuver
DEFAULT_THRESHOLD_ECC    = 0.001   # per hour — circularization
DEFAULT_THRESHOLD_PERIGEE = 200.0  # km — deorbit candidate


# ─── DB connection ────────────────────────────────────────────────────────────

def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        dbname=os.environ.get("POSTGRES_DB", "sattrack_ml"),
        user=os.environ.get("POSTGRES_USER", "sattrack_ml"),
        password=os.environ["POSTGRES_PASSWORD"],
    )


# ─── Classification logic ─────────────────────────────────────────────────────

def classify_maneuver(
    row: dict,
    threshold_inc: float,
    threshold_mm: float,
    threshold_ecc: float,
    threshold_perigee: float,
) -> tuple[str | None, float]:
    """
    Apply rule-based thresholds to a tle_features row.
    Returns (maneuver_type, confidence) or (None, 0.0) if no maneuver.
    """
    d_inc = row.get("d_inclination")
    d_mm  = row.get("d_mean_motion")
    d_ecc = row.get("d_eccentricity")
    perigee = row.get("perigee_km")

    if d_inc is None or d_mm is None or d_ecc is None:
        return None, 0.0

    abs_inc = abs(d_inc)
    abs_mm  = abs(d_mm)

    maneuver_type: str | None = None
    confidence: float = 0.0

    # Deorbit: low perigee + mean motion increasing (decaying orbit)
    if perigee is not None and perigee < threshold_perigee and d_mm > threshold_mm:
        maneuver_type = "deorbit"
        confidence = min(1.0, 0.5 + (threshold_perigee - perigee) / threshold_perigee * 0.5)

    # Circularization: eccentricity decreasing fast
    elif d_ecc < -threshold_ecc:
        maneuver_type = "circularization"
        confidence = min(1.0, 0.5 + abs(d_ecc) / (threshold_ecc * 10))

    # Inclination maneuver: dominant inclination change
    elif abs_inc > threshold_inc and abs_inc > abs_mm * 10:
        maneuver_type = "inclination"
        confidence = min(1.0, 0.5 + (abs_inc - threshold_inc) / threshold_inc)

    # Altitude / phasing: significant mean motion change
    elif abs_mm > threshold_mm:
        # Distinguish altitude raise/lower from phasing
        if abs_inc > threshold_inc * 0.5:
            maneuver_type = "phasing"
        else:
            maneuver_type = "altitude"
        confidence = min(1.0, 0.5 + (abs_mm - threshold_mm) / threshold_mm)

    return maneuver_type, round(confidence, 4)


def compute_delta_v_proxy(row: dict) -> float:
    """Rough Δv proxy = sqrt(d_inc² + d_raan² + scaled_mm²)."""
    d_inc  = row.get("d_inclination") or 0.0
    d_raan = row.get("d_raan") or 0.0
    d_mm   = row.get("d_mean_motion") or 0.0
    # Scale mean motion change to same order as inclination changes (deg)
    mm_scaled = d_mm * 100.0
    return math.sqrt(d_inc ** 2 + d_raan ** 2 + mm_scaled ** 2)


# ─── DB I/O ───────────────────────────────────────────────────────────────────

def fetch_feature_rows(
    conn: psycopg2.extensions.connection,
    norad_ids: list[int] | None,
) -> list[dict]:
    sql = """
        SELECT
            tf.norad_id, tf.epoch,
            tf.d_inclination, tf.d_eccentricity, tf.d_raan,
            tf.d_arg_perigee, tf.d_mean_motion, tf.d_bstar,
            tf.dt_hours, tf.perigee_km,
            tf.is_maneuver
        FROM tle_features tf
        WHERE tf.dt_hours IS NOT NULL
          AND tf.d_inclination IS NOT NULL
    """
    params: tuple = ()
    if norad_ids:
        sql += " AND tf.norad_id = ANY(%s)"
        params = (norad_ids,)
    sql += " ORDER BY tf.norad_id, tf.epoch"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_prior_epoch(conn: psycopg2.extensions.connection, norad_id: int, epoch) -> object | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT epoch FROM tle_history
            WHERE norad_id = %s AND epoch < %s
            ORDER BY epoch DESC LIMIT 1
            """,
            (norad_id, epoch),
        )
        row = cur.fetchone()
        return row[0] if row else None


def upsert_maneuver_events(
    conn: psycopg2.extensions.connection,
    events: list[dict],
) -> int:
    if not events:
        return 0
    sql = """
        INSERT INTO maneuver_events (
            norad_id, detected_epoch, prior_epoch,
            delta_inclination, delta_mean_motion, delta_eccentricity,
            delta_v_proxy, maneuver_type, confidence, detection_method
        ) VALUES %s
        ON CONFLICT (norad_id, detected_epoch) DO UPDATE SET
            maneuver_type     = EXCLUDED.maneuver_type,
            confidence        = EXCLUDED.confidence,
            delta_v_proxy     = EXCLUDED.delta_v_proxy,
            detection_method  = EXCLUDED.detection_method
    """
    data = [
        (
            e["norad_id"], e["detected_epoch"], e.get("prior_epoch"),
            e.get("delta_inclination"), e.get("delta_mean_motion"), e.get("delta_eccentricity"),
            e.get("delta_v_proxy"), e["maneuver_type"], e["confidence"], "rule_based",
        )
        for e in events
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, data, page_size=500)
    conn.commit()
    return len(events)


def label_tle_features(
    conn: psycopg2.extensions.connection,
    labels: list[tuple],  # (norad_id, epoch, is_maneuver, confidence)
) -> None:
    """Update is_maneuver + maneuver_confidence in tle_features."""
    if not labels:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            UPDATE tle_features SET
                is_maneuver          = data.is_maneuver::boolean,
                maneuver_confidence  = data.confidence::double precision
            FROM (VALUES %s) AS data(norad_id, epoch, is_maneuver, confidence)
            WHERE tle_features.norad_id = data.norad_id::integer
              AND tle_features.epoch    = data.epoch::timestamptz
            """,
            labels,
            page_size=500,
        )
    conn.commit()


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(
    norad_ids: list[int] | None,
    threshold_inc: float,
    threshold_mm: float,
    threshold_ecc: float,
    threshold_perigee: float,
) -> None:
    conn = get_conn()
    log.info("Fetching tle_features rows (norad_ids=%s)...", norad_ids or "all")
    rows = fetch_feature_rows(conn, norad_ids)
    log.info("Processing %d feature rows", len(rows))

    events: list[dict] = []
    labels: list[tuple] = []

    for row in rows:
        mtype, confidence = classify_maneuver(
            row, threshold_inc, threshold_mm, threshold_ecc, threshold_perigee
        )
        is_maneuver = mtype is not None

        labels.append((
            row["norad_id"],
            row["epoch"],
            is_maneuver,
            confidence if is_maneuver else 0.0,
        ))

        if is_maneuver:
            prior = get_prior_epoch(conn, row["norad_id"], row["epoch"])
            dv = compute_delta_v_proxy(row)
            events.append({
                "norad_id":          row["norad_id"],
                "detected_epoch":    row["epoch"],
                "prior_epoch":       prior,
                "delta_inclination": abs(row.get("d_inclination") or 0.0),
                "delta_mean_motion": abs(row.get("d_mean_motion") or 0.0),
                "delta_eccentricity": abs(row.get("d_eccentricity") or 0.0),
                "delta_v_proxy":     round(dv, 6),
                "maneuver_type":     mtype,
                "confidence":        confidence,
            })

    log.info("Detected %d maneuver events from %d feature rows", len(events), len(rows))

    n_events = upsert_maneuver_events(conn, events)
    log.info("Wrote %d rows to maneuver_events", n_events)

    label_tle_features(conn, labels)
    log.info("Updated %d tle_features labels", len(labels))

    conn.close()

    # Summary
    if events:
        from collections import Counter
        type_counts = Counter(e["maneuver_type"] for e in events)
        log.info("Maneuver breakdown: %s", dict(type_counts))
        unique_sats = len({e["norad_id"] for e in events})
        log.info("Satellites with detected maneuvers: %d", unique_sats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rule-based maneuver detection from tle_features")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Process all satellites (default)")
    group.add_argument("--norad-ids", nargs="+", type=int, help="Specific NORAD IDs")
    parser.add_argument("--threshold-inc",  type=float, default=DEFAULT_THRESHOLD_INC,
                        help=f"Inclination change threshold deg/hr (default: {DEFAULT_THRESHOLD_INC})")
    parser.add_argument("--threshold-mm",   type=float, default=DEFAULT_THRESHOLD_MM,
                        help=f"Mean motion change threshold rev/day/hr (default: {DEFAULT_THRESHOLD_MM})")
    parser.add_argument("--threshold-ecc",  type=float, default=DEFAULT_THRESHOLD_ECC,
                        help=f"Eccentricity change threshold /hr (default: {DEFAULT_THRESHOLD_ECC})")
    parser.add_argument("--threshold-perigee", type=float, default=DEFAULT_THRESHOLD_PERIGEE,
                        help=f"Perigee km for deorbit detection (default: {DEFAULT_THRESHOLD_PERIGEE})")
    args = parser.parse_args()

    run(
        norad_ids=args.norad_ids if not args.all else None,
        threshold_inc=args.threshold_inc,
        threshold_mm=args.threshold_mm,
        threshold_ecc=args.threshold_ecc,
        threshold_perigee=args.threshold_perigee,
    )
