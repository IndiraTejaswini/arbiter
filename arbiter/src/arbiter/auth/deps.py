"""FastAPI dependency wrapping token decode -- the one place a route pulls
an `Actor` out of a request. See tokens.py's module docstring for the token
format and its honestly-scoped-for-a-hackathon status."""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from arbiter.config import get_settings

from .tokens import Actor, decode_token


def get_current_actor(authorization: Optional[str] = Header(default=None)) -> Actor:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token -- Authorization: Bearer <token> is required")
    token = authorization[len("Bearer "):]
    actor = decode_token(token, get_settings().auth_secret)
    if actor is None:
        raise HTTPException(401, "invalid or expired token")
    return actor
