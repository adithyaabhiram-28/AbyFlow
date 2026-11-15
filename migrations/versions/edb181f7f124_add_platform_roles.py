
"""add platform roles

Revision ID: edb181f7f124
Revises: df10075dd25a
Create Date: 2026-11-11 14:01:26.826291

"""
from alembic import op
import sqlalchemy as sa


revision = 'edb181f7f124'
down_revision = 'df10075dd25a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'platform_role',
                sa.String(length=20),
                server_default='user',
                nullable=False
            )
        )

        batch_op.create_check_constraint(
            'ck_user_valid_platform_role',
            "platform_role IN ('user', 'operator', 'admin')"
        )


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_constraint(
            'ck_user_valid_platform_role',
            type_='check'
        )

        batch_op.drop_column('platform_role')
