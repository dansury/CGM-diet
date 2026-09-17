"""weekly digest toggle

Revision ID: d7e7078226c7
Revises: c99f6c8fc6ff
Create Date: 2026-09-17 05:40:20.442057
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'd7e7078226c7'
down_revision = 'c99f6c8fc6ff'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # On for existing users too: the digest is one message a week, and it is
    # opt-out (`/set week off`), not opt-in.
    op.add_column(
        'users',
        sa.Column('weekly_digest_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column('users', 'weekly_digest_enabled')
