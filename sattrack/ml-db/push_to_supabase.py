"""
push_to_supabase.py — Push ML-enriched data from local PostgreSQL → Supabase.

Usage:
  python push_to_supabase.py --enrichment              # push satellite_enrichment
  python push_to_supabase.py --maneuvers               # push maneuver_events
  python push_to_supabase.py --decay                   # compute + push decay_predictions
  python push_to_supabase.py --all                     # all three
  python push_to_supabase.py --enrichment --dry-run    # print counts only, no writes
  python push_to_supabase.py --since 2026-01-01        # enrichment rows updated since date

Requires: .env with SUPABASE_URL, SUPABASE_SERVICE_KEY, POSTGRES_* vars
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

PAGE_SIZE = 1000

# ─── Connection helpers ───────────────────────────────────────────────────────

def get_supabase() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


def get_local_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        dbname=os.environ.get("POSTGRES_DB", "sattrack_ml"),
        user=os.environ.get("POSTGRES_USER", "sattrack_ml"),
        password=os.environ["POSTGRES_PASSWORD"],
    )


# ─── Type-safe row conversion ─────────────────────────────────────────────────

def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Convert psycopg2 row dict into a plain Python dict safe for Supabase JSON.

    - numpy scalar types → Python native int/float
    - datetime/date → ISO 8601 string
    - list values (arrays) are passed through as-is (Supabase accepts them)
    - None values are kept as None
    """
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif isinstance(v, (datetime,)):
            out[k] = v.isoformat()
        elif isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, list):
            # Postgres arrays — pass through (already plain Python types from psycopg2)
            out[k] = v
        elif hasattr(v, '__class__') and v.__class__.__name__ == 'Decimal':
            # psycopg2 returns NUMERIC/DECIMAL columns as Python Decimal — cast to float
            from decimal import Decimal as _Decimal
            out[k] = float(v) if isinstance(v, _Decimal) else v
        else:
            # Coerce numpy scalars to native Python types if numpy is present
            try:
                import numpy as np
                if isinstance(v, np.integer):
                    out[k] = int(v)
                elif isinstance(v, np.floating):
                    out[k] = float(v)
                elif isinstance(v, np.bool_):
                    out[k] = bool(v)
                else:
                    out[k] = v
            except ImportError:
                out[k] = v
    return out


# ─── Supabase upsert wrappers (with retry) ───────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _upsert_page(
    supa: Client,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    ignore_duplicates: bool = False,
) -> int:
    """Upsert a single page of rows into a Supabase table. Returns row count."""
    result = (
        supa.table(table)
        .upsert(rows, on_conflict=on_conflict, ignore_duplicates=ignore_duplicates)
        .execute()
    )
    return len(result.data) if result.data else 0


# ─── push_enrichment ─────────────────────────────────────────────────────────

ENRICHMENT_SQL = """
SELECT
    o.norad_id,
    o.rcs_m2,
    o.rcs_size_class,
    o.launch_mass_kg,
    o.dry_mass_kg,
    o.power_bol_w,
    o.orbit_type::text        AS orbit_type,
    o.altitude_km,
    o.apogee_km,
    o.perigee_km,
    o.incl_deg,
    o.period_min,
    o.eccentricity,
    o.constellation,
    o.primary_purpose,
    o.comm_bands_arr,
    o.throughput_gbps,
    o.propulsion_type,
    o.design_life_yr,
    de.conjunction_risk::text AS conjunction_risk,
    de.parent_norad,
    de.parent_object,
    de.frag_event_name,
    de.constellations_at_risk
FROM catalog.objects o
LEFT JOIN catalog.debris_enrichment de USING (norad_id)
"""

ENRICHMENT_SQL_SINCE = ENRICHMENT_SQL.rstrip() + "\nWHERE o.updated_at >= %(since)s\n"


def push_enrichment(
    conn: psycopg2.extensions.connection,
    supa: Client,
    dry_run: bool = False,
    since: date | None = None,
) -> int:
    """Read catalog.objects + debris_enrichment, push to satellite_enrichment in Supabase."""
    log.info("push_enrichment: starting (dry_run=%s, since=%s)", dry_run, since)

    # Build the query — use OFFSET/LIMIT pagination
    base_sql = ENRICHMENT_SQL_SINCE if since else ENRICHMENT_SQL
    total_pushed = 0
    offset = 0
    page_num = 0

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        while True:
            paged_sql = base_sql + f" ORDER BY o.norad_id LIMIT {PAGE_SIZE} OFFSET {offset}"

            if since:
                cur.execute(paged_sql, {"since": since.isoformat()})
            else:
                cur.execute(paged_sql)

            raw_rows = cur.fetchall()
            if not raw_rows:
                break

            # Filter out rows with no norad_id, convert types
            _VALID_RISK = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
            rows = []
            for r in raw_rows:
                if r.get("norad_id") is None:
                    continue
                cleaned = _clean_row(dict(r))
                # Sanitise conjunction_risk — only allow valid CHECK values
                risk = cleaned.get("conjunction_risk")
                if risk and risk.upper() not in _VALID_RISK:
                    cleaned["conjunction_risk"] = None
                elif risk:
                    cleaned["conjunction_risk"] = risk.upper()
                rows.append(cleaned)

            if dry_run:
                log.info(
                    "  [DRY RUN] page %d: would push %d rows (offset %d)",
                    page_num, len(rows), offset,
                )
            else:
                try:
                    n = _upsert_page(supa, "satellite_enrichment", rows, on_conflict="norad_id")
                    total_pushed += n
                except Exception as exc:
                    log.error(
                        "  push_enrichment: error on page %d (offset %d): %s — skipping batch",
                        page_num, offset, exc,
                    )

                if page_num % 10 == 0:
                    log.info(
                        "  push_enrichment: page %d done, %d rows pushed so far (offset %d)",
                        page_num, total_pushed, offset,
                    )

            if len(raw_rows) < PAGE_SIZE:
                break

            offset += PAGE_SIZE
            page_num += 1

    if dry_run:
        log.info("push_enrichment [DRY RUN]: %d rows would be pushed", offset + len(raw_rows) if 'raw_rows' in dir() else 0)
    else:
        log.info("push_enrichment: complete — %d rows pushed", total_pushed)

    return total_pushed


# ─── push_maneuvers ──────────────────────────────────────────────────────────

MANEUVERS_SQL = """
SELECT
    norad_id,
    detected_epoch,
    prior_epoch,
    maneuver_type,
    confidence,
    delta_v_proxy,
    delta_inclination,
    delta_mean_motion,
    delta_eccentricity,
    detection_method
FROM public.maneuver_events
ORDER BY norad_id, detected_epoch
"""


def push_maneuvers(
    conn: psycopg2.extensions.connection,
    supa: Client,
    dry_run: bool = False,
) -> int:
    """Read public.maneuver_events and push to Supabase maneuver_events."""
    log.info("push_maneuvers: starting (dry_run=%s)", dry_run)

    total_pushed = 0
    offset = 0
    page_num = 0

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        while True:
            paged_sql = MANEUVERS_SQL.rstrip() + f" LIMIT {PAGE_SIZE} OFFSET {offset}"
            cur.execute(paged_sql)
            raw_rows = cur.fetchall()
            if not raw_rows:
                break

            rows: list[dict[str, Any]] = []
            for r in raw_rows:
                cleaned = _clean_row(dict(r))
                if cleaned.get("norad_id") is None:
                    continue
                rows.append(cleaned)

            if dry_run:
                log.info(
                    "  [DRY RUN] page %d: would push %d rows (offset %d)",
                    page_num, len(rows), offset,
                )
            else:
                try:
                    n = _upsert_page(
                        supa,
                        "maneuver_events",
                        rows,
                        on_conflict="norad_id,detected_epoch",
                        ignore_duplicates=True,
                    )
                    total_pushed += n
                except Exception as exc:
                    log.error(
                        "  push_maneuvers: error on page %d (offset %d): %s — skipping batch",
                        page_num, offset, exc,
                    )

                if page_num % 10 == 0:
                    log.info(
                        "  push_maneuvers: page %d done, %d rows pushed so far",
                        page_num, total_pushed,
                    )

            if len(raw_rows) < PAGE_SIZE:
                break

            offset += PAGE_SIZE
            page_num += 1

    log.info("push_maneuvers: complete — %d rows pushed", total_pushed)
    return total_pushed


# ─── push_decay ──────────────────────────────────────────────────────────────

DECAY_SQL = """
SELECT DISTINCT ON (norad_id)
    norad_id,
    perigee_km,
    d_mean_motion,
    epoch
FROM public.tle_features
WHERE perigee_km IS NOT NULL
  AND perigee_km < 500
  AND d_mean_motion IS NOT NULL
  AND d_mean_motion < -0.00005
  AND is_maneuver = FALSE
ORDER BY norad_id, epoch DESC
"""


def _compute_decay_record(
    norad_id: int,
    perigee_km: float,
    d_mean_motion: float,
    epoch: Any,
) -> dict[str, Any]:
    """
    Compute a decay prediction dict from TLE feature values.

    d_mean_motion units: rev/day/hr (rate of change of mean motion per day).
    Approximation: 1 rev/day increase in mean motion ≈ 28 km altitude decrease for LEO.
    Reentry threshold: 120 km.
    """
    # Convert to km/day altitude loss (d_mean_motion is negative = decaying)
    decay_rate_km_per_day = abs(d_mean_motion) * 24 * 28  # rough proxy

    km_remaining = float(perigee_km) - 120.0

    predicted_reentry: str | None = None
    confidence_days: int | None = None

    if decay_rate_km_per_day > 0 and km_remaining > 0:
        days_remaining = km_remaining / decay_rate_km_per_day
        predicted_reentry = (date.today() + timedelta(days=days_remaining)).isoformat()
        confidence_days = max(3, int(days_remaining * 0.15))  # ±15% window
    # else: already below threshold or zero rate — leave as None

    # Epoch to ISO string
    epoch_str: str | None = None
    if isinstance(epoch, datetime):
        epoch_str = epoch.isoformat()
    elif isinstance(epoch, date):
        epoch_str = epoch.isoformat()
    elif epoch is not None:
        epoch_str = str(epoch)

    return {
        "norad_id": int(norad_id),
        "perigee_km": float(perigee_km),
        "decay_rate_km_per_day": round(decay_rate_km_per_day, 6),
        "predicted_reentry": predicted_reentry,
        "confidence_days": confidence_days,
        # Supabase decay_predictions uses norad_id as PK (one row per sat) — no prediction_date
    }


def push_decay(
    conn: psycopg2.extensions.connection,
    supa: Client,
    dry_run: bool = False,
) -> int:
    """Compute decay estimates from tle_features and push to Supabase decay_predictions."""
    log.info("push_decay: starting (dry_run=%s)", dry_run)

    total_pushed = 0
    page_num = 0
    offset = 0

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # DISTINCT ON with ORDER BY forces a specific query shape — wrap in pagination
        # We re-execute with LIMIT/OFFSET on the outer query via a subquery approach
        while True:
            paged_sql = (
                "SELECT * FROM (\n"
                + DECAY_SQL
                + "\n) _decay_base"
                + f" LIMIT {PAGE_SIZE} OFFSET {offset}"
            )
            cur.execute(paged_sql)
            raw_rows = cur.fetchall()
            if not raw_rows:
                break

            rows: list[dict[str, Any]] = []
            for r in raw_rows:
                d = dict(r)
                norad_id = d.get("norad_id")
                perigee_km = d.get("perigee_km")
                d_mean_motion = d.get("d_mean_motion")
                epoch = d.get("epoch")

                if norad_id is None or perigee_km is None or d_mean_motion is None:
                    continue

                record = _compute_decay_record(
                    norad_id=norad_id,
                    perigee_km=perigee_km,
                    d_mean_motion=d_mean_motion,
                    epoch=epoch,
                )
                rows.append(record)

            if dry_run:
                log.info(
                    "  [DRY RUN] page %d: would push %d rows (offset %d)",
                    page_num, len(rows), offset,
                )
            else:
                try:
                    n = _upsert_page(
                        supa,
                        "decay_predictions",
                        rows,
                        on_conflict="norad_id",
                    )
                    total_pushed += n
                except Exception as exc:
                    log.error(
                        "  push_decay: error on page %d (offset %d): %s — skipping batch",
                        page_num, offset, exc,
                    )

                if page_num % 10 == 0:
                    log.info(
                        "  push_decay: page %d done, %d rows pushed so far",
                        page_num, total_pushed,
                    )

            if len(raw_rows) < PAGE_SIZE:
                break

            offset += PAGE_SIZE
            page_num += 1

    log.info("push_decay: complete — %d rows pushed", total_pushed)
    return total_pushed


# ─── Summary printer ──────────────────────────────────────────────────────────

def print_summary(counts: dict[str, int], dry_run: bool) -> None:
    mode = "[DRY RUN]" if dry_run else ""
    print("\n" + "=" * 50)
    print(f"  Push to Supabase — Summary {mode}")
    print("=" * 50)
    for table, count in counts.items():
        label = "would push" if dry_run else "pushed"
        print(f"  {table:<30}  {count:>8} rows {label}")
    print("=" * 50 + "\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Push ML-enriched data from local PostgreSQL → Supabase"
    )
    parser.add_argument(
        "--enrichment",
        action="store_true",
        help="Push catalog.objects + debris_enrichment → satellite_enrichment",
    )
    parser.add_argument(
        "--maneuvers",
        action="store_true",
        help="Push public.maneuver_events → Supabase maneuver_events",
    )
    parser.add_argument(
        "--decay",
        action="store_true",
        help="Compute decay estimates from tle_features → Supabase decay_predictions",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all three pushes (enrichment + maneuvers + decay)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts only — no writes to Supabase",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Enrichment only: filter to rows with updated_at >= this date",
    )
    args = parser.parse_args()

    # Validate at least one action flag
    if not (args.enrichment or args.maneuvers or args.decay or args.all):
        parser.error("Specify at least one of: --enrichment, --maneuvers, --decay, --all")

    # Parse --since date
    since_date: date | None = None
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            print(f"Invalid date: {args.since}  (expected YYYY-MM-DD)")
            sys.exit(1)

    # Resolve which tasks to run
    run_enrichment = args.enrichment or args.all
    run_maneuvers = args.maneuvers or args.all
    run_decay = args.decay or args.all

    log.info(
        "Connecting to local ML DB (host=%s port=%s db=%s)...",
        os.environ.get("POSTGRES_HOST", "localhost"),
        os.environ.get("POSTGRES_PORT", "5433"),
        os.environ.get("POSTGRES_DB", "sattrack_ml"),
    )
    conn = get_local_conn()
    supa = get_supabase()
    log.info("Connected to local DB and Supabase.")

    counts: dict[str, int] = {}

    try:
        if run_enrichment:
            n = push_enrichment(conn, supa, dry_run=args.dry_run, since=since_date)
            counts["satellite_enrichment"] = n

        if run_maneuvers:
            n = push_maneuvers(conn, supa, dry_run=args.dry_run)
            counts["maneuver_events"] = n

        if run_decay:
            n = push_decay(conn, supa, dry_run=args.dry_run)
            counts["decay_predictions"] = n

    finally:
        conn.close()
        log.info("Local DB connection closed.")

    print_summary(counts, dry_run=args.dry_run)
