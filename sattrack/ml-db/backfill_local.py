"""
backfill_local.py — Download historical TLEs and ingest into local PostgreSQL ML database.

Adapted from ../backfill.py (Supabase target → local psycopg2 target).
All download functions are reused verbatim; only the ingestion path changes.

Usage:
  python backfill_local.py --source ethz
  python backfill_local.py --source spacetrack --norad-ids 25544 20580 --st-user U --st-pass P
  python backfill_local.py --source celestrak_archives
  python backfill_local.py --source ethz --all-active
  python backfill_local.py --ingest-only --output ./tle_training_data

Requires: .env with POSTGRES_* vars (Supabase vars not needed here)
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from sgp4.api import Satrec
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = Path("./tle_training_data")
RATE_LIMIT_DELAY = 1.0
MATH_PI = math.pi
MU_KM3 = 398600.4418
EARTH_RADIUS_KM = 6371.0

# ─── Local DB connection ──────────────────────────────────────────────────────

def get_local_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        dbname=os.environ.get("POSTGRES_DB", "sattrack_ml"),
        user=os.environ.get("POSTGRES_USER", "sattrack_ml"),
        password=os.environ["POSTGRES_PASSWORD"],
    )


# ─── Orbit classification helper ─────────────────────────────────────────────

def classify_orbit(mean_motion_rad_per_min: float) -> str:
    try:
        a_km = (MU_KM3 / (mean_motion_rad_per_min * 60 / (2 * MATH_PI)) ** 2) ** (1 / 3)
        alt_km = a_km - 6378.137
    except (ZeroDivisionError, ValueError):
        return "UNKNOWN"
    if alt_km < 2000:
        return "LEO"
    if alt_km < 35000:
        return "MEO"
    if alt_km < 37000:
        return "GEO"
    return "HEO"


# ─── Batch ingest to local PostgreSQL ─────────────────────────────────────────

def _upsert_batch(
    conn: psycopg2.extensions.connection,
    sat_batch: list[dict],
    tle_batch: list[dict],
) -> int:
    if not tle_batch:
        return 0

    sat_sql = """
        INSERT INTO satellites (norad_id, name, orbit_class, source_flags)
        VALUES %s
        ON CONFLICT (norad_id) DO UPDATE SET
            orbit_class  = COALESCE(EXCLUDED.orbit_class, satellites.orbit_class),
            source_flags = satellites.source_flags || EXCLUDED.source_flags,
            updated_at   = NOW()
    """
    sat_data = [
        (
            r["norad_id"],
            r["name"],
            r.get("orbit_class"),
            psycopg2.extras.Json(r.get("source_flags") or {}),
        )
        for r in sat_batch
    ]

    tle_sql = """
        INSERT INTO tle_history (
            norad_id, epoch, source, tle_line1, tle_line2,
            inclination, eccentricity, raan, arg_perigee,
            mean_anomaly, mean_motion, bstar, quality_score
        ) VALUES %s
        ON CONFLICT (norad_id, epoch, source) DO NOTHING
    """
    tle_data = [
        (
            r["norad_id"], r["epoch"], r["source"], r["tle_line1"], r["tle_line2"],
            r.get("inclination"), r.get("eccentricity"), r.get("raan"),
            r.get("arg_perigee"), r.get("mean_anomaly"),
            r.get("mean_motion"), r.get("bstar"),
            r.get("quality_score", 50),
        )
        for r in tle_batch
    ]

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sat_sql, sat_data, page_size=500)
        psycopg2.extras.execute_values(cur, tle_sql, tle_data, page_size=500)
    conn.commit()
    return len(tle_batch)


def ingest_files_to_local(
    tle_dir: Path,
    source_name: str = "backfill",
    batch_size: int = 5000,
) -> int:
    """Parse TLE files in tle_dir and ingest them into local PostgreSQL."""
    conn = get_local_conn()
    tle_files = list(tle_dir.rglob("*.tle")) + list(tle_dir.rglob("*.xtle"))
    log.info("Scanning %d TLE files in %s", len(tle_files), tle_dir)

    sat_batch: list[dict] = []
    tle_batch: list[dict] = []
    total_ingested = 0
    total_skipped = 0

    def flush(force: bool = False) -> None:
        nonlocal total_ingested
        if len(tle_batch) >= batch_size or force:
            try:
                n = _upsert_batch(conn, sat_batch[:], tle_batch[:])
                total_ingested += n
                log.info("  Ingested %d rows (total %d)", n, total_ingested)
            except Exception as exc:
                log.error("Batch failed: %s", exc)
            sat_batch.clear()
            tle_batch.clear()

    for tle_file in tle_files:
        src = source_name if source_name != "auto" else tle_file.parent.name
        lines = [l.strip() for l in tle_file.read_text(errors="ignore").splitlines() if l.strip()]
        i = 0
        name = "UNKNOWN"
        while i < len(lines):
            # 3-line format: name line followed by line1/line2
            if (
                not lines[i].startswith("1 ")
                and i + 2 < len(lines)
                and lines[i + 1].startswith("1 ")
                and lines[i + 2].startswith("2 ")
            ):
                name = lines[i].strip()
                i += 1
                continue

            if lines[i].startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
                l1, l2 = lines[i], lines[i + 1]
                try:
                    sat = Satrec.twoline2rv(l1, l2)
                    norad_id = sat.satnum
                    if norad_id == 0:
                        i += 2
                        name = "UNKNOWN"
                        continue

                    epoch_dt = datetime.fromtimestamp(
                        (sat.jdsatepoch - 2440587.5) * 86400.0, tz=timezone.utc
                    )
                    n_revday = sat.no_kozai * 1440.0 / (2 * MATH_PI)
                    orbit_class = classify_orbit(sat.no_kozai)

                    tle_batch.append({
                        "norad_id":    norad_id,
                        "epoch":       epoch_dt.isoformat(),
                        "source":      src,
                        "tle_line1":   l1,
                        "tle_line2":   l2,
                        "inclination": sat.inclo * 180.0 / MATH_PI,
                        "eccentricity": sat.ecco,
                        "raan":        sat.nodeo * 180.0 / MATH_PI,
                        "arg_perigee": sat.argpo * 180.0 / MATH_PI,
                        "mean_anomaly": sat.mo * 180.0 / MATH_PI,
                        "mean_motion": n_revday,
                        "bstar":       sat.bstar,
                        "quality_score": 70,
                    })
                    sat_batch.append({
                        "norad_id":    norad_id,
                        "name":        name if name != "UNKNOWN" else f"SAT-{norad_id}",
                        "orbit_class": orbit_class,
                        "source_flags": {src: True},
                    })
                    flush()
                except Exception:
                    total_skipped += 1
                i += 2
                name = "UNKNOWN"
            else:
                i += 1

    flush(force=True)
    conn.close()
    log.info("Ingest complete — %d rows ingested, %d skipped", total_ingested, total_skipped)
    return total_ingested


# ─── Download functions (adapted from ../backfill.py) ────────────────────────

def download_ethz_satdb(
    norad_ids=None, start_date=None, end_date=None,
    output_dir: Path = OUTPUT_DIR / "ethz_satdb",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if start_date is None:
        start_date = datetime.utcnow() - timedelta(days=365)
    if end_date is None:
        end_date = datetime.utcnow()

    base_url = "https://satdb.ethz.ch/api/satellitedata/"

    if norad_ids is None:
        log.info("[ETH Zurich] Fetching all satellites (%s → %s)...",
                 start_date.date(), end_date.date())
        params = {
            "start-datetime": start_date.strftime("%Y%m%dT%H%M"),
            "end-datetime":   end_date.strftime("%Y%m%dT%H%M"),
            "without-frequency-data": "True",
            "before": 3, "after": 0,
        }
        all_tles = _paginate_ethz(base_url, params)
        out_file = output_dir / f"all_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.tle"
        _save_tle_list(all_tles, out_file)
        log.info("[ETH Zurich] Saved %d TLEs → %s", len(all_tles), out_file)
        return out_file

    all_tles = []
    for norad_id in norad_ids:
        params = {
            "start-datetime": start_date.strftime("%Y%m%dT%H%M"),
            "end-datetime":   end_date.strftime("%Y%m%dT%H%M"),
            "norad-id":       norad_id,
            "without-frequency-data": "True",
            "before": 7, "after": 7,
        }
        tles = _paginate_ethz(base_url, params)
        all_tles.extend(tles)
        log.info("[ETH Zurich] NORAD %d: %d TLEs", norad_id, len(tles))
        time.sleep(RATE_LIMIT_DELAY)

    out_file = output_dir / "selected_satellites.tle"
    _save_tle_list(all_tles, out_file)
    return out_file


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=20))
def _paginate_ethz(url: str, params: dict) -> list:
    tles = []
    next_url: str | None = url
    req_params: dict | None = params.copy()
    while next_url:
        if req_params:
            resp = requests.get(next_url, params=req_params, timeout=30)
            req_params = None
        else:
            resp = requests.get(next_url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        tles.extend(data.get("results", []))
        next_url = data.get("next")
        if next_url:
            time.sleep(RATE_LIMIT_DELAY)
    return tles


def _save_tle_list(tles: list, filepath: Path) -> None:
    with open(filepath, "w") as f:
        for tle in tles:
            norad_str = tle.get("norad_str", "")
            if norad_str:
                f.write(norad_str + "\n")


def download_spacetrack_history(
    username: str, password: str, norad_ids: list[int],
    start_date=None, end_date=None,
    output_dir: Path = OUTPUT_DIR / "spacetrack",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if start_date is None:
        start_date = datetime.utcnow() - timedelta(days=730)
    if end_date is None:
        end_date = datetime.utcnow()

    ST_BASE = "https://www.space-track.org"
    session = requests.Session()
    resp = session.post(f"{ST_BASE}/ajaxauth/login",
                        data={"identity": username, "password": password})
    if resp.status_code != 200 or "Login" in resp.text:
        log.error("[SpaceTrack] Login failed — check credentials")
        return
    log.info("[SpaceTrack] Logged in")

    date_range = f"{start_date.strftime('%Y-%m-%d')}--%{end_date.strftime('%Y-%m-%d')}"

    for i, norad_id in enumerate(norad_ids):
        if i > 0 and i % 25 == 0:
            log.info("[SpaceTrack] Rate limit pause (%d/%d)...", i, len(norad_ids))
            time.sleep(65)
        url = (
            f"{ST_BASE}/basicspacedata/query/class/gp_history"
            f"/NORAD_CAT_ID/{norad_id}/EPOCH/{date_range}"
            f"/orderby/EPOCH asc/format/json"
        )
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            out_file = output_dir / f"{norad_id:05d}_history.json"
            with open(out_file, "w") as f:
                json.dump(records, f)
            log.info("[SpaceTrack] NORAD %d: %d TLEs", norad_id, len(records))
        except Exception as exc:
            log.error("[SpaceTrack] NORAD %d: %s", norad_id, exc)
        time.sleep(2.5)

    session.get(f"{ST_BASE}/auth/logout")
    log.info("[SpaceTrack] Logged out")


def download_celestrak_archives(
    groups: list[str] | None = None,
    output_dir: Path = OUTPUT_DIR / "celestrak_archives",
) -> None:
    ARCHIVES = {
        "weather":     ["https://celestrak.org/NORAD/archives/weather.zip"],
        "noaa":        ["https://celestrak.org/NORAD/archives/noaa.zip"],
        "iss_history": ["https://celestrak.org/NORAD/archives/stations.zip"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    if groups is None:
        groups = list(ARCHIVES.keys())
    for group in groups:
        for url in ARCHIVES.get(group, []):
            try:
                log.info("[CelesTrak Archive] Downloading %s...", group)
                resp = requests.get(url, timeout=60, stream=True)
                if resp.status_code == 200:
                    try:
                        z = zipfile.ZipFile(io.BytesIO(resp.content))
                        z.extractall(output_dir / group)
                        log.info("[CelesTrak Archive] %s: extracted %d files", group, len(z.namelist()))
                    except zipfile.BadZipFile:
                        out_file = output_dir / f"{group}.tle"
                        out_file.write_bytes(resp.content)
                        log.info("[CelesTrak Archive] %s: saved %d bytes", group, len(resp.content))
                time.sleep(2)
            except Exception as exc:
                log.error("[CelesTrak Archive] %s: %s", group, exc)


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill historical TLEs into local ML PostgreSQL")
    parser.add_argument(
        "--source",
        choices=["ethz", "spacetrack", "celestrak_archives", "all"],
        default="ethz",
    )
    parser.add_argument("--output", default="./tle_training_data", help="Download directory")
    parser.add_argument("--norad-ids", nargs="+", type=int, default=None,
                        help="Specific NORAD IDs to fetch (default: built-in training set)")
    parser.add_argument("--all-active", action="store_true",
                        help="Fetch all active satellites from ETH Zurich (no --norad-ids filter)")
    parser.add_argument("--days-back", type=int, default=365)
    parser.add_argument("--st-user", default=None, help="Space-Track username")
    parser.add_argument("--st-pass", default=None, help="Space-Track password")
    parser.add_argument("--batch-size", type=int, default=5000,
                        help="DB insert batch size (default: 5000)")
    parser.add_argument("--ingest-only", action="store_true",
                        help="Skip download, ingest files already in --output dir")
    parser.add_argument("--no-ingest", action="store_true",
                        help="Download only, do not ingest into DB")
    args = parser.parse_args()

    OUTPUT_DIR = Path(args.output)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    start_date = datetime.utcnow() - timedelta(days=args.days_back)

    TRAINING_SET = [25544, 20580, 25338, 28654, 33591, 38771, 27424, 25994,
                    44713, 44714, 44715, 44716, 44717, 22779, 28474, 32711,
                    39533, 43873, 26038, 29155, 36395, 41866, 43226]
    norad_ids = None if args.all_active else (args.norad_ids or TRAINING_SET)

    if not args.ingest_only:
        if args.source in ("ethz", "all"):
            log.info("=== ETH Zurich Satellite Database ===")
            download_ethz_satdb(
                norad_ids=norad_ids,
                start_date=start_date,
                output_dir=OUTPUT_DIR / "ethz_satdb",
            )

        if args.source in ("spacetrack", "all"):
            if args.st_user and args.st_pass:
                log.info("=== Space-Track GP_History ===")
                download_spacetrack_history(
                    username=args.st_user, password=args.st_pass,
                    norad_ids=norad_ids or TRAINING_SET,
                    start_date=start_date,
                    output_dir=OUTPUT_DIR / "spacetrack",
                )
            else:
                log.warning("Space-Track skipped (provide --st-user and --st-pass)")

        if args.source in ("celestrak_archives", "all"):
            log.info("=== CelesTrak Archives ===")
            download_celestrak_archives(output_dir=OUTPUT_DIR / "celestrak_archives")

    if not args.no_ingest:
        log.info("=== Ingesting into local PostgreSQL ===")
        ingest_files_to_local(OUTPUT_DIR, source_name="auto", batch_size=args.batch_size)

    log.info("Done. Training data in: %s", OUTPUT_DIR.absolute())
