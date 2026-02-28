"""
ML-derived data endpoints for SatTrack.

Endpoints:
  GET /ml/maneuvers/{norad_id}  — maneuver events for a single satellite
  GET /ml/maneuvers/recent      — recent maneuvers across all satellites (last 30 days)
  GET /ml/decay                 — all satellites with a predicted reentry date
  GET /ml/decay/{norad_id}      — single satellite decay prediction or 404
  GET /ml/density               — orbital density bins computed from Supabase tle_history
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from db.client import get_client

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)
except ImportError:
    _limiter = None

def _limit(rate: str):
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
# GET /ml/maneuvers/recent  (must be before /{norad_id})
# ─────────────────────────────────────────────

@router.get("/ml/maneuvers/recent")
def get_recent_maneuvers(
    limit: int = Query(default=100, ge=1, le=1000),
    maneuver_type: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return recent maneuvers across all satellites from the last 30 days.

    Optionally filter by maneuver_type.  Satellite name is joined from the
    satellites table.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        q = (
            _db()
            .table("maneuver_events")
            .select(
                "norad_id,"
                "detected_epoch,"
                "prior_epoch,"
                "maneuver_type,"
                "confidence,"
                "delta_v_proxy,"
                "delta_inclination,"
                "delta_mean_motion,"
                "delta_eccentricity,"
                "satellites(name)"
            )
            .gte("detected_epoch", cutoff)
            .order("detected_epoch", desc=True)
            .limit(limit)
        )

        if maneuver_type:
            q = q.eq("maneuver_type", maneuver_type)

        result = q.execute()
        return {
            "count": len(result.data),
            "limit": limit,
            "since": cutoff,
            "maneuver_type_filter": maneuver_type,
            "data": result.data,
        }
    except Exception as exc:
        logger.error("get_recent_maneuvers: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# ─────────────────────────────────────────────
# GET /ml/maneuvers/{norad_id}
# ─────────────────────────────────────────────

@router.get("/ml/maneuvers/{norad_id}")
def get_maneuvers_for_satellite(norad_id: int) -> dict[str, Any]:
    """Return maneuver events for a single satellite, newest first, capped at 50."""
    try:
        result = (
            _db()
            .table("maneuver_events")
            .select(
                "detected_epoch,"
                "prior_epoch,"
                "maneuver_type,"
                "confidence,"
                "delta_v_proxy,"
                "delta_inclination,"
                "delta_mean_motion,"
                "delta_eccentricity"
            )
            .eq("norad_id", norad_id)
            .order("detected_epoch", desc=True)
            .limit(50)
            .execute()
        )
        return {
            "norad_id": norad_id,
            "count": len(result.data),
            "data": result.data,
        }
    except Exception as exc:
        logger.error("get_maneuvers_for_satellite norad_id=%d: %s", norad_id, exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# ─────────────────────────────────────────────
# GET /ml/decay
# ─────────────────────────────────────────────

@router.get("/ml/decay")
def get_decay_predictions() -> dict[str, Any]:
    """Return all satellites with a non-null predicted reentry, sorted soonest first."""
    try:
        result = (
            _db()
            .table("decay_predictions")
            .select(
                "*,"
                "satellites(name)"
            )
            .not_.is_("predicted_reentry", "null")
            .order("predicted_reentry", desc=False)
            .limit(200)
            .execute()
        )
        return {
            "count": len(result.data),
            "data": result.data,
        }
    except Exception as exc:
        logger.error("get_decay_predictions: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# ─────────────────────────────────────────────
# GET /ml/decay/{norad_id}
# ─────────────────────────────────────────────

@router.get("/ml/decay/{norad_id}")
def get_decay_prediction_for_satellite(norad_id: int) -> dict[str, Any]:
    """Return the decay prediction for a single satellite, or 404 if not found."""
    try:
        result = (
            _db()
            .table("decay_predictions")
            .select(
                "*,"
                "satellites(name)"
            )
            .eq("norad_id", norad_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            raise HTTPException(
                status_code=404,
                detail=f"No decay prediction found for NORAD ID {norad_id}",
            )
        return result.data
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_decay_prediction_for_satellite norad_id=%d: %s", norad_id, exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# ─────────────────────────────────────────────
# GET /ml/density
# ─────────────────────────────────────────────

@router.get("/ml/density")
@_limit("10/minute")
def get_orbital_density(request: Request) -> dict[str, Any]:
    """Return orbital density binned in 50 km altitude bands.

    Computes perigee altitude from mean_motion + eccentricity (Kepler's third
    law) for every current TLE in Supabase tle_history, then bins into 50 km
    altitude shells.  No local ML database or environment flag required.
    """
    _MU = 398600.4418      # Earth gravitational parameter (km³/s²)
    _RE = 6371.0           # Earth mean radius (km)
    _BIN = 50              # altitude bin width (km)
    _PAGE = 1000           # rows per Supabase page
    _MAX_ROWS = 20_000     # safety cap — ~65K TLEs exist but we stop early

    try:
        db = _db()
        rows: list[dict] = []
        offset = 0

        # Paginate through current TLEs — only fetch the two columns needed
        while len(rows) < _MAX_ROWS:
            result = (
                db.table("tle_history")
                .select("mean_motion, eccentricity")
                .eq("is_current", True)
                .not_.is_("mean_motion", "null")
                .not_.is_("eccentricity", "null")
                .gt("mean_motion", 0)
                .range(offset, offset + _PAGE - 1)
                .execute()
            )
            page = result.data or []
            rows.extend(page)
            if len(page) < _PAGE:
                break
            offset += _PAGE

        # Compute perigee and bin
        bins: dict[int, dict] = {}
        for row in rows:
            try:
                n = row["mean_motion"] * 2 * math.pi / 86400   # rad/s
                sma = (_MU / n ** 2) ** (1.0 / 3.0)            # km
                perigee = sma * (1.0 - row["eccentricity"]) - _RE
                if perigee <= 0 or perigee > 40_000:            # sanity bounds
                    continue
                band = int(perigee / _BIN) * _BIN
                if band not in bins:
                    bins[band] = {"count": 0, "ecc_sum": 0.0}
                bins[band]["count"] += 1
                bins[band]["ecc_sum"] += row["eccentricity"]
            except (ZeroDivisionError, ValueError):
                continue

        data = sorted(
            [
                {
                    "altitude_km": float(alt),
                    "object_count": b["count"],
                    "avg_eccentricity": round(b["ecc_sum"] / b["count"], 6),
                }
                for alt, b in bins.items()
            ],
            key=lambda x: x["altitude_km"],
        )

        return {"count": len(data), "data": data}

    except Exception as exc:
        logger.error("get_orbital_density: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")
