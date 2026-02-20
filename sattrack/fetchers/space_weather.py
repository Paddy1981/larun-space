"""
NOAA SWPC space weather fetcher.

- Kp index: real-time 1-minute data from SWPC JSON
- F10.7 flux: daily from CelesTrak SW-Last5Years.txt
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from db.client import upsert_space_weather, log_source_health

logger = logging.getLogger(__name__)

KP_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
F107_URL = "https://celestrak.org/SpaceData/SW-Last5Years.txt"
TIMEOUT = httpx.Timeout(20.0)


async def fetch_kp_index() -> None:
    """Fetch real-time 1-minute Kp index and upsert to space_weather."""
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(KP_URL, timeout=TIMEOUT)
            resp.raise_for_status()
            data: list[dict] = resp.json()

        records: list[dict[str, Any]] = []
        for entry in data:
            try:
                observed_at = datetime.fromisoformat(
                    entry["time_tag"].replace("Z", "+00:00")
                )
                kp = entry.get("kp_index")
                if kp is None:
                    continue
                records.append({
                    "observed_at": observed_at.isoformat(),
                    "kp_index": float(kp),
                    "source": "noaa_swpc",
                })
            except Exception as exc:
                logger.debug("kp_index parse error: %s", exc)
                continue

        elapsed = int((time.time() - t0) * 1000)
        if records:
            upsert_space_weather(records)
            freshest = max(r["observed_at"] for r in records)
            log_source_health(
                source="swpc_kp",
                status="ok",
                count=len(records),
                response_time_ms=elapsed,
                freshest_epoch=datetime.fromisoformat(freshest),
            )
            logger.info("kp_index: upserted %d records", len(records))
        else:
            log_source_health(source="swpc_kp", status="empty",
                              response_time_ms=elapsed)
    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        log_source_health(source="swpc_kp", status="error", error=str(exc),
                          response_time_ms=elapsed)
        logger.error("fetch_kp_index failed: %s", exc)


def _parse_f107_line(line: str) -> dict[str, Any] | None:
    """
    Parse a data line from CelesTrak SW-Last5Years.txt (fixed-width).
    Columns: YYYY MM DD ... F10.7_OBS ... AP_AVG ...
    """
    line = line.strip()
    if not line or line.startswith("#") or line.startswith(":"):
        return None
    parts = line.split()
    if len(parts) < 30:
        return None
    try:
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
        # F10.7 observed is at column index 26 in CelesTrak format
        f107 = float(parts[26]) if parts[26] != "-1.0" else None
        # Ap index (daily) is at column index 22
        ap = float(parts[22]) if parts[22] not in ("-1", "-1.0") else None
        if year < 1957:
            return None
        observed_at = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
        return {
            "observed_at": observed_at.isoformat(),
            "f107_flux": f107,
            "ap_index": ap,
            "source": "celestrak_sw",
        }
    except Exception:
        return None


async def fetch_f107_flux() -> None:
    """Fetch daily F10.7 solar flux from CelesTrak and upsert."""
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(F107_URL, timeout=TIMEOUT)
            resp.raise_for_status()
            text = resp.text

        records: list[dict[str, Any]] = []
        in_observed = False
        for line in text.splitlines():
            if line.strip() == "BEGIN OBSERVED":
                in_observed = True
                continue
            if line.strip() == "END OBSERVED":
                break  # Stop — ignore DAILY_PREDICTED and MONTHLY_PREDICTED
            if not in_observed:
                continue
            parsed = _parse_f107_line(line)
            if parsed:
                records.append(parsed)

        elapsed = int((time.time() - t0) * 1000)
        if records:
            upsert_space_weather(records)
            freshest = max(r["observed_at"] for r in records)
            log_source_health(
                source="celestrak_f107",
                status="ok",
                count=len(records),
                response_time_ms=elapsed,
                freshest_epoch=datetime.fromisoformat(freshest),
            )
            logger.info("f107_flux: upserted %d records", len(records))
        else:
            log_source_health(source="celestrak_f107", status="empty",
                              response_time_ms=elapsed)
    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        log_source_health(source="celestrak_f107", status="error", error=str(exc),
                          response_time_ms=elapsed)
        logger.error("fetch_f107_flux failed: %s", exc)
