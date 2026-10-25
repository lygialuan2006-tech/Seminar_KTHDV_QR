"""Pure helpers: input validation, time, geo distance and QR rendering."""

from __future__ import annotations

import io
import math
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import qrcode
from fastapi import HTTPException
from sqlalchemy import select

from models import QrType, Ticket


def _optional(body: dict, key: str, limit: int) -> str | None:
    value = body.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise HTTPException(400, f"Invalid {key}")
    return value.strip() or None


def _utc(value: datetime) -> datetime:
    """SQLite returns naive values for timezone=True columns; normalize as UTC."""
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _parse_time(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise HTTPException(400, f"Invalid {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(400, f"Invalid {field}") from exc
    return _utc(parsed)


def _coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    """Validate an optional GPS pair; returns None when both are absent."""
    if latitude is None and longitude is None:
        return None
    if (
        latitude is None
        or longitude is None
        or isinstance(latitude, bool)
        or isinstance(longitude, bool)
    ):
        raise HTTPException(400, "Send both location coordinates")
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid location") from None
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise HTTPException(400, "Invalid location")
    return latitude, longitude


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate great-circle distance between two GPS coordinates."""
    earth_radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi, delta_lambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    value = min(1.0, max(0.0, value))
    return earth_radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _qr_png(content: str) -> bytes:
    code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=int(os.getenv("QR_SIZE", "8")),
        border=int(os.getenv("QR_BORDER", "4")),
    )
    code.add_data(content)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _event_settings(body: dict) -> dict[str, Any]:
    point = _coordinates(body.get("latitude"), body.get("longitude"))
    latitude, longitude = point or (None, None)
    radius = body.get("radiusMeters", 100)
    if (
        isinstance(radius, bool)
        or not isinstance(radius, int)
        or not 20 <= radius <= 2000
    ):
        raise HTTPException(400, "Check-in radius must be between 20 and 2000 meters")

    starts_at = _parse_time(body.get("startsAt"), "startsAt")
    ends_at = _parse_time(body.get("endsAt"), "endsAt")
    if starts_at and ends_at and ends_at <= starts_at:
        raise HTTPException(400, "Event end time must be after start time")
    if latitude is not None and (starts_at is None or ends_at is None):
        raise HTTPException(
            400, "GPS self check-in requires both opening and closing times"
        )
    return {
        "starts_at": starts_at,
        "ends_at": ends_at,
        "checkin_latitude": latitude,
        "checkin_longitude": longitude,
        "checkin_radius_meters": radius,
    }


def _public_ticket_status(ticket: Ticket) -> str:
    if ticket.status == "REVOKED" or ticket.revoked_at:
        return "REVOKED"
    if _utc(ticket.expires_at) <= datetime.now(timezone.utc):
        return "EXPIRED"
    return "USED" if ticket.used_at or ticket.status == "USED" else "VALID"


def _ticket_public_id(payload: str, base_url: str) -> str | None:
    """Accept only this app's public PRODUCT/DIGITAL_TICKET URL, never arbitrary URLs."""
    try:
        parsed, expected = urlsplit(payload), urlsplit(base_url)
        if (
            parsed.scheme != expected.scheme
            or parsed.netloc.lower() != expected.netloc.lower()
        ):
            return None
        parts = parsed.path.strip("/").split("/")
        if (
            len(parts) == 2
            and parts[0] in ("p", "v")
            and 0 < len(parts[1]) <= 32
            and all(c.isalnum() or c in "-_" for c in parts[1])
        ):
            return parts[1]
    except Exception:
        return None


def _find_product(session, public_id: str) -> Ticket:
    """Load an active PRODUCT ticket by public id or raise 404."""
    ticket = session.scalar(
        select(Ticket).where(
            Ticket.public_id == public_id,
            Ticket.qr_type == QrType.PRODUCT.value,
        )
    )
    if not ticket or ticket.archived_at or ticket.status == "REVOKED":
        raise HTTPException(404, "Product not found")
    return ticket


def _find_public_ticket(session, public_id: str) -> Ticket:
    """Load a DIGITAL_TICKET by public id or raise 404."""
    ticket = session.scalar(
        select(Ticket).where(
            Ticket.public_id == public_id,
            Ticket.qr_type == QrType.DIGITAL_TICKET.value,
        )
    )
    if not ticket or ticket.archived_at:
        raise HTTPException(404, "Ticket not found")
    return ticket
