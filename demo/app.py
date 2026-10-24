"""Secure QR attendance: FastAPI, SQLAlchemy, opaque one-time QR tokens."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import qrcode
import uvicorn
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).parent
# Set to 1 to start an ngrok HTTPS tunnel whenever `python app.py` starts.
# Install/configure the ngrok CLI first. Keep its auth token out of this file.
NGROK_ENABLED = 1
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
STATIC_DIR = ROOT / "static"
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_PASSWORD_HASH = password_hasher.hash("constant-invalid-login-password")


class QrType(str, Enum):
    PRODUCT = "PRODUCT"
    EVENT_CHECKIN = "EVENT_CHECKIN"
    DIGITAL_TICKET = "DIGITAL_TICKET"


class Base(DeclarativeBase):
    pass


class StaffUser(Base):
    __tablename__ = "staff_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="staff")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    public_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkin_latitude: Mapped[float | None] = mapped_column(nullable=True)
    checkin_longitude: Mapped[float | None] = mapped_column(nullable=True)
    checkin_radius_meters: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class EventStaff(Base):
    __tablename__ = "event_staff"
    __table_args__ = (UniqueConstraint("user_id", "event_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("staff_users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_event_status", "event_id", "status"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"), nullable=True, index=True)
    reference_id: Mapped[str] = mapped_column(String(100), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    public_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    qr_type: Mapped[str] = mapped_column(String(32), default="EVENT_CHECKIN")
    label: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ISSUED")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("staff_users.id"), nullable=True)
    holder: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seat: Mapped[str | None] = mapped_column(String(20), nullable=True)
    batch: Mapped[str | None] = mapped_column(String(50), nullable=True)
    info: Mapped[str | None] = mapped_column(String(300), nullable=True)


class CheckinLog(Base):
    __tablename__ = "checkin_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="RESTRICT"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"), index=True)
    staff_user_id: Mapped[int] = mapped_column(ForeignKey("staff_users.id", ondelete="RESTRICT"), index=True)
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class EventAttendance(Base):
    __tablename__ = "event_attendance"
    __table_args__ = (UniqueConstraint("event_id", "student_id", name="uq_event_attendance_event_student"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(String(100))
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _read_json(request: Request) -> Any:
    try:
        return await request.json()
    except Exception as exc:
        raise HTTPException(400, "Request body must be valid JSON") from exc


def encrypt_payload(payload: bytes, key: bytes, event_id: int, kid: str = "1") -> str:
    """Optional authenticated QRA1 payload; encryption does not stop QR replay."""
    if len(key) != 32:
        raise ValueError("AES-256 key must be exactly 32 bytes")
    nonce = secrets.token_bytes(12)
    aad = f"QRA1|{kid}|attendance|{event_id}".encode()
    cipher_and_tag = AESGCM(key).encrypt(nonce, payload, aad)
    encoded = base64.urlsafe_b64encode(nonce + cipher_and_tag).rstrip(b"=").decode()
    return f"QRA1.{kid}.{encoded}"


def decrypt_payload(value: str, keys: dict[str, bytes], event_id: int) -> bytes:
    try:
        prefix, kid, encoded = value.split(".", 2)
        if prefix != "QRA1" or kid not in keys:
            raise ValueError
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if len(raw) < 12 + 16:
            raise ValueError
        aad = f"QRA1|{kid}|attendance|{event_id}".encode()
        return AESGCM(keys[kid]).decrypt(raw[:12], raw[12:], aad)
    except Exception as exc:
        raise ValueError("Invalid QR payload") from exc


def create_app(test_config: dict[str, Any] | None = None) -> FastAPI:
    cfg: dict[str, Any] = {
        "DATABASE_URL": os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'attendance.db'}"),
        "SESSION_SECRET": os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48),
        "SESSION_HTTPS_ONLY": os.getenv("SESSION_HTTPS_ONLY", "0") == "1",
        "QR_PUBLIC_BASE_URL": os.getenv("QR_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
        "APP_ENV": os.getenv("APP_ENV", "development"),
        "TRUSTED_HOSTS": [h.strip() for h in os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()],
        "QR_VALIDITY_SECONDS": int(os.getenv("QR_VALIDITY_SECONDS", "86400")),
        "MAX_BODY_BYTES": int(os.getenv("MAX_BODY_BYTES", "16384")),
        "TESTING": False,
    }
    if test_config:
        cfg.update(test_config)
    if cfg["APP_ENV"] == "production" and (not os.getenv("SESSION_SECRET") or len(os.getenv("SESSION_SECRET", "")) < 32):
        raise RuntimeError("Production requires SESSION_SECRET with at least 32 characters")
    if cfg["APP_ENV"] == "production" and not cfg["SESSION_HTTPS_ONLY"]:
        raise RuntimeError("Production requires SESSION_HTTPS_ONLY=1")
    if cfg["APP_ENV"] == "production" and not cfg["QR_PUBLIC_BASE_URL"].startswith("https://"):
        raise RuntimeError("Production requires QR_PUBLIC_BASE_URL to use HTTPS")
    aes_env = os.getenv("QR_AES_KEYS")
    if aes_env:
        try:
            key_config = json.loads(aes_env)
            if not isinstance(key_config, dict) or not key_config or any(len(base64.b64decode(v, validate=True)) != 32 for v in key_config.values()):
                raise ValueError
        except Exception as exc:
            raise RuntimeError("QR_AES_KEYS must be a JSON map of key IDs to base64-encoded 32-byte keys") from exc

    connect_args = {"check_same_thread": False} if cfg["DATABASE_URL"].startswith("sqlite") else {}
    engine = create_engine(cfg["DATABASE_URL"], connect_args=connect_args, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    if cfg["TESTING"]:
        Base.metadata.create_all(engine)

    app = FastAPI(title="Secure QR Attendance", docs_url=None if cfg["APP_ENV"] == "production" else "/docs", redoc_url=None, openapi_url=None if cfg["APP_ENV"] == "production" else "/openapi.json")
    app.state.config, app.state.engine, app.state.session_factory = cfg, engine, factory
    app.add_middleware(SessionMiddleware, secret_key=cfg["SESSION_SECRET"], session_cookie="qr_session", same_site="lax", https_only=cfg["SESSION_HTTPS_ONLY"], max_age=8 * 60 * 60)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg["TRUSTED_HOSTS"] or ["localhost"])
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Small per-process throttle. Use a shared gateway/Redis limiter for multi-worker production.
    rate: dict[str, list[float]] = {}
    def limited(request: Request, key: str, maximum: int, seconds: int = 60) -> None:
        now = time.monotonic()
        bucket_key = f"{key}:{request.client.host if request.client else 'unknown'}"
        hits = [t for t in rate.get(bucket_key, []) if now - t < seconds]
        if len(hits) >= maximum:
            raise HTTPException(429, "Too many requests")
        hits.append(now)
        rate[bucket_key] = hits

    def db() -> Session:
        return factory()

    def actor(request: Request, session: Session) -> StaffUser:
        uid = request.session.get("uid")
        user = session.get(StaffUser, uid) if isinstance(uid, int) else None
        if not user or not user.active:
            raise HTTPException(401, "Authentication required")
        return user

    def require_csrf(request: Request) -> None:
        expected, supplied = request.session.get("csrf"), request.headers.get("x-csrf-token")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            raise HTTPException(403, "CSRF validation failed")

    def require_admin(user: StaffUser) -> None:
        if user.role != "admin":
            raise HTTPException(403, "Admin permission required")

    def audit(session: Session, user_id: int | None, action: str, target_type: str, target_id: str, details: dict[str, Any] | None = None) -> None:
        session.add(AuditLog(actor_id=user_id, action=action, target_type=target_type, target_id=str(target_id), details=json.dumps(details or {}, ensure_ascii=False)))

    @app.middleware("http")
    async def hardening(request: Request, call_next):
        if int(request.headers.get("content-length", "0") or 0) > cfg["MAX_BODY_BYTES"]:
            return JSONResponse({"error": "Request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(self)", "Cache-Control": "no-store"})
        if cfg["APP_ENV"] == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return TEMPLATES.TemplateResponse(request, "index.html", {})

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "login.html", {})

    @app.get("/p/{public_id}", response_class=HTMLResponse)
    def public_product_page(request: Request, public_id: str):
        with db() as session:
            ticket = session.scalar(select(Ticket).where(Ticket.public_id == public_id, Ticket.qr_type == QrType.PRODUCT.value))
            if not ticket or ticket.archived_at or ticket.status == "REVOKED":
                raise HTTPException(404, "Product not found")
            product = {"referenceId": ticket.reference_id, "label": ticket.label, "batch": ticket.batch, "info": ticket.info}
        return TEMPLATES.TemplateResponse(request, "product.html", {"product": product})

    @app.get("/v/{public_id}", response_class=HTMLResponse)
    def public_ticket_page(request: Request, public_id: str):
        with db() as session:
            ticket = session.scalar(select(Ticket).where(Ticket.public_id == public_id, Ticket.qr_type == QrType.DIGITAL_TICKET.value))
            if not ticket or ticket.archived_at:
                raise HTTPException(404, "Ticket not found")
            state = _public_ticket_status(ticket)
            event = session.get(Event, ticket.event_id) if ticket.event_id else None
            ticket_url = f"{cfg['QR_PUBLIC_BASE_URL']}/v/{ticket.public_id}"
            details = {"status": state, "eventName": event.name if event else "Sự kiện", "expiresAt": ticket.expires_at.isoformat(),
                "contactSaved": bool(ticket.contact_email and ticket.contact_phone),
                "referenceId": ticket.reference_id,
                "ticketUrl": ticket_url,
                "qrImagePngBase64": base64.b64encode(_qr_png(ticket_url, cfg)).decode("ascii") if ticket.contact_email and ticket.contact_phone else None}
        return TEMPLATES.TemplateResponse(request, "verify_ticket.html", {"ticket": details})

    @app.get("/e/{public_id}", response_class=HTMLResponse)
    def public_event_checkin_page(request: Request, public_id: str):
        with db() as session:
            event = session.scalar(select(Event).where(Event.public_id == public_id, Event.active.is_(True)))
            if not event:
                raise HTTPException(404, "Event check-in QR not found")
            details = {"eventName": event.name, "locationRequired": event.checkin_latitude is not None and event.checkin_longitude is not None}
        return TEMPLATES.TemplateResponse(request, "checkin_confirm.html", {"ticket": details})

    @app.get("/scanner", response_class=HTMLResponse)
    def scanner_page(request: Request):
        with db() as session:
            user = actor(request, session)
            if user.role not in ("staff", "admin"):
                raise HTTPException(403, "Staff permission required")
        return TEMPLATES.TemplateResponse(request, "scanner.html", {})

    @app.get("/api/csrf")
    def csrf(request: Request):
        request.session["csrf"] = secrets.token_urlsafe(32)
        return {"csrfToken": request.session["csrf"]}

    @app.get("/api/session")
    def session_info(request: Request):
        with db() as session:
            user = actor(request, session)
            events = session.scalars(select(Event).where(Event.active.is_(True))).all()
            if user.role != "admin":
                ids = set(session.scalars(select(EventStaff.event_id).where(EventStaff.user_id == user.id)).all())
                events = [event for event in events if event.id in ids]
            return {"username": user.username, "role": user.role, "events": [{"id": e.id, "name": e.name} for e in events]}

    @app.post("/api/login")
    async def login(request: Request):
        require_csrf(request)
        limited(request, "login", 8)
        try:
            body = await request.json()
            username, password = body.get("username", ""), body.get("password", "")
            if not isinstance(username, str) or not isinstance(password, str): raise ValueError
        except Exception:
            raise HTTPException(400, "Invalid login request")
        with db() as session:
            user = session.scalar(select(StaffUser).where(StaffUser.username == username.strip().lower()))
            # Verify a fixed dummy hash when the account is absent to reduce username timing leaks.
            if user:
                try: password_hasher.verify(user.password_hash, password)
                except VerifyMismatchError: user = None
            else:
                try: password_hasher.verify(DUMMY_PASSWORD_HASH, password)
                except VerifyMismatchError: pass
            if not user or not user.active:
                raise HTTPException(401, "Invalid username or password")
            request.session.clear()
            request.session["uid"] = user.id
            request.session["csrf"] = secrets.token_urlsafe(32)
            audit(session, user.id, "login", "staff_user", user.id)
            session.commit()
            return {"result": "OK", "csrfToken": request.session["csrf"], "role": user.role}

    @app.post("/api/logout")
    def logout(request: Request):
        require_csrf(request)
        request.session.clear()
        return {"result": "OK"}

    @app.post("/api/events", status_code=201)
    async def create_event(request: Request):
        require_csrf(request)
        body = await _read_json(request)
        if not isinstance(body, dict): raise HTTPException(400, "JSON object is required")
        with db() as session:
            user = actor(request, session); require_admin(user)
            name = body.get("name", "").strip() if isinstance(body, dict) else ""
            if not name or len(name) > 160: raise HTTPException(400, "Event name is required")
            settings = _event_settings(body)
            event = Event(name=name, public_id=secrets.token_urlsafe(18), **settings)
            session.add(event); session.flush(); audit(session, user.id, "event.create", "event", event.id); session.commit()
            checkin_url, qr_png = _event_checkin_qr(event, cfg)
            return {"id": event.id, "name": event.name, "selfCheckinEnabled": event.checkin_latitude is not None,
                "radiusMeters": event.checkin_radius_meters, "checkinUrl": checkin_url,
                "imagePngBase64": base64.b64encode(qr_png).decode("ascii") if qr_png else None}

    @app.patch("/api/events/{event_id}/settings")
    async def update_event_settings(event_id: int, request: Request):
        require_csrf(request)
        body = await _read_json(request)
        if not isinstance(body, dict): raise HTTPException(400, "JSON object is required")
        settings = _event_settings(body)
        with db() as session:
            user = actor(request, session); require_admin(user)
            event = session.get(Event, event_id)
            if not event: raise HTTPException(404, "Event not found")
            for key, value in settings.items(): setattr(event, key, value)
            audit(session, user.id, "event.checkin_settings", "event", event.id,
                {"selfCheckinEnabled": event.checkin_latitude is not None, "radiusMeters": event.checkin_radius_meters})
            session.commit()
            checkin_url, qr_png = _event_checkin_qr(event, cfg)
            return {"id": event.id, "name": event.name, "selfCheckinEnabled": event.checkin_latitude is not None,
                "radiusMeters": event.checkin_radius_meters, "checkinUrl": checkin_url,
                "imagePngBase64": base64.b64encode(qr_png).decode("ascii") if qr_png else None}

    @app.get("/api/events/{event_id}/settings")
    def get_event_settings(event_id: int, request: Request):
        with db() as session:
            user = actor(request, session); require_admin(user)
            event = session.get(Event, event_id)
            if not event: raise HTTPException(404, "Event not found")
            checkin_url, qr_png = _event_checkin_qr(event, cfg)
            return {"id": event.id, "name": event.name, "latitude": event.checkin_latitude,
                "longitude": event.checkin_longitude, "radiusMeters": event.checkin_radius_meters,
                "startsAt": _utc(event.starts_at).isoformat() if event.starts_at else None,
                "endsAt": _utc(event.ends_at).isoformat() if event.ends_at else None,
                "selfCheckinEnabled": event.checkin_latitude is not None, "checkinUrl": checkin_url,
                "imagePngBase64": base64.b64encode(qr_png).decode("ascii") if qr_png else None}

    @app.post("/api/qr", status_code=201)
    async def create_qr(request: Request):
        require_csrf(request); limited(request, "issue", 30)
        body = await _read_json(request)
        if not isinstance(body, dict): raise HTTPException(400, "JSON object is required")
        try: qr_type = QrType(body.get("type"))
        except (ValueError, TypeError): raise HTTPException(400, "Invalid QR type")
        ref, label = body.get("referenceId"), body.get("label")
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 100 or not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise HTTPException(400, "referenceId and label are required")
        with db() as session:
            user = actor(request, session); require_admin(user)
            event_id = body.get("eventId")
            event = None
            if qr_type != QrType.PRODUCT:
                event = session.get(Event, event_id) if isinstance(event_id, int) else None
                if not event or not event.active: raise HTTPException(400, "A valid active eventId is required")
            token = secrets.token_urlsafe(32)
            public_id = secrets.token_urlsafe(18) if qr_type in (QrType.PRODUCT, QrType.DIGITAL_TICKET) else None
            expires = datetime.max.replace(tzinfo=timezone.utc) if qr_type == QrType.PRODUCT else datetime.now(timezone.utc) + timedelta(seconds=cfg["QR_VALIDITY_SECONDS"])
            ticket = Ticket(event_id=event.id if event else None, reference_id=ref.strip(), token_hash=token_digest(token), public_id=public_id, qr_type=qr_type.value, label=label.strip(), expires_at=expires,
                holder=_optional(body, "holder", 100), seat=_optional(body, "seat", 20), batch=_optional(body, "batch", 50), info=_optional(body, "info", 300))
            session.add(ticket); session.flush(); audit(session, user.id, "ticket.issue", "ticket", ticket.id, {"type": qr_type.value, "eventId": event.id if event else None}); session.commit()
        public_url = None
        qr_content = token
        if qr_type == QrType.PRODUCT and public_id:
            public_url = f"{cfg['QR_PUBLIC_BASE_URL']}/p/{public_id}"
            qr_content = public_url
        elif qr_type == QrType.DIGITAL_TICKET and public_id:
            public_url = f"{cfg['QR_PUBLIC_BASE_URL']}/v/{public_id}"
            qr_content = public_url
        png = _qr_png(qr_content, cfg)
        return {"token": token, "type": qr_type.value, "referenceId": ticket.reference_id, "label": ticket.label,
                "publicUrl": public_url, "imagePngBase64": base64.b64encode(png).decode("ascii"), "expiresAt": expires.isoformat(), "eventId": ticket.event_id}

    @app.get("/api/tickets")
    def list_tickets(request: Request):
        with db() as session:
            user = actor(request, session); require_admin(user)
            tickets = session.scalars(select(Ticket).where(Ticket.archived_at.is_(None)).order_by(Ticket.issued_at.desc()).limit(100)).all()
            return {"tickets": [_ticket_json(t) for t in tickets]}

    @app.post("/api/products/lookup")
    async def lookup_product(request: Request):
        require_csrf(request)
        body = await _read_json(request)
        if not isinstance(body, dict) or not isinstance(body.get("token"), str) or not body["token"].strip():
            raise HTTPException(400, "Product QR token is required")
        with db() as session:
            user = actor(request, session); require_admin(user)
            ticket = session.scalar(select(Ticket).where(Ticket.token_hash == token_digest(body["token"].strip())))
            if not ticket or ticket.archived_at or ticket.qr_type != QrType.PRODUCT.value:
                raise HTTPException(404, "Product QR not found")
            if ticket.status == "REVOKED":
                raise HTTPException(410, "Product QR expired or revoked")
            return {"referenceId": ticket.reference_id, "label": ticket.label, "batch": ticket.batch, "info": ticket.info, "expiresAt": None, "status": ticket.status}

    @app.get("/api/products/{public_id}")
    def public_product(public_id: str):
        with db() as session:
            ticket = session.scalar(select(Ticket).where(Ticket.public_id == public_id, Ticket.qr_type == QrType.PRODUCT.value))
            if not ticket or ticket.archived_at or ticket.status == "REVOKED":
                raise HTTPException(404, "Product not found")
            return {"referenceId": ticket.reference_id, "label": ticket.label, "batch": ticket.batch, "info": ticket.info, "status": "ACTIVE"}

    @app.get("/api/tickets/public/{public_id}")
    def public_ticket_status(public_id: str):
        with db() as session:
            ticket = session.scalar(select(Ticket).where(Ticket.public_id == public_id, Ticket.qr_type == QrType.DIGITAL_TICKET.value))
            if not ticket or ticket.archived_at:
                raise HTTPException(404, "Ticket not found")
            return {"type": ticket.qr_type, "status": _public_ticket_status(ticket), "expiresAt": ticket.expires_at.isoformat()}

    @app.post("/api/tickets/public/{public_id}/contact")
    async def register_ticket_contact(public_id: str, request: Request):
        require_csrf(request); limited(request, "ticket_contact", 10)
        body = await _read_json(request)
        if not isinstance(body, dict): raise HTTPException(400, "Contact details are required")
        email = body.get("email")
        phone = body.get("phone")
        email_pattern = r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+"
        email = email.strip() if isinstance(email, str) else email
        email_local = email.split("@", 1)[0] if isinstance(email, str) and "@" in email else ""
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(email_pattern, email, re.IGNORECASE) or email_local.startswith(".") or email_local.endswith(".") or ".." in email_local:
            raise HTTPException(400, "Enter a valid email address")
        if not isinstance(phone, str): raise HTTPException(400, "Enter a valid phone number")
        phone = phone.strip()
        compact_phone = re.sub(r"[\s().-]", "", phone)
        phone_match = re.fullmatch(r"0([35789][0-9]{8})", compact_phone)
        if phone_match is None:
            phone_match = re.fullmatch(r"(?:\+84|84|0084)(?:0)?([35789][0-9]{8})", compact_phone)
        if len(phone) > 32 or phone_match is None:
            raise HTTPException(400, "Enter a valid phone number")
        normalized_phone = "+84" + phone_match.group(1)
        with db() as session:
            now = datetime.now(timezone.utc)
            ticket = session.scalar(select(Ticket).where(Ticket.public_id == public_id, Ticket.qr_type == QrType.DIGITAL_TICKET.value, Ticket.archived_at.is_(None)))
            if not ticket: raise HTTPException(404, "Ticket not found")
            if ticket.status != "ISSUED" or _utc(ticket.expires_at) <= now:
                raise HTTPException(410, "This ticket is no longer available for registration")
            if ticket.contact_email or ticket.contact_phone:
                raise HTTPException(409, "Contact details have already been saved for this ticket")
            result = session.execute(update(Ticket).where(Ticket.id == ticket.id, Ticket.status == "ISSUED",
                Ticket.archived_at.is_(None), Ticket.expires_at > now, Ticket.contact_email.is_(None), Ticket.contact_phone.is_(None))
                .values(contact_email=email.strip().lower(), contact_phone=normalized_phone).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                session.rollback()
                raise HTTPException(409, "This ticket was already registered or is no longer available")
            audit(session, None, "digital_ticket.contact_saved", "ticket", ticket.id, {"contactProvided": True})
            session.commit()
            ticket_url = f"{cfg['QR_PUBLIC_BASE_URL']}/v/{ticket.public_id}"
            return {"result": "SAVED", "referenceId": ticket.reference_id,
                "ticketUrl": ticket_url,
                "imagePngBase64": base64.b64encode(_qr_png(ticket_url, cfg)).decode("ascii")}

    @app.post("/api/event-self-check-ins")
    async def event_self_check_in(request: Request):
        require_csrf(request); limited(request, "event_self_checkin", 300)
        body = await _read_json(request)
        if not isinstance(body, dict) or not isinstance(body.get("eventPublicId"), str) or not isinstance(body.get("studentId"), str):
            raise HTTPException(400, "Event QR and student ID are required")
        student_display = body["studentId"].strip()
        student_id = student_display.casefold()
        if not student_id or len(student_id) > 100:
            raise HTTPException(400, "Enter a valid student ID")
        latitude, longitude = body.get("latitude"), body.get("longitude")
        if (latitude is None) != (longitude is None): raise HTTPException(400, "Send both location coordinates")
        if latitude is not None:
            if isinstance(latitude, bool) or isinstance(longitude, bool): raise HTTPException(400, "Invalid location")
            try: latitude, longitude = float(latitude), float(longitude)
            except (TypeError, ValueError): raise HTTPException(400, "Invalid location")
            if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise HTTPException(400, "Invalid location")
        public_id = body["eventPublicId"].strip()
        if not 16 <= len(public_id) <= 32 or not all(c.isalnum() or c in "-_" for c in public_id):
            raise HTTPException(400, "Event QR is invalid")
        now = datetime.now(timezone.utc)
        with db() as session:
            event = session.scalar(select(Event).where(Event.public_id == public_id, Event.active.is_(True)))
            if not event: raise HTTPException(404, "Event not found or inactive")
            if event.starts_at and _utc(event.starts_at) > now: raise HTTPException(403, "Event check-in has not opened yet")
            if event.ends_at and _utc(event.ends_at) <= now: raise HTTPException(410, "Event check-in is closed")
            if event.checkin_latitude is not None and event.checkin_longitude is not None:
                if latitude is None or longitude is None: raise HTTPException(400, "Location permission is required for this event")
                distance = _distance_meters(latitude, longitude, event.checkin_latitude, event.checkin_longitude)
                if distance > event.checkin_radius_meters:
                    raise HTTPException(403, f"You are outside the event check-in area ({round(distance)} m away)")
            session.add(EventAttendance(event_id=event.id, student_id=student_id, checked_in_at=now))
            audit(session, None, "event.self_checkin", "event", event.id)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(409, "This student ID has already checked in") from exc
            return {"result": "CHECKED_IN", "eventName": event.name, "studentId": student_display, "checkedInAt": now.isoformat()}

    @app.post("/api/tickets/{ticket_id}/revoke")
    def revoke_ticket(ticket_id: int, request: Request):
        require_csrf(request)
        with db() as session:
            user = actor(request, session); require_admin(user)
            ticket = session.get(Ticket, ticket_id)
            if not ticket: raise HTTPException(404, "Ticket not found")
            if ticket.status == "USED": raise HTTPException(409, "Used ticket cannot be revoked")
            if ticket.status != "REVOKED":
                ticket.status, ticket.revoked_at = "REVOKED", datetime.now(timezone.utc)
                audit(session, user.id, "ticket.revoke", "ticket", ticket.id); session.commit()
            return {"result": "REVOKED"}

    @app.delete("/api/tickets/{ticket_id}")
    def archive_ticket(ticket_id: int, request: Request):
        require_csrf(request)
        with db() as session:
            user = actor(request, session); require_admin(user)
            ticket = session.get(Ticket, ticket_id)
            if not ticket or ticket.archived_at:
                raise HTTPException(404, "QR not found")
            now = datetime.now(timezone.utc)
            ticket.archived_at = now
            if ticket.status == "ISSUED":
                ticket.status, ticket.revoked_at = "REVOKED", now
            audit(session, user.id, "ticket.archive", "ticket", ticket.id)
            session.commit()
            return {"result": "DELETED"}

    @app.post("/api/check-ins")
    async def check_in(request: Request):
        require_csrf(request); limited(request, "checkin", 60)
        body = await _read_json(request)
        if not isinstance(body, dict) or not isinstance(body.get("token"), str): raise HTTPException(400, "QR payload is required")
        token = body["token"].strip()
        public_id = _ticket_public_id(token, cfg["QR_PUBLIC_BASE_URL"])
        if not token or len(token) > 512 or (not public_id and ("." in token or "://" in token)):
            raise HTTPException(400, "Malformed QR payload")
        now = datetime.now(timezone.utc)
        with db() as session:
            user = actor(request, session)
            if user.role not in ("staff", "admin"): raise HTTPException(403, "Staff permission required")
            ticket = session.scalar(select(Ticket).where(Ticket.public_id == public_id)) if public_id else session.scalar(select(Ticket).where(Ticket.token_hash == token_digest(token)))
            if not ticket or ticket.archived_at or ticket.qr_type == QrType.PRODUCT.value: raise HTTPException(404, "Ticket not found")
            if ticket.event_id is None: raise HTTPException(400, "Ticket has no event")
            event = session.get(Event, ticket.event_id)
            if not event or not event.active: raise HTTPException(404, "Event not found or inactive")
            if event.starts_at and _utc(event.starts_at) > now: raise HTTPException(403, "Event check-in has not opened yet")
            if event.ends_at and _utc(event.ends_at) <= now: raise HTTPException(410, "Event check-in is closed")
            if user.role != "admin" and session.scalar(select(EventStaff.id).where(EventStaff.user_id == user.id, EventStaff.event_id == ticket.event_id)) is None:
                raise HTTPException(403, "No access to this event")
            if _utc(ticket.expires_at) <= now: raise HTTPException(410, "Ticket expired")
            if ticket.status == "REVOKED" or ticket.revoked_at: raise HTTPException(410, "Ticket revoked")
            result = session.execute(update(Ticket).where(Ticket.id == ticket.id, Ticket.status == "ISSUED", Ticket.used_at.is_(None), Ticket.revoked_at.is_(None), Ticket.expires_at > now).values(status="USED", used_at=now, used_by=user.id).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                session.rollback()
                with db() as inspect:
                    latest = inspect.get(Ticket, ticket.id)
                    if latest and _utc(latest.expires_at) <= now: raise HTTPException(410, "Ticket expired")
                    if latest and (latest.status == "REVOKED" or latest.revoked_at): raise HTTPException(410, "Ticket revoked")
                raise HTTPException(409, "Ticket already used")
            session.add(CheckinLog(ticket_id=ticket.id, event_id=ticket.event_id, staff_user_id=user.id, checked_in_at=now))
            audit(session, user.id, "ticket.checkin", "ticket", ticket.id, {"eventId": ticket.event_id})
            session.commit()
            return {"result": "CHECKED_IN", "referenceId": ticket.reference_id, "checkedInAt": now.isoformat()}

    @app.get("/api/stats")
    def stats(request: Request):
        with db() as session:
            user = actor(request, session); require_admin(user)
            tickets = session.scalars(select(Ticket).where(Ticket.qr_type != "PRODUCT", Ticket.archived_at.is_(None))).all()
            checked = [t for t in tickets if t.status == "USED"]
            self_checkins = len(session.scalars(select(EventAttendance.id)).all())
            recent = sorted(checked, key=lambda t: t.used_at or t.issued_at, reverse=True)[:20]
            return {"checkedIn": len(checked) + self_checkins, "tickets": len(tickets) + self_checkins,
                "issued": sum(t.status == "ISSUED" for t in tickets), "selfCheckins": self_checkins,
                "recent": [{"label": t.label, "referenceId": t.reference_id, "type": t.qr_type, "checkedInAt": t.used_at.isoformat() if t.used_at else None} for t in recent]}

    @app.get("/api/check-ins")
    def checkin_history(request: Request):
        with db() as session:
            user = actor(request, session); require_admin(user)
            staff_rows = session.execute(select(CheckinLog, Ticket.reference_id, StaffUser.username).select_from(CheckinLog).join(Ticket, CheckinLog.ticket_id == Ticket.id).join(StaffUser, CheckinLog.staff_user_id == StaffUser.id).where(Ticket.archived_at.is_(None)).order_by(CheckinLog.checked_in_at.desc()).limit(100)).all()
            items = [{"referenceId": ref, "staff": name, "checkedInAt": log.checked_in_at.isoformat(), "eventId": log.event_id} for log, ref, name in staff_rows]
            self_rows = session.scalars(select(EventAttendance).order_by(EventAttendance.checked_in_at.desc()).limit(100)).all()
            items.extend({"referenceId": row.student_id, "staff": "Tự điểm danh", "checkedInAt": row.checked_in_at.isoformat(), "eventId": row.event_id} for row in self_rows)
            def history_time(item):
                return _utc(datetime.fromisoformat(item["checkedInAt"].replace("Z", "+00:00")))
            return {"items": sorted(items, key=history_time, reverse=True)[:100]}

    @app.delete("/api/check-ins/history")
    def clear_checkin_history(request: Request):
        require_csrf(request)
        with db() as session:
            user = actor(request, session); require_admin(user)
            staff_count = session.execute(delete(CheckinLog)).rowcount or 0
            self_count = session.execute(delete(EventAttendance)).rowcount or 0
            audit(session, user.id, "checkin_history.clear", "attendance", "all",
                {"staffCheckinsRemoved": staff_count, "selfCheckinsRemoved": self_count})
            session.commit()
            return {"result": "CLEARED", "staffCheckinsRemoved": staff_count, "selfCheckinsRemoved": self_count}

    @app.get("/api/audit")
    def audit_history(request: Request):
        with db() as session:
            user = actor(request, session); require_admin(user)
            rows = session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)).all()
            return {"items": [{"action": r.action, "targetType": r.target_type, "targetId": r.target_id, "details": json.loads(r.details), "createdAt": r.created_at.isoformat()} for r in rows]}

    # Legacy endpoints deliberately remain available but require admin authentication.
    @app.get("/api/qr/resolve/{token}")
    def resolve_qr(token: str, request: Request):
        raise HTTPException(410, "Token-in-URL lookup was removed; use admin ticket management")

    @app.get("/api/qr/image/{token}")
    def qr_image(token: str, request: Request):
        raise HTTPException(410, "Token-in-URL image lookup was removed; save the PNG at issuance")

    @app.get("/t/{token}")
    def old_ticket_page(token: str):
        # Prevent legacy public ticket URLs from acting as bearer credentials.
        raise HTTPException(410, "Ticket links were removed; use the authenticated scanner")

    @app.post("/api/reset")
    def reset_removed():
        raise HTTPException(410, "Reset is removed because it could re-enable used tickets")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def _optional(body: dict, key: str, limit: int) -> str | None:
    value = body.get(key)
    if value is None or value == "": return None
    if not isinstance(value, str) or len(value.strip()) > limit: raise HTTPException(400, f"Invalid {key}")
    return value.strip() or None


def _event_settings(body: dict) -> dict[str, Any]:
    latitude, longitude = body.get("latitude"), body.get("longitude")
    if (latitude is None) != (longitude is None):
        raise HTTPException(400, "Set both latitude and longitude to enable self check-in")
    if latitude is not None:
        if isinstance(latitude, bool) or isinstance(longitude, bool): raise HTTPException(400, "Invalid event coordinates")
        try: latitude, longitude = float(latitude), float(longitude)
        except (TypeError, ValueError): raise HTTPException(400, "Invalid event coordinates")
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise HTTPException(400, "Event coordinates are out of range")
    radius = body.get("radiusMeters", 100)
    if isinstance(radius, bool) or not isinstance(radius, int) or not 20 <= radius <= 2000:
        raise HTTPException(400, "Check-in radius must be between 20 and 2000 meters")

    def parse_datetime(value, field):
        if value in (None, ""): return None
        if not isinstance(value, str): raise HTTPException(400, f"Invalid {field}")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError as exc:
            raise HTTPException(400, f"Invalid {field}") from exc

    starts_at, ends_at = parse_datetime(body.get("startsAt"), "startsAt"), parse_datetime(body.get("endsAt"), "endsAt")
    if starts_at and ends_at and ends_at <= starts_at:
        raise HTTPException(400, "Event end time must be after start time")
    if latitude is not None and (starts_at is None or ends_at is None):
        raise HTTPException(400, "GPS self check-in requires both opening and closing times")
    return {"starts_at": starts_at, "ends_at": ends_at, "checkin_latitude": latitude,
        "checkin_longitude": longitude, "checkin_radius_meters": radius}


def _utc(value: datetime) -> datetime:
    """SQLite returns naive values for timezone=True columns; normalize as UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate great-circle distance between two GPS coordinates."""
    earth_radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi, delta_lambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    value = min(1.0, max(0.0, value))
    return earth_radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _public_ticket_status(ticket: Ticket) -> str:
    if ticket.status == "REVOKED" or ticket.revoked_at:
        return "REVOKED"
    if _utc(ticket.expires_at) <= datetime.now(timezone.utc):
        return "EXPIRED"
    return "USED" if ticket.used_at or ticket.status == "USED" else "VALID"


def _ticket_public_id(payload: str, base_url: str) -> str | None:
    """Accept only this app's public DIGITAL_TICKET URL, never arbitrary URLs."""
    try:
        parsed, expected = urlsplit(payload), urlsplit(base_url)
        if parsed.scheme != expected.scheme or parsed.netloc.lower() != expected.netloc.lower():
            return None
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] in ("p", "v") and 0 < len(parts[1]) <= 32 and all(c.isalnum() or c in "-_" for c in parts[1]):
            return parts[1]
    except Exception:
        return None
    return None


def _qr_png(content: str, config: dict) -> bytes:
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=int(os.getenv("QR_SIZE", "8")), border=int(os.getenv("QR_BORDER", "4")))
    code.add_data(content); code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")
    output = io.BytesIO(); image.save(output, format="PNG")
    return output.getvalue()


def _event_checkin_qr(event: Event, config: dict) -> tuple[str | None, bytes | None]:
    if not event.public_id:
        return None, None
    url = f"{config['QR_PUBLIC_BASE_URL']}/e/{event.public_id}"
    return url, _qr_png(url, config)


def _ticket_json(ticket: Ticket) -> dict[str, Any]:
    return {"id": ticket.id, "eventId": ticket.event_id, "type": ticket.qr_type, "referenceId": ticket.reference_id, "label": ticket.label, "status": ticket.status, "issuedAt": ticket.issued_at.isoformat(), "expiresAt": None if ticket.qr_type == QrType.PRODUCT.value else ticket.expires_at.isoformat(), "usedAt": ticket.used_at.isoformat() if ticket.used_at else None, "revokedAt": ticket.revoked_at.isoformat() if ticket.revoked_at else None, "contactEmail": ticket.contact_email, "contactPhone": ticket.contact_phone}


def create_staff(username: str, password: str, role: str, event_id: int | None = None, database_url: str | None = None) -> None:
    url = database_url or os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'attendance.db'}")
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        existing = session.scalar(select(StaffUser).where(StaffUser.username == username.lower()))
        if existing: raise SystemExit("Username already exists")
        user = StaffUser(username=username.lower(), password_hash=password_hasher.hash(password), role=role)
        session.add(user); session.flush()
        if role == "staff":
            if event_id is None or session.get(Event, event_id) is None: raise SystemExit("Staff account requires an existing --event-id")
            session.add(EventStaff(user_id=user.id, event_id=event_id))
        print(f"Created {role} account: {username.lower()}")


def start_ngrok(port: int, timeout: float = 30.0) -> tuple[subprocess.Popen, str]:
    """Start ngrok CLI and return its HTTPS URL from the local agent API."""
    executable = os.getenv("NGROK_EXECUTABLE") or shutil.which("ngrok")
    if not executable:
        raise SystemExit("NGROK_ENABLED=1, but ngrok CLI was not found in PATH. Install ngrok and configure its auth token first.")
    try:
        probe = subprocess.run([executable, "version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot run ngrok at {executable}: {exc}. Install the official ngrok Windows agent and reopen PowerShell.") from exc
    if probe.returncode != 0:
        details = (probe.stderr or probe.stdout or "no version output").strip()
        raise RuntimeError(f"ngrok command at {executable} did not run: {details}. Install the official ngrok Windows agent.")
    process = subprocess.Popen(
        [executable, "http", str(port), "--log", "stdout"],
        stdin=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("ngrok exited before creating a tunnel. See the ngrok message above; check the CLI and its configured auth token.")
            try:
                with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as response:
                    tunnels = json.load(response).get("tunnels", [])
                url = next((item.get("public_url", "") for item in tunnels if item.get("public_url", "").startswith("https://")), "")
                if url:
                    return process, url.rstrip("/")
            except (OSError, urllib.error.URLError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Timed out waiting for ngrok to provide an HTTPS URL.")
    except Exception:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise


app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Secure QR Attendance")
    parser.add_argument("--create-user", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--role", choices=("admin", "staff"), default="admin")
    parser.add_argument("--event-id", type=int)
    args = parser.parse_args()
    if args.create_user:
        import getpass
        username = args.username or input("Username: ").strip()
        password = getpass.getpass("Password (12+ characters): ")
        if len(password) < 12: raise SystemExit("Password must have at least 12 characters")
        create_staff(username, password, args.role, args.event_id)
    else:
        host = os.getenv("HOST", "127.0.0.1")
        port = int(os.getenv("PORT", "8000"))
        tunnel_process = None
        if NGROK_ENABLED == 1:
            try:
                tunnel_process, public_url = start_ngrok(port)
            except (RuntimeError, OSError) as exc:
                raise SystemExit(f"Could not start ngrok: {exc}") from exc
            app.state.config["QR_PUBLIC_BASE_URL"] = public_url
            # Only trust the hostname issued by this tunnel; secure session cookies
            # because browsers reach the app over the HTTPS ngrok URL.
            public_host = urlsplit(public_url).hostname
            for middleware in app.user_middleware:
                if middleware.cls is TrustedHostMiddleware:
                    middleware.kwargs["allowed_hosts"] = [public_host, "localhost", "127.0.0.1"]
                elif middleware.cls is SessionMiddleware:
                    middleware.kwargs["https_only"] = True
            app.state.config["SESSION_HTTPS_ONLY"] = True
            print(f"ngrok HTTPS URL: {public_url}")
            print("QR mới sẽ dùng URL này. URL có thể đổi khi khởi động lại ngrok.")
        try:
            uvicorn.run(app, host=host, port=port)
        finally:
            if tunnel_process and tunnel_process.poll() is None:
                tunnel_process.terminate()
                try:
                    tunnel_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    tunnel_process.kill()
