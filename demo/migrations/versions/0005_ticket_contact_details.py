"""Store attendee email and phone on digital tickets.

Revision ID: 0005_ticket_contact_details
Revises: 0004_archive_tickets
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0005_ticket_contact_details"
down_revision = "0004_archive_tickets"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("tickets")}
    if "contact_email" not in columns:
        op.add_column("tickets", sa.Column("contact_email", sa.String(length=254), nullable=True))
    if "contact_phone" not in columns:
        op.add_column("tickets", sa.Column("contact_phone", sa.String(length=32), nullable=True))


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; attendee contact details require manual review")
