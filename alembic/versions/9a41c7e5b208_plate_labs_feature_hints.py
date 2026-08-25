"""harvard plate settings, feature hints

Revision ID: 9a41c7e5b208
Revises: 7d2c4a9e5b31
Create Date: 2026-08-25 10:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '9a41c7e5b208'
down_revision = '7d2c4a9e5b31'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('plate_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column('users', sa.Column('meals_per_day', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('last_hint_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'feature_flags',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
            index=True,
        ),
        sa.Column('feature', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='new'),
        sa.Column('shown', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_shown_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'feature', name='uq_feature_user'),
    )


def downgrade() -> None:
    op.drop_table('feature_flags')
    op.drop_column('users', 'last_hint_at')
    op.drop_column('users', 'meals_per_day')
    op.drop_column('users', 'plate_enabled')
