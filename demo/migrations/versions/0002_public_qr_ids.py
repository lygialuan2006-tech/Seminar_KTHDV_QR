"""Add public QR identifiers for repeatable product/ticket lookup.

Revision ID: 0002_public_qr_ids
Revises: 0001_secure_attendance
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_public_qr_ids"
down_revision = "0001_secure_attendance"
branch_labels = None
depends_on = None

def upgrade():
    # The original bootstrap migration uses current metadata; fresh installs may
    # already have this column. Older deployed schemas receive it here.
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("tickets")}
    if "public_id" not in columns:
        op.add_column("tickets", sa.Column("public_id", sa.String(length=32), nullable=True))
    indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes("tickets")}
    if "ix_tickets_public_id" not in indexes:
        op.create_index("ix_tickets_public_id", "tickets", ["public_id"], unique=True)

def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; review product/ticket QR data manually")
