"""HTTP routes grouped by audience: public, staff and admin."""

from __future__ import annotations

import base64
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from models import (
    AuditLog,
    CheckinLog,
    Event,
    EventAttendance,
    EventStaff,
    QrType,
    StaffUser,
    Ticket,
)
from security import (
    actor,
    audit,
    authenticate,
    db,
    device_signature,
    limited,
    normalize_device_id,
    require_admin,
    require_csrf,
    token_digest,
)
from utils import (
    _coordinates,
    _distance_meters,
    _event_settings,
    _find_product,
    _find_public_ticket,
    _optional,
    _public_ticket_status,
    _qr_png,
    _ticket_public_id,
    _utc,
)

ROOT = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))

public_router = APIRouter()
staff_router = APIRouter()
admin_router = APIRouter()


def _cfg(request: Request) -> dict[str, Any]:
    return request.app.state.config


def _event_qr(event: Event, cfg: dict[str, Any]) -> dict[str, Any]:
    url = (
        f"{cfg['QR_PUBLIC_BASE_URL']}/e/{event.public_id}" if event.public_id else None
    )
    png = _qr_png(url) if url else None
    return {
        "id": event.id,
        "name": event.name,
        "selfCheckinEnabled": event.checkin_latitude is not None,
        "radiusMeters": event.checkin_radius_meters,
        "checkinUrl": url,
        "imagePngBase64": base64.b64encode(png).decode("ascii") if png else None,
    }


def _ticket_json(ticket: Ticket) -> dict[str, Any]:
    product = ticket.qr_type == QrType.PRODUCT.value
    return {
        "id": ticket.id,
        "eventId": ticket.event_id,
        "type": ticket.qr_type,
        "referenceId": ticket.reference_id,
        "label": ticket.label,
        "status": ticket.status,
        "issuedAt": ticket.issued_at.isoformat(),
        "expiresAt": None if product else ticket.expires_at.isoformat(),
        "usedAt": ticket.used_at.isoformat() if ticket.used_at else None,
        "revokedAt": ticket.revoked_at.isoformat() if ticket.revoked_at else None,
        "contactEmail": ticket.contact_email,
        "contactPhone": ticket.contact_phone,
        "deviceLocked": ticket.device_id is not None,
        "deviceLabel": ticket.device_label,
    }


def _split_payload(token: str) -> tuple[str, str | None]:
    """Split `<payload>.<hmac>`; PRODUCT URLs keep their dots untouched."""
    payload, _, signature = token.rpartition(".")
    if not payload or len(signature) != 16:
        return token, None
    return payload, signature


def _check_event_window(event: Event, now: datetime) -> None:
    if event.starts_at and _utc(event.starts_at) > now:
        raise HTTPException(403, "Event check-in has not opened yet")
    if event.ends_at and _utc(event.ends_at) <= now:
        raise HTTPException(410, "Event check-in is closed")


# --- pages -----------------------------------------------------------------


@public_router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return TEMPLATES.TemplateResponse(request, "index.html", {})


@public_router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "login.html", {})


@public_router.get("/p/{public_id}", response_class=HTMLResponse)
def public_product_page(request: Request, public_id: str):
    with db(request) as session:
        ticket = _find_product(session, public_id)
        product = {
            "referenceId": ticket.reference_id,
            "label": ticket.label,
            "batch": ticket.batch,
            "info": ticket.info,
        }
    return TEMPLATES.TemplateResponse(request, "product.html", {"product": product})


@public_router.get("/v/{public_id}", response_class=HTMLResponse)
def public_ticket_page(request: Request, public_id: str):
    with db(request) as session:
        ticket = _find_public_ticket(session, public_id)
        event = session.get(Event, ticket.event_id) if ticket.event_id else None
        ticket_url = f"{_cfg(request)['QR_PUBLIC_BASE_URL']}/v/{ticket.public_id}"
        has_contact = bool(ticket.contact_email and ticket.contact_phone)
        details = {
            "status": _public_ticket_status(ticket),
            "eventName": event.name if event else "Sự kiện",
            "expiresAt": ticket.expires_at.isoformat(),
            "contactSaved": has_contact,
            "referenceId": ticket.reference_id,
            "ticketUrl": ticket_url,
            "qrImagePngBase64": (
                base64.b64encode(_qr_png(ticket_url)).decode("ascii")
                if has_contact
                else None
            ),
        }
    return TEMPLATES.TemplateResponse(
        request, "verify_ticket.html", {"ticket": details}
    )


@public_router.get("/e/{public_id}", response_class=HTMLResponse)
def public_event_checkin_page(request: Request, public_id: str):
    with db(request) as session:
        event = session.scalar(
            select(Event).where(Event.public_id == public_id, Event.active.is_(True))
        )
        if not event:
            raise HTTPException(404, "Event check-in QR not found")
        details = {
            "eventName": event.name,
            "locationRequired": event.checkin_latitude is not None
            and event.checkin_longitude is not None,
        }
    return TEMPLATES.TemplateResponse(
        request, "checkin_confirm.html", {"ticket": details}
    )


@staff_router.get("/scanner", response_class=HTMLResponse)
def scanner_page(request: Request):
    with db(request) as session:
        user = actor(request, session)
        if user.role not in ("staff", "admin"):
            raise HTTPException(403, "Staff permission required")
    return TEMPLATES.TemplateResponse(request, "scanner.html", {})


# --- session ---------------------------------------------------------------


@public_router.get("/api/csrf")
def csrf(request: Request):
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"csrfToken": request.session["csrf"]}


@staff_router.get("/api/session")
def session_info(request: Request):
    with db(request) as session:
        user = actor(request, session)
        events = session.scalars(select(Event).where(Event.active.is_(True))).all()
        if user.role != "admin":
            ids = set(
                session.scalars(
                    select(EventStaff.event_id).where(EventStaff.user_id == user.id)
                ).all()
            )
            events = [event for event in events if event.id in ids]
        return {
            "username": user.username,
            "role": user.role,
            "events": [{"id": e.id, "name": e.name} for e in events],
        }


@public_router.post("/api/login")
async def login(request: Request):
    require_csrf(request)
    limited(request, "login", 8)
    try:
        body = await request.json()
        username, password = body.get("username", ""), body.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError
    except Exception:
        raise HTTPException(400, "Invalid login request")
    with db(request) as session:
        user = authenticate(session, username, password)
        if not user:
            raise HTTPException(401, "Invalid username or password")
        request.session.clear()
        request.session["uid"] = user.id
        request.session["csrf"] = secrets.token_urlsafe(32)
        audit(session, user.id, "login", "staff_user", user.id)
        session.commit()
        return {
            "result": "OK",
            "csrfToken": request.session["csrf"],
            "role": user.role,
        }


@public_router.post("/api/logout")
def logout(request: Request):
    require_csrf(request)
    request.session.clear()
    return {"result": "OK"}


# --- events ----------------------------------------------------------------


@admin_router.post("/api/events", status_code=201)
async def create_event(request: Request):
    require_csrf(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON object is required")
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        name = body.get("name", "").strip()
        if not name or len(name) > 160:
            raise HTTPException(400, "Event name is required")
        event = Event(
            name=name, public_id=secrets.token_urlsafe(18), **_event_settings(body)
        )
        session.add(event)
        session.flush()
        audit(session, user.id, "event.create", "event", event.id)
        session.commit()
        return _event_qr(event, _cfg(request))


@admin_router.patch("/api/events/{event_id}/settings")
async def update_event_settings(event_id: int, request: Request):
    require_csrf(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON object is required")
    settings = _event_settings(body)
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        event = session.get(Event, event_id)
        if not event:
            raise HTTPException(404, "Event not found")
        for key, value in settings.items():
            setattr(event, key, value)
        audit(
            session,
            user.id,
            "event.checkin_settings",
            "event",
            event.id,
            {
                "selfCheckinEnabled": event.checkin_latitude is not None,
                "radiusMeters": event.checkin_radius_meters,
            },
        )
        session.commit()
        return _event_qr(event, _cfg(request))


@admin_router.get("/api/events/{event_id}/settings")
def get_event_settings(event_id: int, request: Request):
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        event = session.get(Event, event_id)
        if not event:
            raise HTTPException(404, "Event not found")
        return {
            **_event_qr(event, _cfg(request)),
            "latitude": event.checkin_latitude,
            "longitude": event.checkin_longitude,
            "startsAt": _utc(event.starts_at).isoformat() if event.starts_at else None,
            "endsAt": _utc(event.ends_at).isoformat() if event.ends_at else None,
        }


# --- QR issuance -----------------------------------------------------------


@admin_router.post("/api/qr", status_code=201)
async def create_qr(request: Request):
    require_csrf(request)
    limited(request, "issue", 30)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON object is required")
    try:
        qr_type = QrType(body.get("type"))
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid QR type")
    ref, label = body.get("referenceId"), body.get("label")
    device = normalize_device_id(body.get("deviceId"))
    if qr_type != QrType.PRODUCT and not device:
        raise HTTPException(
            400, "deviceId is required so the QR is locked to one device"
        )
    if (
        not isinstance(ref, str)
        or not ref.strip()
        or len(ref) > 100
        or not isinstance(label, str)
        or not label.strip()
        or len(label) > 200
    ):
        raise HTTPException(400, "referenceId and label are required")
    cfg = _cfg(request)
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        event = None
        if qr_type != QrType.PRODUCT:
            event_id = body.get("eventId")
            event = session.get(Event, event_id) if isinstance(event_id, int) else None
            if not event or not event.active:
                raise HTTPException(400, "A valid active eventId is required")
        token = secrets.token_urlsafe(32)
        public_id = (
            secrets.token_urlsafe(18)
            if qr_type in (QrType.PRODUCT, QrType.DIGITAL_TICKET)
            else None
        )
        expires = (
            datetime.max.replace(tzinfo=timezone.utc)
            if qr_type == QrType.PRODUCT
            else datetime.now(timezone.utc)
            + timedelta(seconds=cfg["QR_VALIDITY_SECONDS"])
        )
        ticket = Ticket(
            event_id=event.id if event else None,
            reference_id=ref.strip(),
            token_hash=token_digest(token),
            public_id=public_id,
            qr_type=qr_type.value,
            label=label.strip(),
            expires_at=expires,
            holder=_optional(body, "holder", 100),
            seat=_optional(body, "seat", 20),
            batch=_optional(body, "batch", 50),
            info=_optional(body, "info", 300),
            device_id=device,
            device_label=_optional(body, "deviceLabel", 80),
        )
        session.add(ticket)
        session.flush()
        audit(
            session,
            user.id,
            "ticket.issue",
            "ticket",
            ticket.id,
            {"type": qr_type.value, "eventId": event.id if event else None},
        )
        session.commit()
    public_url, qr_content = None, token
    if public_id:
        path = "p" if qr_type == QrType.PRODUCT else "v"
        public_url = f"{cfg['QR_PUBLIC_BASE_URL']}/{path}/{public_id}"
        qr_content = public_url
    # Tickets carry an HMAC of token+deviceId so a copied QR fails check-in.
    qr_payload = (
        f"{qr_content}.{device_signature(token, device, cfg['SESSION_SECRET'])}"
        if device
        else qr_content
    )
    return {
        "token": token,
        "qrPayload": qr_payload,
        "type": qr_type.value,
        "referenceId": ticket.reference_id,
        "label": ticket.label,
        "publicUrl": public_url,
        "imagePngBase64": base64.b64encode(_qr_png(qr_payload)).decode("ascii"),
        "expiresAt": expires.isoformat(),
        "eventId": ticket.event_id,
        "deviceLocked": ticket.device_id is not None,
    }


@admin_router.get("/api/tickets")
def list_tickets(request: Request):
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        tickets = session.scalars(
            select(Ticket)
            .where(Ticket.archived_at.is_(None))
            .order_by(Ticket.issued_at.desc())
            .limit(100)
        ).all()
        return {"tickets": [_ticket_json(t) for t in tickets]}


@admin_router.post("/api/tickets/{ticket_id}/revoke")
def revoke_ticket(ticket_id: int, request: Request):
    require_csrf(request)
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        ticket = session.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(404, "Ticket not found")
        if ticket.status == "USED":
            raise HTTPException(409, "Used ticket cannot be revoked")
        if ticket.status != "REVOKED":
            ticket.status, ticket.revoked_at = "REVOKED", datetime.now(timezone.utc)
            audit(session, user.id, "ticket.revoke", "ticket", ticket.id)
            session.commit()
        return {"result": "REVOKED"}


@admin_router.delete("/api/tickets/{ticket_id}")
def archive_ticket(ticket_id: int, request: Request):
    require_csrf(request)
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
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


# --- products --------------------------------------------------------------


@admin_router.post("/api/products/lookup")
async def lookup_product(request: Request):
    require_csrf(request)
    body = await request.json()
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("token"), str)
        or not body["token"].strip()
    ):
        raise HTTPException(400, "Product QR token is required")
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        ticket = session.scalar(
            select(Ticket).where(
                Ticket.token_hash == token_digest(body["token"].strip()),
                Ticket.qr_type == QrType.PRODUCT.value,
            )
        )
        if not ticket or ticket.archived_at:
            raise HTTPException(404, "Product QR not found")
        if ticket.status == "REVOKED":
            raise HTTPException(410, "Product QR expired or revoked")
        return {
            "referenceId": ticket.reference_id,
            "label": ticket.label,
            "batch": ticket.batch,
            "info": ticket.info,
            "expiresAt": None,
            "status": ticket.status,
        }


@public_router.get("/api/products/{public_id}")
def public_product(request: Request, public_id: str):
    with db(request) as session:
        ticket = _find_product(session, public_id)
        return {
            "referenceId": ticket.reference_id,
            "label": ticket.label,
            "batch": ticket.batch,
            "info": ticket.info,
            "status": "ACTIVE",
        }


# --- digital tickets -------------------------------------------------------


@public_router.get("/api/tickets/public/{public_id}")
def public_ticket_status(request: Request, public_id: str):
    with db(request) as session:
        ticket = _find_public_ticket(session, public_id)
        return {
            "type": ticket.qr_type,
            "status": _public_ticket_status(ticket),
            "expiresAt": ticket.expires_at.isoformat(),
        }


@public_router.post("/api/tickets/public/{public_id}/contact")
async def register_ticket_contact(public_id: str, request: Request):
    require_csrf(request)
    limited(request, "ticket_contact", 10)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Contact details are required")
    email = body.get("email")
    phone = body.get("phone")
    email = email.strip() if isinstance(email, str) else email
    if (
        not isinstance(email, str)
        or len(email) > 254
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email)
    ):
        raise HTTPException(400, "Enter a valid email address")
    if not isinstance(phone, str):
        raise HTTPException(400, "Enter a valid phone number")
    phone = phone.strip()
    phone_match = re.fullmatch(
        r"(?:0([35789][0-9]{8})|(?:\+84|84|0084)0?([35789][0-9]{8}))",
        re.sub(r"[\s().-]", "", phone),
    )
    if len(phone) > 32 or phone_match is None:
        raise HTTPException(400, "Enter a valid phone number")
    normalized_phone = "+84" + (phone_match.group(1) or phone_match.group(2))
    with db(request) as session:
        now = datetime.now(timezone.utc)
        ticket = session.scalar(
            select(Ticket).where(
                Ticket.public_id == public_id,
                Ticket.qr_type == QrType.DIGITAL_TICKET.value,
                Ticket.archived_at.is_(None),
            )
        )
        if not ticket:
            raise HTTPException(404, "Ticket not found")
        if ticket.status != "ISSUED" or _utc(ticket.expires_at) <= now:
            raise HTTPException(
                410, "This ticket is no longer available for registration"
            )
        if ticket.contact_email or ticket.contact_phone:
            raise HTTPException(
                409, "Contact details have already been saved for this ticket"
            )
        result = session.execute(
            update(Ticket)
            .where(
                Ticket.id == ticket.id,
                Ticket.status == "ISSUED",
                Ticket.archived_at.is_(None),
                Ticket.expires_at > now,
                Ticket.contact_email.is_(None),
                Ticket.contact_phone.is_(None),
            )
            .values(contact_email=email.strip().lower(), contact_phone=normalized_phone)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(
                409, "This ticket was already registered or is no longer available"
            )
        audit(
            session,
            None,
            "digital_ticket.contact_saved",
            "ticket",
            ticket.id,
            {"contactProvided": True},
        )
        session.commit()
        ticket_url = f"{_cfg(request)['QR_PUBLIC_BASE_URL']}/v/{ticket.public_id}"
        return {
            "result": "SAVED",
            "referenceId": ticket.reference_id,
            "ticketUrl": ticket_url,
            "imagePngBase64": base64.b64encode(_qr_png(ticket_url)).decode("ascii"),
        }


# --- self check-in ---------------------------------------------------------


@public_router.post("/api/event-self-check-ins")
async def event_self_check_in(request: Request):
    require_csrf(request)
    limited(request, "event_self_checkin", 300)
    body = await request.json()
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("eventPublicId"), str)
        or not isinstance(body.get("studentId"), str)
    ):
        raise HTTPException(400, "Event QR and student ID are required")
    student_display = body["studentId"].strip()
    student_id = student_display.casefold()
    if not student_id or len(student_id) > 100:
        raise HTTPException(400, "Enter a valid student ID")
    point = _coordinates(body.get("latitude"), body.get("longitude"))
    public_id = body["eventPublicId"].strip()
    if not 16 <= len(public_id) <= 32 or not all(
        c.isalnum() or c in "-_" for c in public_id
    ):
        raise HTTPException(400, "Event QR is invalid")
    now = datetime.now(timezone.utc)
    with db(request) as session:
        event = session.scalar(
            select(Event).where(Event.public_id == public_id, Event.active.is_(True))
        )
        if not event:
            raise HTTPException(404, "Event not found or inactive")
        _check_event_window(event, now)
        if event.checkin_latitude is not None and event.checkin_longitude is not None:
            if point is None:
                raise HTTPException(
                    400, "Location permission is required for this event"
                )
            distance = _distance_meters(
                *point, event.checkin_latitude, event.checkin_longitude
            )
            if distance > event.checkin_radius_meters:
                raise HTTPException(
                    403,
                    f"You are outside the event check-in area ({round(distance)} m away)",
                )
        session.add(
            EventAttendance(event_id=event.id, student_id=student_id, checked_in_at=now)
        )
        audit(session, None, "event.self_checkin", "event", event.id)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, "This student ID has already checked in") from exc
        return {
            "result": "CHECKED_IN",
            "eventName": event.name,
            "studentId": student_display,
            "checkedInAt": now.isoformat(),
        }


# --- staff check-in --------------------------------------------------------


@staff_router.post("/api/check-ins")
async def check_in(request: Request):
    require_csrf(request)
    limited(request, "checkin", 60)
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("token"), str):
        raise HTTPException(400, "QR payload is required")
    token = body["token"].strip()
    device = normalize_device_id(body.get("deviceId"))
    selected_event = body.get("eventId")
    if selected_event is not None and (
        isinstance(selected_event, bool) or not isinstance(selected_event, int)
    ):
        raise HTTPException(400, "Invalid selected event")
    payload, signature = _split_payload(token)
    cfg = _cfg(request)
    public_id = _ticket_public_id(payload, cfg["QR_PUBLIC_BASE_URL"])
    if (
        not payload
        or len(payload) > 512
        or (not public_id and (" " in payload or ":" in payload))
    ):
        raise HTTPException(400, "Malformed QR payload")
    now = datetime.now(timezone.utc)
    with db(request) as session:
        user = actor(request, session)
        if user.role not in ("staff", "admin"):
            raise HTTPException(403, "Staff permission required")
        ticket = (
            session.scalar(select(Ticket).where(Ticket.public_id == public_id))
            if public_id
            else session.scalar(
                select(Ticket).where(Ticket.token_hash == token_digest(payload))
            )
        )
        if not ticket or ticket.archived_at or ticket.qr_type == QrType.PRODUCT.value:
            raise HTTPException(404, "Ticket not found")
        if ticket.event_id is None:
            raise HTTPException(400, "Ticket has no event")
        event = session.get(Event, ticket.event_id)
        if not event or not event.active:
            raise HTTPException(404, "Event not found or inactive")
        if selected_event is not None and selected_event != event.id:
            raise HTTPException(403, "Ticket belongs to a different event")
        _check_event_window(event, now)
        if (
            user.role != "admin"
            and session.scalar(
                select(EventStaff.id).where(
                    EventStaff.user_id == user.id,
                    EventStaff.event_id == ticket.event_id,
                )
            )
            is None
        ):
            raise HTTPException(403, "No access to this event")
        device_mismatch = ticket.device_id is not None and ticket.device_id != device
        if signature and not hmac.compare_digest(
            device_signature(payload, device or "", cfg["SESSION_SECRET"]), signature
        ):
            device_mismatch = True
        if device_mismatch:
            raise HTTPException(403, "QR này đã bị khoá với thiết bị khác")
        if _utc(ticket.expires_at) <= now:
            raise HTTPException(410, "Ticket expired")
        if ticket.status == "REVOKED" or ticket.revoked_at:
            raise HTTPException(410, "Ticket revoked")
        result = session.execute(
            update(Ticket)
            .where(
                Ticket.id == ticket.id,
                Ticket.status == "ISSUED",
                Ticket.used_at.is_(None),
                Ticket.revoked_at.is_(None),
                Ticket.expires_at > now,
            )
            .values(status="USED", used_at=now, used_by=user.id)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            with db(request) as inspect:
                latest = inspect.get(Ticket, ticket.id)
                if latest and _utc(latest.expires_at) <= now:
                    raise HTTPException(410, "Ticket expired")
                if latest and (latest.status == "REVOKED" or latest.revoked_at):
                    raise HTTPException(410, "Ticket revoked")
            raise HTTPException(409, "Ticket already used")
        session.add(
            CheckinLog(
                ticket_id=ticket.id,
                event_id=ticket.event_id,
                staff_user_id=user.id,
                checked_in_at=now,
            )
        )
        audit(
            session,
            user.id,
            "ticket.checkin",
            "ticket",
            ticket.id,
            {"eventId": ticket.event_id},
        )
        session.commit()
        return {
            "result": "CHECKED_IN",
            "referenceId": ticket.reference_id,
            "checkedInAt": now.isoformat(),
        }


# --- reports ---------------------------------------------------------------


@admin_router.get("/api/stats")
def stats(request: Request):
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        tickets = session.scalars(
            select(Ticket).where(
                Ticket.qr_type != "PRODUCT", Ticket.archived_at.is_(None)
            )
        ).all()
        checked = [t for t in tickets if t.status == "USED"]
        self_checkins = len(session.scalars(select(EventAttendance.id)).all())
        recent = sorted(checked, key=lambda t: t.used_at or t.issued_at, reverse=True)[
            :20
        ]
        return {
            "checkedIn": len(checked) + self_checkins,
            "tickets": len(tickets) + self_checkins,
            "issued": sum(t.status == "ISSUED" for t in tickets),
            "selfCheckins": self_checkins,
            "recent": [
                {
                    "label": t.label,
                    "referenceId": t.reference_id,
                    "type": t.qr_type,
                    "checkedInAt": t.used_at.isoformat() if t.used_at else None,
                }
                for t in recent
            ],
        }


@admin_router.get("/api/check-ins")
def checkin_history(request: Request):
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        staff_rows = session.execute(
            select(CheckinLog, Ticket.reference_id, StaffUser.username)
            .select_from(CheckinLog)
            .join(Ticket, CheckinLog.ticket_id == Ticket.id)
            .join(StaffUser, CheckinLog.staff_user_id == StaffUser.id)
            .where(Ticket.archived_at.is_(None))
            .order_by(CheckinLog.checked_in_at.desc())
            .limit(100)
        ).all()
        items = [
            {
                "referenceId": ref,
                "staff": name,
                "checkedInAt": log.checked_in_at.isoformat(),
                "eventId": log.event_id,
            }
            for log, ref, name in staff_rows
        ]
        items.extend(
            {
                "referenceId": row.student_id,
                "staff": "Tự điểm danh",
                "checkedInAt": row.checked_in_at.isoformat(),
                "eventId": row.event_id,
            }
            for row in session.scalars(
                select(EventAttendance)
                .order_by(EventAttendance.checked_in_at.desc())
                .limit(100)
            ).all()
        )

        def history_time(item):
            return _utc(
                datetime.fromisoformat(item["checkedInAt"].replace("Z", "+00:00"))
            )

        return {"items": sorted(items, key=history_time, reverse=True)[:100]}


@admin_router.delete("/api/check-ins/history")
def clear_checkin_history(request: Request):
    require_csrf(request)
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        staff_count = session.execute(delete(CheckinLog)).rowcount or 0
        self_count = session.execute(delete(EventAttendance)).rowcount or 0
        audit(
            session,
            user.id,
            "checkin_history.clear",
            "attendance",
            "all",
            {"staffCheckinsRemoved": staff_count, "selfCheckinsRemoved": self_count},
        )
        session.commit()
        return {
            "result": "CLEARED",
            "staffCheckinsRemoved": staff_count,
            "selfCheckinsRemoved": self_count,
        }


@admin_router.get("/api/audit")
def audit_history(request: Request):
    with db(request) as session:
        user = actor(request, session)
        require_admin(user)
        rows = session.scalars(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)
        ).all()
        return {
            "items": [
                {
                    "action": r.action,
                    "targetType": r.target_type,
                    "targetId": r.target_id,
                    "details": json.loads(r.details),
                    "createdAt": r.created_at.isoformat(),
                }
                for r in rows
            ]
        }


# --- retired endpoints -----------------------------------------------------


@public_router.get("/t/{token}")
def old_ticket_page(token: str):
    raise HTTPException(410, "Ticket links were removed; use the authenticated scanner")


@public_router.post("/api/reset")
def reset_removed():
    raise HTTPException(410, "Reset is removed because it could re-enable used tickets")


@public_router.get("/health")
def health():
    return {"status": "ok"}
