"""
NOAA SWPC space weather fetcher.

- Kp index: real-time 1-minute data from SWPC JSON
- F10.7 flux: daily from CelesTrak SW-Last5Years.txt
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from db.client import upsert_space_weather, log_source_health
from fetchers.retries import http_retry

logger = logging.getLogger(__name__)

KP_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
F107_URL = "https://celestrak.org/SpaceData/SW-Last5Years.txt"
TIMEOUT = httpx.Timeout(20.0)

# CelesTrak SW-Last5Years.txt fixed-width column indices (0-based, space-split).
# Format: YYYY MM DD BSRN ND Kp×8(5-12) Sum(13) Ap×8(14-21) Ap(22) Cp(23) C9(24) ISN(25) F10.7obs(26) ...
_COL_YEAR  = 0
_COL_MONTH = 1
_COL_DAY   = 2
_COL_AP    = 22   # daily Ap geomagnetic index
_COL_F107  = 26   # F10.7 solar flux, observed
_MIN_COLS  = 30
_F107_MIN, _F107_MAX = 50.0, 500.0   # physically plausible range (solar flux units)
_AP_MIN,   _AP_MAX   = 0.0,  400.0   # Ap index range


@http_retry
async def _http_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    resp = await client.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


async def fetch_kp_index() -> None:
    """Fetch real-time 1-minute Kp index and upsert to space_weather."""
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            resp = await _http_get(client, KP_URL)
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
    Parse a data line from CelesTrak SW-Last5Years.txt (fixed-width, space-split).
    Validates parsed F10.7 and Ap values against physically plausible ranges.
    """
    line = line.strip()
    if not line or line.startswith("#") or line.startswith(":"):
        return None
    parts = line.split()
    if len(parts) < _MIN_COLS:
        return None
    try:
        year  = int(parts[_COL_YEAR])
        month = int(parts[_COL_MONTH])
        day   = int(parts[_COL_DAY])

        raw_f107 = parts[_COL_F107]
        f107 = float(raw_f107) if raw_f107 not in ("-1", "-1.0") else None
        if f107 is not None and not (_F107_MIN <= f107 <= _F107_MAX):
            logger.warning(
                "F10.7 value %.1f outside plausible range [%.0f, %.0f] — skipping",
                f107, _F107_MIN, _F107_MAX,
            )
            f107 = None

        raw_ap = parts[_COL_AP]
        ap = float(raw_ap) if raw_ap not in ("-1", "-1.0") else None
        if ap is not None and not (_AP_MIN <= ap <= _AP_MAX):
            logger.warning(
                "Ap value %.1f outside plausible range [%.0f, %.0f] — skipping",
                ap, _AP_MIN, _AP_MAX,
            )
            ap = None

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
            resp = await _http_get(client, F107_URL)
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
