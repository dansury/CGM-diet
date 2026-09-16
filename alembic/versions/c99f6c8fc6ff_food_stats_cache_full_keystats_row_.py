"""food_stats cache: full KeyStats row + users.food_stats_at

Revision ID: c99f6c8fc6ff
Revises: 81dec5104bdb
Create Date: 2026-09-14 00:31:58.626289
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c99f6c8fc6ff'
down_revision = '81dec5104bdb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The cache table may already hold rows: NOT NULL needs a server default.
    op.add_column('food_stats', sa.Column('sd', sa.Float(), nullable=True))
    op.add_column(
        'food_stats',
        sa.Column('n_without', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column('food_stats', sa.Column('mean_without', sa.Float(), nullable=True))
    op.add_column('food_stats', sa.Column('contrast', sa.Float(), nullable=True))
    op.add_column('food_stats', sa.Column('p_value', sa.Float(), nullable=True))
    op.add_column('food_stats', sa.Column('examples', sa.JSON(), nullable=True))
    op.add_column('users', sa.Column('food_stats_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'food_stats_at')
    op.drop_column('food_stats', 'examples')
    op.drop_column('food_stats', 'p_value')
    op.drop_column('food_stats', 'contrast')
    op.drop_column('food_stats', 'mean_without')
    op.drop_column('food_stats', 'n_without')
    op.drop_column('food_stats', 'sd')
