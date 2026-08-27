"""sugar track profile fields and the message log

Revision ID: d51aa877105e
Revises: c73a5e10b8d2
Create Date: 2026-08-27 21:11:54.389563
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = 'd51aa877105e'
down_revision = 'c73a5e10b8d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'message_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('buttons', sa.JSON(), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_message_log_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_message_log'))
    )
    op.create_index('ix_message_log_user_row', 'message_log', ['user_id', 'id'], unique=False)
    op.add_column('body_profile', sa.Column('diabetes', sa.String(length=16), nullable=True))
    op.add_column('body_profile', sa.Column('diabetes_meds', sa.Text(), nullable=True))
    op.add_column('body_profile', sa.Column('glucose_methods', sa.Text(), nullable=True))
    # существующим пользователям предложение замеров не включаем: его ставит
    # только сахарный трек анкеты (`spec/onboarding.md`)
    op.add_column(
        'users',
        sa.Column(
            'glucose_prompt_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'glucose_prompt_enabled')
    op.drop_column('body_profile', 'glucose_methods')
    op.drop_column('body_profile', 'diabetes_meds')
    op.drop_column('body_profile', 'diabetes')
    op.drop_index('ix_message_log_user_row', table_name='message_log')
    op.drop_table('message_log')
