"""body_profile: goals picked at first run

Revision ID: c73a5e10b8d2
Revises: b2f6c1a9d074
Create Date: 2026-08-27 10:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c73a5e10b8d2'
down_revision = 'b2f6c1a9d074'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('body_profile', sa.Column('focus', sa.Text(), nullable=True))
    op.add_column('body_profile', sa.Column('focus_note', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('body_profile', 'focus_note')
    op.drop_column('body_profile', 'focus')
