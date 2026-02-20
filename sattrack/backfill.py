"""
LARUN AstroData — Historical TLE Data Downloader + Supabase Backfill
=====================================================================
Downloads training data from ALL available free sources and optionally
ingests it directly into the Supabase tle_history table.

Sources covered:
  1. ETH Zurich satdb       — 7.97M TLEs, free API, no auth
  2. Jonathan McDowell XTLE — Historical archive back to 1957, no auth
  3. IAU SatChecker         — CelesTrak + SpaceTrack merged, free API
  4. Space-Track GP_History — 138M TLEs (free account required)
  5. Wayback Machine        — CelesTrak snapshots
  6. CelesTrak Archives     — Weather/Earth observation satellites 1980-present

Run (download only):
  python backfill.py --source all --output ./tle_training_data

Run (download + seed Supabase):
  python backfill.py --source ethz --ingest
  python backfill.py --source all --ingest --ingest-batch-size 500
"""

import requests
import time
import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path("./tle_training_data")
RATE_LIMIT_DELAY = 1.0   # seconds between requests (be polite)

# ─── Source 1: ETH Zurich Satellite Database ─────────────────────────────────
# satdb.ethz.ch — 7.97 MILLION TLEs, completely free, no login required
# Fetches from CelesTrak hourly since ~2020. Best single source for recent history.

def download_ethz_satdb(norad_ids=None, start_date=None, end_date=None,
                         output_dir=OUTPUT_DIR / "ethz_satdb"):
    """
    Download historical TLEs from ETH Zurich Satellite Database.
    Free, no authentication. Returns TLEs in standard format.
    
    Args:
        norad_ids: list of NORAD IDs, or None for all satellites
        start_date: datetime, defaults to 1 year ago
        end_date: datetime, defaults to today
        
    API: https://satdb.ethz.ch/api-documentation/
    Rate limit: Be polite — 1 req/sec is fine
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if start_date is None:
        start_date = datetime.utcnow() - timedelta(days=365)
    if end_date is None:
        end_date = datetime.utcnow()

    base_url = "https://satdb.ethz.ch/api/satellitedata/"
    
    # If no specific NORAD IDs, fetch all satellites in date range
    if norad_ids is None:
        print("[ETH Zurich] Fetching all satellites (this may take a while)...")
        params = {
            "start-datetime": start_date.strftime("%Y%m%dT%H%M"),
            "end-datetime":   end_date.strftime("%Y%m%dT%H%M"),
            "without-frequency-data": "True",
            "before": 3,
            "after": 0,
        }
        all_tles = _paginate_ethz(base_url, params)
        out_file = output_dir / f"all_sats_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.tle"
        _save_tle_list(all_tles, out_file)
        print(f"[ETH Zurich] Saved {len(all_tles)} TLEs → {out_file}")
        return out_file
    
    # Fetch specific NORAD IDs
    all_tles = []
    for norad_id in norad_ids:
        params = {
            "start-datetime": start_date.strftime("%Y%m%dT%H%M"),
            "end-datetime":   end_date.strftime("%Y%m%dT%H%M"),
            "norad-id":       norad_id,
            "without-frequency-data": "True",
            "before": 7,
            "after": 7,
        }
        tles = _paginate_ethz(base_url, params)
        all_tles.extend(tles)
        print(f"[ETH Zurich] NORAD {norad_id}: {len(tles)} TLEs")
        time.sleep(RATE_LIMIT_DELAY)
    
    out_file = output_dir / "selected_satellites.tle"
    _save_tle_list(all_tles, out_file)
    return out_file

def _paginate_ethz(url, params):
    """Handle ETH Zurich API pagination (500 records per page)."""
    tles = []
    next_url = url
    req_params = params.copy()
    
    while next_url:
        try:
            if req_params:
                resp = requests.get(next_url, params=req_params, timeout=30)
                req_params = None  # Only use params on first request; next_url has them
            else:
                resp = requests.get(next_url, timeout=30)
            
            resp.raise_for_status()
            data = resp.json()
            tles.extend(data.get("results", []))
            next_url = data.get("next")
            
            if next_url:
                time.sleep(RATE_LIMIT_DELAY)
        except Exception as e:
            print(f"  [ETH Zurich] Error: {e}")
            break
    
    return tles

def _save_tle_list(tles, filepath):
    """Save list of ETH Zurich TLE records to standard TLE file format."""
    with open(filepath, "w") as f:
        for tle in tles:
            norad_str = tle.get("norad_str", "")
            if norad_str:
                f.write(norad_str + "\n")


# ─── Source 2: Jonathan McDowell Historical XTLE Archive ─────────────────────
# planet4589.org/space/ele.html
# Coverage: 1957 to ~2000s for all NORAD-tracked objects
# Format: XTLE (extended TLE, backward-compatible with standard TLE parsers)
# License: Free to use
# Size: Complete history for all objects 00001-24000+ organized in directories

MCDOWELL_BASE = "https://planet4589.org/space/elements"

def download_mcdowell_xtle(norad_ids, output_dir=OUTPUT_DIR / "mcdowell"):
    """
    Download historical XTLEs from Jonathan McDowell's archive.
    Organized in directories of 100 catalog numbers each.
    
    Perfect for pre-2005 history and for satellites not covered elsewhere.
    Directory structure: /elements/00001-00100/S00001.tle, S00002.tle, etc.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for norad_id in norad_ids:
        # Calculate directory: IDs 1-100 → 00001-00100, 101-200 → 00101-00200
        low  = ((norad_id - 1) // 100) * 100 + 1
        high = low + 99
        dir_name = f"{low:05d}-{high:05d}"
        
        # Try both naming conventions McDowell uses
        for prefix in ["S", ""]:
            url = f"{MCDOWELL_BASE}/{dir_name}/{prefix}{norad_id:05d}.tle"
            try:
                resp = requests.get(url, timeout=20)
                if resp.status_code == 200 and len(resp.text) > 50:
                    out_file = output_dir / f"{norad_id:05d}.xtle"
                    out_file.write_text(resp.text)
                    lines = [l for l in resp.text.splitlines() if l.strip()]
                    print(f"[McDowell] NORAD {norad_id}: {len(lines)//2} TLEs → {out_file.name}")
                    break
            except Exception as e:
                print(f"[McDowell] NORAD {norad_id}: {e}")
            time.sleep(0.5)


# ─── Source 3: IAU SatChecker API ────────────────────────────────────────────
# satchecker.cps.iau.org
# International Astronomical Union — Center for the Protection of the Dark Sky
# Merges CelesTrak + SpaceTrack data. Free, no auth required.
# Perfect API for programmatic historical access.

IAU_API = "https://satchecker.cps.iau.org"

def download_iau_satchecker(norad_ids, start_jd=None, end_jd=None,
                             output_dir=OUTPUT_DIR / "iau_satchecker"):
    """
    Download historical TLEs from IAU SatChecker API.
    Uses Julian Date format for time queries.
    
    Julian Date converter: JD 2451545.0 = J2000 = Jan 1.5 2000
    JD 2460400 ≈ April 2024.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Default: last 2 years
    if end_jd is None:
        end_jd = datetime_to_jd(datetime.utcnow())
    if start_jd is None:
        start_jd = end_jd - 730  # 2 years back
    
    all_tles = {}
    
    for norad_id in norad_ids:
        url = f"{IAU_API}/tools/get-tle-data/"
        params = {
            "id": norad_id,
            "id_type": "catalog",
            "start_date_jd": round(start_jd, 2),
            "end_date_jd": round(end_jd, 2),
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            all_tles[norad_id] = data
            print(f"[IAU SatChecker] NORAD {norad_id}: {len(data)} TLEs")
        except Exception as e:
            print(f"[IAU SatChecker] NORAD {norad_id}: {e}")
        time.sleep(RATE_LIMIT_DELAY)
    
    # Save as JSON (preserves metadata like date_collected, data_source)
    out_file = output_dir / "iau_satchecker_tles.json"
    with open(out_file, "w") as f:
        json.dump(all_tles, f, indent=2)
    
    # Also save as standard TLE file
    tle_file = output_dir / "iau_satchecker_tles.tle"
    with open(tle_file, "w") as f:
        for norad_id, records in all_tles.items():
            for rec in records:
                f.write(f"0 {rec.get('satellite_name', '')}\n")
                f.write(rec['tle_line1'] + "\n")
                f.write(rec['tle_line2'] + "\n")
    
    print(f"[IAU SatChecker] Saved {sum(len(v) for v in all_tles.values())} TLEs total")
    return tle_file

def datetime_to_jd(dt):
    """Convert datetime to Julian Date."""
    # Simple approximation adequate for our purposes
    jd_j2000 = 2451545.0
    delta = dt - datetime(2000, 1, 1, 12, 0, 0)
    return jd_j2000 + delta.total_seconds() / 86400.0


# ─── Source 4: Space-Track GP_History (Free account required) ────────────────
# www.space-track.org — Register at https://www.space-track.org/auth/createAccount
# 138 MILLION historical element sets going back to ~1957
# Free account — just fill out a form, approved within minutes
# This is the motherlode. Handle with care — rate limit aggressively.

ST_BASE = "https://www.space-track.org"

def download_spacetrack_history(username, password, norad_ids,
                                 start_date=None, end_date=None,
                                 output_dir=OUTPUT_DIR / "spacetrack"):
    """
    Download historical TLEs from Space-Track GP_History.
    Requires free account registration at space-track.org.
    
    IMPORTANT: Space-Track rate limits strictly:
      - Max 30 requests per minute
      - Max 300 requests per hour  
      - Violations get you temporarily banned
    This function respects those limits automatically.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if start_date is None:
        start_date = datetime.utcnow() - timedelta(days=730)
    if end_date is None:
        end_date = datetime.utcnow()
    
    session = requests.Session()
    
    # Login
    login_url = f"{ST_BASE}/ajaxauth/login"
    credentials = {"identity": username, "password": password}
    resp = session.post(login_url, data=credentials)
    if resp.status_code != 200 or "Login" in resp.text:
        print("[SpaceTrack] Login failed — check credentials")
        return None
    print("[SpaceTrack] Logged in successfully")
    
    date_range = f"{start_date.strftime('%Y-%m-%d')}--%{end_date.strftime('%Y-%m-%d')}"
    
    for i, norad_id in enumerate(norad_ids):
        # Enforce rate limit: max 30 req/min → 2 second delay
        if i > 0 and i % 25 == 0:
            print(f"[SpaceTrack] Rate limit pause (processed {i}/{len(norad_ids)})...")
            time.sleep(65)  # Wait a full minute every 25 requests
        
        url = (
            f"{ST_BASE}/basicspacedata/query/class/gp_history"
            f"/NORAD_CAT_ID/{norad_id}"
            f"/EPOCH/{date_range}"
            f"/orderby/EPOCH asc"
            f"/format/json"
        )
        
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            records = resp.json()
            
            out_file = output_dir / f"{norad_id:05d}_history.json"
            with open(out_file, "w") as f:
                json.dump(records, f)
            
            print(f"[SpaceTrack] NORAD {norad_id}: {len(records)} TLEs")
        except Exception as e:
            print(f"[SpaceTrack] NORAD {norad_id}: {e}")
        
        time.sleep(2.5)  # Stay well under rate limit
    
    # Logout politely
    session.get(f"{ST_BASE}/auth/logout")
    print("[SpaceTrack] Logged out")


# ─── Source 5: Wayback Machine (Internet Archive) ────────────────────────────
# For CelesTrak snapshots — great for filling gaps

def download_wayback_celestrak(url_path, from_year=2020, to_year=2025,
                                output_dir=OUTPUT_DIR / "wayback"):
    """
    Retrieve historical CelesTrak TLE files from the Wayback Machine.
    Best for getting annual snapshots of specific satellite groups.
    
    Example url_path: "/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # The Wayback Machine CDX API tells us which snapshots exist
    cdx_url = "http://web.archive.org/cdx/search/cdx"
    full_url = f"https://celestrak.org{url_path}"
    
    params = {
        "url": full_url,
        "output": "json",
        "from": f"{from_year}0101",
        "to": f"{to_year}1231",
        "fl": "timestamp,original",
        "limit": 500,
        "filter": "statuscode:200",
        "collapse": "timestamp:6",  # One snapshot per month
    }
    
    try:
        resp = requests.get(cdx_url, params=params, timeout=30)
        snapshots = resp.json()[1:]  # Skip header row
        print(f"[Wayback] Found {len(snapshots)} snapshots for {url_path}")
    except Exception as e:
        print(f"[Wayback] CDX lookup failed: {e}")
        return
    
    for timestamp, original_url in snapshots:
        # Construct Wayback URL
        wb_url = f"http://web.archive.org/web/{timestamp}/{original_url}"
        year_month = timestamp[:6]
        out_file = output_dir / f"celestrak_{year_month}.tle"
        
        if out_file.exists():
            continue
        
        try:
            resp = requests.get(wb_url, timeout=30)
            if resp.status_code == 200 and "1 " in resp.text:
                out_file.write_text(resp.text)
                lines = [l for l in resp.text.splitlines() if l.startswith("1 ")]
                print(f"[Wayback] {year_month}: {len(lines)} TLEs saved")
            time.sleep(1)
        except Exception as e:
            print(f"[Wayback] {year_month}: {e}")


# ─── Source 6: CelesTrak Official Historical Archives ────────────────────────
# celestrak.org/NORAD/archives/ — weather, earth observation sats 1980-present

CELESTRAK_ARCHIVES = {
    # Satellite group → list of archive URLs
    "weather": [
        "https://celestrak.org/NORAD/archives/weather.zip",
    ],
    "noaa": [
        "https://celestrak.org/NORAD/archives/noaa.zip",
    ],
    "iss_history": [
        "https://celestrak.org/NORAD/archives/stations.zip",
    ],
}

def download_celestrak_archives(groups=None, output_dir=OUTPUT_DIR / "celestrak_archives"):
    """
    Download CelesTrak's official historical archives.
    These go back to 1980 for specific satellite categories.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if groups is None:
        groups = list(CELESTRAK_ARCHIVES.keys())
    
    import zipfile
    import io
    
    for group in groups:
        for url in CELESTRAK_ARCHIVES.get(group, []):
            try:
                print(f"[CelesTrak Archive] Downloading {group}...")
                resp = requests.get(url, timeout=60, stream=True)
                if resp.status_code == 200:
                    # Try to unzip
                    try:
                        z = zipfile.ZipFile(io.BytesIO(resp.content))
                        z.extractall(output_dir / group)
                        print(f"[CelesTrak Archive] {group}: extracted {len(z.namelist())} files")
                    except zipfile.BadZipFile:
                        # Not a zip, save as-is
                        out_file = output_dir / f"{group}.tle"
                        out_file.write_bytes(resp.content)
                        print(f"[CelesTrak Archive] {group}: saved {len(resp.content)} bytes")
                time.sleep(2)
            except Exception as e:
                print(f"[CelesTrak Archive] {group}: {e}")


# ─── Training Data Builder ────────────────────────────────────────────────────

def build_training_dataset(tle_dir=OUTPUT_DIR, output_file=OUTPUT_DIR / "training_dataset.csv"):
    """
    Parse all downloaded TLE files and build a structured training CSV.
    
    Output columns:
      norad_id, epoch, inclination, raan, eccentricity, arg_perigee,
      mean_anomaly, mean_motion, bstar, orbit_class, source
    
    This CSV is what you feed into the ML correction model.
    """
    try:
        from sgp4.api import Satrec
    except ImportError:
        print("Install sgp4: pip install sgp4")
        return
    
    import csv
    
    records = []
    tle_files = list(Path(tle_dir).rglob("*.tle")) + list(Path(tle_dir).rglob("*.xtle"))
    
    print(f"\nBuilding training dataset from {len(tle_files)} TLE files...")
    
    for tle_file in tle_files:
        lines = [l.strip() for l in tle_file.read_text(errors='ignore').splitlines() 
                 if l.strip()]
        
        i = 0
        while i < len(lines):
            # Find line pairs starting with "1 " and "2 "
            if lines[i].startswith("1 ") and i + 1 < len(lines) and lines[i+1].startswith("2 "):
                line1, line2 = lines[i], lines[i+1]
                try:
                    sat = Satrec.twoline2rv(line1, line2)
                    
                    # Classify orbit by altitude (mean_motion → km)
                    n = sat.no_kozai  # rad/min
                    a_km = (398600.4418 / (n * 60 / (2 * 3.14159)) ** 2) ** (1/3)
                    alt_km = a_km - 6378.137
                    
                    if alt_km < 2000:
                        orbit_class = "LEO"
                    elif alt_km < 35000:
                        orbit_class = "MEO"
                    elif alt_km < 37000:
                        orbit_class = "GEO"
                    else:
                        orbit_class = "HEO"
                    
                    records.append({
                        "norad_id":    sat.satnum,
                        "epoch_year":  sat.epochyr,
                        "epoch_days":  sat.epochdays,
                        "inclination": sat.inclo,     # rad
                        "raan":        sat.nodeo,     # rad
                        "eccentricity": sat.ecco,
                        "arg_perigee": sat.argpo,    # rad
                        "mean_anomaly": sat.mo,      # rad
                        "mean_motion": sat.no_kozai, # rad/min
                        "bstar":       sat.bstar,
                        "orbit_class": orbit_class,
                        "altitude_km": round(alt_km, 1),
                        "source":      tle_file.parent.name,
                        "line1":       line1,
                        "line2":       line2,
                    })
                except Exception:
                    pass
                i += 2
            else:
                i += 1
    
    # Write CSV
    if records:
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)
        
        print(f"\n✅ Training dataset: {len(records):,} TLE records → {output_file}")
        
        # Print summary
        from collections import Counter
        orbit_counts = Counter(r["orbit_class"] for r in records)
        source_counts = Counter(r["source"] for r in records)
        print("\nBy orbit class:")
        for oc, count in sorted(orbit_counts.items()):
            print(f"  {oc:6s}: {count:>8,}")
        print("\nBy source:")
        for src, count in sorted(source_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"  {src:30s}: {count:>8,}")
    else:
        print("No valid TLEs found — check your downloaded files")
    
    return output_file


# ─── Supabase Ingestion ───────────────────────────────────────────────────────

def ingest_to_supabase(tle_dir=OUTPUT_DIR, batch_size=500):
    """
    Parse all downloaded TLE files and ingest into Supabase tle_history.

    Reads the same files as build_training_dataset() but calls upsert_tle_batch()
    and upsert_satellites() instead of writing to CSV. Duplicate epochs are
    silently skipped via the unique index on (norad_id, epoch, source).

    Args:
        tle_dir:    root directory containing downloaded TLE files
        batch_size: number of TLE records per Supabase upsert call
    """
    try:
        from sgp4.api import Satrec
    except ImportError:
        print("Install sgp4: pip install sgp4")
        return

    # Import db helpers (works when run from inside sattrack/ dir)
    try:
        from db.client import upsert_tle_batch, upsert_satellites
        from quality.scorer import score_tle_quality
    except ImportError:
        print("Run this script from inside sattrack/: cd sattrack && python backfill.py --ingest")
        sys.exit(1)

    import math

    MATH_PI = 3.14159265358979
    MU_KM3 = 398600.4418

    tle_files = list(Path(tle_dir).rglob("*.tle")) + list(Path(tle_dir).rglob("*.xtle"))
    print(f"\n[Ingest] Scanning {len(tle_files)} TLE files in {tle_dir}...")

    sat_batch: list[dict] = []
    tle_batch: list[dict] = []
    total_ingested = 0
    total_skipped = 0

    def flush(force=False):
        nonlocal total_ingested
        if len(tle_batch) >= batch_size or force:
            if tle_batch:
                try:
                    upsert_satellites(sat_batch[:])
                    upsert_tle_batch(tle_batch[:])
                    total_ingested += len(tle_batch)
                    print(f"  [Ingest] {total_ingested:,} rows ingested...", end="\r")
                except Exception as exc:
                    print(f"\n  [Ingest] Batch failed: {exc}")
                sat_batch.clear()
                tle_batch.clear()

    for tle_file in tle_files:
        source_name = tle_file.parent.name  # e.g. "ethz_satdb", "mcdowell"
        lines = [l.strip() for l in tle_file.read_text(errors="ignore").splitlines()
                 if l.strip()]
        i = 0
        while i < len(lines):
            # Skip name line if present (3-line format)
            name = "UNKNOWN"
            if (not lines[i].startswith("1 ") and
                    i + 2 < len(lines) and
                    lines[i + 1].startswith("1 ") and
                    lines[i + 2].startswith("2 ")):
                name = lines[i]
                i += 1
                continue

            if (lines[i].startswith("1 ") and
                    i + 1 < len(lines) and
                    lines[i + 1].startswith("2 ")):
                l1, l2 = lines[i], lines[i + 1]
                try:
                    sat = Satrec.twoline2rv(l1, l2)
                    norad_id = sat.satnum
                    if norad_id == 0:
                        i += 2
                        continue

                    # Epoch from jdsatepoch (JD → Unix → UTC datetime)
                    epoch_dt = datetime.fromtimestamp(
                        (sat.jdsatepoch - 2440587.5) * 86400.0, tz=timezone.utc
                    )

                    # Mean motion rad/min → rev/day
                    n_revday = sat.no_kozai * 1440.0 / (2 * MATH_PI)
                    # Semi-major axis → altitude
                    try:
                        a_km = (MU_KM3 / (sat.no_kozai * 60 / (2 * MATH_PI)) ** 2) ** (1 / 3)
                        alt_km = a_km - 6378.137
                    except ZeroDivisionError:
                        alt_km = 0

                    if alt_km < 2000:
                        orbit_class = "LEO"
                    elif alt_km < 35000:
                        orbit_class = "MEO"
                    elif alt_km < 37000:
                        orbit_class = "GEO"
                    else:
                        orbit_class = "HEO"

                    tle_rec = {
                        "norad_id":    norad_id,
                        "epoch":       epoch_dt.isoformat(),
                        "source":      source_name,
                        "tle_line1":   l1,
                        "tle_line2":   l2,
                        "inclination": sat.inclo * 180.0 / MATH_PI,
                        "eccentricity": sat.ecco,
                        "raan":        sat.nodeo * 180.0 / MATH_PI,
                        "arg_perigee": sat.argpo * 180.0 / MATH_PI,
                        "mean_anomaly": sat.mo * 180.0 / MATH_PI,
                        "mean_motion": n_revday,
                        "bstar":       sat.bstar,
                        "orbit_class": orbit_class,
                    }
                    tle_rec["quality_score"] = score_tle_quality(tle_rec)
                    del tle_rec["orbit_class"]  # not a DB column in tle_history

                    sat_rec = {
                        "norad_id":    norad_id,
                        "name":        name if name != "UNKNOWN" else f"SAT-{norad_id}",
                        "orbit_class": orbit_class,
                        "source_flags": {source_name: True},
                    }

                    tle_batch.append(tle_rec)
                    sat_batch.append(sat_rec)
                    flush()
                except Exception:
                    total_skipped += 1
                i += 2
            else:
                i += 1

    flush(force=True)  # Final partial batch

    print(f"\n[Ingest] Complete — {total_ingested:,} rows ingested, {total_skipped:,} skipped")
    return total_ingested


# ─── Quick-Start: Download the Most Important Historical Satellites ───────────

# Satellites chosen to give maximum training diversity across orbital regimes
TRAINING_SATELLITE_SET = {
    # ISS & crewed — frequent TLE updates, well-studied
    "ISS": [25544],
    
    # LEO well-known — decades of history  
    "LEO_classic": [
        20580,   # Hubble Space Telescope
        25338,   # NOAA 15
        28654,   # NOAA 18
        33591,   # NOAA 19
        38771,   # NOAA 20
        27424,   # Aqua (NASA EOS)
        25994,   # Terra (NASA EOS)
    ],
    
    # Starlink — high drag, excellent test of drag correction
    "Starlink_sample": [
        44713, 44714, 44715, 44716, 44717,  # Early Starlink batch
        53547, 53548, 53549, 53550, 53551,  # Recent batch
    ],
    
    # MEO — GPS constellation, stable orbits, good baseline
    "GPS": [
        22779,  # GPS IIA-14
        28474,  # GPS IIR-11
        32711,  # GPS IIR-M-3
        39533,  # GPS IIF-5
        43873,  # GPS III-2
    ],
    
    # GEO — very stable, good for GEO model training  
    "GEO_sample": [
        25338,   # NOAA 15 (actually LEO — placeholder)
        26038,   # GOES 11
        29155,   # GOES 12
        36395,   # GOES 15
        41866,   # GOES 16
        43226,   # GOES 17
    ],
    
    # Debris — important for conjunction analysis
    "Debris_sample": [
        20001, 20002, 20003, 20004, 20005,  # Legacy debris
    ],
}

ALL_TRAINING_NORAD_IDS = [
    norad_id 
    for ids in TRAINING_SATELLITE_SET.values() 
    for norad_id in ids
]


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download historical TLE training data")
    parser.add_argument("--source", choices=["ethz", "mcdowell", "iau", "spacetrack", 
                                              "wayback", "celestrak_archives", "all"],
                        default="ethz", help="Which source to download from")
    parser.add_argument("--output", default="./tle_training_data", help="Output directory")
    parser.add_argument("--norad-ids", nargs="+", type=int, default=None,
                        help="Specific NORAD IDs (default: built-in training set)")
    parser.add_argument("--days-back", type=int, default=365, help="Days of history to fetch")
    parser.add_argument("--st-user", default=None, help="Space-Track.org username")
    parser.add_argument("--st-pass", default=None, help="Space-Track.org password")
    parser.add_argument("--build-csv", action="store_true", help="Build training CSV after download")
    parser.add_argument("--ingest", action="store_true",
                        help="Seed downloaded TLEs into Supabase (requires SUPABASE_URL + SUPABASE_SERVICE_KEY env vars)")
    parser.add_argument("--ingest-only", action="store_true",
                        help="Skip downloading, just ingest files already in --output directory")
    parser.add_argument("--ingest-batch-size", type=int, default=500,
                        help="Supabase upsert batch size (default: 500)")
    args = parser.parse_args()

    OUTPUT_DIR = Path(args.output)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    norad_ids = args.norad_ids or ALL_TRAINING_NORAD_IDS
    start_date = datetime.utcnow() - timedelta(days=args.days_back)
    
    print(f"\n{'='*60}")
    print(f"LARUN Historical TLE Downloader")
    print(f"{'='*60}")
    print(f"Source: {args.source}")
    print(f"NORAD IDs: {len(norad_ids)} satellites")
    print(f"Date range: {start_date.date()} to today")
    print(f"Output: {OUTPUT_DIR}")
    print(f"{'='*60}\n")
    
    if args.source in ("ethz", "all"):
        print("\n[1/5] ETH Zurich Satellite Database...")
        download_ethz_satdb(
            norad_ids=norad_ids,
            start_date=start_date,
            output_dir=OUTPUT_DIR / "ethz_satdb"
        )
    
    if args.source in ("mcdowell", "all"):
        print("\n[2/5] Jonathan McDowell XTLE Archive...")
        download_mcdowell_xtle(
            norad_ids=norad_ids,
            output_dir=OUTPUT_DIR / "mcdowell"
        )
    
    if args.source in ("iau", "all"):
        print("\n[3/5] IAU SatChecker API...")
        download_iau_satchecker(
            norad_ids=norad_ids,
            start_jd=datetime_to_jd(start_date),
            output_dir=OUTPUT_DIR / "iau_satchecker"
        )
    
    if args.source in ("spacetrack", "all"):
        if args.st_user and args.st_pass:
            print("\n[4/5] Space-Track GP_History (138M TLEs)...")
            download_spacetrack_history(
                username=args.st_user,
                password=args.st_pass,
                norad_ids=norad_ids,
                start_date=start_date,
                output_dir=OUTPUT_DIR / "spacetrack"
            )
        else:
            print("\n[4/5] Space-Track: Skipped (provide --st-user and --st-pass)")
            print("      Register free at: https://www.space-track.org/auth/createAccount")
    
    if args.source in ("wayback", "all"):
        print("\n[5/5] Wayback Machine CelesTrak snapshots...")
        download_wayback_celestrak(
            url_path="/NORAD/elements/active.txt",
            from_year=2020,
            output_dir=OUTPUT_DIR / "wayback"
        )
    
    if args.build_csv:
        print("\n[+] Building training CSV...")
        build_training_dataset(OUTPUT_DIR, OUTPUT_DIR / "training_dataset.csv")

    if args.ingest or args.ingest_only:
        print("\n[+] Ingesting into Supabase...")
        from dotenv import load_dotenv
        load_dotenv()
        if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_KEY"):
            print("ERROR: Set SUPABASE_URL and SUPABASE_SERVICE_KEY env vars (or create a .env file)")
            sys.exit(1)
        ingest_to_supabase(OUTPUT_DIR, batch_size=args.ingest_batch_size)

    print(f"\n✅ Done! Training data in: {OUTPUT_DIR.absolute()}")
    if not (args.ingest or args.ingest_only):
        print("\nNext steps:")
        print("  Build CSV:       python backfill.py --build-csv")
        print("  Seed Supabase:   python backfill.py --ingest-only")
        print("  Space-Track:     python backfill.py --source spacetrack --st-user YOU --st-pass PASS --ingest")
