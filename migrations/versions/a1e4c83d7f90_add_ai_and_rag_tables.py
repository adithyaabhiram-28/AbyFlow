"""add ai and rag tables

Revision ID: a1e4c83d7f90
Revises: 932ed677e876
Create Date: 2026-11-12 00:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
from app.models import EmbeddingVector


# revision identifiers, used by Alembic.
revision = 'a1e4c83d7f90'
down_revision = '932ed677e876'
branch_labels = None
depends_on = None


def upgrade():
    # Attempt to enable pgvector extension if on PostgreSQL
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    # 1. ai_conversation
    op.create_table(
        'ai_conversation',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), server_default='Billing Inquiry', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_conversation', schema=None) as batch_op:
        batch_op.create_index('ix_ai_conversation_tenant_user', ['tenant_id', 'user_id'], unique=False)
        batch_op.create_index('ix_ai_conversation_created_at', ['created_at'], unique=False)

    # 2. ai_message
    op.create_table(
        'ai_message',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('tool_call_id', sa.String(length=100), nullable=True),
        sa.Column('tool_name', sa.String(length=100), nullable=True),
        sa.Column('tool_arguments', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role IN ('system', 'user', 'assistant', 'tool')", name='ck_ai_message_valid_role'),
        sa.ForeignKeyConstraint(['conversation_id'], ['ai_conversation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_message', schema=None) as batch_op:
        batch_op.create_index('ix_ai_message_conversation_created', ['conversation_id', 'created_at'], unique=False)

    # 3. ai_usage
    op.create_table(
        'ai_usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('model', sa.String(length=50), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completion_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('estimated_cost_usd', sa.Numeric(precision=10, scale=6), server_default='0', nullable=False),
        sa.Column('latency_ms', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['ai_conversation.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.create_index('ix_ai_usage_tenant_user', ['tenant_id', 'user_id'], unique=False)
        batch_op.create_index('ix_ai_usage_created_at', ['created_at'], unique=False)

    # 4. knowledge_document
    op.create_table(
        'knowledge_document',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('category', sa.String(length=50), server_default='policy', nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'slug', 'version', name='uq_knowledge_doc_tenant_slug_version')
    )
    with op.batch_alter_table('knowledge_document', schema=None) as batch_op:
        batch_op.create_index('ix_knowledge_document_tenant_slug', ['tenant_id', 'slug'], unique=False)

    # 5. document_chunk
    op.create_table(
        'document_chunk',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', EmbeddingVector(), nullable=False),
        sa.Column('token_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['knowledge_document.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('document_chunk', schema=None) as batch_op:
        batch_op.create_index('ix_document_chunk_doc_tenant', ['document_id', 'tenant_id'], unique=False)


def downgrade():
    with op.batch_alter_table('document_chunk', schema=None) as batch_op:
        batch_op.drop_index('ix_document_chunk_doc_tenant')
    op.drop_table('document_chunk')

    with op.batch_alter_table('knowledge_document', schema=None) as batch_op:
        batch_op.drop_index('ix_knowledge_document_tenant_slug')
    op.drop_table('knowledge_document')

    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.drop_index('ix_ai_usage_created_at')
        batch_op.drop_index('ix_ai_usage_tenant_user')
    op.drop_table('ai_usage')

    with op.batch_alter_table('ai_message', schema=None) as batch_op:
        batch_op.drop_index('ix_ai_message_conversation_created')
    op.drop_table('ai_message')

    with op.batch_alter_table('ai_conversation', schema=None) as batch_op:
        batch_op.drop_index('ix_ai_conversation_created_at')
        batch_op.drop_index('ix_ai_conversation_tenant_user')
    op.drop_table('ai_conversation')

