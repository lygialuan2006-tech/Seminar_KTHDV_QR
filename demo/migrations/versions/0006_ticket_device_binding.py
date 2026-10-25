"""Lock each ticket QR to the device that requested it.

Revision ID: 0006_ticket_device_binding
Revises: 0005_ticket_contact_details
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0006_ticket_device_binding"
down_revision = "0005_ticket_contact_details"
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        column["name"]: column
        for column in inspect(op.get_bind()).get_columns("tickets")
    }
    if "device_id" not in columns:
        op.add_column(
            "tickets", sa.Column("device_id", sa.String(length=64), nullable=True)
        )
    if "device_label" not in columns:
        op.add_column(
            "tickets", sa.Column("device_label", sa.String(length=80), nullable=True)
        )
    indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes("tickets")}
    if "ix_tickets_device_id" not in indexes:
        op.create_index("ix_tickets_device_id", "tickets", ["device_id"])


def downgrade():
    raise RuntimeError(
        "Destructive downgrade is disabled; device binding is part of the audit trail"
    )
