"""add tenant invoice sequence model

Revision ID: 0a87ec41c0d2
Revises: 4df9cb8aa022
Create Date: 2026-11-08

"""
from alembic import op
import sqlalchemy as sa


revision = '0a87ec41c0d2'
down_revision = '4df9cb8aa022'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'invoice_sequence',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column(
            'next_number',
            sa.Integer(),
            server_default='1',
            nullable=False
        ),
        sa.CheckConstraint(
            'next_number >= 1',
            name='ck_invoice_sequence_positive_number'
        ),
        sa.ForeignKeyConstraint(
            ['tenant_id'],
            ['tenant.id'],
            ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id',
            name='uq_invoice_sequence_tenant'
        )
    )


def downgrade():
    op.drop_table('invoice_sequence')