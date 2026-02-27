"""
sync_from_supabase.py — Mirror Supabase live data → local PostgreSQL ML database.

Usage:
  python sync_from_supabase.py --since 2026-01-01   # incremental sync
  python sync_from_supabase.py --full                # complete mirror

Requires: .env with SUPABASE_URL, SUPABASE_SERVICE_KEY, POSTGRES_* vars
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone, date
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


# ─── Generic paginated Supabase fetch ────────────────────────────────────────

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=30))
def _fetch_page(supa: Client, table: str, filters: dict, offset: int) -> list[dict]:
    q = supa.table(table).select("*")
    for col, val in filters.items():
        q = q.gte(col, val)
    result = q.range(offset, offset + PAGE_SIZE - 1).execute()
    return result.data or []


def fetch_all(supa: Client, table: str, filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    rows: list[dict] = []
    offset = 0
    while True:
        page = _fetch_page(supa, table, filters, offset)
        if not page:
            break
        rows.extend(page)
        log.info("  %s: fetched %d rows (total %d)", table, len(page), len(rows))
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


# ─── Local upsert helpers ─────────────────────────────────────────────────────

def upsert_satellites_local(conn: psycopg2.extensions.connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO satellites (
            norad_id, cospar_id, name, orbit_class, object_type, status,
            launch_date, decay_date, operator, country, source_flags, created_at, updated_at
        ) VALUES %s
        ON CONFLICT (norad_id) DO UPDATE SET
            cospar_id    = EXCLUDED.cospar_id,
            name         = EXCLUDED.name,
            orbit_class  = EXCLUDED.orbit_class,
            object_type  = EXCLUDED.object_type,
            status       = EXCLUDED.status,
            launch_date  = EXCLUDED.launch_date,
            decay_date   = EXCLUDED.decay_date,
            operator     = EXCLUDED.operator,
            country      = EXCLUDED.country,
            source_flags = EXCLUDED.source_flags,
            updated_at   = NOW()
    """
    data = [
        (
            r["norad_id"], r.get("cospar_id"), r["name"],
            r.get("orbit_class"), r.get("object_type"), r.get("status"),
            r.get("launch_date"), r.get("decay_date"),
            r.get("operator"), r.get("country"),
            psycopg2.extras.Json(r.get("source_flags") or {}),
            r.get("created_at"), r.get("updated_at"),
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, data, page_size=500)
    conn.commit()
    return len(rows)


def upsert_tle_history_local(conn: psycopg2.extensions.connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO tle_history (
            norad_id, epoch, source, tle_line1, tle_line2,
            inclination, eccentricity, raan, arg_perigee, mean_anomaly,
            mean_motion, bstar, quality_score, is_current, ingested_at
        ) VALUES %s
        ON CONFLICT (norad_id, epoch, source) DO NOTHING
    """
    data = [
        (
            r["norad_id"], r["epoch"], r["source"], r["tle_line1"], r["tle_line2"],
            r.get("inclination"), r.get("eccentricity"), r.get("raan"),
            r.get("arg_perigee"), r.get("mean_anomaly"),
            r.get("mean_motion"), r.get("bstar"),
            r.get("quality_score", 50), r.get("is_current", False),
            r.get("ingested_at"),
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, data, page_size=500)
    conn.commit()
    return len(rows)


def upsert_space_weather_local(conn: psycopg2.extensions.connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO space_weather (observed_at, kp_index, ap_index, f107_flux, source, ingested_at)
        VALUES %s
        ON CONFLICT (observed_at) DO UPDATE SET
            kp_index  = EXCLUDED.kp_index,
            ap_index  = EXCLUDED.ap_index,
            f107_flux = EXCLUDED.f107_flux,
            source    = EXCLUDED.source
    """
    data = [
        (
            r["observed_at"], r.get("kp_index"), r.get("ap_index"),
            r.get("f107_flux"), r.get("source", "noaa"), r.get("ingested_at"),
        )
        for r in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, data, page_size=500)
    conn.commit()
    return len(rows)


# ─── Main sync logic ──────────────────────────────────────────────────────────

def sync(since: date | None = None, full: bool = False) -> None:
    supa = get_supabase()
    conn = get_local_conn()
    log.info("Connected to local DB and Supabase")

    filters: dict[str, Any] = {}
    if since and not full:
        filters["ingested_at"] = since.isoformat()

    # 1. satellites (always full mirror — small table)
    log.info("Syncing satellites...")
    sat_rows = fetch_all(supa, "satellites")
    n = upsert_satellites_local(conn, sat_rows)
    log.info("satellites: %d rows synced", n)

    # 2. tle_history (paginated, filtered by ingested_at if incremental)
    log.info("Syncing tle_history (filter=%s)...", filters or "none")
    tle_rows = fetch_all(supa, "tle_history", filters)
    n = upsert_tle_history_local(conn, tle_rows)
    log.info("tle_history: %d rows synced", n)

    # 3. space_weather (always full — small table)
    log.info("Syncing space_weather...")
    sw_rows = fetch_all(supa, "space_weather")
    n = upsert_space_weather_local(conn, sw_rows)
    log.info("space_weather: %d rows synced", n)

    conn.close()
    log.info("Sync complete.")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Supabase data → local ML PostgreSQL")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--since", metavar="YYYY-MM-DD",
        help="Incremental sync: only rows ingested on or after this date"
    )
    group.add_argument(
        "--full", action="store_true",
        help="Full mirror: sync all rows (slow for large tle_history)"
    )
    args = parser.parse_args()

    since_date: date | None = None
    if args.since:
        try:
            since_date = date.fromisoformat(args.since)
        except ValueError:
            print(f"Invalid date: {args.since}  (expected YYYY-MM-DD)")
            sys.exit(1)

    sync(since=since_date, full=args.full)
