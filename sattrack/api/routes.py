"""
FastAPI routes for SatTrack Phase 1.

Endpoints:
  GET /v1/satellites          — paginated satellite list
  GET /v1/satellites/{id}     — single satellite
  GET /v1/tle/{norad_id}      — current best TLE
  GET /v1/status              — system health summary
  GET /v1/status/sources      — per-source freshness
  GET /v1/weather/current     — latest Kp and F10.7
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from typing import List

from db.client import get_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _db():
    return get_client()


# ─────────────────────────────────────────────
# /v1/satellites
# ─────────────────────────────────────────────

@router.get("/v1/satellites")
def list_satellites(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    orbit_class: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return a paginated list of satellites with optional filters."""
    try:
        q = _db().table("satellites").select("*")
        if orbit_class:
            q = q.eq("orbit_class", orbit_class.upper())
        if status:
            q = q.eq("status", status.lower())
        if search:
            q = q.ilike("name", f"%{search}%")
        result = q.range(offset, offset + limit - 1).execute()
        return {
            "count": len(result.data),
            "offset": offset,
            "limit": limit,
            "data": result.data,
        }
    except Exception as exc:
        logger.error("list_satellites: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/v1/satellites/{norad_id}")
def get_satellite(norad_id: int) -> dict[str, Any]:
    """Return a single satellite by NORAD ID."""
    try:
        result = (
            _db().table("satellites")
            .select("*")
            .eq("norad_id", norad_id)
            .single()
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Satellite not found")
        return result.data
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_satellite %d: %s", norad_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────
# /v1/tle
# ─────────────────────────────────────────────

@router.get("/v1/tle/{norad_id}")
def get_current_tle(norad_id: int) -> dict[str, Any]:
    """Return the current best TLE for a satellite."""
    try:
        result = (
            _db().table("tle_history")
            .select("*, satellites(name, orbit_class, status)")
            .eq("norad_id", norad_id)
            .eq("is_current", True)
            .order("epoch", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            raise HTTPException(
                status_code=404,
                detail=f"No current TLE found for NORAD ID {norad_id}",
            )
        return result.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_current_tle %d: %s", norad_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v1/tle/bulk")
def get_bulk_tles(norad_ids: List[int] = Body(...)) -> dict[str, Any]:
    """Return current TLEs for multiple NORAD IDs in a single DB query.

    Request body: JSON array of NORAD IDs, e.g. [25544, 48274, 55044]
    Capped at 2000 IDs per request.
    """
    try:
        if not norad_ids:
            return {"count": 0, "data": []}
        norad_ids = norad_ids[:2000]
        result = (
            _db().table("tle_history")
            .select("norad_id, tle_line1, tle_line2, epoch, is_current")
            .in_("norad_id", norad_ids)
            .eq("is_current", True)
            .execute()
        )
        return {
            "count": len(result.data),
            "data": result.data,
        }
    except Exception as exc:
        logger.error("get_bulk_tles: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────
# /v1/status
# ─────────────────────────────────────────────

@router.get("/v1/status")
def get_status() -> dict[str, Any]:
    """Return system health summary."""
    try:
        db = _db()

        sat_count = db.table("satellites").select("norad_id", count="exact").execute()
        tle_count = db.table("tle_history").select("id", count="exact").execute()
        weather_count = db.table("space_weather").select("id", count="exact").execute()

        latest_health = (
            db.table("source_health")
            .select("source, status, checked_at")
            .order("checked_at", desc=True)
            .limit(20)
            .execute()
        )

        # Determine overall health
        recent_statuses = [r["status"] for r in (latest_health.data or [])]
        overall = "ok"
        if recent_statuses and all(s == "error" for s in recent_statuses[:5]):
            overall = "degraded"

        return {
            "status": overall,
            "satellites_tracked": sat_count.count or 0,
            "tle_records_total": tle_count.count or 0,
            "weather_records_total": weather_count.count or 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("get_status: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/v1/status/sources")
def get_source_status() -> dict[str, Any]:
    """Return per-source freshness and health."""
    try:
        db = _db()
        # Get latest record per source
        result = db.rpc("get_latest_source_health", {}).execute()
        if result.data:
            return {"sources": result.data}

        # Fallback: manual aggregation
        all_health = (
            db.table("source_health")
            .select("*")
            .order("checked_at", desc=True)
            .limit(200)
            .execute()
        )

        by_source: dict[str, dict] = {}
        for row in (all_health.data or []):
            src = row["source"]
            if src not in by_source:
                by_source[src] = row

        return {"sources": list(by_source.values())}
    except Exception as exc:
        logger.error("get_source_status: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────
# /v1/weather
# ─────────────────────────────────────────────

@router.get("/v1/weather/current")
def get_current_weather() -> dict[str, Any]:
    """Return the latest Kp index and F10.7 flux."""
    try:
        db = _db()

        # Latest Kp
        kp_result = (
            db.table("space_weather")
            .select("observed_at, kp_index, source")
            .not_.is_("kp_index", "null")
            .order("observed_at", desc=True)
            .limit(1)
            .execute()
        )

        # Latest F10.7
        f107_result = (
            db.table("space_weather")
            .select("observed_at, f107_flux, source")
            .not_.is_("f107_flux", "null")
            .order("observed_at", desc=True)
            .limit(1)
            .execute()
        )

        latest_kp = kp_result.data[0] if kp_result.data else None
        latest_f107 = f107_result.data[0] if f107_result.data else None

        return {
            "kp_index": latest_kp["kp_index"] if latest_kp else None,
            "kp_observed_at": latest_kp["observed_at"] if latest_kp else None,
            "f107_flux": latest_f107["f107_flux"] if latest_f107 else None,
            "f107_observed_at": latest_f107["observed_at"] if latest_f107 else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("get_current_weather: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
