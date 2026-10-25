"""SQLAlchemy models for the QR attendance service."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class QrType(str, Enum):
    PRODUCT = "PRODUCT"
    EVENT_CHECKIN = "EVENT_CHECKIN"
    DIGITAL_TICKET = "DIGITAL_TICKET"


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StaffUser(Base):
    __tablename__ = "staff_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="staff")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    public_id: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, nullable=True
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkin_latitude: Mapped[float | None] = mapped_column(nullable=True)
    checkin_longitude: Mapped[float | None] = mapped_column(nullable=True)
    checkin_radius_meters: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class EventStaff(Base):
    __tablename__ = "event_staff"
    __table_args__ = (UniqueConstraint("user_id", "event_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("staff_users.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_event_status", "event_id", "status"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    reference_id: Mapped[str] = mapped_column(String(100), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    public_id: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, nullable=True
    )
    qr_type: Mapped[str] = mapped_column(String(32), default="EVENT_CHECKIN")
    label: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ISSUED")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    used_by: Mapped[int | None] = mapped_column(
        ForeignKey("staff_users.id"), nullable=True
    )
    holder: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seat: Mapped[str | None] = mapped_column(String(20), nullable=True)
    batch: Mapped[str | None] = mapped_column(String(50), nullable=True)
    info: Mapped[str | None] = mapped_column(String(300), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    device_label: Mapped[str | None] = mapped_column(String(80), nullable=True)


class CheckinLog(Base):
    __tablename__ = "checkin_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="RESTRICT"), index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT"), index=True
    )
    staff_user_id: Mapped[int] = mapped_column(
        ForeignKey("staff_users.id", ondelete="RESTRICT"), index=True
    )
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class EventAttendance(Base):
    __tablename__ = "event_attendance"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "student_id", name="uq_event_attendance_event_student"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[str] = mapped_column(String(100))
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
