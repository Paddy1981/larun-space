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

import hmac
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import os
from fastapi import APIRouter, Body, HTTPException, Header, Query, Request
from typing import List

from db.client import get_client, is_conn_err, reset_client

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)
except ImportError:
    _limiter = None

def _limit(rate: str):
    """Return a slowapi limit decorator, or a no-op if slowapi is unavailable."""
    if _limiter:
        return _limiter.limit(rate)
    return lambda f: f

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

@router.get("/v1/satellites", tags=["Satellites"])
def list_satellites(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    after_id: int = Query(default=0, ge=0),
    orbit_class: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    has_current_tle: bool = Query(default=False),
) -> dict[str, Any]:
    """Return a paginated list of satellites with optional filters.

    has_current_tle=true restricts results to satellites that have at least
    one TLE record with is_current=true (i.e. propagatable right now).
    Uses cursor-based pagination via after_id (norad_id) to avoid offset
    timeouts on large result sets from join queries.
    """
    try:
        db = _db()
        if has_current_tle:
            # Inner-join via tle_history; cursor pagination on norad_id avoids
            # the expensive OFFSET clause on joined queries.
            q = db.table("satellites").select(
                "norad_id, name, cospar_id, orbit_class, status, operator, country, "
                "launch_date, decay_date, object_type, source_flags, created_at, updated_at, "
                "tle_history!inner(is_current)"
            ).eq("tle_history.is_current", True).order("norad_id")
            if after_id:
                q = q.gt("norad_id", after_id)
        else:
            q = db.table("satellites").select("*")
            if after_id:
                q = q.gt("norad_id", after_id).order("norad_id")

        if orbit_class:
            q = q.eq("orbit_class", orbit_class.upper())
        if status:
            q = q.eq("status", status.lower())
        if search:
            if search.strip().isdigit():
                q = q.eq("norad_id", int(search.strip()))
            else:
                # Escape ilike special chars so user input can't craft wildcard patterns
                safe = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                q = q.ilike("name", f"%{safe}%")

        if after_id:
            result = q.limit(limit).execute()
        else:
            result = q.range(offset, offset + limit - 1).execute()

        rows = result.data or []
        if has_current_tle:
            for row in rows:
                row.pop("tle_history", None)

        # Expose last_id for cursor-based next-page fetch
        last_id = rows[-1]["norad_id"] if rows else None

        return {
            "count": len(rows),
            "offset": offset,
            "after_id": after_id,
            "last_id": last_id,
            "limit": limit,
            "data": rows,
        }
    except Exception as exc:
        logger.error("list_satellites: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/satellites/{norad_id}", tags=["Satellites"])
def get_satellite(norad_id: int) -> dict[str, Any]:
    """Return a single satellite by NORAD ID, merged with enrichment metadata."""
    try:
        db = _db()

        # Primary satellite row
        result = (
            db.table("satellites")
            .select("*")
            .eq("norad_id", norad_id)
            .single()
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Satellite not found")

        satellite = dict(result.data)

        # Enrichment row (LEFT JOIN equivalent � absent row is not an error)
        enrichment_result = (
            db.table("satellite_enrichment")
            .select("*")
            .eq("norad_id", norad_id)
            .limit(1)
            .execute()
        )
        if enrichment_result.data:
            enrichment = dict(enrichment_result.data[0])
            # Remove duplicate key before merging; satellite.norad_id is authoritative
            enrichment.pop("norad_id", None)
            satellite["enrichment"] = enrichment
        else:
            satellite["enrichment"] = None

        return satellite
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_satellite %d: %s", norad_id, exc)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/satellites/{norad_id}/enrichment", tags=["Satellites"])
def get_satellite_enrichment(norad_id: int) -> dict[str, Any]:
    """Return only the enrichment metadata row for a satellite.

    Returns 404 if the satellite_enrichment table has no row for this NORAD ID
    (i.e. enrichment data has not yet been populated).
    """
    try:
        result = (
            _db().table("satellite_enrichment")
            .select("*")
            .eq("norad_id", norad_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            raise HTTPException(
                status_code=404,
                detail=f"No enrichment data found for NORAD ID {norad_id}",
            )
        return result.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_satellite_enrichment %d: %s", norad_id, exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# ─────────────────────────────────────────────
# /v1/tle
# ─────────────────────────────────────────────

@router.get("/v1/tle/{norad_id}", tags=["TLE"])
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
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/v1/tle/bulk", tags=["TLE"])
@_limit("30/minute")
def get_bulk_tles(request: Request, norad_ids: List[int] = Body(...)) -> dict[str, Any]:
    """Return current TLEs for multiple NORAD IDs in a single DB query.

    Request body: JSON array of NORAD IDs, e.g. [25544, 48274, 55044]
    Capped at 2000 IDs per request.
    """
    try:
        if not norad_ids:
            return {"count": 0, "data": []}
        norad_ids = norad_ids[:2000]

        def _fetch():
            return (
                _db().table("tle_history")
                .select("norad_id, tle_line1, tle_line2, epoch, is_current")
                .in_("norad_id", norad_ids)
                .eq("is_current", True)
                .limit(2001)
                .execute()
            )

        try:
            result = _fetch()
        except Exception as exc:
            if is_conn_err(exc):
                reset_client()
                logger.info("get_bulk_tles: retrying after connection reset")
                result = _fetch()
            else:
                raise

        return {
            "count": len(result.data),
            "data": result.data,
        }
    except Exception as exc:
        logger.error("get_bulk_tles: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# ─────────────────────────────────────────────
# /v1/status
# ─────────────────────────────────────────────

@router.get("/v1/status", tags=["System"])
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
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/status/sources", tags=["System"])
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
        raise HTTPException(status_code=500, detail="Internal server error")


# ─────────────────────────────────────────────
# /v1/weather
# ─────────────────────────────────────────────

@router.get("/v1/weather/current", tags=["Space Weather"])
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
        raise HTTPException(status_code=500, detail="Internal server error")


# ─────────────────────────────────────────────
# /v1/admin  (protected by ADMIN_SECRET env var)
# ─────────────────────────────────────────────

def _require_admin(x_admin_secret: str | None) -> None:
    """Raise 403 if the caller doesn't supply the correct admin secret."""
    expected = os.environ.get("ADMIN_SECRET", "")
    if not expected or not hmac.compare_digest(x_admin_secret or "", expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/v1/admin/mark-active", tags=["System"])
def admin_mark_active(
    x_admin_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Scan tle_history for satellites with a fresh TLE (≤30 days) and promote
    them to status='active'.  Useful after first deploy or data backfills.

    Requires X-Admin-Secret header matching ADMIN_SECRET environment variable.
    """
    _require_admin(x_admin_secret)
    try:
        from db.client import mark_satellites_active
        db = _db()
        result = (
            db.table("tle_history")
            .select("norad_id")
            .eq("is_current", True)
            .gte("epoch", (datetime.now(timezone.utc) - timedelta(days=30)).isoformat())
            .execute()
        )
        norad_ids = list({row["norad_id"] for row in (result.data or [])})
        promoted = mark_satellites_active(norad_ids)
        return {
            "candidates": len(norad_ids),
            "promoted": promoted,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("admin_mark_active: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")
