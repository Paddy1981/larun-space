"""
TLE quality scoring — returns 0–100.

Factors:
  - TLE age vs orbit class tolerance
  - Source reliability weight
  - Solar activity penalty for LEO (higher Kp/F10.7 → faster drag changes)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Maximum acceptable TLE age by orbit class (hours) for full score
_AGE_TOLERANCE_HRS: dict[str, float] = {
    "LEO": 6,
    "MEO": 48,
    "GEO": 72,
    "HEO": 24,
    "DEEP": 168,
    "UNKNOWN": 24,
}

# Score decay: after tolerance, lose 1 point per this many hours
_AGE_DECAY_HRS: dict[str, float] = {
    "LEO": 0.5,
    "MEO": 4,
    "GEO": 8,
    "HEO": 2,
    "DEEP": 12,
    "UNKNOWN": 2,
}

# Source reliability weights (0.7–1.0 multiplier)
_SOURCE_WEIGHT: dict[str, float] = {
    "celestrak": 1.0,
    "supplemental": 0.95,
    "amsat": 0.90,
    "satnogs": 0.80,
    "unknown": 0.70,
}


def score_tle_quality(
    tle_record: dict[str, Any],
    space_weather: dict[str, Any] | None = None,
) -> int:
    """
    Score a TLE record on a 0–100 scale.

    Args:
        tle_record: dict with keys: epoch (ISO str or datetime), source (str),
                    orbit_class (str, optional).
        space_weather: dict with optional keys: kp_index (float), f107_flux (float).

    Returns:
        Integer score 0–100.
    """
    score = 100.0

    # --- Age penalty ---
    orbit_class = (tle_record.get("orbit_class") or "UNKNOWN").upper()
    if orbit_class not in _AGE_TOLERANCE_HRS:
        orbit_class = "UNKNOWN"

    epoch = tle_record.get("epoch")
    if epoch:
        if isinstance(epoch, str):
            try:
                epoch = datetime.fromisoformat(epoch.replace("Z", "+00:00"))
            except ValueError:
                epoch = None
        if epoch:
            now = datetime.now(timezone.utc)
            if epoch.tzinfo is None:
                epoch = epoch.replace(tzinfo=timezone.utc)
            age_hrs = (now - epoch).total_seconds() / 3600.0
            tolerance = _AGE_TOLERANCE_HRS[orbit_class]
            decay = _AGE_DECAY_HRS[orbit_class]
            if age_hrs > tolerance:
                over = age_hrs - tolerance
                score -= over / decay
    else:
        # No epoch → heavy penalty
        score -= 40

    # --- Source reliability ---
    source = (tle_record.get("source") or "unknown").lower()
    # Normalise supplemental variants
    if "supp" in source or "starlink" in source or "oneweb" in source:
        source = "supplemental"
    weight = _SOURCE_WEIGHT.get(source, _SOURCE_WEIGHT["unknown"])
    score *= weight

    # --- Solar activity penalty for LEO ---
    if orbit_class == "LEO" and space_weather:
        kp = space_weather.get("kp_index") or 0.0
        f107 = space_weather.get("f107_flux") or 70.0
        # Kp penalty: each point above 4 costs 2 score points
        if kp > 4:
            score -= (kp - 4) * 2
        # F10.7 penalty: each unit above 150 costs 0.05 score points
        if f107 > 150:
            score -= (f107 - 150) * 0.05

    return max(0, min(100, round(score)))
