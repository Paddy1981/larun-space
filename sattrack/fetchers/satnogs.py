"""
SatNOGS satellite metadata fetcher.

Fetches the full paginated satellite list from the SatNOGS DB API
and merges operator, country, and status metadata into the satellites table.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from db.client import upsert_satellites, log_source_health

logger = logging.getLogger(__name__)

SATNOGS_API = "https://db.satnogs.org/api/satellites/"
PAGE_SIZE = 100
TIMEOUT = httpx.Timeout(30.0)


def _status_map(satnogs_status: str) -> str:
    """Normalise SatNOGS status string."""
    s = (satnogs_status or "").lower()
    if s in ("alive",):
        return "active"
    if s in ("re-entered", "decayed"):
        return "decayed"
    if s in ("dead",):
        return "inactive"
    return "unknown"


async def fetch_satnogs_metadata() -> None:
    """Fetch all SatNOGS satellites and upsert metadata."""
    t0 = time.time()
    records: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient() as client:
            url: str | None = f"{SATNOGS_API}?format=json&page_size={PAGE_SIZE}"
            while url:
                resp = await client.get(url, timeout=TIMEOUT)
                resp.raise_for_status()
                data = resp.json()

                # SatNOGS uses DRF pagination: {"count": N, "next": url, "results": [...]}
                if isinstance(data, dict):
                    items = data.get("results", [])
                    url = data.get("next")
                else:
                    items = data
                    url = None

                for sat in items:
                    norad_id = sat.get("norad_cat_id")
                    if not norad_id:
                        continue
                    try:
                        norad_id = int(norad_id)
                    except (TypeError, ValueError):
                        continue

                    records.append({
                        "norad_id": norad_id,
                        "name": sat.get("name") or "UNKNOWN",
                        "cospar_id": sat.get("intl_designator") or None,
                        "status": _status_map(sat.get("status", "")),
                        "operator": sat.get("operator") or None,
                        "country": sat.get("countries") or None,
                        "source_flags": {"satnogs": True},
                    })

        elapsed = int((time.time() - t0) * 1000)

        if records:
            upsert_satellites(records)
            log_source_health(
                source="satnogs",
                status="ok",
                count=len(records),
                response_time_ms=elapsed,
            )
            logger.info("satnogs: upserted %d satellite metadata records", len(records))
        else:
            log_source_health(source="satnogs", status="empty",
                              response_time_ms=elapsed)

    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        log_source_health(source="satnogs", status="error", error=str(exc),
                          response_time_ms=elapsed)
        logger.error("fetch_satnogs_metadata failed: %s", exc)
