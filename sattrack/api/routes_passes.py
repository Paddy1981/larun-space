"""
FastAPI routes — Phase 2: pass predictions.

  GET /v1/passes/{norad_id}
      Query params: lat, lon, alt_m, days (1-14), min_elevation (0-90)
      Returns: {norad_id, passes: [{aos, tca, los, max_elevation_deg,
                                    duration_sec, direction, range_km_at_tca}]}

GEO satellites return a single entry with type="geostationary".
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from passes.pass_predictor import predict_passes

router = APIRouter()


@router.get("/v1/passes/{norad_id}")
def get_passes(
    norad_id: int,
    lat: float = Query(..., ge=-90, le=90, description="Observer latitude (degrees)"),
    lon: float = Query(..., ge=-180, le=180, description="Observer longitude (degrees)"),
    alt_m: float = Query(default=0.0, ge=0, description="Observer altitude above sea level (m)"),
    days: int = Query(default=3, ge=1, le=14, description="Prediction window in days"),
    min_elevation: float = Query(default=10.0, ge=0, le=90, description="Minimum elevation (degrees)"),
) -> dict[str, Any]:
    """Predict satellite passes over an observer location."""
    try:
        return predict_passes(norad_id, lat, lon, alt_m, days, min_elevation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
