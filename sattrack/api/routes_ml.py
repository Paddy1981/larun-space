"""
ML-derived data endpoints for SatTrack.

Endpoints:
  GET /ml/maneuvers/{norad_id}  — maneuver events for a single satellite
  GET /ml/maneuvers/recent      — recent maneuvers across all satellites (last 30 days)
  GET /ml/decay                 — all satellites with a predicted reentry date
  GET /ml/decay/{norad_id}      — single satellite decay prediction or 404
  GET /ml/density               — orbital density bins from local tle_features DB
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from db.client import get_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _db():
    return get_client()


def _local_pg_conn():
    """Open a connection to the local PostgreSQL instance that holds tle_features."""
    import psycopg2  # local import — not available on Railway production
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5433)),
        dbname=os.environ.get("POSTGRES_DB", "sattrack_ml"),
        user=os.environ.get("POSTGRES_USER", "sattrack_ml"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


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
        raise HTTPException(status_code=500, detail=str(exc))


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
        raise HTTPException(status_code=500, detail=str(exc))


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
        raise HTTPException(status_code=500, detail=str(exc))


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
            .single()
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
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────
# GET /ml/density
# ─────────────────────────────────────────────

@router.get("/ml/density")
def get_orbital_density() -> dict[str, Any]:
    """Return orbital density binned in 50 km altitude bands.

    Queries the local PostgreSQL tle_features table (port POSTGRES_PORT),
    groups by ROUND(perigee_km/50)*50, and returns object counts and mean
    eccentricity per bin.
    """
    sql = """
        SELECT
            ROUND(perigee_km / 50.0) * 50  AS altitude_km,
            COUNT(*)                         AS object_count,
            AVG(eccentricity)                AS avg_eccentricity
        FROM tle_features
        WHERE perigee_km IS NOT NULL
        GROUP BY ROUND(perigee_km / 50.0) * 50
        ORDER BY altitude_km
    """
    try:
        conn = _local_pg_conn()
    except Exception as exc:
        # Local ML database is not available (expected on Railway production).
        # Return an empty result so the frontend can degrade gracefully rather
        # than logging a noisy 500 error.
        logger.warning("get_orbital_density: local ML DB unavailable — %s", exc)
        return {"count": 0, "data": [], "unavailable": True}

    try:
        import psycopg2.extras  # local import — not available on Railway production
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        conn.close()

    bins = [
        {
            "altitude_km": float(row["altitude_km"]),
            "object_count": int(row["object_count"]),
            "avg_eccentricity": (
                float(row["avg_eccentricity"])
                if row["avg_eccentricity"] is not None
                else None
            ),
        }
        for row in rows
    ]
    return {
        "count": len(bins),
        "data": bins,
    }
