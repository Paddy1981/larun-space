"""
FastAPI routes — Phase 2: conjunction data.

  GET /v1/conjunctions
      Query params: hours, threshold_km, limit
      Returns: {last_computed, hours, conjunctions: [...]}

  hours  — only return conjunctions whose TCA falls within the last N hours
           (default 72, min 1, max 168). The ``last_computed`` field is
           unaffected by this filter and always reflects the most recent
           screening run ever completed.

Returns {last_computed: null, hours: <hours>, conjunctions: []} until the
first 6-hour scheduler run completes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from db.client import get_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/v1/conjunctions")
def get_conjunctions(
    hours: int = Query(
        default=72,
        ge=1,
        le=168,
        description="Only return conjunctions with TCA within this many hours from now",
    ),
    threshold_km: float = Query(default=10.0, ge=0.1, le=100.0),
    limit: int = Query(default=100, ge=1, le=500),
    min_risk: float = Query(default=0.0, ge=0.0, le=10.0, description="Minimum risk score filter"),
) -> dict[str, Any]:
    """Return latest conjunction screenings below the distance threshold.

    Args:
        hours: Sliding window — only conjunctions whose TCA is no earlier than
               ``now - hours`` are returned. Does not affect ``last_computed``.
        threshold_km: Upper bound on miss distance (km).
        limit: Maximum number of rows to return.
    """
    try:
        db = get_client()

        # When was the last screening run? Unfiltered — reflects all history.
        meta = (
            db.table("conjunctions")
            .select("computed_at")
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
        last_computed = meta.data[0]["computed_at"] if meta.data else None

        if last_computed is None:
            return {"last_computed": None, "hours": hours, "conjunctions": []}

        # Only return events whose TCA falls within the requested time window.
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        result = (
            db.table("conjunctions")
            .select(
                "norad_id_1, norad_id_2, tca_time, "
                "miss_distance_km, relative_velocity_km_s, screening_window_hrs, "
                "risk_score, conjunction_risk_label"
            )
            .gte("tca_time", cutoff.isoformat())
            .lte("miss_distance_km", threshold_km)
            .gte("risk_score", min_risk)
            .order("miss_distance_km", desc=False)
            .limit(limit)
            .execute()
        )

        rows = result.data or []
        if not rows:
            return {"last_computed": last_computed, "hours": hours, "conjunctions": []}

        # Resolve satellite names in one extra query
        norad_ids = list(
            {r["norad_id_1"] for r in rows} | {r["norad_id_2"] for r in rows}
        )
        names_result = (
            db.table("satellites")
            .select("norad_id, name")
            .in_("norad_id", norad_ids)
            .execute()
        )
        names: dict[int, str] = {
            r["norad_id"]: r["name"] for r in (names_result.data or [])
        }

        conjunctions = [
            {
                "norad_id_1": r["norad_id_1"],
                "name_1": names.get(r["norad_id_1"], str(r["norad_id_1"])),
                "norad_id_2": r["norad_id_2"],
                "name_2": names.get(r["norad_id_2"], str(r["norad_id_2"])),
                "tca_time": r["tca_time"],
                "miss_distance_km": r["miss_distance_km"],
                "relative_velocity_km_s": r["relative_velocity_km_s"],
                "screening_window_hrs": r["screening_window_hrs"],
                "risk_score": r.get("risk_score"),
                "conjunction_risk_label": r.get("conjunction_risk_label"),
            }
            for r in rows
        ]

        return {"last_computed": last_computed, "hours": hours, "conjunctions": conjunctions}

    except Exception as exc:
        logger.error("get_conjunctions: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")
