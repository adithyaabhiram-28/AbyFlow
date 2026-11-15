"""add invoice tax rate constraint

Revision ID: b535855fde14
Revises: 99f0dfbee152
"""

from alembic import op


revision = 'b535855fde14'
down_revision = '99f0dfbee152'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('invoice') as batch_op:
        batch_op.create_check_constraint(
            'ck_invoice_valid_tax_rate',
            'tax_rate >= 0 AND tax_rate <= 100'
        )


def downgrade():
    with op.batch_alter_table('invoice') as batch_op:
        batch_op.drop_constraint(
            'ck_invoice_valid_tax_rate',
            type_='check'
        )