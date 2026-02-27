"""
SatTrack — Authentication helpers

JWT verification using python-jose (Supabase HS256 tokens).

FastAPI dependencies:
  - get_current_user()  → dict | raises HTTP 401
  - get_optional_user() → dict | None (no 401 on missing/invalid token)

Endpoint:
  GET /v1/auth/me — returns the authenticated user's profile row.
"""
from __future__ import annotations

import logging
import os

import jwt as pyjwt
from fastapi import APIRouter, Depends, Header, HTTPException

from db.client import get_client

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_token(authorization: str | None) -> dict:
    """Decode and verify a Supabase-issued JWT.

    Returns the decoded claims dict on success.
    Raises HTTPException(401) on any failure.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.removeprefix("Bearer ").strip()
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not secret:
        logger.error("SUPABASE_JWT_SECRET env var not set")
        raise HTTPException(status_code=500, detail="Auth configuration error")

    try:
        payload = pyjwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except pyjwt.PyJWTError as exc:
        logger.debug("JWT decode failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency — requires a valid Supabase JWT, else HTTP 401."""
    return _verify_token(authorization)


def get_optional_user(authorization: str | None = Header(default=None)) -> dict | None:
    """FastAPI dependency — returns None instead of raising 401 (public endpoints)."""
    if not authorization:
        return None
    try:
        return _verify_token(authorization)
    except HTTPException:
        return None


# ── /v1/auth/me ──────────────────────────────────────────────────────────────

@router.get("/v1/auth/me", tags=["Auth"])
def get_me(claims: dict = Depends(get_current_user)) -> dict:
    """Return the authenticated user's profile from user_profiles.

    Requires a valid Supabase JWT in `Authorization: Bearer <token>`.
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token claims")

    db = get_client()
    try:
        result = (
            db.table("user_profiles")
            .select("id, email, display_name, tier, created_at, updated_at")
            .eq("id", user_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error("user_profiles fetch failed for %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch user profile")

    if not result.data:
        raise HTTPException(status_code=404, detail="User profile not found")

    return result.data
