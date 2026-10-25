"""Authentication, CSRF, rate limiting, audit trail and QR device binding."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AuditLog, StaffUser

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_PASSWORD_HASH = password_hasher.hash("constant-invalid-login-password")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def device_signature(token: str, device_id: str, secret: str) -> str:
    """Bind a QR to one device: HMAC-SHA256 over token+device id, truncated to 16 hex."""
    mac = hmac.new(
        secret.encode(), f"qr-device|{token}|{device_id}".encode(), hashlib.sha256
    )
    return mac.hexdigest()[:16]


def normalize_device_id(value: Any) -> str | None:
    """Device ids are opaque 8-64 char tokens; reject anything that looks like an injection."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not 8 <= len(value) <= 64 or not all(c.isalnum() or c in "-_" for c in value):
        raise HTTPException(400, "Invalid deviceId")
    return value


def db(request: Request) -> Session:
    return request.app.state.session_factory()


def limited(request: Request, key: str, maximum: int, seconds: int = 60) -> None:
    """Per-process throttle; multi-worker deployments need Redis or a gateway limiter."""
    now = time.monotonic()
    bucket = f"{key}:{request.client.host if request.client else 'unknown'}"
    rate = request.app.state.rate
    hits = [t for t in rate.get(bucket, []) if now - t < seconds]
    if len(hits) >= maximum:
        raise HTTPException(429, "Too many requests")
    hits.append(now)
    rate[bucket] = hits


def authenticate(session: Session, username: str, password: str) -> StaffUser | None:
    """Verify credentials, doing the same work for unknown accounts (no timing leak)."""
    user = session.scalar(
        select(StaffUser).where(StaffUser.username == username.strip().lower())
    )
    try:
        password_hasher.verify(
            user.password_hash if user else DUMMY_PASSWORD_HASH, password
        )
    except VerifyMismatchError:
        return None
    return user if user and user.active else None


def actor(request: Request, session: Session) -> StaffUser:
    uid = request.session.get("uid")
    user = session.get(StaffUser, uid) if isinstance(uid, int) else None
    if not user or not user.active:
        raise HTTPException(401, "Authentication required")
    return user


def require_csrf(request: Request) -> None:
    expected = request.session.get("csrf")
    supplied = request.headers.get("x-csrf-token")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(403, "CSRF validation failed")


def require_admin(user: StaffUser) -> None:
    if user.role != "admin":
        raise HTTPException(403, "Admin permission required")


def audit(
    session: Session,
    user_id: int | None,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=json.dumps(details or {}, ensure_ascii=False),
        )
    )
