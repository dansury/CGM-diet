"""users.last_seen_at and users.blocked_at for the owner registry

Revision ID: c8d3e5a1f962
Revises: b2f6c1a9d074
Create Date: 2026-08-25 13:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c8d3e5a1f962'
down_revision = 'c4e1a7b93f52'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('blocked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'blocked_at')
    op.drop_column('users', 'last_seen_at')
