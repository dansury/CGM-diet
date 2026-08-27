"""body_profile pregnancy flag and free-text conditions

Revision ID: b2f6c1a9d074
Revises: 9a41c7e5b208
Create Date: 2026-08-25 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'b2f6c1a9d074'
down_revision = '9a41c7e5b208'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('body_profile', sa.Column('pregnant', sa.Boolean(), nullable=True))
    op.add_column('body_profile', sa.Column('conditions', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('body_profile', 'conditions')
    op.drop_column('body_profile', 'pregnant')
