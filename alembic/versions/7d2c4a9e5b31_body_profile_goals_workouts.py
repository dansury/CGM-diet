"""body profile, weight goals, bioimpedance columns and workouts

Revision ID: 7d2c4a9e5b31
Revises: 3c9a1f7b2d41
Create Date: 2026-08-24 20:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '7d2c4a9e5b31'
down_revision = '3c9a1f7b2d41'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column('body_fat_pct', sa.Float(), nullable=True),
        sa.Column('muscle_mass_kg', sa.Float(), nullable=True),
        sa.Column('water_pct', sa.Float(), nullable=True),
        sa.Column('bone_mass_kg', sa.Float(), nullable=True),
        sa.Column('visceral_fat', sa.Float(), nullable=True),
        sa.Column('bmr_kcal', sa.Float(), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False, server_default='manual'),
    ):
        op.add_column('weights', column)
    op.create_index('ix_weights_user_measured', 'weights', ['user_id', 'measured_at'])

    op.create_table(
        'body_profile',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('height_cm', sa.Float(), nullable=True),
        sa.Column('birth_year', sa.Integer(), nullable=True),
        sa.Column('sex', sa.String(length=1), nullable=True),
        sa.Column('activity', sa.String(length=16), nullable=False, server_default='light'),
        sa.Column('weight_prompt_days', sa.Integer(), nullable=False, server_default='14'),
        sa.Column('last_weight_prompt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', name='uq_body_profile_user'),
    )
    op.create_index('ix_body_profile_user_id', 'body_profile', ['user_id'])

    op.create_table(
        'body_goals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False, server_default='lose'),
        sa.Column('target_weight_kg', sa.Float(), nullable=True),
        sa.Column('start_weight_kg', sa.Float(), nullable=True),
        sa.Column('rate_kg_week', sa.Float(), nullable=True),
        sa.Column('target_kcal', sa.Float(), nullable=True),
        sa.Column('target_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_body_goals_user_id', 'body_goals', ['user_id'])
    op.create_index('ix_body_goals_active', 'body_goals', ['user_id', 'is_active'])

    op.create_table(
        'workouts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('kind', sa.String(length=24), nullable=False, server_default='other'),
        sa.Column('title', sa.String(length=128), nullable=True),
        sa.Column('duration_min', sa.Float(), nullable=True),
        sa.Column('intensity', sa.String(length=8), nullable=True),
        sa.Column('distance_m', sa.Float(), nullable=True),
        sa.Column('steps', sa.Integer(), nullable=True),
        sa.Column('avg_hr', sa.Float(), nullable=True),
        sa.Column('rpe', sa.Integer(), nullable=True),
        sa.Column('sweat', sa.String(length=8), nullable=True),
        sa.Column('kcal', sa.Float(), nullable=True),
        sa.Column('kcal_source', sa.String(length=12), nullable=False, server_default='estimated'),
        sa.Column('met', sa.Float(), nullable=True),
        sa.Column('source', sa.String(length=12), nullable=False, server_default='text'),
        sa.Column('media_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['media_id'], ['media_files.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_workouts_user_id', 'workouts', ['user_id'])
    op.create_index('ix_workouts_user_start', 'workouts', ['user_id', 'started_at'])


def downgrade() -> None:
    op.drop_index('ix_workouts_user_start', table_name='workouts')
    op.drop_index('ix_workouts_user_id', table_name='workouts')
    op.drop_table('workouts')
    op.drop_index('ix_body_goals_active', table_name='body_goals')
    op.drop_index('ix_body_goals_user_id', table_name='body_goals')
    op.drop_table('body_goals')
    op.drop_index('ix_body_profile_user_id', table_name='body_profile')
    op.drop_table('body_profile')
    op.drop_index('ix_weights_user_measured', table_name='weights')
    for column in (
        'source',
        'bmr_kcal',
        'visceral_fat',
        'bone_mass_kg',
        'water_pct',
        'muscle_mass_kg',
        'body_fat_pct',
    ):
        op.drop_column('weights', column)
