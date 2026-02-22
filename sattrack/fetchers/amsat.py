"""
AMSAT TLE fetcher.

Fetches nasabare.txt (3-line TLE format) from amsat.org and upserts
to tle_history.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sgp4.api import Satrec, WGS84

from db.client import upsert_tle_batch, upsert_satellites, log_source_health
from fetchers.retries import http_retry
from quality.scorer import score_tle_quality

logger = logging.getLogger(__name__)

AMSAT_URL = "https://www.amsat.org/tle/current/nasabare.txt"
TIMEOUT = httpx.Timeout(30.0)


def _parse_three_line_tle(text: str) -> list[dict[str, Any]]:
    """
    Parse 3-line TLE text (name / line1 / line2) into normalised records.
    Returns list of (satellite_record, tle_record) dicts.
    """
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    records: list[dict[str, Any]] = []

    i = 0
    while i + 2 < len(lines):
        name_line = lines[i]
        l1 = lines[i + 1]
        l2 = lines[i + 2]

        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            i += 1
            continue

        try:
            sat = Satrec.twoline2rv(l1, l2)
            norad_id = sat.satnum
            if norad_id == 0:
                i += 3
                continue

            # Epoch from jdsatepoch
            epoch = datetime(1949, 12, 31, tzinfo=timezone.utc)
            epoch_dt = datetime.fromtimestamp(
                (sat.jdsatepoch - 2440587.5) * 86400.0, tz=timezone.utc
            )

            # Mean motion: rad/min → rev/day
            mean_motion_revday = sat.no * 1440.0 / (2 * 3.14159265358979)
            # Eccentricity
            ecc = sat.ecco
            inc_deg = sat.inclo * 180.0 / 3.14159265358979
            raan_deg = sat.nodeo * 180.0 / 3.14159265358979
            arg_p_deg = sat.argpo * 180.0 / 3.14159265358979
            mean_a_deg = sat.mo * 180.0 / 3.14159265358979
            bstar = sat.bstar

            orbit_class = _classify(mean_motion_revday, ecc)

            tle_record: dict[str, Any] = {
                "norad_id": norad_id,
                "epoch": epoch_dt.isoformat(),
                "source": "amsat",
                "tle_line1": l1,
                "tle_line2": l2,
                "inclination": inc_deg,
                "eccentricity": ecc,
                "raan": raan_deg,
                "arg_perigee": arg_p_deg,
                "mean_anomaly": mean_a_deg,
                "mean_motion": mean_motion_revday,
                "bstar": bstar,
                "orbit_class": orbit_class,
            }
            tle_record["quality_score"] = score_tle_quality(tle_record)
            del tle_record["orbit_class"]

            sat_record: dict[str, Any] = {
                "norad_id": norad_id,
                "name": name_line.strip(),
                "orbit_class": orbit_class,
                "source_flags": {"amsat": True},
            }

            records.append((sat_record, tle_record))
        except Exception as exc:
            logger.debug("amsat parse error at line %d: %s", i, exc)
        finally:
            i += 3

    return records


def _classify(mean_motion: float, eccentricity: float) -> str:
    if mean_motion > 11.25:
        return "LEO"
    if mean_motion > 2.0:
        return "MEO"
    if 0.9 < mean_motion <= 2.0:
        return "HEO" if eccentricity > 0.2 else "GEO"
    return "DEEP"


@http_retry
async def _http_get_text(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


async def fetch_amsat_elements() -> None:
    """Fetch AMSAT TLE file and upsert."""
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            text = await _http_get_text(client, AMSAT_URL)

        parsed = _parse_three_line_tle(text)
        elapsed = int((time.time() - t0) * 1000)

        if not parsed:
            log_source_health(source="amsat", status="empty",
                              response_time_ms=elapsed)
            return

        sat_records = [s for s, _ in parsed]
        tle_records = [t for _, t in parsed]

        upsert_satellites(sat_records)
        upsert_tle_batch(tle_records)

        freshest = max((t["epoch"] for t in tle_records), default=None)
        freshest_dt = datetime.fromisoformat(freshest) if freshest else None

        log_source_health(
            source="amsat",
            status="ok",
            count=len(tle_records),
            response_time_ms=elapsed,
            freshest_epoch=freshest_dt,
        )
        logger.info("amsat: upserted %d TLE records", len(tle_records))

    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        log_source_health(source="amsat", status="error", error=str(exc),
                          response_time_ms=elapsed)
        logger.error("fetch_amsat_elements failed: %s", exc)
