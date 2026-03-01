"""
Supabase client and batch upsert helpers for SatTrack.
"""
from __future__ import annotations

import os
import logging
import functools
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
logger = logging.getLogger(__name__)

_client: Client | None = None

# ── Chunking constants ────────────────────────────────────────────────────────
# Each chunk must complete within Supabase's 8 s statement timeout (free tier).
_UPSERT_CHUNK = 200   # rows per tle_history upsert batch
_RPC_CHUNK    = 500   # norad_ids per refresh_current_tle RPC call

# ── Connection-error detection & retry ───────────────────────────────────────
# HTTP/2 connections to Supabase are recycled after ~10 000 streams
# (GOAWAY last_stream_id:19999).  The supabase-py singleton never reconnects
# by itself, so we detect the error, recreate the client, and retry once.
_CONN_ERR_KEYS = (
    "connectionterminated",
    "server disconnected",
    "connection reset",
    "connection refused",
    "eof occurred",
    "broken pipe",
    "remotely closed",
    "errno 104",
)


def is_conn_err(exc: Exception) -> bool:
    return any(k in str(exc).lower() for k in _CONN_ERR_KEYS)


def reset_client() -> None:
    global _client
    _client = None
    logger.warning("db.client: Supabase client reset due to connection error")


def get_client(force_new: bool = False) -> Client:
    global _client
    if _client is None or force_new:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
    return _client


def _db_retry(fn):
    """Retry fn once after resetting the Supabase client on connection errors."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if is_conn_err(exc):
                reset_client()
                logger.info("db.client: retrying %s after connection reset", fn.__name__)
                return fn(*args, **kwargs)
            raise
    return wrapper


# ── Satellite upserts ─────────────────────────────────────────────────────────

@_db_retry
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


# ── TLE upserts ───────────────────────────────────────────────────────────────

@_db_retry
def upsert_tle_batch(records: list[dict[str, Any]]) -> int:
    """
    Batch upsert TLE records into tle_history, then refresh is_current flags.

    Records are inserted in chunks of _UPSERT_CHUNK to stay under Supabase's
    8-second statement timeout on the free tier.  The refresh_current_tle RPC
    is called in chunks of _RPC_CHUNK norad_ids; transient RPC failures are
    logged and skipped rather than falling back to a per-satellite Python loop
    (which would make thousands of individual API calls and always time out).
    """
    if not records:
        return 0
    client = get_client()
    total_inserted = 0

    # 1. Chunked upsert — avoids statement timeout on large TLE ingest batches
    for i in range(0, len(records), _UPSERT_CHUNK):
        chunk = records[i : i + _UPSERT_CHUNK]
        try:
            result = (
                client.table("tle_history")
                .upsert(chunk, on_conflict="norad_id,epoch,source", ignore_duplicates=True)
                .execute()
            )
            total_inserted += len(result.data) if result.data else 0
        except Exception as exc:
            logger.error(
                "upsert_tle_batch chunk [%d:%d] failed: %s",
                i, i + _UPSERT_CHUNK, exc,
            )
            raise

    # 2. Refresh is_current flags via RPC — chunked to avoid RPC timeout
    norad_ids = list({r["norad_id"] for r in records})
    for i in range(0, len(norad_ids), _RPC_CHUNK):
        chunk_ids = norad_ids[i : i + _RPC_CHUNK]
        try:
            client.rpc("refresh_current_tle", {"p_norad_ids": chunk_ids}).execute()
        except Exception as exc:
            # Transient RPC failure — data is already inserted correctly.
            # Do NOT fall back to per-satellite Python loop (3 API calls × N sats).
            logger.warning(
                "upsert_tle_batch: refresh_current_tle RPC failed for chunk "
                "[%d:%d] (%d ids): %s",
                i, i + _RPC_CHUNK, len(chunk_ids), exc,
            )

    logger.info(
        "upsert_tle_batch: %d records inserted for %d satellites",
        total_inserted, len(norad_ids),
    )
    return total_inserted


# ── Space weather ─────────────────────────────────────────────────────────────

@_db_retry
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


# ── Satellite status ──────────────────────────────────────────────────────────

@_db_retry
def mark_satellites_active(norad_ids: list[int]) -> int:
    """Set status='active' for satellites that a live TLE source considers operational.

    Only updates rows where:
      - status IS NULL  (never classified)
      - status = 'unknown'  (SatNOGS couldn't determine status)
    Intentionally skips 'decayed' and 'inactive' so GCAT/SatNOGS terminal
    states are preserved.  Also skips rows with a known decay_date.
    """
    if not norad_ids:
        return 0
    client = get_client()
    total = 0
    _NON_PAYLOAD = ["ROCKET BODY", "DEBRIS", "R", "Rb", "D", "DEB"]
    CHUNK = 500
    for i in range(0, len(norad_ids), CHUNK):
        chunk = norad_ids[i : i + CHUNK]
        try:
            for q in [
                client.table("satellites")
                    .update({"status": "active"})
                    .in_("norad_id", chunk)
                    .is_("decay_date", "null")
                    .is_("status", "null")
                    .not_.in_("object_type", _NON_PAYLOAD),
                client.table("satellites")
                    .update({"status": "active"})
                    .in_("norad_id", chunk)
                    .is_("decay_date", "null")
                    .eq("status", "unknown")
                    .not_.in_("object_type", _NON_PAYLOAD),
            ]:
                r = q.execute()
                total += len(r.data) if r.data else 0
        except Exception as exc:
            logger.error("mark_satellites_active chunk %d failed: %s", i, exc)
    logger.info("mark_satellites_active: promoted %d satellites to active", total)
    return total


# ── Conjunctions ──────────────────────────────────────────────────────────────

@_db_retry
def upsert_conjunctions(records: list[dict[str, Any]]) -> int:
    """
    Upsert conjunction screening results.

    Deduplicates on (norad_id_1, norad_id_2, tca_time) via the unique
    constraint defined in schema_phase2.sql.  Returns number of rows written.
    """
    if not records:
        return 0
    client = get_client()
    try:
        result = (
            client.table("conjunctions")
            .upsert(
                records,
                on_conflict="norad_id_1,norad_id_2,tca_time",
                ignore_duplicates=False,
            )
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.info("upsert_conjunctions: %d rows written", count)
        return count
    except Exception as exc:
        logger.error("upsert_conjunctions failed: %s", exc)
        raise


# ── Source health ─────────────────────────────────────────────────────────────

@_db_retry
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


# ── Enrichment & ML ───────────────────────────────────────────────────────────

@_db_retry
def upsert_satellite_enrichment(records: list[dict[str, Any]]) -> int:
    """
    Batch upsert satellite enrichment metadata (physical specs, mission data,
    debris risk labels).  Conflicts on norad_id; all other columns are updated.
    Returns number of rows written.
    """
    if not records:
        return 0
    client = get_client()
    try:
        result = (
            client.table("satellite_enrichment")
            .upsert(records, on_conflict="norad_id")
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.debug("upsert_satellite_enrichment: %d rows", count)
        return count
    except Exception as exc:
        logger.error("upsert_satellite_enrichment failed: %s", exc)
        raise


@_db_retry
def upsert_maneuver_events(records: list[dict[str, Any]]) -> int:
    """
    Batch upsert maneuver detection events.
    Deduplicates on (norad_id, detected_epoch); existing confirmed events are
    not overwritten (ignore_duplicates=True).
    Returns number of rows written.
    """
    if not records:
        return 0
    client = get_client()
    try:
        result = (
            client.table("maneuver_events")
            .upsert(
                records,
                on_conflict="norad_id,detected_epoch",
                ignore_duplicates=True,
            )
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.debug("upsert_maneuver_events: %d rows", count)
        return count
    except Exception as exc:
        logger.error("upsert_maneuver_events failed: %s", exc)
        raise


@_db_retry
def upsert_decay_predictions(records: list[dict[str, Any]]) -> int:
    """
    Batch upsert orbital decay predictions.
    Conflicts on norad_id; all columns replaced with latest computation.
    Returns number of rows written.
    """
    if not records:
        return 0
    client = get_client()
    try:
        result = (
            client.table("decay_predictions")
            .upsert(records, on_conflict="norad_id")
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.debug("upsert_decay_predictions: %d rows", count)
        return count
    except Exception as exc:
        logger.error("upsert_decay_predictions failed: %s", exc)
        raise
