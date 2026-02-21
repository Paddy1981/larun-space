"""
CelesTrak GP + Supplemental TLE fetcher.

Fetches all 18 GP groups concurrently (OMM JSON format) and the
high-frequency supplemental groups (Starlink, OneWeb).
Deduplicates by keeping the latest epoch per norad_id.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sgp4.api import Satrec

from db.client import upsert_tle_batch, upsert_satellites, log_source_health, mark_satellites_active
from quality.scorer import score_tle_quality

logger = logging.getLogger(__name__)

# Correct CelesTrak GP base path (SATCAT/GP.php returns 404 — correct path is NORAD/elements/gp.php)
_GP_BASE = "https://celestrak.org/NORAD/elements/gp.php"

# 20 CelesTrak GP groups (OMM JSON)
GP_GROUPS: list[dict[str, str]] = [
    {"name": "stations",      "url": f"{_GP_BASE}?GROUP=stations&FORMAT=JSON"},
    {"name": "active",        "url": f"{_GP_BASE}?GROUP=active&FORMAT=JSON"},
    {"name": "analyst",       "url": f"{_GP_BASE}?GROUP=analyst&FORMAT=JSON"},
    {"name": "last-30-days",  "url": f"{_GP_BASE}?GROUP=last-30-days&FORMAT=JSON"},
    {"name": "weather",       "url": f"{_GP_BASE}?GROUP=weather&FORMAT=JSON"},
    {"name": "noaa",          "url": f"{_GP_BASE}?GROUP=noaa&FORMAT=JSON"},
    {"name": "goes",          "url": f"{_GP_BASE}?GROUP=goes&FORMAT=JSON"},
    {"name": "resource",      "url": f"{_GP_BASE}?GROUP=resource&FORMAT=JSON"},
    {"name": "sarsat",        "url": f"{_GP_BASE}?GROUP=sarsat&FORMAT=JSON"},
    {"name": "dmc",           "url": f"{_GP_BASE}?GROUP=dmc&FORMAT=JSON"},
    {"name": "tdrss",         "url": f"{_GP_BASE}?GROUP=tdrss&FORMAT=JSON"},
    {"name": "argos",         "url": f"{_GP_BASE}?GROUP=argos&FORMAT=JSON"},
    {"name": "planet",        "url": f"{_GP_BASE}?GROUP=planet&FORMAT=JSON"},
    {"name": "spire",         "url": f"{_GP_BASE}?GROUP=spire&FORMAT=JSON"},
    {"name": "gnss",          "url": f"{_GP_BASE}?GROUP=gnss&FORMAT=JSON"},
    {"name": "galileo",       "url": f"{_GP_BASE}?GROUP=galileo&FORMAT=JSON"},
    {"name": "iridium",       "url": f"{_GP_BASE}?GROUP=iridium&FORMAT=JSON"},
    {"name": "iridium-NEXT",  "url": f"{_GP_BASE}?GROUP=iridium-NEXT&FORMAT=JSON"},
    {"name": "starlink",      "url": f"{_GP_BASE}?GROUP=starlink&FORMAT=JSON"},
    {"name": "oneweb",        "url": f"{_GP_BASE}?GROUP=oneweb&FORMAT=JSON"},
]

# High-frequency supplemental groups (Starlink/OneWeb fresh data)
SUPPLEMENTAL_GROUPS: list[dict[str, str]] = [
    {"name": "starlink-supp", "url": f"{_GP_BASE}?SPECIAL=starlink&FORMAT=JSON"},
    {"name": "oneweb-supp",   "url": f"{_GP_BASE}?SPECIAL=oneweb&FORMAT=JSON"},
]

TIMEOUT = httpx.Timeout(30.0)


def _parse_omm_epoch(epoch_str: str) -> datetime | None:
    """Parse CelesTrak OMM epoch string to datetime."""
    if not epoch_str:
        return None
    try:
        # Format: "2024-001.12345678" or ISO
        if "T" in epoch_str or "-" in epoch_str[:8]:
            dt = datetime.fromisoformat(epoch_str.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(epoch_str, "%Y-%j.%f")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _classify_orbit(mean_motion: float, eccentricity: float) -> str:
    """Rough orbit classification from mean motion (rev/day) and eccentricity."""
    if mean_motion > 11.25:
        return "LEO"
    if mean_motion > 2.0:
        return "MEO"
    if 0.9 < mean_motion <= 2.0:
        if eccentricity > 0.2:
            return "HEO"
        return "GEO"
    return "DEEP"


def _omm_to_records(omm: dict[str, Any], source: str) -> tuple[dict, dict] | None:
    """
    Convert a single OMM JSON object to (satellite_record, tle_record).
    Returns None if the record is invalid.
    """
    try:
        norad_id = int(omm.get("NORAD_CAT_ID", 0))
        if norad_id == 0:
            return None

        name = (omm.get("OBJECT_NAME") or "UNKNOWN").strip()
        cospar_id = omm.get("OBJECT_ID") or None
        mean_motion = float(omm.get("MEAN_MOTION", 0))
        eccentricity = float(omm.get("ECCENTRICITY", 0))
        inclination = float(omm.get("INCLINATION", 0))
        raan = float(omm.get("RA_OF_ASC_NODE", 0))
        arg_perigee = float(omm.get("ARG_OF_PERICENTER", 0))
        mean_anomaly = float(omm.get("MEAN_ANOMALY", 0))
        bstar = float(omm.get("BSTAR", 0))
        epoch_str = omm.get("EPOCH", "")

        epoch = _parse_omm_epoch(epoch_str)
        if epoch is None:
            return None

        orbit_class = _classify_orbit(mean_motion, eccentricity)
        object_type = omm.get("OBJECT_TYPE", "UNKNOWN")

        # Reconstruct TLE lines using sgp4
        sat = Satrec()
        sat.sgp4init(
            2,  # WGS84
            "i",  # improved mode
            norad_id,
            (epoch - datetime(1949, 12, 31, tzinfo=timezone.utc)).total_seconds() / 86400.0,
            bstar,
            0.0,  # ndot
            0.0,  # nddot
            eccentricity,
            float(omm.get("ARG_OF_PERICENTER", 0)) * 3.14159265358979 / 180,
            inclination * 3.14159265358979 / 180,
            mean_anomaly * 3.14159265358979 / 180,
            mean_motion * 2 * 3.14159265358979 / 1440.0,
            raan * 3.14159265358979 / 180,
        )

        # Use raw TLE lines from OMM if available, else reconstruct
        tle1 = omm.get("TLE_LINE1") or _build_tle_line1(omm, norad_id, epoch, bstar)
        tle2 = omm.get("TLE_LINE2") or _build_tle_line2(omm, norad_id, inclination, raan,
                                                          eccentricity, arg_perigee,
                                                          mean_anomaly, mean_motion)

        sat_record: dict[str, Any] = {
            "norad_id": norad_id,
            "cospar_id": cospar_id,
            "name": name,
            "orbit_class": orbit_class,
            "object_type": object_type,
            "source_flags": {source: True},
        }

        tle_record: dict[str, Any] = {
            "norad_id": norad_id,
            "epoch": epoch.isoformat(),
            "source": source,
            "tle_line1": tle1,
            "tle_line2": tle2,
            "inclination": inclination,
            "eccentricity": eccentricity,
            "raan": raan,
            "arg_perigee": arg_perigee,
            "mean_anomaly": mean_anomaly,
            "mean_motion": mean_motion,
            "bstar": bstar,
            "orbit_class": orbit_class,
        }
        tle_record["quality_score"] = score_tle_quality(tle_record)
        # Remove helper field not in DB schema
        del tle_record["orbit_class"]

        return sat_record, tle_record
    except Exception as exc:
        logger.debug("_omm_to_records failed for %s: %s", omm.get("NORAD_CAT_ID"), exc)
        return None


def _build_tle_line1(omm: dict, norad_id: int, epoch: datetime, bstar: float) -> str:
    """Build TLE line 1 string from OMM fields."""
    year = epoch.year % 100
    day_of_year = epoch.timetuple().tm_yday
    day_frac = (epoch.hour * 3600 + epoch.minute * 60 + epoch.second) / 86400.0
    epoch_field = f"{year:02d}{day_of_year:03d}.{day_frac:.8f}"[3:]
    epoch_field = f"{year:02d}{day_of_year + day_frac:.8f}"
    bstar_str = f"{bstar:.4e}".replace("e-0", "-").replace("e+0", "+").replace("e", "")

    line1 = (
        f"1 {norad_id:05d}U "
        f"{omm.get('OBJECT_ID', ''):8s} "
        f"{year:02d}{day_of_year:03d}.{int(day_frac * 100000000):08d} "
        f" .00000000  00000-0 {bstar_str:8s} 0  9990"
    )
    return line1[:69]


def _build_tle_line2(omm: dict, norad_id: int, inc: float, raan: float,
                      ecc: float, arg_p: float, mean_a: float, mean_m: float) -> str:
    """Build TLE line 2 string from Keplerian elements."""
    ecc_str = f"{ecc:.7f}"[2:]  # strip "0."
    line2 = (
        f"2 {norad_id:05d} "
        f"{inc:8.4f} "
        f"{raan:8.4f} "
        f"{ecc_str} "
        f"{arg_p:8.4f} "
        f"{mean_a:8.4f} "
        f"{mean_m:11.8f}00000 9990"
    )
    return line2[:69]


async def _fetch_group(
    client: httpx.AsyncClient,
    group: dict[str, str],
) -> list[dict[str, Any]]:
    """Fetch a single CelesTrak group and return list of OMM dicts."""
    try:
        resp = await client.get(group["url"], timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as exc:
        logger.warning("fetch_group %s failed: %s", group["name"], exc)
        return []


def _deduplicate(records: list[tuple[dict, dict]]) -> list[tuple[dict, dict]]:
    """Keep only the latest epoch per norad_id."""
    best: dict[int, tuple[dict, dict]] = {}
    for sat_rec, tle_rec in records:
        nid = sat_rec["norad_id"]
        if nid not in best:
            best[nid] = (sat_rec, tle_rec)
        else:
            existing_epoch = best[nid][1]["epoch"]
            if tle_rec["epoch"] > existing_epoch:
                best[nid] = (sat_rec, tle_rec)
    return list(best.values())


async def fetch_celestrak_gp() -> None:
    """Fetch all 18 CelesTrak GP groups, dedup, and upsert."""
    t0 = time.time()
    async with httpx.AsyncClient() as client:
        tasks = [_fetch_group(client, g) for g in GP_GROUPS]
        results = await asyncio.gather(*tasks)

    raw: list[tuple[dict, dict]] = []
    for group, omm_list in zip(GP_GROUPS, results):
        for omm in omm_list:
            parsed = _omm_to_records(omm, source="celestrak")
            if parsed:
                raw.append(parsed)

    deduped = _deduplicate(raw)
    sat_records = [s for s, _ in deduped]
    tle_records = [t for _, t in deduped]

    elapsed = int((time.time() - t0) * 1000)

    try:
        upsert_satellites(sat_records)
        upsert_tle_batch(tle_records)
        # Promote all freshly ingested satellites to active status.
        # CelesTrak GP only lists operationally active objects, so every satellite
        # we received from it should be marked active (unless already 'decayed').
        norad_ids = [s["norad_id"] for s in sat_records]
        promoted = mark_satellites_active(norad_ids)
        freshest = max((t["epoch"] for t in tle_records), default=None)
        freshest_dt = datetime.fromisoformat(freshest) if freshest else None
        log_source_health(
            source="celestrak",
            status="ok",
            count=len(tle_records),
            response_time_ms=elapsed,
            freshest_epoch=freshest_dt,
        )
        logger.info("celestrak GP: upserted %d satellites, promoted %d to active",
                    len(tle_records), promoted)
    except Exception as exc:
        log_source_health(source="celestrak", status="error", error=str(exc),
                          response_time_ms=elapsed)
        raise


async def fetch_celestrak_supplemental() -> None:
    """Fetch CelesTrak supplemental groups (Starlink/OneWeb) for fresh data."""
    t0 = time.time()
    async with httpx.AsyncClient() as client:
        tasks = [_fetch_group(client, g) for g in SUPPLEMENTAL_GROUPS]
        results = await asyncio.gather(*tasks)

    raw: list[tuple[dict, dict]] = []
    for group, omm_list in zip(SUPPLEMENTAL_GROUPS, results):
        for omm in omm_list:
            parsed = _omm_to_records(omm, source="supplemental")
            if parsed:
                raw.append(parsed)

    deduped = _deduplicate(raw)
    sat_records = [s for s, _ in deduped]
    tle_records = [t for _, t in deduped]
    elapsed = int((time.time() - t0) * 1000)

    try:
        upsert_satellites(sat_records)
        upsert_tle_batch(tle_records)
        norad_ids = [s["norad_id"] for s in sat_records]
        mark_satellites_active(norad_ids)
        freshest = max((t["epoch"] for t in tle_records), default=None)
        freshest_dt = datetime.fromisoformat(freshest) if freshest else None
        log_source_health(
            source="supplemental",
            status="ok",
            count=len(tle_records),
            response_time_ms=elapsed,
            freshest_epoch=freshest_dt,
        )
        logger.info("celestrak supplemental: upserted %d records", len(tle_records))
    except Exception as exc:
        log_source_health(source="supplemental", status="error", error=str(exc),
                          response_time_ms=elapsed)
        raise
