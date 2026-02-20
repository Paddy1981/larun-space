"""
FastAPI routes — Phase 2: conjunction data.

  GET /v1/conjunctions
      Query params: hours, threshold_km, limit
      Returns: {last_computed, conjunctions: [...]}

Returns {last_computed: null, conjunctions: []} until the first 6-hour
scheduler run completes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from db.client import get_client

router = APIRouter()


@router.get("/v1/conjunctions")
def get_conjunctions(
    threshold_km: float = Query(default=10.0, ge=0.1, le=100.0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Return latest conjunction screenings below the distance threshold."""
    try:
        db = get_client()

        # When was the last screening run?
        meta = (
            db.table("conjunctions")
            .select("computed_at")
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
        last_computed = meta.data[0]["computed_at"] if meta.data else None

        if last_computed is None:
            return {"last_computed": None, "conjunctions": []}

        # Fetch closest-approach events
        result = (
            db.table("conjunctions")
            .select(
                "norad_id_1, norad_id_2, tca_time, "
                "miss_distance_km, relative_velocity_km_s, screening_window_hrs"
            )
            .lte("miss_distance_km", threshold_km)
            .order("miss_distance_km", desc=False)
            .limit(limit)
            .execute()
        )

        rows = result.data or []
        if not rows:
            return {"last_computed": last_computed, "conjunctions": []}

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
            }
            for r in rows
        ]

        return {"last_computed": last_computed, "conjunctions": conjunctions}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
