"""
Supabase client and batch upsert helpers for SatTrack.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
    return _client


def upsert_satellites(records: list[dict[str, Any]]) -> int:
    """Batch upsert satellite metadata. Returns number of rows affected."""
    if not records:
        return 0
    client = get_client()
    try:
        result = (
            client.table("satellites")
            .upsert(records, on_conflict="norad_id")
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.debug("upsert_satellites: %d rows", count)
        return count
    except Exception as exc:
        logger.error("upsert_satellites failed: %s", exc)
        raise


def upsert_tle_batch(records: list[dict[str, Any]]) -> int:
    """
    Batch upsert TLE records into tle_history.
    After inserting, marks is_current=False on all previous rows for the
    affected norad_ids, then sets is_current=True on the latest epoch per
    norad_id.
    """
    if not records:
        return 0
    client = get_client()

    # 1. Upsert the new TLE rows (ignore exact duplicates via unique index)
    try:
        result = (
            client.table("tle_history")
            .upsert(records, on_conflict="norad_id,epoch,source", ignore_duplicates=True)
            .execute()
        )
    except Exception as exc:
        logger.error("upsert_tle_batch insert failed: %s", exc)
        raise

    inserted = len(result.data) if result.data else 0

    # 2. For each affected norad_id, refresh is_current flag via RPC
    norad_ids = list({r["norad_id"] for r in records})
    try:
        client.rpc("refresh_current_tle", {"p_norad_ids": norad_ids}).execute()
    except Exception as exc:
        # Fallback: update is_current manually in Python if RPC not yet created
        logger.warning("refresh_current_tle RPC unavailable, using fallback: %s", exc)
        _refresh_current_tle_fallback(client, norad_ids)

    logger.info("upsert_tle_batch: inserted %d rows for %d satellites", inserted, len(norad_ids))
    return inserted


def _refresh_current_tle_fallback(client: Client, norad_ids: list[int]) -> None:
    """
    Fallback current-TLE refresh when the SQL function isn't deployed yet.
    Marks all rows false, then the most-recent epoch per norad_id true.
    """
    for norad_id in norad_ids:
        try:
            # Mark all false
            client.table("tle_history").update({"is_current": False}).eq(
                "norad_id", norad_id
            ).execute()
            # Get latest epoch
            row = (
                client.table("tle_history")
                .select("id, epoch")
                .eq("norad_id", norad_id)
                .order("epoch", desc=True)
                .limit(1)
                .execute()
            )
            if row.data:
                client.table("tle_history").update({"is_current": True}).eq(
                    "id", row.data[0]["id"]
                ).execute()
        except Exception as exc:
            logger.error("_refresh_current_tle_fallback norad_id=%d: %s", norad_id, exc)


def upsert_space_weather(records: list[dict[str, Any]]) -> int:
    """Batch upsert space weather observations."""
    if not records:
        return 0
    client = get_client()
    try:
        result = (
            client.table("space_weather")
            .upsert(records, on_conflict="observed_at")
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.debug("upsert_space_weather: %d rows", count)
        return count
    except Exception as exc:
        logger.error("upsert_space_weather failed: %s", exc)
        raise


def log_source_health(
    source: str,
    status: str,
    count: int = 0,
    response_time_ms: int = 0,
    freshest_epoch: datetime | None = None,
    error: str | None = None,
) -> None:
    """Insert a source health record."""
    client = get_client()
    record: dict[str, Any] = {
        "source": source,
        "status": status,
        "objects_returned": count,
        "response_time_ms": response_time_ms,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if freshest_epoch:
        record["freshest_epoch"] = freshest_epoch.isoformat()
    if error:
        record["error_message"] = str(error)[:500]
    try:
        client.table("source_health").insert(record).execute()
    except Exception as exc:
        logger.error("log_source_health failed: %s", exc)
