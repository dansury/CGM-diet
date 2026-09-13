"""meal sugar reminder stamp

Revision ID: 81dec5104bdb
Revises: 9a219bc869d7
Create Date: 2026-09-13 22:25:19.837100
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = '81dec5104bdb'
down_revision = '9a219bc869d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'meals', sa.Column('sugar_reminder_sent_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('meals', 'sugar_reminder_sent_at')
