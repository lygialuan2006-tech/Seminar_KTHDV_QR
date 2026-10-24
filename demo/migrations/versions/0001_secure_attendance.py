"""Create secure attendance schema.

Revision ID: 0001_secure_attendance
Revises:
"""
from alembic import op
from app import Base

revision = "0001_secure_attendance"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    Base.metadata.create_all(bind=op.get_bind())

def downgrade():
    # Deliberately not implemented: dropping attendance data must be an explicit,
    # separately reviewed operational action.
    raise RuntimeError("Destructive downgrade is disabled; back up and review manually")
