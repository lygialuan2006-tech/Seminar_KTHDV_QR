"""Add public event QR, GPS geofence, and self-check-in records.

Revision ID: 0003_event_self_checkin
Revises: 0002_public_qr_ids
"""
from alembic import op
from secrets import token_urlsafe
import sqlalchemy as sa
from sqlalchemy import inspect
from app import EventAttendance

revision = "0003_event_self_checkin"
down_revision = "0002_public_qr_ids"
branch_labels = None
depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    event_columns = {column["name"] for column in inspector.get_columns("events")}
    if "public_id" not in event_columns:
        op.add_column("events", sa.Column("public_id", sa.String(length=32), nullable=True))
    if "checkin_latitude" not in event_columns:
        op.add_column("events", sa.Column("checkin_latitude", sa.Float(), nullable=True))
    if "checkin_longitude" not in event_columns:
        op.add_column("events", sa.Column("checkin_longitude", sa.Float(), nullable=True))
    if "checkin_radius_meters" not in event_columns:
        op.add_column("events", sa.Column("checkin_radius_meters", sa.Integer(), nullable=False, server_default="100"))

    event_table = sa.table("events", sa.column("id", sa.Integer()), sa.column("public_id", sa.String(length=32)))
    connection = op.get_bind()
    for row in connection.execute(sa.select(event_table.c.id).where(event_table.c.public_id.is_(None))):
        connection.execute(event_table.update().where(event_table.c.id == row.id).values(public_id=token_urlsafe(18)))
    indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes("events")}
    if "ix_events_public_id" not in indexes:
        op.create_index("ix_events_public_id", "events", ["public_id"], unique=True)

    EventAttendance.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; back up and review event attendance data manually")
