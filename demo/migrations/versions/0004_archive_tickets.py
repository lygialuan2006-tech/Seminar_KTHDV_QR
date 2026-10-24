"""Allow admins to remove issued QR codes from the active list.

Revision ID: 0004_archive_tickets
Revises: 0003_event_self_checkin
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_archive_tickets"
down_revision = "0003_event_self_checkin"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("tickets")}
    if "archived_at" not in columns:
        op.add_column("tickets", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; archived QR and check-in history must be reviewed manually")
