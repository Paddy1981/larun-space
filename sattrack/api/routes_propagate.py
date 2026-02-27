"""
FastAPI routes — Phase 2: orbit propagation.

  GET /v1/propagate/{norad_id}
      Query params: t (ISO 8601), obs_lat, obs_lon, obs_alt_m
      Returns: lat, lon, alt_km, velocity_km_s [, az_deg, el_deg, range_km]

  GET /v1/propagate/{norad_id}/groundtrack
      Query params: minutes (1-720), step_s (10-600)
      Returns: {norad_id, points: [{t, lat, lon, alt_km}, ...]}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from propagator.propagator import propagate_single, propagate_groundtrack

router = APIRouter()


@router.get("/v1/propagate/{norad_id}")
def get_position(
    norad_id: int,
    t: str | None = Query(default=None, description="ISO 8601 timestamp; defaults to now"),
    obs_lat: float | None = Query(default=None, ge=-90, le=90),
    obs_lon: float | None = Query(default=None, ge=-180, le=180),
    obs_alt_m: float = Query(default=0.0, ge=0),
) -> dict[str, Any]:
    """Return satellite position at time t with optional observer look angles."""
    if t is not None:
        try:
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid ISO timestamp: {t!r}")
    else:
        dt = datetime.now(timezone.utc)

    try:
        return propagate_single(norad_id, dt, obs_lat, obs_lon, obs_alt_m)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/propagate/{norad_id}/groundtrack")
def get_groundtrack(
    norad_id: int,
    minutes: int = Query(default=90, ge=1, le=720),
    step_s: int = Query(default=60, ge=10, le=600),
) -> dict[str, Any]:
    """Return groundtrack points for the next N minutes starting from now."""
    try:
        return propagate_groundtrack(norad_id, minutes, step_s)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error")
