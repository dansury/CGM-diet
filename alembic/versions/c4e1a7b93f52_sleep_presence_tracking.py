"""sleep watch: presence pings and per-user switch

Revision ID: c4e1a7b93f52
Revises: 9a41c7e5b208
Create Date: 2026-08-25 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = 'c4e1a7b93f52'
down_revision = 'b2f6c1a9d074'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'sleep_presence_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'users',
        sa.Column('last_presence_reminder_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'presence_pings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
            index=True,
        ),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='telegram'),
    )
    op.create_index('ix_presence_user_at', 'presence_pings', ['user_id', 'at'])


def downgrade() -> None:
    op.drop_index('ix_presence_user_at', table_name='presence_pings')
    op.drop_table('presence_pings')
    op.drop_column('users', 'last_presence_reminder_at')
    op.drop_column('users', 'sleep_presence_enabled')
