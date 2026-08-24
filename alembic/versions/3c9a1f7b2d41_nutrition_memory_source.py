"""nutrition memory: source of the numbers (user|label)

Revision ID: 3c9a1f7b2d41
Revises: 1b5b3749a97a
Create Date: 2026-08-24 18:40:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '3c9a1f7b2d41'
down_revision = '1b5b3749a97a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'user_nutrition',
        sa.Column('source', sa.String(length=16), nullable=False, server_default='user'),
    )


def downgrade() -> None:
    op.drop_column('user_nutrition', 'source')
