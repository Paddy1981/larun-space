"""
FastAPI routes — Phase 2: pass predictions + sun outages.

  GET /v1/passes/{norad_id}
      Query params: lat, lon, alt_m, days (1-14), min_elevation (0-90)
      Returns: {norad_id, passes: [{aos, tca, los, max_elevation_deg,
                                    duration_sec, direction, range_km_at_tca}]}

  GET /v1/satellites/{norad_id}/sun-outage
      Query params: lat, lon, alt_m, days (1-365), threshold_deg (0.5-5.0)
      Returns: {norad_id, observer, threshold_deg, satellite_az_deg,
                satellite_el_deg, outages: [{start, peak, end,
                duration_sec, min_separation_deg}]}

GEO satellites return a single entry with type="geostationary" for passes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from passes.pass_predictor import predict_passes
from passes.sun_outage import predict_sun_outages

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
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/v1/satellites/{norad_id}/sun-outage", tags=["Passes"])
def get_sun_outage(
    norad_id: int,
    lat: float = Query(..., ge=-90, le=90, description="Observer latitude (degrees)"),
    lon: float = Query(..., ge=-180, le=180, description="Observer longitude (degrees)"),
    alt_m: float = Query(default=0.0, ge=0, description="Observer altitude above sea level (m)"),
    days: int = Query(default=365, ge=1, le=365, description="Prediction window in days (max 365)"),
    threshold_deg: float = Query(default=2.0, ge=0.5, le=5.0, description="Sun–satellite angular threshold (degrees)"),
) -> dict[str, Any]:
    """
    Predict solar transit (sun outage) windows for a GEO satellite.

    Returns windows during which the sun passes within *threshold_deg* of the
    satellite as seen from the observer, causing signal interference.  Only
    applicable to geostationary satellites (mean_motion < 2 rev/day).
    """
    try:
        return predict_sun_outages(norad_id, lat, lon, alt_m, days, threshold_deg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error")
