"""
McDowell GCAT full catalog fetcher.

Fetches derived/currentcat.tsv from Jonathan McDowell's GCAT and
upserts metadata (no TLEs) into the satellites table.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, date
from typing import Any

import httpx

from db.client import upsert_satellites, log_source_health
from fetchers.retries import http_retry

logger = logging.getLogger(__name__)

GCAT_URL = "https://planet4589.org/space/gcat/tsv/derived/currentcat.tsv"
TIMEOUT = httpx.Timeout(60.0)


def _safe_int(val: str) -> int | None:
    try:
        return int(val.strip())
    except Exception:
        return None


def _safe_date(val: str) -> str | None:
    val = val.strip()
    if not val or val == "-":
        return None
    # GCAT uses YYYY-MM-DD or YYYY-Mon-DD
    formats = ["%Y-%m-%d", "%Y-%b-%d", "%Y %b %d"]
    for fmt in formats:
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _orbit_class_from_type(orbit_type: str) -> str:
    """Map GCAT orbit type codes to our classification."""
    ot = (orbit_type or "").upper()
    if ot.startswith("LEO"):
        return "LEO"
    if ot.startswith("MEO"):
        return "MEO"
    if ot in ("GEO", "GSO", "SSO"):
        return "GEO"
    if ot.startswith("HEO") or ot in ("GTO", "MTO"):
        return "HEO"
    if ot in ("DEEP", "HELIO", "LUNAR", "PLANET"):
        return "DEEP"
    return "UNKNOWN"


@http_retry
async def _http_get_text(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


async def fetch_gcat_catalog() -> None:
    """Fetch GCAT TSV and upsert satellite metadata."""
    t0 = time.time()
    try:
        async with httpx.AsyncClient() as client:
            text = await _http_get_text(client, GCAT_URL)

        lines = text.splitlines()
        if not lines:
            log_source_health(source="gcat", status="empty",
                              response_time_ms=int((time.time() - t0) * 1000))
            return

        # Find header line
        header_line = None
        data_start = 0
        for i, line in enumerate(lines):
            if line.startswith("#") or line.startswith("Jcat"):
                header_line = line.lstrip("#").strip()
                data_start = i + 1
                break

        if header_line is None:
            # Try first non-comment line as header
            header_line = lines[0]
            data_start = 1

        headers = [h.strip() for h in header_line.split("\t")]

        def col(row: list[str], name: str) -> str:
            try:
                idx = headers.index(name)
                return row[idx].strip() if idx < len(row) else ""
            except ValueError:
                return ""

        records: list[dict[str, Any]] = []
        for line in lines[data_start:]:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")

            # Try common GCAT column names
            norad_raw = col(parts, "Satcat") or col(parts, "NORAD") or col(parts, "satno")
            norad_id = _safe_int(norad_raw)
            if not norad_id:
                continue

            name = col(parts, "Name") or col(parts, "Satname") or "UNKNOWN"
            cospar = col(parts, "COSPAR") or col(parts, "Intldes") or None
            object_type = col(parts, "Type") or col(parts, "ObjType") or "UNKNOWN"
            orbit_type = col(parts, "Orbit") or col(parts, "OrbitClass") or ""
            launch_raw = col(parts, "LaunchDate") or col(parts, "Launch") or ""
            decay_raw = col(parts, "DeorbitDate") or col(parts, "Decay") or ""

            orbit_class = _orbit_class_from_type(orbit_type)
            launch_date = _safe_date(launch_raw)
            decay_date = _safe_date(decay_raw)

            record: dict[str, Any] = {
                "norad_id": norad_id,
                "name": name,
                "cospar_id": cospar if cospar and cospar != "-" else None,
                "object_type": object_type,
                "source_flags": {"gcat": True},
            }
            # Only set orbit_class if GCAT has a meaningful value — don't
            # overwrite the correct value already set by CelesTrak/AMSAT
            if orbit_class != "UNKNOWN":
                record["orbit_class"] = orbit_class
            if launch_date:
                record["launch_date"] = launch_date
            if decay_date:
                record["decay_date"] = decay_date
                # Do NOT override status here — CelesTrak active tracking is the
                # authoritative source for operational status.  mark_satellites_active
                # already skips satellites with a decay_date (decay_date IS NULL check),
                # so decayed objects will naturally not be promoted.

            records.append(record)

        # Deduplicate by norad_id — GCAT TSV can list the same object multiple times
        seen: dict[int, dict] = {}
        for r in records:
            seen[r["norad_id"]] = r
        records = list(seen.values())

        elapsed = int((time.time() - t0) * 1000)

        if records:
            upsert_satellites(records)
            log_source_health(
                source="gcat",
                status="ok",
                count=len(records),
                response_time_ms=elapsed,
            )
            logger.info("gcat: upserted %d satellite catalog records", len(records))
        else:
            log_source_health(source="gcat", status="empty",
                              response_time_ms=elapsed)

    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        log_source_health(source="gcat", status="error", error=str(exc),
                          response_time_ms=elapsed)
        logger.error("fetch_gcat_catalog failed: %s", exc)
